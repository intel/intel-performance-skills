# Examples

Runnable examples demonstrating each aspect of perfmon-skills. All examples work without real hardware.

## Prerequisites

```bash
pip install -e .
# Ensure ./perfmon symlink or PERFMON_DATA points to intel/perfmon repo
```

## Files

| Example | What it shows |
|---------|---------------|
| `01_quick_start.sh` | All 5 CLI commands in action |
| `02_tma_drilldown.py` | Full TMA drill-down workflow step-by-step |
| `03_trace_visualization.py` | Decision tracing DAG in all 4 output formats |
| `04_perf_output_parsing.py` | Parsing perf stat output (text + JSON) and event name normalization |

---

## Example 1: Quick Start (`01_quick_start.sh`)

Shows all CLI subcommands with SPR (Sapphire Rapids) platform data.

```bash
bash examples/01_quick_start.sh
```

<details>
<summary>Output (click to expand)</summary>

```
=== 1. Event/Metric Search ===

======================================================================
EVENTS (SPR) — 67 matches
======================================================================
  OFFCORE_REQUESTS_OUTSTANDING.L3_MISS_DEMAND_DATA_RD
    For every cycle, increments by the number of demand data read requests pending t
    Code: 0x20, UMask: 0x10, Counter: 0,1,2,3, PEBS: 0

  OFFCORE_REQUESTS.L3_MISS_DEMAND_DATA_RD
    Counts demand data read requests that miss the L3 cache.
    Code: 0x21, UMask: 0x10, Counter: 0,1,2,3, PEBS: 0

  L2_RQSTS.DEMAND_DATA_RD_MISS
    Demand Data Read miss L2 cache
    Code: 0x24, UMask: 0x21, Counter: 0,1,2,3, PEBS: 0
  ...
  (67 events matching "cache miss")

=== 2. TMA Metric Lookup ===

======================================================================
METRICS (SPR) — 1 matches
======================================================================
  Backend_Bound (L1, TMA)
    This category represents fraction of slots where no uops are being delivered due
    Unit: percent
    Groups: BvOB;TmaL1

=== 3. Generate L1 TMA Command ===

# TMA Level 1 (4 nodes) [SPR]
# Events: 6 (GP: 1, Fixed: 0, PerfMetrics: 5)
# Counters available: 12 (GP: 8)

perf stat -e cpu/INT_MISC.UOP_DROPPING/,topdown-be-bound,topdown-bad-spec,topdown-fe-bound,topdown-retiring,slots sleep 5

=== 4. Generate Drill-Down Command ===

# TMA drill-down: Backend_Bound → ['Core_Bound', 'Memory_Bound'] [SPR]
# Events: 5 (GP: 0, Fixed: 0, PerfMetrics: 5)
# Counters available: 12 (GP: 8)

perf stat -e topdown-be-bound,topdown-bad-spec,topdown-fe-bound,topdown-mem-bound,topdown-retiring -p <PID> sleep 5

=== 5. Cross-Platform Comparison ===

======================================================================
ICX → SPR Comparison (metrics)
======================================================================
  Added metrics:   26
  Removed metrics: 8
  Changed metrics: 91
  ...

=== 6. Guided Investigation ===

======================================================================
NEW INVESTIGATION SESSION
======================================================================
  Platform: SPR
  Strategy: ...
  Counter budget: ...

  STEP 1: Run this command and feed the output back:

  perf stat -j -e cpu/INT_MISC.UOP_DROPPING/,topdown-be-bound,... -- sleep 2
```

</details>

---

## Example 2: TMA Drill-Down (`02_tma_drilldown.py`)

Demonstrates the iterative recommendation engine with synthetic perf data. Shows a 3-step investigation from L1 to L3.

```bash
python examples/02_tma_drilldown.py
```

<details>
<summary>Output (click to expand)</summary>

