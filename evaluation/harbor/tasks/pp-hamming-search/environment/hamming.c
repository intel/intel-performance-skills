/* Hamming distance between binary codes. */
#include <string.h>
#include "hamming.h"

uint64_t hamming_distance(const uint8_t *a, const uint8_t *b, size_t nbytes) {
    uint64_t d = 0;
    size_t i = 0;
    for (; i + 8 <= nbytes; i += 8) {
        uint64_t x, y;
        memcpy(&x, a + i, 8);
        memcpy(&y, b + i, 8);
        d += __builtin_popcountll(x ^ y);
    }
    for (; i < nbytes; i++)
        d += __builtin_popcount(a[i] ^ b[i]);
    return d;
}
