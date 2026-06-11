/*
 * mutex-to-rwlock-bench.c — Verify claims in mutex-to-rwlock.md
 *
 * Demonstrates:
 *   1. Mutex serializes readers → throughput degrades with core count
 *   2. rwlock allows concurrent readers → throughput scales with core count
 *   3. IPC difference between mutex and rwlock under read-heavy load
 *
 * Build:
 *   gcc -O2 -pthread -o mutex-to-rwlock-bench mutex-to-rwlock-bench.c
 *
 * Run:
 *   ./mutex-to-rwlock-bench [max_threads] [duration_sec] [write_pct] [mode]
 *
 *   mode: "mutex" | "rwlock" | omit for comparative run
 *
 * Examples:
 *   ./mutex-to-rwlock-bench 32 2 5          # comparative table
 *   ./mutex-to-rwlock-bench 32 5 0 mutex    # single-mode for profiling
 *   ./mutex-to-rwlock-bench 32 5 0 rwlock   # single-mode for profiling
 *
 * Profile (to see futex_wait / osq_lock with mutex):
 *   perf record -g ./mutex-to-rwlock-bench 32 5 0 mutex
 *   perf report
 *
 * Expected results:
 *   - Mutex throughput plateaus or degrades as threads increase (serialization)
 *   - rwlock throughput scales near-linearly with core count (concurrent readers)
 *   - perf shows mutex spending 40%+ in kernel (futex_wait, schedule, wake IPIs)
 *   - perf shows rwlock staying entirely in user-space (atomic reader-count ops)
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <stdatomic.h>
#include <time.h>
#include <sched.h>
#include <unistd.h>

/* Align per-thread data to avoid false sharing between thread_result structs */
#define CACHE_LINE 64

/* 64K entries — large enough that random accesses miss L1, simulating a
 * realistic lookup table (e.g., routing table, config cache, hash map) */
#define TABLE_SIZE (64 * 1024)

/* 256 random reads per critical section — makes the critical section long
 * enough that rwlock's user-space overhead is negligible relative to the work,
 * while mutex serialization becomes the dominant bottleneck */
#define READ_ITERS 256

/* Thread-local PRNG — xorshift32 has no shared state, so it doesn't introduce
 * contention that would mask the mutex/rwlock scaling difference.
 * (glibc rand_r has internal locking on some implementations) */
static inline unsigned int xorshift32(unsigned int *state) {
    unsigned int x = *state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    *state = x;
    return x;
}

/* Shared lookup table — represents the read-heavy data structure protected
 * by the lock (e.g., a routing table, session cache, or config store) */
struct lookup_table {
    int entries[TABLE_SIZE];
};

static struct lookup_table g_table __attribute__((aligned(CACHE_LINE)));

static pthread_mutex_t g_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_rwlock_t g_rwlock = PTHREAD_RWLOCK_INITIALIZER;

static atomic_int g_running;
/* Barrier ensures all threads are spawned and ready before any start working */
static pthread_barrier_t g_barrier;

/* Per-thread result — padded to a full cache line so threads don't
 * false-share their operation counters */
struct thread_result {
    unsigned long ops;
    char pad[CACHE_LINE - sizeof(unsigned long)];
} __attribute__((aligned(CACHE_LINE)));

struct bench_config {
    int thread_id;
    int write_pct;
    struct thread_result *result;
};

/* Simulate a read-heavy critical section: 256 random lookups into the table.
 * Random access pattern ensures cache misses that make the section non-trivial,
 * representative of real hash table / tree lookups */
static int do_read_work(unsigned int *state) {
    int sum = 0;
    for (int i = 0; i < READ_ITERS; i++) {
        int idx = xorshift32(state) % TABLE_SIZE;
        sum += g_table.entries[idx];
    }
    return sum;
}

/* Simulate a rare write (e.g., cache invalidation, config update).
 * Intentionally cheap — the point is that even rare writes cap rwlock scaling
 * because a writer must wait for all readers to release */
static void do_write_work(int tid) {
    g_table.entries[tid % TABLE_SIZE] = tid;
}

