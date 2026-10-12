#!/usr/bin/env python3
"""Demonstrates perf stat output parsing and event name normalization.

Shows how perfmon-skills handles the translation between:
- What `perf stat` outputs (topdown-fe-bound, cpu/EVENT/, etc.)
- What Intel's perfmon JSON formulas expect (PERF_METRICS.FRONTEND_BOUND, EVENT)

No hardware required — uses sample output strings.
"""

from perfmon_tools.core.perf_output import (
    parse_perf_stat_text,
    parse_perf_stat_json,
    parse_perf_stat_interval,
    parse_auto,
    _normalize_event_values,
    PERF_TO_PERFMON,
)


def section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


# --- Example 1: Text format parsing ---
section("1. Parsing perf stat TEXT output")

text_output = """\
 Performance counter stats for './workload':

     4,521,345,678      cycles                                        (66.52%)
     2,890,123,456      instructions              #    0.64  insn per cycle
       345,678,901      cache-references
        12,345,678      cache-misses              #    3.57% of all cache refs
       <not counted>    branch-misses

       2.501234567 seconds time elapsed

       2.480000000 seconds user
       0.020000000 seconds sys
"""

result = parse_perf_stat_text(text_output)

print("Parsed event values:")
for name, value in sorted(result.event_values.items()):
    print(f"  {name:40s} = {value:,.0f}")

print(f"\nDuration: {result.duration_seconds:.3f} seconds")

print(f"\nMultiplexing issues ({len(result.multiplexing_issues)}):")
for issue in result.multiplexing_issues:
    print(f"  {issue.message}")


# --- Example 2: JSON format parsing ---
section("2. Parsing perf stat JSON output (-j flag)")

json_output = """\
{"counter-value": "6000000.000000", "unit": "", "event": "slots", "pcnt-running": 100.00}
{"counter-value": "3000000.000000", "unit": "", "event": "topdown-be-bound", "pcnt-running": 100.00}
{"counter-value": "1500000.000000", "unit": "", "event": "topdown-fe-bound", "pcnt-running": 100.00}
{"counter-value": "1000000.000000", "unit": "", "event": "topdown-retiring", "pcnt-running": 100.00}
{"counter-value": "500000.000000", "unit": "", "event": "topdown-bad-spec", "pcnt-running": 100.00}
{"counter-value": "45000.000000", "unit": "", "event": "cpu/INT_MISC.UOP_DROPPING/", "pcnt-running": 100.00}
"""

result = parse_perf_stat_json(json_output)

print("Parsed event values (raw perf names):")
for name, value in sorted(result.event_values.items()):
    print(f"  {name:40s} = {value:,.0f}")


# --- Example 3: Event name normalization ---
section("3. Event Name Normalization (perf → perfmon)")

print("The PERF_TO_PERFMON mapping:")
for perf_name, perfmon_name in sorted(PERF_TO_PERFMON.items()):
    print(f"  {perf_name:30s} → {perfmon_name}")

print("\n\nApplying normalization to parsed values:")
normalized = _normalize_event_values(result.event_values)

print("\nAfter normalization (all available names):")
for name, value in sorted(normalized.items()):
    print(f"  {name:45s} = {value:,.0f}")

print("\nKey insight: both 'topdown-be-bound' AND 'PERF_METRICS.BACKEND_BOUND'")
print("now resolve to the same value. Metric formulas can use either name.")


# --- Example 4: parse_auto (auto-detect format) ---
section("4. Auto-detection with parse_auto()")

print("parse_auto() detects format AND normalizes event names in one call.")
print("It checks if input starts with '{' (JSON) or not (text).\n")

result = parse_auto(json_output)
print(f"Detected format: JSON")
print(f"Events parsed: {len(result.event_values)}")
print(f"Includes normalized names: {'PERF_METRICS.BACKEND_BOUND' in result.event_values}")


# --- Example 5: Interval mode parsing ---
section("5. Parsing interval mode output (-I flag)")

interval_output = """\
1.000123456;cycles;5000000;;100.00
1.000123456;instructions;2500000;;100.00
1.000123456;cache-misses;50000;;100.00
2.000234567;cycles;5100000;;100.00
2.000234567;instructions;2600000;;100.00
2.000234567;cache-misses;48000;;100.00
3.000345678;cycles;5200000;;100.00
3.000345678;instructions;2700000;;100.00
3.000345678;cache-misses;52000;;100.00
"""

intervals = parse_perf_stat_interval(interval_output)

print(f"Parsed {len(intervals)} intervals:\n")
print(f"  {'Interval':<10} {'cycles':>12} {'instructions':>14} {'cache-misses':>14} {'IPC':>6}")
print(f"  {'-'*10} {'-'*12} {'-'*14} {'-'*14} {'-'*6}")
for i, interval in enumerate(intervals, 1):
    ipc = interval.get("instructions", 0) / interval.get("cycles", 1)
    print(f"  {i:<10} {interval.get('cycles', 0):>12,.0f} "
          f"{interval.get('instructions', 0):>14,.0f} "
          f"{interval.get('cache-misses', 0):>14,.0f} "
          f"{ipc:>6.2f}")

print("\nInterval mode is used for phase detection — if IPC varies significantly")
print("across intervals, the workload has multiple phases that need separate analysis.")


# --- Example 6: Multiplexing detection ---
section("6. Multiplexing Detection")

mux_output = """\
{"counter-value": "5000000.000000", "unit": "", "event": "cycles", "pcnt-running": 100.00}
{"counter-value": "2500000.000000", "unit": "", "event": "instructions", "pcnt-running": 85.50}
{"counter-value": "100000.000000", "unit": "", "event": "cache-misses", "pcnt-running": 42.30}
{"counter-value": "<not counted>", "unit": "", "event": "branch-misses", "pcnt-running": 0.00}
"""

result = parse_perf_stat_json(mux_output)

print("When more events are requested than available hardware counters,")
print("perf time-shares (multiplexes) them. This introduces statistical error.\n")
print("Detection results:")
for issue in result.multiplexing_issues:
    print(f"  ⚠ {issue.message}")
print(f"\nThreshold: events measured < 90% of time are flagged.")
print(f"Action: reduce event count or split into multiple runs.")
