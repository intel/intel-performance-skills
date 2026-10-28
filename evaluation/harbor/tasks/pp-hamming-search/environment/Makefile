CC ?= gcc
CFLAGS ?= -O2 -g

all: libhamming.a bench

libhamming.a: hamming.o search.o
	ar rcs $@ $^

hamming.o: hamming.c hamming.h
	$(CC) $(CFLAGS) -c hamming.c -o $@

search.o: search.c hamming.h
	$(CC) $(CFLAGS) -c search.c -o $@

bench: bench.c libhamming.a
	$(CC) $(CFLAGS) bench.c libhamming.a -o $@

clean:
	rm -f *.o libhamming.a bench

.PHONY: all clean
