"""CLI command for trace visualization."""

import argparse
import json
import sys
from pathlib import Path


def add_parser(subparsers):
    parser = subparsers.add_parser("trace", help="Visualize decision trace from a session")
    parser.add_argument(
        "--session", metavar="DIR", help="Path to session directory containing trace.json"
    )
    parser.add_argument(
        "--last", action="store_true", help="Use the most recent session"
    )
    parser.add_argument(
        "--format", "-f",
        choices=["json", "mermaid", "dot", "html"],
        default="mermaid",
        help="Output format (default: mermaid)",
    )
    parser.add_argument(
        "--output", "-o", metavar="FILE", help="Write output to file instead of stdout"
    )
    parser.set_defaults(func=run_trace)


def run_trace(args):
    from ..core.tracer import DecisionNode, Trace
    from ..recommend.session import Session

    # Resolve session directory
    if args.session:
        session_dir = Path(args.session)
    elif args.last:
        base_dir = Path.cwd() / "sessions"
        session = Session.find_latest(base_dir)
        if session is None:
            print("Error: no sessions found", file=sys.stderr)
            sys.exit(1)
        session_dir = session.dir
    else:
        print("Error: specify --session DIR or --last", file=sys.stderr)
        sys.exit(1)

    # Load trace data
    trace_path = session_dir / "trace.json"
    if not trace_path.exists():
        print(f"Error: no trace.json found in {session_dir}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(trace_path.read_text())

    # Reconstruct Trace object
    trace = Trace()
    for node_dict in data.get("nodes", []):
        node = DecisionNode(
            id=node_dict.get("id", ""),
            timestamp=node_dict.get("timestamp", ""),
            actor=node_dict.get("actor", "system"),
            operation=node_dict.get("operation", ""),
            inputs=node_dict.get("inputs", {}),
            reasoning=node_dict.get("reasoning", ""),
            decision=node_dict.get("decision", ""),
            alternatives=node_dict.get("alternatives", []),
            confidence=node_dict.get("confidence", 1.0),
            parent_id=node_dict.get("parent_id"),
            children_ids=node_dict.get("children_ids", []),
            metadata=node_dict.get("metadata", {}),
            duration_ms=node_dict.get("duration_ms", 0.0),
        )
        trace.nodes.append(node)
        trace._node_map[node.id] = node

    # Render in requested format
    if args.format == "json":
        output = trace.to_json()
    elif args.format == "mermaid":
        output = trace.to_mermaid()
    elif args.format == "dot":
        output = trace.to_dot()
    elif args.format == "html":
        output = trace.to_html()

    # Write output
    if args.output:
        Path(args.output).write_text(output)
    else:
        print(output)
