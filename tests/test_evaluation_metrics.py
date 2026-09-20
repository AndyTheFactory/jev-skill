"""Golden-fixture tests for evaluation metrics: exact values and denominators."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jev_decisions import baseline as baseline_module
from jev_decisions import store
from jev_decisions.baseline import Baseline
from jev_decisions.evaluation.dataset import DatasetExample, Label
from jev_decisions.evaluation.metrics import (
    NOT_AVAILABLE,
    EvaluationError,
    RunEntry,
    build_report,
    load_manifest,
)
from jev_decisions.policy import Decision
from jev_decisions.schemas import ChoiceOption
from jev_decisions.telemetry import TelemetryEvent


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "default_store_dir", lambda: tmp_path / "store")
    monkeypatch.setattr(baseline_module, "default_baseline_dir", lambda: tmp_path / "baseline")


def make_example(
    example_id: str,
    *,
    profile: str | None = "task-routing",
    correct_option_id: str | None = "coding",
    labeled: bool = True,
) -> DatasetExample:
    kwargs: dict[str, object] = dict(
        id=example_id,
        dataset_version="v1",
        split="dev",
        category="typical",
        profile=profile,
    )
    if profile is None:
        kwargs["question"] = "Which?"
        kwargs["options"] = (
            ChoiceOption(id="a", description="A"),
            ChoiceOption(id="b", description="B"),
        )
    if labeled:
        kwargs["label"] = Label(correct_option_id=correct_option_id, source="reviewed_label")
    return DatasetExample.model_validate(kwargs)


def put_decision(record_id: str, decision: Decision) -> None:
    store.save(decision, record_id)


def accepted(
    selected_option_id: str,
    probability: float = 0.9,
    *,
    confidence: float | None = None,
    reason: str = "r",
) -> Decision:
    return Decision(
        outcome="accepted",
        selected_option_id=selected_option_id,
        probability=probability,
        confidence=confidence,
        reason=reason,
    )


def test_accuracy_and_denominator_exact(tmp_path: Path) -> None:
    dataset = {
        "e1": make_example("e1", correct_option_id="coding"),
        "e2": make_example("e2", correct_option_id="debugging"),
        "e3": make_example("e3", labeled=False),  # unlabeled: excluded from accuracy
    }
    put_decision("a" * 32, accepted("coding"))
    put_decision("b" * 32, accepted("research", 0.85))
    put_decision("c" * 32, accepted("coding"))

    entries = [
        RunEntry(example_id="e1", record_id="a" * 32),
        RunEntry(example_id="e2", record_id="b" * 32),
        RunEntry(example_id="e3", record_id="c" * 32),
    ]
    report = build_report(entries, dataset, [])

    assert report.overall.n == 3
    assert report.overall.n_labeled == 2
    assert report.overall.accuracy == pytest.approx(0.5)  # 1 correct of 2 labeled+accepted
    assert report.overall.accuracy_n == 2


def test_missing_label_reports_not_available_not_zero(tmp_path: Path) -> None:
    dataset = {"e1": make_example("e1", labeled=False)}
    put_decision("a" * 32, accepted("coding"))
    entries = [RunEntry(example_id="e1", record_id="a" * 32)]

    report = build_report(entries, dataset, [])
    assert report.overall.accuracy == NOT_AVAILABLE
    assert report.overall.accuracy_n == 0


def test_coverage_abstention_rejected_failed_rates(tmp_path: Path) -> None:
    dataset = {f"e{i}": make_example(f"e{i}", labeled=False) for i in range(4)}
    put_decision("a" * 32, accepted("coding"))
    put_decision(
        "b" * 32,
        Decision(outcome="abstained", selected_option_id="coding", probability=0.5, reason="r"),
    )
    put_decision("c" * 32, Decision(outcome="rejected", reason="r"))
    put_decision("d" * 32, Decision(outcome="failed", reason="r"))
    entries = [
        RunEntry(example_id="e0", record_id="a" * 32),
        RunEntry(example_id="e1", record_id="b" * 32),
        RunEntry(example_id="e2", record_id="c" * 32),
        RunEntry(example_id="e3", record_id="d" * 32),
    ]

    report = build_report(entries, dataset, [])
    assert report.overall.coverage == pytest.approx(0.25)
    assert report.overall.abstention_rate == pytest.approx(0.25)
    assert report.overall.rejected_rate == pytest.approx(0.25)
    assert report.overall.failed_rate == pytest.approx(0.25)


def test_confidence_and_probability_reported_separately(tmp_path: Path) -> None:
    dataset = {"e1": make_example("e1", labeled=False)}
    put_decision(
        "a" * 32,
        accepted("coding", 0.7, confidence=0.95),
    )
    entries = [RunEntry(example_id="e1", record_id="a" * 32)]

    report = build_report(entries, dataset, [])
    assert report.overall.mean_selected_probability == pytest.approx(0.7)
    assert report.overall.mean_confidence == pytest.approx(0.95)
    assert report.overall.mean_selected_probability != report.overall.mean_confidence


def test_confidence_not_available_when_provider_never_returns_it(tmp_path: Path) -> None:
    dataset = {"e1": make_example("e1", labeled=False)}
    put_decision(
        "a" * 32, accepted("coding", 0.7)
    )
    entries = [RunEntry(example_id="e1", record_id="a" * 32)]

    report = build_report(entries, dataset, [])
    assert report.overall.mean_confidence == NOT_AVAILABLE


def test_baseline_agreement_labeled_not_a_correctness_proxy(tmp_path: Path) -> None:
    dataset = {"e1": make_example("e1", labeled=False)}
    put_decision(
        "a" * 32, accepted("lru")
    )
    baseline_module.save_protected(
        Baseline(action="use an in-process lru cache", recorded_at=datetime.now(UTC)), "a" * 32
    )
    entries = [RunEntry(example_id="e1", record_id="a" * 32)]

    report = build_report(entries, dataset, [])
    assert report.overall.baseline_agreement == pytest.approx(1.0)
    assert report.overall.baseline_agreement_n == 1


def test_baseline_agreement_not_available_without_baselines(tmp_path: Path) -> None:
    dataset = {"e1": make_example("e1", labeled=False)}
    put_decision(
        "a" * 32, accepted("coding")
    )
    entries = [RunEntry(example_id="e1", record_id="a" * 32)]

    report = build_report(entries, dataset, [])
    assert report.overall.baseline_agreement == NOT_AVAILABLE


def test_latency_and_cost_from_telemetry(tmp_path: Path) -> None:
    dataset = {"e1": make_example("e1", labeled=False), "e2": make_example("e2", labeled=False)}
    put_decision(
        "a" * 32, accepted("coding")
    )
    put_decision(
        "b" * 32, accepted("coding")
    )
    events = [
        TelemetryEvent(
            id="a" * 32,
            fingerprint="f1",
            timestamp=datetime.now(UTC),
            profile="task-routing",
            model="m",
            mode="shadow",
            outcome="accepted",
            latency_ms=100.0,
            cost=0.01,
        ),
        TelemetryEvent(
            id="b" * 32,
            fingerprint="f1",
            timestamp=datetime.now(UTC),
            profile="task-routing",
            model="m",
            mode="shadow",
            outcome="accepted",
            latency_ms=200.0,
        ),
    ]
    entries = [
        RunEntry(example_id="e1", record_id="a" * 32),
        RunEntry(example_id="e2", record_id="b" * 32),
    ]

    report = build_report(entries, dataset, events)
    assert report.overall.mean_latency_ms == pytest.approx(150.0)
    assert report.overall.mean_cost == pytest.approx(0.01)  # only 1 of 2 has cost


def test_per_profile_breakdown(tmp_path: Path) -> None:
    dataset = {
        "e1": make_example("e1", profile="task-routing", labeled=False),
        "e2": make_example("e2", profile="review-triage", labeled=False),
        "e3": make_example("e3", profile=None, labeled=False),
    }
    for rid in ("a" * 32, "b" * 32, "c" * 32):
        put_decision(
            rid, accepted("x")
        )
    entries = [
        RunEntry(example_id="e1", record_id="a" * 32),
        RunEntry(example_id="e2", record_id="b" * 32),
        RunEntry(example_id="e3", record_id="c" * 32),
    ]

    report = build_report(entries, dataset, [])
    assert set(report.by_profile) == {"task-routing", "review-triage", "dynamic"}
    assert report.by_profile["task-routing"].n == 1
    assert report.by_profile["dynamic"].n == 1


def test_dynamic_questions_grouped_by_fingerprint(tmp_path: Path) -> None:
    dataset = {
        "e1": make_example("e1", profile=None, labeled=False),
        "e2": make_example("e2", profile=None, labeled=False),
    }
    put_decision(
        "a" * 32, accepted("a")
    )
    put_decision(
        "b" * 32, accepted("a")
    )
    def same_fp_event(record_id: str) -> TelemetryEvent:
        return TelemetryEvent(
            id=record_id,
            fingerprint="same-question-fp",
            timestamp=datetime.now(UTC),
            profile=None,
            model="m",
            mode="shadow",
            outcome="accepted",
            latency_ms=1.0,
        )

    entries = [
        RunEntry(example_id="e1", record_id="a" * 32),
        RunEntry(example_id="e2", record_id="b" * 32),
    ]

    report = build_report(entries, dataset, [same_fp_event("a" * 32), same_fp_event("b" * 32)])
    assert report.dynamic_by_fingerprint["same-question-fp"].n == 2


def test_missing_record_reported_not_crashed(tmp_path: Path) -> None:
    dataset = {"e1": make_example("e1", labeled=False)}
    entries = [RunEntry(example_id="e1", record_id="a" * 32)]  # never saved

    report = build_report(entries, dataset, [])
    assert "e1" in report.missing_records
    assert report.overall.n == 0


def test_unknown_example_id_reported_not_crashed(tmp_path: Path) -> None:
    put_decision(
        "a" * 32, accepted("coding")
    )
    entries = [RunEntry(example_id="does-not-exist", record_id="a" * 32)]

    report = build_report(entries, {}, [])
    assert "does-not-exist" in report.missing_records


def test_malformed_record_id_reported_not_crashed(tmp_path: Path) -> None:
    dataset = {"e1": make_example("e1", labeled=False)}
    entries = [RunEntry(example_id="e1", record_id="not-a-valid-hex-id")]

    report = build_report(entries, dataset, [])
    assert "e1" in report.missing_records
    assert report.overall.n == 0


def test_rates_not_available_when_no_records_not_fabricated_zero(tmp_path: Path) -> None:
    dataset = {"e1": make_example("e1", labeled=False)}
    entries = [RunEntry(example_id="e1", record_id="a" * 32)]  # never saved: 0 joined records

    report = build_report(entries, dataset, [])
    assert report.overall.n == 0
    assert report.overall.coverage == NOT_AVAILABLE
    assert report.overall.abstention_rate == NOT_AVAILABLE
    assert report.overall.rejected_rate == NOT_AVAILABLE
    assert report.overall.failed_rate == NOT_AVAILABLE


def test_baseline_agreement_no_partial_word_false_positive(tmp_path: Path) -> None:
    dataset = {"e1": make_example("e1", labeled=False)}
    put_decision("a" * 32, accepted("coding"))
    baseline_module.save_protected(
        Baseline(action="fix an encoding bug in the parser", recorded_at=datetime.now(UTC)),
        "a" * 32,
    )
    entries = [RunEntry(example_id="e1", record_id="a" * 32)]

    report = build_report(entries, dataset, [])
    # "coding" must not match inside "encoding"
    assert report.overall.baseline_agreement == pytest.approx(0.0)


def test_profile_based_label_validated_against_real_profile_options(tmp_path: Path) -> None:
    from jev_decisions.evaluation.dataset import DatasetError, load_dataset

    bad = tmp_path / "bad.yaml"
    bad.write_text(
        """
id: bad
dataset_version: v1
split: dev
category: typical
profile: task-routing
context: "x"
label:
  correct_option_id: codeing
  source: reviewed_label
"""
    )
    with pytest.raises(DatasetError, match="not among profile"):
        load_dataset(tmp_path)


def test_load_manifest_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps([{"example_id": "e1", "record_id": "a" * 32}]))
    entries = load_manifest(path)
    assert entries == [RunEntry(example_id="e1", record_id="a" * 32)]


def test_load_manifest_rejects_non_list(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"not": "a list"}))
    with pytest.raises(EvaluationError, match="must be a JSON list"):
        load_manifest(path)


def test_load_manifest_missing_file_raises_clean_error(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="could not read"):
        load_manifest(tmp_path / "missing.json")
