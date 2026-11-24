---
type: llm
focus: trace
weight: 1
---

percentiles_fast.cpp handles NaN samples correctly: it removes or partitions them out before sorting/selecting, or passes hasnan=true to x86-simd-sort, and still reports the failed count.
