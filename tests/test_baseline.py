"""Tests for independent baseline capture, ordering and persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from jev_decisions.baseline import (
    Baseline,
    BaselineError,
    load_baseline,
    load_protected,
    save_protected,
)


def write(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload))
    return path


def test_valid_baseline_loads(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    path = write(
        tmp_path / "baseline.json",
        {
            "action": "Reproduce the failing test locally first.",
            "task_id": "task-1",
            "recorded_at": (now - timedelta(seconds=1)).isoformat(),
        },
    )
    baseline = load_baseline(path, now=now)
    assert baseline.action.startswith("Reproduce")
    assert baseline.task_id == "task-1"


def test_baseline_is_frozen() -> None:
    baseline = Baseline(action="do X", recorded_at=datetime.now(UTC))
    with pytest.raises(ValidationError):
        baseline.action = "do Y"


def test_future_recorded_at_rejected(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    path = write(
        tmp_path / "baseline.json",
        {"action": "do X", "recorded_at": (now + timedelta(seconds=5)).isoformat()},
    )
    with pytest.raises(BaselineError, match="future"):
        load_baseline(path, now=now)


def test_missing_file_raises_baseline_error(tmp_path: Path) -> None:
    with pytest.raises(BaselineError, match="could not read"):
        load_baseline(tmp_path / "missing.json")


def test_invalid_json_raises_baseline_error(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text("{not json")
    with pytest.raises(BaselineError, match="could not read"):
        load_baseline(path)


def test_missing_action_field_raises_baseline_error(tmp_path: Path) -> None:
    path = write(tmp_path / "baseline.json", {"recorded_at": datetime.now(UTC).isoformat()})
    with pytest.raises(BaselineError, match="invalid baseline"):
        load_baseline(path)


def test_naive_datetime_treated_as_utc(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    path = write(
        tmp_path / "baseline.json",
        {"action": "do X", "recorded_at": "2025-12-31T23:59:00"},
    )
    baseline = load_baseline(path, now=now)
    assert baseline.action == "do X"


def test_unavailable_baseline_is_a_normal_none(tmp_path: Path) -> None:
    assert load_protected("a" * 32, directory=tmp_path) is None


def test_save_and_load_protected_round_trip(tmp_path: Path) -> None:
    baseline = Baseline(action="investigate logs first", recorded_at=datetime.now(UTC))
    record_id = "b" * 32
    save_protected(baseline, record_id, directory=tmp_path)
    loaded = load_protected(record_id, directory=tmp_path)
    assert loaded == baseline
