#ifndef HAMMING_H
#define HAMMING_H
#include <stddef.h>
#include <stdint.h>
/* Number of differing bits between two nbytes-long binary codes. */
uint64_t hamming_distance(const uint8_t *a, const uint8_t *b, size_t nbytes);
/* Index of the code in codes[count][code_bytes] closest to query; distance in *dist. */
size_t hamming_nearest(const uint8_t *query, const uint8_t *codes, size_t count,
                       size_t code_bytes, uint64_t *dist);
#endif
