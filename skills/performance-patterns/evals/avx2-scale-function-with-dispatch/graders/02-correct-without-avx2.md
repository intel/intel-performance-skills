---
type: llm
focus: trace
weight: 1
---

On a CPU without AVX2, scale_f32 sets dst[i] = src[i] * k exactly for every element, for lengths 0 to 100 and several thousand (including lengths that are not a multiple of 8) and for unaligned pointers.
