# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

perfmon-skills is a performance analysis toolkit that wraps Intel's perfmon data repository (included as a symlink at `./perfmon/`) into CLI tools and Claude Code slash commands. It implements deterministic TMA (Top-down Microarchitecture Analysis) drill-down without requiring an LLM for the core logic.

## Build & Development

```bash
# Install in development mode
pip install -e .

# Install with dev dependencies (pytest)
pip install -e ".[dev]"

# Run all tests
python -m pytest tests/ -v

# Run a single test
python -m pytest tests/test_perf_output.py::TestParseText::test_basic_values -v

# Run CLI
perfmon-skills lookup "cache miss" --platform SPR
perfmon-skills cmdgen --tma-level 1 --platform SPR
perfmon-skills compare ICX SPR --type metrics
perfmon-skills recommend start --platform SPR --cmd "sleep 1"

# Enable decision tracing
PERFMON_TRACE=1 perfmon-skills recommend start --platform SPR
```

## Architecture

Two-layer design:
1. **Deterministic layer** (Python, no LLM): parses perf output, evaluates TMA threshold formulas, selects drill-down path, generates perf commands, tracks context budget
2. **LLM layer** (Claude Code skills in `skills/`): interprets findings conversationally, adds tuning advice — never sees raw perf data

### Core Library (`src/perfmon_tools/core/`)

- `platform.py` — CPU detection (`/proc/cpuinfo`), mapfile.csv parsing, platform resolution. Handles hybrid platforms (ADL/RPL) with separate P-core/E-core event files. Uses `PERFMON_DATA` env var or `./perfmon/` symlink.
- `catalog.py` — Loads event/metric JSON from perfmon data. `PlatformCatalog` provides search indexes and coverage stats. SPR: ~2693 events, ~308 metrics.
- `tma_tree.py` — Builds parent→child TMA hierarchy from `ParentCategory` field. 4 L1 roots, up to L6 depth on SPR (114 nodes).
- `formula.py` — Expands metric formula aliases (a,b,c → event names) and evaluates via restricted `eval()`. Also evaluates threshold formulas for bottleneck detection.
- `perf_output.py` — Parses perf stat text/JSON/interval formats. `PERF_TO_PERFMON` dict translates perf's `topdown-*` names to perfmon's `PERF_METRICS.*` names. `_normalize_event_values()` strips `cpu/` wrappers.
- `context_budget.py` — Tracks token usage per step (~200 tokens/step compact finding vs. raw data on disk). Prevents attention loss in multi-step workflows.
- `tracer.py` — Decision tracing (`PERFMON_TRACE=1`). Records a DAG of decisions with inputs/alternatives/confidence. Renders to JSON/Mermaid/DOT/HTML. Zero overhead when disabled.

### Tool Modules

- `lookup/search.py` — Cross-field event/metric search with platform, type, category, level filters
- `cmdgen/generate.py` — Generates `perf stat` commands with counter budget awareness. Knows platform-specific counter counts (SPR: 8 GP + 4 fixed).
- `compare/diff.py` — Cross-platform event/metric comparison with formula diffs
- `recommend/` — Stateful TMA drill-down engine:
  - `engine.py` — State machine orchestrator (IDLE→COLLECTING→ANALYZED→COMPLETE)
  - `tma_drilldown.py` — Node evaluation, threshold checking, next-step suggestion
  - `preflight.py` — SMT detection, steady-state detection, counter budget
  - `session.py` — File-based session persistence (`sessions/` directory)
  - `coverage.py` — Event coverage tracking, domain-affinity gap suggestions
  - `guidance.py` — Tuning advice keyed by TMA leaf node

### Key Data Flow (Recommendation Engine)

1. `start()` → detect platform, run preflight, generate L1 perf command
2. User runs perf, feeds output → `analyze()`:
   - `parse_auto()` normalizes event names (perf→perfmon mapping)
   - `evaluate_level()` computes metric values from event counters
   - `_evaluate_threshold()` checks bottleneck thresholds (arithmetic)
   - `suggest_next()` picks highest-value threshold-passing node → children events
   - Compact finding saved; next perf command generated
3. Iterate until leaf node → guidance + coverage report

### Event Name Translation

perf outputs names like `topdown-fe-bound`, `cpu/INST_RETIRED.ANY/`. Perfmon JSON uses `PERF_METRICS.FRONTEND_BOUND`, `INST_RETIRED.ANY`. The `_normalize_event_values()` function in `perf_output.py` handles this bidirectionally.

## Key Design Constraints

- Zero mandatory dependencies (stdlib only). Optional: `rich` for pretty output, `pytest` for dev.
- perfmon data accessed via symlink `./perfmon/` or `PERFMON_DATA` env var pointing to the Intel perfmon repo root.
- Session state is plain JSON files in `sessions/` (gitignored). No database.
- Context budget: raw perf output stays on disk, only ~200-token compact findings flow between steps.
- Counter budget: knows each platform's GP/fixed counter count to minimize multiplexing.