```
TMA Drill-Down Recommendation Engine — Synthetic Simulation
========================================================================

This demo walks through the iterative TMA methodology:
  L1: Identify which top-level category is the bottleneck
  L2: Drill into that category's children
  L3: Continue drilling until we reach a leaf or actionable node

All data is synthetic — no real hardware or perf tool required.


========================================================================

STEP 0: Start Investigation
----------------------------------------
We begin by telling the engine which platform we're analyzing.
It will generate the initial perf stat command for L1 TMA collection.

  Platform:      SPR
  Strategy:      {'smt_active': True, 'use_perf_metrics': True, 'counters': '8 GP + 4 fixed'}
  Session dir:   /tmp/tma_demo_.../2026-05-19_..._cmd
  State:         COLLECTING
  Command:       perf stat -j -e cpu/INT_MISC.UOP_DROPPING/,topdown-be-bound,...
  Notes:
    - SMT active: using per-thread events. Cross-thread interference may affect L3+ accuracy.
    - PERF_METRICS supported: L1/L2 TMA available without multiplexing.

On a real system, you would now run the perf command above.
Here we simulate its output with synthetic data.

========================================================================

STEP 1: Analyze L1 TMA Results
----------------------------------------

Scenario: Our workload is backend-bound (memory/compute limited).
We simulate perf output where:
  Backend_Bound  = 50%  (3M out of 6M slots)
  Frontend_Bound = 25%  (1.5M slots)
  Retiring       = 17%  (1.02M slots)
  Bad_Speculation=  8%  (0.48M slots)

Analysis results:
  State:         COLLECTING
  Step:          1
  Path so far:   Backend_Bound
  Complete:      False
  Node values:
    Backend_Bound                  = 50.0% [THRESHOLD PASSED]
    Retiring                       = 17.0%
    Frontend_Bound                 = N/A (missing events)
    Bad_Speculation                = N/A (missing events)
  Next command:  perf stat -j -e topdown-be-bound,topdown-bad-spec,...
  Next action:   Run the command, then feed output to 'recommend analyze'

========================================================================

STEP 2: Drill Into Backend_Bound (L2)
----------------------------------------

The engine identified Backend_Bound as the top bottleneck.
Now we drill into its children: Memory_Bound vs Core_Bound.

Analysis results:
  State:         COLLECTING
  Step:          2
  Path so far:   Backend_Bound -> Memory_Bound
  Complete:      False
  Node values:
    Memory_Bound                   = 45.0%
    Core_Bound                     = N/A (missing events)
  Next command:  perf stat -j -e cpu/CPU_CLK_UNHALTED.THREAD/,...
  Next action:   Run the command, then feed output to 'recommend analyze'

========================================================================

STEP 3: Drill Into Memory_Bound (L3)
----------------------------------------

Analysis results:
  State:         COLLECTING
  Step:          3
  Path so far:   Backend_Bound -> Memory_Bound -> DRAM_Bound
  Complete:      False
  Node values:
    DRAM_Bound                     = 16.0%
    L3_Bound                       = 8.0%
    L2_Bound                       = 6.0%
    Store_Bound                    = 4.0%
    L1_Bound                       = N/A (missing events)
  Next command:  perf stat -j -e cpu/CPU_CLK_UNHALTED.THREAD/,...
  Next action:   Run the command, then feed output to 'recommend analyze'

========================================================================

SESSION STATUS
----------------------------------------

  State:     COLLECTING
  Platform:  SPR
  Step:      3
  Path:      Backend_Bound -> Memory_Bound -> DRAM_Bound
  Target:    {'pid': None, 'command': './my_workload'}
  Findings:
    L1: Backend_Bound = 50.0%
    L2: Memory_Bound = 45.0%
    L3: DRAM_Bound = 16.0%

========================================================================

SUMMARY OF TMA METHODOLOGY
----------------------------------------

The Top-down Microarchitecture Analysis (TMA) method works by:

  1. CLASSIFY: Measure L1 categories to find the dominant bottleneck
     (Frontend_Bound, Backend_Bound, Bad_Speculation, Retiring)

  2. DRILL DOWN: For the top bottleneck, measure its children
     (e.g., Backend_Bound -> Memory_Bound vs Core_Bound)

  3. REPEAT: Continue drilling until reaching a leaf node or
     actionable category (e.g., DRAM_Bound, Branch_Mispredicts)

  4. ACT: Use the LocateWith events (perf record) to find the
     exact code locations responsible for the bottleneck
```

</details>

---

## Example 3: Trace Visualization (`03_trace_visualization.py`)

Shows the decision tracing DAG — every automated decision recorded with inputs, alternatives, and confidence levels. Outputs in JSON, Mermaid, DOT, and interactive HTML.

```bash
python examples/03_trace_visualization.py
```

<details>
<summary>Output (click to expand)</summary>

