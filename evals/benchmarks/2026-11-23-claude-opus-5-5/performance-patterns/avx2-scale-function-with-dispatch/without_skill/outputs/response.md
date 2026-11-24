## AVX2 `scale_f32` with runtime dispatch

I've added `scale.c`, and it builds with your existing `cc -O2 -fPIC`. Don't add `-mavx2`: that would let the compiler put AVX2 instructions anywhere in the library, including the fallback path. Both the AVX2 path and the fallback pass the full test suite, and the right one gets picked at run time. I tested on an Apple Silicon Mac using Rosetta, so real x86 hardware and an x86 GCC build are still untested (see below).

### How it works

- **AVX2 kernel:** `scale_f32_avx2` is the only function compiled for AVX2, using `__attribute__((target("avx2")))`.
  - It does 32 floats per iteration (four 256-bit vectors), then 8 at a time, then up to 7 one by one. So it never reads or writes past the end of the arrays.
  - Loads and stores don't require aligned buffers.
  - The compiler adds `vzeroupper` before it returns, so the caller's SSE code isn't slowed down.
- **CPU check (`cpu_has_avx2`):** it checks three things:
  - the CPU reports AVX and that the OS manages extended register state (CPUID leaf 1);
  - the OS actually saves the 256-bit registers (XGETBV);
  - the CPU reports AVX2 (CPUID leaf 7).

  Checking only the AVX2 CPUID bit isn't enough: the kernel would crash with SIGILL under an OS or hypervisor that left AVX disabled.
- **Dispatch:** a static function pointer starts out pointing at a resolver.
  - The first call runs the CPU check, stores the chosen kernel and passes the call on.
  - Every later call is one load plus an indirect jump.
  - Relaxed atomic load/store keep simultaneous first calls from several threads safe.
  - Unlike GNU ifunc, this also works on macOS and musl, and doesn't depend on load order.
- **Fallback:** a plain loop. It's used on x86 without AVX2, on non-x86 targets, and with compilers other than GCC ≥ 4.9 or clang (the dispatch code is compiled out for those).
- **Contract:** `dst == src` (in-place) works; otherwise the two arrays must not overlap. With `n == 0` the pointers are never touched.

### How I checked it

I built x86-64 binaries with Apple clang 21 and ran them under Rosetta 2. By default Rosetta hides AVX (a machine without AVX2); with `ROSETTA_ADVERTISE_AVX=1` it advertises AVX2. Rosetta runs AVX2 instructions even when it isn't advertising them, so "didn't crash" proves nothing. The tests check which kernel was chosen instead.

- **`test_scale.c`** compares every result bit-for-bit with `src[i] * k`. It covers:
  - sizes 0–300 plus larger ones up to 65,543;
  - every combination of misaligned `src`/`dst`, and in-place calls;
  - marker values on both sides of `dst`, to catch writes outside it;
  - special values (±0, ±inf, NaN, denormals, overflow);
  - arrays that end exactly at an inaccessible page, so any out-of-bounds access crashes;
  - 8 threads making the first call at the same time.

  All checks pass in both modes (253,913 with AVX2, 169,279 without), at `-O0` through `-O3` and `-Os`, and under ASan, UBSan and TSan.
- **Kernel selection:** the test asserts which kernel was chosen. On the separately built `libscale.dylib`, debugger breakpoints showed the expected sequence: resolver, then `scale_f32_scalar` on every call without AVX, and resolver, then `scale_f32_avx2` with AVX. My check agrees with `__builtin_cpu_supports("avx2")` in both modes.
- **Generated code:** at every `-O` level, no AVX instruction appears outside `scale_f32_avx2`, so nothing can SIGILL on an older CPU. `vzeroupper` is there, and only `scale_f32` is exported.
- **Do the tests catch bugs?** I planted five typical mistakes, and every one was caught:
  - a full-vector store at the tail (writes past the end);
  - an off-by-one tail;
  - an "overlapping last vector" tail, which corrupts in-place calls;
  - a copy-paste error in the unrolled loop;
  - a dispatcher that ignores the CPU check.

  TSan reports a race if the atomics are replaced by plain accesses.
- **Other builds:** compiles with no warnings under `-Wall -Wextra -Wpedantic -Werror` (C89 to C17) for x86-64 Linux, 32-bit x86 with `-fPIC`, MinGW, and arm64 with both clang and GCC 14.

### Not verified

- **No real x86 hardware or x86 GCC here.** The x86 path was only built and run with clang; GCC 14 was only used for the arm64 build. A quick run on your Linux build machine would close that gap:
  `cc -O2 -pthread test_scale.c -o test_scale && ./test_scale`
- **No meaningful speed numbers.** Under Rosetta the AVX2 path was about 1.6–2x faster than a truly scalar fallback, but slower than clang's auto-vectorized SSE2 fallback. That's a side effect of Rosetta's translation, not real hardware behaviour. Run `bench_scale.c` on your target machines. On real hardware, expect the gain mostly for arrays that fit in cache; very large arrays are limited by memory bandwidth whichever path runs.
- **The OS-disabled-AVX case wasn't exercised.** Under Rosetta the OS register-state check always agrees with CPUID, so that branch never triggered.

### Decision for you

On machines without AVX2, a GCC `-O2` build runs the fallback one element at a time; clang vectorizes it with SSE2, except for in-place calls. If speed on those machines matters, I can add an SSE2 kernel. Every x86-64 CPU has SSE2, so it needs no CPU check. I also deliberately left out aligning the destination before the main loop, non-temporal stores for huge arrays, and AVX-512.

Files are in the working directory:
- `scale.c`
- `test_scale.c`
- `bench_scale.c`

Each file's header comment has its build command.
