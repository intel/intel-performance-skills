#ifndef KVGATE_SERVER_H
#define KVGATE_SERVER_H
#include <stdatomic.h>
#include <stdint.h>

struct global_counters {
    _Atomic uint64_t requests;  /* every request, all workers; exported on /metrics */
    uint64_t max_inflight;      /* set from config at startup, read-only afterwards */
};

extern struct global_counters g_counters;

#endif
