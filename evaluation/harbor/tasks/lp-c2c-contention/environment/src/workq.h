#ifndef KVGATE_WORKQ_H
#define KVGATE_WORKQ_H
#include <stdint.h>

struct job;

/* The listener pushes jobs; all 24 workers pop. */
struct work_queue {
    volatile int lock;
    uint32_t     _pad;
    struct job **slots;               /* separate allocation */
    uint32_t     head __attribute__((aligned(128)));
    uint32_t     tail;
    uint32_t     mask;
};

void wq_lock(struct work_queue *q);
void wq_unlock(struct work_queue *q);

#endif
