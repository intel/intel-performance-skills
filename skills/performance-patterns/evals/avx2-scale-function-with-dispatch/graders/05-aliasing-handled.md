---
type: llm
focus: trace
weight: 1
---

The code handles aliasing deliberately: if it marks dst or src `restrict`, the response or a code comment states that dst and src must not overlap (so in-place calls with dst == src are not allowed); otherwise it leaves `restrict` off so in-place calls keep working.
