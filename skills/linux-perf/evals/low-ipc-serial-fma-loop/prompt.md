---
tags: [linux-perf, readme-example]
max_turns: 25
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

This function runs at only 1.2 IPC despite low cache misses, on a Xeon that should manage 4+. What's wrong, and how do I fix it? From the box I've attached perf stat and perf report output (perf-stat.txt), perf annotate of the function (perf-annotate.txt) and the CPU details (cpu.txt); the source is residuals.c.

Input files: residuals.c, perf-stat.txt, perf-annotate.txt, cpu.txt
