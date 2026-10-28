#ifndef CRC32C_H
#define CRC32C_H
#include <stddef.h>
#include <stdint.h>
uint32_t crc32c(uint32_t crc, const void *buf, size_t len);
#endif
