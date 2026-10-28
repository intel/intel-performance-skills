/* Worker thread body; ingestd starts --threads of these. */
#include "ingestd.h"

#define OUT_BATCH 8

void *worker_main(void *arg)
{
    struct worker *w = arg;
    struct record *rec;

    while (ring_pop(g_in, &rec)) {
        if (parse_record(rec, &w->parsed) != 0)
            continue;
        w->batch.recs[w->batch.count].id = w->parsed.id;
        w->batch.recs[w->batch.count].score = score_block(&w->parsed, w->model);
        if (++w->batch.count == OUT_BATCH)
            batch_commit(&w->batch);
        record_free(rec);
    }
    if (w->batch.count)
        batch_commit(&w->batch);
    return 0;
}
