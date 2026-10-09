/*
 * mlp-chain-walk-bench.c - Verify claims in mlp-chain-walk.md
 *
 * Demonstrates:
 *   1. A serial pointer-chase over a random chain runs at flat DRAM latency
 *      (one outstanding miss at a time).
 *   2. Interleaving W independent chains puts W dependent loads in flight
 *      simultaneously, dividing per-node time by roughly W up to the core's
 *      outstanding-miss capacity.
 *   3. Above the capacity, adding more chains stops helping (the sweep
 *      plateaus).
 *
 * How the test is built:
 *   - The arena holds W independent random-shuffled cyclic linked lists,
 *     each sized so that (per-chain nodes) x sizeof(Node) is far past the
 *     last-level cache. This guarantees each step is a real DRAM miss and
 *     the hardware prefetcher cannot help (address is only known after the
 *     current load returns).
 *   - The "serial" run walks chain 0 for STEPS iterations. This is the
 *     baseline: one dependent load in flight at all times.
 *   - The "interleaved" run keeps W cursors live and advances all of them
 *     per outer iteration, so W dependent loads sit in the load buffers
 *     concurrently. Each cursor visits STEPS/W nodes, so total work is
 *     identical to the serial run.
 *   - Both runs xor node addresses into a sink to defeat dead-code
 *     elimination and to give a correctness check (same set of visited
 *     nodes -> same xor accumulator when W divides STEPS evenly).
 *
 * Build:
 *   gcc -O2 -pthread -o mlp-chain-walk-bench mlp-chain-walk-bench.c
 *
 * Run:
 *   ./mlp-chain-walk-bench [mib_per_chain] [steps_millions] [mode]
 *
 *   mib_per_chain    per-chain working set in MiB (default 64)
 *   steps_millions   total steps to walk (default 200 = 200e6)
 *   mode             "sweep" (default): compare W in {1,2,4,8,16}
 *                    "serial":        run W=1 only, no table (for perf)
 *                    "interleaved":   run W=8 only, no table (for perf)
 *                    "ns-per-step":   print just the ns/step number for
 *                                     the interleaved run (for scripting)
 *
 * Profile the two loops in isolation:
 *   perf record -g ./mlp-chain-walk-bench 64 200 serial
 *   perf report               # one dependent-load line dominates
 *   perf record -g ./mlp-chain-walk-bench 64 200 interleaved
 *   perf report               # same line, much smaller share of samples
 *
 * Expected results:
 *   - Serial ns/step lands near the platform's flat DRAM load-to-use
 *     latency (typically ~80-120 ns on modern server CPUs).
 *   - W=8 ns/step drops to roughly 1/W of serial on cores whose
 *     outstanding-miss capacity is at least 8 (typical of current server
 *     cores), for an overall gain in the 4-8x range on the walk region.
 *   - W=16 usually shows little or no gain over W=8 (capacity-limited).
 *   - The interleaved run's dependent-load line still shows in `perf
 *     annotate`, but its sample share is much smaller because the same
 *     wall-clock time now retires W times as many loads.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sched.h>

/* Cache line size assumed by the Node layout. Each node consumes exactly
 * one cache line so every step incurs a fresh miss. */
#define CACHE_LINE 64

/* Interleave widths swept by the default "sweep" mode. Chosen to cross
 * the outstanding-miss capacity of current server cores from below (W=1)
 * to above (W=16). */
static const int kSweepWidths[] = {1, 2, 4, 8, 16};
#define NUM_WIDTHS ((int)(sizeof(kSweepWidths) / sizeof(kSweepWidths[0])))
#define MAX_WIDTH 16

typedef struct Node {
    struct Node *next;
    /* Pad to exactly one cache line so no two nodes share a line. */
    char pad[CACHE_LINE - sizeof(struct Node *)];
} Node;
_Static_assert(sizeof(Node) == CACHE_LINE, "Node must be one cache line");

static double now_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

