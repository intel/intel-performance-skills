/* Hamming distance, runtime-dispatched (performance-patterns
 * references/known-algorithms-impl.md, Hamming Distance; library/cpu-dispatch.md
 * Mechanism B). AVX512_VPOPCNTDQ -> AVX2 Harley-Seal -> POPCNT x4 -> portable. */
#include <string.h>
#include <immintrin.h>
#include "hamming.h"

static inline uint64_t ld64(const uint8_t *p) { uint64_t v; memcpy(&v, p, 8); return v; }

static uint64_t swar64(uint64_t x) {
    x -= (x >> 1) & 0x5555555555555555ull;
    x = (x & 0x3333333333333333ull) + ((x >> 2) & 0x3333333333333333ull);
    x = (x + (x >> 4)) & 0x0F0F0F0F0F0F0F0Full;
    return (x * 0x0101010101010101ull) >> 56;
}

static uint64_t hd_portable(const uint8_t *a, const uint8_t *b, size_t n) {
    uint64_t d = 0;
    size_t i = 0;
    for (; i + 8 <= n; i += 8) d += swar64(ld64(a + i) ^ ld64(b + i));
    for (; i < n; i++) d += swar64((uint64_t)(a[i] ^ b[i]));
    return d;
}

/* CPUID: POPCNT */
__attribute__((target("popcnt")))
static uint64_t hd_popcnt(const uint8_t *a, const uint8_t *b, size_t n) {
    uint64_t d0 = 0, d1 = 0, d2 = 0, d3 = 0;
    size_t i = 0;
    for (; i + 32 <= n; i += 32) {
        d0 += __builtin_popcountll(ld64(a + i) ^ ld64(b + i));
        d1 += __builtin_popcountll(ld64(a + i + 8) ^ ld64(b + i + 8));
        d2 += __builtin_popcountll(ld64(a + i + 16) ^ ld64(b + i + 16));
        d3 += __builtin_popcountll(ld64(a + i + 24) ^ ld64(b + i + 24));
    }
    for (; i + 8 <= n; i += 8) d0 += __builtin_popcountll(ld64(a + i) ^ ld64(b + i));
    for (; i < n; i++) d0 += __builtin_popcount(a[i] ^ b[i]);
    return d0 + d1 + d2 + d3;
}

/* CPUID: AVX2, POPCNT */
__attribute__((target("avx2,popcnt")))
static inline __m256i cnt8(__m256i x) {
    const __m256i lut = _mm256_setr_epi8(0, 1, 1, 2, 1, 2, 2, 3, 1, 2, 2, 3, 2, 3, 3, 4,
                                         0, 1, 1, 2, 1, 2, 2, 3, 1, 2, 2, 3, 2, 3, 3, 4);
    const __m256i lo = _mm256_set1_epi8(0x0f);
    return _mm256_add_epi8(_mm256_shuffle_epi8(lut, _mm256_and_si256(x, lo)),
                           _mm256_shuffle_epi8(lut, _mm256_and_si256(_mm256_srli_epi16(x, 4), lo)));
}
#define XV(o) _mm256_xor_si256(_mm256_loadu_si256((const __m256i *)(a + i + (o))), \
                               _mm256_loadu_si256((const __m256i *)(b + i + (o))))
#define CSA(h, l, A, B, C) do { __m256i u_ = _mm256_xor_si256(A, B); \
    h = _mm256_or_si256(_mm256_and_si256(A, B), _mm256_and_si256(u_, C)); \
    l = _mm256_xor_si256(u_, C); } while (0)
#define SAD(v) _mm256_sad_epu8(cnt8(v), _mm256_setzero_si256())

