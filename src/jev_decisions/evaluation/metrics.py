"""Compute evaluation metrics from a run manifest, protected decisions and labels.

A "run" is produced by executing `jev decide` for each dataset example and
recording the resulting record id (see `eval/BENCHMARK.md`). This module
joins that manifest back to the protected store, baselines and telemetry by
record id, and reports metrics -- never fabricating accuracy where no label
exists, and never conflating provider confidence with the selected option's
own probability.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from jev_decisions import baseline as baseline_module
from jev_decisions import store
from jev_decisions.baseline import Baseline
from jev_decisions.evaluation.dataset import DatasetExample
from jev_decisions.policy import Decision
from jev_decisions.telemetry import TelemetryEvent

NOT_AVAILABLE: Literal["not available"] = "not available"
Metric = float | Literal["not available"]


class EvaluationError(Exception):
    """Raised for a malformed run manifest."""


class RunEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    example_id: str
    record_id: str


class ProfileMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n: int
    n_labeled: int
    coverage: Metric
    abstention_rate: Metric
    rejected_rate: Metric
    failed_rate: Metric
    accuracy: Metric
    accuracy_n: int
    mean_selected_probability: Metric
    mean_confidence: Metric
    calibration_error: Metric
    calibration_n: int
    baseline_agreement: Metric
    baseline_agreement_n: int
    mean_latency_ms: Metric
    mean_cost: Metric


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str | None
    overall: ProfileMetrics
    by_profile: dict[str, ProfileMetrics]
    dynamic_by_fingerprint: dict[str, ProfileMetrics]
    missing_records: list[str]


class _Record(BaseModel):
    """One joined (example, decision, baseline, telemetry) tuple, internal to this module."""

    model_config = ConfigDict(extra="forbid")

    example: DatasetExample
    decision: Decision
    baseline: Baseline | None = None
    telemetry: TelemetryEvent | None = None


def load_manifest(path: Path) -> list[RunEntry]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"could not read run manifest: {exc}") from exc
    if not isinstance(raw, list):
        raise EvaluationError("run manifest must be a JSON list of {example_id, record_id}")
    try:
        return [RunEntry.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise EvaluationError(f"invalid run manifest entry: {exc}") from exc


def _mean(values: list[float]) -> Metric:
    return sum(values) / len(values) if values else NOT_AVAILABLE


def _aggregate(records: list[_Record]) -> ProfileMetrics:
    n = len(records)
    accepted = [r for r in records if r.decision.outcome == "accepted"]
    abstained = [r for r in records if r.decision.outcome == "abstained"]
    rejected = [r for r in records if r.decision.outcome == "rejected"]
    failed = [r for r in records if r.decision.outcome == "failed"]
    labeled = [r for r in records if r.example.label and r.example.label.correct_option_id]

    def _correct_id(record: _Record) -> str | None:
        assert record.example.label is not None
        return record.example.label.correct_option_id

    labeled_accepted = [
        r for r in accepted if r.example.label and r.example.label.correct_option_id
    ]
    correct = [r for r in labeled_accepted if r.decision.selected_option_id == _correct_id(r)]
    accuracy = len(correct) / len(labeled_accepted) if labeled_accepted else NOT_AVAILABLE

    probs = [r.decision.probability for r in accepted if r.decision.probability is not None]
    confidences = [r.decision.confidence for r in accepted if r.decision.confidence is not None]

    calib_records = [r for r in labeled_accepted if r.decision.probability is not None]
    calibration_errors = [
        abs(
            (r.decision.probability or 0.0)
            - (1.0 if r.decision.selected_option_id == _correct_id(r) else 0.0)
        )
        for r in calib_records
    ]

    def _mentions_option(record: _Record) -> bool:
        assert record.baseline is not None and record.decision.selected_option_id is not None
        pattern = r"\b" + re.escape(record.decision.selected_option_id.lower()) + r"\b"
        return re.search(pattern, record.baseline.action.lower()) is not None

    baseline_eligible = [r for r in accepted if r.baseline is not None]
    baseline_agree = [
        r
        for r in baseline_eligible
        if r.decision.selected_option_id is not None and _mentions_option(r)
    ]

    latencies = [r.telemetry.latency_ms for r in records if r.telemetry is not None]
    costs = [
        r.telemetry.cost
        for r in records
        if r.telemetry is not None and r.telemetry.cost is not None
    ]

    def _rate(count: int) -> Metric:
        return count / n if n else NOT_AVAILABLE

    return ProfileMetrics(
        n=n,
        n_labeled=len(labeled),
        coverage=_rate(len(accepted)),
        abstention_rate=_rate(len(abstained)),
        rejected_rate=_rate(len(rejected)),
        failed_rate=_rate(len(failed)),
        accuracy=accuracy,
        accuracy_n=len(labeled_accepted),
        mean_selected_probability=_mean(probs),
        mean_confidence=_mean(confidences),
        calibration_error=_mean(calibration_errors),
        calibration_n=len(calib_records),
        baseline_agreement=(
            len(baseline_agree) / len(baseline_eligible) if baseline_eligible else NOT_AVAILABLE
        ),
        baseline_agreement_n=len(baseline_eligible),
        mean_latency_ms=_mean(latencies),
        mean_cost=_mean(costs),
    )


def build_report(
    entries: list[RunEntry],
    dataset_by_id: dict[str, DatasetExample],
    telemetry_events: list[TelemetryEvent],
    *,
    store_dir: Path | None = None,
    baseline_dir: Path | None = None,
) -> EvaluationReport:
    """Join a run manifest to protected decisions/baselines/telemetry and score it."""
    telemetry_by_id = {e.id: e for e in telemetry_events}
    records: list[_Record] = []
    missing: list[str] = []

    for entry in entries:
        example = dataset_by_id.get(entry.example_id)
        if example is None:
            missing.append(entry.example_id)
            continue
        try:
            decision = store.load(entry.record_id, directory=store_dir)
        except (store.RecordNotFoundError, store.InvalidRecordIdError):
            missing.append(entry.example_id)
            continue
        baseline = baseline_module.load_protected(entry.record_id, directory=baseline_dir)
        telemetry = telemetry_by_id.get(entry.record_id)
        records.append(
            _Record(example=example, decision=decision, baseline=baseline, telemetry=telemetry)
        )

    by_profile: dict[str, list[_Record]] = {}
    for record in records:
        key = record.example.profile or "dynamic"
        by_profile.setdefault(key, []).append(record)

    dynamic_by_fingerprint: dict[str, list[_Record]] = {}
    for record in records:
        if record.example.profile is None and record.telemetry is not None:
            dynamic_by_fingerprint.setdefault(record.telemetry.fingerprint, []).append(record)

    versions = {r.example.dataset_version for r in records}
    dataset_version = next(iter(versions)) if len(versions) == 1 else None

    return EvaluationReport(
        dataset_version=dataset_version,
        overall=_aggregate(records),
        by_profile={k: _aggregate(v) for k, v in by_profile.items()},
        dynamic_by_fingerprint={k: _aggregate(v) for k, v in dynamic_by_fingerprint.items()},
        missing_records=missing,
    )
