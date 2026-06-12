"""Main CLI entry point for perfmon-skills."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="perfmon-skills",
        description="Performance analysis toolkit built on Intel perfmon data",
    )
    subparsers = parser.add_subparsers(dest="command")

    # Register subcommands
    from . import lookup_cmd, cmdgen_cmd, compare_cmd, recommend_cmd, trace_cmd
    lookup_cmd.add_parser(subparsers)
    cmdgen_cmd.add_parser(subparsers)
    compare_cmd.add_parser(subparsers)
    recommend_cmd.add_parser(subparsers)
    trace_cmd.add_parser(subparsers)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
