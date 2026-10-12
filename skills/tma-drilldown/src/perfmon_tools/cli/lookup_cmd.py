"""CLI for event/metric lookup."""

import argparse
import json
import sys


def add_parser(subparsers):
    parser = subparsers.add_parser("lookup", help="Search events and metrics")
    parser.add_argument("query", help="Search term")
    parser.add_argument("--platform", "-p", help="Platform shortname (default: auto-detect)")
    parser.add_argument("--type", "-t", choices=["events", "metrics", "all"], default="all")
    parser.add_argument("--category", "-c", help="Filter metrics by category")
    parser.add_argument("--level", "-l", type=int, help="Filter metrics by TMA level")
    parser.add_argument("--cross-arch", action="store_true", help="Search all architectures")
    parser.add_argument("--format", "-f", choices=["table", "json", "brief"], default="table")
    parser.add_argument("--deprecated", action="store_true", help="Include deprecated events")
    parser.set_defaults(func=run)


def run(args):
    from ..lookup.search import search

    results = search(
        query=args.query,
        platform=args.platform,
        search_type=args.type,
        category=args.category,
        level=args.level,
        cross_arch=args.cross_arch,
        include_deprecated=args.deprecated,
    )

    if args.format == "json":
        print(json.dumps(results, indent=2))
        return

    platform_str = results.get("platform", "unknown")

    if results["events"]:
        print(f"\n{'='*70}")
        print(f"EVENTS ({platform_str}) — {len(results['events'])} matches")
        print(f"{'='*70}")
        if args.format == "brief":
            for ev in results["events"]:
                print(f"  {ev['name']}")
        else:
            for ev in results["events"]:
                dep = " [DEPRECATED]" if ev["deprecated"] else ""
                print(f"  {ev['name']}{dep}")
                print(f"    {ev['description'][:80]}")
                print(f"    Code: {ev['event_code']}, UMask: {ev['umask']}, "
                      f"Counter: {ev['counter']}, PEBS: {ev['precise']}")
                print()

    if results["metrics"]:
        print(f"\n{'='*70}")
        print(f"METRICS ({platform_str}) — {len(results['metrics'])} matches")
        print(f"{'='*70}")
        if args.format == "brief":
            for m in results["metrics"]:
                print(f"  {m['name']} (L{m['level']}, {m['category']})")
        else:
            for m in results["metrics"]:
                parent = f" → {m['parent_category']}" if m.get("parent_category") else ""
                print(f"  {m['name']} (L{m['level']}, {m['category']}{parent})")
                print(f"    {m['description'][:80]}")
                if m.get("unit"):
                    print(f"    Unit: {m['unit']}")
                if m.get("metric_group"):
                    print(f"    Groups: {m['metric_group']}")
                print()

    total = len(results.get("events", [])) + len(results.get("metrics", []))
    if total == 0:
        print(f"No results for '{args.query}' on {platform_str}")
        if not args.cross_arch:
            print("  Try --cross-arch to search all platforms")
