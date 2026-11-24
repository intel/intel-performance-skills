# The bottleneck is one global spinlock, `c->lock`, and the cache line it shares with the LRU head

Every operation goes through `cache_lookup()`, which serializes the whole process. Every lookup takes the one global spinlock, including the 97% that hit. While holding it, the lookup **writes** shared state:
- `c->hits++`
- the LRU move-to-front: `c->lru_head` plus the `lru_prev`/`lru_next` of up to three other entries

The lock word and those header fields sit on the same 64-byte line, so the program advances one cache-line handoff at a time.

Your 1-thread profile alone predicts the plateau. Past it, throughput falls: threads waiting on the lock keep executing `lock cmpxchg`, and each attempt pulls that line away from the thread holding the lock.

## 1. A single lock predicts the plateau

At 1 thread an op takes 204 ns. About 69 ns of that runs under `c->lock`: `cache_lookup` (29.84%) plus `cache_insert` (3.95%). A 33.8% serial fraction caps throughput at **2.96x single-thread, about 14.5 Mops/s**, with the knee near 3 threads.

A closed-queue model (MVA, mean-value analysis) built from just those two numbers:

| threads | measured Mops/s | one-lock model | ns per op (1/throughput) |
|--:|--:|--:|--:|
| 2 | 8.73 | 8.81 | 115 |
| 4 | 12.05 | 13.21 | 83 |
| 8 | 13.10 | 14.52 | 76 |
| 16 | 13.02 | 14.53 | 77 |
| 32 | 12.44 | 14.53 | 80 |
| 64 | 7.86 | 14.53 | 127 |

- **At 8 threads you are at 90% of that ceiling.** More cores cannot help.
- **After that the model stays flat but your box doesn't.** The serialized cost per op grows from 69 ns (1 thread) to 127 ns (64 threads), so each lock handoff gets more expensive as waiters are added.
- **Latency fits one unfair lock.** Mean latency grows linearly with threads, and p99 sits at 7–9x the mean, which is what one unfair test-and-set lock produces.

## 2. Where the cycles go at 64 threads

