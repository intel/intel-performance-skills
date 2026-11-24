---
type: llm
focus: trace
weight: 1
---

The response identifies g_wstats (the per-worker `struct worker_stats` array, the four ~13-15% lines) as false sharing: each worker writes its own element, but the 16-byte elements of 8 workers sit on the same 128-byte line at different offsets.
