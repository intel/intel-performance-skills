"""Parse perf stat output (text and JSON formats)."""

import json
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class PerfStatResult:
    event_values: dict  # event_name -> value
    multiplexing_issues: list  # events with poor coverage
    duration_seconds: Optional[float]
    raw_text: str


@dataclass
class MultiplexingIssue:
    event: str
    enabled_pct: float  # percentage of time event was actually measured
    message: str


def parse_perf_stat_text(output: str) -> PerfStatResult:
    """Parse standard perf stat text output.

    Handles formats like:
        1,234,567      event_name          # comment
        1234567        event_name:modifier  (66.52%)
        <not counted>  event_name
    """
    event_values = {}
    multiplexing_issues = []
    duration = None

    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("Performance"):
            continue

        # Duration line: "1.234567890 seconds time elapsed"
        dur_match = re.match(r'^\s*([\d.]+)\s+seconds\s+time\s+elapsed', line)
        if dur_match:
            duration = float(dur_match.group(1))
            continue

        # <not counted> events
        not_counted = re.match(r'^\s*<not counted>\s+(\S+)', line)
        if not_counted:
            event_name = not_counted.group(1)
            multiplexing_issues.append(
                MultiplexingIssue(event_name, 0.0, f"{event_name}: not counted")
            )
            continue

        # Standard value line: "  1,234,567  event_name  ...  (XX.XX%)"
        match = re.match(
            r'^\s*([\d,]+(?:\.\d+)?)\s+(\S+)(?:\s+.*?)?\s*(?:\((\d+\.\d+)%\))?\s*$',
            line,
        )
        if match:
            value_str = match.group(1).replace(",", "")
            event_name = match.group(2)
            pct_str = match.group(3)

            try:
                value = float(value_str)
            except ValueError:
                continue

            event_values[event_name] = value

            # Check multiplexing percentage
            if pct_str:
                pct = float(pct_str)
                if pct < 90.0:
                    multiplexing_issues.append(
                        MultiplexingIssue(
                            event_name,
                            pct,
                            f"{event_name}: measured only {pct:.1f}% of time",
                        )
                    )
            continue

    return PerfStatResult(
        event_values=event_values,
        multiplexing_issues=multiplexing_issues,
        duration_seconds=duration,
        raw_text=output,
    )


def parse_perf_stat_json(output: str) -> PerfStatResult:
    """Parse perf stat JSON output (one JSON object per line).

    Each line is like:
    {"counter-value": "1234.000000", "unit": "", "event": "cycles", ...}
    or with newer perf:
    {"counter-value": "1234", "event": "cycles", "event-runtime": 100, "pcnt-running": 100.00}
    """
    event_values = {}
    multiplexing_issues = []
    duration = None

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        event = obj.get("event", "")
        if not event:
            continue

        # Handle "counter-value" field
        val_str = obj.get("counter-value", "")
        if val_str == "<not counted>" or val_str == "":
            multiplexing_issues.append(
                MultiplexingIssue(event, 0.0, f"{event}: not counted")
            )
            continue

        try:
            value = float(val_str)
        except (ValueError, TypeError):
            continue

        event_values[event] = value

        # Check multiplexing via pcnt-running or enabled/running ratio
        pcnt = obj.get("pcnt-running")
        if pcnt is not None:
            try:
                pct = float(pcnt)
                if pct < 90.0:
                    multiplexing_issues.append(
                        MultiplexingIssue(
                            event, pct, f"{event}: measured only {pct:.1f}% of time"
                        )
                    )
            except (ValueError, TypeError):
                pass

    return PerfStatResult(
        event_values=event_values,
        multiplexing_issues=multiplexing_issues,
        duration_seconds=duration,
        raw_text=output,
    )


def parse_perf_stat_interval(output: str) -> list:
    """Parse perf stat interval output (-I mode).

    Returns list of dicts, one per interval, each mapping event_name -> value.
    Interval output format:
        1.000123456;event_name;1234567;;100.00
    or text format with timestamp prefix.
    """
    intervals = []
    current_time = None
    current_values = {}

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue

        # CSV format: timestamp;event;value;unit;percent
        parts = line.split(";")
        if len(parts) >= 3:
            try:
                timestamp = float(parts[0])
                value_str = parts[2].replace(",", "")
                event_name = parts[1]

                if current_time is not None and abs(timestamp - current_time) > 0.001:
                    if current_values:
                        intervals.append(current_values.copy())
                    current_values = {}
                current_time = timestamp

                if value_str and value_str != "<not counted>":
                    current_values[event_name] = float(value_str)
            except (ValueError, IndexError):
                continue

    # Don't forget the last interval
    if current_values:
        intervals.append(current_values)

    return intervals


# Reverse mapping from perf event names to perfmon canonical names
PERF_TO_PERFMON = {
    "topdown-fe-bound": "PERF_METRICS.FRONTEND_BOUND",
    "topdown-bad-spec": "PERF_METRICS.BAD_SPECULATION",
    "topdown-be-bound": "PERF_METRICS.BACKEND_BOUND",
    "topdown-retiring": "PERF_METRICS.RETIRING",
    "topdown-fetch-lat": "PERF_METRICS.FETCH_LATENCY",
    "topdown-br-mispredict": "PERF_METRICS.BRANCH_MISPREDICTS",
    "topdown-mem-bound": "PERF_METRICS.MEMORY_BOUND",
    "topdown-heavy-ops": "PERF_METRICS.HEAVY_OPS",
    "slots": "TOPDOWN.SLOTS",
}


def _normalize_event_values(event_values: dict) -> dict:
    """Normalize perf event names to perfmon canonical names.

    Handles:
    - topdown-* → PERF_METRICS.*
    - cpu/EVENT/ or cpu_core/EVENT/ → EVENT
    - Keeps original names alongside normalized ones
    """
    normalized = {}
    for name, value in event_values.items():
        normalized[name] = value

        # Map perf topdown names
        if name in PERF_TO_PERFMON:
            normalized[PERF_TO_PERFMON[name]] = value

        # Strip cpu/ wrapper
        if name.startswith("cpu/") and name.endswith("/"):
            bare = name[4:-1]
            normalized[bare] = value
        elif name.startswith("cpu_core/") and name.endswith("/"):
            bare = name[9:-1]
            normalized[bare] = value

    return normalized


def parse_auto(output: str) -> PerfStatResult:
    """Auto-detect format, parse, and normalize event names."""
    stripped = output.strip()
    if stripped.startswith("{"):
        result = parse_perf_stat_json(output)
    else:
        result = parse_perf_stat_text(output)

    result.event_values = _normalize_event_values(result.event_values)
    return result
