#!/usr/bin/env python3
"""Demonstrate the decision tracing and visualization feature of perfmon-skills.

Decision tracing records every decision point in an investigation (whether automated
or human-guided) with full context -- inputs, reasoning, alternatives considered,
confidence level -- forming an inspectable DAG (Directed Acyclic Graph).

This is invaluable for:
- Understanding WHY the tool chose a particular drill-down path
- Reproducing and auditing analysis sessions
- Debugging incorrect bottleneck identification
- Visualizing the exploration tree in multiple formats (JSON, Mermaid, DOT, HTML)

When enabled via PERFMON_TRACE=1, every decision made by the recommendation engine
is captured with zero overhead when disabled.
"""

import os
import sys
import time

# Set tracing env var BEFORE importing the tracer module, since TRACE_ENABLED
# is evaluated at import time.
os.environ["PERFMON_TRACE"] = "1"

# Add the source tree to the path so we can import perfmon_tools
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from perfmon_tools.core.tracer import Trace, DecisionNode


def build_example_trace() -> Trace:
    """Build a realistic trace simulating a TMA drill-down investigation.

    This demonstrates how the recommendation engine records its decisions
    as it drills from L1 (Frontend/Backend/Retiring/BadSpeculation) down
    to a specific leaf node with tuning guidance.
    """
    trace = Trace()

    # Decision 1: Start the investigation (root node)
    node1 = DecisionNode(
        id="d001",
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        actor="system",
        operation="start_investigation",
        inputs={"cpu_family": 6, "model": "0x8F", "detected": "SPR"},
        reasoning="Detected Intel Sapphire Rapids via /proc/cpuinfo family=6 model=0x8F",
        decision="Selected SPR platform with 8 GP + 4 fixed counters",
        alternatives=[],
        confidence=1.0,
        parent_id=None,
        children_ids=[],
        metadata={"counters_gp": 8, "counters_fixed": 4, "tma_depth": 6},
        duration_ms=12.3,
    )
    trace._add_node(node1)

    # Decision 2: Evaluate L1 metrics
    node2 = DecisionNode(
        id="d002",
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        actor="system",
        operation="evaluate_l1",
        inputs={
            "PERF_METRICS.FRONTEND_BOUND": 0.25,
            "PERF_METRICS.BACKEND_BOUND": 0.50,
            "PERF_METRICS.RETIRING": 0.17,
            "PERF_METRICS.BAD_SPECULATION": 0.08,
        },
        reasoning="All L1 TMA metrics computed from topdown slots; Backend_Bound highest at 50%",
        decision="Backend_Bound is the dominant bottleneck at 50%",
        alternatives=[],
        confidence=0.95,
        parent_id="d001",
        children_ids=[],
        metadata={"threshold": 0.20, "level": 1},
        duration_ms=3.1,
    )
    trace._add_node(node2)

    # Decision 3: Select the L1 bottleneck to drill into
    node3 = DecisionNode(
        id="d003",
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        actor="system",
        operation="select_bottleneck",
        inputs={"candidates": ["Backend_Bound=50%", "Frontend_Bound=25%", "Retiring=17%"]},
        reasoning="Backend_Bound exceeds threshold (50% > 20%) and is highest among L1 nodes",
        decision="Chose Backend_Bound for drill-down",
        alternatives=[
            {"option": "Frontend_Bound", "reason_rejected": "25% is below Backend_Bound's 50%"},
            {"option": "Retiring", "reason_rejected": "17% below threshold, not a bottleneck"},
        ],
        confidence=0.95,
        parent_id="d002",
        children_ids=[],
        metadata={"selected_value": 0.50, "next_level": 2},
        duration_ms=1.5,
    )
    trace._add_node(node3)

    # Decision 4: Evaluate L2 metrics under Backend_Bound
    node4 = DecisionNode(
        id="d004",
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        actor="system",
        operation="evaluate_l2",
        inputs={
            "MEMORY_BOUND": 0.45,
            "CORE_BOUND": 0.15,
        },
        reasoning="L2 children of Backend_Bound evaluated; Memory_Bound=45%, Core_Bound=15%",
        decision="Memory_Bound=45%, Core_Bound=15% under Backend_Bound",
        alternatives=[],
        confidence=0.92,
        parent_id="d003",
        children_ids=[],
        metadata={"parent_node": "Backend_Bound", "level": 2},
        duration_ms=4.2,
    )
    trace._add_node(node4)

    # Decision 5: Select L2 bottleneck
    node5 = DecisionNode(
        id="d005",
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        actor="system",
        operation="select_bottleneck",
        inputs={"candidates": ["Memory_Bound=45%", "Core_Bound=15%"]},
        reasoning="Memory_Bound is 3x higher than Core_Bound and exceeds threshold",
        decision="Chose Memory_Bound for drill-down",
        alternatives=[
            {"option": "Core_Bound", "reason_rejected": "15% is significantly lower than Memory_Bound's 45%"},
        ],
        confidence=0.93,
        parent_id="d004",
        children_ids=[],
        metadata={"selected_value": 0.45, "next_level": 3},
        duration_ms=1.2,
    )
    trace._add_node(node5)

    # Decision 6: Evaluate L3 metrics under Memory_Bound
    node6 = DecisionNode(
        id="d006",
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        actor="system",
        operation="evaluate_l3",
        inputs={
            "DRAM_BOUND": 0.30,
            "L1_BOUND": 0.05,
            "L2_BOUND": 0.04,
            "L3_BOUND": 0.06,
        },
        reasoning="DRAM_Bound dominates at 30%; cache levels (L1/L2/L3) are minor contributors",
        decision="DRAM_Bound=30% is the leaf bottleneck (no further children)",
        alternatives=[],
        confidence=0.90,
        parent_id="d005",
        children_ids=[],
        metadata={"parent_node": "Memory_Bound", "level": 3, "is_leaf": True},
        duration_ms=3.8,
    )
    trace._add_node(node6)

    # Decision 7: Generate tuning guidance for the identified leaf
    node7 = DecisionNode(
        id="d007",
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        actor="ai",
        operation="generate_guidance",
        inputs={"leaf_node": "DRAM_Bound", "value": 0.30, "platform": "SPR"},
        reasoning="DRAM_Bound leaf reached; generating platform-specific tuning advice",
        decision="Tuning advice: optimize data locality, consider prefetching, check NUMA placement",
        alternatives=[
            {"option": "suggest_hardware_upgrade", "reason_rejected": "SW optimization not yet exhausted"},
        ],
        confidence=0.85,
        parent_id="d006",
        children_ids=[],
        metadata={
            "guidance_keys": ["data_locality", "prefetch", "numa_placement"],
            "applicable_tools": ["numactl", "perf mem", "Intel VTune"],
        },
        duration_ms=8.7,
    )
    trace._add_node(node7)

    return trace


