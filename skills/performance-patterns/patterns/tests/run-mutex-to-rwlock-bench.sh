#!/bin/bash
# run-mutex-to-rwlock-bench.sh — Build and run the mutex-to-rwlock benchmark
#
# Usage:
#   ./run-mutex-to-rwlock-bench.sh [duration_sec]
#
# The script detects CPU topology, pins threads to physical cores on socket 0,
# and runs the benchmark at multiple write percentages. Optionally collects
# perf profiles if perf is available.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$SCRIPT_DIR/mutex-to-rwlock-bench.c"
BIN="$SCRIPT_DIR/mutex-to-rwlock-bench"
DURATION="${1:-3}"

# --- Detect CPU topology ---

detect_topology() {
    if ! command -v lscpu &>/dev/null; then
        echo "ERROR: lscpu not found" >&2
        exit 1
    fi

    SOCKETS=$(lscpu | awk '/^Socket\(s\):/ {print $2}')
    CORES_PER_SOCKET=$(lscpu | awk '/^Core\(s\) per socket:/ {print $4}')
    THREADS_PER_CORE=$(lscpu | awk '/^Thread\(s\) per core:/ {print $4}')
    TOTAL_CPUS=$(lscpu | awk '/^CPU\(s\):/ {print $2}' | head -1)

    echo "=== CPU Topology ==="
    echo "  Sockets:          $SOCKETS"
    echo "  Cores/socket:     $CORES_PER_SOCKET"
    echo "  Threads/core:     $THREADS_PER_CORE"
    echo "  Total CPUs:       $TOTAL_CPUS"
    echo ""

    # Determine physical cores on socket 0 (avoid hyperthreads)
    # Use lscpu -p to get CPU,Core,Socket mapping
    SOCKET0_PHYS_CORES=()
    declare -A SEEN_CORES
    while IFS=, read -r cpu core socket rest; do
        [[ "$cpu" =~ ^# ]] && continue
        if [[ "$socket" == "0" ]] && [[ -z "${SEEN_CORES[$core]:-}" ]]; then
            SOCKET0_PHYS_CORES+=("$cpu")
            SEEN_CORES[$core]=1
        fi
    done < <(lscpu -p=CPU,Core,Socket 2>/dev/null)

    NUM_PHYS_CORES=${#SOCKET0_PHYS_CORES[@]}

    if [[ $NUM_PHYS_CORES -eq 0 ]]; then
        echo "WARNING: Could not detect socket 0 physical cores, using all CPUs" >&2
        MAX_THREADS=$((TOTAL_CPUS > 32 ? 32 : TOTAL_CPUS))
        TASKSET_PREFIX=""
        return
    fi

    # Cap at 32 threads (half a socket is representative, avoids HT effects)
    MAX_THREADS=$((NUM_PHYS_CORES > 32 ? 32 : NUM_PHYS_CORES))

    # Build CPU list for taskset (first MAX_THREADS physical cores on socket 0)
    CPU_LIST=""
    for ((i = 0; i < MAX_THREADS; i++)); do
        if [[ -n "$CPU_LIST" ]]; then
            CPU_LIST="$CPU_LIST,${SOCKET0_PHYS_CORES[$i]}"
        else
            CPU_LIST="${SOCKET0_PHYS_CORES[$i]}"
        fi
    done

    TASKSET_PREFIX="taskset -c $CPU_LIST"

    echo "  Socket 0 physical cores: $NUM_PHYS_CORES"
    echo "  Using cores: $CPU_LIST (max $MAX_THREADS threads)"
    echo ""
}

# --- Build ---

build() {
    echo "=== Building ==="
    if ! command -v gcc &>/dev/null; then
        echo "ERROR: gcc not found" >&2
        exit 1
    fi

    gcc -O2 -pthread -o "$BIN" "$SRC"
    echo "  Built: $BIN"
    echo ""
}

# --- Run benchmark ---
# TASKSET_PREFIX is either "taskset -c <cpulist>" or empty; unquoted expansion
# gives correct word splitting in both cases.

run_bench() {
    local write_pct=$1
    echo "--- Write percentage: ${write_pct}% (duration: ${DURATION}s per data point) ---"
    echo ""

    $TASKSET_PREFIX "$BIN" "$MAX_THREADS" "$DURATION" "$write_pct"
    echo ""
}

# --- Calibration: measure perf observer overhead ---
# Tests both single-thread and multi-thread scenarios since perf overhead
# can differ (per-thread sampling, context switch tracking at scale).

calibrate_perf() {
    echo "=== Calibration: measuring perf overhead ==="
    echo ""

    local threshold=10
    local pass=true

    for threads in 1 "$MAX_THREADS"; do
        echo "  --- ${threads} thread(s) (rwlock mode) ---"

        local baseline with_perf perf_data overhead
        baseline=$($TASKSET_PREFIX "$BIN" "$threads" "$DURATION" 0 rwlock-ops 2>/dev/null)

        perf_data=$(mktemp /tmp/perf_cal_XXXXXX.data)
        with_perf=$(perf record -g --call-graph dwarf -o "$perf_data" -- $TASKSET_PREFIX "$BIN" "$threads" "$DURATION" 0 rwlock-ops 2>/dev/null) || true
        rm -f "$perf_data"

        if [[ -z "$baseline" || "$baseline" == "0" ]]; then
            echo "  WARNING: calibration failed (no baseline), skipping"
            echo ""
            continue
        fi

        overhead=$(awk "BEGIN { printf \"%.1f\", (1 - $with_perf / $baseline) * 100 }")
        echo "  Baseline:    $baseline ops/s"
        echo "  With perf:   $with_perf ops/s"
        echo "  Overhead:    ${overhead}%"

        if awk "BEGIN { exit !((1 - $with_perf / $baseline) * 100 > $threshold) }"; then
            echo "  WARNING: overhead exceeds ${threshold}%"
            pass=false
        else
            echo "  OK"
        fi
        echo ""
    done

    if [[ "$pass" == "false" ]]; then
        echo "  WARNING: perf overhead exceeds ${threshold}% in one or more configurations"
        echo "  Profiling results may be distorted. Consider reducing sample frequency."
    else
        echo "  All configurations within acceptable overhead range."
    fi
    echo ""
}

# --- Collect perf profile ---
# Uses the benchmark's mode positional argument to run mutex-only or rwlock-only,
# so perf captures a clean profile of each lock type in isolation.

run_perf_profile() {
    if ! command -v perf &>/dev/null; then
        echo "=== perf not available, skipping profiling ==="
        echo ""
        return
    fi

    calibrate_perf

    echo "=== perf stat comparison (${MAX_THREADS} threads, 0% writes, ${DURATION}s) ==="
    echo ""

    echo "--- Mutex ---"
    perf stat -e cycles,instructions,context-switches,cache-misses -- $TASKSET_PREFIX "$BIN" "$MAX_THREADS" "$DURATION" 0 mutex 2>&1 | grep -E "(cycles|instructions|context-switches|cache-misses|elapsed)" || true
    echo ""

    echo "--- rwlock ---"
    perf stat -e cycles,instructions,context-switches,cache-misses -- $TASKSET_PREFIX "$BIN" "$MAX_THREADS" "$DURATION" 0 rwlock 2>&1 | grep -E "(cycles|instructions|context-switches|cache-misses|elapsed)" || true
    echo ""

    # Collect call graph profiles
    local perf_mutex_data perf_rwlock_data
    perf_mutex_data=$(mktemp /tmp/perf_mutex_XXXXXX.data)
    perf_rwlock_data=$(mktemp /tmp/perf_rwlock_XXXXXX.data)

    echo "=== perf call graph (mutex, ${MAX_THREADS} threads) ==="
    echo ""
    if perf record -g --call-graph dwarf -o "$perf_mutex_data" -- $TASKSET_PREFIX "$BIN" "$MAX_THREADS" "$DURATION" 0 mutex 2>/dev/null; then
        perf report -i "$perf_mutex_data" --stdio --children -G --percent-limit 1 2>&1 | head -80 || true
    else
        echo "  WARNING: perf record failed (permissions?), skipping call graph"
    fi
    echo ""

    echo "=== perf call graph (rwlock, ${MAX_THREADS} threads) ==="
    echo ""
    if perf record -g --call-graph dwarf -o "$perf_rwlock_data" -- $TASKSET_PREFIX "$BIN" "$MAX_THREADS" "$DURATION" 0 rwlock 2>/dev/null; then
        perf report -i "$perf_rwlock_data" --stdio --children -G --percent-limit 1 2>&1 | head -60 || true
    else
        echo "  WARNING: perf record failed (permissions?), skipping call graph"
    fi
    echo ""

    # Cleanup
    rm -f "$perf_mutex_data" "$perf_rwlock_data"
}

# --- Main ---

echo "========================================"
echo " mutex-to-rwlock verification benchmark"
echo "========================================"
echo ""

detect_topology
build

echo "=== Throughput scaling ==="
echo ""

run_bench 0
run_bench 1
run_bench 5

run_perf_profile

echo "=== Done ==="
