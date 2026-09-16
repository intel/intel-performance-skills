<!-- (C) 2026 Intel Corporation, MIT license -->
# Pattern: pointer-chase chain walk → memory-level parallelism (K-way interleaved)

## When to apply

Apply when a hot loop walks a *dependent-load chain* — collision chain,
linked list, tree probe, skip list — one node at a time, and the caller
has many *independent* chain-starts available to process at once. A
single chain walk is bounded by cache/DRAM load-to-use latency: one
outstanding miss at a time, each `p = p->next` waiting on the previous
line to arrive. Interleaving K chains keeps K dependent loads in flight
simultaneously and multiplies useful memory bandwidth without changing
the total work.

### Source code signals

- Loop of the form `while (p != nullptr) { ...; p = p->next; }` or
  equivalent — walking a collision chain, linked list, or tree
- The consumed value is loaded from `*p` (e.g. `p->key`, `p->next`) —
  the load result is *needed* to compute the next iteration's address,
  so the next issue must wait for the previous cache line
- The caller holds a batch of independent chain heads (row ids, keys,
  probe values) that are all traversed by the same loop today, one
  after the other
- Working set of walked nodes is large enough that a random step misses
  L2 or L3 — the wider the working set, the more this pattern wins
- Existing code may already emit a single `__builtin_prefetch(next)`
  inside the walk; that overlaps at most two loads and is not a
  substitute for K-way interleaving

### Profiling signals

- One dependent-load instruction (typically `mov <off>(%reg), %reg2`
  where `%reg2` is used to compute the next address) dominates
  `perf annotate` cycle share on the walk symbol
