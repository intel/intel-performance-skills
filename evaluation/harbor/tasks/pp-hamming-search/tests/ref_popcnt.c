/* The baseline the speed gate is measured against: the hardware 64-bit popcount
 * instruction on one dependency chain. This is what fixing the libgcc
 * __popcountdi2 call gives, and it is a fraction of what this CPU can do. */
#include <stddef.h>
#include <stdint.h>
#include <string.h>

__attribute__((target("popcnt")))
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