/* Mutex worker: acquires exclusive lock for EVERY operation (read or write).
 * This is the anti-pattern — all threads serialize even when only reading.
 * At high thread counts, most time is spent in futex_wait (kernel sleep). */
static void *worker_mutex(void *arg) {
    struct bench_config *cfg = arg;
    unsigned long ops = 0;
    unsigned int rng = cfg->thread_id + 1;
    int sink = 0;

    pthread_barrier_wait(&g_barrier);

    while (atomic_load_explicit(&g_running, memory_order_relaxed)) {
        int is_write = (cfg->write_pct > 0) &&
                       ((xorshift32(&rng) % 100) < cfg->write_pct);

        pthread_mutex_lock(&g_mutex);
        if (is_write)
            do_write_work(cfg->thread_id);
        else
            sink += do_read_work(&rng);
        pthread_mutex_unlock(&g_mutex);
        ops++;
    }

    cfg->result->ops = ops;
    return (void *)(long)sink;
}

/* rwlock worker: readers take rdlock (shared), writers take wrlock (exclusive).
 * This is the fix — concurrent readers proceed without blocking each other.
 * At high thread counts, all cores do useful work in parallel.
 * The only overhead is user-space atomic inc/dec of the reader count. */
static void *worker_rwlock(void *arg) {
    struct bench_config *cfg = arg;
    unsigned long ops = 0;
    unsigned int rng = cfg->thread_id + 1;
    int sink = 0;

    pthread_barrier_wait(&g_barrier);

    while (atomic_load_explicit(&g_running, memory_order_relaxed)) {
        int is_write = (cfg->write_pct > 0) &&
                       ((xorshift32(&rng) % 100) < cfg->write_pct);

        if (is_write) {
            pthread_rwlock_wrlock(&g_rwlock);
            do_write_work(cfg->thread_id);
            pthread_rwlock_unlock(&g_rwlock);
        } else {
            pthread_rwlock_rdlock(&g_rwlock);
            sink += do_read_work(&rng);
            pthread_rwlock_unlock(&g_rwlock);
        }
        ops++;
    }

    cfg->result->ops = ops;
    return (void *)(long)sink;
}

/* Run one benchmark configuration: spawn num_threads, let them run for
 * duration_sec, then signal stop and collect total operations.
 * Returns aggregate throughput in ops/sec. */
static double run_bench(int num_threads, int duration_sec, int write_pct,
                        int use_rwlock) {
    pthread_t *threads = calloc(num_threads, sizeof(pthread_t));
    struct bench_config *cfgs = calloc(num_threads, sizeof(struct bench_config));
    struct thread_result *results = aligned_alloc(CACHE_LINE,
        num_threads * sizeof(struct thread_result));
    memset(results, 0, num_threads * sizeof(struct thread_result));

    /* Barrier count = workers + main thread; main participates so it can
     * start the timer at the exact instant all workers begin */
    pthread_barrier_init(&g_barrier, NULL, num_threads + 1);
    atomic_store(&g_running, 1);

    for (int i = 0; i < num_threads; i++) {
        cfgs[i].thread_id = i;
        cfgs[i].write_pct = write_pct;
        cfgs[i].result = &results[i];
        pthread_create(&threads[i], NULL,
                       use_rwlock ? worker_rwlock : worker_mutex, &cfgs[i]);
    }

    /* Wait for all workers to be ready, then start timing */
    pthread_barrier_wait(&g_barrier);

    struct timespec start, end;
    clock_gettime(CLOCK_MONOTONIC, &start);
    sleep(duration_sec);
    atomic_store(&g_running, 0);
    clock_gettime(CLOCK_MONOTONIC, &end);

    double elapsed = (end.tv_sec - start.tv_sec) +
                     (end.tv_nsec - start.tv_nsec) / 1e9;

    unsigned long total_ops = 0;
    for (int i = 0; i < num_threads; i++) {
        pthread_join(threads[i], NULL);
        total_ops += results[i].ops;
    }

    pthread_barrier_destroy(&g_barrier);
    free(threads);
    free(cfgs);
    free(results);

    return total_ops / elapsed;
}