def main():
    print("=" * 72)
    print("  perfmon-skills: Decision Tracing & Visualization Demo")
    print("=" * 72)
    print()
    print("This example builds a realistic decision trace representing a TMA")
    print("drill-down from L1 metrics down to a DRAM_Bound leaf node, then")
    print("renders the trace in all supported output formats.")
    print()

    # Build the trace
    trace = build_example_trace()

    print(f"Trace contains {len(trace.nodes)} decision nodes")
    print(f"Root nodes: {trace.root_ids}")
    print()

    # --- JSON Output ---
    print("-" * 72)
    print("  FORMAT 1: JSON (first 20 lines)")
    print("-" * 72)
    print()
    print("The JSON format captures the full DAG with all metadata.")
    print("Suitable for programmatic analysis or session replay.")
    print()
    json_output = trace.to_json()
    json_lines = json_output.split("\n")
    for line in json_lines[:20]:
        print(f"  {line}")
    print(f"  ... ({len(json_lines)} total lines)")
    print()

    # --- Mermaid Output ---
    print("-" * 72)
    print("  FORMAT 2: Mermaid Flowchart")
    print("-" * 72)
    print()
    print("Mermaid diagrams render in GitHub markdown, Obsidian, and mermaid.live.")
    print("Paste this into any Mermaid-compatible viewer to see the DAG.")
    print()
    mermaid_output = trace.to_mermaid()
    for line in mermaid_output.split("\n"):
        print(f"  {line}")
    print()

    # --- DOT Output ---
    print("-" * 72)
    print("  FORMAT 3: Graphviz DOT")
    print("-" * 72)
    print()
    print("DOT format for Graphviz. Render with: dot -Tpng trace.dot -o trace.png")
    print("Colors indicate actor: blue=system, yellow=human, green=ai")
    print()
    dot_output = trace.to_dot()
    for line in dot_output.split("\n"):
        print(f"  {line}")
    print()

    # --- HTML Output ---
    print("-" * 72)
    print("  FORMAT 4: Interactive HTML")
    print("-" * 72)
    print()
    print("Self-contained HTML with a clickable tree view. Each node expands")
    print("to show inputs, reasoning, alternatives, and metadata.")
    print()

    html_output = trace.to_html()
    html_path = "/tmp/perfmon_trace_example.html"
    with open(html_path, "w") as f:
        f.write(html_output)
    print(f"  Saved interactive HTML to: {html_path}")
    print(f"  Open in a browser to explore the decision tree interactively.")
    print()

    # --- Save Mermaid file ---
    mermaid_path = "/tmp/perfmon_trace_example.mmd"
    with open(mermaid_path, "w") as f:
        f.write(mermaid_output)
    print(f"  Saved Mermaid diagram to: {mermaid_path}")
    print(f"  View at https://mermaid.live or in any compatible markdown viewer.")
    print()

    # --- Summary ---
    print("=" * 72)
    print("  Summary")
    print("=" * 72)
    print()
    print("Decision path taken:")
    print("  start_investigation (SPR)")
    print("    -> evaluate_l1 (Backend_Bound=50%)")
    print("      -> select_bottleneck (Backend_Bound)")
    print("        -> evaluate_l2 (Memory_Bound=45%)")
    print("          -> select_bottleneck (Memory_Bound)")
    print("            -> evaluate_l3 (DRAM_Bound=30%)")
    print("              -> generate_guidance (optimize data locality)")
    print()
    print("To enable tracing in your own workflows:")
    print("  PERFMON_TRACE=1 perfmon-skills recommend start --platform SPR")
    print()
    print("The trace will be saved automatically and can be visualized with:")
    print("  perfmon-skills trace show --format html")
    print()


if __name__ == "__main__":
    main()
