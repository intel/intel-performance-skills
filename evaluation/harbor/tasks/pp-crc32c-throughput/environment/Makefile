CC ?= gcc
CFLAGS ?= -O2 -g

all: libcrc32c.a bench

libcrc32c.a: crc32c.o
	ar rcs $@ $^

crc32c.o: crc32c.c crc32c.h
	$(CC) $(CFLAGS) -c crc32c.c -o $@

bench: bench.c libcrc32c.a
	$(CC) $(CFLAGS) bench.c libcrc32c.a -o $@

clean:
	rm -f *.o libcrc32c.a bench

.PHONY: all clean
