---
type: llm
focus: trace
weight: 1
---

The fast path removes the serial dependency of a single CRC chain, e.g. by running several independent CRC streams over different parts of the buffer and combining them, or by folding with PCLMULQDQ, instead of feeding one `_mm_crc32_u64` accumulator 8 bytes at a time.
