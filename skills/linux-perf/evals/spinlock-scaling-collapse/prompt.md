---
tags: [linux-perf, readme-example]
max_turns: 25
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

This workload doesn't scale past 8 cores: throughput is flat from 8 to 32 threads and drops at 64. Help me find the bottleneck. Everything I collected on the 64-core box is attached: a thread sweep (sweep.txt), perf report at 1 and 64 threads (perf-1t.txt, perf-64t.txt), perf annotate of the function that blows up (annotate-cache_lookup.txt), a perf c2c report at 64 threads (c2c-64t.txt), and the source (lru_cache.c).

Input files: sweep.txt, perf-1t.txt, perf-64t.txt, annotate-cache_lookup.txt, c2c-64t.txt, lru_cache.c
