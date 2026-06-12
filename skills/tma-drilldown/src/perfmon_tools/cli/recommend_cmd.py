"""CLI for the recommendation engine."""

import argparse
import json
import sys


def add_parser(subparsers):
    parser = subparsers.add_parser("recommend", help="TMA-guided performance investigation")
    sub = parser.add_subparsers(dest="action")

    # start
    start = sub.add_parser("start", help="Start new investigation session")
    start.add_argument("--platform", "-p", help="Platform shortname (default: auto-detect)")
    start.add_argument("--pid", type=int, help="Target PID")
    start.add_argument("--cmd", help="Command to profile")
    start.add_argument("--duration", "-d", type=int, default=5, help="Duration (seconds)")
    start.set_defaults(func=run_start)

    # analyze
    analyze = sub.add_parser("analyze", help="Analyze perf stat output")
    analyze.add_argument("--input", "-i", help="Path to perf stat output file")
    analyze.add_argument("--stdin", action="store_true", help="Read from stdin")
    analyze.add_argument("--session", help="Session directory path")
    analyze.set_defaults(func=run_analyze)

    # status
    status = sub.add_parser("status", help="Show current session state")
    status.add_argument("--session", help="Session directory path")
    status.set_defaults(func=run_status)

    # summary
    summary = sub.add_parser("summary", help="Show investigation summary")
    summary.add_argument("--session", help="Session directory path")
    summary.set_defaults(func=run_summary)

    # General format option
    parser.add_argument("--format", "-f", choices=["text", "json"], default="text")
    parser.set_defaults(func=lambda args: parser.print_help())


def run_start(args):
    from ..recommend.engine import RecommendationEngine

    engine = RecommendationEngine()
    result = engine.start(
        platform=args.platform,
        pid=args.pid,
        command=args.cmd,
        duration=args.duration,
    )

    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(json.dumps(result, indent=2, default=str))
        return

    print(f"\n{'='*70}")
    print(f"NEW INVESTIGATION SESSION")
    print(f"{'='*70}")
    print(f"  Platform: {result['platform']}")
    print(f"  Session:  {result['session_dir']}")
    print(f"  Strategy: {result['strategy']}")
    if result.get("notes"):
        for note in result["notes"]:
            print(f"  Note: {note}")
    print(f"\n  Counter budget: {result['counter_budget']}")
    print(f"\n{'='*70}")
    print(f"  STEP 1: Run this command and feed the output back:")
    print(f"{'='*70}")
    print(f"\n  {result['command']}")
    print(f"\n  Then run: perfmon-skills recommend analyze --input <output_file>")
    print(f"  Or pipe:  <command> 2>&1 | perfmon-skills recommend analyze --stdin")
    print()


def run_analyze(args):
    from ..recommend.engine import RecommendationEngine

    # Read perf output
    if args.input:
        from pathlib import Path
        perf_output = Path(args.input).read_text()
    elif args.stdin or not sys.stdin.isatty():
        perf_output = sys.stdin.read()
    else:
        print("Error: provide --input FILE or --stdin, or pipe perf output")
        sys.exit(1)

    engine = RecommendationEngine()
    result = engine.analyze(
        perf_output=perf_output,
        session_dir=args.session,
    )

    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(json.dumps(result, indent=2, default=str))
        return

    print(f"\n{'='*70}")
    print(f"ANALYSIS — Step {result['step']} [{result['state']}]")
    print(f"{'='*70}")
    print(f"  Path: {' → '.join(result['path']) or '(root)'}")
    print()

    # Show results
    print("  Node Values:")
    for r in result["results"]:
        if r["value"] is not None:
            marker = " ◀ BOTTLENECK" if r["threshold_passed"] else ""
            print(f"    {r['name']:30s} {r['value']:6.1f}%{marker}")

    # Multiplexing warnings
    if result.get("multiplexing_issues"):
        print(f"\n  ⚠ Multiplexing issues:")
        for m in result["multiplexing_issues"]:
            print(f"    {m['event']}: measured {m['measured_pct']:.1f}% of time")

    if result["is_complete"]:
        print(f"\n{'='*70}")
        print(f"  INVESTIGATION COMPLETE")
        print(f"{'='*70}")
        print(f"  Bottleneck path: {' → '.join(result['path'])}")
        if result.get("guidance"):
            g = result["guidance"]
            print(f"\n  Diagnosis: {g.get('brief', '')}")
            print(f"\n  Suggestions:")
            for s in g.get("suggestions", []):
                print(f"    - {s}")
            if g.get("compiler_suggestion"):
                print(f"\n  Compiler: {g['compiler_suggestion']}")
        if result.get("sampling_suggestion"):
            ss = result["sampling_suggestion"]
            print(f"\n  For code-level localization, run:")
            print(f"    {ss['command']}")
    else:
        print(f"\n{'='*70}")
        print(f"  NEXT STEP: Run this command:")
        print(f"{'='*70}")
        print(f"\n  {result.get('next_command', '(no command generated)')}")
        print(f"\n  Then: perfmon-skills recommend analyze --input <output_file>")
    print()


def run_status(args):
    from ..recommend.engine import RecommendationEngine

    engine = RecommendationEngine()
    result = engine.status(session_dir=args.session)

    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(json.dumps(result, indent=2))
        return

    print(f"\n  State:    {result.get('state', 'unknown')}")
    print(f"  Platform: {result.get('platform', 'unknown')}")
    print(f"  Step:     {result.get('step', 0)}")
    print(f"  Path:     {' → '.join(result.get('path', []))}")
    if result.get("findings"):
        print(f"  Findings:")
        for f in result["findings"]:
            print(f"    L{f['level']}: {f['node']} = {f['value']:.1f}%")
    print()


def run_summary(args):
    from ..recommend.engine import RecommendationEngine

    engine = RecommendationEngine()
    result = engine.summary(session_dir=args.session)

    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(json.dumps(result, indent=2, default=str))
        return

    if "error" in result:
        print(f"  {result['error']}")
        return

    if result.get("message"):
        print(f"  {result['message']}")
        return

    print(f"\n{'='*70}")
    print(f"INVESTIGATION SUMMARY")
    print(f"{'='*70}")
    path = result.get("bottleneck_path", [])
    print(f"  Bottleneck: {' → '.join(path)}")
    print(f"  Final node: {result.get('final_node', '')}")
    if result.get("guidance"):
        g = result["guidance"]
        print(f"\n  {g.get('brief', '')}")
        for s in g.get("suggestions", []):
            print(f"    - {s}")
    if result.get("coverage_pct") is not None:
        print(f"\n  Event coverage: {result['coverage_pct']:.1f}%")
    if result.get("suggested_expansions"):
        print(f"\n  Suggested deeper investigation:")
        for s in result["suggested_expansions"]:
            print(f"    {s['node']}: {s['rationale']}")
            for ev in s.get("events", [])[:3]:
                print(f"      - {ev}")
    print()
