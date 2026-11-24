## What's going on

The report points at two globals, `g_wstats` and `g_state`. Together they cause **88% of all HITMs** (53,914 HITMs, all local, all on node 0). Only part of it is false sharing. The biggest problem on `g_state` is a counter that every thread really does share, and padding can't fix that.

| c2c line(s) | What it is (from the offsets and source lines) | HITMs | Kind |
|---|---|---|---|
| #1–#4 (`…d200`–`…d380`) | `g_wstats[0..31]`: `rx_packets` (pktsrv.c:58) at 0x0, 0x10, … and `rx_bytes` (:59) at 0x8, 0x18, …. Each offset is touched by only 1–2 CPUs. | 29,405 (54.5%) | **False sharing** |
| #0 (`…d100`), offset 0x0 | `g_state.inflight`: `atomic_fetch_add`/`atomic_fetch_sub` at :56 and :60 on the same 8 bytes, from **32 CPUs** | ~14,300 (26.5%) | **True sharing** (a contended atomic) |
| #0, offset 0x10 | `g_state.shutting_down`, read on every loop iteration (:51), on the same line as `inflight` | ~3,930 (7.3%) | **False sharing** |
| #5, #6, #8–#13 | Per-worker RX rings. `ring_pop` reads the index at 0x0 (ring.c:41) that `ring_push` writes (ring.c:28). The index at 0x40 works the other way round (ring.c:24 reads it, ring.c:47 writes it). | 5,575 (10.3%) | True sharing between producer and consumer; this is how a ring works |
| #7 (`0x7f3a1c21ac80`) | glibc malloc arena, `_int_malloc`/`_int_free` from 4 CPUs | 706 (1.3%) | Allocator; minor |

**Telling the two kinds apart in the Pareto table:**
- **False sharing** is many offsets in one line, each with `cpu cnt` 1–2. Each worker writes only its own slot, but `struct worker_stats` is 16 bytes, so 4 workers share every 64-byte line. Padding fixes this.
- **True sharing** is one offset with `cpu cnt` 32 (`inflight`). Padding does nothing for this; the only fix is to stop sharing the counter.

**Why it gets slower as you add threads.** Every packet does two `lock`-prefixed read-modify-writes on the line that holds `inflight`. So every worker waits in turn for ownership of that one line, at roughly 200–300 cycles per handoff (the `lcl hitm` cycles column). That line sets the server's throughput limit, not the core count, and each added thread makes the queue longer.

`shutting_down` on the same line makes it worse. Each worker's loop check, including the idle spin on an empty ring, pulls in a shared copy of the line, and the next `lock add` then has to invalidate all those copies. Each `g_wstats` update also takes its line away from up to 3 neighbouring workers, or 7 counting the adjacent-line pair.

**About `--double-cl`.** It only changes how samples are grouped (into 128-byte pairs); the HITM events are still counted per 64-byte line. So read the offsets: 0x00–0x3f is one line and 0x40–0x7f is the next.
- That is why the rings look as if they share a line. Their two indices are at 0x00 and 0x40, so they are already on separate 64-byte lines. Those HITMs are the consumer reading the producer's index and the producer reading the consumer's.
- `--double-cl` was still the right call. Intel's L2 spatial prefetcher fetches 64-byte lines in 128-byte-aligned pairs, so writers on neighbouring lines still interfere. That's why the fix pads to 128 bytes, not 64.

## What I changed (pktsrv.c and stats.h, edited in place)

1. **`struct worker_stats` is now `_Alignas(128)`.** Each worker owns a whole 128-byte block, so the array grows from 1 KB to 8 KB. Each worker keeps its counters in local variables and publishes them with relaxed atomic stores. There is only one writer per entry, so these compile to plain `mov`s with no `lock`. It also makes the monitor's concurrent reads well-defined, which they weren't before.
2. **The global `inflight` is gone.** It is now a per-worker 0/1 field in the same block, and the monitor adds them up, so the `inflight=` output doesn't change.
3. **`g_state` is split.** `shutting_down` and `log_level` (both read-mostly) share one 128-byte block, and `total_conns` has another. Nothing in `g_state` is written per packet any more, so the loop check is an L1 hit.

