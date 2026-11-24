---
type: llm
focus: trace
weight: 1
---

The new implementation builds with plain `cc -O2 -fPIC` for x86-64 and returns exactly what the original crc32c() returns (same seed convention) for random buffers of 0 to 70,000 bytes at unaligned addresses, for chained calls, and for the '123456789' check string (0xE3069283 after the final inversion), with and without AVX available.
