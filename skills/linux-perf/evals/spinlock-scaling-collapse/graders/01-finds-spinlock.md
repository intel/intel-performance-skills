---
type: llm
focus: trace
weight: 1
---

The response identifies the spin lock in cache_lookup as the bottleneck that grows with thread count, citing that cache_lookup goes from ~30% of cycles at 1 thread to ~72% at 64 and that most of its samples sit on `lock cmpxchg` and `pause`.
