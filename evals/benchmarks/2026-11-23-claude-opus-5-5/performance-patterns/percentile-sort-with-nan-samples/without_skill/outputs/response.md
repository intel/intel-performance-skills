# Making `compute_percentiles()` fast

The sort was the bottleneck, and it was also a bug. `percentiles_fast.cpp` replaces it with an exact two-pass radix select: no sort and no data movement. It keeps the signature and the index formula, and fixes the NaN handling.

On 20M samples it goes from **~1.3 s to ~16–19 ms (70–80x)** for continuous latency values, and about 15x for whole-millisecond values. The results now match a NaN-filtered full sort exactly. The biggest cost left is the by-value copy, and you remove that at the call site with `std::move`.

## What the profile shows

| perf entry | cause |
|---|---|
| 70.4% `std::sort` (`__introsort_loop` + `__final_insertion_sort`) | A full O(n log n) sort of 5–20M floats, about 24 levels of branchy partitioning, just to read 4 values. |
| 15.6% `__memmove_avx512_unaligned_erms` (+ `clear_page_erms`, `_int_malloc`) | `compute_percentiles(std::vector<float> samples)` takes the vector by value, so every call allocates, page-faults and copies 20–80 MB. |
| 7.8% `count_if` | A second full pass over the data only to count NaNs. |

**There's also a correctness bug.** `std::sort` with NaNs is undefined behaviour, because `<` is not a strict weak ordering once NaN is involved. The comment "NaNs end up at the back" is wrong for libstdc++:

- **NaNs stay scattered.** I sorted 1M samples with 1% NaN: the first NaN was at index 178, and only 8 of the 9,853 NaNs ended up in the last 9,853 slots.
- **The rest isn't sorted either.** The non-NaN values were out of order too, so `samples[q*(ok-1)]` reads from a scrambled array.
- **The tail is badly under-reported.** On 20M samples with 1% failures, the original reported p99 = 84.2 and p99.9 = 99.5. The true values are 101.9 and 173.8, so p99.9 is about 40% low.
- **It can even return NaN.** At 5M samples it returned **p99 = NaN**.

Every minute in which any request failed has produced wrong percentiles.

## What `percentiles_fast.cpp` does

The signature and the definition are unchanged:
- `failed` is the number of NaN samples.
- pXX is the `floor(q*(ok-1))`-th smallest non-NaN sample, using the same `double` expression.
- All four values are NaN if no request completed.

How it works:

- **NaNs are taken out before anything is ordered,** and they're counted in the same pass.
  - Detection is a bit test, so it keeps working under `-ffast-math`.
  - I checked the original under `-ffast-math`: it reports `failed=0` and p50 = NaN, because `std::isnan` gets folded away.
- **From 40K samples up, it uses an exact radix select on the float bit patterns.** A standard bit flip makes unsigned integer order equal float order.
  1. Pass 1 histograms the top 16 bits (sign, exponent and 7 mantissa bits) into 64K bins, with NaNs in a bin of their own. Each counter is kept 4 times, interleaved. Without that, runs of equal values (sorted input, whole-ms timers) would serialise on one counter's load→add→store chain.
  2. A walk over the bins finds which bin holds each of the 4 target ranks.
  3. Pass 2 histograms the low 16 bits of only the samples that fall in those ≤4 bins, which pins each rank to one exact float. A fixed 16-sample "any match?" test skips most of the data.
     - GCC 14 vectorises that test at `-O2`. GCC 13's `-O2` uses the same cost model ("very-cheap").
     - clang emits plain SSE2 for it on x86-64, so no `-march` is needed.
  
  Both passes are streaming reads. There are no swaps, nothing is written back to the 80 MB array, and the histograms take about 1–2 MB.
- **Below 40K samples, it partitions out the NaNs and runs `std::nth_element` on shrinking ranges:** p50 over everything, then p90 over the upper part, and so on. 40K is the measured crossover point, because the radix path has about 0.1 ms of fixed cost.

## The call-site change (signature unchanged)

The copy is made by the caller, because the parameter is taken by value. If `run_report()` doesn't use the vector after the call:

```cpp
Percentiles p = compute_percentiles(std::move(samples));   // no 20-80 MB copy
```

Once the sort is gone, this copy is the largest remaining cost of the call.

Here's a rough projection from your profile's proportions. What's left after that is mostly `load_samples` and `write_report`:

| | whole job vs. today |
|---|---|
| without the `std::move` | about 4x faster |
| with the `std::move` | about 9–14x faster |

