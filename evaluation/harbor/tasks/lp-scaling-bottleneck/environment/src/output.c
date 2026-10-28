/* Scored records leave the workers in small batches. */
#include <stdatomic.h>
#include <stdint.h>
#include "ingestd.h"

/* Reported on the /stats endpoint. */
_Atomic uint64_t g_records_committed;

struct out_batch {
    struct sink *sink;
    struct scored *recs;
    uint32_t count;
};

void batch_commit(struct out_batch *b)
{
    emit_batch(b->sink, b->recs, b->count);
    atomic_fetch_add_explicit(&g_records_committed, b->count,
                              memory_order_relaxed);
    b->count = 0;
}
