---
type: llm
focus: trace
weight: 1
---

The response points out that sorting the samples with NaNs in them using std::sort/operator< is invalid (NaN breaks the strict weak ordering), so the existing assumption that NaNs end up at the back isn't guaranteed.
