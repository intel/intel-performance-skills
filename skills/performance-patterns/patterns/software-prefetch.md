<!-- (C) 2026 Intel Corporation, MIT license -->
# Pattern: latency-exposed stride-predictable scan → software prefetch

## When to apply

A hot **stride-predictable scan** over fixed-stride records still exposes
per-record load latency — typically inside a **batched producer-consumer
scan** (fill a small batch, hand it to a consumer, resume). Whether it costs
cycles depends on the target's hardware-prefetcher coverage of the loop
shape: confirm the stall in the profile *before* adding the hint and with the
bench *after*. Never commit the hint unmeasured.

### Source code signals

- `for (row = 0; row + rowSize <= limit; row += rowSize) { ...;
  rows[count++] = data + row; if (count == kBatch) hand_off(); }`
- Working set ≫ LLC (hundreds of MiB to GiB, walked once)
- Small per-element work (flag-byte check, offset add, batch append)
- Periodic batched hand-off to a consumer (aggregation, hash probe, filter)
- No `__builtin_prefetch` on the row stream

### Profiling signals

- `perf annotate` on the scan symbol concentrates samples on the *first
  load of each record* (`movsbl` / `movzbl` of a flag byte, or a header `mov`)
- Memory-latency Top-Down bucket dominates the scan region; elevated LLC
  miss rate despite a sequential walk
- `perf c2c` shows no HITM

If the scan is a small share of cycles or no early load dominates, the
hardware prefetcher already covers it — do not add the hint.

---

## Why this is slow

Each hand-off pauses the scan while the consumer issues its own memory
traffic, bounding the hardware prefetcher's run-ahead; after resume the first
records tend to miss. Small per-element work lets demand catch up with the
prefetcher's lead, and some microarchitectures throttle prefetch under
back-end stall pressure. The fix adds no MLP; it restores run-ahead — a
prefetch `N` bytes ahead (`N / rowSize` iterations) fills before demand
arrives.

---

## The fix: `__builtin_prefetch` at a tuned lookahead distance

```c
while (row + row_bytes <= arena_bytes) {
    int count = 0;
    while (count < BATCH_ROWS && row + row_bytes <= arena_bytes) {
#if defined(__x86_64__)
        __builtin_prefetch((const char *)((uintptr_t)data + row + 2048));
#endif
        if (data[row] & 1) { row += row_bytes; continue; }  /* hot header load */
        batch[count++] = data + row;
        row += row_bytes;
    }
    hand_off(batch, count);
}
```

- **Distance in bytes, not elements** — one constant covers variable row
  widths. 2 KiB (32 lines) is a starting point, not a default.
- **`uintptr_t` arithmetic** — `data + row + N` past the allocation is UB
  even undereferenced; `__builtin_prefetch` is non-faulting on `x86_64`.
- **`#if defined(__x86_64__)`** — ship the hint only where it was measured.
- Keep the default `T0` hint; `T1` (`__builtin_prefetch(a, 0, 1)`) rarely
  wins and `NTA` (`, 0, 0)`) is only for never-re-read streams — measure
  before switching.

### Sweep

`patterns/tests/software-prefetch-bench.c` sweeps N ∈ {0, 1, 2, 4, 8} KiB.
**Flat** (every N within ~2 % of N=0): the pattern does not apply — do not
add the hint. **Shaped** (clear valley): pick N at the flat maximum; typical
scan-region gain 10–30 %. For the production loop, rebuild per N
(`-DPREFETCH_DISTANCE=$d`) and take the median of ≥3 runs.

### When NOT to apply

- **Cache-resident scans** — fits in L3; flat sweep.
- **Random / hashed / pointer-chase access** (`p = p->next`,
  `table[hash(key)]`) — the next address is not derivable from the index.
- **Prefetcher already covers the shape** — flat sweep; the hint only
  costs an instruction per iteration.
- **The consumer is the bottleneck** — check the scan symbol's cycle share
  in `perf report` first.

---

## Verification

1. **Correctness** — byte-identical output.
2. **Profiling** — header-byte load's annotate share drops noticeably.
3. **Bench** — chosen N at or near the flat maximum; flat sweep → remove
   or re-gate the hint.
4. **End-to-end** — single-digit to low-double-digit percent on the scan
   region, scaled by its workload share.
5. **Regression** — re-sweep on every `x86_64` family the binary ships to;
   gate more narrowly if one regresses.

Bench files: `patterns/tests/software-prefetch-bench.c`,
`run-software-prefetch-bench.sh`, `software-prefetch-results.md` (shaped vs
flat sweep). Isolated modes:
`./software-prefetch-bench 256 64 2 {noprefetch|prefetch|ns-per-row}`.

---

## Presenting this to the user

1. Show `perf annotate` with the dominant scan load and its share; if none
   dominates, say the pattern likely does not apply.
2. "The stride is predictable, but each batch hand-off resets the hardware
   prefetcher's run-ahead, so the first load per record stalls. A one-line
   `__builtin_prefetch` restores it — *if* this target's prefetcher does
   not already cover the loop shape."
3. Run the bench first: shaped → apply at the best N; flat → do nothing.
4. Expect **10–30 % on the scan region** when it applies; byte-identical
   output.
