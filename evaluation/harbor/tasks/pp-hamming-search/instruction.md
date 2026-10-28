Our image-retrieval service re-ranks candidate shortlists by the Hamming distance
between binary codes (4096-bit codes, 512 bytes each, today). The distance code is
`hamming_distance()` in `/app/hamming.c`, and it is now the top hotspot:
`/app/perf_report.txt` is a profile from production.

Make `hamming_distance()` as fast as you reasonably can on this machine.

Constraints:

- Keep the signature
  `uint64_t hamming_distance(const uint8_t *a, const uint8_t *b, size_t nbytes)`
  and the exact results. Other indexes use other code sizes, so any `nbytes`
  (not only multiples of 8) and any buffer alignment must work. Several services
  call `hamming_distance()` directly, not only through `hamming_nearest()`.
- `make -C /app` must still build `/app/libhamming.a` (the library other services
  link) and `/app/bench`. You may add source files and change the `Makefile`.
- We ship one build to the whole fleet, including x86 servers that are many
  generations older than this one, so the library must still produce correct
  results on a CPU that lacks whatever instructions you use for speed.

`/app/bench` prints the throughput of the current build if you want to measure.
