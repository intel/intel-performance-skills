#ifndef PKTSRV_STATS_H
#define PKTSRV_STATS_H

#include <stdatomic.h>
#include <stdint.h>

#define MAX_WORKERS 64

/* Per-worker packet counters, indexed by worker id.  Each worker only ever
 * writes its own entry; the monitor thread reads all of them once a second. */
struct worker_stats {
    uint64_t rx_packets;
    uint64_t rx_bytes;
};

/* Process-wide state shared by all threads. */
struct server_state {
    _Atomic uint64_t inflight;      /* packets currently being processed */
    _Atomic uint64_t total_conns;   /* bumped when a client connects (rare) */
    volatile int     shutting_down; /* set once by the signal handler */
    int              log_level;
};

extern struct worker_stats g_wstats[MAX_WORKERS];
extern struct server_state g_state;

#endif
