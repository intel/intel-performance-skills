---
type: llm
focus: trace
weight: 1
---

The widest SIMD path in dot_fast.c keeps at least two independent vector accumulators in its main loop, rather than a single accumulator register.
