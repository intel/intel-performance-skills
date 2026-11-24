# What your c2c report shows

Two structures cause **88% of all HITMs** (53,914 in total, all local, all on node 0). There are two different problems, and they need two different fixes:

1. **`g_wstats[]`: false sharing, 54.5% of HITMs.**
   - `struct worker_stats` is 16 bytes, so 4 workers share every 64-byte cache line and 8 share every 128-byte pair.
   - Each worker only writes its own entry. But every `rx_packets++` takes the line away from its neighbours.
2. **`g_state`: true sharing plus a false-sharing victim, 33.8% of HITMs.**
   - **`inflight` (true sharing, 26.5%):** all 32 workers do a `lock` inc and a `lock` dec on the single `g_state.inflight` counter for every packet.
   - **`shutting_down` (false sharing, 7.3%):** it is polled on every loop iteration and never written during the run. It sits 16 bytes from `inflight` on the same line, so every poll misses too.

Each of these misses costs **161–317 cycles** (the report's `lcl hitm` column), against about 5 for an L1 hit.

The `inflight` line is shared by *every* worker, so every packet in the process waits its turn on that one line. Adding threads adds waiters, not throughput. Padding fixes `g_wstats` and `shutting_down`. It **cannot** fix `inflight`: a single variable written by every thread has nothing to separate, so it has to stop being a shared counter.

I've applied the fix to `pktsrv.c` and `stats.h`, and also saved it as `false-sharing-fix.patch` (apply with `patch -p1` in your source directory).

## Summary

You used `--double-cl`, so each entry below is a 128-byte pair of cache lines and offsets run from 0x00 to 0x7f.

| # | Address | Tot Hitm | What it is | Sharing type |
|---|---|---|---|---|
| 1–4 | `0x5612e4a1d200`, `…d280`, `…d300`, `…d380` | 14.62 + 13.95 + 13.20 + 12.77 = **54.54%** | `g_wstats[0..31]`, 8 workers per 128 B | **False sharing** |
| 0 | `0x5612e4a1d100` | **33.81%** | `g_state` | **True sharing** on `inflight` (+0x00, 78% of the line) **and false sharing** on `shutting_down` (+0x10, 22%) |
| 5, 6, 8–13 | `0x5612e5b2…` | 3.12% down to 0.70% (10.3% total) | per-worker RX rings (`ring.c`) | True sharing; each is below the 5% threshold |
| 7 | `0x7f3a1c21ac80` | 1.31% | glibc malloc arena | Below the threshold |

---

## Case 1: `g_wstats[]`, false sharing (54.5% of HITMs)

### The data structure

From `stats.h:11` and `pktsrv.c:15`:
```c
struct worker_stats {          /* sizeof = 0x10 */
    uint64_t rx_packets;       /* +0x00 */
    uint64_t rx_bytes;         /* +0x08 */
};
struct worker_stats g_wstats[MAX_WORKERS];   /* g_wstats[i] at +0x10*i */
```
- `0x…d200` holds `g_wstats[0..7]`.
- `0x…d280` holds `g_wstats[8..15]`.
- `0x…d300` holds `g_wstats[16..23]`.
- `0x…d380` holds `g_wstats[24..31]`.

### Access map for line 1 (`0x5612e4a1d200`)

Lines 2–4 show the same pattern.

| LclHitm | Offsets | Field | Code | Source | CPUs per offset |
|---|---|---|---|---|---|
| 49.3% (8 rows, ≈6% each) | 0x00, 0x10, … 0x70 | `g_wstats[0..7].rx_packets` | `worker_main+0x7c` | `pktsrv.c:58` | 1 (2 means the thread migrated) |
| 49.7% (8 rows, ≈6% each) | 0x08, 0x18, … 0x78 | `g_wstats[0..7].rx_bytes` | `worker_main+0x84` | `pktsrv.c:59` | 1–2 |
| 1.0% | 0x00, 0x28, 0x50, 0x78 | (reads) | `monitor_main+0x4e/+0x52` | `pktsrv.c:74–75` | 1 |

There are 16 different offsets, and each one is written by a single CPU. Different fields on the same line means **false sharing**.

The monitor is not the problem (1%); the workers are interfering with each other. Worker 1's `rx_packets++` finds the line Modified in worker 0's cache, because worker 0 has just bumped *its own* counter. The header comment "Each worker only ever writes its own entry" is true, and that's the trap: the data is logically private but physically shared.

### Accessor: `worker_main`, `pktsrv.c:51–60`

This function accounts for 99% of the HITMs on lines 1–4 and 99.7% of those on line 0. It is the thread entry point, with one thread per worker started at `pktsrv.c:106`. It is 20 lines, so here it is in full:
```c
static void *worker_main(void *arg)
{
    int id = (int)(intptr_t)arg;
    struct worker_stats *ws = &g_wstats[id];
    struct ring *rx = ring_for_worker(id);
    struct pkt p;

    while (!g_state.shutting_down) {             /* ◄ read  g_state+0x10      (Case 2) */
        if (!ring_pop(rx, &p, sizeof p)) {
            __builtin_ia32_pause();
            continue;
        }
        atomic_fetch_add_explicit(&g_state.inflight, 1, memory_order_relaxed); /* ◄ write g_state+0x00 */
        process_packet(&p);
        ws->rx_packets++;                        /* ◄ write g_wstats[id]+0x00 (Case 1) */
        ws->rx_bytes += p.len;                   /* ◄ write g_wstats[id]+0x08 (Case 1) */
        atomic_fetch_sub_explicit(&g_state.inflight, 1, memory_order_relaxed); /* ◄ write g_state+0x00 */
    }
    return NULL;
}
```

---

## Case 2: `g_state`, true sharing on `inflight` and false sharing on `shutting_down` (33.8% of HITMs)

### The data structure

From `stats.h:17`:
```c
struct server_state {               /* sizeof = 0x18 */
    _Atomic uint64_t inflight;      /* +0x00 */
    _Atomic uint64_t total_conns;   /* +0x08 */
    volatile int     shutting_down; /* +0x10 */
    int              log_level;     /* +0x14 */
};
```

### Access map for line 0 (`0x5612e4a1d100`)

| LclHitm | Offset | Field | Code | Source | CPUs |
|---|---|---|---|---|---|
| 40.21% | 0x00 | `inflight`, `lock` add | `worker_main+0x56` | `pktsrv.c:56` | 32 |
| 38.04% | 0x00 | `inflight`, `lock` sub | `worker_main+0x89` | `pktsrv.c:60` | 32 |
| 21.48% | 0x10 | `shutting_down`, read | `worker_main+0x2d` | `pktsrv.c:51` | 32 |
| 0.21% | 0x00 | `inflight`, read | `monitor_main+0x9b` | `pktsrv.c:79` | 1 |
| 0.06% | 0x10 | `shutting_down`, read | `monitor_main+0x1f` | `pktsrv.c:70` | 1 |

- **`inflight` (+0x00) is true sharing.** All 32 CPUs write the same offset. This is the "shared statistics counter" pattern: every worker does two locked read-modify-writes on one line for every packet. Padding can't help here.
- **`shutting_down` (+0x10) is a false-sharing victim.** It's a different field and is never written during the run. It accounts for 21.5% of this line's HITMs only because it sits next to `inflight`. A worker spinning on an empty ring also rereads it on every iteration, which pulls the line into its own cache. The next `lock` operation by a busy worker then has to invalidate that copy again, so idle threads slow down the busy ones.

---

## What to change

This is already applied; see `false-sharing-fix.patch`.

### 1. Give each worker its own 128-byte block (`stats.h`)

```c
#define PKTSRV_CL_PAIR 128

struct worker_stats {
    _Alignas(PKTSRV_CL_PAIR)
    _Atomic uint64_t rx_packets;    /* +0x00 */
    _Atomic uint64_t rx_bytes;      /* +0x08 */
    _Atomic uint32_t busy;          /* +0x10  replaces g_state.inflight */
                                    /* +0x14 .. +0x7f  padding */
};
_Static_assert(sizeof(struct worker_stats) == PKTSRV_CL_PAIR, "...");
```

I used 128 bytes, not 64:
- **The prefetcher works in pairs.** On Xeons, the L2 spatial (adjacent-line) prefetcher fetches lines in 128-byte-aligned pairs. Threads writing neighbouring 64-byte lines can therefore still interfere, and that is the effect `--double-cl` is designed to show.
- **64 bytes isn't enough.** With 64-byte padding, workers 2k and 2k+1 would still share each 128-byte block.
- **The cost is small:** 64 × 128 B = 8 KB.

### 2. Keep counters private and replace the global `inflight` with a per-worker `busy` flag (`worker_main`)

```c
    uint64_t pkts  = atomic_load_explicit(&ws->rx_packets, memory_order_relaxed);
    uint64_t bytes = atomic_load_explicit(&ws->rx_bytes, memory_order_relaxed);

    while (!atomic_load_explicit(&g_state.shutting_down, memory_order_relaxed)) {
        if (!ring_pop(rx, &p, sizeof p)) { __builtin_ia32_pause(); continue; }
        atomic_store_explicit(&ws->busy, 1, memory_order_relaxed);
        process_packet(&p);
        pkts  += 1;
        bytes += p.len;
        atomic_store_explicit(&ws->rx_packets, pkts, memory_order_relaxed);
        atomic_store_explicit(&ws->rx_bytes, bytes, memory_order_relaxed);
        atomic_store_explicit(&ws->busy, 0, memory_order_relaxed);
    }
```
- **No locked instructions are left in the hot path.** Each slot has exactly one writer, so a relaxed load and store is enough. On x86 that is a plain `mov` into a line the worker keeps in its own L1.
- **The monitor still reports the same number.** It now sums `g_wstats[i].busy` to get the count of workers currently processing a packet.
- **Why per-worker and not batching.** `inflight` is a gauge that goes +1 and then −1 for each packet, so batching it locally would always net to zero. A per-worker flag keeps its meaning. If nobody actually looks at the inflight number, you can simply delete it.
- **Don't write `ws->rx_packets++` on these `_Atomic` fields.** That compiles to a `lock` read-modify-write.
- **This also fixes a real data race.** The monitor was reading plain `uint64_t` counters while the workers wrote them.

### 3. Put `shutting_down` in a read-mostly block (`stats.h`)

```c
struct server_state {
    _Alignas(PKTSRV_CL_PAIR)
    atomic_int       shutting_down; /* +0x00  polled by all workers, written once */
    int              log_level;     /* +0x04 */
    _Alignas(PKTSRV_CL_PAIR)
    _Atomic uint64_t total_conns;   /* +0x80  written occasionally, kept off the polled block */
};
```
- **The poll becomes an L1 hit**, because nothing in its block is written at runtime.
- **I changed `volatile int` to `atomic_int`.** `volatile` doesn't make cross-thread access well defined in C11. A relaxed atomic load compiles to the same `mov`, and storing to a lock-free atomic is allowed in a signal handler.
- **Explicit alignment also stops the linker from deciding the layout.** In my clang build of the *original* code, `g_cfg` landed at `g_state+0x18`. That would have made `g_cfg.checksum_offload`, which is read on every packet, a third victim on the `inflight` line. Your GCC build happened to put it somewhere else.

Rebuild everything that includes `stats.h`, because the struct sizes have changed. If another file (for example `ring.c`) uses `g_state.inflight`, it will now fail to compile. That's intentional, so you'll find any remaining users.

---

## How to confirm the fix on the Xeon

```bash
gcc -O2 -g ...    # your usual build; keep -g
objdump -d --no-show-raw-insn pktsrv | awk '/<worker_main>:/,/^$/' | grep -cw lock   # was 2, expect 0
perf c2c record -g -- ./pktsrv --threads 32 --duration 30
perf c2c report --full-symbols --stdio --double-cl > c2c_after.txt
```
What to expect:
- **The `g_state` entry and the four `g_wstats` entries should drop out of the Shared Data Cache Line Table.** At most, a few HITMs from the monitor's once-a-second reads should remain. The target is under 1% each.
- **Compare HITMs per packet, not raw totals.** Throughput should go up, and the ring entries will grow along with it, because each packet is one producer-to-consumer handoff.
- **Check that throughput now scales.** Run with `--threads` 1, 4, 8, 16 and 32 and watch the monitor's `pkts/s`. It should now rise with the thread count.

If those entries don't drop, then part of the diagnosis is wrong. In that case, send me the new report before changing anything else.

## Below the 5% threshold (not the cause of your scaling problem)

- **RX rings** (entries 5, 6 and 8–13; 10.3% in total, at most 3.12% each):
  - In each ring, `ring_push` (`ring.c:28`) writes +0x00 and `ring_pop` (`ring.c:41`) reads it. `ring_pop` (`ring.c:47`) writes +0x40 and `ring_push` (`ring.c:24`) reads it.
  - Each offset is written on one side and read on the other, so this is true sharing. That's the unavoidable handoff in a single-producer, single-consumer ring.
  - Head and tail are already on separate 64-byte lines; `--double-cl` just groups them into one entry.
  - If you want to trim this later: `g_cfg.batch = 32` exists, but `worker_main` pops one packet per call. Batched pops, plus each side caching the other side's index, would cut the index traffic.
  - I don't have `ring.c`, so all of this is read from the offsets only.
- **glibc malloc arena** (entry 7, 1.31%, 4 CPUs): `_int_malloc` and `_int_free` are sharing an arena. It doesn't come from the worker loop, which uses a packet on the stack; it's probably on the producer side. You can ignore it for now.

## What I checked here

I don't have `perf` here, so I ran these checks on a Mac with x86-64 code under Rosetta and a stub ring. They're a sanity check only.
- **Build and layout:** both versions compile cleanly with `-Wall -Wextra -Wpedantic`, and the `_Static_assert`s hold. The offsets above come from `offsetof`, not from counting by hand.
- **Disassembly of `worker_main`:** the original has 2 `lock` instructions (`lock incq` and `lock decq` on `_g_state`), and the patched version has **0**. The counters stay in registers and are written with plain `movq` into slots 128 bytes apart.
- **ThreadSanitizer:** the original has 2 data-race reports (`pktsrv.c:74–75`), and the patched version has **0**.
- **Patch file:** `false-sharing-fix.patch` applies cleanly to the original files.
- **Throughput** (millions of packets per second, median of three 1-second samples; the stub ring makes 64-byte packets locally and has no shared state):

| threads | original | padding only (global `inflight` kept) | full patch |
|---|---|---|---|
| 1 | 13.3 | 13.1 | 12.8 |
| 2 | 14.3 | 26.3 | 26.9 |
| 4 | 11.8 | 36.3 | 50.2 |
| 6 | 12.2 | 36.9 | 43.5 |
| 12 | 14.8 | 36.5 | 63.4 |
| 18 | 16.3 | 44.4 | 141.9 |

The middle column is the important one. Removing only the false sharing helps, but throughput then flattens from about 4 threads on because of `inflight`. Removing the shared counter is what lets it keep scaling.

The numbers are noisy on this machine: an earlier run gave 164 and 225 M/s for the full patch at 12 and 18 threads. Both runs showed the same pattern. This is a different CPU from your Xeon, so the c2c rerun on your box is the real test.

## Assumptions and open questions

- **Ring code:** `ring.c` and `ring.h` weren't attached. The ring notes are inferred from the report, and my stub ring is not your ring.
- **`inflight`:** I treated it as display-only, because only the monitor reads it here. If something else depends on it (for example draining on shutdown), sum the per-worker `busy` flags instead.

Files are in `<work>/`:
- `pktsrv.c` and `stats.h`, edited in place.
- `false-sharing-fix.patch`.
- `_verify/`, which has the untouched originals, the stub ring, the padding-only variant, the benchmark scripts and the results.