```
========================================================================
  perfmon-skills: Decision Tracing & Visualization Demo
========================================================================

This example builds a realistic decision trace representing a TMA
drill-down from L1 metrics down to a DRAM_Bound leaf node, then
renders the trace in all supported output formats.

Trace contains 7 decision nodes
Root nodes: ['d001']

------------------------------------------------------------------------
  FORMAT 1: JSON (first 20 lines)
------------------------------------------------------------------------

The JSON format captures the full DAG with all metadata.
Suitable for programmatic analysis or session replay.

  {
    "trace_version": "1.0",
    "nodes": [
      {
        "id": "d001",
        "timestamp": "2026-05-19T15:50:57",
        "actor": "system",
        "operation": "start_investigation",
        "inputs": {
          "cpu_family": 6,
          "model": "0x8F",
          "detected": "SPR"
        },
        "reasoning": "Detected Intel Sapphire Rapids via /proc/cpuinfo family=6 model=0x8F",
        "decision": "Selected SPR platform with 8 GP + 4 fixed counters",
        "alternatives": [],
        "confidence": 1.0,
        ...
  ... (207 total lines)

------------------------------------------------------------------------
  FORMAT 2: Mermaid Flowchart
------------------------------------------------------------------------

Mermaid diagrams render in GitHub markdown, Obsidian, and mermaid.live.
Paste this into any Mermaid-compatible viewer to see the DAG.

  graph TD
      d001[start_investigation\nSelected SPR platform with 8 GP + 4 fixed counters]
      d002[evaluate_l1\nBackend_Bound is the dominant bottleneck at 50%]
      d001 -->|All L1 TMA metrics computed from topdown| d002
      d003[select_bottleneck\nChose Backend_Bound for drill-down]
      d002 -->|Backend_Bound exceeds threshold (50% > 2| d003
      d004[evaluate_l2\nMemory_Bound=45%, Core_Bound=15% under Backend_Bound]
      d003 -->|L2 children of Backend_Bound evaluated; | d004
      d005[select_bottleneck\nChose Memory_Bound for drill-down]
      d004 -->|Memory_Bound is 3x higher than Core_Boun| d005
      d006[evaluate_l3\nDRAM_Bound=30% is the leaf bottleneck (no further children)]
      d005 -->|DRAM_Bound dominates at 30%; cache level| d006
      d007[generate_guidance\nTuning advice: optimize data locality, consider prefetching, check NUMA placement]
      d006 -->|DRAM_Bound leaf reached; generating plat| d007

------------------------------------------------------------------------
  FORMAT 3: Graphviz DOT
------------------------------------------------------------------------

DOT format for Graphviz. Render with: dot -Tpng trace.dot -o trace.png
Colors indicate actor: blue=system, yellow=human, green=ai

  digraph trace {
      rankdir=TB;
      node [shape=box, style=rounded];
      "d001" [label="start_investigation\n...\nconf=1.00", fillcolor="lightblue", ...];
      "d002" [label="evaluate_l1\n...\nconf=0.95", fillcolor="lightblue", ...];
      ...
      "d007" [label="generate_guidance\n...\nconf=0.85", fillcolor="lightgreen", ...];
      "d001" -> "d002" [label="All L1 TMA metrics computed fr"];
      "d002" -> "d003" [label="Backend_Bound exceeds threshol"];
      ...
  }

------------------------------------------------------------------------
  FORMAT 4: Interactive HTML
------------------------------------------------------------------------

  Saved interactive HTML to: /tmp/perfmon_trace_example.html
  Open in a browser to explore the decision tree interactively.

  Saved Mermaid diagram to: /tmp/perfmon_trace_example.mmd

========================================================================
  Summary
========================================================================

Decision path taken:
  start_investigation (SPR)
    -> evaluate_l1 (Backend_Bound=50%)
      -> select_bottleneck (Backend_Bound)
        -> evaluate_l2 (Memory_Bound=45%)
          -> select_bottleneck (Memory_Bound)
            -> evaluate_l3 (DRAM_Bound=30%)
              -> generate_guidance (optimize data locality)

To enable tracing in your own workflows:
  PERFMON_TRACE=1 perfmon-skills recommend start --platform SPR
```

</details>

---

## Example 4: Perf Output Parsing (`04_perf_output_parsing.py`)

Shows how perfmon-skills parses perf stat output in all formats (text, JSON, interval) and translates between perf event names and Intel perfmon canonical names.

