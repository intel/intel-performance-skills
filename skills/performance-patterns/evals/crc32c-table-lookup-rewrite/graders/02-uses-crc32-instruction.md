---
type: llm
focus: trace
weight: 1
---

On SSE4.2 CPUs the new implementation uses the hardware CRC32 instruction (`_mm_crc32_u64`/`crc32q`) and/or PCLMULQDQ folding rather than a byte-at-a-time table lookup.
