I wrote `dot_fast.c`, a drop-in replacement for `dot()`, plus a test-and-benchmark harness, `test_dot.c`. `dot()` now picks an SSE2 or an AVX2 version once, at the first call, and it builds with plain `cc -O2 -fPIC`. The SSE2 path is about 13× faster at n = 4000 in my measurement. I could only time it under Apple's x86 emulation (Rosetta 2), not on Intel hardware, and I couldn't time the AVX2 path at all; the per-CPU figures below are estimates.

## What's slow in the original

1. **Every add waits for the previous one.** `sum += a[i] * b[i]` is one long chain of dependent adds, so the loop runs at one element per add latency. That's about 3 cycles per element on Westmere through Broadwell and about 4 on Skylake/Cascade Lake/Ice Lake, however wide the CPU is. clang `-O2` unrolls it 4× but keeps a single chain (`addss` → `addss` → …).
2. **The compiler isn't allowed to fix it.** Splitting the chain reorders float additions, which changes rounding, so neither GCC nor clang will do it without `-ffast-math`.
   - `-ffast-math` would be the wrong fix anyway: it changes NaN/Inf behaviour for the whole library.
   - With GCC older than 13 it also makes shared objects switch on flush-to-zero and denormals-are-zero (FTZ/DAZ) in every customer process that loads them.
3. **`-O2` with no `-march` means SSE2 only.** The AVX2 units on Haswell through Sapphire Rapids are never used. You can't add `-mavx2` or `-march=native` either, because the library would then crash (illegal instruction) on Westmere. The fix is to check the CPU at run time and compile the AVX2 code for just one function.

The `int` index and possible pointer aliasing are not problems here (nothing is stored), and `n <= 0` still returns 0.

## What `dot_fast.c` does

- **Drop-in:** same signature and symbol, `float dot(const float *a, const float *b, int n)`. `dot` is the only exported symbol, and there are no new dependencies (no ifunc, no libgcc CPU-detection helper).
- **32 independent partial sums:** element *i* goes to lane *i* mod 32, and the lanes are combined in a fixed pairwise tree.
- **Two kernels, chosen once from CPUID/XGETBV:**
  - **SSE2**, the x86-64 baseline: Westmere and Sandy/Ivy Bridge. It uses 8 SSE registers. SSE3 through SSE4.2 add nothing useful here; `dpps` is slower than a separate multiply and add.
  - **AVX2**, enabled for that one function with `__attribute__((target("avx2")))`: Haswell through Sapphire Rapids, and AMD Zen. It uses 4 256-bit registers, and its tail uses masked loads so it never reads past the end of the arrays.
- **The same result on every machine.** Both kernels do the same multiplies and adds in the same order, with no fused multiply-add (FMA). A Westmere box and a Sapphire Rapids box therefore return exactly the same float, as the old code does.
  - Results do change in the last bits compared with the old sequential sum; any reordering does that.
  - They are more accurate. Error relative to a double-precision sum, measured as |error| / Σ|aᵢbᵢ|:

| Data | Original (mean / max) | dot_fast (mean / max) |
|---|---|---|
| n=4000, values in [-1,1) | 1.5e-8 / 8.1e-8 | 3.6e-9 / 1.1e-8 |
| n=4000, values in [0,1) | 6.7e-7 / 2.5e-6 | 2.8e-8 / 1.4e-7 |
| n=65536, values in [0,1) | 2.4e-6 / 8.0e-6 | 9.0e-8 / 2.6e-7 |

- **Checked machine code** (from the generated assembly):
  - The SSE2 kernel contains only SSE/SSE2 instructions.
  - The AVX2 instructions appear only in the AVX2 kernel, which ends with `vzeroupper`.
  - There are no FMA instructions anywhere.
  - After the first call, `dot()` is just a load, a branch and a jump to the chosen kernel.
  - The AVX2 loop is 15 instructions per 32 elements. I stopped the compiler from rewriting its memory accesses into an indexed form that costs up to ~25% on Haswell/Skylake.

## Verification
- **Builds:** warning-free with `-O2 -fPIC` and strict warnings (`-Wall -Wextra -pedantic -Wconversion`) in C99, C11 and C17. Built for macOS x86-64 and Linux x86-64 with clang, and for arm64 with clang and GCC 14 (the portable fallback). Calling `dot()` through a shared library built this way works.
- **`test_dot.c`:** every available kernel is checked bit-for-bit against a separately written scalar version of the same summation order. The cases are:
  - every n from 0 to 300, plus 21 larger sizes up to 65,569;
  - pointer misalignments;
  - NaN, ±Inf, ±0, denormals and overflow;
  - 7 floating-point modes (FTZ, DAZ, all rounding modes);
  - arrays that end or start right next to an unmapped page.
