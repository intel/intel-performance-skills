#ifndef KVGATE_WORKER_H
#define KVGATE_WORKER_H
#include <stdint.h>

/* Per-worker progress, read by the watchdog. */
struct worker_slot {
    uint64_t done;
    uint64_t last_ns;
};

extern struct worker_slot g_slots[24];

#endif
