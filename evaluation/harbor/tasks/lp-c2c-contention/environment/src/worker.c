/* Worker main loop. Excerpt. */
#include "worker.h"
#include "workq.h"

struct worker_slot g_slots[24];

void worker_progress(int id, uint64_t now_ns)
{
    g_slots[id].done++;
    g_slots[id].last_ns = now_ns;
}

void *worker_main(void *arg)
{
    struct worker *w = arg;
    struct job *j;

    while ((j = wq_pop(w->q))) {
        handle_request(w, j);
        worker_progress(w->id, now_ns());
    }
    return 0;
}
