/* Worker threads for solver: each worker scores a slice of the candidate blocks. */
#include <stdint.h>
#include <stddef.h>
#include "solver.h"

struct worker_stats {
    uint64_t processed;
    uint64_t last_batch_ns;
};

/* One entry per worker, indexed by worker id; read by the progress reporter. */
struct worker_stats g_stats[MAX_WORKERS];

static void score_block(struct worker *w, const struct block *blk)
{
    for (size_t c = 0; c < blk->ncand; c++) {
        double s = accumulate_scores(blk->cand[c], w->ref, blk->nfeat);
        w->best = s > w->best ? s : w->best;
        worker_update_stats(g_stats, w->id, now_ns());
    }
}

void *worker_main(void *arg)
{
    struct worker *w = arg;
    const struct block *blk;
    while ((blk = next_block(w)))
        score_block(w, blk);
    return 0;
}

void worker_update_stats(struct worker_stats *stats, int id, uint64_t now)
{
    stats[id].processed++;
    stats[id].last_batch_ns = now;
}
