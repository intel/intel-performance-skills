The new version is in `dot_fast.c`. It is about 12–13× faster on the Westmere-class code path at n = 1000–4096, and every test passes. **The AVX-512 kernel, which your Sapphire Rapids customers would get, has not been run**, because nothing here has AVX-512.

## Review of `dot()`

```c
float sum = 0.0f;
for (int i = 0; i < n; i++)
    sum += a[i] * b[i];        /* ◄ serial dependency: each add waits for the previous one */
```

1. **One accumulator means one long dependency chain.** This is the main problem. Each `+=` needs the previous `sum`, so the loop does one element per floating-point add latency (about 3–4 cycles). Every CPU in your range could start one or two vector adds or FMAs per cycle. Your build (`cc -O2 -fPIC`) produces exactly this: the compiler unrolls 4× but keeps a scalar `mulss`/`addss` chain (`xmm0→xmm1→xmm2→xmm3→xmm0`).
2. **The compiler can't fix it.** Reordering float additions changes the result. Without `-ffast-math` the compiler has to keep the serial order, so it never vectorizes this loop at any `-O` level.
3. **None of the vector width is used.** With plain `-O2` and no `-march`, the compiler can only target baseline SSE2. You can't raise `-march` for the library because Westmere has no AVX. So the Haswell-and-later boxes never use their 256-bit FMA units, and Sapphire Rapids never uses its 512-bit units. Fixing this needs a runtime CPU check.

Checked and not applicable:
- **`restrict`:** neither pointer is written, so there is no aliasing hazard. `dot(v, v, n)` (squared norm) stays a valid call.
- **`vzeroupper`:** the compiler emits it for these functions, and I confirmed it in the disassembly.
- **Threading:** there is no shared mutable state apart from the one-time dispatch pointer, which is handled below.

## What `dot_fast.c` does

It keeps the same signature and ABI (`float dot(const float *a, const float *b, int n)`), builds with plain `cc -O2 -fPIC`, and exports only `dot`. Replace `dot.c` with it in the build; linking both gives a duplicate-symbol error.

| Kernel | Chosen when CPUID reports | Your machines |
|---|---|---|
| `dot_avx512`: 16 floats/vector, FMA | AVX512F + AVX2 + FMA, with OS support for the 512-bit register state | Skylake-SP … Sapphire Rapids (also AMD Zen 4/5) |
| `dot_avx2`: 8 floats/vector, FMA | AVX2 + FMA | Haswell, Broadwell, other AVX2 parts without AVX-512 |
| `dot_sse2`: 4 floats/vector | always (SSE2 is part of baseline x86-64) | Westmere, Sandy/Ivy Bridge, Atom-based servers |

- **8 independent vector accumulators in every kernel.** That is enough to hide the add/FMA latency at full load throughput on every CPU in the range, including Haswell's 5-cycle FMA.
- **The CPU check runs once.**
  - Each kernel gets its instruction set from `__attribute__((target(...)))`, so no build flags change.
  - On the first call, `__builtin_cpu_supports` picks the kernel and stores it in a function pointer. After that, each call is a single indirect call. Concurrent first calls are thread-safe.
  - I didn't use `target_clones` on the plain loop: the compiler still wouldn't vectorize this sum, and the IFUNC mechanism behind it isn't available on every libc and has load-order problems in shared libraries.
- **No serial scalar tail.** Leftover whole vectors each go into their own accumulator. The last `n % W` elements (W = floats per vector) are handled by one vector that ends exactly at `a[n-1]`, with the lanes already counted masked to zero. Nothing outside `a[0..n-1]` or `b[0..n-1]` is ever read.
- **The baseline is plain SSE2.** Westmere's SSE4.2 has nothing useful for this loop: `dpps` is slower than multiply-add into vector accumulators.
- Builds that aren't x86-64 fall back to a portable C loop with 4 accumulators.
- `n <= 0` still returns `0.0f`. Indexing uses `size_t`, so `n` near `INT_MAX` can't overflow.

## Floating-point behaviour (please read)

**Results won't be bit-identical to the old `dot()`.** The additions happen in a different order, and the AVX2/AVX-512 kernels also use FMA. The differences are at rounding level, not logic errors. **The same input can also give results that differ in the last bits between a Westmere box and a Haswell or Sapphire Rapids box.**

In practice the new code is more accurate. Against a double-precision reference at n = 3000, over 20 random vectors:
- mean error relative to Σ|aᵢbᵢ| falls from 1.8e-8 (original) to 3.0e-9 (SSE2 kernel) and 3.3e-9 (AVX2 kernel);
- worst case falls from 8.0e-8 to under 8e-9.

NaN and ±Inf propagate exactly as before.

If any customer needs bit-identical results, either against old output or across machines, this can't ship as is. A reproducible variant is possible: no FMA, and one fixed summation order shared by all kernels. It would give up most of the AVX-512 gain.

## Verification

