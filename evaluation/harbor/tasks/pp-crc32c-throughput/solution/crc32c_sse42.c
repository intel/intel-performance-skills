/* (C) 2026 Intel Corporation, MIT license */
/*
 * CRC32C using SSE4.2 _mm_crc32_u64 with three independent accumulators.
 *
 * Three parallel scalar streams hide the 3-cycle latency of _mm_crc32_u64.
 * Accumulator combination uses PCLMULQDQ (always available alongside SSE4.2).
 * The xnmodp helper is adapted from https://github.com/corsix/fast-crc32/
 * (MIT or zlib licensed) with permission from the open-source license terms.
 *
 * Compile with: -msse4.2 -mpclmul
 * CPUID check:  __builtin_cpu_supports("sse4.2")
 * Typical speed: ~15-25 GB/s depending on microarchitecture.
 *
 * Entry point: crc32c_sse42_impl(uint32_t crc, const char *buf, size_t len)
 */

#include <stddef.h>
#include <stdint.h>
#include <nmmintrin.h>
#include <wmmintrin.h>

#if defined(_MSC_VER)
#define CRC_AINLINE static __forceinline
#else
#define CRC_AINLINE static __inline __attribute__((always_inline))
#endif

/* Compute x^n mod P(CRC32C) in O(log n) time using repeated squaring.
 * Adapted from https://github.com/corsix/fast-crc32/ (MIT or zlib). */
static uint32_t xnmodp(uint64_t n) {
  uint64_t stack = ~(uint64_t)1;
  uint32_t acc, low;
  for (; n > 191; n = (n >> 1) - 16) {
    stack = (stack << 1) + (n & 1);
  }
  stack = ~stack;
  acc = ((uint32_t)0x80000000) >> (n & 31);
  for (n >>= 5; n; --n) {
    acc = _mm_crc32_u32(acc, 0);
  }
  while ((low = stack & 1), stack >>= 1) {
    __m128i x = _mm_cvtsi32_si128(acc);
    uint64_t y = _mm_cvtsi128_si64(_mm_clmulepi64_si128(x, x, 0));
    acc = _mm_crc32_u64(0, y << low);
  }
  return acc;
}

/* Compute the combining factor for an accumulator that covered nbytes,
 * so its value can be folded into the stream that follows it. */
CRC_AINLINE __m128i crc_shift(uint32_t crc, size_t nbytes) {
  __m128i a = _mm_cvtsi32_si128(crc);
  __m128i b = _mm_cvtsi32_si128(xnmodp(nbytes * 8 - 33));
  return _mm_clmulepi64_si128(a, b, 0);
}

extern uint32_t crc32c_sse42_impl(uint32_t crc0, const char *buf, size_t len) {
  crc0 = ~crc0;

  /* Align to 8-byte boundary. */
  for (; len && ((uintptr_t)buf & 7); --len) {
    crc0 = _mm_crc32_u8(crc0, (uint8_t)*buf++);
  }

  /* Three-accumulator main loop: split input into three equal streams and
   * process them in parallel to saturate the CRC32 execution unit. */
  if (len >= 3 * 8) {
    size_t klen = (len / 3) & ~(size_t)7; /* per-stream block length */
    uint32_t crc1 = 0, crc2 = 0;
    const char *p1 = buf + klen;
    const char *p2 = buf + klen * 2;
    size_t i;

    for (i = 0; i < klen; i += 8) {
      crc0 = _mm_crc32_u64(crc0, *(const uint64_t*)(buf + i));
      crc1 = _mm_crc32_u64(crc1, *(const uint64_t*)(p1  + i));
      crc2 = _mm_crc32_u64(crc2, *(const uint64_t*)(p2  + i));
    }

    /* Combine: shift crc0 over klen*2 more bytes, crc1 over klen more. */
    uint64_t v0 = _mm_extract_epi64(crc_shift(crc0, klen * 2), 0);
    uint64_t v1 = _mm_extract_epi64(crc_shift(crc1, klen),     0);
    crc0 = _mm_crc32_u64(0, v0 ^ v1);
    crc0 ^= crc2;

    buf  = p2 + klen;
    len -= klen * 3;
  }

  /* Scalar tail. */
  for (; len >= 8; buf += 8, len -= 8) {
    crc0 = _mm_crc32_u64(crc0, *(const uint64_t*)buf);
  }
  for (; len; --len) {
    crc0 = _mm_crc32_u8(crc0, (uint8_t)*buf++);
  }

  return ~crc0;
}
