---
tags: [linux-perf, readme-example]
max_turns: 25
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

My server gets slower as I add threads. Can you use perf c2c to find false sharing? I ran `perf c2c record -g -- ./pktsrv --threads 32 --duration 30` and then `perf c2c report --full-symbols --stdio --double-cl > c2c_report.txt` on the box (32 worker threads on a 56-core Xeon). The report and the source are attached; tell me what's going on and what to change.

Input files: c2c_report.txt, pktsrv.c, stats.h
