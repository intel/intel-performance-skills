"""CLI for cross-platform comparison."""

import argparse
import json


def add_parser(subparsers):
    parser = subparsers.add_parser("compare", help="Compare events/metrics between platforms")
    parser.add_argument("platform1", help="First platform shortname")
    parser.add_argument("platform2", help="Second platform shortname")
    parser.add_argument("--type", "-t", choices=["events", "metrics", "all"], default="all")
    parser.add_argument("--metric", help="Compare specific metric")
    parser.add_argument("--event", help="Compare specific event")
    parser.add_argument("--category", "-c", help="Filter by category")
    parser.add_argument("--format", "-f", choices=["text", "json"], default="text")
    parser.set_defaults(func=run)


def run(args):
    from ..compare.diff import compare_platforms

    result = compare_platforms(
        platform1=args.platform1,
        platform2=args.platform2,
        compare_type=args.type,
        metric_name=args.metric,
        event_name=args.event,
        category=args.category,
    )

    if args.format == "json":
        print(json.dumps(result, indent=2))
        return

    p1 = result["platform1"]
    p2 = result["platform2"]
    print(f"\n{'='*70}")
    print(f"COMPARISON: {p1} → {p2}")
    print(f"{'='*70}")

    # Events
    ev = result["events"]
    if ev["added"] or ev["removed"] or ev["changed"]:
        print(f"\n--- EVENTS ---")
        if ev["added"]:
            print(f"\n  Added in {p2} ({len(ev['added'])}):")
            for e in ev["added"][:20]:
                print(f"    + {e['name']}: {e.get('description', '')[:60]}")
            if len(ev["added"]) > 20:
                print(f"    ... and {len(ev['added'])-20} more")

        if ev["removed"]:
            print(f"\n  Removed in {p2} ({len(ev['removed'])}):")
            for e in ev["removed"][:20]:
                print(f"    - {e['name']}: {e.get('description', '')[:60]}")
            if len(ev["removed"]) > 20:
                print(f"    ... and {len(ev['removed'])-20} more")

        if ev["changed"]:
            print(f"\n  Changed ({len(ev['changed'])}):")
            for e in ev["changed"][:20]:
                print(f"    ~ {e['name']}:")
                for field, change in e["changes"].items():
                    if isinstance(change, dict):
                        print(f"        {field}: {change['from']} → {change['to']}")
                    else:
                        print(f"        {field}: {change}")

    # Metrics
    mt = result["metrics"]
    if mt["added"] or mt["removed"] or mt["changed"]:
        print(f"\n--- METRICS ---")
        if mt["added"]:
            print(f"\n  Added in {p2} ({len(mt['added'])}):")
            for m in mt["added"][:20]:
                print(f"    + {m['name']} (L{m.get('level','?')}, {m.get('category','')})")
            if len(mt["added"]) > 20:
                print(f"    ... and {len(mt['added'])-20} more")

        if mt["removed"]:
            print(f"\n  Removed in {p2} ({len(mt['removed'])}):")
            for m in mt["removed"][:20]:
                print(f"    - {m['name']} (L{m.get('level','?')}, {m.get('category','')})")
            if len(mt["removed"]) > 20:
                print(f"    ... and {len(mt['removed'])-20} more")

        if mt["changed"]:
            print(f"\n  Changed ({len(mt['changed'])}):")
            for m in mt["changed"][:15]:
                print(f"    ~ {m['name']} (L{m.get('level','?')}, {m.get('category','')}):")
                for field, change in m["changes"].items():
                    if isinstance(change, dict):
                        if field == "formula":
                            print(f"        formula changed")
                        else:
                            print(f"        {field}: {change.get('from','')} → {change.get('to','')}")
                    elif isinstance(change, list) and change:
                        print(f"        {field}: {change[:5]}")
            if len(mt["changed"]) > 15:
                print(f"    ... and {len(mt['changed'])-15} more")

    # Summary
    total_changes = (
        len(ev["added"]) + len(ev["removed"]) + len(ev["changed"]) +
        len(mt["added"]) + len(mt["removed"]) + len(mt["changed"])
    )
    if total_changes == 0:
        print("\n  No differences found.")
    print()
