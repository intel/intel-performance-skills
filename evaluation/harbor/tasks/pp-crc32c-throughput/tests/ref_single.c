/* The baseline the speed gate is measured against: hardware CRC32C with one
 * _mm_crc32_u64 dependency chain. This is what a direct port to the crc32
 * instruction gives, and it is ~1/3 of what the instruction can do. */
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <nmmintrin.h>

__attribute__((target("sse4.2")))
uint32_t crc32c(uint32_t crc, const void *buf, size_t len) {
    const unsigned char *p = buf;
    uint64_t c = ~crc & 0xFFFFFFFFu;
    while (len >= 8) {
        uint64_t v;
        memcpy(&v, p, 8);
        c = _mm_crc32_u64(c, v);
        p += 8;
        len -= 8;
    }
    while (len--)
        c = _mm_crc32_u8((uint32_t)c, *p++);
    return ~(uint32_t)c;
}
