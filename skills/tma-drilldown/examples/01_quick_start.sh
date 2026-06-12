#!/bin/bash
# Quick-start example for perfmon-skills CLI
# Demonstrates all 5 subcommands using SPR (Sapphire Rapids) platform.
# Usage: chmod +x 01_quick_start.sh && ./01_quick_start.sh
set -e

echo "=== 1. Event/Metric Search ==="
# Search for events and metrics related to cache misses
perfmon-skills lookup "cache miss" --platform SPR

echo ""
echo "=== 2. TMA Metric Lookup ==="
# Look up a specific TMA metric by name
perfmon-skills lookup "Backend_Bound" --platform SPR --type metrics

echo ""
echo "=== 3. Generate L1 TMA Command ==="
# Generate a perf command for top-level TMA analysis (5 second duration)
perfmon-skills cmdgen --tma-level 1 --platform SPR --duration 5

echo ""
echo "=== 4. Generate Drill-Down Command ==="
# Generate a perf command targeting a specific TMA node on this process
perfmon-skills cmdgen --tma-node Backend_Bound --platform SPR --pid $$

echo ""
echo "=== 5. Cross-Platform Comparison ==="
# Compare available metrics between Ice Lake and Sapphire Rapids
perfmon-skills compare ICX SPR --type metrics

echo ""
echo "=== 6. Guided Investigation ==="
# Start a recommended investigation workflow
perfmon-skills recommend start --platform SPR --cmd "sleep 2"

echo ""
echo "--- Next Steps ---"
echo "Run the generated perf commands, then feed the output back to"
echo "perfmon-skills for analysis and further drill-down recommendations."