/* Harley-Seal over 16 x 32-byte words (Mula, Kurz, Lemire 2017). */
__attribute__((target("avx2,popcnt")))
static uint64_t hd_avx2(const uint8_t *a, const uint8_t *b, size_t n) {
    __m256i tot = _mm256_setzero_si256(), ones = tot, twos = tot, fours = tot, eights = tot;
    __m256i sixteens, twosA, twosB, foursA, foursB, eightsA, eightsB;
    size_t i = 0;
    for (; i + 512 <= n; i += 512) {
        CSA(twosA, ones, ones, XV(0), XV(32));    CSA(twosB, ones, ones, XV(64), XV(96));
        CSA(foursA, twos, twos, twosA, twosB);
        CSA(twosA, ones, ones, XV(128), XV(160)); CSA(twosB, ones, ones, XV(192), XV(224));
        CSA(foursB, twos, twos, twosA, twosB);
        CSA(eightsA, fours, fours, foursA, foursB);
        CSA(twosA, ones, ones, XV(256), XV(288)); CSA(twosB, ones, ones, XV(320), XV(352));
        CSA(foursA, twos, twos, twosA, twosB);
        CSA(twosA, ones, ones, XV(384), XV(416)); CSA(twosB, ones, ones, XV(448), XV(480));
        CSA(foursB, twos, twos, twosA, twosB);
        CSA(eightsB, fours, fours, foursA, foursB);
        CSA(sixteens, eights, eights, eightsA, eightsB);
        tot = _mm256_add_epi64(tot, SAD(sixteens));
    }
    tot = _mm256_slli_epi64(tot, 4);
    tot = _mm256_add_epi64(tot, _mm256_slli_epi64(SAD(eights), 3));
    tot = _mm256_add_epi64(tot, _mm256_slli_epi64(SAD(fours), 2));
    tot = _mm256_add_epi64(tot, _mm256_slli_epi64(SAD(twos), 1));
    tot = _mm256_add_epi64(tot, SAD(ones));
    for (; i + 32 <= n; i += 32) tot = _mm256_add_epi64(tot, SAD(XV(0)));
    uint64_t d = (uint64_t)_mm256_extract_epi64(tot, 0) + (uint64_t)_mm256_extract_epi64(tot, 1)
               + (uint64_t)_mm256_extract_epi64(tot, 2) + (uint64_t)_mm256_extract_epi64(tot, 3);
    for (; i + 8 <= n; i += 8) d += __builtin_popcountll(ld64(a + i) ^ ld64(b + i));
    for (; i < n; i++) d += __builtin_popcount(a[i] ^ b[i]);
    return d;
}

/* CPUID: AVX512F, AVX512BW (masked byte loads), AVX512_VPOPCNTDQ */
#define XZ(o) _mm512_xor_si512(_mm512_loadu_si512(a + i + (o)), _mm512_loadu_si512(b + i + (o)))
__attribute__((target("avx512f,avx512bw,avx512vpopcntdq")))
static uint64_t hd_avx512(const uint8_t *a, const uint8_t *b, size_t n) {
    __m512i s0 = _mm512_setzero_si512(), s1 = s0, s2 = s0, s3 = s0;
    size_t i = 0;
    for (; i + 256 <= n; i += 256) {
        s0 = _mm512_add_epi64(s0, _mm512_popcnt_epi64(XZ(0)));
        s1 = _mm512_add_epi64(s1, _mm512_popcnt_epi64(XZ(64)));
        s2 = _mm512_add_epi64(s2, _mm512_popcnt_epi64(XZ(128)));
        s3 = _mm512_add_epi64(s3, _mm512_popcnt_epi64(XZ(192)));
    }
    for (; i + 64 <= n; i += 64) s0 = _mm512_add_epi64(s0, _mm512_popcnt_epi64(XZ(0)));
    if (i < n) {
        __mmask64 m = ~0ull >> (64 - (n - i));
        s1 = _mm512_add_epi64(s1, _mm512_popcnt_epi64(_mm512_xor_si512(
                 _mm512_maskz_loadu_epi8(m, a + i), _mm512_maskz_loadu_epi8(m, b + i))));
    }
    return (uint64_t)_mm512_reduce_add_epi64(
        _mm512_add_epi64(_mm512_add_epi64(s0, s1), _mm512_add_epi64(s2, s3)));
}

typedef uint64_t (*hd_fn)(const uint8_t *, const uint8_t *, size_t);

static hd_fn resolve(void) {
    __builtin_cpu_init();
    if (__builtin_cpu_supports("avx512f") && __builtin_cpu_supports("avx512bw") &&
        __builtin_cpu_supports("avx512vpopcntdq"))
        return hd_avx512;
    if (__builtin_cpu_supports("avx2") && __builtin_cpu_supports("popcnt"))
        return hd_avx2;
    if (__builtin_cpu_supports("popcnt"))
        return hd_popcnt;
    return hd_portable;
}

uint64_t hamming_distance(const uint8_t *a, const uint8_t *b, size_t nbytes) {
    static hd_fn impl;
    if (__builtin_expect(impl == 0, 0))
        impl = resolve();
    return impl(a, b, nbytes);
}
