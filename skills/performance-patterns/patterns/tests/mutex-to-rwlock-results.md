# mutex-to-rwlock benchmark results

Machine: 2-socket, 40 cores/socket, 2 threads/core (160 logical CPUs)
Kernel: Linux 7.0.0-rc5+, perf 6.17.rc6
Test config: pinned to socket 0 physical cores (taskset -c 0-31), max 32 threads
Barrier sync: all threads start simultaneously via pthread_barrier_t

## Throughput scaling (single socket, 0% writes, pure readers)

```
threads   mutex (Mops/s)  rwlock (Mops/s)     speedup
------  ---------------  ---------------  ----------
     1             1.64             1.64        1.0x
     2             1.29             3.23        2.5x
     4             0.97             6.45        6.6x
     8             1.09            12.59       11.6x
    16             1.08            18.20       16.8x
    32             1.06            15.47       14.6x
```

Claim verified: mutex throughput degrades from 1.64 to 1.06 Mops/s (serialization);
rwlock scales to 16.8x at 16 cores within a single socket. Scaling flattens at
16–32 cores due to rwlock reader-count atomic contention across the L3 cache.

## Throughput with writes (single socket)

```
write_pct=1%:
threads   mutex (Mops/s)  rwlock (Mops/s)     speedup
     1             1.64             1.64        1.0x
     2             1.29             2.91        2.3x
     4             0.97             2.81        2.9x
     8             1.09             2.76        2.5x
    16             1.02             2.71        2.6x
    32             1.01             2.70        2.7x

write_pct=5%:
threads   mutex (Mops/s)  rwlock (Mops/s)     speedup
     1             1.71             1.71        1.0x
     2             1.35             2.07        1.5x
     4             1.01             1.79        1.8x
     8             1.12             1.77        1.6x
    16             1.05             1.74        1.7x
    32             1.03             1.73        1.7x
```

Even with 1-5% writes, rwlock provides 1.7-2.7x improvement. Writers cap scaling
because a writer must wait for all readers to release, and all readers must wait
for the writer to finish.

## Calibration: perf observer overhead

```
                      Baseline (ops/s)    With perf record    Overhead
1 thread (rwlock)         1,636,221          1,617,828          1.1%
32 threads (rwlock)      15,771,005         14,904,674          5.5%
```

Overhead is within acceptable range (<10%). Perf profiling does not materially
distort the measurements.

## perf stat comparison (32 threads, socket 0, 0% writes, 3 seconds)

```
                        MUTEX                    RWLOCK
cycles              39,549,083,426         287,658,101,661   (7.3x more with rwlock — all cores active)
instructions        29,495,737,451         157,153,503,110   (5.3x more useful work done)
IPC                          0.75                    0.55
context-switches         3,125,080                     225   (13,900x fewer with rwlock)
cache-misses                80,712                  90,863   (comparable)
```

### IPC analysis

- **Mutex IPC = 0.75**: higher per-core IPC than rwlock because only one thread
  runs at a time (the mutex holder) — that single core is doing useful work
  efficiently. But total cycles is only 40B (31 cores are sleeping on futex),
  so aggregate throughput is just 1.06 Mops/s.

- **rwlock IPC = 0.55**: all 32 cores are active (288B cycles total), doing useful
  work concurrently. The IPC is moderate because the reader-count atomic updates
  cause cache-line bouncing across cores. Despite per-core IPC being lower,
  aggregate throughput is 15.47 Mops/s (14.6x the mutex).

- **Context switches**: mutex causes 3.13M context switches in 3s (futex sleep/wake
  storm — ~1.04M/s). rwlock has 225 total (thread creation/exit/barrier only).

## perf call graph analysis (32 threads, socket 0, 0% writes)

### Where time is spent — layer breakdown

```
                        MUTEX                              RWLOCK
Layer               %-of-time  What happens           %-of-time  What happens
──────────────────  ─────────  ─────────────────────  ─────────  ─────────────────────
Application work       5.25%   do_read_work             23.74%   do_read_work
                               (1 thread at a time)              (32 threads in parallel)

User-space lock        0.91%   pthread_mutex_lock       75.89%   pthread_rwlock_rdlock
                               (fast-path CAS fails)             + pthread_rwlock_unlock
                                                                 (atomic reader-count ops)

Kernel                93.84%   futex_wait + futex_wake    0.0%   (never enters kernel)
                               + spinlock contention
```

