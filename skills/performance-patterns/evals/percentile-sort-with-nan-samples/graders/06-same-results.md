---
type: llm
focus: trace
weight: 1
---

On test data that contains NaNs, compute_percentiles in percentiles_fast.cpp returns the same p50/p90/p99/p999 values and failed count as a reference that drops the NaNs, sorts, and reads the same indices.
