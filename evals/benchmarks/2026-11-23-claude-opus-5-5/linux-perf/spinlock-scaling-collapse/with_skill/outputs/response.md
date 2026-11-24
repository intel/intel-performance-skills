# CPU Scaling Report — kvbench (`lru_cache.c`)

## Bottom line

The bottleneck is the single global spinlock in `lru_cache.c`. `cache_lookup()` and `cache_insert()` both take `c->lock`, so the whole process can perform only one cache operation at a time. Two problems stack:

1. **One lock sets a hard ceiling of about 14.5M ops/s.** Your 1-thread profile spends 33.8% of each 204 ns request inside that lock, about 69 ns. One lock cannot hand out more than 1 / 69 ns ≈ 14.5M acquisitions per second, however many threads you add. You reach 13.1M at 8 threads (90% of the ceiling), and that is the flat part from 8 to 32.
2. **The lock is a test-and-set spinlock on the same cache line as the data it protects.** That is the drop at 64. Each waiter retries `lock cmpxchg`, which takes the line exclusively even when it fails. That line also holds `hits`, `misses`, `lru_head` and `lru_tail`. So 63 spinning threads keep pulling away the line the lock holder needs to finish, and every handoff gets slower as waiters are added. At 64 threads, half of them are on the second socket, so the line also crosses sockets.

At 64 threads, about 59% of all CPU cycles are spent in that 4-instruction spin loop.

Fixing the spin (TTAS, lock on its own line) should stop the collapse, but it cannot go above about 14.5M ops/s. To scale, shard the cache so threads stop sharing one lock and one LRU list. A reader-writer lock is not a fix: every lookup writes the LRU list and `hits`.

**Files.** All are new; `lru_cache.c` is unchanged.
- `<work>/proposed_fix/lru_cache_sharded.c`: the fix that scales.
- `<work>/proposed_fix/lru_cache_ttas.c`: the minimal lock fix.
- `<work>/proposed_fix/stress.c`: a multithreaded correctness harness; build lines are at the top. `arm64-shim/` next to it is test-only.

## Scaling sweep

| Threads | ops/s | Speedup | Marginal gain per added thread | Mean request time (threads ÷ ops/s) | p99 | p99 ÷ mean |
|--:|--:|--:|--:|--:|--:|--:|
| 1 | 4.91M | 1.00× | — | 0.20 µs | 0.61 µs | 3.0× |
| 2 | 8.73M | 1.78× | 0.78 | 0.23 µs | 0.83 µs | 3.6× |
| 4 | 12.05M | 2.45× | 0.34 | 0.33 µs | 1.92 µs | 5.8× |
| 8 | 13.10M | 2.67× | 0.05 | 0.61 µs | 4.40 µs | 7.2× |
| 16 | 13.02M | 2.65× | −0.00 | 1.23 µs | 9.75 µs | 7.9× |
| 32 | 12.44M | 2.53× | −0.01 | 2.57 µs | 22.1 µs | 8.6× |
| 64 | 7.86M | 1.60× | −0.03 | 8.14 µs | 71.3 µs | 8.8× |

Marginal gain = (ops(N) − ops(prev)) ÷ (N − prev) ÷ ops(1). Mean request time comes from Little's law for closed-loop workers.

- **The knee is really at about 3–4 threads.** Each request wants the lock for ~69 of its ~204 ns, so about 3 threads keep it busy all the time. By 4 threads, each added thread is worth only 0.34 of a thread.
- **Once saturated, the cache completes one op every 1 ÷ throughput:** 76 ns at 8 threads, 80 ns at 32, 127 ns at 64. The locked work itself is about 69 ns. The rest is the cost of passing the lock (and its line) between cores. It grows with the number of waiters and jumps when threads 33–64 land on socket 1.
- **The p99 ÷ mean ratio climbing from 3× to 9× is the unfairness of test-and-set.** Whoever happens to hold the line when the lock is freed wins it, so some threads lose repeatedly.

## Iteration 1 — Key functions

### Dual-profile table (64 threads vs 1 thread)

| 64T rank | Function | 64T % | 1T rank | 1T % | Rank Δ | Ratio |
|--:|---|--:|--:|--:|--:|--:|
| **1** | **`cache_lookup`** | **72.06%** | **1** | **29.84%** | **0** | **2.41×** |
| **2** | **`cache_insert`** | **8.91%** | **8** | **3.95%** | **▲6** | **2.26×** |
| 3 | `parse_request` | 4.77% | 2 | 17.12% | ▼1 | 0.28× |
| 4 | `xxh64` | 3.20% | 3 | 11.43% | ▼1 | 0.28× |
| 5 | `format_response` | 2.74% | 4 | 9.87% | ▼1 | 0.28× |
| 6 | `__memmove_avx512_unaligned_erms` | 2.13% | 5 | 8.02% | ▼1 | 0.27× |
| 7 | `backing_store_get` | 1.55% | 6 | 5.61% | ▼1 | 0.28× |
| 8 | `_int_malloc` | 1.21% | 7 | 4.40% | ▼1 | 0.27× |
| 12 | `native_queued_spin_lock_slowpath` [k] | 0.31% | — | — | new | below the 2% threshold |
| 9–15 | remaining entries | | | | | 0.27–0.28× |