```diff
 struct worker_stats {
-    uint64_t rx_packets;
-    uint64_t rx_bytes;
+    _Alignas(PKTSRV_DESTRUCTIVE_SIZE) _Atomic uint64_t rx_packets;   /* 128 */
+    _Atomic uint64_t rx_bytes;
+    _Atomic uint64_t inflight;  /* 1 while this worker is processing a packet */
 };
 struct server_state {
-    _Atomic uint64_t inflight;
-    _Atomic uint64_t total_conns;
-    volatile int     shutting_down;
+    _Alignas(PKTSRV_DESTRUCTIVE_SIZE) volatile int shutting_down;
     int              log_level;
+    _Alignas(PKTSRV_DESTRUCTIVE_SIZE) _Atomic uint64_t total_conns;
 };
```
```diff
-        atomic_fetch_add_explicit(&g_state.inflight, 1, memory_order_relaxed);
+        atomic_store_explicit(&ws->inflight, 1, memory_order_relaxed);
         process_packet(&p);
-        ws->rx_packets++;
-        ws->rx_bytes += p.len;
-        atomic_fetch_sub_explicit(&g_state.inflight, 1, memory_order_relaxed);
+        rx_packets++;                 /* thread-local running totals */
+        rx_bytes += p.len;
+        atomic_store_explicit(&ws->rx_packets, rx_packets, memory_order_relaxed);
+        atomic_store_explicit(&ws->rx_bytes, rx_bytes, memory_order_relaxed);
+        atomic_store_explicit(&ws->inflight, 0, memory_order_relaxed);
```

`monitor_main` now adds up the three per-worker fields using relaxed loads. A `_Static_assert` checks the 128-byte alignment.

What I checked:
- It builds warning-free with clang (`-Wall -Wextra -Werror`, x86-64) and passes a `gcc-14 -std=c11 -pedantic -fsyntax-only` check.
- With both compilers, `sizeof(struct worker_stats)` is 128 and `g_wstats[i]` entries are 128 bytes apart.
- In the clang x86-64 build, the per-packet path now has no `lock`-prefixed instructions; the original had `lock inc` and `lock dec` there.

## Local sanity check (not your Xeon)

`ring.c`/`ring.h` weren't attached, so I used a stand-in ring that never runs empty, has no producer thread and serves 64-byte packets. That leaves only the `g_state`/`g_wstats` traffic. These are x86-64 builds run under Rosetta on an 18-core Apple M5 Max; figures are Mpkt/s, the median of the per-second samples:

| threads | 1 | 2 | 4 | 8 | 12 | 16 |
|---|---|---|---|---|---|---|
| original | 14.5 | 16.8 | 13.3 | 13.6 | 16.2 | 17.2 |
| pad `g_wstats` only | 14.8 | 18.0 | 24.4 | 26.9 | 23.4 | 22.6 |
| pad everything, keep the global `inflight` | 15.0 | 28.3 | 52.8 | 55.0 | 52.9 | 49.1 |
| **this patch** | 15.1 | 28.2 | 56.0 | 109.3 | 163.8 | 125.8 |

The absolute numbers won't carry over to your box, but the shape should:
- Padding the stats alone roughly doubles throughput, then flattens.
- Moving `shutting_down` off the counter's line doubles it again.
- Only removing the shared atomic lets it keep scaling.

The dip at 16 threads is most likely this machine running short of idle cores: it has 18 cores of two kinds and other jobs were running. A second run under heavier background load gave the same ordering.

## Still left: the rings (~10%; not changed because `ring.c` wasn't attached)

The ring traffic is true sharing, so padding won't remove it, but you can make it much less frequent:
- **Cache the other side's index.** The consumer re-reads the producer's index only when the ring looks empty, and the producer re-reads the consumer's only when it looks full.
- **Pop and push in bursts.** `g_cfg.batch = 32` exists, but `worker_main` pops one packet at a time. Bursts mean one index read and one index write per 32 packets instead of per packet.
- **Move the two indices 128 bytes apart.** They are 64 bytes apart now, so they are still affected by the adjacent-line prefetch.

The malloc line (1.3%) means some code outside these two files calls malloc/free in a hot loop on 4 CPUs. For example, 2 KB packet buffers are above glibc's default tcache limit (1032 bytes), so every such call goes through the shared arena. You recorded with `-g`, so this shows who calls it:

`perf report -i perf.data --stdio --no-children -S _int_malloc,_int_free -g caller`

It's not worth chasing until the rest is fixed.

## Verify on the box

1. **Re-run c2c.** Rebuild, then repeat the same `perf c2c record -g …` and `perf c2c report … --double-cl`. Lines #0–#4 should disappear, leaving the rings and malloc at the top. The monitor still reads each worker's block once a second, but that comes to only a few dozen HITMs per second in total.
2. **Measure throughput.** Run `--threads 1/2/4/8/16/32` before and after and compare the monitor's `pkts/s`. That is the number that matters: c2c shows where sharing happens, not how much time it costs.

## Assumptions and notes

- **`g_state.inflight` no longer exists.** If code I haven't seen reads it, that code will now fail to compile; add up `g_wstats[i].inflight` there instead.
- **Other files writing the stats.** The stats fields are now `_Atomic`. If other files write them, use relaxed load/store; `++` would compile to a `lock`ed instruction.
- **`g_cfg`** is read on every packet but doesn't appear in the report. It is never written, so every core keeps its own copy and it's fine as it is.
- **`shutting_down` is still `volatile int`.** Reading it from other threads is formally a data race. An `atomic_int` with relaxed loads costs the same on x86 and is still safe to set from the signal handler. I left it as is to keep this diff about sharing.