On the large path the new code only reads the samples. So if you can change the signature later, taking `const std::vector<float>&` would avoid the copy even for callers that keep their data.

## Measurements

Setup:
- **Machine and compiler:** Apple M5 Max with GCC 14.2 `-O2 -std=c++17` and libstdc++, which has the same `std::sort`/`nth_element` code as GCC 13.
- **Timing:** min/median of interleaved runs.
- **Data:** synthetic lognormal latencies (median 20 ms) with 1% NaN.

| | original | fast, called with lvalue (copy) | fast, called with `std::move` |
|---|---|---|---|
| 20M, continuous values | 1,303–1,364 ms | 20–24 ms (57–65x) | 16–19 ms (71–81x) |
| 20M, whole-ms values | 429–447 ms | 31–36 ms (~13x) | 27–29 ms (~15x) |
| 5M, continuous values | 294 ms | 4.1 ms (72x) | 3.7 ms (80x) |

- **Simpler alternative:** NaN partition plus nested `nth_element` takes 171 ms at 20M, which is 7.6x. That's the small-n path in the file, if you'd rather give up the extra ~10x for simpler code.
- **x86-64 under Rosetta 2** (clang/libc++, SSE2) shows the same pattern: 549 → 16 ms, or 34x. libc++'s sort is faster than libstdc++'s, which is why the ratio is smaller.

**Caveat:** I could not measure on Sapphire Rapids. This machine has no SPR or AVX-512, no Linux `perf`, and no GCC 13. The machine was also shared and at times heavily loaded, so expect about ±15% noise. The ratios should carry over; the absolute numbers won't. Please run the benchmark below on the real box.

## Verification

- **Tests.** `test_percentiles.cpp` has two parts:
  - **Hand-derived cases:** where NaNs sit, all-NaN and empty input, ±inf counted as valid samples, unusual NaN encodings (including NaNs whose bits sit right next to ±inf), negatives and ±0, heavy duplicates, and the loop tails.
  - **Randomised comparison** against a NaN-filtered full sort: 24 sizes × 10 distributions × 3 seeds, including random bit patterns.
- **Results:**
  - **New code:** passes 739/739 under GCC `-O2`, GCC with `_GLIBCXX_ASSERTIONS`, GCC `-O3 -ffast-math`, clang ASan+UBSan, and x86-64.
  - **Original:** fails 333/739, which is every input that contains a NaN.
- **Mutation check.** I broke the new code 11 ways, for example a wrong NaN test, wrong ordering of negatives, dropped loop tails and off-by-one ranks. All 10 changes that alter behaviour fail the tests, one of them only under ASan. The eleventh doesn't change behaviour.

To reproduce with GCC 13 on the SPR box:

```sh
g++ -O2 -std=c++17 test_percentiles.cpp percentiles_fast.cpp -o test_fast && ./test_fast
g++ -O2 -std=c++17 test_percentiles.cpp percentiles.cpp -o test_orig && ./test_orig   # shows the NaN bug
g++ -O2 -std=c++17 -Dcompute_percentiles=compute_percentiles_orig -c percentiles.cpp -o orig.o
g++ -O2 -std=c++17 bench_percentiles.cpp percentiles_fast.cpp orig.o -o bench
taskset -c 2 ./bench 20000000 7
g++ -O2 -std=c++17 -fopt-info-vec-optimized -c percentiles_fast.cpp   # line 206 should say "loop vectorized"
```

## Notes and options

- **Build flags:** you don't need special flags. `-march=sapphirerapids` would only widen the block test, while pass 1 is a scalar histogram and dominates the time, so measure before bothering. Keep `-ffast-math` away from any code that depends on NaN.
- **Parallelism:** the function keeps no shared state. If you report many endpoints per minute, processing endpoints in parallel across SPR's cores is the next easy multiplier.
- **Ingestion-side histograms:** if you control ingestion, a streaming histogram per endpoint (HdrHistogram or DDSketch) would avoid storing 20M samples at all. The trade-off is a bounded relative error instead of exact order statistics.
- **Assumption:** I kept the original's percentile definition (lower nearest-rank, `floor(q*(ok-1))`) and count ±inf as completed requests, as the original does. The only possible difference from a NaN-filtered `std::sort` is the sign of a zero result, which `std::sort` doesn't define either.

Files are in the working directory:
- `percentiles_fast.cpp`: the new implementation
- `test_percentiles.cpp`: the tests
- `bench_percentiles.cpp`: the benchmark
