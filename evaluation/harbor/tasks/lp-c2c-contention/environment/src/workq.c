/* Work queue shared by the listener and all workers. Excerpt. */
#include "workq.h"

void wq_lock(struct work_queue *q)
{
    while (!__sync_bool_compare_and_swap(&q->lock, 0, 1))
        ;
}

void wq_unlock(struct work_queue *q)
{
    __atomic_store_n(&q->lock, 0, __ATOMIC_RELEASE);
}

struct job *wq_pop(struct work_queue *q)
{
    struct job *j = 0;
    wq_lock(q);
    if (q->head != q->tail)
        j = q->slots[q->head++ & q->mask];
    wq_unlock(q);
    return j;
}