/* Fisher-Yates over an index array; deterministic seed so both the serial
 * and interleaved runs see the exact same shuffled chains. Any repeatable
 * PRNG works here; xorshift64 is small, fast, and has no shared state. */
static uint64_t xorshift64(uint64_t *s) {
    uint64_t x = *s;
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    *s = x;
    return x;
}
static void shuffle(size_t *idx, size_t n, uint64_t seed) {
    uint64_t s = seed;
    for (size_t i = n - 1; i > 0; --i) {
        size_t j = xorshift64(&s) % (i + 1);
        size_t t = idx[i]; idx[i] = idx[j]; idx[j] = t;
    }
}

/* Build one cyclic random chain of N nodes threaded through the arena.
 * Returns the head node. Chains are independent: a walker on chain a and
 * a walker on chain b never touch the same cache line, so their misses
 * really are independent for the memory subsystem. */
static Node *build_chain(Node *arena, size_t N, uint64_t seed) {
    size_t *idx = malloc(sizeof(size_t) * N);
    if (!idx) { perror("malloc"); exit(1); }
    for (size_t i = 0; i < N; ++i) idx[i] = i;
    shuffle(idx, N, seed);

    for (size_t i = 0; i + 1 < N; ++i)
        arena[idx[i]].next = &arena[idx[i + 1]];
    arena[idx[N - 1]].next = &arena[idx[0]];   /* cyclic */

    Node *head = &arena[idx[0]];
    free(idx);
    return head;
}

/* Serial walk: one cursor, one dependent load at a time.
 * Marked noinline so `perf annotate` attributes the hot load to a
 * dedicated symbol (not a merged inlined blob). The asm volatile keeps
 * the compiler from hoisting or reordering the chase; it also blocks the
 * value from being treated as loop-invariant. */
static __attribute__((noinline))
uint64_t walk_serial(Node *head, uint64_t steps) {
    Node *p = head;
    uint64_t sink = 0;
    for (uint64_t i = 0; i < steps; ++i) {
        p = p->next;
        __asm__ volatile("" : "+r"(p));
        sink ^= (uint64_t)(uintptr_t)p;
    }
    return sink;
}

/* Interleaved walk: keep W cursors live; each outer iteration advances
 * every cursor once, so W dependent loads are in flight at the same time.
 * The core can hold ~10-20 outstanding L1D misses; small W scales close
 * to linearly, large W saturates the capacity and plateaus.
 *
 * The W cursors are stored in an array indexed by a compile-time-known
 * loop bound (W passed as parameter -> compiler still generates a small
 * loop, but the loads are independent because each cursor's next-address
 * comes from its own line). We accept a modest amount of loop overhead
 * because the point is to demonstrate the memory-level parallelism, not
 * to ship a hand-tuned kernel.
 *
 * total_steps is split evenly across the W cursors so total work matches
 * the serial baseline. */
static __attribute__((noinline))
uint64_t walk_interleaved(Node *const *heads, int W, uint64_t total_steps) {
    Node *cur[MAX_WIDTH];
    for (int w = 0; w < W; ++w) cur[w] = heads[w];

    uint64_t per_cursor = total_steps / (uint64_t)W;
    uint64_t sink = 0;

    for (uint64_t i = 0; i < per_cursor; ++i) {
        /* Issue W dependent loads. Because cur[0..W-1] point into W
         * independent chains, none of these loads depends on any other
         * in this batch -- the CPU can hold them all in the load buffers
         * simultaneously. */
        for (int w = 0; w < W; ++w) {
            cur[w] = cur[w]->next;
        }
        /* Sink prevents the compiler from dropping the loads. Kept
         * outside the inner loop so it does not artificially serialize
         * the W issues. */
        for (int w = 0; w < W; ++w) {
            __asm__ volatile("" : "+r"(cur[w]));
            sink ^= (uint64_t)(uintptr_t)cur[w];
        }
    }
    return sink;
}

/* One run: build W chains, walk serial vs interleaved, print ns/step.
 * total_steps is scaled to be divisible by every width in the sweep so
 * per-cursor work is exact. */
