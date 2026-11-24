## Optimization Report: pts/fluxsim-1.2.0

pts/fluxsim is now **about 5.2× faster: 582.98 → 3019.71 MCells/s**. Back-to-back runs under the same conditions give 5.4×. The gain comes entirely from source changes; compiler flags are still the stock `-O2`. The outputs are unchanged: the diffused field and the column fluxes match the original bit for bit, and the printed checksum is identical.

### Baseline
- Score: **582.98 MCells/s** (5 runs, deviation 0.62%). This is the stock install: `cc -O2`, Apple clang 21, native arm64, M5 Max.

### Where the time went (before)
There's no `perf` on macOS, so I used:
- Instruments' `xctrace` Time Profiler and CPU Counters (Apple's top-down "bottlenecks" view).
- A harness that times each kernel on its own.
- The compiler's assembly and vectorizer reports.

| Kernel | Share of time | Useful work / backend-stalled | Cause |
|---|---|---|---|
| `column_flux` | **60.2%** | 9% / 88% | It reads a row-major array one column at a time, an 8 KB jump per element, so only 8 of each 128-byte cache line are used before eviction. Clang vectorizes this loop only for the case `nx == 1`. |
| `residual` | **28.4%** | 16% / 84% | It sums into a single accumulator. Clang vectorizes the subtract and square but has to add the terms one at a time, about 2.2 cycles per cell. |
| `diffuse` | 11.1% | 69% / 29% | Already vectorized. No problem here. |

Memory bandwidth isn't the limit. `diffuse` runs at about 0.18 ns/cell at every grid size from 128² to 1448²; only 2048² (beyond the caches) slows down. So the extra passes were costing instructions and stalls, not bandwidth.

### Changes Made
All changes are in `step()`, which replaces the three kernels in `fluxsim.c`.
1. **Column sums computed row by row.** Each column still adds its rows in order 0..ny−1, so the sums are bit-identical to before.
2. **One pass instead of three.** The field update, the column sums and the residual now happen in a single row-major sweep per iteration.
3. **Residual split across 8 independent partial sums** (the skill's parallel-accumulator pattern). This is plain C with no intrinsics and no `-ffast-math`; the compiler vectorizes it to NEON here and to SSE2/AVX on x86.
4. **Two small codegen fixes:**
   - One cell is handled separately so the vector loop starts 16-byte aligned (+18%).
   - `step()` is marked `noinline`. When clang inlines it into `main()`, it stops pairing the vector loads and stores and the loop runs about 25% slower.

Tried and not kept:
- `-O3` and `-mcpu=native`: no change.
- A clang-only `fp reassociate` pragma: slower than the portable version, and GCC would ignore it.
- Hand-written generic-vector code: no gain.
- In-place/rolling buffers: pointless, since the kernel isn't memory-bound.

The x86-specific advice in the skills (AVX-512, `vzeroupper`, CPU dispatch) doesn't apply to an arm64 binary.

### Result
- Score: **3019.71 MCells/s**. Two PTS runs of 5 trials each, taken in a quiet window: 3021.41 (±0.69%) and 3018.01 (±0.52%).
- Delta: **+2436.73 MCells/s (+418%)** vs. the recorded baseline.
  - In alternating runs of the original and new binary under the same conditions: 555.6 → 3019.7, **+443% (5.4×)**.
- Verdict: **Improved**

After the change, total profiled time for the same workload fell from 2684 ms to 567 ms, and `step()` accounts for 99% of it. The share of useful work went from 9–16% in the hot kernels to 72%. The kernel now takes about 1.25 cycles per cell, close to the core's vector throughput limit.

### Correctness
- **Field and column sums: bit-identical to the original at every iteration**, on 14 grid sizes including the real 1024×1024, 1500-iteration workload. This also holds for x86-64 builds (SSE2, and AVX2+FMA).
- **Residual:** the original and new binaries print the same checksum (`2.129678810452e+03`) for every configuration I tried.
  - Splitting the sum changes the order in which floating-point numbers are added, so the residual itself differs by at most 8.8×10⁻¹⁴ relative. That is rounding, not a logic error.
  - If you need the residual bit-identical to the original one-at-a-time sum, I measured a variant that keeps a single accumulator. It is fully bit-exact but only about 2.9× faster on the kernel instead of about 5.5×.

### Things to know
- **A reinstall undoes this.** `force-install` rebuilds from the original tarball. I didn't touch the test profile, the iteration count or the grid size; the change lives only in the source tree and the patch file.
- **The machine was shared during this session.** Other jobs used up to about 15 cores at times. That's why the final numbers come from alternating original/new runs taken only while the machine was quiet. The runs taken under load are also in the results store, and they show the same pattern.

### Pending Ideas
1. Ship the bit-exact residual variant (about 2.9×), if exact reproducibility of the residual matters.
2. Multithread the sweep. It would scale with core count, but the profile declares the test single-threaded (`scales_cpu_cores: 0`), so it would change what the test measures. That's your call.
3. Make the change survive a reinstall, by adding a patch step to `install.sh` or getting it into FluxSim itself.
4. Limited micro-tuning headroom remains, about 10–20%. Part of it is the 4 register copies per 8 cells that NEON's multiply-add instruction forces.

Would you like me to try another round of optimization?

### Files
All under `<work>/`:
- Patched source: `pts-root/installed-tests/pts/fluxsim-1.2.0/src/fluxsim/fluxsim.c`
- Installed binary (patched build): `pts-root/installed-tests/pts/fluxsim-1.2.0/fluxsim-bin`
- Patch (applies cleanly to `fluxsim-1.2.tar.gz`): `analysis/fluxsim-1.2-fused.patch`
- Original binary backup: `analysis/fluxsim-bin.orig`
- Results store (10 runs): `home/.pts-results.json`
- Profiles and test tools:
  - Instruments traces: `analysis/*.trace`
  - Kernel harness: `analysis/kbench.c`
  - Bitwise check against the original: `analysis/verify_final.c`
  - Alternating-run measurement script: `analysis/ab_quiet.sh`