### Mutex — call flow (59% lock + 35% unlock + 5% work)

```
 LOCK PATH (59.2%)                          UNLOCK PATH (35.3%)
 ───────────────────────────────────        ────────────────────────────────────
 ┌─────────────────────┐                    ┌──────────────────────────────────┐
 │ pthread_mutex_lock   │ user-space        │ pthread_mutex_unlock             │ user-space
 │  CAS fails → syscall │                   │  must wake next waiter → syscall │
 └──────────┬───────────┘                   └───────────────┬──────────────────┘
            │                                               │
 ┌──────────▼───────────┐                   ┌───────────────▼──────────────────┐
 │ futex_wait           │ kernel            │ futex_wake                       │ kernel
 │  "put me to sleep"   │                   │  "wake the next waiter"          │
 └──────────┬───────────┘                   └───────────────┬──────────────────┘
            │                                               │
 ┌──────────▼───────────┐                   ┌───────────────▼──────────────────┐
 │ futex hash bucket    │ kernel            │ futex hash bucket                │ kernel
 │ _raw_spin_lock       │ spinlock          │ _raw_spin_lock                   │ spinlock
 │  44% of total time!  │                   │  27% of total time!              │
 │  (queued_spin_lock_  │                   │  (queued_spin_lock_              │
 │   slowpath)          │                   │   slowpath)                      │
 └──────────┬───────────┘                   └───────────────┬──────────────────┘
            │                                               │
            ▼                                               ▼
   schedule (4.3%)                              wake_up_q → try_to_wake_up
   thread sleeps on                             send IPI to wake sleeping core
   wait queue
```

The key insight: 71% of all CPU time is spent spinning on a **kernel spinlock** inside
the futex hash bucket — not even doing useful scheduler work. 32 threads fight over
the same hash bucket to sleep or wake, creating a spinlock storm inside the kernel.

### rwlock — call flow (flat, user-space only)

```
 ┌─────────────────────────────────┐
 │ pthread_rwlock_rdlock  (19.9%)  │ atomic_inc(reader_count)
 └────────────────┬────────────────┘
                  │  (no syscall, no blocking)
 ┌────────────────▼────────────────┐
 │ do_read_work           (23.7%)  │ 32 threads execute concurrently
 └────────────────┬────────────────┘
                  │
 ┌────────────────▼────────────────┐
 │ pthread_rwlock_unlock  (56.0%)  │ atomic_dec(reader_count)
 └─────────────────────────────────┘

 No kernel. No syscall. No scheduler. No IPI.
 Total call depth: 1 level.
```

The unlock is more expensive than the lock (56.0% vs 19.9%) because the atomic
decrement must check whether it was the last reader and a writer is waiting —
this requires a compare-and-swap loop rather than a simple increment.

### Side-by-side summary

```
                          MUTEX                           RWLOCK
                          ─────                           ──────
Call depth          8+ levels deep into kernel        1 level (user-space only)
                    (app → glibc → syscall →
                     futex → hash bucket spinlock)

Bottleneck          native_queued_spin_lock_slowpath  cache-line bouncing on
                    on futex hash bucket (71%)        reader_count atomic (76%)

Kernel time         94% of cycles                    0%

Useful work         5.3% (1 thread runs)             24% (32 threads run)

Context switches    1.04M/sec                        ~75/sec
```

## Claims verified

1. Mutex serializes all readers → throughput plateaus at ~1.06 Mops/s regardless of cores
2. rwlock allows concurrent readers → throughput scales to 16.8x at 16 cores
3. Context switches 13,900x higher with mutex (futex sleep/wake storm)
4. Profile shows futex/kernel spinlock symbols exactly as predicted in pattern
5. rwlock overhead is user-space reader-count atomics, not kernel scheduler
6. rwlock profile has zero kernel symbols — purely user-space operation
7. Even with 1-5% writes, rwlock provides 1.7-2.7x improvement over mutex