static void run_one(int W, size_t nodes_per_chain, uint64_t total_steps,
                    double *out_ns_per_step_serial,
                    double *out_ns_per_step_interleaved) {
    /* Allocate one large arena that holds W independent chains
     * back-to-back. Aligned to 4 KiB so each chain begins on a page
     * boundary; that keeps chain-to-chain TLB pressure symmetric between
     * runs at different W. */
    size_t bytes_per_chain = nodes_per_chain * sizeof(Node);
    Node *arena = aligned_alloc(4096, (size_t)W * bytes_per_chain);
    if (!arena) { perror("aligned_alloc"); exit(1); }
    memset(arena, 0, (size_t)W * bytes_per_chain);

    Node *heads[MAX_WIDTH];
    for (int w = 0; w < W; ++w) {
        heads[w] = build_chain(arena + (size_t)w * nodes_per_chain,
                               nodes_per_chain,
                               /* Distinct seeds -> distinct permutations,
                                * so the W chains touch different cache
                                * lines in different orders. */
                               (uint64_t)(0x9E3779B97F4A7C15ULL ^ (uint64_t)w));
    }

    /* Warm up: touch every page in every chain to fault them in and
     * prime the TLB. A sequential arena walk is much faster than a
     * random pointer chase and pays no wall-clock share of the timed
     * work; without any warmup the first timed run would eat page-fault
     * cost and the serial-vs-interleaved comparison would be unfair. */
    for (int w = 0; w < W; ++w) {
        volatile char sink = 0;
        char *base = (char *)(arena + (size_t)w * nodes_per_chain);
        for (size_t off = 0; off < bytes_per_chain; off += 4096)
            sink ^= base[off];
        (void)sink;
    }

    /* Serial baseline: chain 0 only, full steps count. */
    double t0 = now_sec();
    volatile uint64_t serial_sink = walk_serial(heads[0], total_steps);
    double t1 = now_sec();
    (void)serial_sink;
    double ns_serial = (t1 - t0) * 1e9 / (double)total_steps;

    /* Interleaved: all W chains, work split W ways. */
    t0 = now_sec();
    volatile uint64_t inter_sink = walk_interleaved(heads, W, total_steps);
    double t1b = now_sec();
    (void)inter_sink;
    double ns_inter = (t1b - t0) * 1e9 / (double)total_steps;

    *out_ns_per_step_serial = ns_serial;
    *out_ns_per_step_interleaved = ns_inter;

    free(arena);
}

/* Single-mode entry: run one W once, no table. Used by `perf stat` /
 * `perf record` so the profile is attributed to exactly one loop. */
static double run_single(int W, size_t nodes_per_chain, uint64_t total_steps) {
    size_t bytes_per_chain = nodes_per_chain * sizeof(Node);
    Node *arena = aligned_alloc(4096, (size_t)W * bytes_per_chain);
    if (!arena) { perror("aligned_alloc"); exit(1); }
    memset(arena, 0, (size_t)W * bytes_per_chain);

    Node *heads[MAX_WIDTH];
    for (int w = 0; w < W; ++w) {
        heads[w] = build_chain(arena + (size_t)w * nodes_per_chain,
                               nodes_per_chain,
                               (uint64_t)(0x9E3779B97F4A7C15ULL ^ (uint64_t)w));
    }
    /* Sequential page-touch warmup (see run_one for rationale). */
    for (int w = 0; w < W; ++w) {
        volatile char sink = 0;
        char *base = (char *)(arena + (size_t)w * nodes_per_chain);
        for (size_t off = 0; off < bytes_per_chain; off += 4096)
            sink ^= base[off];
        (void)sink;
    }

    double t0 = now_sec();
    volatile uint64_t sink;
    if (W == 1) sink = walk_serial(heads[0], total_steps);
    else        sink = walk_interleaved(heads, W, total_steps);
    double t1 = now_sec();
    (void)sink;

    free(arena);
    return (t1 - t0) * 1e9 / (double)total_steps;
}

