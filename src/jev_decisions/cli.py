"""``jev`` command-line interface: ``decide`` and ``doctor``.

JSON results go to stdout; diagnostics and errors go to stderr. Exit codes:

- 0: decide outcome "accepted"
- 1: "abstained"
- 2: "failed" (provider unavailable/errored)
- 3: "rejected" (malformed/untrusted response)
- 2: CLI usage error (argparse default, e.g. missing --input/--stdin)
- 65: invalid input data (fails validation before any network call)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from jev_decisions import __version__
from jev_decisions.config import ConfigError, JevConfig, load_config
from jev_decisions.policy import Decision, evaluate
from jev_decisions.profiles import ProfileError, load_registry
from jev_decisions.provider.openrouter import OpenRouterAdapter, ProviderError
from jev_decisions.schemas import ChoiceOption, ChoiceRequest

EXIT_BY_OUTCOME = {"accepted": 0, "abstained": 1, "failed": 2, "rejected": 3}
EX_DATAERR = 65


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser without side effects or network access."""
    parser = argparse.ArgumentParser(
        prog="jev",
        description="Jev decisions for Claude Code.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    decide = subparsers.add_parser("decide", help="Evaluate a single Choice decision.")
    source = decide.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", metavar="FILE", help="Path to a JSON Choice request.")
    source.add_argument("--stdin", action="store_true", help="Read the JSON request from stdin.")

    subparsers.add_parser("doctor", help="Diagnose configuration and dependencies.")
    doctor_check = subparsers.choices["doctor"]
    doctor_check.add_argument(
        "--check-provider",
        action="store_true",
        help="Attempt a live, minimal provider call (requires credentials).",
    )

    profile = subparsers.add_parser("profile", help="Inspect the Choice profile registry.")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    profile_sub.add_parser("list", help="List available profiles.")
    show = profile_sub.add_parser("show", help="Show one profile's definition.")
    show.add_argument("profile_id")

    return parser


def _read_request_json(args: argparse.Namespace) -> dict[str, Any]:
    if args.stdin:
        raw = sys.stdin.read()
    else:
        with open(args.input, encoding="utf-8") as fh:
            raw = fh.read()
    return json.loads(raw)  # type: ignore[no-any-return]


def _parse_request(raw: dict[str, Any]) -> ChoiceRequest:
    if not isinstance(raw, dict):
        raise TypeError(f"request must be a JSON object, got {type(raw).__name__}")
    options = raw.get("options", [])
    raw = dict(raw)
    raw["options"] = tuple(
        ChoiceOption(**opt) if isinstance(opt, dict) else opt for opt in options
    )
    return ChoiceRequest.model_validate(raw)


def cmd_decide(args: argparse.Namespace) -> int:
    try:
        raw = _read_request_json(args)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read request: {exc}", file=sys.stderr)
        return EX_DATAERR

    abstain_option_ids: frozenset[str] = frozenset()
    if isinstance(raw, dict) and raw.get("profile") and "options" not in raw:
        try:
            resolved = load_registry().get(raw["profile"])
        except ProfileError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EX_DATAERR
        raw = {
            **raw,
            "question": resolved.question,
            "options": [opt.model_dump() for opt in resolved.options],
        }
        abstain_option_ids = resolved.abstain_option_ids

    try:
        request = _parse_request(raw)
    except (ValidationError, TypeError, ValueError) as exc:
        print(f"error: invalid request: {exc}", file=sys.stderr)
        return EX_DATAERR

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_DATAERR

    decision = _run_decision(config, request, abstain_option_ids=abstain_option_ids)
    print(json.dumps(decision.model_dump(), indent=2))
    print(f"outcome={decision.outcome}", file=sys.stderr)
    return EXIT_BY_OUTCOME[decision.outcome]


def _run_decision(
    config: JevConfig, request: ChoiceRequest, *, abstain_option_ids: frozenset[str] = frozenset()
) -> Decision:
    try:
        with OpenRouterAdapter(config) as adapter:
            response = adapter.decide(request)
    except ProviderError as exc:
        return evaluate(request, None, error=exc, config=config.policy)
    return evaluate(
        request, response, config=config.policy, abstain_option_ids=abstain_option_ids
    )


def cmd_profile(args: argparse.Namespace) -> int:
    registry = load_registry()
    if args.profile_command == "list":
        print(json.dumps([p.id for p in registry.list()], indent=2))
        return 0
    try:
        profile = registry.get(args.profile_id)
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_DATAERR
    print(json.dumps(profile.model_dump(mode="json"), indent=2))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {}

    try:
        config = load_config()
        report["config"] = "ok"
    except ConfigError as exc:
        report["config"] = f"error: {exc}"
        print(json.dumps(report, indent=2))
        return EX_DATAERR

    report["execution_mode"] = config.execution.mode
    report["credential_present"] = config.get_api_key() is not None

    if args.check_provider:
        if not report["credential_present"]:
            report["provider_connectivity"] = "skipped: no credential"
        else:
            probe = ChoiceRequest(
                question="doctor connectivity probe",
                options=(
                    ChoiceOption(id="ok", description="reachable"),
                    ChoiceOption(id="fail", description="unreachable"),
                ),
            )
            try:
                with OpenRouterAdapter(config) as adapter:
                    adapter.decide(probe)
                report["provider_connectivity"] = "ok"
            except ProviderError as exc:
                report["provider_connectivity"] = f"error: {exc.error.code}"

    print(json.dumps(report, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "decide":
        return cmd_decide(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "profile":
        return cmd_profile(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