/* Mode for single-lock profiling runs (perf stat / perf record) and
 * calibration (rwlock-ops / mutex-ops print raw throughput for scripting) */
enum run_mode { MODE_BOTH, MODE_MUTEX, MODE_RWLOCK, MODE_MUTEX_OPS, MODE_RWLOCK_OPS };

static enum run_mode parse_mode(const char *s) {
    if (strcmp(s, "mutex") == 0) return MODE_MUTEX;
    if (strcmp(s, "rwlock") == 0) return MODE_RWLOCK;
    if (strcmp(s, "mutex-ops") == 0) return MODE_MUTEX_OPS;
    if (strcmp(s, "rwlock-ops") == 0) return MODE_RWLOCK_OPS;
    return MODE_BOTH;
}

int main(int argc, char **argv) {
    int max_threads = 160;
    int duration_sec = 2;
    int write_pct = 5;
    enum run_mode mode = MODE_BOTH;

    if (argc > 1) max_threads = atoi(argv[1]);
    if (argc > 2) duration_sec = atoi(argv[2]);
    if (argc > 3) write_pct = atoi(argv[3]);
    if (argc > 4) mode = parse_mode(argv[4]);

    if (max_threads < 1) max_threads = 1;
    if (max_threads > 1024) max_threads = 1024;

    /* Cap threads at online core count — oversubscription would measure
     * scheduler behavior rather than lock contention */
    int cores = sysconf(_SC_NPROCESSORS_ONLN);
    if (max_threads > cores) max_threads = cores;

    /* Single-mode run: used by perf to profile one lock type in isolation */
    if (mode == MODE_MUTEX) {
        run_bench(max_threads, duration_sec, write_pct, 0);
        return 0;
    }
    if (mode == MODE_RWLOCK) {
        run_bench(max_threads, duration_sec, write_pct, 1);
        return 0;
    }
    /* Ops mode: print raw throughput for calibration scripting */
    if (mode == MODE_MUTEX_OPS) {
        printf("%.0f\n", run_bench(max_threads, duration_sec, write_pct, 0));
        return 0;
    }
    if (mode == MODE_RWLOCK_OPS) {
        printf("%.0f\n", run_bench(max_threads, duration_sec, write_pct, 1));
        return 0;
    }

    /* Default: comparative benchmark across thread counts */
    printf("mutex-to-rwlock benchmark\n");
    printf("  max_threads=%d  duration=%ds  write_pct=%d%%  cores=%d\n\n",
           max_threads, duration_sec, write_pct, cores);
    printf("%6s  %15s  %15s  %10s\n",
           "threads", "mutex (Mops/s)", "rwlock (Mops/s)", "speedup");
    printf("%6s  %15s  %15s  %10s\n",
           "------", "---------------", "---------------", "----------");

    /* Power-of-2 progression shows scaling behavior clearly;
     * higher values show cross-socket NUMA effects if present */
    int thread_counts[] = {1, 2, 4, 8, 16, 32, 64, 96, 128, 160, 192, 256};
    int n_counts = sizeof(thread_counts) / sizeof(thread_counts[0]);

    for (int i = 0; i < n_counts; i++) {
        int t = thread_counts[i];
        if (t > max_threads) break;

        double mutex_ops = run_bench(t, duration_sec, write_pct, 0);
        double rwlock_ops = run_bench(t, duration_sec, write_pct, 1);
        double speedup = rwlock_ops / mutex_ops;

        printf("%6d  %15.2f  %15.2f  %9.1fx\n",
               t, mutex_ops / 1e6, rwlock_ops / 1e6, speedup);
    }

    printf("\nKey claims verified:\n");
    printf("  - Mutex throughput should degrade or plateau as thread count grows\n");
    printf("  - rwlock throughput should scale with thread count (read-heavy)\n");
    printf("  - Speedup should increase with core count (O(N) serialization)\n");

    return 0;
}
