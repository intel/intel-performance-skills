/* scoring.h - public interface of the embedding scoring module.
 *
 * The numeric kernels live in kernels.c (C, built with -std=c11); the worker
 * pool, sharded candidate store and ranking live in pool.cpp (C++17).
 */
#ifndef SCORING_H
#define SCORING_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SCORING_DIM 256          /* floats per embedding */
#define SCORING_CODE_BYTES 64    /* bytes per binary code (512-bit sketch) */

/* ---- kernels.c ---- */
void     scoring_kernels_init(void);
float    vec_mean_f32(const float *x, size_t n);
void     vec_affine_f32(float *dst, const float *src, const float *bias,
                        float alpha, float beta, size_t n);
float    vec_dot_f32(const float *a, const float *b, size_t n);
void     vec_center_f32(float *x, float mean, size_t n);
uint32_t hamming_distance(const uint8_t *a, const uint8_t *b, size_t n);
uint32_t block_crc32c(uint32_t crc, const void *buf, size_t len);
void     block_copy_f32x32(float *dst, const float *src);

#ifdef __cplusplus
}
#endif

#endif /* SCORING_H */
