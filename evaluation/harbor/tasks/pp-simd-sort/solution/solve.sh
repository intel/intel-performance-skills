#!/bin/bash
# Oracle: the skill's SIMD-sort fix (patterns/simd-sort.md): replace std::sort with
# x86simdsort::qsort(p, n, /*hasnan=*/false) from numpy/x86-simd-sort, using the
# runtime-dispatching library build (not the compile-time header-only one), so one
# build runs on CPUs without AVX-512/AVX2. The grader has no network, so the source
# is vendored under /app and its objects go into libranker.a. Per-ISA -march flags
# are the ones upstream's lib/meson.build uses.
set -euo pipefail
XSS_SHA=6e059b14975df2e3c74260ec905167a1be5a34ca
mkdir -p /app/third_party
curl -fsSL "https://codeload.github.com/numpy/x86-simd-sort/tar.gz/$XSS_SHA" \
    | tar -xz -C /app/third_party
rm -rf /app/third_party/x86-simd-sort
mv "/app/third_party/x86-simd-sort-$XSS_SHA" /app/third_party/x86-simd-sort

cat > /app/ranker.cpp <<'CPP'
// Score sorting for the ranking pipeline: x86-simd-sort (vectorized quicksort,
// runtime CPU dispatch: AVX-512 -> AVX2 -> scalar std::sort fallback).
#include "x86simdsort.h"
#include "ranker.h"

void sort_scores(float *scores, size_t n) {
    x86simdsort::qsort(scores, n, /*hasnan=*/false);  // scores are never NaN
}
CPP

cat > /app/Makefile <<'MK'
CXX ?= g++
CXXFLAGS ?= -O2 -g -std=c++17
XSS = third_party/x86-simd-sort
XFLAGS = -O3 -std=c++17 -fvisibility=hidden -fvisibility-inlines-hidden \
         -I$(XSS)/src -I$(XSS)/lib -I$(XSS)/utils
XOBJS = xss-dispatch.o xss-avx2.o xss-skx.o xss-icl.o xss-spr.o

all: libranker.a bench

libranker.a: ranker.o $(XOBJS)
	rm -f $@
	ar rcs $@ $^

ranker.o: ranker.cpp ranker.h
	$(CXX) $(CXXFLAGS) -I$(XSS)/lib -c ranker.cpp -o $@

xss-dispatch.o: $(XSS)/lib/x86simdsort.cpp
	$(CXX) $(XFLAGS) -c $< -o $@
xss-avx2.o: $(XSS)/lib/x86simdsort-avx2.cpp
	$(CXX) $(XFLAGS) -march=haswell -c $< -o $@
xss-skx.o: $(XSS)/lib/x86simdsort-skx.cpp
	$(CXX) $(XFLAGS) -march=skylake-avx512 -c $< -o $@
xss-icl.o: $(XSS)/lib/x86simdsort-icl.cpp
	$(CXX) $(XFLAGS) -march=icelake-client -c $< -o $@
xss-spr.o: $(XSS)/lib/x86simdsort-spr.cpp
	$(CXX) $(XFLAGS) -march=sapphirerapids -c $< -o $@

bench: bench.cpp libranker.a
	$(CXX) $(CXXFLAGS) bench.cpp libranker.a -o $@

clean:
	rm -f *.o libranker.a bench

.PHONY: all clean
MK
make -C /app clean all -j4
/app/bench
