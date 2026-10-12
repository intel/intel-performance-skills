---
name: tma-drilldown
description: >-
  Intel Top-down Microarchitecture Analysis (TMA) iterative drill-down
  using hardware performance counters. Provides PMU event lookup across
  50+ Intel platforms (2600+ events, 300+ TMA metrics), counter-budget-aware
  perf stat command generation, cross-platform event/metric comparison,
  and deterministic TMA bottleneck identification from L1 (Frontend_Bound,
  Backend_Bound, Bad_Speculation, Retiring) through L6 leaf nodes with
  tuning guidance. Trigger on: TMA, Top-down Microarchitecture Analysis,
  PMU event, performance counter, perf stat events, Frontend_Bound,
  Backend_Bound, Bad_Speculation, Retiring, Memory_Bound, Core_Bound,
  counter budget, multiplexing, event lookup, platform comparison, ICX,
  SPR, EMR, GNR, SKX, CLX, ADL, RPL, MTL, perfmon, topdown, drill-down,
  bottleneck identification, which events to collect, what counters to use,
  TMA level, perf metrics.
---

<!-- (C) 2026 Intel Corporation, MIT license -->


# TMA drill-down skill

Automate Intel's Top-down Microarchitecture Analysis methodology to identify performance bottlenecks at the microarchitectural level. This skill provides a deterministic, counter-budget-aware engine that walks the TMA tree from L1 through L6 leaf nodes — no LLM needed for the core logic.

The skill is organized into four parts:
- **Part 1: Setup** — installation and perfmon data
- **Part 2: Workflows** — four main capabilities (lookup, cmdgen, compare, recommend)
- **Part 3: Cross-skill integration** — how this skill complements `linux-perf` and `performance-patterns`
- **Part 4: Architecture** — two-layer design and key data flow

---

# Part 1: Setup

## Install the CLI

```bash
cd skills/tma-drilldown
pip install -e .
```

## Perfmon data

The skill requires Intel's perfmon event/metric data. Options:
1. Initialize the included submodule: `git submodule update --init skills/tma-drilldown/perfmon`
2. Set `PERFMON_DATA` env var pointing to any local clone of `intel/perfmon`

## Verify

```bash
perfmon-skills --help
perfmon-skills lookup "cache miss" --platform SPR
```

---

# Part 2: Workflows

## A: Event/metric lookup

Search 2600+ PMU events and 300+ TMA metrics across all Intel platforms.

```bash
perfmon-skills lookup "<query>" --format json [--platform PLT] [--type events|metrics] [--level N]
```

Use when the user asks "what events measure X?" or "is there a metric for Y?"

See `references/perf-lookup.md` for full options.

## B: Perf command generation

Generate ready-to-run `perf stat` commands with counter budget awareness (knows GP/fixed counter counts per platform to minimize multiplexing).

```bash
perfmon-skills cmdgen --format json --tma-level N --platform PLT [--cmd CMD] [--pid PID]
```

Use when the user asks "what perf command should I run?" or "give me the events for TMA level 2."

See `references/perf-cmdgen.md` for full options.

## C: Cross-platform comparison

Diff events and metrics between platform generations to understand what changed.

```bash
perfmon-skills compare PLATFORM1 PLATFORM2 --format json [--type events|metrics]
```

Use when the user asks "what's different between ICX and SPR?" or "did this metric change?"

See `references/perf-compare.md` for full options.

## D: TMA drill-down investigation (primary workflow)

Iterative state-machine engine that automates the full TMA methodology:

1. **Start**: detect platform, run preflight checks, generate L1 perf command
2. **Collect + Analyze** (iterate): user runs perf, feeds output; engine evaluates thresholds, picks bottleneck branch, generates next perf command
3. **Complete**: reaches leaf node, provides tuning guidance and coverage report

```bash
# Start investigation
perfmon-skills recommend start --format json --platform SPR --cmd "./workload"

# Feed perf output
perfmon-skills recommend analyze --input perf_output.txt --format json

# Check status
perfmon-skills recommend status --format json
```

Typically takes 3-5 iterations (L1 → leaf). Each step produces a compact finding (~200 tokens); raw perf data stays on disk.

See `references/perf-recommend.md` for full workflow details.

## Decision tracing

Enable with `PERFMON_TRACE=1` to record every decision as an inspectable DAG. Renders to JSON, Mermaid, DOT, or HTML.

```bash
PERFMON_TRACE=1 perfmon-skills recommend start --platform SPR --cmd "./workload"
perfmon-skills trace --last --format mermaid
```

---

# Part 3: Cross-skill integration

## With `linux-perf`

`linux-perf` handles profiling data collection (perf record, perf report, perf c2c, hotspot reporting). `tma-drilldown` handles the structured TMA methodology (which events to collect, threshold evaluation, drill-down path selection). They complement each other:

- Use `linux-perf` Flow A (perf stat) → then feed the data into `tma-drilldown` for automated TMA analysis
- Use `tma-drilldown` to identify the bottleneck category → then use `linux-perf` Flow B (perf record) to localize to specific functions/lines

## With `performance-patterns`

After `tma-drilldown` identifies a leaf-node bottleneck (e.g., Memory_Bound → L3_Bound → SQ_Full), delegate to `performance-patterns` for fix playbooks and code-level remediation.

---

# Part 4: Architecture

Two-layer design:
1. **Deterministic layer** (Python CLI): parses perf output, evaluates TMA threshold formulas, selects drill-down path, generates perf commands, tracks context budget
2. **LLM layer** (this SKILL.md + references): interprets findings conversationally, adds tuning advice — never sees raw perf data

### Key data flow (recommendation engine)

1. `start()` → detect platform, run preflight, generate L1 perf command
2. User runs perf, feeds output → `analyze()`:
   - Parse and normalize event names (perf → perfmon mapping)
   - Evaluate metric values from event counters
   - Check bottleneck thresholds (arithmetic)
   - Pick highest-value threshold-passing node → generate children events
   - Save compact finding; generate next perf command
3. Iterate until leaf node → tuning guidance + coverage report

### Supported platforms

All platforms in Intel's perfmon repository (50+), including SPR, EMR, GNR, ICX, SKX/CLX, ADL/RPL, MTL/ARL, and more.

### Counter budget

Knows platform-specific counter counts (e.g., SPR: 8 GP + 4 fixed) to generate commands that avoid unnecessary multiplexing.
