---
tags: [performance-patterns, readme-example, codegen]
max_turns: 40
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

Write a vectorized AVX2 float array scale function with CPU dispatch.

Signature: `void scale_f32(float *dst, const float *src, float k, size_t n)`, computing dst[i] = src[i] * k. Put it in scale.c. It goes into a library that's built with plain `cc -O2 -fPIC` and also has to run on machines without AVX2.
