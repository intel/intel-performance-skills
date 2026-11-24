---
type: llm
focus: trace
weight: 1
---

The plan includes perf record with call-graph collection that works on an -O3 build without frame pointers (`--call-graph dwarf` or `lbr`, or rebuilding with -fno-omit-frame-pointer) and perf report to rank the hot functions.
