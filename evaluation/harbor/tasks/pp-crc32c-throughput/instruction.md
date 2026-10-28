Our storage service checksums every block it writes with CRC32C. The checksum
code is `crc32c()` in `/app/crc32c.c`, and after the last round of I/O work it is
now the top hotspot: `/app/perf_report.txt` is a profile from production.

Make `crc32c()` as fast as you reasonably can on this machine.

Constraints:

- Keep the signature `uint32_t crc32c(uint32_t crc, const void *buf, size_t len)`
  and the exact results. The value is part of our on-disk format, and callers
  chain it: `crc32c(crc32c(0, a, n), b, m)` must equal the checksum of `a`
  followed by `b`.
- `make -C /app` must still build `/app/libcrc32c.a` (the library other services
  link) and `/app/bench`. You may add source files and change the `Makefile`.
- We ship one build to the whole fleet, including older x86 servers without
  AVX-512, so the library must still produce correct results on a CPU that
  lacks whatever instructions you use for speed.

`/app/bench` prints the throughput of the current build if you want to measure.
