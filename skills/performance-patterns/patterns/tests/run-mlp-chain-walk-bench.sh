#!/bin/bash
# run-mlp-chain-walk-bench.sh - Build, run, and profile mlp-chain-walk-bench.
#
# Three phases:
#   1. Comparative sweep across W in {1,2,4,8,16}, printing ns/step and speedup.
#   2. perf stat around the isolated serial and interleaved modes so the
#      reader can see cycles / instructions / cache-references / cache-misses
#      change with interleaving.
#   3. perf record + perf report --stdio on both isolated modes so the reader
#      can see which instruction the samples land on: one dependent load
#      dominates in serial mode, and its share drops substantially with
#      interleaving.
#
# Usage:
#   ./run-mlp-chain-walk-bench.sh [mib_per_chain] [steps_millions]
#
#   mib_per_chain    per-chain working set in MiB (default 64, sized so
#                    each chain is far past L3 on any current server core)
#   steps_millions   total steps walked per run (default 200 = 200e6)
#
# The bench binary pins itself to CPU 0, so a plain run measures a single
# core in isolation. Only portable perf events are used; nothing here is
# vendor-specific.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$SCRIPT_DIR/mlp-chain-walk-bench.c"
BIN="$SCRIPT_DIR/mlp-chain-walk-bench"

MIB_PER_CHAIN="${1:-64}"
STEPS_MILLIONS="${2:-200}"

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
"$BIN" "$MIB_PER_CHAIN" "$STEPS_MILLIONS"
echo

# --- Phase 2: perf stat on isolated modes ---------------------------------

if command -v perf >/dev/null 2>&1; then
    # Portable events: cycles / instructions / cache-references / cache-misses
    # are exposed as generic PERF_TYPE_HARDWARE by every backend that
    # implements the perf subsystem, so this section works across vendors.
    PERF_EVENTS="cycles,instructions,cache-references,cache-misses"

    echo "=== Phase 2: perf stat, serial (W=1) ==="
    perf stat -e "$PERF_EVENTS" -- "$BIN" "$MIB_PER_CHAIN" "$STEPS_MILLIONS" serial 2>&1
    echo

    echo "=== Phase 2: perf stat, interleaved (W=8) ==="
    perf stat -e "$PERF_EVENTS" -- "$BIN" "$MIB_PER_CHAIN" "$STEPS_MILLIONS" interleaved 2>&1
    echo

    # --- Phase 3: perf record / report on isolated modes ------------------

    RECDIR="$(mktemp -d)"
    trap 'rm -rf "$RECDIR"' EXIT

    echo "=== Phase 3: perf record + report, serial (W=1) ==="
    # -F 4000: sample at ~4 kHz, enough resolution for a single hot loop
    # --call-graph fp: cheap unwinder, good enough to attribute samples
    #                  when only one non-inlined leaf matters.
    perf record -F 4000 --call-graph fp -o "$RECDIR/serial.data" \
        -- "$BIN" "$MIB_PER_CHAIN" "$STEPS_MILLIONS" serial >/dev/null 2>&1
    perf report --stdio --no-children -i "$RECDIR/serial.data" 2>/dev/null | head -40
    echo

    echo "=== Phase 3: perf record + report, interleaved (W=8) ==="
    perf record -F 4000 --call-graph fp -o "$RECDIR/interleaved.data" \
        -- "$BIN" "$MIB_PER_CHAIN" "$STEPS_MILLIONS" interleaved >/dev/null 2>&1
    perf report --stdio --no-children -i "$RECDIR/interleaved.data" 2>/dev/null | head -40
    echo
else
    echo "=== Phase 2/3 skipped: perf not installed ==="
    echo "  install linux-tools-common (or your distro's equivalent) to enable"
    echo
fi

echo "=== Done ==="
echo "See mlp-chain-walk-results.md for a sample output and interpretation."
