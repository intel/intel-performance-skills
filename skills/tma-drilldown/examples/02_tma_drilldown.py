#!/usr/bin/env python3
"""TMA Drill-Down Recommendation Workflow — Synthetic Simulation

This example demonstrates the full Top-down Microarchitecture Analysis (TMA)
drill-down workflow using the RecommendationEngine, with synthetic perf data
so it works without real hardware.

On a real system, you would:
  1. Run `perfmon-skills recommend start --platform SPR --cmd "your_workload"`
  2. Execute the generated `perf stat` command against your workload
  3. Feed the real output to `perfmon-skills recommend analyze`
  4. Repeat until a leaf node is reached

Here we simulate that loop by constructing synthetic perf JSON output at each
step. The synthetic data uses the same format that `perf stat -j` produces:
  {"counter-value": "VALUE", "event": "EVENT_NAME", "pcnt-running": 100.00}

Key insight for TMA L1 with PERF_METRICS support: the topdown-* events are
reported as raw slot counts. For example, 50% Backend_Bound with 6M total
slots means topdown-be-bound reports 3,000,000 and slots reports 6,000,000.
The formula then computes PERF_METRICS.BACKEND_BOUND / TOPDOWN.SLOTS * 100.

For L2+ metrics, the engine needs whatever events appear in each metric's
formula. Since we cannot easily construct all needed events synthetically,
we handle the case where evaluate_metric returns None gracefully and show
what the engine does at each step regardless.

Usage:
    pip install -e /path/to/perfmon-skills
    python 02_tma_drilldown.py
"""

import json
import sys
import tempfile
from pathlib import Path


def make_perf_json_line(event: str, value: float, pcnt_running: float = 100.0) -> str:
    """Create one line of perf stat JSON output."""
    return json.dumps({
        "counter-value": f"{value:.6f}",
        "event": event,
        "pcnt-running": pcnt_running,
    })


def make_perf_json_output(events: dict, pcnt_running: float = 100.0) -> str:
    """Create multi-line perf stat JSON output from event->value dict."""
    lines = []
    for event, value in events.items():
        lines.append(make_perf_json_line(event, value, pcnt_running))
    return "\n".join(lines)


def print_separator():
    print("\n" + "=" * 72 + "\n")


def print_results(result: dict, step_label: str):
    """Pretty-print analysis results."""
    print(f"  State:         {result.get('state', '?')}")
    print(f"  Step:          {result.get('step', '?')}")
    print(f"  Path so far:   {' -> '.join(result.get('path', [])) or '(none)'}")
    print(f"  Complete:      {result.get('is_complete', False)}")

    if result.get("results"):
        print(f"  Node values:")
        for r in result["results"]:
            val = f"{r['value']:.1f}%" if r["value"] is not None else "N/A (missing events)"
            thresh = ""
            if r["threshold_passed"] is True:
                thresh = " [THRESHOLD PASSED]"
            elif r["threshold_passed"] is False:
                thresh = " [below threshold]"
            print(f"    {r['name']:30s} = {val}{thresh}")

    if result.get("multiplexing_issues"):
        print(f"  Multiplexing issues:")
        for m in result["multiplexing_issues"]:
            print(f"    {m['event']}: measured {m['measured_pct']:.0f}% of time")

    if result.get("next_command"):
        print(f"  Next command:  {result['next_command'][:100]}...")
        print(f"  Next action:   {result.get('next_action', '')}")

    if result.get("guidance"):
        guidance = result["guidance"]
        if isinstance(guidance, dict):
            print(f"  Guidance:      {guidance.get('summary', guidance)}")
        else:
            print(f"  Guidance:      {guidance}")

    if result.get("sampling_suggestion"):
        ss = result["sampling_suggestion"]
        print(f"  Sampling suggestion: perf record with {ss.get('events', [])}")


