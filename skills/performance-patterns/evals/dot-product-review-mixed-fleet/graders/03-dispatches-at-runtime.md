---
type: llm
focus: trace
weight: 1
---

dot_fast.c chooses its implementation at runtime from CPU feature detection (`__builtin_cpu_supports`, cpuid, `target_clones` or an ifunc), so one binary uses AVX2 or AVX-512 where available and still runs on CPUs without AVX.
