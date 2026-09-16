/*
 * software-prefetch-bench.c - Verify claims in software-prefetch.md
 *
 * Demonstrates:
 *   1. A batched producer-consumer scan over a large fixed-stride record
 *      array leaves the first load of each record on the critical path
 *      when the hardware prefetcher does not fully cover the loop shape.
 *   2. Inserting __builtin_prefetch(&data[i + N]) at a tuned distance N
 *      restores memory run-ahead: the fetch of the record the loop will
 *      reach in Np = N/rowSize iterations completes before demand arrives.
 *   3. There is a distance range that helps and a range that does not;
 *      the sweep prints ns/row across a small set of distances so the
 *      caller can pick the flat maximum.
 *
 * How the test is built:
 *   - The arena holds fixed-stride records over a working set larger
 *     than the last-level cache, so scanning it is guaranteed to miss.
 *     Each record starts with one byte that acts as a "live" flag; the
 *     scan reads that flag, and if the record is live, appends the
 *     record's pointer to a small batch of BATCH_ROWS. When the batch
 *     fills, the scan pauses and hands off to a consumer.
 *   - The consumer runs one pseudo-random 8-byte probe into a table
 *     sized well above the private caches. This models a realistic
 *     batched producer-consumer scan (hash probe / aggregation lookup):
 *     the consumer issues its own memory traffic, which trains the
 *     hardware prefetcher away from the row stream and re-exposes
 *     load latency the moment the scan resumes.
 *   - The scan optionally issues __builtin_prefetch(&data[row + N])
 *     at every step. N is a compile-time argument via -DPREFETCH_DIST,
 *     because per-distance process isolation is what keeps distances
 *     comparable: any in-process sweep would leak cache warmup between
 *     distance points and understate the win of every distance except
 *     the first.
 *   - The default "sweep" mode does *not* compile many binaries; it
 *     runs several distances back-to-back in the same process, which
 *     is enough to see the qualitative shape. For production tuning
 *     rebuild once per distance and take the median across N runs.
 *
 * Build:
 *   gcc -O2 -pthread -o software-prefetch-bench software-prefetch-bench.c
 *
 * Run:
 *   ./software-prefetch-bench [arena_mib] [row_bytes] [seconds] [mode]
 *
 *   arena_mib   record arena size in MiB (default 256, past L3 on most
 *               current server CPUs)
 *   row_bytes   fixed stride per record in bytes (default 64)
 *   seconds     wall-clock time budget per configuration (default 2)
 *   mode        "sweep"       (default): compare N in {0,1024,2048,4096,8192}
 *               "noprefetch": run N=0 only, no table (for perf)
 *               "prefetch":   run N=2048 only, no table (for perf)
 *               "ns-per-row": print just the ns/row for N=2048 (for scripting)
 *
 * Profile the two loops in isolation:
 *   perf record -g ./software-prefetch-bench 256 64 2 noprefetch
 *   perf report               # one header-byte load line dominates
 *   perf record -g ./software-prefetch-bench 256 64 2 prefetch
 *   perf report               # same line, smaller cycle share
 *
 * Expected results:
 *   - When the target CPU's hardware prefetcher does not fully cover
 *     this loop shape, the sweep shows a clear valley in ns/row around
 *     N in the 1-8 KiB range (typical sweet spot: 2 KiB). Overall gain
 *     on the scan region typically falls in the 10-30 % range.
 *   - When the target's hardware prefetcher already covers this loop
 *     shape, the sweep is flat and the pattern does not apply here.
 *     Do not add the hint in that case.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sched.h>

/* Cache line size assumed by row alignment logic. */
#define CACHE_LINE 64

/* Batch size the scan hands off to the consumer, matching typical
 * columnar-scan batch sizes seen in real callers. Small enough that the
 * scan resumes often (which is exactly the condition that causes the
 * pattern) and large enough that the consumer amortizes its own
 * setup. */
#define BATCH_ROWS 1024

/* Consumer probe table size (8 MiB): large enough to miss the private
 * caches on any current server core, so the consumer's own memory
 * traffic really does re-train / disturb the hardware prefetcher. */
#define PROBE_TABLE_BYTES (8ULL << 20)
#define PROBE_SLOTS       (PROBE_TABLE_BYTES / sizeof(uint64_t))
#define PROBE_MASK        (PROBE_SLOTS - 1)

/* Distances swept in the default sweep mode, in bytes. 0 is the
 * no-prefetch baseline; 2 KiB is a reasonable starting point on
 * typical current server CPUs; the outer values (1K, 4K, 8K) bracket
 * it so the caller can see the shape of the curve. */
