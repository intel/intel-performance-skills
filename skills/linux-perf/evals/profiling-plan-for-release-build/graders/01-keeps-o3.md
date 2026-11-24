---
type: llm
focus: trace
weight: 1
---

The plan adds debug info without changing the optimization level being profiled: it keeps -O3 (e.g. adds -g to CMAKE_CXX_FLAGS or CMAKE_CXX_FLAGS_RELEASE) and does not switch to RelWithDebInfo or -O2, or, if it mentions RelWithDebInfo, it warns that this changes -O3 to -O2.
