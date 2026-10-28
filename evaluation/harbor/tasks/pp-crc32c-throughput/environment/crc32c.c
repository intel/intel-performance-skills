/* CRC32C (Castagnoli), reflected polynomial 0x82F63B78. */
#include "crc32c.h"

static uint32_t table[256];
static int table_ready;

static void init_table(void) {
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ (0x82F63B78u & (0u - (c & 1u)));
        table[i] = c;
    }
    table_ready = 1;
}

uint32_t crc32c(uint32_t crc, const void *buf, size_t len) {
    const unsigned char *p = buf;
    if (!table_ready)
        init_table();
    crc = ~crc;
    while (len--)
        crc = table[(crc ^ *p++) & 0xFF] ^ (crc >> 8);
    return ~crc;
}
