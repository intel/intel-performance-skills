---
type: llm
focus: trace
weight: 1
---

On a CPU with AVX2 and FMA, dot_fast.c returns results within 1e-4 relative error of a double-precision reference, including lengths that are not a multiple of the vector width.
