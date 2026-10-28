/* Ingest pipeline: the parser thread turns wire frames into jobs, the flusher thread
 * writes completed results back to the log. Excerpt. */
#include <stddef.h>
#include "pipeline.h"
#include "workq.h"

struct pipeline_stats g_pipe_stats;

void parse_loop(struct conn_set *cs, struct work_queue *q)
{
    struct pipeline_stats *st = &g_pipe_stats;
    struct frame f;

    while (next_frame(cs, &f)) {
        struct job *j = job_from_frame(&f);
        if (!j) {
            st->parse_errors++;
            continue;
        }
        st->parsed++;
        wq_push(q, j);
    }
}

void flush_batch(struct flusher *fl, struct result **res, size_t n)
{
    struct pipeline_stats *st = &g_pipe_stats;

    for (size_t i = 0; i < n; i++) {
        size_t len = log_append(fl->log, res[i]);
        st->flushed++;
        st->flush_bytes += len;
    }
}
