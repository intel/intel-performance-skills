#!/bin/bash
# Oracle: linux-perf Flow E report (references/flow-e.md template) built from the
# recorded files: perf stat summary, top functions with the 5% cutoff, per-function
# annotated source with the within-function % column, pattern-scan observations.
set -euo pipefail
cat > /app/hotspot_report.md <<'MD'
# Hotspot report for `solver`

Hotspot analysis was performed using the command: `./solver --input case1 --threads 4`
(recorded on the production host; see `/app/profiles/CAPTURE.txt`).

## System-level summary

| Metric              | Value   |
|---------------------|---------|
| IPC                 | 0.61    |
| Cache-miss rate     | 1.07%   |
| Branch-miss rate    | 1.29%   |
| Kernel time         | 16.1%   |
| CPU time            | 10.02 s |

*Low IPC with few cache misses, few branch misses and modest kernel time: the
workload is CPU-bound and stalled on dependencies, not memory-bound.*

---

## Top functions

| Rank | Function                       | Location          | %   |
|------|--------------------------------|-------------------|-----|
|    1 | `accumulate_scores`            | `score.c:12`      | 58% |
|    2 | `worker_update_stats`          | `worker.c:32`     | 19% |
|    3 | `__memmove_avx_unaligned_erms` | `<libc.so.6>`     | 7%  |

---

### Function 1: `accumulate_scores` detailed report

```c

      double accumulate_scores(const double *a, const double *b, size_t n)
      {
          double sum = 0.0;
  24%     for (size_t i = 0; i < n; ++i)
  76%         sum += a[i] * b[i];

          return sum;
      }

```

**Observations:**
- Scalar FP, no vectorization: the hot loop is `movsd` / `mulsd` / `addsd` only; no
  packed FP instruction appears in the function.
- Serial accumulator: `addsd %xmm1,%xmm0` alone takes 59.79% of the function and
  accumulates into `xmm0` every iteration (loop-carried dependency on `sum`); IPC 0.61
  with a 1.07% cache-miss rate.

---

### Function 2: `worker_update_stats` detailed report

```c

      void worker_update_stats(struct worker_stats *stats, int id, uint64_t now)
      {
  91%     stats[id].processed++;
   8%     stats[id].last_batch_ns = now;
   1% }

```

**Observations:**
- Cache-line contention (perf c2c): line `0x55e4b9c2f080` carries 84.37% of all HITM;
  `worker_update_stats+0xa` (worker.c:34) writes it at offsets 0x0, 0x10, 0x20 and 0x30,
  each from a different CPU. Different offsets on one line: false sharing between the
  per-worker `g_stats[]` entries (16 bytes each, four per cache line).

---

### Function 3: `__memmove_avx_unaligned_erms` detailed report

> Binary has no debug symbols (libc) -- assembly view shown.

```asm
      ...
    2%  :   a8f20:   mov    %rdx,%rcx                        ; byte count
   85%  :   a8f23:   rep movsb %ds:(%rsi),%es:(%rdi)         ; bulk copy
    1%  :   a8f25:   vzeroupper
      ...
    4%  :   a8e40:   vmovdqu (%rsi),%ymm0                    ; load head
    2%  :   a8e44:   vmovdqu -0x20(%rsi,%rdx,1),%ymm1        ; load tail
    3%  :   a8e4a:   vmovdqu %ymm0,(%rdi)                    ; store head
    2%  :   a8e4e:   vmovdqu %ymm1,-0x20(%rdi,%rdx,1)        ; store tail
      ...
```

---
MD
cat /app/hotspot_report.md
