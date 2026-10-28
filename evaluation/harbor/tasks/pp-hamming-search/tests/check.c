/* Correctness: the library under test against a bit-by-bit reference on every
 * length 0..1100 at many (a, b) alignment pairs, long buffers, the all-bits-differ
 * worst case (catches narrow-accumulator overflow) and hamming_nearest(). */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
uint64_t hamming_distance(const uint8_t *a, const uint8_t *b, size_t nbytes);
size_t hamming_nearest(const uint8_t *query, const uint8_t *codes, size_t count,
                       size_t code_bytes, uint64_t *dist);

static uint64_t ref(const uint8_t *a, const uint8_t *b, size_t n) {
    uint64_t d = 0;
    for (size_t i = 0; i < n; i++)
        for (int k = 0; k < 8; k++) d += ((a[i] ^ b[i]) >> k) & 1u;
    return d;
}

int main(void) {
    int bad = 0;
    size_t big = (1 << 20) + 37;
    uint8_t *a = malloc(big + 128), *b = malloc(big + 128);
    uint64_t s = 0x9E3779B97F4A7C15ull;
    for (size_t i = 0; i < big + 128; i++) {
        s ^= s << 13; s ^= s >> 7; s ^= s << 17; a[i] = (uint8_t)s;
        s ^= s << 13; s ^= s >> 7; s ^= s << 17; b[i] = (uint8_t)s;
    }
    if (hamming_distance(a, b, 0) != 0) { puts("FAIL empty"); bad++; }
    for (size_t oa = 0; oa < 64 && bad < 5; oa += 3)
        for (size_t n = 0; n <= 1100 && bad < 5; n++) {
            size_t ob = (oa * 7 + n) % 64;
            if (hamming_distance(a + oa, b + ob, n) != ref(a + oa, b + ob, n)) {
                printf("FAIL a+%zu b+%zu len=%zu\n", oa, ob, n); bad++;
            }
        }
    size_t lens[] = {4096, 65536 + 1, 262144 + 13, big};
    for (int i = 0; i < 4; i++)
        if (hamming_distance(a + 5, b + 1, lens[i]) != ref(a + 5, b + 1, lens[i])) {
            printf("FAIL long len=%zu\n", lens[i]); bad++;
        }
    uint8_t *z = calloc(big, 1), *o = malloc(big);
    memset(o, 0xFF, big);
    if (hamming_distance(z, o, big) != 8ull * big) { puts("FAIL all-bits-differ"); bad++; }
    if (hamming_distance(o + 1, o + 3, big - 3) != 0) { puts("FAIL identical"); bad++; }
    /* nearest: plant a code at distance 3 from the query among random codes */
    size_t code = 512, count = 300;
    uint8_t *db = malloc(code * count);
    memcpy(db, a + 1000, code * count);
    memcpy(db + 217 * code, b, code);
    db[217 * code + 9] ^= 0x01; db[217 * code + 100] ^= 0x80; db[217 * code + 511] ^= 0x10;
    uint64_t d = 0;
    size_t idx = hamming_nearest(b, db, count, code, &d);
    if (idx != 217 || d != 3) { printf("FAIL nearest idx=%zu dist=%llu\n", idx, (unsigned long long)d); bad++; }
    puts(bad ? "CHECK FAIL" : "CHECK OK");
    return bad != 0;
}
