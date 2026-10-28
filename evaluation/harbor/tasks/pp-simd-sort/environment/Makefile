CXX ?= g++
CXXFLAGS ?= -O2 -g -std=c++17

all: libranker.a bench

libranker.a: ranker.o
	ar rcs $@ $^

ranker.o: ranker.cpp ranker.h
	$(CXX) $(CXXFLAGS) -c ranker.cpp -o $@

bench: bench.cpp libranker.a
	$(CXX) $(CXXFLAGS) bench.cpp libranker.a -o $@

clean:
	rm -f *.o libranker.a bench

.PHONY: all clean
