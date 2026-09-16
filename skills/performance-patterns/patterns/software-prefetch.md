<!-- (C) 2026 Intel Corporation, MIT license -->
# Pattern: latency-exposed stride-predictable scan → software prefetch

## When to apply

Apply when a hot loop is a **stride-predictable scan** (sequential or
almost-sequential walk over a fixed-stride record layout) that still
leaves per-record load latency exposed at runtime. This is common
inside **batched producer-consumer scans**: the loop fills a small
batch of pointers / values, hands the batch to a consumer that runs a
while, then the scan resumes. Each pause bounds how far the hardware
prefetcher can run ahead within the batch; combined with small
per-element work on the scan side, the first load of each record can
end up on the critical path even though the stride is trivial. A
one-line `__builtin_prefetch(&data[row + N])` at a tuned lookahead
distance issues that next-record fetch early enough that the fill
completes before the demand load arrives.

Whether the pattern actually costs cycles on a given target CPU
depends on the hardware's coverage of the specific loop shape. Use
the **profile signals** below to confirm the stall is present on the
target *before* adding the hint, and the **microbenchmark** to confirm
the hint recovers cycles *after*. Do not commit the hint without
measurement.

### Source code signals

- Loop of the form `for (row = 0; row + rowSize <= limit; row += rowSize)
  { ...; rows[count++] = data + row; if (count == kBatch) hand_off(); }`
- Working set is far larger than the last-level cache (rows array
  spans hundreds of MiB to GiB, is walked once, is not resident)
- Per-element work inside the scan loop is small: a flag byte check,
  an offset add, a write to a small output batch — few dozen
  instructions at most
- The scan is *paused* periodically by a batched hand-off to a
  consumer (aggregation lookup, hash probe, filter callback). Each
  resume gives the prefetcher a fresh run-in window during which its
  training has not caught up with the stream
- Existing code contains no `__builtin_prefetch` on the row stream

### Profiling signals

- `perf annotate` on the scan symbol concentrates a large share of
  samples on the *first load of each record* — typically a `movsbl` /
  `movzbl` reading a per-row flag byte at row start, or the `mov`
  that loads a header field
- The "back-end bound by memory latency" bucket of a Top-Down profile
  dominates the scan region (exact event names differ per
  microarchitecture — consult the target CPU's PMU reference)
- Last-level cache miss rate per retired load is elevated on the scan
  symbol; L1D hit rate is *not* dominant even though the loop is a
  pure sequential walk
- `perf c2c` shows *no* HITM on the line: this is single-writer DRAM
  latency, not cache-line contention (distinguishes from false-sharing
  patterns)

If these signals are absent on the target — the scan region is a
small share of total cycles, no single early load carries most of the
scan's samples — the hardware prefetcher is already covering the
stream in this loop shape and this pattern does not apply. Do not
add the hint.

---

## Why this is slow

Hardware stream / stride prefetchers train on observed access patterns
and issue prefetches some distance ahead of the current demand stream.
Coverage depends on the prefetcher's design (queue depth, confidence
heuristics, per-page reach) and on what the demand stream looks like.
Two properties of a batched producer-consumer scan can combine to
leave load latency exposed:

1. **Bounded run-ahead per batch.** The scan runs, fills a small
   batch, hands off to a consumer that may issue its own memory
   traffic (hash-table probes, aggregation-bucket updates). While the
   scan is paused the prefetcher can only stay so far ahead before
   its queue or confidence window caps. When the scan resumes it
   re-trains and re-leads the stream, and the first record after
   each resume tends to miss.
2. **Small per-element work.** When the loop body is a handful of
   scalar ops per record, demand issues cache lines fast enough that
   the prefetcher's steady-state lead over demand can shrink. Under
   rising back-end stall pressure a prefetcher may throttle back to
   avoid demand-fetch collisions — whether and how much depends on
   the microarchitecture.

The fix does not add memory-level parallelism — the stream already
supports it in principle. The fix restores *run-ahead distance*: an
explicit `__builtin_prefetch(&data[row + N])` on every iteration
issues a fetch of a line the loop will need `Nₚ = N / rowSize`
iterations later, and the L1D fill completes by the time that
iteration arrives.

Example latency-exposed scan (batched producer, simplified):

```c
while (row + row_bytes <= arena_bytes) {
    int count = 0;
    while (count < BATCH_ROWS && row + row_bytes <= arena_bytes) {
        /* The first load of each record: this is the instruction that
         * dominates `perf annotate` on targets where the hardware
         * prefetcher does not cover this loop shape. */
        if (data[row] & 1) { row += row_bytes; continue; }
        batch[count++] = data + row;
        row += row_bytes;
    }
    hand_off(batch, count);
}
```