All testing used x86-64 code built with Apple clang 21 and run under Rosetta 2 on an Apple Silicon Mac. By default Rosetta looks like Westmere (SSE4.2, no AVX). With `ROSETTA_ADVERTISE_AVX=1` it looks like Haswell (AVX2 + FMA).

- **Build:** `cc -O2 -fPIC` succeeds for x86-64 and arm64. There are no warnings even with `-Wall -Wextra -Wpedantic -Wconversion -Wshadow -Wcast-align`. As a shared library it exports only `dot`.
- **Safe on Westmere (checked in the disassembly):**
  - `dot`, the dispatcher and `dot_sse2` contain no AVX/AVX-512 instructions or registers.
  - Both AVX kernels clear the upper register state (`vzeroupper`) before every return that used 256/512-bit registers.
  - `dot_avx512` uses only AVX512F instructions, so checking `avx512f` is enough.
- **Tests (`test_dot_fast.c`):** they pass in both Rosetta modes, under ASan+UBSan, and natively on arm64 with clang and GCC 14. They cover:
  - exact-arithmetic checks for every n from 0 to 1100 and for large n up to about 1,000,000, with all 16×16 pointer misalignments for small n;
  - arrays placed right against unreadable guard pages at both ends, to catch any read past the array;
  - NaN and ±Inf at every index position;
  - an error bound against a double-precision reference;
  - the dispatcher picking `sse2` on the Westmere-like CPU and `avx2` on the Haswell-like one.
- **The tests catch real bugs.** I injected six realistic bugs, and the suite failed on every one: mask off by one, reading one float past the end, a dropped leftover vector, double counting, masking only one operand, and a missing CPU check.

**What I couldn't verify:**
- **The AVX-512 kernel has never run.** What I did check:
  - it compiles, and its disassembly is correct;
  - its loop and leftover-vector logic is token-for-token identical to the tested AVX2 kernel;
  - its tail mask was checked for every tail length.

  Before shipping, run `./test_dot_fast` on a Sapphire Rapids box, or under Intel's emulator with `sde64 -spr -- ./test_dot_fast`.
- **No GCC build for x86-64.** The only GCC here targets arm64. Build once with your production compiler; the code uses only `target` attributes, `__builtin_cpu_supports` and standard intrinsics.

## Performance

These are times for the SSE2 kernel (the one Westmere would get), measured under Rosetta, in nanoseconds per call, best of 7 runs:

| n | original | new `dot()` | speedup |
|---|---|---|---|
| 1000 | 573 | 49 | 11.8× |
| 2000 | 1255 | 94 | 13.4× |
| 3000 | 1883 | 151 | 12.5× |
| 4096 | 2744 | 200 | 13.7× |

Rosetta isn't an Intel core, so these numbers only show that the dependency chain is gone. Under Rosetta the AVX2 kernel even measures slower than SSE2. That is an emulation artifact, not a kernel problem: Rosetta runs identical 128-bit loops about 1.6× slower in AVX encoding than in SSE encoding. It says nothing about real Haswell or later chips.

Rough expectations on real hardware, estimated from per-core load and FMA limits with the vectors already in L1/L2 cache (not measured):

| | Before (serial add chain) | After (what limits it) | Rough speedup |
|---|---|---|---|
| Westmere, SSE2 kernel | ~3 cycles/element | one load port: ~0.5 cycles/element | ~5–6× |
| Sandy/Ivy Bridge, SSE2 kernel | ~3 cycles/element | two loads per cycle: ~0.3 cycles/element | ~10× |
| Haswell to Cascade Lake, AVX2 kernel | 3–4 cycles/element | two 32-byte loads per cycle: ~0.13 cycles/element | ~15–30× |
| Skylake-SP to Sapphire Rapids, AVX-512 kernel | 3–4 cycles/element | two 64-byte loads per cycle: ~0.07 cycles/element | up to ~2× the AVX2 figure; less with unaligned data or downclocking |

If the vectors come from main memory instead, every version is limited by memory bandwidth and the gains shrink. To confirm on real machines, run `./test_dot_fast bench`, or check that instructions per cycle rise in `perf stat`.

## Decisions for you

1. **AVX-512 on Skylake-SP / Cascade Lake.** Heavy 512-bit FMA makes those chips lower their clock speed, which can eat the gain for occasional short calls. Sapphire Rapids is much less affected. If profiling on those customers shows a regression, there are two options:
   - a one-line change so `dot_avx512` is picked only on Ice Lake or later (for example, by also requiring `avx512vbmi`);
   - an environment variable that caps which kernel is chosen.
2. **Alignment.** With AVX-512, every load from a pointer that isn't 64-byte aligned splits across two cache lines. If `perf` on Sapphire Rapids shows many split loads, a small extra step that aligns `a` first would help. I left it out because I couldn't measure it here.
3. **Bit-identical results across machines.** If customers need this, I can write the reproducible variant described above.

Files are in the working directory:
- `dot_fast.c`
- `test_dot_fast.c` — tests, plus a `bench` mode. Build with `cc -O2 -fPIC test_dot_fast.c -o test_dot_fast -lm`.
