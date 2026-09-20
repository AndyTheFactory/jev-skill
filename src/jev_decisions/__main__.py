"""Allow `python -m jev_decisions` as an alternative entry point."""

from jev_decisions.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
