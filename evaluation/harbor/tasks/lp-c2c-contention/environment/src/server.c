/* Request entry points, run on every worker thread. Excerpt. */
#include <stdatomic.h>
#include "server.h"

struct global_counters g_counters;

int admission_check(const struct worker *w)
{
    return w->inflight < g_counters.max_inflight;
}

void count_request(void)
{
    atomic_fetch_add_explicit(&g_counters.requests, 1, memory_order_relaxed);
}

int handle_request(struct worker *w, struct job *j)
{
    if (!admission_check(w))
        return reject_busy(j);
    count_request();
    return dispatch(w, j);
}
