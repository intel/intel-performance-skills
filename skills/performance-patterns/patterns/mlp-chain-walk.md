<!-- (C) 2026 Intel Corporation, MIT license -->
# Pattern: pointer-chase chain walk → memory-level parallelism (K-way interleaved)

## When to apply

A hot loop walks a *dependent-load chain* — collision chain, linked list,
tree probe, skip list — one node at a time, and the caller has many
*independent* chain-starts available at once.

### Source code signals

- `while (p) { ...; p = p->next; }` or equivalent, where the loaded value
  is the next iteration's address
- The caller traverses a batch of independent chain heads (row ids, keys,
  probe values) one after another
- Walked working set misses L2/L3 — the larger, the bigger the win
- An existing single `__builtin_prefetch(next)` overlaps at most two loads
  and is not a substitute

### Profiling signals

- One dependent load (`mov <off>(%reg), %reg2`, `%reg2` feeding the next
  address) dominates `perf annotate` on the walk symbol
- High LLC miss rate per retired load; memory-latency Top-Down bucket
  dominates (event names differ per microarchitecture)
- IPC ≪ 1 despite a short loop body and no branch mispredicts
- `perf c2c` shows no HITM (not `false-sharing` / `ttas`)

---

## Why this is slow

Each `p->next` address is the previous load's result, so iterations cannot
overlap and the hardware prefetcher cannot predict the next address. Past L3,
every step costs flat DRAM latency (~80–120 ns on current server CPUs) with
one of the core's outstanding-miss slots in use. K *independent* walks use up
to K slots: throughput `K / DRAM-latency`, capped at the core's
outstanding-miss capacity. The fix changes the loop-carried dependency
structure; no hint is needed.

---

## The fix: K-way interleaved chain walk

```c
#define K 8   /* match the core's outstanding-miss capacity; sweep with the bench */

Node *cur[K];
int   active = 0;

while (active < K && input_index < input_count)
    cur[active++] = table[input[input_index++]];

while (active > 0) {
    /* K independent dependent loads in flight: this loop creates the MLP. */
    for (int i = 0; i < active; ++i) {
        Node *c = cur[i];
        if (c == NULL) continue;
        consume(c);
        cur[i] = c->next;
    }
    /* Compact retired cursors, refill from remaining input. */
    int w = 0;
    for (int i = 0; i < active; ++i)
        if (cur[i] != NULL) cur[w++] = cur[i];
    active = w;
    while (active < K && input_index < input_count)
        cur[active++] = table[input[input_index++]];
}
```

**Choosing K:** 8 is a safe default on current mainstream server cores. Run
`patterns/tests/mlp-chain-walk-bench.c` and pick the smallest K where
interleaved ns/step flattens (typically 8–16); 1 → 8 gives 4×–8× on the
walk region.

**Ordering:** hits are emitted in cursor order, not input order. If the
caller needs input order, buffer each cursor's hits in a small fixed-size
array (bound ≈ K) and drain a cursor only when it is the earliest input
still being walked.

### When NOT to apply

- **Chains fit in L1** — no stall to hide; the bench sweep goes flat.
- **Chain length is always 1** — setup is not amortized; gate the K-way
  path and keep the trivial single-hit loop.
- **No batch of independent chain-starts** — batch upstream first.
- **Intra-chain hit order must stay interleaved with other chains'** —
  buffered flush preserves cross-input order only.

---

## Verification

1. **Correctness** — chain lengths 1, a few, and long; output matches the
   serial version under the caller's ordering contract.
2. **Bench** — serial ns/step ≈ flat DRAM latency, constant across rows;
   interleaved ≈ serial/K at small K, then plateaus. 4×–8× on cores with
   outstanding-miss capacity ≥ 8.
3. **PMU** — IPC on the walk symbol rises sharply (~10× is not unusual);
   memory-latency Top-Down share drops.
4. **End-to-end** — walk at N% of cycles, ns/step down by factor F →
   roughly `N × (1 − 1/F)` reduction.

Bench files: `patterns/tests/mlp-chain-walk-bench.c`,
`run-mlp-chain-walk-bench.sh`, `mlp-chain-walk-results.md` (sample output;
serial annotate shows one dependent-load line at ~99%). Isolated modes:
`./mlp-chain-walk-bench 256 50 {serial|interleaved|ns-per-step}`.

Background: group prefetch / software-pipelined lookup (Chen, Ailamaki,
Gibbons, Mowry, ICDE 2004), without the explicit hints.

---

## Presenting this to the user

1. Show `perf annotate` with the dependent-load line and its share.
2. "This is a pointer-chase — one DRAM miss in flight at a time.
   Interleaving K independent chains puts K misses in flight."
3. Confirm a batch of independent inputs exists; gate out the
   chain-length-1 case.
4. Recommend K = 8 and the bench for the per-target sweep; expect
   **4×–8× on the walk region**, scaled end-to-end by its cycle share.
