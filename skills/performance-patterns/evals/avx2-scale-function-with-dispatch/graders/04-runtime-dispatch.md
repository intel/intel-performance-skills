---
type: llm
focus: trace
weight: 1
---

The AVX2 path is selected at runtime from CPU feature detection (e.g. `__builtin_cpu_supports("avx2")`, cpuid, `target_clones` or an ifunc), not only by a compile-time `#ifdef __AVX2__`.
