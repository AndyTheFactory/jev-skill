#!/usr/bin/env python3
"""Reproducible shadow-benchmark runner: dataset -> `jev decide` -> run manifest.

For every example in the labeled dataset, records a baseline action (as the
skill instructs Claude to do) and then runs the normal shadow-mode
`engine.run_shadow` path -- no different from a live invocation -- writing a
timestamped manifest plus environment metadata that `jev evaluate` and a
human reviewer can both use, so results are reproducible against a stated
dataset/environment version rather than a one-off local run.

This script only produces the manifest half of the M3 benchmark. Comparing
*task completion* with vs. without the skill (issue #18's "external checks,
latency, unnecessary tool calls, token/cost overhead") requires running real
coding/debugging/review/routing tasks twice inside an actual Claude Code
session -- that part is a human/agent procedure, documented in
`eval/BENCHMARK.md`, not something this offline script can do by itself.

Usage:
    python scripts/run_benchmark.py [--dataset DIR] [--split dev|eval|all] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jev_decisions import __version__  # noqa: E402
from jev_decisions.baseline import Baseline  # noqa: E402
from jev_decisions.config import load_config  # noqa: E402
from jev_decisions.dynamic import DynamicRequestRejected, validate_dynamic_request  # noqa: E402
from jev_decisions.engine import run_shadow  # noqa: E402
from jev_decisions.evaluation.dataset import (  # noqa: E402
    DEFAULT_DATASET_DIR,
    DatasetExample,
    load_dataset,
)
from jev_decisions.profiles import ProfileRegistry, load_registry  # noqa: E402
from jev_decisions.schemas import ChoiceRequest  # noqa: E402


def _build_request(
    example: DatasetExample, registry: ProfileRegistry
) -> tuple[ChoiceRequest, frozenset[str]]:
    if example.profile is not None:
        profile = registry.get(example.profile)
        return (
            ChoiceRequest(
                question=profile.question,
                options=profile.options,
                context=example.context,
                profile=example.profile,
            ),
            profile.abstain_option_ids,
        )
    assert example.question is not None and example.options is not None
    return (
        ChoiceRequest(
            question=example.question, options=example.options, context=example.context
        ),
        frozenset(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=None, help="Dataset dir (default: eval/dataset/v1)"
    )
    parser.add_argument("--split", choices=["dev", "eval", "all"], default="eval")
    parser.add_argument("--out", type=Path, default=None, help="Manifest output path")
    args = parser.parse_args(argv)

    dataset_dir = args.dataset or DEFAULT_DATASET_DIR
    examples = load_dataset(dataset_dir)
    if args.split != "all":
        examples = [e for e in examples if e.split == args.split]
    if not examples:
        print(f"no examples found for split={args.split!r} in {dataset_dir}", file=sys.stderr)
        return 1

    config = load_config()
    registry = load_registry()

    manifest = []
    for example in examples:
        request, abstain_option_ids = _build_request(example, registry)

        if example.category == "unsuitable_invocation":
            # Never reaches the provider: this is exactly what the skill's
            # own dynamic-question validation is supposed to refuse before
            # any network call. Confirm that here rather than spending a
            # real provider call on content that should never be sent.
            try:
                validate_dynamic_request(request)
                print(f"{example.id}: UNEXPECTED - validation did not reject this example")
            except DynamicRequestRejected:
                print(f"{example.id}: correctly rejected before any provider call")
            continue

        baseline = Baseline(
            action=f"[benchmark] dataset example {example.id}",
            task_id=f"benchmark-{example.id}",
            recorded_at=datetime.now(UTC),
        )
        result = run_shadow(
            config, request, abstain_option_ids=abstain_option_ids, baseline=baseline
        )
        manifest.append({"example_id": example.id, "record_id": result.record_id})
        print(f"{example.id}: outcome={result.outcome} record_id={result.record_id}")

    out_path = args.out or (
        Path(__file__).parent.parent
        / "eval"
        / "results"
        / f"manifest-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2))

    meta_path = out_path.with_suffix(".meta.json")
    meta_path.write_text(
        json.dumps(
            {
                "jev_decisions_version": __version__,
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "dataset_dir": str(dataset_dir),
                "dataset_version": examples[0].dataset_version,
                "split": args.split,
                "n_examples": len(examples),
                "n_decided": len(manifest),
                "n_unsuitable_skipped": len(examples) - len(manifest),
                "provider_model": config.provider.model,
                "generated_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
    )

    print(f"\nwrote {len(manifest)} entries to {out_path}")
    print(f"wrote environment metadata to {meta_path}")
    print(f"\nnext: jev evaluate --input {out_path} --dataset {dataset_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