- **perf:** `cache_lookup` grows from 29.8% to 72.1%, and `cache_insert` from 4.0% to 8.9%. Every other function shrinks by the same factor (about 0.28), so their relative shares are unchanged and all the growth is in the two lock-holding functions. The allocator is about 2% and the kernel about 0.3%.
- **annotate:** 68.9% of `cache_lookup` is on `lock cmpxchg` and 11.9% on `pause`. **About 59% of all cycles at 64 threads are spent waiting for `c->lock` in lookups alone.**
- **c2c:** one line, `0x7f2c4a000080`, carries **78.4% of all HITMs** (loads that hit a line modified in another core's cache). It is the `struct cache` header; I checked the offsets against the x86-64 layout with compile-time asserts:

| offset | field | share of this line's HITMs | touched by |
|---|---|--:|---|
| 0x0 | `lock` | 79.0% (lookup CAS 66.9, insert CAS 7.9, unlock 4.2) | every thread, spinning |
| 0x8 | `hits` | 9.6% | lock holder only |
| 0x18 | `lru_head` | 8.8% | lock holder only |
| 0x20, 0x10, 0x4 | `lru_tail`, `misses`, `nentries` | 2.6% | lock holder only |

The 21% on fields that are only touched while *holding* the lock explains the decline:
- **Those accesses should be L1 hits.** After a successful CAS (compare-and-swap) the holder owns the line.
- **They miss instead.** Each costs 300–430 cycles per the c2c cycle columns, because a spinner took the line in the middle of the critical section.
- **Spinners take ownership even when they fail.** A failed `lock cmpxchg` still takes the line exclusive (the SDM specifies a write cycle either way).
- **So 63 spinners keep demanding the line the holder needs** for `hits++`, `lru_head = e` and the unlock store. 66% of the stores to that line miss L1.

More waiters means longer critical sections, which means lower throughput.

c2c lines 1–5 (8.7% of HITMs) are individual hot entries, hit at +0x10/+0x18 (`lru_prev`/`lru_next`), the move-to-front pointer writes. This is the structural half of the problem: under strict LRU a *hit* is a write to shared memory, so no lock implementation can make lookups run in parallel.

## 3. Your c2c contradicts "pinned to physical cores, socket 0 first"

**What the c2c run shows:**
- All 64 CPUs that touched the hot line are on NUMA node 0: `cpu cnt` is 64 and the trailing "Shared Node" column is `0`.
- RmtHitm (remote-socket HITMs) is **0 on every line**.

If threads 33–64 had been on socket 1, that line would be crossing UPI (the socket-to-socket link) and remote HITMs would dominate. So in that run the 64 threads were almost certainly on socket 0's 32 cores × 2 hyperthreads, not 32 + 32 across sockets.

That changes the explanation for the 64-thread cliff, not the root cause. Going from 32 to 64 threads most likely added a second hyperthread to every core. That doubles the spinners, and each lock holder now shares a core with a spinning sibling. It is not cross-socket traffic. If you meant 64 physical cores, the pinning isn't doing that. Check with:

```sh
lscpu -e=CPU,NODE,SOCKET,CORE                 # which CPU ids are siblings, which socket
ps -L -o tid,psr,comm -p "$(pidof kvbench)"   # during a 64-thread run
perf c2c report -NN --stdio                   # per-node CPU lists for each hot line
```

Two smaller oddities, neither of which changes the conclusion:
- **Low sample count.** The annotate's 1,418,230 samples at 72.06% is only about 25 CPUs' worth at perf's default 4 kHz × 20 s. Either perf throttled its sampling (`dmesg | grep 'took too long'`) or the threads weren't all on CPU. `perf stat -e task-clock,context-switches -- ./kvbench --threads 64 --duration 20` rules out descheduling, which matters because a preempted spinlock holder makes the cliff deeper.
- **Odd per-instruction attribution.** A few c2c rows put load HITMs on pure store instructions, such as the unlock at `+0x81`. PEBS load sampling shouldn't produce that, so treat the per-instruction split as approximate. The per-offset table above doesn't depend on it.

## 4. The fix: remove the single serialization point

These won't help:
- **rwlock:** every lookup writes.
- **pthread mutex:** same serial section, plus futex wakeup latency.
- **atomic counters or a lock-free table:** `hits++` and `lru_head = e` are still writes to a single line.
- **a better lock alone:** measured in section 5, TTAS doesn't move the ceiling.

**`lru_cache_sharded.c`** is a new file; `lru_cache.c` is untouched.
- **256 independent LRU caches (lock striping).** A key's shard comes from the top bits of a multiplicative hash; the bucket still comes from the key's low bits.
  - Each shard has its own lock, LRU list, counters and slice of buckets.
  - Each shard starts on its own 128-byte block, because SPR's L2 prefetcher pulls 64-byte lines in pairs.
  - The lookup and insert logic is otherwise the original's, line for line.
- **TTAS spin** (test-and-test-and-set): waiters read the lock until it looks free, then CAS. On x86 this keeps waiters on a shared copy instead of issuing failing ownership requests (RFOs) on a hot shard. On arm64 it made no measurable difference once sharded.
- **API:** `cache_lookup` and `cache_insert` keep their signatures. It adds `cache_create(capacity)`, since your file has no constructor, and `cache_stats(c, &hits, &misses)` to replace reading `c->hits` directly.
- **Trade-offs:**
  - LRU order is kept per shard, so it is approximate across the whole cache.
  - Capacity is split evenly across shards, which costs hit rate when shards are small and key popularity is flat. Measured at 64K capacity with uniform keys: 97.0% becomes 96.0% with 256 shards, or 96.7% with 64 shards.
  - There's no loss with Zipf-skewed keys, and the loss is negligible with larger shards.

## 5. Validation on my machine (18-core Apple M5 Max, not your box)

`repro/kvbench_local.c` is a stand-in for kvbench that compiles your `lru_cache.c` **unmodified**. On arm64, a 3-line `immintrin.h` shim maps `_mm_pause` to `isb`. I calibrated the per-op work so the original peaks at about 3x single-thread, like yours.

Zipf θ=0.99 keys, 97% hits, Mops/s, median of 3 runs. Uniform keys give the same picture; see `repro/results/summary.txt`.

| threads | original | lock only (1 shard, TTAS) | **fix (256 shards, TTAS)** |
|--:|--:|--:|--:|
| 1 | 2.41 | 2.34 | 2.30 |
| 4 | **7.30** (peak) | 7.01 | 8.87 |
| 6 | 6.15 | 5.23 | 12.32 |
| 8 | 2.78 | 3.08 | 15.21 |
| 16 | 1.55 | 2.11 | 26.72 |
| 18 | 1.49 | 2.11 | **28.17** |
| p99 at 18 threads | 80 µs | 88 µs | **2.0 µs** |

- **The original reproduces your shape.** It plateaus at about 3x single-thread, then collapses once threads spill out of the first 6-core L2 cluster (6 to 8 threads), the same kind of topology step as your 32 to 64. Its peak stays at about 7.5 Mops/s whether per-op work is 200, 300 or 400 units, so the ceiling belongs to the lock, not the workload.
- **Changing only the lock algorithm doesn't lift the ceiling.** TTAS only reshapes the collapse.
- **Sharding scales:** 14–19x the original at 16–18 threads, with p99 flat at about 2 µs. TAS and TTAS were indistinguishable once sharded, in interleaved A/B runs.
- **Sanity check:** a 1-shard TAS build of the new file matches the original within 4% at every thread count, so the refactor and the harness don't change anything on their own.
- **Correctness:**
  - `repro/test_lru.c` passes against your original and against all 6 sharded builds (1, 64 or 256 shards, each with TAS and TTAS).
  - It is clean under TSan and ASan/UBSan.
  - It catches 5 deliberately broken variants: no lock, the wrong shard's lock, no promotion, evicting the head, and a dropped capacity remainder.
  - Across 288 benchmark runs, no hit returned the wrong value. Your original passes too: it is correct, just serialized.

Three things don't transfer to your box: the absolute numbers, the size of any TAS/TTAS difference (arm64 LSE atomics vs x86 `lock cmpxchg`), and where the cliff sits.

## 6. Next steps on the 64-core box

1. **Swap in `lru_cache_sharded.c`.** Call `c = cache_create(capacity)` where the cache is allocated today, and `cache_stats()` wherever `c->hits`/`c->misses` are read. Build as before (`-O2 -g -march=native`).
2. **Re-run the sweep and c2c.** Throughput should keep climbing past 8 threads instead of capping at about 13 Mops/s, and the 78% line should dissolve into a thin spread over the shard headers. Fix the pinning first if the 64-thread point should mean 64 cores.
3. **If c2c still shows hot shards or entries** because keys are very skewed, make hits write less, in increasing order of change:
   - skip the move when `e` is already `lru_head` (no semantic change);
   - bump each entry at most once per N ms (as memcached does);
   - switch to CLOCK eviction.
4. **Choose the shard count.** Use `NSHARDS` of at least about 4x the thread count (256 for 64 threads). With a small capacity and flat keys, 64–128 shards keep more hit rate.

One behavior is unchanged in both versions: two threads that miss on the same key both insert it, leaving a duplicate entry until it ages out.

Files are in `<work>/`:
- `lru_cache_sharded.c`: the fix.
- `analysis/model.py`: the ceiling / MVA / USL check of your sweep (`python3 -I analysis/model.py`).
- `analysis/offsets_check.c`: the layout asserts behind the c2c mapping.
- `repro/kvbench_local.c`, `repro/test_lru.c`, `repro/compat/immintrin.h`: the benchmark stand-in, the tests and the arm64 shim.
- `repro/run_sweep.sh` and `repro/summarize.py`: re-run the whole comparison (about 8 minutes).
- `repro/results/`: raw runs and `summary.txt`.

If you'd like this as a shareable page for your team, I can publish it.