The `data[row]` load reads a header byte at the start of every
record. The stride is fixed (`row_bytes`) and the address is trivially
predictable — yet when the profile shows this load dominating the
scan's cycles, the hardware prefetcher is not fully covering this
loop shape on the target, and the demand load stalls on DRAM latency.

---

## The fix: `__builtin_prefetch` at a tuned lookahead distance

Insert one prefetch per iteration for the record the loop will reach
in ~N bytes:

```c
while (row + row_bytes <= arena_bytes) {
    int count = 0;
    while (count < BATCH_ROWS && row + row_bytes <= arena_bytes) {
#if defined(__x86_64__)
        /* Restore memory run-ahead in this batched producer-consumer
         * scan. Integer arithmetic (not pointer arithmetic) so an
         * out-of-bounds target is well-defined; __builtin_prefetch is
         * non-faulting on x86_64. */
        __builtin_prefetch((const char *)((uintptr_t)data + row + 2048));
#endif
        if (data[row] & 1) { row += row_bytes; continue; }
        batch[count++] = data + row;
        row += row_bytes;
    }
    hand_off(batch, count);
}
```

Three details matter and each is load-bearing:

- **Distance N in bytes, not elements.** Record layouts often have
  variable per-row widths. Reasoning in bytes covers all row widths
  uniformly with one constant. 2 KiB (32 cache lines) is a reasonable
  starting point for typical row widths; sweep it (see below).
- **Integer arithmetic for the target address, not `data + row + N`.**
  A `char*` past the end of a valid allocation is undefined behavior
  in C/C++, even without dereference. Compute the target via
  `uintptr_t` so the arithmetic is well-defined; `__builtin_prefetch`
  is non-faulting on `x86_64` for any canonical address, so the target
  landing past the mapping is safe at runtime.
- **`#if defined(__x86_64__)` guard.** `__builtin_prefetch` expands
  to a `PREFETCHT0`-family instruction on `x86_64`; on other ISAs it
  may or may not lower to a useful hint. Confining the hint to the
  ISA where it was measured to help keeps the code path unchanged
  elsewhere.

### Sweep methodology

Adding the hint is easy; picking `N` well is the interesting part.
Sweep it with a per-caller microbenchmark:

1. **Does the pattern apply on this CPU?** The reference bench in
   `patterns/tests/software-prefetch-bench.c` sweeps prefetch distances
   {0, 1 KiB, 2 KiB, 4 KiB, 8 KiB} over a batched producer-consumer
   scan. If every distance is within ~2 % of the N=0 baseline, the
   hardware prefetcher already covers this loop shape on the target
   and the pattern does not apply here. Do not add the hint.
2. **What N is best?** If the sweep is *shaped* — a clear valley in
   the middle — pick the N at the flat maximum. For a per-caller
   sweep of the actual production loop, rebuild the binary at each N
   (a compile-time `-DPREFETCH_DISTANCE=$d` is the simplest way) and
   median across ≥3 runs; per-distance process isolation prevents
   in-process cache warmup from biasing the sweep. Typical gain on
   the scan region falls in the **10–30 %** range on targets where
   the pattern applies.

### Prefetch hint variants

`__builtin_prefetch(addr)` defaults to `_MM_HINT_T0` (temporal, all
cache levels). This is right for read-once streams whose consumer will
re-touch the same line moments later — the batched producer-consumer
pattern here. Do not switch to `T1` / `T2` / `NTA` without evidence:

- `__builtin_prefetch(addr, 0, 1)` → `T1` (L2+L3): use if the row
  stream is much larger than L1 and the consumer will not re-touch it
  soon; empirically rarely wins over `T0` in real callers
- `__builtin_prefetch(addr, 0, 0)` → `NTA` (streaming, bypass L2):
  reserved for pure sequential passes that will not be re-read;
  measure before committing

### When NOT to apply

- **Cache-resident scans.** If the working set fits in the L3 the
  scan is bound to, the sweep is flat and real callers show no
  measurable win either. No reason to add code.
- **Random / hashed / pointer-chase access.** The next address is
  not derivable from the current index — `p = p->next`,
  `table[hash(key)]`, tree probes. A stride prefetch cannot compute
  the next address; the fix for a loaded-pointer dependency is a
  change of loop-carried dependency structure, not a hint.
