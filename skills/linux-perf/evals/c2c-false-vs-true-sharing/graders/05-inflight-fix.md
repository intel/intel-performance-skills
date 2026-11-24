---
type: llm
focus: trace
weight: 1
---

The response recommends taking the shared atomic `inflight` counter off the per-packet path (e.g. per-worker counters that the monitor sums, batching, or dropping it), rather than only padding it.