- Last-level cache miss rate per retired load is high on the walk
  symbol, and the "back-end bound by memory latency" bucket of a
  Top-Down profile dominates the walk region (exact event names differ
  by microarchitecture — consult the target CPU's PMU reference)
- IPC of the walk loop is ≪ 1 despite the loop body being short and
  having no branch mispredicts — the CPU is stalled on dependent
  loads, not under-fed with work
- `perf c2c` shows no HITM on the line (single-writer latency, not
  contention) — distinguishes from `false-sharing` / `ttas` patterns

The reference microbenchmark in `patterns/tests/mlp-chain-walk-bench.c`
reproduces all these signals in isolation; run it on the target CPU to
see the flat-DRAM-latency ns/step number that a single-chain walk hits
on that hardware, and confirm the profile looks like the one in
`patterns/tests/mlp-chain-walk-results.md`.

---

## Why this is slow

A chain walk is the textbook *pointer-chase* pattern. Each iteration
issues a load whose address comes from the previous iteration's load
result, so the CPU cannot overlap iterations even with a wide
out-of-order window — every next issue is blocked on a dependent load.
Once the working set exceeds L3, each step is a real DRAM miss and the
loop runs at flat DRAM load-to-use latency, which is typically
~80–120 ns on current server CPUs. The hardware prefetcher cannot help:
the next address is not derivable from the current stride, only from
the value returned by the current load.

The relevant hardware capability is *memory-level parallelism* (MLP):
a modern core can hold several outstanding L1D misses at once (the
line-fill / miss-status buffers). If the workload issues one dependent
load at a time, only one such slot is used, and the achieved throughput
is `1 / DRAM-latency`. If the workload issues K *independent* dependent
loads, up to K slots are used, and throughput becomes `K / DRAM-latency`
— a K× speedup, capped once K reaches the core's outstanding-miss
capacity.

The fix is to change the loop's dependency structure, not to add a
hint. Interleave K independent chain walks so at any moment there are
K dependent loads in flight. Because the K walks belong to K different
inputs (rows, keys, chain heads), they are truly independent — the CPU
can freely reorder their loads.

Example serial chain walk:

```c
while (input_index < input_count) {
    Node *cur = table[input[input_index]];   /* head load */
    while (cur != NULL) {
        consume(cur);
        cur = cur->next;                     /* dependent load */
    }
    ++input_index;
}
```

Every `cur->next` load's address is the value returned by the previous
`cur->next` load. Across thousands of chain-starts, only one dependent
load is ever in flight.

---

## The fix: K-way interleaved chain walk

Process K chains in parallel. The core idea is minimal — hold K live
cursors and advance each one per outer iteration:

```c
/* K should match the outstanding-miss capacity of the target core;
 * 8 is a safe default across current mainstream server cores.
 * Verify on the target with patterns/tests/mlp-chain-walk-bench.c. */
#define K 8

Node *cur[K];
int   active = 0;

/* Refill: load K independent chain heads. */
while (active < K && input_index < input_count) {
    cur[active++] = table[input[input_index++]];
}

while (active > 0) {
    /* Advance every live cursor once. All K loads issue independently
     * because cur[0]..cur[K-1] point into K different chains, so their
     * next-pointers live in K different cache lines. */
    for (int i = 0; i < active; ++i) {
        Node *c = cur[i];
        if (c == NULL) continue;
        consume(c);
        cur[i] = c->next;
    }

    /* Compact retired cursors (cur[i] == NULL) out of the array so the
     * inner loop stays tight; refill from remaining input. */
    int w = 0;
    for (int i = 0; i < active; ++i) {
        if (cur[i] != NULL) cur[w++] = cur[i];
    }
    active = w;
    while (active < K && input_index < input_count) {
        cur[active++] = table[input[input_index++]];
    }
}
```

The inner "advance every cursor" loop is what makes MLP happen. The K
`c->next` loads it issues per outer iteration go out on K different
cache lines simultaneously; the core holds all K in the load buffers,
and each individual load's latency is hidden behind the others. That
is the whole trick — everything else in the sketch is bookkeeping.

### Choosing K

- K should match the outstanding-miss capacity of the target core.
  Small K under-uses the memory pipeline; large K stops helping once
  the capacity cap is reached and only adds bookkeeping overhead.
- **8 is a safe default** across current mainstream server cores.
  Run `patterns/tests/mlp-chain-walk-bench.c` on the target and read
  off the sweep: pick the smallest K at which the interleaved ns/step
  curve flattens. On typical current hardware the plateau sits between
  8 and 16, and moving from 1 to 8 delivers a 4×–8× reduction in ns/step
  on the walk region.

### Ordering caveat

The sketch above emits hits in the order K live cursors happen to be
advanced, which is *not* input order. If the caller must see results
in input order, buffer per-cursor emissions into a small stack array
and drain only when that cursor becomes the "head" (the earliest
input still being walked). Bound each buffer to a fixed size so the
iterator state stays `O(K × buffer_bound)`; a bound equal to K is a
reasonable default.

### When NOT to apply

- **Chains fit in L1** — no memory stall to hide, the interleave
  overhead becomes pure cost. The bench with a small `mib_per_chain`
  shows this: the sweep goes flat.
- **Chain length is always 1** — every input has at most one hit, so
  the setup cost is not amortized. Gate the K-way path so it only
  runs when chain length can exceed 1; fall back to the trivial
  single-hit loop otherwise.
- **The caller cannot provide K independent chain-starts at once** —
  some streaming APIs expose one input at a time and there is no
  batch to interleave over; consider batching upstream first.
- **Ordering of hits *within* a single chain must be preserved and
  cannot be reordered relative to other chains' hits** — the
  cross-chain interleave inherently reorders emissions. Buffered flush
  (above) preserves cross-input order; nothing preserves cross-chain
  interleaving of intra-chain order.

---

## Verification

After applying the fix:

1. **Correctness** — run on inputs that exercise chain lengths of 1, a
   handful, and long, and confirm the output matches the serial version
   under whatever ordering contract the caller requires.
2. **Microbenchmark sweep** — run `patterns/tests/mlp-chain-walk-bench.c`
   on the target. Expect: serial ns/step lands at flat DRAM latency
   and stays roughly constant across rows; interleaved ns/step drops
   close to serial/K at small K then plateaus. Overall gain on the
   walk region typically falls in the **4×–8×** range on cores whose
   outstanding-miss capacity is at least 8.
3. **PMU** — rerun `perf stat`; IPC on the walk symbol should rise
   substantially (a ~10× jump is not unusual on a pure pointer-chase
   loop), and the "back-end bound by memory latency" share of cycles
   should drop. Event names differ by microarchitecture — consult the
   target's PMU reference for the pending-miss / memory-stall counters
   that apply. The reference bench uses only the portable
   `cycles / instructions / cache-references / cache-misses` events
   so the same wrapper script works everywhere.
4. **End-to-end** — the operator's wall-clock time should drop by
   roughly the fraction of its cycles that were the chain walk. If the
   walk was N% of total cycles and MLP reduced its ns/step by factor F,
   expect an operator-level reduction of roughly `N × (1 − 1/F)`.

---

## Reference microbenchmark

`patterns/tests/mlp-chain-walk-bench.c` is a standalone,
single-translation-unit C program that builds K independent
random-shuffled cyclic chains sized past L3, then times a serial
single-chain walk against a K-way interleaved walk of K independent
chains for W ∈ {1, 2, 4, 8, 16}. Both walks touch the same total
number of nodes so the comparison is like-for-like.

- `patterns/tests/mlp-chain-walk-bench.c` — the bench
- `patterns/tests/run-mlp-chain-walk-bench.sh` — build + comparative
  sweep + `perf stat` on the serial and interleaved modes in isolation
  + `perf record` / `perf report --stdio` for both modes
- `patterns/tests/mlp-chain-walk-results.md` — a sample output table
  and how to read it, including the `perf annotate` shape a serial
  chain-walk symbol takes (one dependent-load line at ~99% of the
  function) and how that share drops after interleaving

Modes for perf isolation (used by the run script):

    ./mlp-chain-walk-bench 256 50 serial        # W=1 only, no table
    ./mlp-chain-walk-bench 256 50 interleaved   # W=8 only, no table
    ./mlp-chain-walk-bench 256 50 ns-per-step   # print raw ns/step for scripting

---

## Background

The K-way interleaved chain walk with buffered flush is the *group
prefetch / software-pipelined lookup* technique from Chen, Ailamaki,
Gibbons, Mowry, *Improving Hash Join Performance through Prefetching*
(ICDE 2004). The version presented here has the same shape without the
prefetch hints — modern out-of-order cores discover the K parallel
dependent loads without an explicit hint per iteration.

---

## Presenting this to the user

1. Show `perf annotate` on the walk symbol with the dependent-load
   line highlighted (typically `mov (%r*),%r*`) and its cycle share.
   If it dominates the function, the walk is single-issue-outstanding.
2. State: "This is a pointer-chase. Each `p->next` load depends on the
   previous one, so the CPU issues one DRAM miss at a time even though
   the core can hold several outstanding misses. Interleaving K
   independent chains puts K dependent loads in flight simultaneously
   and turns a latency-bound loop into an MLP-bound one."
3. Confirm the caller has a batch of independent inputs to interleave
   over. If not, batching upstream is the enabling prerequisite.
4. If chain length can be 1 (single-hit case), gate the K-way path
   so it does not run in that case — a K-way loop that regresses on
   trivial inputs is worse than doing nothing.
5. Recommend K = 8 as a starting point and point at
   `patterns/tests/mlp-chain-walk-bench.c` for the per-target sweep.
6. Expected win: **4×–8× on the walk region** on cores whose
   outstanding-miss capacity is at least 8, translating end-to-end to
   whatever fraction of operator cycles that region held.
