CC ?= gcc
CXX ?= g++
CFLAGS ?= -O2 -g -std=c11 -Wall
CXXFLAGS ?= -O2 -g -std=c++17 -Wall -pthread

all: libscoring.a selftest

kernels.o: src/kernels.c src/scoring.h
	$(CC) $(CFLAGS) -c src/kernels.c -o $@

pool.o: src/pool.cpp src/scoring.h
	$(CXX) $(CXXFLAGS) -c src/pool.cpp -o $@

libscoring.a: kernels.o pool.o
	ar rcs $@ $^

selftest: src/pool.cpp kernels.o src/scoring.h
	$(CXX) $(CXXFLAGS) -DSCORING_SELFTEST src/pool.cpp kernels.o -o $@

check: selftest
	./selftest

clean:
	rm -f *.o libscoring.a selftest

.PHONY: all check clean
