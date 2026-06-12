---
name: perf-recommend
description: TMA-guided iterative performance investigation
---

# Performance Recommendation Engine

Orchestrate an iterative TMA drill-down investigation to identify performance bottlenecks.

## Workflow

### Step 1: Start Investigation

```bash
perfmon-skills recommend start --format json [--pid PID | --cmd "command"] [--duration SEC]
```

This returns the first `perf stat` command to run.

### Step 2: Collect and Analyze (iterative)

Run the perf command suggested by the engine, then feed the output back:

```bash
perf stat ... 2>&1 | perfmon-skills recommend analyze --stdin --format json
```

Or save to file:
```bash
perfmon-skills recommend analyze --input perf_output.txt --format json
```

The engine will either:
- Identify a deeper bottleneck and suggest the next perf command (continue iterating)
- Reach a leaf node and provide tuning guidance (investigation complete)

### Step 3: Check Status / Summary

```bash
perfmon-skills recommend status --format json
perfmon-skills recommend summary --format json
```

## Interpretation

- At each step, explain what the TMA analysis found and why the engine chose to drill into a particular branch
- Flag multiplexing issues if events exceeded counter budget
- When complete, present the full bottleneck path and actionable tuning suggestions
- Mention event coverage gaps if significant domains were unexplored
- Suggest `perf record` sampling commands for code-level localization

## Important Notes

- The engine is deterministic — it uses threshold formulas from Intel's TMA methodology
- Each step produces a compact finding (~200 tokens); raw perf data stays on disk
- The investigation typically takes 3-5 iterations (L1 → leaf)
- If the user's workload is non-steady-state, the engine will detect phases
