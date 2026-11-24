---
type: llm
focus: trace
weight: 1
---

dot_fast.c builds with plain `cc -O2 -fPIC` for x86-64 and, on a CPU without AVX, returns results within 1e-4 relative error of a double-precision reference, including lengths that are not a multiple of the vector width.
