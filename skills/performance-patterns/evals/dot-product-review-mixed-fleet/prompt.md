---
tags: [performance-patterns, readme-example, codegen]
max_turns: 40
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

Review this dot-product loop for performance issues:

```c
for (int i = 0; i < n; i++) sum += a[i] * b[i];
```

It's the `dot()` function in dot.c, part of a shared library we ship to customers whose servers range from 2010-era Westmere boxes (SSE4.2, no AVX) up to Sapphire Rapids; n is usually a few thousand. Write your optimized version to dot_fast.c with the same signature. Our build compiles every file with the same flags (`cc -O2 -fPIC`), so dot_fast.c has to build that way.

Input files: dot.c
