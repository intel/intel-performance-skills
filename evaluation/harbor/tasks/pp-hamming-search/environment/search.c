/* Shortlist re-ranking: linear scan for the nearest code. */
#include "hamming.h"

size_t hamming_nearest(const uint8_t *query, const uint8_t *codes, size_t count,
                       size_t code_bytes, uint64_t *dist) {
    size_t best = 0;
    uint64_t best_d = UINT64_MAX;
    for (size_t i = 0; i < count; i++) {
        uint64_t d = hamming_distance(query, codes + i * code_bytes, code_bytes);
        if (d < best_d) {
            best_d = d;
            best = i;
        }
    }
    if (dist)
        *dist = best_d;
    return best;
}
