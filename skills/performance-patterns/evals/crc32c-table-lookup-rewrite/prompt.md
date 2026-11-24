---
tags: [performance-patterns, codegen]
max_turns: 50
timeout_seconds: 1500
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

crc32c() in crc32c.c is about 25% of CPU in our storage engine: we checksum every 4 KiB page and 64 KiB WAL block. The checksums are stored on disk, so the function must keep returning exactly the same values, and it has to run on every x86-64 server in our fleet, from 2010-era Westmere boxes (SSE4.2 and PCLMUL, no AVX) up to Sapphire Rapids. Make it as fast as you reasonably can. Keep the function signature and put the new implementation in crc32c_fast.c (add more .c files if you need them). Our build compiles every file with plain `cc -O2 -fPIC`.

Input files: crc32c.c
