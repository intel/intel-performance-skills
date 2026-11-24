/* lru_cache.c - in-process LRU cache shared by all request threads.
 * Built with: gcc -O2 -g -march=native
 */
#include <immintrin.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define NBUCKETS 65536

struct entry {
    uint64_t      key;
    struct entry *hnext;               /* hash chain */
    struct entry *lru_prev, *lru_next; /* LRU list, most recent at head */
    char          value[64];
};

struct cache {
    volatile int  lock;
    uint32_t      nentries;
    uint64_t      hits;
    uint64_t      misses;
    struct entry *lru_head;
    struct entry *lru_tail;
    uint32_t      capacity;
    struct entry *buckets[NBUCKETS];
};

static inline void spin_lock(volatile int *l)
{
    while (!__sync_bool_compare_and_swap(l, 0, 1))
        _mm_pause();
}

static inline void spin_unlock(volatile int *l)
{
    __sync_lock_release(l);
}

static void lru_unlink(struct cache *c, struct entry *e)
{
    if (e->lru_prev) e->lru_prev->lru_next = e->lru_next;
    else             c->lru_head = e->lru_next;
    if (e->lru_next) e->lru_next->lru_prev = e->lru_prev;
    else             c->lru_tail = e->lru_prev;
}

static void lru_push_front(struct cache *c, struct entry *e)
{
    e->lru_prev = NULL;
    e->lru_next = c->lru_head;
    if (c->lru_head) c->lru_head->lru_prev = e;
    c->lru_head = e;
    if (!c->lru_tail) c->lru_tail = e;
}

/* Returns 1 and copies the value on a hit, 0 on a miss. */
int cache_lookup(struct cache *c, uint64_t key, char out[64])
{
    int found = 0;

    spin_lock(&c->lock);
    struct entry *e = c->buckets[key & (NBUCKETS - 1)];
    while (e && e->key != key)
        e = e->hnext;
    if (e) {
        c->hits++;
        lru_unlink(c, e);
        lru_push_front(c, e);
        memcpy(out, e->value, sizeof e->value);
        found = 1;
    } else {
        c->misses++;
    }
    spin_unlock(&c->lock);
    return found;
}

/* Called on a miss after the value has been fetched from the backing store
 * (roughly 3% of lookups). */
void cache_insert(struct cache *c, uint64_t key, const char value[64])
{
    struct entry *e;

    spin_lock(&c->lock);
    if (c->nentries == c->capacity) {
        e = c->lru_tail;                      /* evict least recently used */
        lru_unlink(c, e);
        struct entry **pp = &c->buckets[e->key & (NBUCKETS - 1)];
        while (*pp != e)
            pp = &(*pp)->hnext;
        *pp = e->hnext;
    } else {
        e = malloc(sizeof *e);
        c->nentries++;
    }
    e->key = key;
    memcpy(e->value, value, sizeof e->value);
    e->hnext = c->buckets[key & (NBUCKETS - 1)];
    c->buckets[key & (NBUCKETS - 1)] = e;
    lru_push_front(c, e);
    spin_unlock(&c->lock);
}