- **The two jumpers are exactly the two functions that take `c->lock`.** Everything else shrinks by the same 0.27–0.28×, so none of it got relatively more expensive. There is no malloc contention and no slower hashing, so no second bottleneck is hiding at this level.
- **Kernel time is about zero.** The waiting is pure user-space spinning: waiters never sleep, they burn their core.

### Jumper function details

`struct cache` (lru_cache.c:18). The offsets were checked with `offsetof` on x86-64 and match the c2c Offset column:

```c
struct cache {
    volatile int  lock;               /* +0x00  ◄ write: lock cmpxchg from every thread */
    uint32_t      nentries;           /* +0x04  ◄ write (cache_insert) */
    uint64_t      hits;               /* +0x08  ◄ write on every hit */
    uint64_t      misses;             /* +0x10  ◄ write on every miss */
    struct entry *lru_head;           /* +0x18  ◄ read + write on every hit */
    struct entry *lru_tail;           /* +0x20  ◄ read on every hit */
    uint32_t      capacity;           /* +0x28 */
    struct entry *buckets[NBUCKETS];  /* +0x30 */
};
```

`spin_lock` (lru_cache.c:29) and `cache_lookup` (lru_cache.c:58), percentages from your 64-thread annotate:

```c
static inline void spin_lock(volatile int *l)
{
    while (!__sync_bool_compare_and_swap(l, 0, 1))  /* ◄ lock CAS, exclusive acquire: 68.93% */
        _mm_pause();                                /*   pause: 11.87% */
}

int cache_lookup(struct cache *c, uint64_t key, char out[64])
{
    int found = 0;

    spin_lock(&c->lock);                            /* ◄ lock CAS */
    struct entry *e = c->buckets[key & (NBUCKETS - 1)];
    while (e && e->key != key)
        e = e->hnext;
    if (e) {
        c->hits++;                                  /* ◄ cache contention (+0x08): 3.71% */
        lru_unlink(c, e);                           /* ◄ writes both neighbour entries */
        lru_push_front(c, e);                       /* ◄ cache contention (lru_head +0x18) */
        memcpy(out, e->value, sizeof e->value);
        found = 1;
    } else {
        c->misses++;
    }
    spin_unlock(&c->lock);
    return found;
}
```

`spin_lock` is a tiny inlined wrapper, so the real candidate is the calling pattern: one lock around every cache operation. `cache_insert` (lru_cache.c:81) takes the same lock, which is the `cache_insert+0x1b` row in c2c.

### Diagnostic results

**Annotate pattern scan, `cache_lookup` at 64 threads**

| Pattern | Evidence |
|---|---|
| Test-and-set spin | `lock cmpxchg %ecx,(%rdi)` 68.93% + `pause` 11.87%, in a 4-instruction loop (`0x2c50`–`0x2c58`) with no plain load of the lock before the atomic. That is 81.6% of the function, and 72.06% × 0.816 ≈ **59% of all cycles**. |
| Stores to the contended line inside the critical section | `hits++` (`addq $0x1,0x8(%rdi)`) 3.71% and `lru_head = e` (`mov %rax,0x18(%rdi)`) 2.12% are the next hottest instructions. Both write the lock's line. |

The real lookup work (bucket load, chain walk, 64-byte copy) is under 7% of the function. This is not a compute or vectorization problem.

**Focused c2c: cache line 0, `0x7f2c4a000080`. This is `struct cache` itself: 78.44% of all HITMs, touched from 64 CPUs.**

| Offset | Field | Accessor | Source | Share of the line's HITMs |
|---|---|---|---|--:|
| 0x00 | `lock` | `cache_lookup+0x14` `lock cmpxchg` | lru_cache.c:31 | 66.91% |
| 0x08 | `hits` | `cache_lookup+0x2c` `hits++` | lru_cache.c:67 | 9.63% |
| 0x18 | `lru_head` | `cache_lookup+0x4b` | lru_cache.c:51 | 8.79% |
| 0x00 | `lock` | `cache_insert+0x1b` `lock cmpxchg` | lru_cache.c:31 | 7.88% |
| 0x00 | `lock` | `cache_lookup+0x81` release store | lru_cache.c:37 | 4.21% |
| 0x20 | `lru_tail` | `cache_lookup+0x68` | lru_cache.c:54 | 1.10% |
| 0x10 | `misses` | `cache_lookup+0x98` | lru_cache.c:73 | 0.88% |
| 0x04 | `nentries` | `cache_insert+0x3a` | lru_cache.c:95 | 0.60% |