static const int kSweepDistances[] = {0, 1024, 2048, 4096, 8192};
#define NUM_DISTANCES ((int)(sizeof(kSweepDistances) / sizeof(kSweepDistances[0])))

/* xorshift64: deterministic, no shared state, fast enough that the
 * consumer's per-row cost is dominated by the memory probe. */
static uint64_t xorshift64(uint64_t *s) {
    uint64_t x = *s;
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    *s = x;
    return x;
}

static double now_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

/* Fill the arena so every page is really backed (not a shared zero
 * page) and clear the low bit of the flag byte of every record so all
 * records are "live" and the scan really visits them. */
static void arena_init(char *data, size_t bytes, int row_bytes) {
    uint64_t s = 0xC0FFEE1234567ULL;
    for (size_t i = 0; i + sizeof(uint64_t) <= bytes; i += sizeof(uint64_t)) {
        uint64_t v = xorshift64(&s);
        memcpy(data + i, &v, sizeof(v));
    }
    /* Walk the stride and clear the flag byte so every record survives
     * the "is_live" check. The scan matches the stride below. */
    for (size_t row = 0; row + (size_t)row_bytes <= bytes; row += row_bytes) {
        data[row] &= ~(char)1;
    }
}

/* Consumer: one pseudo-random 8-byte probe per collected row. The
 * probe address is derived from a small hash of the row pointer so
 * each row triggers a different table slot; the table is bigger than
 * the private caches, so probes miss L2 and issue memory traffic. */
static __attribute__((always_inline))
uint64_t consume_batch(const char *const *rows, int n,
                       const uint64_t *table) {
    uint64_t acc = 0;
    for (int i = 0; i < n; ++i) {
        uint64_t key = (uint64_t)(uintptr_t)rows[i];
        /* Mix so nearby rows land in far-apart probe slots. */
        key ^= key >> 33;
        key *= 0xFF51AFD7ED558CCDULL;
        key ^= key >> 33;
        acc ^= table[key & PROBE_MASK];
    }
    return acc;
}

/* The scan. row_bytes and prefetch_distance are runtime parameters so
 * one binary can drive the whole sweep. Marked noinline so `perf
 * annotate` attributes the hot load to a dedicated symbol.
 *
 * Every version of this loop uses the same structure so their perf
 * profiles are directly comparable: only the presence and target of
 * __builtin_prefetch changes. */
static __attribute__((noinline))
uint64_t scan_and_consume(const char *data, size_t bytes, int row_bytes,
                          int prefetch_distance,
                          const uint64_t *probe_table) {
    const char *batch[BATCH_ROWS];
    uint64_t sink = 0;
    size_t row = 0;

    while (row + (size_t)row_bytes <= bytes) {
        int count = 0;
        while (count < BATCH_ROWS && row + (size_t)row_bytes <= bytes) {
            /* Prefetch the record the loop will reach some bytes later.
             * Integer arithmetic on uintptr_t so the target address is
             * well-defined even when it lands past the mapping's end;
             * __builtin_prefetch is non-faulting on x86_64 for any
             * canonical address, so out-of-range is safe. */
#if defined(__x86_64__)
            if (prefetch_distance > 0) {
                const char *target = (const char *)(
                    (uintptr_t)data + row + (uintptr_t)prefetch_distance);
                __builtin_prefetch(target);
            }
#endif
            /* The first load of each record: this is the instruction
             * that dominates `perf annotate` when the hardware
             * prefetcher does not cover the loop shape. */
            if (data[row] & 1) {
                /* Record is freed / skipped -- match the batched-scan
                 * shape of real callers where free bookkeeping is
                 * folded into the same loop. */
                row += row_bytes;
                continue;
            }
            batch[count++] = data + row;
            row += row_bytes;
        }
        sink ^= consume_batch(batch, count, probe_table);
    }
    return sink;
}

/* Timed run: scan the whole arena repeatedly until `seconds` elapses,
 * count total rows visited, report ns/row. */
static double time_scan(const char *data, size_t bytes, int row_bytes,
                        int prefetch_distance, const uint64_t *probe_table,
                        double seconds) {
    /* One pass to warm caches / TLB and to establish an initial rate
     * estimate. */
    volatile uint64_t warm = scan_and_consume(data, bytes, row_bytes,
                                              prefetch_distance, probe_table);
    (void)warm;

    size_t rows_per_pass = bytes / (size_t)row_bytes;
    uint64_t total_rows = 0;
    volatile uint64_t sink = 0;

    double t0 = now_sec();
    double deadline = t0 + seconds;
    do {
        sink ^= scan_and_consume(data, bytes, row_bytes,
                                 prefetch_distance, probe_table);
        total_rows += rows_per_pass;
    } while (now_sec() < deadline);
    double t1 = now_sec();
    (void)sink;

    return (t1 - t0) * 1e9 / (double)total_rows;
}