- **Results:** 250,068 checks, 0 failures, in both dispatch modes. Under Rosetta the CPU looks Westmere-like by default and offers AVX2 when the `ROSETTA_ADVERTISE_AVX=1` environment variable is set. It also passes at -O0 through -O3 and -Os, and under ASan and UBSan.
- **The tests catch real mistakes:** I broke the code on purpose three ways (enabling FMA, dropping the tail blend, reading past the end), and each one is caught.

## Performance
**Measured** (x86-64 build under Rosetta 2 on an Apple M5 Max, SSE2 path):

| n | 256 | 1000 | 4000 | 8000 | 4000, misaligned | 4000, from DRAM |
|---|---|---|---|---|---|---|
| Speedup vs original | 7.3× | 11.2× | 13.3× (2239 → 168 ns) | 13.6× | 11.3× | 6.9× |

**AVX2 timings from this machine are meaningless.** Rosetta slows down AVX-encoded instructions as such: the same SSE2 algorithm in AVX encoding runs 2.2× slower there. So the AVX2 kernel was verified for correctness only.

**Expected on your hardware.** These are estimates from the hardware's load and add limits on the generated loops, for data already in L1/L2, not measurements:

| CPU | Original | dot_fast | Rough speedup |
|---|---|---|---|
| Westmere (1 load per cycle) | ~3 cycles/element | ~2 elements/cycle (SSE2) | ~6× |
| Sandy/Ivy Bridge | ~3 cycles/element | ~3.8 elements/cycle (SSE2) | ~11× |
| Haswell/Broadwell | ~3 cycles/element | ~8 elements/cycle (AVX2) | up to ~24× |
| Skylake-SP/Cascade Lake/Ice Lake | ~4 cycles/element | ~6–8 elements/cycle (AVX2) | ~25–30× |
| Sapphire Rapids | 1 add latency/element | ~8–12 elements/cycle (AVX2) | ~15–30× |

When the data comes from DRAM every version is limited by memory bandwidth, so the gain is smaller.

## Decisions for you
- **No FMA.** Westmere has no FMA, so using it on newer CPUs would break the "same answer on every machine" property. It buys little because the loop is limited by loads (two per multiply-add). My estimate of the cost is up to ~10–15% on Skylake-class cores with data in L1, and roughly nothing elsewhere. If you don't need identical results across machines, it's a two-line change: `target("avx2,fma")` plus `_mm256_fmadd_ps`.
- **No AVX-512.** At a few thousand elements it would help only on Sapphire Rapids with data in L1, by at most ~1.3×. On Skylake-SP/Cascade Lake, 512-bit math triggers clock-speed drops that can slow surrounding code. I'd only try it if profiling on Sapphire Rapids still shows `dot()` hot.
- **Alignment.** Callers who allocate with 64-byte alignment avoid cache-line-split loads in the AVX2 path, which can be worth up to ~1.5× on Haswell/Skylake with data in L1.

## Build notes
1. Build `dot_fast.c` instead of `dot.c`. Both define `dot`, so linking both fails with a duplicate symbol.
2. It needs GCC ≥ 4.9 or clang ≥ 3.8. Since `dot.c`'s `for (int …)` already compiles under your default flags, you are on GCC ≥ 5 or clang.
3. **I couldn't compile with GCC for x86 here.** Some older GCC releases split unaligned 256-bit loads in two (you'd see `vinsertf128`), which roughly halves the AVX2 kernel's in-cache speed. Check with `objdump -d libvecmath.so | grep vinsertf128`. If it shows up, add `-mno-avx256-split-unaligned-load` to the global flags; it only affects 256-bit code.
4. Make sure the build host's compiler doesn't already default to AVX: `echo | cc -dM -E - | grep -c __AVX__` must print 0. Newer distros such as RHEL 10 target an AVX2 baseline, and then the whole library, not just `dot()`, would fail on Westmere.
5. 32-bit x86 builds get the portable C version: correct, but not vectorized.

## Next step
Run the harness on a Westmere box and a Sapphire Rapids box, and ideally a Skylake-SP. It checks every kernel that CPU supports, then prints accuracy and speed against the original:
```
cc -O2 test_dot.c -o test_dot && ./test_dot     # add --tests-only to skip the benchmark
```

Files are in `<work>`:
- `dot_fast.c`
- `test_dot.c`
- `dot.c` is unchanged.
