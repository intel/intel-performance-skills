---
type: llm
focus: trace
weight: 1
---

The response notices that the read-mostly `shutting_down` flag (offset 0x10, read in every worker's loop condition) shares that cache line with the constantly written `inflight` counter, and recommends moving it to a separate cache line.
