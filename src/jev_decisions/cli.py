"""CLI bootstrap. Decision/provider commands will be added in subsequent issues."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from jev_decisions import __version__


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser without side effects or network access."""
    parser = argparse.ArgumentParser(
        prog="jev",
        description="Jev decisions for Claude Code (decision commands coming in M0).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line entry point."""
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