```bash
python examples/04_perf_output_parsing.py
```

<details>
<summary>Output (click to expand)</summary>

```
======================================================================
  1. Parsing perf stat TEXT output
======================================================================

Parsed event values:
  cache-misses                             = 12,345,678
  cache-references                         = 345,678,901
  cycles                                   = 4,521,345,678
  instructions                             = 2,890,123,456

Duration: 2.501 seconds

Multiplexing issues (2):
  cycles: measured only 66.5% of time
  branch-misses: not counted

======================================================================
  2. Parsing perf stat JSON output (-j flag)
======================================================================

Parsed event values (raw perf names):
  cpu/INT_MISC.UOP_DROPPING/               = 45,000
  slots                                    = 6,000,000
  topdown-bad-spec                         = 500,000
  topdown-be-bound                         = 3,000,000
  topdown-fe-bound                         = 1,500,000
  topdown-retiring                         = 1,000,000

======================================================================
  3. Event Name Normalization (perf → perfmon)
======================================================================

The PERF_TO_PERFMON mapping:
  slots                          → TOPDOWN.SLOTS
  topdown-bad-spec               → PERF_METRICS.BAD_SPECULATION
  topdown-be-bound               → PERF_METRICS.BACKEND_BOUND
  topdown-br-mispredict          → PERF_METRICS.BRANCH_MISPREDICTS
  topdown-fe-bound               → PERF_METRICS.FRONTEND_BOUND
  topdown-fetch-lat              → PERF_METRICS.FETCH_LATENCY
  topdown-heavy-ops              → PERF_METRICS.HEAVY_OPS
  topdown-mem-bound              → PERF_METRICS.MEMORY_BOUND
  topdown-retiring               → PERF_METRICS.RETIRING

After normalization (all available names):
  INT_MISC.UOP_DROPPING                         = 45,000
  PERF_METRICS.BACKEND_BOUND                    = 3,000,000
  PERF_METRICS.BAD_SPECULATION                  = 500,000
  PERF_METRICS.FRONTEND_BOUND                   = 1,500,000
  PERF_METRICS.RETIRING                         = 1,000,000
  TOPDOWN.SLOTS                                 = 6,000,000
  cpu/INT_MISC.UOP_DROPPING/                    = 45,000
  slots                                         = 6,000,000
  topdown-bad-spec                              = 500,000
  topdown-be-bound                              = 3,000,000
  topdown-fe-bound                              = 1,500,000
  topdown-retiring                              = 1,000,000

Key insight: both 'topdown-be-bound' AND 'PERF_METRICS.BACKEND_BOUND'
now resolve to the same value. Metric formulas can use either name.

======================================================================
  4. Auto-detection with parse_auto()
======================================================================

parse_auto() detects format AND normalizes event names in one call.
It checks if input starts with '{' (JSON) or not (text).

Detected format: JSON
Events parsed: 12
Includes normalized names: True

======================================================================
  5. Parsing interval mode output (-I flag)
======================================================================

Parsed 3 intervals:

  Interval         cycles   instructions   cache-misses    IPC
  ---------- ------------ -------------- -------------- ------
  1             5,000,000      2,500,000         50,000   0.50
  2             5,100,000      2,600,000         48,000   0.51
  3             5,200,000      2,700,000         52,000   0.52

Interval mode is used for phase detection — if IPC varies significantly
across intervals, the workload has multiple phases that need separate analysis.

======================================================================
  6. Multiplexing Detection
======================================================================

When more events are requested than available hardware counters,
perf time-shares (multiplexes) them. This introduces statistical error.

Detection results:
  ⚠ instructions: measured only 85.5% of time
  ⚠ cache-misses: measured only 42.3% of time
  ⚠ branch-misses: not counted

Threshold: events measured < 90% of time are flagged.
Action: reduce event count or split into multiple runs.
```

</details>

---

## On Real Hardware (Intel)

```bash
# Start an actual investigation
perfmon-skills recommend start --cmd "./my_workload"

# Run the suggested perf command, then:
perf stat -j -e <events from above> -- ./my_workload 2> perf_out.txt
perfmon-skills recommend analyze --input perf_out.txt

# Repeat until investigation completes (typically 3-5 steps)

# View the decision trace
PERFMON_TRACE=1 perfmon-skills recommend start --cmd "./my_workload"
# ... run investigation ...
perfmon-skills trace --last --format html --output trace.html
```
