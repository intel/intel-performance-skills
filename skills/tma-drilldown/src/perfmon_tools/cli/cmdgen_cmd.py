"""CLI for perf command generation."""

import argparse
import json


def add_parser(subparsers):
    parser = subparsers.add_parser("cmdgen", help="Generate perf stat commands")
    parser.add_argument("--platform", "-p", help="Platform shortname (default: auto-detect)")
    parser.add_argument("--metric", "-m", action="append", help="Metric name (repeatable)")
    parser.add_argument("--tma-level", type=int, help="TMA level (1-6)")
    parser.add_argument("--tma-node", help="TMA node name (generate drill-down command)")
    parser.add_argument("--event", "-e", action="append", help="Specific event (repeatable)")
    parser.add_argument("--duration", "-d", type=int, default=5, help="Duration in seconds")
    parser.add_argument("--pid", type=int, help="Target PID")
    parser.add_argument("--cmd", help="Command to profile")
    parser.add_argument("--json", "-j", action="store_true", help="JSON output from perf")
    parser.add_argument("--per-core", action="store_true", help="Per-core output")
    parser.add_argument("--interval", "-I", type=int, help="Interval in ms")
    parser.add_argument("--repeat", "-r", type=int, help="Number of repetitions")
    parser.add_argument("--format", "-f", choices=["text", "json"], default="text")
    parser.set_defaults(func=run)


def run(args):
    from ..cmdgen.generate import generate_perf_command

    result = generate_perf_command(
        platform=args.platform,
        metrics=args.metric,
        tma_level=args.tma_level,
        tma_node=args.tma_node,
        events=args.event,
        duration=args.duration,
        pid=args.pid,
        command=args.cmd,
        json_output=args.json,
        per_core=args.per_core,
        interval_ms=args.interval,
        repeat=args.repeat,
    )

    if args.format == "json":
        print(json.dumps(result, indent=2))
        return

    print(f"\n# {result['description']} [{result['platform']}]")

    ci = result["counter_info"]
    print(f"# Events: {ci['events_count']} "
          f"(GP: {ci['programmable_events']}, Fixed: {ci['fixed_events']}, "
          f"PerfMetrics: {ci['perf_metrics_events']})")
    print(f"# Counters available: {ci['available_counters']} "
          f"(GP: {ci['gp_counters']})")

    if ci["needs_multiplexing"]:
        ratio = ci["estimated_mux_ratio"]
        print(f"# WARNING: Multiplexing needed (ratio: {ratio:.1f}x)")

    for note in result.get("notes", []):
        print(f"# NOTE: {note}")

    print()
    for cmd in result["commands"]:
        print(cmd)
    print()
