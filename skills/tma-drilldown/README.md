# tma-drilldown

Performance analysis toolkit built on [Intel perfmon](https://github.com/intel/perfmon) data. Provides CLI tools for streamlined TMA (Top-down Microarchitecture Analysis) investigation on Intel platforms.

Part of [intel-performance-skills](https://github.com/intel/intel-performance-skills).

## What it does

- **Event/metric lookup** — Search 2600+ PMU events and 300+ TMA metrics across 50+ Intel platforms
- **Command generation** — Generate ready-to-run `perf stat` commands with counter budget awareness
- **Cross-platform comparison** — Diff events and metrics between platform generations (e.g., ICX → SPR)
- **TMA drill-down** — Iterative recommendation engine that automates the Top-down Microarchitecture Analysis methodology
- **Decision tracing** — Record and visualize every decision as an inspectable DAG (Mermaid, DOT, HTML)

## Installation

### Prerequisites

- Python 3.9+
- Linux with `perf` tool (for actual data collection; not needed for lookup/comparison/examples)

### Install from the repo

```bash
git clone --recurse-submodules https://github.com/intel/intel-performance-skills.git
cd intel-performance-skills/skills/tma-drilldown
pip install -e .
```

If you already cloned without `--recurse-submodules`:

```bash
git submodule update --init skills/tma-drilldown/perfmon
```

Alternatively, set the `PERFMON_DATA` environment variable to point to any local clone of `intel/perfmon`.

### Verify installation

```bash
perfmon-skills --help
perfmon-skills lookup "cache miss" --platform SPR
```

## Quick Start

### Search for events

```bash
$ perfmon-skills lookup "cache miss" --platform SPR
======================================================================
EVENTS (SPR) — 67 matches
======================================================================
  L2_RQSTS.DEMAND_DATA_RD_MISS
    Demand Data Read miss L2 cache
    Code: 0x24, UMask: 0x21, Counter: 0,1,2,3, PEBS: 0
  ...
```

### Generate perf commands

```bash
$ perfmon-skills cmdgen --tma-level 1 --platform SPR
# TMA Level 1 (4 nodes) [SPR]
# Events: 6 (GP: 1, Fixed: 0, PerfMetrics: 5)
# Counters available: 12 (GP: 8)

perf stat -e cpu/INT_MISC.UOP_DROPPING/,topdown-be-bound,topdown-bad-spec,topdown-fe-bound,topdown-retiring,slots sleep 5
```

### Compare platforms

```bash
$ perfmon-skills compare ICX SPR --type metrics
======================================================================
ICX → SPR Comparison (metrics)
======================================================================
  Added metrics:   26
  Removed metrics: 8
  Changed metrics: 91
```

### Run a guided investigation

```bash
$ perfmon-skills recommend start --platform SPR --cmd "./my_workload"
======================================================================
NEW INVESTIGATION SESSION
======================================================================
  Platform: SPR
  Counter budget: ...

  STEP 1: Run this command and feed the output back:

  perf stat -j -e cpu/INT_MISC.UOP_DROPPING/,topdown-be-bound,... -- ./my_workload

$ perf stat -j -e <events> -- ./my_workload 2> step1.txt
$ perfmon-skills recommend analyze --input step1.txt
======================================================================
ANALYSIS — Step 1 [COLLECTING]
======================================================================
  Path: Backend_Bound

  Node Values:
    Backend_Bound                      50.0% ◀ BOTTLENECK
    Frontend_Bound                     25.0%
    Retiring                           17.0%
    Bad_Speculation                     8.0%

  NEXT STEP: Run this command:
  perf stat -j -e ... -- ./my_workload
```

Repeat until the investigation reaches a leaf node with tuning guidance.

### Visualize decision trace

```bash
$ PERFMON_TRACE=1 perfmon-skills recommend start --platform SPR --cmd "./workload"
# ... run investigation steps ...
$ perfmon-skills trace --last --format mermaid
graph TD
    d001[start_investigation\nSelected SPR platform]
    d002[evaluate_l1\nBackend_Bound=50%]
    d001 -->|L1 TMA computed| d002
    d003[select_bottleneck\nChose Backend_Bound]
    d002 -->|threshold passed| d003
    ...
```

## Examples

The `examples/` directory contains runnable scripts that work without real hardware:

```bash
bash examples/01_quick_start.sh          # All CLI commands at a glance
python examples/02_tma_drilldown.py      # Full iterative drill-down with synthetic data
python examples/03_trace_visualization.py # Decision tracing in all 4 formats
python examples/04_perf_output_parsing.py # Perf output parsing and event normalization
```

See [examples/README.md](examples/README.md) for captured output from each example.

## Architecture

```
skills/tma-drilldown/
├── perfmon/                  # git submodule → intel/perfmon data
├── src/perfmon_tools/
│   ├── core/                 # Platform detection, catalog, TMA tree, formula eval,
│   │                         # perf output parsing, context budget, decision tracing
│   ├── lookup/               # Event/metric search
│   ├── cmdgen/               # Perf command generation
│   ├── compare/              # Cross-platform diff
│   ├── recommend/            # TMA drill-down engine (state machine, session mgmt,
│   │                         # coverage tracking, tuning guidance)
│   └── cli/                  # CLI entry points (lookup, cmdgen, compare, recommend, trace)
├── references/               # Detailed slash command docs
├── examples/                 # Runnable demos with output
└── tests/                    # Test suite (pytest)
```

### Design principles

- **Zero mandatory dependencies** — stdlib only (`json`, `csv`, `re`, `pathlib`, `subprocess`)
- **Deterministic core** — TMA drill-down runs without an LLM; the LLM layer (SKILL.md) adds interpretation
- **Context budget aware** — raw perf output stays on disk; only compact findings (~200 tokens/step) flow between steps
- **Counter budget aware** — knows platform-specific counter counts (SPR: 8 GP + 4 fixed) to minimize multiplexing
- **Session persistence** — plain JSON files, trivially inspectable and shareable

## Supported Platforms

All platforms in Intel's perfmon repository are supported, including:

| Platform | Codename | TMA Levels |
|----------|----------|------------|
| SPR | Sapphire Rapids | 6 |
| EMR | Emerald Rapids | 6 |
| GNR | Granite Rapids | 6 |
| ICX | Ice Lake Server | 5 |
| SKX/CLX | Skylake/Cascade Lake | 4 |
| ADL/RPL | Alder Lake/Raptor Lake (hybrid) | 5 |
| MTL/ARL | Meteor Lake/Arrow Lake (hybrid) | 5 |

And 40+ more. Run `perfmon-skills lookup --cross-arch "your_query"` to search across all.

## Development

```bash
cd skills/tma-drilldown
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## License

MIT — see [COPYRIGHT.md](../../COPYRIGHT.md)
