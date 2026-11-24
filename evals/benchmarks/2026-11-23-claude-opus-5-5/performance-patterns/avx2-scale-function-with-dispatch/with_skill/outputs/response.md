I wrote `scale_f32` in `scale.c`, with your exact signature, and added a test file, `test_scale.c`. It builds with plain `cc -O2 -fPIC` and uses the AVX2 kernel only on CPUs that report AVX2. Every other CPU gets the plain C loop, and both paths give bit-identical results.

## How it works
- **Dispatch:** the first call checks once for AVX2 (`__builtin_cpu_supports("avx2")`, which also confirms the OS saves the 256-bit registers). It stores the chosen kernel in a function pointer, so every later call is one load and a jump. I avoided `ifunc`/`target_clones`, which can hit initialization-order problems in shared libraries.
- **No `-mavx2` needed:** the AVX2 kernel is compiled via `__attribute__((target("avx2")))`, and the rest of the file stays baseline x86-64. Disassembly confirms every AVX instruction is inside that one function.
- **The AVX2 kernel:**
  - steps one element at a time until `dst` is 32-byte aligned;
  - then does 4×8 floats per iteration with aligned stores;
  - finishes the last few elements one at a time;
  - calls `_mm256_zeroupper()` before returning.
- **Other targets:** non-x86 targets, and compilers other than GCC/clang, build only the plain loop. The same file therefore also builds on ARM; I tested that.

## Decisions you should know about
- **Overlapping buffers are handled; there's no `restrict`.** In-place calls (`dst == src`) use the vector path. The one layout where vector processing would change the result is `dst` starting 1–31 bytes after `src`, and that case falls back to the plain loop. Any overlap therefore gives exactly what the plain loop gives.
- **Aligned stores** avoid stores that straddle cache lines. There is also a compiler reason: GCC 8–10 at its default tuning split unaligned 256-bit stores in two (I checked GCC's tuning tables). Those are still the system compilers on RHEL 8, Ubuntu 20.04 and Debian 11.
- **Explicit `vzeroupper`:** older GCC only adds it automatically at `-O2`/`-O3`. Without it, an `-O0` or `-Os` build would slow down the code that runs after it.
- **Minimum compilers** for the AVX2 path are GCC 4.9 or clang 6.
- **Header:** add `void scale_f32(float *dst, const float *src, float k, size_t n);` to the library's public header.

## Testing
This machine is an Apple Silicon Mac, so I built for x86_64 and ran under Rosetta, which reports AVX2 only when `ROSETTA_ADVERTISE_AVX=1` is set.
- **Dispatch:** the dispatcher picked the plain loop when AVX2 wasn't reported and the AVX2 kernel when it was.
- **Correctness:** 99,314 checks per kernel, all bit-for-bit matches against the plain loop. They cover:
  - every alignment;
  - in-place calls;
  - overlaps of −40 to +40 floats;
  - NaN, infinity, denormal and ±0 values;
  - out-of-bounds writes.
- **More builds:** also clean with AddressSanitizer/UBSan, at `-O0`, `-Os` and `-O3`, and on ARM with clang and GCC 14.
- **As a library:** I built it as a shared library with only `-O2 -fPIC`, and it exports only `scale_f32`.
- **The test catches real bugs:** I planted 8 typical SIMD bugs (tail overrun, wrong overlap check, over-read, and so on) and the test caught every one.
- **Threads:** ThreadSanitizer found no race when 8 threads hit the first call at once.

To run the test: `cc -O2 -o test_scale test_scale.c && ./test_scale` (add `-fsanitize=address` to also catch reads past the end).

## Not verified
- **No speed numbers.** Rosetta translates AVX2 into ARM instructions, so timings here say nothing about real x86 hardware.
- **No real non-AVX2 CPU.** Rosetta runs AVX2 code even when it doesn't report AVX2, so a crash-free run proves nothing on its own. The proof that the fallback contains no AVX instructions is the disassembly check. It's worth one run on a real non-AVX2 machine, or under Intel SDE: `sde64 -snb -- ./test_scale`.
- **No x86 GCC compile.** No x86 GCC is available here, so the GCC behaviour above comes from GCC's source, not from compiling with it. 32-bit x86 is enabled in the code but untested.

## Possible follow-ups
- **Older CPUs:** the kernel actually only needs AVX. Changing `"avx2"` to `"avx"` in its two places would also cover Sandy Bridge and Ivy Bridge.
- **Faster fallback:** the fallback is the plain loop. Clang auto-vectorizes it with SSE2; GCC at `-O2` probably leaves it scalar. If speed on non-AVX2 machines matters, an SSE2 version is about 10 lines.
- **AVX-512:** I didn't add it. This loop is limited by store and memory bandwidth rather than arithmetic, and I couldn't test it here.
