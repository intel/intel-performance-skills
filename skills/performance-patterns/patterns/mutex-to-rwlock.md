<!-- (C) 2026 Intel Corporation, MIT license -->
# Pattern: mutex protecting read-heavy operations → rwlock

## When to apply

Apply when a mutex (kernel `mutex` or user-space `pthread_mutex`) protects a
critical section where the majority of acquisitions perform only reads. Under
contention at high core counts, all readers serialize on the mutex even though
they do not conflict with each other.

### Source code signals

- `mutex_lock()` / `pthread_mutex_lock()` guarding a lookup, search, or status
  check that rarely modifies the protected data
- Critical section contains read operations (cache lookup, tree search,
  configuration read) in the common path, with writes only on a rare branch
  (creation, insertion, update)
- Read-to-write ratio exceeds ~75% (the crossover threshold where rwlock wins)
- The protected data structure is long-lived and accessed by many threads
  (unit table, connection pool, routing table, config cache)

### Profiling signals

- `osq_lock` prominent in `perf report` — this is the Optimistic Spin Queue,
  the spinning phase of the Linux kernel mutex fast path; high cycles here mean
  threads are spinning waiting for a mutex holder
- `mutex_lock` / `__mutex_lock_slowpath` / `__mutex_lock.constprop.0` in the
  hotspot list
- `native_queued_spin_lock_slowpath` under a `mutex_lock` call chain (the mutex
  internally uses a spinlock to protect its wait queue)
- IPC drops dramatically as core count grows while user-space work remains
  constant — the incremental cycles come from lock contention
- Kernel cycle ratio increases with core count (user-space mutex slow path
  enters kernel via `futex_wait`; this signal applies to `pthread_mutex`, not
  kernel `mutex`)
- `perf lock stat` shows high `contentions` relative to `acquisitions` and
  `wait_total` >> `hold_total` for the identified mutex

---

## Why this is slow

A mutex is an **exclusive lock**: every acquisition moves the lock cache line to
Modified state via a LOCK CMPXCHG (Read-For-Ownership). Even two threads that
only read the protected data must take turns:

```
Mutex with N readers (all serialize):

  Thread 1: [LOCK CMPXCHG → M state] read data [unlock → store]
  Thread 2:  ← waits (RFO pending) → [LOCK CMPXCHG → M state] read data [unlock]
  Thread 3:  ← waits ──────────────── ← waits (RFO pending) → [acquire] read [unlock]
  ...
  Thread N:  ← waits for all N-1 predecessors
```

At HCC scale (100+ cores), this serialization is catastrophic:

1. **O(N) wait time per reader** — each reader must wait for all preceding
   readers to release, even though no data is being modified
2. **OSQ spin burns cycles** — the kernel mutex optimistic spin queue keeps
   threads spinning on their MCS nodes while the holder is running; with many
   readers each holding briefly, the aggregate spin time is enormous
3. **Cache-line bouncing** — the mutex's internal state transitions
   (locked → unlocked → locked) force the lock cache line to bounce between
   cores via the LLC/CHA, adding coherence latency to every handoff

The Linux kernel mutex implementation has three acquisition phases:
1. **Fast path** — single LOCK CMPXCHG; succeeds if mutex is unlocked
2. **Midpath (OSQ)** — optimistic spinning on a per-CPU MCS node while the
   owner is running on another CPU; avoids the cost of sleeping
3. **Slow path** — thread is added to the wait queue and calls `schedule()`
   (sleeps); woken by the holder on unlock via `wake_up_process()`

When `osq_lock` dominates perf, threads are stuck in the midpath — spinning
because the mutex holder is running (doing its read-only work) but not
releasing fast enough for the queue of waiters.

---

## The fix: replace mutex with rwlock

An rwlock (read-write lock) distinguishes read acquisitions from write
acquisitions:

- **Readers** hold the lock in Shared mode — multiple readers proceed
  simultaneously with greatly reduced serialization compared to a mutex
  (reader-count updates still produce some coherence traffic, but there is no
  exclusive handoff between readers)
- **Writers** hold the lock in Exclusive mode — same as mutex behavior

Scaling is sub-linear at high core counts — the reader-count atomic update
bounces a single cache line across all active cores, capping throughput once
coherence latency dominates the critical section.

### Kernel space