def main():
    print("TMA Drill-Down Recommendation Engine — Synthetic Simulation")
    print("=" * 72)
    print()
    print("This demo walks through the iterative TMA methodology:")
    print("  L1: Identify which top-level category is the bottleneck")
    print("  L2: Drill into that category's children")
    print("  L3: Continue drilling until we reach a leaf or actionable node")
    print()
    print("All data is synthetic — no real hardware or perf tool required.")
    print()

    # Import after printing header so import errors are visible
    try:
        from perfmon_tools.recommend.engine import RecommendationEngine
    except ImportError as e:
        print(f"ERROR: Could not import RecommendationEngine: {e}")
        print("Make sure perfmon-skills is installed: pip install -e /path/to/perfmon-skills")
        sys.exit(1)

    # Use a temp directory for sessions so we don't pollute the workspace
    with tempfile.TemporaryDirectory(prefix="tma_demo_") as tmpdir:
        sessions_dir = Path(tmpdir)
        engine = RecommendationEngine(sessions_dir=sessions_dir)

        # ==================================================================
        # STEP 0: Start the investigation
        # ==================================================================
        print_separator()
        print("STEP 0: Start Investigation")
        print("-" * 40)
        print("We begin by telling the engine which platform we're analyzing.")
        print("It will generate the initial perf stat command for L1 TMA collection.")
        print()

        try:
            start_result = engine.start(platform="SPR", command="./my_workload")
        except Exception as e:
            print(f"ERROR starting investigation: {e}")
            print("Ensure PERFMON_DATA is set or ./perfmon/ symlink exists.")
            sys.exit(1)

        print(f"  Platform:      {start_result['platform']}")
        print(f"  Strategy:      {start_result['strategy']}")
        print(f"  Session dir:   {start_result['session_dir']}")
        print(f"  State:         {start_result['state']}")
        print(f"  Command:       {start_result['command'][:100]}...")
        if start_result.get("notes"):
            print(f"  Notes:")
            for note in start_result["notes"]:
                print(f"    - {note}")
        print()
        print("On a real system, you would now run the perf command above.")
        print("Here we simulate its output with synthetic data.")

        session_dir = start_result["session_dir"]

        # ==================================================================
        # STEP 1: L1 TMA — Identify top-level bottleneck
        # ==================================================================
        print_separator()
        print("STEP 1: Analyze L1 TMA Results")
        print("-" * 40)
        print()
        print("Scenario: Our workload is backend-bound (memory/compute limited).")
        print("We simulate perf output where:")
        print("  Backend_Bound  = 50%  (3M out of 6M slots)")
        print("  Frontend_Bound = 25%  (1.5M slots)")
        print("  Retiring       = 17%  (1.02M slots)")
        print("  Bad_Speculation=  8%  (0.48M slots)")
        print()
        print("With PERF_METRICS support (SPR), L1 TMA uses hardware topdown")
        print("counters that report raw slot counts. The ratio to total slots")
        print("gives the percentage.")
        print()

        # Synthetic L1 data: topdown events as raw slot counts
        # Total slots = 6,000,000
        total_slots = 6000000.0
        l1_events = {
            "topdown-be-bound": total_slots * 0.50,   # 50% Backend_Bound
            "topdown-fe-bound": total_slots * 0.25,   # 25% Frontend_Bound
            "topdown-retiring": total_slots * 0.17,   # 17% Retiring
            "topdown-bad-spec": total_slots * 0.08,   #  8% Bad_Speculation
            "slots": total_slots,
        }

        l1_perf_output = make_perf_json_output(l1_events)
        print("Synthetic perf JSON (first 3 lines):")
        for line in l1_perf_output.splitlines()[:3]:
            print(f"  {line}")
        print("  ...")
        print()

        try:
            result1 = engine.analyze(perf_output=l1_perf_output, session_dir=session_dir)
            print("Analysis results:")
            print_results(result1, "L1")
        except Exception as e:
            print(f"  Analysis raised an exception: {type(e).__name__}: {e}")
            print("  This can happen if the formula requires events beyond what we provided.")
            print("  The engine still records the step. Continuing...")
            result1 = {"path": [], "is_complete": False}

        # ==================================================================
        # STEP 2: L2 under Backend_Bound
        # ==================================================================
        print_separator()
        print("STEP 2: Drill Into Backend_Bound (L2)")
        print("-" * 40)
        print()
        print("The engine identified Backend_Bound as the top bottleneck.")
        print("Now we drill into its children: Memory_Bound vs Core_Bound.")
        print()
        print("For L2 metrics, the formulas reference specific hardware events")
        print("(not just topdown-* counters). We provide synthetic values for")
        print("the events we can guess, but some formulas may not evaluate.")
        print()

        # For L2 Backend_Bound children (Memory_Bound, Core_Bound), the formulas
        # typically reference events like MEMORY_ACTIVITY.STALLS_*, EXE_ACTIVITY.*,
        # TOPDOWN.SLOTS, etc. We provide a best-effort synthetic set.
        # The topdown-mem-bound event is available on SPR for L2.
        l2_events = {
            "topdown-mem-bound": total_slots * 0.45,   # 45% Memory_Bound (of pipeline)
            "topdown-be-bound": total_slots * 0.50,    # keep parent context
            "topdown-fe-bound": total_slots * 0.25,
            "topdown-retiring": total_slots * 0.17,
            "topdown-bad-spec": total_slots * 0.08,
            "slots": total_slots,
            # Additional events that L2 formulas might reference
            "TOPDOWN.SLOTS": total_slots,
            "PERF_METRICS.BACKEND_BOUND": total_slots * 0.50,
            "PERF_METRICS.MEMORY_BOUND": total_slots * 0.45,
            "INT_MISC.UOP_DROPPING": 0.0,
            "cpu/INT_MISC.UOP_DROPPING/": 0.0,
        }

        l2_perf_output = make_perf_json_output(l2_events)

        try:
            result2 = engine.analyze(perf_output=l2_perf_output, session_dir=session_dir)
            print("Analysis results:")
            print_results(result2, "L2")
        except Exception as e:
            print(f"  Analysis raised an exception: {type(e).__name__}: {e}")
            print("  This is expected with synthetic data — L2 formulas need many")
            print("  specific events that are hard to fake without knowing the exact")
            print("  formula structure. On real hardware, perf collects all needed events.")
            result2 = {"path": result1.get("path", []), "is_complete": False}

        # ==================================================================
        # STEP 3: L3 under Memory_Bound
        # ==================================================================
        print_separator()
        print("STEP 3: Drill Into Memory_Bound (L3)")
        print("-" * 40)
        print()
        print("If we successfully identified Memory_Bound as the L2 bottleneck,")
        print("the next step examines its children:")
        print("  DRAM_Bound, L1_Bound, L2_Bound, L3_Bound, Store_Bound, etc.")
        print()
        print("At L3, formulas get very specific to the microarchitecture.")
        print("We provide a broad set of synthetic events to maximize our chances.")
        print()

        # L3 Memory_Bound children on SPR include nodes like:
        # DRAM_Bound, L1_Bound, L2_Bound, L3_Bound, Store_Bound
        # These reference very specific events. We try our best.
        l3_events = {
            "slots": total_slots,
            "topdown-be-bound": total_slots * 0.50,
            "topdown-mem-bound": total_slots * 0.45,
            "topdown-fe-bound": total_slots * 0.25,
            "topdown-retiring": total_slots * 0.17,
            "topdown-bad-spec": total_slots * 0.08,
            "TOPDOWN.SLOTS": total_slots,
            "PERF_METRICS.BACKEND_BOUND": total_slots * 0.50,
            "PERF_METRICS.MEMORY_BOUND": total_slots * 0.45,
            # Synthetic events for L3 nodes — DRAM_Bound related
            "MEMORY_ACTIVITY.STALLS_L3_MISS": 800000.0,
            "MEMORY_ACTIVITY.STALLS_L2_MISS": 1200000.0,
            "MEMORY_ACTIVITY.STALLS_L1D_MISS": 1500000.0,
            "EXE_ACTIVITY.BOUND_ON_STORES": 200000.0,
            "CYCLE_ACTIVITY.STALLS_L3_MISS": 800000.0,
            "CYCLE_ACTIVITY.STALLS_L2_MISS": 1200000.0,
            "CYCLE_ACTIVITY.STALLS_MEM_ANY": 1800000.0,
            "CPU_CLK_UNHALTED.THREAD": 5000000.0,
            "CPU_CLK_UNHALTED.DISTRIBUTED": 5000000.0,
            "MEM_LOAD_RETIRED.L3_MISS": 50000.0,
            "MEM_LOAD_RETIRED.L2_MISS": 80000.0,
            "MEM_LOAD_RETIRED.L1_MISS": 120000.0,
            "MEM_LOAD_RETIRED.L3_HIT": 70000.0,
            "MEM_LOAD_RETIRED.L2_HIT": 100000.0,
            "OCR.ALL_RFO.L3_MISS.REMOTE_FWD": 5000.0,
            "OCR.ALL_RFO.L3_MISS.REMOTE_HITM": 3000.0,
            "INT_MISC.UOP_DROPPING": 0.0,
        }

        l3_perf_output = make_perf_json_output(l3_events)

        try:
            result3 = engine.analyze(perf_output=l3_perf_output, session_dir=session_dir)
            print("Analysis results:")
            print_results(result3, "L3")
        except Exception as e:
            print(f"  Analysis raised an exception: {type(e).__name__}: {e}")
            print("  With synthetic data, deep formula evaluation often fails because")
            print("  each L3 metric references 5-10 specific events with exact names.")
            print("  This is fine — the point is to show the workflow structure.")
            result3 = {"path": result2.get("path", []), "is_complete": False}

        # ==================================================================
        # Session Status and Summary
        # ==================================================================
        print_separator()
        print("SESSION STATUS")
        print("-" * 40)
        print()
        print("The engine tracks all state in a session directory.")
        print("At any point, you can query the current status:")
        print()

        try:
            status = engine.status(session_dir=session_dir)
            print(f"  State:     {status.get('state', '?')}")
            print(f"  Platform:  {status.get('platform', '?')}")
            print(f"  Step:      {status.get('step', 0)}")
            print(f"  Path:      {' -> '.join(status.get('path', [])) or '(none yet)'}")
            print(f"  Target:    {status.get('target', {})}")
            if status.get("findings"):
                print(f"  Findings:")
                for f in status["findings"]:
                    print(f"    L{f['level']}: {f['node']} = {f['value']:.1f}%"
                          if f.get('value') is not None
                          else f"    L{f['level']}: {f['node']} = N/A")
        except Exception as e:
            print(f"  Status query failed: {e}")

        print()
        print()
        print("SESSION SUMMARY")
        print("-" * 40)
        print()
        print("Once investigation is COMPLETE (reached a leaf), summary provides")
        print("the full bottleneck path, tuning guidance, and coverage report.")
        print("If incomplete, it shows progress so far:")
        print()

        try:
            summary = engine.summary(session_dir=session_dir)
            print(f"  {json.dumps(summary, indent=4)}")
        except Exception as e:
            print(f"  Summary query failed: {e}")

        # ==================================================================
        # Wrap-up
        # ==================================================================
        print_separator()
        print("SUMMARY OF TMA METHODOLOGY")
        print("-" * 40)
        print()
        print("The Top-down Microarchitecture Analysis (TMA) method works by:")
        print()
        print("  1. CLASSIFY: Measure L1 categories to find the dominant bottleneck")
        print("     (Frontend_Bound, Backend_Bound, Bad_Speculation, Retiring)")
        print()
        print("  2. DRILL DOWN: For the top bottleneck, measure its children")
        print("     (e.g., Backend_Bound -> Memory_Bound vs Core_Bound)")
        print()
        print("  3. REPEAT: Continue drilling until reaching a leaf node or")
        print("     actionable category (e.g., DRAM_Bound, Branch_Mispredicts)")
        print()
        print("  4. ACT: Use the LocateWith events (perf record) to find the")
        print("     exact code locations responsible for the bottleneck")
        print()
        print("The RecommendationEngine automates this loop:")
        print("  - Generates the right perf commands at each level")
        print("  - Evaluates metric formulas from collected counters")
        print("  - Applies threshold logic to identify real bottlenecks")
        print("  - Tracks session state across multiple measurement steps")
        print("  - Provides tuning guidance when a leaf is reached")
        print()
        print("On real hardware, replace the synthetic data with actual perf output")
        print("and the engine will guide you to the precise bottleneck.")
        print()
        print("Done.")


if __name__ == "__main__":
    main()
