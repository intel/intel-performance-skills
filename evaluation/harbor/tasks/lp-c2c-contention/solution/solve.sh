#!/bin/bash
# Oracle: linux-perf Flow C on the recorded report (5% Tot Hitm threshold, Offset
# column classification, field names from the struct, fix per sharing type).
set -euo pipefail
cat > /app/contention.json <<'JSON'
{
  "lines": [
    {"address": "0x5603c1a8e100",
     "sharing": "false",
     "data": "struct pipeline_stats g_pipe_stats",
     "fields": ["parsed", "flushed", "flush_bytes"],
     "fix": "Structured false-sharing fix: put the parser-owned fields (parsed, parse_errors) and the flusher-owned fields (flushed, flush_bytes) on separate cache lines, e.g. two groups each aligned/padded to 64 bytes."},
    {"address": "0x5603c1a8e200",
     "sharing": "true",
     "data": "struct global_counters g_counters",
     "fields": ["requests"],
     "fix": "Per-CPU / per-thread statistics: each worker increments its own cache-line-aligned requests counter and /metrics sums them; also move max_inflight off the line."},
    {"address": "0x7f3b2c001080",
     "sharing": "true",
     "data": "struct work_queue (wq_lock/wq_unlock)",
     "fields": ["lock"],
     "fix": "Test-and-Test-and-Set: spin on a plain load of q->lock until it reads 0, and only then attempt the compare-and-swap, so waiters share the line instead of stealing it."}
  ]
}
JSON
cat /app/contention.json
