/* kernels.c - numeric kernels for the embedding scoring service.
 *
 * Every function here runs once per candidate vector inside
 * ScoringPool::score_batch(), so each one is on the hot path.
 * Embeddings are SCORING_DIM floats, binary codes SCORING_CODE_BYTES bytes.
 */
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <immintrin.h>

#include "scoring.h"

/* ------------------------------------------------------------------------- */
/* Block checksum                                                            */
/* ------------------------------------------------------------------------- */

#define CRC32C_POLY 0x82F63B78u   /* Castagnoli, reflected */

static uint32_t crc_table[256];

static void crc_table_init(void)
{
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ (CRC32C_POLY & (0u - (c & 1u)));
        crc_table[i] = c;
    }
}

/* Checksum of a candidate block as it is copied into a shard; the value is
 * stored next to the block and re-checked on read. */
uint32_t block_crc32c(uint32_t crc, const void *buf, size_t len)
{
    const uint8_t *p = (const uint8_t *)buf;
    crc = ~crc;
    while (len--)
        crc = crc_table[(crc ^ *p++) & 0xFFu] ^ (crc >> 8);
    return ~crc;
}

/* ------------------------------------------------------------------------- */
/* Reductions                                                                */
/* ------------------------------------------------------------------------- */

/* Mean of one embedding, used to center it before scoring. */
float vec_mean_f32(const float *x, size_t n)
{
    if (n == 0)
        return 0.0f;
    float total = 0.0f;
    for (size_t i = 0; i < n; i++)
        total += x[i];
    return total / (float)n;
}

/* Inner product.  The AVX2 path keeps four independent FMA chains and the
 * portable path four scalar partial sums; both reassociate the sum, which the
 * ranking stage tolerates (scores are compared, never stored). */
__attribute__((target("avx2,fma")))
static float dot_avx2(const float *restrict a, const float *restrict b, size_t n)
{
    __m256 s0 = _mm256_setzero_ps(), s1 = _mm256_setzero_ps();
    __m256 s2 = _mm256_setzero_ps(), s3 = _mm256_setzero_ps();
    size_t i = 0;
    for (; i + 32 <= n; i += 32) {
        s0 = _mm256_fmadd_ps(_mm256_loadu_ps(a + i),      _mm256_loadu_ps(b + i),      s0);
        s1 = _mm256_fmadd_ps(_mm256_loadu_ps(a + i + 8),  _mm256_loadu_ps(b + i + 8),  s1);
        s2 = _mm256_fmadd_ps(_mm256_loadu_ps(a + i + 16), _mm256_loadu_ps(b + i + 16), s2);
        s3 = _mm256_fmadd_ps(_mm256_loadu_ps(a + i + 24), _mm256_loadu_ps(b + i + 24), s3);
    }
    __m256 s = _mm256_add_ps(_mm256_add_ps(s0, s1), _mm256_add_ps(s2, s3));
    __m128 r = _mm_add_ps(_mm256_castps256_ps128(s), _mm256_extractf128_ps(s, 1));
    r = _mm_hadd_ps(r, r);
    r = _mm_hadd_ps(r, r);
    float out = _mm_cvtss_f32(r);
    for (; i < n; i++)
        out += a[i] * b[i];
    return out;
}

static float dot_portable(const float *restrict a, const float *restrict b, size_t n)
{
    float s0 = 0.0f, s1 = 0.0f, s2 = 0.0f, s3 = 0.0f;
    size_t i = 0;
    for (; i + 4 <= n; i += 4) {
        s0 += a[i] * b[i];
        s1 += a[i + 1] * b[i + 1];
        s2 += a[i + 2] * b[i + 2];
        s3 += a[i + 3] * b[i + 3];
    }
    for (; i < n; i++)
        s0 += a[i] * b[i];
    return (s0 + s1) + (s2 + s3);
}

typedef float (*dot_fn)(const float *, const float *, size_t);
static dot_fn dot_impl = dot_portable;

float vec_dot_f32(const float *a, const float *b, size_t n)
{
    return dot_impl(a, b, n);
}

/* ------------------------------------------------------------------------- */
/* Element-wise transforms                                                   */
/* ------------------------------------------------------------------------- */

/* dst = alpha * src + beta * bias.  score_batch() always passes three distinct
 * buffers: the worker's scratch row, the candidate row and the model bias. */
void vec_affine_f32(float *dst, const float *src, const float *bias,
                    float alpha, float beta, size_t n)
{
    for (size_t i = 0; i < n; i++)
        dst[i] = alpha * src[i] + beta * bias[i];
}

/* In place: x -= mean. */
void vec_center_f32(float *x, float mean, size_t n)
{
    for (size_t i = 0; i < n; i++)
        x[i] -= mean;
}

/* Stage 32 floats (one 128-byte tile) from the candidate store into the
 * worker's scratch row. */
void block_copy_f32x32(float *dst, const float *src)
{
    /* CPUID: AVX */
    __asm__ volatile(
        "vmovups    (%[s]), %%ymm0\n\t"
        "vmovups  32(%[s]), %%ymm1\n\t"
        "vmovups  64(%[s]), %%ymm2\n\t"
        "vmovups  96(%[s]), %%ymm3\n\t"
        "vmovups  %%ymm0,    (%[d])\n\t"
        "vmovups  %%ymm1,  32(%[d])\n\t"
        "vmovups  %%ymm2,  64(%[d])\n\t"
        "vmovups  %%ymm3,  96(%[d])\n\t"
        :
        : [s] "r"(src), [d] "r"(dst)
        : "xmm0", "xmm1", "xmm2", "xmm3", "memory");
}

/* ------------------------------------------------------------------------- */
/* Binary sketches                                                           */
/* ------------------------------------------------------------------------- */

/* Number of differing bits between two binary codes of n bytes; the coarse
 * filter that runs before the float rescoring. */
uint32_t hamming_distance(const uint8_t *a, const uint8_t *b, size_t n)
{
    uint32_t dist = 0;
    for (size_t i = 0; i < n; i++)
        dist += (uint32_t)__builtin_popcount((unsigned)(a[i] ^ b[i]));
    return dist;
}

/* ------------------------------------------------------------------------- */

void scoring_kernels_init(void)
{
    crc_table_init();
    __builtin_cpu_init();
    if (__builtin_cpu_supports("avx2") && __builtin_cpu_supports("fma"))
        dot_impl = dot_avx2;
}