```c
// Before: mutex protecting read-heavy lookup
static DEFINE_MUTEX(unit_lock);

void get_unit(int n) {
    mutex_lock(&unit_lock);
    unit = search_cache(n);
    if (!unit) unit = search_tree(n);
    if (!unit) {
        unit = create_unit(n);
        insert_tree(n, unit);
    }
    mutex_unlock(&unit_lock);
}

// After: rwlock separating read and write paths
// Note: rwlock_t is spin-based and must NOT be held across sleeping operations.
// For sleepable critical sections, use rw_semaphore instead:
//   DECLARE_RWSEM(unit_lock); down_read/up_read; down_write/up_write
static DEFINE_RWLOCK(unit_lock);

void get_unit(int n) {
    read_lock(&unit_lock);
    unit = search_cache(n);
    if (!unit) unit = search_tree(n);
    read_unlock(&unit_lock);

    if (!unit) {
        write_lock(&unit_lock);
        // Double-check after upgrading to write lock
        unit = search_tree(n);
        if (!unit) {
            unit = create_unit(n);
            insert_tree(n, unit);
        }
        write_unlock(&unit_lock);
    }
}
```

### User space (pthreads)

```c
// Before
pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;

void lookup(int key) {
    pthread_mutex_lock(&lock);
    result = search(table, key);
    pthread_mutex_unlock(&lock);
}

// After
pthread_rwlock_t lock = PTHREAD_RWLOCK_INITIALIZER;

void lookup(int key) {
    pthread_rwlock_rdlock(&lock);
    result = search(table, key);
    pthread_rwlock_unlock(&lock);
}

void insert(int key, void *value) {
    pthread_rwlock_wrlock(&lock);
    add(table, key, value);
    pthread_rwlock_unlock(&lock);
}
```

### Double-check on the write path

When the read path discovers a write is needed (e.g., cache miss requiring
insertion), a double-check after acquiring the write lock is necessary:

```c
pthread_rwlock_rdlock(&lock);
unit = search(table, key);
pthread_rwlock_unlock(&lock);

if (!unit) {
    pthread_rwlock_wrlock(&lock);
    unit = search(table, key);  // double-check: another thread may have inserted
    if (!unit) {
        unit = create_and_insert(table, key);
    }
    pthread_rwlock_unlock(&lock);
}
```

The window between releasing the read lock and acquiring the write lock allows
another thread to perform the same insertion. Without double-check, duplicate
entries or corruption can result.

### When NOT to apply

- **Write-heavy workloads** (>50% writes): rwlock has higher per-operation
  overhead than mutex; writer acquisition must invalidate all Shared copies,
  which is more expensive than a single Modified→Modified transfer
- **Very short critical sections** (a few instructions): rwlock bookkeeping
  overhead may exceed the serialization cost of a plain mutex
- **Writer starvation concerns**: some rwlock implementations allow readers to
  starve writers indefinitely; if writes are time-sensitive, consider
  `PTHREAD_RWLOCK_PREFER_WRITER_NONRECURSIVE_NP`

---

## Verification

After applying the fix:

1. **Correctness** — run the existing test suite; read/write semantics must be
   preserved. Pay special attention to the double-check on the write path.
2. **Profiling** — rerun `perf report`; `osq_lock` / `mutex_lock` /
   `__mutex_lock_slowpath` should disappear or drop to negligible levels.
3. **IPC recovery** — `perf stat` should show IPC recovering to normal levels
   as core count grows (in one observed case, ~9× IPC improvement was measured).
4. **Kernel ratio** — if the mutex slow path was entering the kernel, kernel
   cycle ratio should drop significantly.
5. **Scaling test** — measure throughput at multiple core counts; the curve
   should now scale with readers rather than flattening or declining.

A reproducible micro-benchmark and collected profiling data are available in
`patterns/tests/` (`mutex-to-rwlock-bench.c` and `run-mutex-to-rwlock-bench.sh`).

---

## Presenting this to the user

1. Show the `perf report` output with `osq_lock` / `mutex_lock` highlighted and
   its cycle percentage.
2. Identify the mutex from the call chain and show the critical section source
   code.
3. Annotate which operations are reads vs. writes, and state the estimated
   read/write ratio.
4. Explain in one paragraph: "A mutex serializes all readers even though they
   don't conflict. With N cores, each reader waits for N-1 predecessors. An
   rwlock lets all readers proceed simultaneously — there is no exclusive
   handoff between readers, eliminating the O(N) serialization. Some coherence
   traffic remains for reader-count updates, but it is far less costly than
   the full exclusive transfer a mutex requires."
5. Present the before/after code with the separated read and write paths.
6. Note the expected improvement range: 1.8–9× depending on read/write ratio
   and core count (higher core counts see larger gains because the
   serialization penalty is O(N)).
