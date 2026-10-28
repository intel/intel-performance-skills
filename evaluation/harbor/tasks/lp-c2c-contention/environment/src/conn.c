/* Connection objects are shared by the listener and the worker serving a request. Excerpt. */
#include <stdatomic.h>

struct conn {
    _Atomic int refcnt;
    int fd;
    /* ... */
};

void conn_get(struct conn *c)
{
    atomic_fetch_add_explicit(&c->refcnt, 1, memory_order_relaxed);
}

void conn_put(struct conn *c)
{
    if (atomic_fetch_sub_explicit(&c->refcnt, 1, memory_order_acq_rel) == 1)
        conn_free(c);
}
