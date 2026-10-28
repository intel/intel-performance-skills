#!/bin/bash
# Oracle: the skill's own library (patterns/fast-crc32c-impl.md) with its dispatch
# wrapper. The three implementation files are copies of
# skills/performance-patterns/library/ and keep their licence headers.
set -euo pipefail
cp /solution/crc32c_avx512_vpclmulqdq.c /solution/crc32c_sse42.c /solution/crc32c_portable.c /app/
cat > /app/crc32c.c <<'C'
/* CRC32C dispatch wrapper (performance-patterns fast-crc32c-impl.md). */
#include "crc32c.h"
extern uint32_t crc32c_avx512_impl(uint32_t crc, const char *buf, size_t len);
extern uint32_t crc32c_sse42_impl(uint32_t crc, const char *buf, size_t len);
extern uint32_t crc32c_portable_impl(uint32_t crc, const char *buf, size_t len);
typedef uint32_t (*crc32c_fn)(uint32_t, const char *, size_t);
static crc32c_fn resolve(void) {
    __builtin_cpu_init();
    if (__builtin_cpu_supports("avx512f") && __builtin_cpu_supports("vpclmulqdq"))
        return crc32c_avx512_impl;
    if (__builtin_cpu_supports("sse4.2"))
        return crc32c_sse42_impl;
    return crc32c_portable_impl;
}
uint32_t crc32c(uint32_t crc, const void *buf, size_t len) {
    static crc32c_fn impl;
    if (__builtin_expect(impl == 0, 0))
        impl = resolve();
    return impl(crc, (const char *)buf, len);
}
C
cat > /app/Makefile <<'M'
CC ?= gcc
CFLAGS ?= -O2 -g
OBJS = crc32c.o crc32c_avx512_vpclmulqdq.o crc32c_sse42.o crc32c_portable.o

all: libcrc32c.a bench

libcrc32c.a: $(OBJS)
	ar rcs $@ $^

crc32c.o: crc32c.c crc32c.h
	$(CC) $(CFLAGS) -c crc32c.c -o $@
crc32c_avx512_vpclmulqdq.o: crc32c_avx512_vpclmulqdq.c
	$(CC) $(CFLAGS) -mavx512f -mavx512vl -mvpclmulqdq -mpclmul -msse4.2 -c $< -o $@
crc32c_sse42.o: crc32c_sse42.c
	$(CC) $(CFLAGS) -msse4.2 -mpclmul -c $< -o $@
crc32c_portable.o: crc32c_portable.c
	$(CC) $(CFLAGS) -c $< -o $@

bench: bench.c libcrc32c.a
	$(CC) $(CFLAGS) bench.c libcrc32c.a -o $@

clean:
	rm -f *.o libcrc32c.a bench

.PHONY: all clean
M
make -C /app clean all
/app/bench
