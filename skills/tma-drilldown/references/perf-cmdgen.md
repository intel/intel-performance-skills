---
name: perf-cmdgen
description: Generate ready-to-run perf stat commands
---

# Performance Command Generator

Generate `perf stat` commands with correct event encoding for the user's platform.

## Usage

```bash
perfmon-skills cmdgen --format json [options]
```

Options:
- `--platform PLT` — target platform (default: auto-detect)
- `--tma-level N` — generate command for TMA level N
- `--tma-node NAME` — generate command for a specific TMA node's children
- `--metric NAME` — include specific metric(s) (repeatable)
- `--event NAME` — include specific event(s) (repeatable)
- `--duration SEC` — collection duration (default: 5)
- `--pid PID` — target process ID
- `--cmd CMD` — command to profile
- `--json` — add `-j` flag for JSON output from perf
- `--per-core` — add per-core breakdown

## Interpretation

- Present the generated command(s) ready to copy-paste
- Explain counter budget: how many events vs available counters
- If multiplexing is needed, explain the confidence impact
- For hybrid platforms, explain the core affinity flags
