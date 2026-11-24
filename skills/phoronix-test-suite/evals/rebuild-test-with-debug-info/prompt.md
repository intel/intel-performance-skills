---
tags: [phoronix-test-suite, sandbox]
max_turns: 40
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

I want to dig into pts/fluxsim with a profiler (Instruments on this Mac), but the installed binary was built without debug info, so I only see addresses. Can you rebuild it with debug info so that `phoronix-test-suite batch-run pts/fluxsim` (and my profiling) use the rebuilt binary? PTS is a local install here: the CLI is ./bin/phoronix-test-suite and its data directory is ./pts-root (same layout as ~/.phoronix-test-suite).

Input files: bin/, pts-root/
