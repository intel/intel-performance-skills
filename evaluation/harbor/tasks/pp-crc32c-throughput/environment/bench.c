#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include "crc32c.h"

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

int main(void) {
    size_t n = 256 << 10;               /* one 256 KiB block */
    unsigned char *b = malloc(n);
    for (size_t i = 0; i < n; i++)
        b[i] = (unsigned char)(i * 2654435761u >> 24);
    printf("check %08x (expect e3069283)\n", crc32c(0, "123456789", 9));
    uint32_t r = 0;
    int reps = 2000;
    double t = now();
    for (int i = 0; i < reps; i++)
        r = crc32c(r, b, n);
    t = now() - t;
    printf("crc=%08x  %.2f GB/s\n", r, (double)n * reps / t / 1e9);
    free(b);
    return 0;
}