- **The lock word (+0x00) is true sharing.** All 64 threads write the same 4 bytes. That is 79% of this line's HITMs, about 62% of all HITMs in the run.
- **The other offsets are the data the lock protects.** Only the lock holder touches them, so they would move once per handoff anyway. But they share the line the waiters are hammering. So when the holder reaches `hits++` or `lru_head`, a waiter's failed `cmpxchg` has often already taken the line away. The holder waits about 380 cycles to get it back (c2c "lcl hitm" cycles for those rows), while holding the lock, right on the serial path. That is the other 21%, and it is why the handoff gets slower as waiters increase.
- **The c2c offset rule would call this false sharing.** Really the lock and its data are sitting together. The fix is to give the lock its own line, not to pad the fields apart from each other.
- **Lines 1–5 (0.9–2.6% each, 8.7% combined).** Line 1's detail is a `struct entry` with `lru_prev` (+0x10, lru_cache.c:52) and `lru_next` (+0x18, lru_cache.c:42) being written. That is move-to-front traffic: every hit rewrites the previous head entry and the entry's neighbours. These lines are below the 5% threshold today, but they come next, and they mean a single strict-LRU list cannot scale even behind a perfect lock.

**Why it falls at 64 instead of staying flat**

- **The line moves on every attempt.** Every failed `lock cmpxchg` is a read-for-ownership, so the line changes cores on each attempt by each waiter.
- **More waiters means slower handoffs.** Each handoff needs more transfers, and the holder loses its own data line more often.
- **64 threads cross sockets.** Up to 32 threads, everything stays on socket 0. At 64, half the threads are on socket 1, so many of those transfers go over UPI. The time per op through the lock rises from 80 ns to 127 ns, which is the 37% drop.

## Solutions

### Proposed: shard the cache (the fix that scales)

`lru_cache_sharded.c` keeps the same `cache_lookup` / `cache_insert` signatures:

- **Shards:** 256 independent LRU caches. Each owns a contiguous slice of today's bucket array plus its own lock, LRU list and counters.
- **Shard choice:** the shard comes from the same key bits that pick the bucket today, so the chains and key distribution are unchanged.

```c
#define NSHARDS       256                   /* power of two; aim for >= 4x threads */
#define SHARD_BUCKETS (NBUCKETS / NSHARDS)

struct shard {
    volatile int  lock;                     /* alone in its 128-byte block */
    _Alignas(128) struct entry *lru_head;   /* from here on: touched by the holder only */
    struct entry *lru_tail;
    uint32_t      nentries, capacity;       /* capacity = ceil(total / NSHARDS) */
    uint64_t      hits, misses;             /* per shard; cache_stats() sums them */
    struct entry *buckets[SHARD_BUCKETS];
};
struct cache { struct shard shards[NSHARDS]; };

static inline unsigned shard_index(uint64_t key)  { return (key & (NBUCKETS - 1)) / SHARD_BUCKETS; }
static inline unsigned bucket_index(uint64_t key) { return key & (SHARD_BUCKETS - 1); }

int cache_lookup(struct cache *c, uint64_t key, char out[64])
{
    struct shard *s = &c->shards[shard_index(key)];
    /* ... same body as today, with s instead of c, TTAS spin_lock ... */
}
```

- **The ceiling goes away.** 64 threads that each want a lock about a third of the time keep each of 256 locks only about 8% busy. The ~14.5M ops/s single-lock limit is no longer the limit, and LRU-head and counter traffic is split 256 ways.
- **Eviction becomes per-shard LRU (approximate global LRU).** In my harness (97K entries, about 380 per shard) the hit rate went from 97.0% to 96.2%, because shards get unequal shares of the keys. The effect shrinks as entries per shard grow. Use fewer shards or a little more capacity if it matters.
- **`cache_insert` now re-checks for the key under the lock.** Today, two threads that miss on the same key both insert it. My stress test reproduces the duplicate entries with your current code.
- **Integration:** create the cache with `aligned_alloc(128, …)` + `cache_init(c, capacity)`, and read counters with `cache_stats()`. The kvbench code that creates the cache and reads `hits`/`misses` wasn't in what you sent, so that is the part to adapt.

### Proposed: TTAS with the lock on its own line (minimal change, and a cheap test of this diagnosis)

