/* Throughput of hamming_distance() re-ranking a 256 KiB shortlist of 512-byte codes
 * (L2-resident, so compute-bound): one call per candidate, as the service does.
 * Prints GB/s of candidate bytes. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <time.h>
uint64_t hamming_distance(const uint8_t *a, const uint8_t *b, size_t nbytes);
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }
int main(void) {
    size_t code = 512, count = 512, n = code * count;
    uint8_t *db = aligned_alloc(64, n), *q = aligned_alloc(64, code);
    for (size_t i = 0; i < n; i++) db[i] = (uint8_t)(i * 2654435761u >> 24);
    for (size_t i = 0; i < code; i++) q[i] = (uint8_t)(i * 40503u >> 7);
    volatile uint64_t sink = 0;
    double best = 1e9;
    for (int k = 0; k < 7; k++) {
        uint64_t s = 0;
        double t = now();
        for (int r = 0; r < 300; r++)
            for (size_t j = 0; j < count; j++) s += hamming_distance(q, db + j * code, code);
        t = (now() - t) / 300;
        sink ^= s;
        if (t < best) best = t;
    }
    printf("%.3f\n", n / best / 1e9);
    return 0;
}
