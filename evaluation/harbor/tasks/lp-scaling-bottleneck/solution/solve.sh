#!/bin/bash
# Oracle: linux-perf Flow D applied to the recorded files (dual-profile jumpers,
# Phase 4 categories, Phase 3 focused c2c as the next measurement).
set -euo pipefail
cat > /app/diagnosis.json <<'JSON'
{
  "bottlenecks": [
    {"function": "ring_lock_acquire",
     "category": "lock_spin",
     "evidence": "0.71% (#8) at 1 thread -> 27.35% (#2) at 32; 94% of its samples on lock cmpxchg retried by jne: test-and-set spin on the shared ring lock taken by every ring_pop"},
    {"function": "batch_commit",
     "category": "shared_counter",
     "evidence": "0.58% (#9) -> 8.94% (#3); 86% on lock add to g_records_committed, result unused: one statistics counter updated by every worker"}
  ],
  "next_measurement": "perf c2c record -g at 32 threads, then perf c2c report --stdio --full-symbols --double-cl filtered to cache lines accessed by ring_lock_acquire and batch_commit: expect high Tot Hitm on the line holding ring->lock and on the line holding g_records_committed, each with all accesses at one offset (true sharing). score_block falls from 72% to 40% and is not why it stops scaling."
}
JSON
cat /app/diagnosis.json
