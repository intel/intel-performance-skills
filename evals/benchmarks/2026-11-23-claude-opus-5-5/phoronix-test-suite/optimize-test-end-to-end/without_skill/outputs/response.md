# pts/fluxsim is now 4.1x faster, with bit-identical results

I measured both versions with the same PTS harness and the same workload (1024×1024 grid, 1500 iterations, `-O2`):

| | pts/fluxsim-1.2.0 (stock) | pts/fluxsim-1.3.0 (optimized) | Change |
|---|---|---|---|
| PTS, 3 alternating rounds × 3 trials | 475.8 MCells/s (±2.7%) | 1967.1 MCells/s (±2.1%) | **4.13x** |
| Fresh confirmation run | 499.8 MCells/s | 2010.2 MCells/s | 4.02x |
| Cycles per cell (hardware counters, unaffected by clock speed) | 7.90 | 1.88 | 4.2x |
| Instructions per cycle (IPC) | 1.72 | 5.50 | |
| Checksum | 2.129678810452e+03 | 2.129678810452e+03 (bit-identical) | |

Other workloads were running on the machine the whole time (load average 5–13), so the absolute numbers are lower than on an idle machine. My first baseline, taken at load 1.7, was 587.9 MCells/s. The ratio is the number to trust, because I always ran old and new alternately.

## What the profile showed

Since there's no perf, I used Instruments from the command line (`xctrace`). I took Time Profiler samples every 1 ms and mapped each sample's instruction address to a loop in the stock binary, where every function is inlined into `main`. I also ran the CPU Counters template.

| Loop in the stock binary | Share of samples |
|---|---|
| `column_flux()` | **59.5%** |
| `residual()` | 28.0% |
| `diffuse()` | 12.3% |

- **`column_flux()`** walks the grid one column at a time. Each step jumps 8 KB in memory, and every add waits for the previous one. clang only vectorizes this loop when `nx == 1`.
- **`residual()`** is a separate pass over the grid. Its sum has to be added up in a fixed order, so every cell waits on one `fadd`.
- **Counters:** 81% of cycles were stalled in the back end and only 17% did useful work. The code was waiting on dependency chains, not on memory bandwidth.
- **Compiler flags don't fix it.** `-O3`, `-O3 -mcpu=native` and `-funroll-loops` all land within ±2%. `-ffast-math` gives only 1.26x and changes the results.

## What I changed (FluxSim 1.2 → 1.2-opt)

1. **Swapped the loop order for the column sums.** They now build up row by row in `colsum[]`. Each column is still summed top to bottom, so the result bits don't change, but memory is read in order and the adds vectorize.
2. **Merged the three passes into one sweep per row.** Diffuse, column sums and residual now happen together, so each step reads and writes the grid once instead of making five passes. The residual's chain of adds now overlaps with the stencil arithmetic. These first two changes give 3.2x (2.55 cycles/cell).
3. **Computed two time steps per sweep.** The second step trails the first by two rows, so it never overwrites a row the first step still needs. That keeps two independent residual sums in flight and brings it to 4.2x (1.88 cycles/cell).

Each iteration still does the same work, including all the per-column sums. The output format and workload are unchanged. After the change, 98% of samples fall in the merged two-step loop. The floating-point units are close to saturated: IPC is 5.5, about 3.5 floating-point/SIMD operations per cycle.

## Verification

- **Bit-for-bit identical to 1.2 in 122 configurations, with 0 mismatches.** I compared the exact checksum plus hashes of the final `grid`, `next` and `colsum` arrays. The configurations cover grids from 16 to 2048 (including odd sizes) and 1 to 101 iterations (including odd counts). I repeated this at `-O2`, `-O3`, `-mcpu=native`, `-funroll-loops` and `-ffp-contract=off`, and also on x86-64 under Rosetta.
- **The checksum's last bit can differ in two build modes:** x86 with `-mfma`, and `-ffp-contract=fast`. In those modes the compiler handles the original's `r += d*d` differently from the new code, so the checksum differs by 1–2 units in the last place. The grid itself is still bit-identical, and the printed checksum is the same.
- **The binary PTS built for 1.3.0 is the one I verified:** its machine code is identical.

## How it fits into PTS

- I added a new test profile, `pts/fluxsim-1.3.0`, plus a new source tarball `fluxsim-1.2-opt.tar.gz` in the download cache, with its checksums in `downloads.xml`. I installed it with `phoronix-test-suite install pts/fluxsim-1.3.0`.
- The workload arguments and the result parser are unchanged. The only edits are the tarball name, the version strings and some notes.
- `pts/fluxsim-1.2.0` is untouched, so the baseline can still be reproduced.
- Unversioned `pts/fluxsim` now resolves to 1.3.0. I bumped the minor version because 1.3.0 results aren't comparable with public 1.2.0 results.

```
./bin/phoronix-test-suite run pts/fluxsim-1.2.0   # before
./bin/phoronix-test-suite run pts/fluxsim-1.3.0   # after (same as plain pts/fluxsim)
```

## Things I deliberately didn't do

- **Reorder the residual sum, or use `-ffast-math`:** either one changes the checksum.
- **ARM NEON intrinsics:** they might add another 10–25%, but ARM-only tuning would tilt a cross-platform benchmark.
- **Threads:** the profile is single-threaded by design (`scales_cpu_cores: 0`).
- **Skip the column sums that are never read:** only `colsum[it % nx]` reaches the checksum, but dropping the rest would remove benchmark work.

## Files

All paths are under `<work>/`:

- Optimized source: `analysis/pkg/fluxsim/fluxsim.c`
- Patch against 1.2: `analysis/fluxsim-1.2-opt.diff`
- Tarball: `pts-root/download-cache/fluxsim-1.2-opt.tar.gz`
- New profile: `pts-root/test-profiles/pts/fluxsim-1.3.0/`
- Installed test: `pts-root/installed-tests/pts/fluxsim-1.3.0/`
- Profiles and counter summaries: `analysis/prof/` (`orig-time.trace` and `final-time.trace` open in Instruments)
- Benchmark logs: `analysis/bench-*.txt` and `analysis/pts-ab/`
- Verification harness: `analysis/verify/verify_wrap.c` and `analysis/verify/matrix.sh`

## Decisions for you

- **Default version:** should plain `pts/fluxsim` stay pointed at 1.3.0? If you want the stock benchmark as the default, for example to compare against public results, use versioned IDs or remove the 1.3.0 profile.
- **Simpler variant:** if you'd rather not carry the two-step sweep, changes 1 and 2 alone give 3.2x. That version is in `analysis/opt/v1/fluxsim.c`.

I can also turn this into a shareable page if you want to pass it on.
