---
type: llm
focus: trace
weight: 1
---

The response points out, from the c2c offsets, that the lock word shares a cache line with fields written inside the critical section (hits, lru_head/lru_tail) and recommends separating them.
