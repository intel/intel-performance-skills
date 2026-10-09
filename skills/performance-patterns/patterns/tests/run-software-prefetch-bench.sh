#!/bin/bash
# run-software-prefetch-bench.sh - Build, run, and profile
# software-prefetch-bench.
#
# Three phases:
#   1. Comparative sweep across prefetch distances {0, 1K, 2K, 4K, 8K},
#      printing ns/row and % vs the no-prefetch baseline.
#   2. perf stat around the isolated noprefetch and prefetch modes so
#      the reader can see cycles / instructions / cache-references /
#      cache-misses change with the hint.
#   3. perf record + perf report --stdio on both isolated modes so the
#      reader can see whether one instruction dominates the scan's
#      cycles and how the hint moves that share.
#
# Usage:
#   ./run-software-prefetch-bench.sh [arena_mib] [row_bytes] [seconds]
#
#   arena_mib     record arena in MiB (default 256; past L3 on most
#                 current server CPUs)
#   row_bytes     fixed stride per record (default 64)
#   seconds       wall-clock budget per configuration (default 2)
#
# The bench binary pins itself to CPU 0, so a plain run measures a
# single core in isolation. Only portable perf events are used;
# nothing here is vendor-specific.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$SCRIPT_DIR/software-prefetch-bench.c"
BIN="$SCRIPT_DIR/software-prefetch-bench"

ARENA_MIB="${1:-256}"
ROW_BYTES="${2:-64}"
SECONDS_PER_CFG="${3:-2}"

# --- Build -----------------------------------------------------------------

if [[ ! -f "$SRC" ]]; then
    echo "ERROR: source not found: $SRC" >&2
    exit 1
fi

echo "=== Building ==="
gcc -O2 -pthread -o "$BIN" "$SRC"
echo "  binary: $BIN"
echo

# --- Phase 1: comparative sweep -------------------------------------------

echo "=== Phase 1: comparative sweep ==="
"$BIN" "$ARENA_MIB" "$ROW_BYTES" "$SECONDS_PER_CFG"
echo

# --- Phase 2 & 3: perf ----------------------------------------------------

if command -v perf >/dev/null 2>&1; then
    PERF_EVENTS="cycles,instructions,cache-references,cache-misses"

    echo "=== Phase 2: perf stat, noprefetch (N=0) ==="
    perf stat -e "$PERF_EVENTS" -- \
        "$BIN" "$ARENA_MIB" "$ROW_BYTES" "$SECONDS_PER_CFG" noprefetch 2>&1
    echo

    echo "=== Phase 2: perf stat, prefetch (N=2048) ==="
    perf stat -e "$PERF_EVENTS" -- \
        "$BIN" "$ARENA_MIB" "$ROW_BYTES" "$SECONDS_PER_CFG" prefetch 2>&1
    echo

    RECDIR="$(mktemp -d)"
    trap 'rm -rf "$RECDIR"' EXIT

    echo "=== Phase 3: perf record + report, noprefetch (N=0) ==="
    perf record -F 4000 --call-graph fp -o "$RECDIR/noprefetch.data" \
        -- "$BIN" "$ARENA_MIB" "$ROW_BYTES" "$SECONDS_PER_CFG" noprefetch \
        >/dev/null 2>&1
    perf report --stdio --no-children -i "$RECDIR/noprefetch.data" 2>/dev/null | head -30
    echo

    echo "=== Phase 3: perf record + report, prefetch (N=2048) ==="
    perf record -F 4000 --call-graph fp -o "$RECDIR/prefetch.data" \
        -- "$BIN" "$ARENA_MIB" "$ROW_BYTES" "$SECONDS_PER_CFG" prefetch \
        >/dev/null 2>&1
    perf report --stdio --no-children -i "$RECDIR/prefetch.data" 2>/dev/null | head -30
    echo
else
    echo "=== Phase 2/3 skipped: perf not installed ==="
    echo "  install linux-tools-common (or your distro's equivalent) to enable"
    echo
fi

echo "=== Done ==="
echo "See software-prefetch-results.md for a sample output and interpretation."
