---
type: llm
focus: trace
weight: 1
---

The response explains that the test-and-set loop does an atomic `lock cmpxchg` on every spin iteration, so every waiting thread keeps pulling the lock's cache line into exclusive state, which is why throughput falls at 64 threads.
