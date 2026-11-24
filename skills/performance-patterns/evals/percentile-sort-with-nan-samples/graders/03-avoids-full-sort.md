---
type: llm
focus: trace
weight: 1
---

The new version avoids fully sorting every sample just to read four percentiles, using a selection algorithm (std::nth_element, x86simdsort::qselect, a partial sort or similar).
