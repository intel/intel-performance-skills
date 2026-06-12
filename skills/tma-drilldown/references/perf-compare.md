---
name: perf-compare
description: Compare events and metrics across Intel platform generations
---

# Cross-Platform Comparison

Compare PMU events and TMA metrics between Intel platform generations.

## Usage

```bash
perfmon-skills compare PLATFORM1 PLATFORM2 --format json [options]
```

Options:
- `--type events|metrics|all` — what to compare
- `--metric NAME` — compare a specific metric
- `--event NAME` — compare a specific event
- `--category CAT` — filter by category

## Interpretation

- Summarize key differences: new capabilities, removed events, changed formulas
- For TMA metrics, highlight accuracy improvements or methodology changes
- When comparing adjacent generations (e.g., ICX→SPR), focus on what's new
- For distant generations (e.g., SKL→SPR), provide a migration guide
