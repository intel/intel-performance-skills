---
tags: [performance-patterns, codegen]
max_turns: 40
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

Our latency-report job is way slower than it should be. perf says ~70% of cycles are in std::sort internals called from compute_percentiles() (perf-report.txt); the code is in percentiles.cpp. Samples are float milliseconds and failed requests are recorded as NaN. We build with GCC 13 (-O2 -std=c++17) and run on Sapphire Rapids. How do I make this fast? Write the improved version to percentiles_fast.cpp, keeping the function signature.

Input files: percentiles.cpp, perf-report.txt
