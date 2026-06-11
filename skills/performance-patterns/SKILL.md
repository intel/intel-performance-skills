---
name: performance-patterns
description: >-
  Detect and fix x86/C/C++ performance patterns from source code or profiling
  output (perf, VTune, flamegraphs). Invoke when the user asks to optimize,
  review for performance, or write new SIMD/vectorized code — even without
  profiling data. Trigger on: serial accumulator loops, narrow SIMD (xmm/ymm
  that could be ymm/zmm), _mm* intrinsics, HITM/cmpxchg clusters, false
  sharing, missing restrict or vzeroupper, futex_wake/notify_all thundering
  herd, hot symbol inside a system library (.so) with a version gap, or any
  request to write a fast reduction, dot product, or CPU-dispatched function.
  Patterns: serial accumulator, TTAS spinlock, SIMD upconversion (zipper),
  false sharing, per-CPU stats, missing vzeroupper, missing restrict,
  cv-thundering-herd, mutex-to-rwlock, CPU dispatch, library version upgrade,
  fast CRC32C, known algorithms (Cosine Similarity, Hamming Distance,
  Jaccard Distance), SIMD sort (x86-simd-sort).
---

<!-- (C) 2026 Intel Corporation, MIT license -->

# Performance patterns skill

A growing catalog of well-known code patterns that cause performance problems,
with detection signals and resolution playbooks for each.

---

## How to use this skill

### Step 1 — Load the right file for your context

| Context | Read this file |
|---------|---------------|
| You have **profiling output** (perf annotate, perf c2c, perf stat, VTune, flamegraph, etc.) | `triggers/from-profile.md` |
| You are **reading existing source code** and have no profiling data yet | `triggers/from-source.md` |
| You are **writing new** performance-sensitive C/C++ or SIMD code | `guidelines/new-code.md` |

The trigger files cover all the same patterns; they are separated so you only
load what is relevant. `guidelines/new-code.md` is a write-time checklist —
load it instead of a trigger file when generating new code, not reviewing it.

### Step 2 — Identify the matching pattern

Each trigger file contains a compact table and brief descriptions — enough to
decide whether the code or profile matches a known pattern.

### Step 3 — Read the pattern detail file

When a pattern matches, read the corresponding file from `patterns/`. Do not
attempt the fix from memory.

### Step 4 — Apply the fix and verify

Follow the step-by-step instructions and verification method in the pattern file.

Multiple patterns can co-apply. Check all plausible matches before picking one.

---

## Reusable library modules

These standalone implementation guides are available to any agent working in this
skill, not only when following a specific pattern. Load the relevant file directly
if the capability is needed.

| Module | What it provides |
|--------|-----------------|
| `library/cpu-dispatch.md` | Runtime CPU feature detection and variant selection: `target_clones` (compiler-driven, plain C/C++) and `__builtin_cpu_supports` (hand-written variants). Use whenever a function has multiple performance-level implementations that need to be wired together at runtime. |
| `patterns/simd-upconversion-impl.md` | Full step-by-step zipper algorithm for doubling vector register width in asm/intrinsics (SSE→AVX2 or AVX2→AVX-512); AVX-512 accumulator template; post-transformation checklist (CPUID guards, vzeroupper, clobber list). |
| `patterns/fast-crc32c-impl.md` | Drop-in CRC32C library: AVX-512 VPCLMULQDQ fusion (corsix v3s1_s3, 64–97 GB/s), SSE4.2+PCLMULQDQ 3-accumulator (~15–25 GB/s), plain C fallback. Runtime CPU dispatch wrapper included. Use whenever new CRC32C code is needed or an existing implementation is the bottleneck. |
