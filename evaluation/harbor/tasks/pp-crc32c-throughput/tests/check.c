/* Correctness: the library under test against a bitwise CRC32C, on test vectors,
 * every length 0..1100 at every alignment 0..15, random long buffers, and chaining. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
uint32_t crc32c(uint32_t crc, const void *buf, size_t len);

static uint32_t ref(uint32_t crc, const unsigned char *p, size_t n) {
    crc = ~crc;
    while (n--) {
        crc ^= *p++;
        for (int k = 0; k < 8; k++)
            crc = (crc >> 1) ^ (0x82F63B78u & (0u - (crc & 1u)));
    }
    return ~crc;
}

int main(void) {
    int bad = 0;
    if (crc32c(0, "123456789", 9) != 0xE3069283u) { puts("FAIL test vector"); bad++; }
    if (crc32c(0, "", 0) != 0) { puts("FAIL empty"); bad++; }
    size_t big = (1 << 20) + 37;
    unsigned char *b = malloc(big + 64);
    uint64_t s = 0x9E3779B97F4A7C15ull;
    for (size_t i = 0; i < big + 64; i++) { s ^= s << 13; s ^= s >> 7; s ^= s << 17; b[i] = (unsigned char)s; }
    for (size_t off = 0; off < 16 && bad < 5; off++)
        for (size_t n = 0; n <= 1100 && bad < 5; n++) {
            uint32_t seed = (uint32_t)(n * 2654435761u);
            if (crc32c(seed, b + off, n) != ref(seed, b + off, n)) {
                printf("FAIL off=%zu len=%zu\n", off, n); bad++;
            }
        }
    size_t lens[] = {4096, 65536, 65536 + 1, 262144 + 13, big};
    for (int i = 0; i < 5; i++)
        if (crc32c(7, b + 3, lens[i]) != ref(7, b + 3, lens[i])) { printf("FAIL long len=%zu\n", lens[i]); bad++; }
    uint32_t whole = crc32c(0, b, big), part = crc32c(crc32c(0, b, 100003), b + 100003, big - 100003);
    if (whole != part || whole != ref(0, b, big)) { puts("FAIL chaining"); bad++; }
    puts(bad ? "CHECK FAIL" : "CHECK OK");
    return bad != 0;
}