`lru_cache_ttas.c` is your file with two changes:

```c
struct cache {
    volatile int  lock;                 /* +0x00, alone in its 128-byte block */
    _Alignas(128) uint32_t nentries;    /* +0x80, protected data starts here */
    ...                                 /* rest unchanged */
};

static inline void spin_lock(volatile int *l)
{
    while (!__sync_bool_compare_and_swap(l, 0, 1))      /* try first: same uncontended cost */
        while (__atomic_load_n(l, __ATOMIC_RELAXED))    /* then read-only spin: line stays Shared */
            _mm_pause();
}
```

- **Waiters spin on a shared copy** and attempt the atomic only when the lock reads free, so the line stops moving on every attempt. The holder's `hits` and `lru_head` are no longer on the waiters' line.
- **Why try-first:** it keeps the uncontended path identical to today, whereas plain TTAS reads first, then CASes.
- **Why 128 bytes rather than 64:** the L2 adjacent-line prefetcher pulls lines in 128-byte pairs, which is the same reason your c2c used `--double-cl`.
- **Prediction to test:** the curve should flatten near the ceiling instead of falling, with some cross-socket cost left at 64. It should not go above about 14.5M ops/s. If it behaves that way, both halves of this diagnosis are confirmed.
- **If you must live with one lock for a while,** a FIFO queue lock (MCS or ticket) also caps the p99.

### Not recommended: `pthread_rwlock` instead of the spinlock

- **A lookup is a write.** `cache_lookup` writes on every hit (`hits++`, `lru_unlink`, `lru_push_front`). Under a read lock that corrupts the list; under a write lock it is just a slower mutex.
- **Read-only lookups would still bounce a line.** Even then, `pthread_rwlock_rdlock` atomically updates a shared reader count, so one line still moves between 64 cores on every lookup.
- **First stop writing on hits, if ever needed.** Read-mostly schemes only make sense after that, for example:
  - promote an entry only if it hasn't been promoted recently (memcached bumps an item at most once every 60 s);
  - CLOCK, where a hit only sets a "referenced" bit if it's not already set.

  Do this after sharding, and only if the per-shard LRU lines still show up in c2c.

A smaller point: `cache_insert` calls `malloc()` while holding the lock. That only happens while the cache is filling, but a page fault there stalls every waiter. Preallocating entries at init avoids it.

## Verification plan (on the 64-core box)

1. **Sweep:** rerun the 1–64-thread sweep for each variant.
2. **`perf annotate -s cache_lookup`:** with TTAS, the `lock cmpxchg` share should collapse and the load/`pause` loop should become the hot spot. With sharding, `cache_lookup` should drop back toward its ~30% single-thread share.
3. **`perf c2c` at 64 threads:** line 0 should fall from 78% of HITMs, with no single line dominating.
4. **`perf stat -e task-clock,cycles,instructions` at 1/8/64 threads:** gives IPC and "CPUs utilized" (see the second loose end below).

## What I checked here

- **Offsets:** the `struct cache` / `struct entry` offsets match the c2c Offset column exactly (`offsetof`, x86-64). Clang emits the same bare `lock cmpxchg` / `pause` loop for your code.
- **Builds:** both proposed files compile with no warnings (`-Wall -Wextra`, x86-64). The TTAS loop comes out as intended: one `lock cmpxchg`, then a `mov`/`test`/`pause` loop.
- **Stress test:** `stress.c` runs a 97%-hit workload, checks every returned value, and walks every LRU list and hash chain afterwards. It passes for all three versions, including under ThreadSanitizer. Duplicate keys appear only in the two versions that keep your `cache_insert`.
- **Throughput here means nothing for your box.** This is Apple Silicon running x86 under Rosetta, and the machine was fully loaded by other jobs. The shape did match, though: both single-lock versions lost throughput as threads were added, and the sharded one scaled.

## Loose ends in the data

Neither changes the diagnosis.

- **c2c reports 0 remote HITMs and only node 0** at 64 threads, although with socket-0-first pinning, 32 of the 64 threads should be on socket 1. Check `numactl -H` and where the c2c run's threads actually ran. If remote HITMs are being counted as local, c2c understates the 64-thread cost.
- **The 64-thread profile under-counts waiting.** Little's law gives 8.1 µs per request at 64 threads versus 0.2 µs alone. So about 97% of each thread's time should be lock wait, and the non-cache request path should be about 2% of samples, not 19%. Either the profiled run differed from the sweep run, or not all threads were on-CPU. "CPUs utilized" from `perf stat` should be about 64. If it is much lower, threads are being descheduled, and a descheduled spinlock holder stalls every spinner.

If it helps, I can plot the scaling curve or turn this report into a shareable page.
