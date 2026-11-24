---
type: llm
focus: trace
weight: 1
---

The response recommends giving each worker's stats their own cache line (e.g. `alignas(64)` or padding `struct worker_stats` to 64 bytes or more, or per-thread allocation), with code.
