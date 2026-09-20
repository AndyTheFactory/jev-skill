"""``jev`` command-line interface: ``decide``, ``reveal``, ``doctor``, ``profile``.

JSON results go to stdout; diagnostics and errors go to stderr. Exit codes:

- 0: decide outcome "accepted"
- 1: "abstained"
- 2: "failed" (provider unavailable/errored)
- 3: "rejected" (malformed/untrusted response)
- 2: CLI usage error (argparse default, e.g. missing --input/--stdin)
- 65: invalid input data (fails validation before any network call)

``decide`` runs in shadow mode by default: it prints only a record id,
outcome and ``action.permitted=false`` -- never the selected option or
probability. Use ``jev reveal RECORD_ID`` as a separate, explicit step to
see the full protected decision. The only exception is a request whose
``profile`` is explicitly listed in a trusted config's
``execution.active_profiles`` with ``execution.mode: active``: for an
"accepted" outcome only, the output additionally includes
``selected_option_id``/``probability`` as a visible recommendation.
``action.permitted`` is still always ``false`` even then -- this never
authorizes anything by itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jev_decisions import __version__, store
from jev_decisions.baseline import BaselineError, load_baseline
from jev_decisions.baseline import load_protected as load_protected_baseline
from jev_decisions.config import ConfigError, load_config
from jev_decisions.engine import run_decision
from jev_decisions.evaluation.dataset import DatasetError, load_dataset
from jev_decisions.evaluation.metrics import EvaluationError, build_report, load_manifest
from jev_decisions.profiles import ProfileError, load_registry
from jev_decisions.provider.openrouter import OpenRouterAdapter, ProviderError
from jev_decisions.schemas import ChoiceOption, ChoiceRequest
from jev_decisions.telemetry import read_events

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
    decide.add_argument(
        "--baseline-file",
        metavar="FILE",
        help="Path to a JSON baseline (Claude's own action), recorded before this call.",
    )

    reveal = subparsers.add_parser(
        "reveal", help="Explicitly reveal a protected shadow decision, outside the original task."
    )
    reveal.add_argument("record_id")

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

    evaluate = subparsers.add_parser(
        "evaluate", help="Score a run manifest against the labeled dataset and telemetry."
    )
    evaluate.add_argument(
        "--input",
        required=True,
        metavar="FILE",
        help="Run manifest: JSON [{example_id, record_id}].",
    )
    evaluate.add_argument(
        "--dataset", metavar="DIR", help="Dataset directory (default: eval/dataset/v1)."
    )

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
    profile_id = raw.get("profile") if isinstance(raw, dict) else None
    if profile_id is not None:
        if not isinstance(profile_id, str):
            print(f"error: invalid request: profile must be a string, got {profile_id!r}",
                  file=sys.stderr)
            return EX_DATAERR
        if "options" not in raw and "question" not in raw:
            try:
                resolved = load_registry().get(profile_id)
            except ProfileError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return EX_DATAERR
            raw = {
                **raw,
                "question": resolved.question,
                "options": [opt.model_dump() for opt in resolved.options],
            }
            abstain_option_ids = resolved.abstain_option_ids
        else:
            # Caller supplied their own question/options alongside a
            # `profile` label. That label was never verified against the
            # registry, so it must not reach the request: active-mode
            # gating (engine._profile_is_active) trusts request.profile
            # completely, and letting an unverified label through here
            # would let arbitrary content masquerade as vetted profile
            # content under active mode.
            raw = {k: v for k, v in raw.items() if k != "profile"}

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

    baseline = None
    if args.baseline_file:
        try:
            baseline = load_baseline(Path(args.baseline_file))
        except BaselineError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EX_DATAERR

    result = run_decision(
        config, request, abstain_option_ids=abstain_option_ids, baseline=baseline
    )
    print(json.dumps(result.model_dump(), indent=2))
    print(f"outcome={result.outcome}", file=sys.stderr)
    return EXIT_BY_OUTCOME[result.outcome]


def cmd_reveal(args: argparse.Namespace) -> int:
    try:
        decision = store.load(args.record_id)
    except (store.RecordNotFoundError, store.InvalidRecordIdError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_DATAERR
    baseline = load_protected_baseline(args.record_id)
    output = decision.model_dump()
    output["baseline"] = baseline.model_dump(mode="json") if baseline else None
    print(json.dumps(output, indent=2))
    return 0


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


def cmd_evaluate(args: argparse.Namespace) -> int:
    try:
        entries = load_manifest(Path(args.input))
    except EvaluationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_DATAERR

    dataset_dir = Path(args.dataset) if args.dataset else None
    try:
        dataset = load_dataset(dataset_dir)
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_DATAERR
    dataset_by_id = {e.id: e for e in dataset}

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_DATAERR
    telemetry_events = read_events(config.telemetry)

    report = build_report(entries, dataset_by_id, telemetry_events)
    print(json.dumps(report.model_dump(mode="json"), indent=2))
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
    report["active_profiles"] = list(config.execution.active_profiles)
    if config.execution.active_profiles:
        known_ids = {p.id for p in load_registry().list()}
        unknown = [p for p in config.execution.active_profiles if p not in known_ids]
        if unknown:
            report["active_profiles_warning"] = (
                f"unknown profile id(s) {unknown} in execution.active_profiles "
                "(typo? active mode silently never triggers for these)"
            )
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
    if args.command == "reveal":
        return cmd_reveal(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "profile":
        return cmd_profile(args)
    if args.command == "evaluate":
        return cmd_evaluate(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