- **The target CPU's hardware prefetcher already covers this loop
  shape.** Verified by the sweep being flat — if the hint recovers no
  cycles, it costs a retired instruction per iteration for no gain.
  Keep the `#if defined(__x86_64__)` guard as the minimum coverage
  boundary; a per-microarchitecture runtime check is possible but
  usually not worth the complexity.
- **The consumer is the bottleneck.** If the batched hand-off is far
  more expensive than the scan step, hiding scan latency does not
  move the total. Verify via `perf record` + `perf report` that the
  scan symbol is a meaningful cycle share before adding the hint.

---

## Verification

After applying the fix:

1. **Correctness** — output must be byte-identical to the unprefetched
   run. `__builtin_prefetch` is a hint with no architectural side
   effects; any diff indicates a bug elsewhere in the change.
2. **Profiling** — rerun `perf record` + `perf annotate` on the
   target CPU where the fix was tuned. On targets where the header-
   byte load was dominant, its sample share should drop noticeably;
   the scan region's cycle share of the whole workload should shrink
   by roughly the fraction of that load's cycles that were exposed.
3. **Microbenchmark** — rerun `patterns/tests/software-prefetch-bench.c`
   and confirm the chosen distance sits at or near the flat maximum
   of the sweep. If the sweep goes flat, the pattern no longer applies
   on this target — remove or re-gate the hint.
4. **End-to-end** — the scan operator's wall-clock should drop by
   roughly the same fraction the profile step measured. Expect gains
   in the **single-digit to low-double-digit percent** range on the
   scan region, translating end-to-end to whatever fraction of the
   workload the scan region held.
5. **Regression check** — rerun the sweep on the other target CPU
   families the binary ships to. Under the `#if defined(__x86_64__)`
   guard non-`x86_64` targets see no change; for `x86_64` targets
   where the sweep is flat the win should be at most zero — if a
   regression shows up, raise the guard or gate more narrowly.

---

## Reference microbenchmark

`patterns/tests/software-prefetch-bench.c` is a standalone,
single-translation-unit C program that reproduces the batched
producer-consumer scan pattern in isolation: a fixed-stride record
arena larger than L3, scanned in batches of 1024 rows, each batch
handed off to a consumer that issues one pseudo-random 8-byte probe
per row into an 8 MiB table (large enough to defeat the private
caches, so the consumer really does contend with the scan's
prefetcher training).

- `patterns/tests/software-prefetch-bench.c` — the bench
- `patterns/tests/run-software-prefetch-bench.sh` — build + comparative
  sweep across prefetch distances {0, 1 K, 2 K, 4 K, 8 K} + `perf stat`
  on the isolated noprefetch and prefetch modes + `perf record` /
  `perf report --stdio` for both modes
- `patterns/tests/software-prefetch-results.md` — a sample output
  table and how to read it, including the two possible outcomes
  (sweep shaped: the pattern applies, pick the flat maximum; sweep
  flat: the pattern does not apply on this target)

Modes for perf isolation (used by the run script):

    ./software-prefetch-bench 256 64 2 noprefetch   # N=0 only, no table
    ./software-prefetch-bench 256 64 2 prefetch     # N=2048 only, no table
    ./software-prefetch-bench 256 64 2 ns-per-row   # print raw ns/row (N=2048)

---

## Presenting this to the user

1. Show the `perf annotate` output with the dominant scan load
   highlighted (if there is one) and its sample share. If no single
   scan load dominates on the target, the pattern likely does not
   apply here — say so.
2. State: "This is a stride-predictable scan inside a batched
   producer-consumer loop. The stride is trivially predictable but
   the first load per record can still stall when the hardware
   prefetcher's run-ahead has been reset by the previous batch
   hand-off. A one-line `__builtin_prefetch` at a tuned lookahead
   restores the run-ahead — *if* this target's prefetcher does not
   already cover the loop shape."
3. Recommend running `patterns/tests/software-prefetch-bench.c` on
   the target first. A shaped sweep says apply the fix and points at
   the best N. A flat sweep says do nothing.
4. Recommend 2 KiB (32 cache lines) as the *starting point* for the
   sweep, not as a magic default.
5. Add `#if defined(__x86_64__)` from the start — no benefit shipping
   the hint to ISAs where it has not been measured.
6. Expected win when the pattern applies: **10–30 % on the scan
   region**, translating end-to-end to whatever fraction of the
   workload the scan held; byte-identical output.
