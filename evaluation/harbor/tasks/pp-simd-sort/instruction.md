Our ranking service scores about one million candidates per request and then sorts
the scores. The sort is `sort_scores()` in `/app/ranker.cpp`, and it is now the top
hotspot: `/app/perf_report.txt` is a profile from production.

Make `sort_scores()` as fast as you reasonably can on this machine.

Constraints:

- Keep the signature `void sort_scores(float *scores, size_t n)` and the result:
  the array sorted ascending, element-for-element equal to what `std::sort` gives.
  Scores are floats, possibly ±inf but never NaN, and the array can have any length. Stability
  does not matter.
- `make -C /app` must still build `/app/libranker.a` (the library other services
  link) and `/app/bench`. You may add source files and change the `Makefile`.
  Other services link `libranker.a` with nothing but the C++ standard library
  (`g++ app.cpp /app/libranker.a`), and our build machines have no network access,
  so everything the library needs must live under `/app` and end up inside
  `libranker.a`. This machine does have internet access, so you may download
  third-party code now, as long as it is vendored under `/app`.
- The service already runs one request thread per core, so `sort_scores()` must
  stay single-threaded.
- We ship one build to the whole fleet, including x86 servers that are many
  generations older than this one, so the library must still produce correct
  results on a CPU that lacks whatever instructions you use for speed.

`/app/bench` prints the throughput of the current build if you want to measure.
