#!/bin/bash
# Oracle: the skill's Hamming-distance recipe (references/known-algorithms-impl.md)
# with a __builtin_cpu_supports dispatch (library/cpu-dispatch.md, Mechanism B).
# AVX512_VPOPCNTDQ -> AVX2 Harley-Seal -> POPCNT x4 -> portable SWAR. The Makefile
# is unchanged: each variant carries its own target attribute.
set -euo pipefail
cp /solution/hamming.c /app/hamming.c
make -C /app clean all
/app/bench
