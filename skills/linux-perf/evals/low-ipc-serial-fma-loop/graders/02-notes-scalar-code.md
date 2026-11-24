---
type: llm
focus: trace
weight: 1
---

The response notes that the hot loop is scalar double precision (`sd` instructions on xmm registers) even though the CPU supports AVX2/AVX-512, i.e. the loop isn't vectorized.
