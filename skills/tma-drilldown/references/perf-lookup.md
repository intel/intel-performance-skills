---
name: perf-lookup
description: Look up Intel PMU events and TMA metrics
---

# Performance Event & Metric Lookup

Use the `perfmon-skills` CLI to search for Intel PMU events and TMA metrics.

## Usage

When the user asks about a performance event or metric, run:

```bash
perfmon-skills lookup "<query>" --format json
```

Options:
- `--platform PLT` — target platform (default: auto-detect from CPU)
- `--type events|metrics|all` — filter by type
- `--category CAT` — filter by category (e.g., "TMA", "Cache", "Pipeline")
- `--level N` — TMA level filter (1-6)
- `--cross-arch` — search across all platforms

## Interpretation

- Present results conversationally, highlighting the most relevant matches
- For TMA metrics, explain their position in the hierarchy and what they measure
- For events, explain what the event counts and common use cases
- If the user's platform isn't specified, mention which platform the results are for