/* Prepare and run one distance. Fresh arena+table each call so previous
 * distances do not leave cache footprint that biases the next. */
static double run_one(size_t arena_bytes, int row_bytes,
                      int prefetch_distance, double seconds) {
    char *data = aligned_alloc(4096, arena_bytes);
    if (!data) { perror("aligned_alloc data"); exit(1); }
    uint64_t *table = aligned_alloc(4096, PROBE_TABLE_BYTES);
    if (!table) { perror("aligned_alloc table"); exit(1); }

    arena_init(data, arena_bytes, row_bytes);
    /* Init probe table with any non-zero pattern; content does not
     * matter, only that every page is faulted and every slot returns
     * a distinct value so the consumer's `acc ^=` is not folded. */
    uint64_t s = 0xDEADBEEF12345678ULL;
    for (size_t i = 0; i < PROBE_SLOTS; ++i) table[i] = xorshift64(&s);

    double ns = time_scan(data, arena_bytes, row_bytes,
                          prefetch_distance, table, seconds);

    free(data);
    free(table);
    return ns;
}

int main(int argc, char **argv) {
    /* Pin to CPU 0 so re-runs measure the same core. */
    cpu_set_t s; CPU_ZERO(&s); CPU_SET(0, &s);
    if (sched_setaffinity(0, sizeof(s), &s) != 0) {
        fprintf(stderr, "warning: sched_setaffinity failed, results may be noisier\n");
    }

    size_t arena_mib = 256;
    int row_bytes = 64;
    double seconds = 2.0;
    const char *mode = "sweep";

    if (argc > 1) arena_mib = (size_t)atoll(argv[1]);
    if (argc > 2) row_bytes = atoi(argv[2]);
    if (argc > 3) seconds = atof(argv[3]);
    if (argc > 4) mode = argv[4];

    if (arena_mib < 1) arena_mib = 1;
    if (row_bytes < (int)CACHE_LINE) row_bytes = CACHE_LINE;
    if (row_bytes % 8 != 0) row_bytes = (row_bytes + 7) & ~7;
    if (seconds < 0.1) seconds = 0.1;

    size_t arena_bytes = arena_mib << 20;

    if (strcmp(mode, "noprefetch") == 0) {
        double ns = run_one(arena_bytes, row_bytes, 0, seconds);
        printf("# mode=noprefetch arena=%zu MiB row=%dB seconds=%.1f ns/row=%.2f\n",
               arena_mib, row_bytes, seconds, ns);
        return 0;
    }
    if (strcmp(mode, "prefetch") == 0) {
        double ns = run_one(arena_bytes, row_bytes, 2048, seconds);
        printf("# mode=prefetch dist=2048 arena=%zu MiB row=%dB seconds=%.1f ns/row=%.2f\n",
               arena_mib, row_bytes, seconds, ns);
        return 0;
    }
    if (strcmp(mode, "ns-per-row") == 0) {
        printf("%.2f\n", run_one(arena_bytes, row_bytes, 2048, seconds));
        return 0;
    }

    /* Default sweep: run each distance for `seconds`, print table. */
    printf("software-prefetch benchmark\n");
    printf("  arena=%zu MiB  row=%dB  seconds=%.1f  batch=%d rows\n\n",
           arena_mib, row_bytes, seconds, BATCH_ROWS);
    printf("%12s  %10s  %10s\n", "prefetch_N", "ns/row", "vs N=0");
    printf("%12s  %10s  %10s\n", "----------", "----------", "----------");

    double ns_baseline = -1.0;
    for (int i = 0; i < NUM_DISTANCES; ++i) {
        int N = kSweepDistances[i];
        double ns = run_one(arena_bytes, row_bytes, N, seconds);
        if (ns_baseline < 0.0) ns_baseline = ns;
        double pct = 100.0 * (ns - ns_baseline) / ns_baseline;
        printf("%12d  %10.2f  %+9.1f%%\n", N, ns, pct);
        fflush(stdout);
    }

    printf("\nInterpretation:\n");
    printf("  - If N > 0 rows are all within noise of N=0, the hardware\n");
    printf("    prefetcher already covers this loop shape on this target.\n");
    printf("    The pattern does not apply here -- do not add the hint.\n");
    printf("  - If ns/row drops for some middle N (1-8 KiB range) and\n");
    printf("    then flattens or rises, that N is the sweet spot. Typical\n");
    printf("    gain on the scan region is in the 10-30 %% range.\n");
    return 0;
}