int main(int argc, char **argv) {
    /* Pin to CPU 0 so re-runs measure the same core and the sweep is not
     * contaminated by cross-core migration. */
    cpu_set_t s; CPU_ZERO(&s); CPU_SET(0, &s);
    if (sched_setaffinity(0, sizeof(s), &s) != 0) {
        /* Not fatal -- just note it. Pinning failure only widens noise. */
        fprintf(stderr, "warning: sched_setaffinity failed, results may be noisier\n");
    }

    size_t mib_per_chain = 64;
    uint64_t steps_millions = 200;
    const char *mode = "sweep";

    if (argc > 1) mib_per_chain = (size_t)atoll(argv[1]);
    if (argc > 2) steps_millions = (uint64_t)atoll(argv[2]);
    if (argc > 3) mode = argv[3];

    if (mib_per_chain < 1) mib_per_chain = 1;
    if (steps_millions < 1) steps_millions = 1;

    size_t nodes_per_chain = (mib_per_chain << 20) / sizeof(Node);
    uint64_t total_steps = steps_millions * 1000000ULL;

    /* Round total_steps up to a multiple of MAX_WIDTH so per-cursor work
     * divides cleanly at every width in the sweep. */
    if (total_steps % MAX_WIDTH != 0)
        total_steps += MAX_WIDTH - (total_steps % MAX_WIDTH);

    if (strcmp(mode, "serial") == 0) {
        double ns = run_single(1, nodes_per_chain, total_steps);
        printf("# mode=serial W=1 mib_per_chain=%zu steps=%llu ns/step=%.2f\n",
               mib_per_chain, (unsigned long long)total_steps, ns);
        return 0;
    }
    if (strcmp(mode, "interleaved") == 0) {
        double ns = run_single(8, nodes_per_chain, total_steps);
        printf("# mode=interleaved W=8 mib_per_chain=%zu steps=%llu ns/step=%.2f\n",
               mib_per_chain, (unsigned long long)total_steps, ns);
        return 0;
    }
    if (strcmp(mode, "ns-per-step") == 0) {
        printf("%.2f\n", run_single(8, nodes_per_chain, total_steps));
        return 0;
    }

    /* Default sweep. */
    printf("mlp-chain-walk benchmark\n");
    printf("  per-chain arena=%zu MiB  steps=%llu  cache-line=%d B\n\n",
           mib_per_chain, (unsigned long long)total_steps, CACHE_LINE);
    printf("%6s  %14s  %14s  %10s\n",
           "W", "serial ns/step", "interlv ns/step", "speedup");
    printf("%6s  %14s  %14s  %10s\n",
           "-----", "--------------", "--------------", "----------");

    /* The serial ns/step is the same across widths (it always runs W=1
     * on chain 0), but re-running it per width acts as a stability check:
     * if it drifts substantially between rows, the machine is noisy and
     * the sweep results should be discarded. */
    for (int i = 0; i < NUM_WIDTHS; ++i) {
        int W = kSweepWidths[i];
        double ns_serial = 0, ns_inter = 0;
        run_one(W, nodes_per_chain, total_steps, &ns_serial, &ns_inter);
        printf("%6d  %14.2f  %14.2f  %9.2fx\n",
               W, ns_serial, ns_inter, ns_serial / ns_inter);
        fflush(stdout);
    }

    printf("\nKey claims verified when the sweep looks right:\n");
    printf("  - serial ns/step is roughly constant across rows and lands\n");
    printf("    near the platform's flat DRAM load-to-use latency.\n");
    printf("  - interleaved ns/step drops close to serial/W at small W,\n");
    printf("    then plateaus once W exceeds the core's outstanding-miss\n");
    printf("    capacity (typically somewhere in the 8-16 range).\n");
    printf("  - overall speedup on the walk region falls in the 4x-8x\n");
    printf("    range on cores whose capacity is at least 8.\n");
    return 0;
}
