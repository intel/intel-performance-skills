/* Bounded multi-producer / multi-consumer ring shared by the reader and all workers. */
#include <stdint.h>
#include "ingestd.h"

struct ring {
    volatile int lock;
    uint32_t head;
    uint32_t tail;
    uint32_t mask;
    struct record **slots;
};

void ring_lock_acquire(struct ring *r)
{
    while (!__sync_bool_compare_and_swap(&r->lock, 0, 1))
        ;
}

void ring_lock_release(struct ring *r)
{
    __atomic_store_n(&r->lock, 0, __ATOMIC_RELEASE);
}

int ring_push(struct ring *r, struct record *rec)
{
    int ok = 0;
    ring_lock_acquire(r);
    if (r->tail - r->head <= r->mask) {
        r->slots[r->tail++ & r->mask] = rec;
        ok = 1;
    }
    ring_lock_release(r);
    return ok;
}

int ring_pop(struct ring *r, struct record **out)
{
    int ok = 0;
    ring_lock_acquire(r);
    if (r->head != r->tail) {
        *out = r->slots[r->head++ & r->mask];
        ok = 1;
    }
    ring_lock_release(r);
    return ok;
}
