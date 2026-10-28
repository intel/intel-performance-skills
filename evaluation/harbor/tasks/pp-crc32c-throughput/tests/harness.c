/* Throughput of crc32c() on a 256 KiB L2-resident block: compute-bound, so the
 * number measures the implementation rather than DRAM. Prints GB/s. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <time.h>
uint32_t crc32c(uint32_t crc, const void *buf, size_t len);
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }
int main(void) {
    size_t n = 256 << 10;
    unsigned char *b = aligned_alloc(64, n);
    for (size_t i = 0; i < n; i++) b[i] = (unsigned char)(i * 2654435761u >> 24);
    volatile uint32_t sink = 0;
    double best = 1e9;
    for (int k = 0; k < 7; k++) {
        uint32_t r = 0;
        double t = now();
        for (int j = 0; j < 400; j++) r = crc32c(r, b, n);
        t = (now() - t) / 400;
        sink ^= r;
        if (t < best) best = t;
    }
    printf("%.3f\n", n / best / 1e9);
    return 0;
}
