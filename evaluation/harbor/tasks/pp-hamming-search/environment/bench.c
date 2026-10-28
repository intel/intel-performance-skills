#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include "hamming.h"

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

int main(void) {
    size_t code = 512, count = 512;     /* one 256 KiB shortlist of 4096-bit codes */
    unsigned char *db = malloc(code * count), *q = malloc(code);
    for (size_t i = 0; i < code * count; i++)
        db[i] = (unsigned char)(i * 2654435761u >> 24);
    for (size_t i = 0; i < code; i++)
        q[i] = (unsigned char)(i * 40503u >> 7);
    uint64_t d;
    size_t best = 0;
    int reps = 2000;
    double t = now();
    for (int i = 0; i < reps; i++)
        best = hamming_nearest(q + (i & 1), db, count, code, &d);
    t = now() - t;
    printf("nearest=%zu dist=%llu  %.2f GB/s\n", best, (unsigned long long)d,
           (double)code * count * reps / t / 1e9);
    free(db);
    free(q);
    return 0;
}
