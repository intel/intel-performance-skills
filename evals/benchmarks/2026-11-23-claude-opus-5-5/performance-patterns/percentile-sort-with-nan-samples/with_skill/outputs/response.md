The job fully sorts 5–20M floats just to read four values out of the result, and with NaNs in the data that sort also gives wrong answers. `percentiles_fast.cpp` keeps the signature and uses selection instead of sorting. It handles NaN correctly, and on Sapphire Rapids it can optionally use x86-simd-sort's AVX-512 quickselect.

On 20M samples:
- **With plain `std::nth_element` (no new dependency):** 7.4x faster (g++/libstdc++, `-O2 -std=c++17`).
- **With x86-simd-sort's AVX2 kernel:** 14x faster.
- **Results:** identical to today's code whenever no request failed.

## What the profile shows

| perf entry | Cause | Fix |
|---|---|---|
| `__introsort_loop` + `__final_insertion_sort`, 70% | An O(n log n) full sort, only to read 4 values | O(n) selection |
| `__memmove_avx512_unaligned_erms`, 15.6% (plus `clear_page_erms`, `_int_malloc`) | Each call copies 20–80 MB into the by-value parameter, into a freshly allocated block | `std::move` at the call site (below) |
| `compute_percentiles` self time, 7.8% | `count_if` makes a second pass over the data | Folded into one NaN-removal pass |

## The current results are wrong whenever a request failed

`std::sort` needs a strict weak ordering, and `operator<` stops being one once NaN is present. That is undefined behaviour, and the comment "NaNs end up at the back" isn't what happens:

- **libstdc++ (what GCC 13 uses), 1M samples** (`bench/nan_sort_demo.cpp` reproduces this):
  - At 0.01% failures, all four percentiles were wrong.
  - At 1% failures, p99 came out as 104.0 ms (true value 129.4 ms) and p99.9 as 125.6 ms (true value 239.2 ms).
  - About 99% of the NaNs ended up inside the first `ok` slots, which is the part the percentiles are read from. With libc++ the reported p99 was even NaN.
- **libc++ in debug-hardening mode** aborts with *"your comparator is not a valid strict-weak ordering"*.
- **In the test suite**, the original disagreed with the correct answer on 71% of the NaN-containing inputs (12,240 of 17,160).

After you deploy this, p99/p99.9 will rise for minutes that had failures. That's the bug fix, not a regression; tell whoever owns the dashboards and alerts.

## What `percentiles_fast.cpp` does

1. **One branch-free pass** moves the non-NaN values to the front and counts the failures, replacing `count_if`.
   - I chose this over `std::partition` because its cost doesn't depend on the failure rate. At 50% failures, `std::partition` took 54 ms against 5 ms (20M samples), because of branch mispredictions.
   - The NaN test checks the bit pattern. Under `-ffast-math`, both GCC and clang turn `std::isnan` into "always false"; I checked this.
2. **Four selections, in ascending order.** p50 is selected over all samples, p90 only over the part above p50, p99 above p90, and p99.9 above p99. That's about n + n/2 + n/10 + n/100 work in total. The rank formula is still `floor(q*(ok-1))`, so results are bit-identical to the old code on NaN-free data.
3. **Selection kernel:**
   - By default, `std::nth_element`.
   - With `-DPERCENTILES_USE_X86SIMDSORT`, it calls `x86simdsort::qselect(..., /*hasnan=*/false)`. The library picks AVX-512, AVX2 or scalar code at run time. So this file needs no `-march` flags, and the binary still runs on any x86-64.
   - `hasnan=false` is only safe because step 1 has already removed the NaNs. Keep that order if anyone edits this.
   - x86-simd-sort's `qsort` would also speed up the sort, but you don't need a sort, so `qselect` is the right call.

## One change at the call site (signature unchanged)

```cpp
// in run_report(), if the raw samples aren't used after this call:
Percentiles p = compute_percentiles(std::move(samples));   // was: compute_percentiles(samples)
```

This removes the memmove, page-zeroing and malloc entries. If you refill the same vector every minute, call `reserve()` on it again after the move.

## Measurements (20M samples, 1% NaN; "fast" includes the `std::move`)

| Build on this machine | Original | Fast | Speedup |
|---|---|---|---|
| g++-14 / libstdc++, `-O2 -std=c++17`, `std::nth_element` (closest to your GCC 13) | 1261 ms | 170 ms | **7.4x** |
| clang x86-64 under Rosetta, `std::nth_element` | 546 ms | 156 ms | 3.5x |
| clang x86-64 under Rosetta, x86-simd-sort AVX2 kernel | 599 ms | 43 ms | **14x** |

Caveats:
- **Hardware:** this is an Apple M5 Max, and x86 ran under Rosetta 2, which has no AVX-512. I couldn't measure the AVX-512 kernel your SPR boxes will use; `bench/run.sh` measures it there.
- **libc++ vs libstdc++:** libc++'s `std::sort` is about 2x faster than libstdc++'s, so the x86 rows understate the gain against your GCC build.
- **The copy:** here it costs only 2–6 ms. In your Linux profile it's about 17%, and once the sort is gone it would be a larger share, so do the `std::move`.
- **Whole job:** `compute_percentiles` was 94% of the job. After this change `load_samples` and `write_report` matter, so the end-to-end gain will be smaller than the function speedup.

## Verification

- `bench/test_percentiles.cpp` runs 22,037 cases, including:
  - empty and all-NaN inputs, and NaN variants (signalling, negative, with payloads)
  - ±0, ±Inf and denormals
  - heavy ties, sorted and reversed input, NaN clusters
  - every size from 1 to 300, random sizes up to 50K, and 1M, 5M and 20M samples
- The fast version matched the reference on every input, and matched the original on all of the roughly 4,870 NaN-free inputs.
- 0 failures with g++-14, clang arm64, clang x86-64, both x86-simd-sort paths (scalar and AVX2), ASan+UBSan, and `percentiles_fast.cpp` built with `-ffast-math`.
- It compiles cleanly with `-Wall -Wextra -Wpedantic -Wconversion -Wshadow -Werror`.

## Running it on an SPR box

```sh
CXX=g++-13 ./bench/run.sh                                  # std::nth_element path, no new dependency

git clone https://github.com/numpy/x86-simd-sort && cd x86-simd-sort
meson setup --buildtype release builddir && cd builddir && meson compile && sudo meson install
EXTRA="-DPERCENTILES_USE_X86SIMDSORT $(pkg-config --cflags x86simdsortcpp)" \
LIBS="$(pkg-config --libs x86simdsortcpp)" CXX=g++-13 ./bench/run.sh
XSS_DISABLE_AVX512=1 ./bench/build/bench_percentiles       # compare against the AVX2 kernel
```

Then re-profile the real job with `perf record -g`. `std::__introsort_loop` should be gone, and so should `__memmove_avx512_unaligned_erms` once the `std::move` is in. If you need more speed after that:
- the endpoints are independent, so they can be processed in parallel;
- if approximate percentiles are acceptable, you could fill a log-linear histogram as samples arrive (HdrHistogram-style) and stop storing raw samples at all.

Files are in `work/`:
- `percentiles_fast.cpp`
- `bench/test_percentiles.cpp`, `bench/bench_percentiles.cpp` and `bench/run.sh`
- `bench/nan_sort_demo.cpp` (reproduces the NaN bug)
- `bench/nanpass_bench.cpp` (`std::partition` vs the branch-free pass)
