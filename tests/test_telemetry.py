"""Tests for telemetry privacy, retention, and write-failure isolation."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jev_decisions.config import TelemetryConfig
from jev_decisions.telemetry import TelemetryEvent, prune_expired, read_events, write_event


def make_event(**overrides: object) -> TelemetryEvent:
    fields: dict[str, object] = dict(
        id="a" * 32,
        fingerprint="f" * 64,
        timestamp=datetime.now(UTC),
        profile="task-routing",
        model="~typesafe/jev-latest",
        mode="shadow",
        outcome="accepted",
        latency_ms=42.0,
    )
    fields.update(overrides)
    return TelemetryEvent.model_validate(fields)


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    config = TelemetryConfig(path=str(tmp_path / "telemetry.jsonl"))
    event = make_event()
    write_event(config, event)
    events = read_events(config)
    assert len(events) == 1
    assert events[0].id == event.id
    assert events[0].outcome == "accepted"


def test_disabled_telemetry_writes_nothing(tmp_path: Path) -> None:
    config = TelemetryConfig(path=str(tmp_path / "telemetry.jsonl"), enabled=False)
    write_event(config, make_event())
    assert not (tmp_path / "telemetry.jsonl").exists()


def test_no_api_key_or_context_fields_exist_on_event() -> None:
    fields = set(TelemetryEvent.model_fields)
    assert "api_key" not in fields
    assert "context" not in fields
    assert "raw_context" not in fields
    assert "provider_payload" not in fields


def test_file_permissions_owner_only(tmp_path: Path) -> None:
    config = TelemetryConfig(path=str(tmp_path / "telemetry.jsonl"))
    write_event(config, make_event())
    mode = (tmp_path / "telemetry.jsonl").stat().st_mode
    assert stat.S_IMODE(mode) == stat.S_IRUSR | stat.S_IWUSR


def test_credential_pattern_in_profile_is_redacted(tmp_path: Path) -> None:
    config = TelemetryConfig(path=str(tmp_path / "telemetry.jsonl"))
    write_event(config, make_event(profile="sk-abcdefghijklmnop leaked"))
    raw = (tmp_path / "telemetry.jsonl").read_text()
    assert "sk-abcdefghijklmnop" not in raw
    assert "[REDACTED]" in raw


def test_write_failure_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point telemetry at a path whose parent can't be created (file, not dir).
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    config = TelemetryConfig(path=str(blocker / "telemetry.jsonl"))
    write_event(config, make_event())  # must not raise


def test_prune_removes_events_older_than_retention(tmp_path: Path) -> None:
    config = TelemetryConfig(path=str(tmp_path / "telemetry.jsonl"), retention_days=1)
    now = datetime(2026, 1, 10, tzinfo=UTC)
    write_event(config, make_event(timestamp=now - timedelta(days=5)))
    write_event(config, make_event(timestamp=now - timedelta(hours=1)))

    kept = prune_expired(config, now=now)
    assert kept == 1
    events = read_events(config)
    assert len(events) == 1
    assert events[0].timestamp > now - timedelta(days=1)


def test_prune_survives_concurrent_append(tmp_path: Path) -> None:
    import threading

    config = TelemetryConfig(path=str(tmp_path / "telemetry.jsonl"), retention_days=1)
    now = datetime(2026, 1, 10, tzinfo=UTC)
    write_event(config, make_event(timestamp=now - timedelta(days=5)))

    def append_during_prune() -> None:
        write_event(config, make_event(id="c" * 32, timestamp=now))

    t = threading.Thread(target=append_during_prune)
    t.start()
    prune_expired(config, now=now)
    t.join()

    events = read_events(config)
    # the old event must be gone, and the concurrently appended one must survive
    assert any(e.id == "c" * 32 for e in events)
    assert all(e.timestamp > now - timedelta(days=1) for e in events)


def test_prune_on_missing_file_is_a_noop(tmp_path: Path) -> None:
    config = TelemetryConfig(path=str(tmp_path / "missing.jsonl"))
    assert prune_expired(config) == 0


def test_shadow_result_joinable_to_baseline_by_id(tmp_path: Path) -> None:
    config = TelemetryConfig(path=str(tmp_path / "telemetry.jsonl"))
    write_event(config, make_event(id="b" * 32, baseline_task_id="task-42"))
    events = read_events(config)
    assert events[0].id == "b" * 32
    assert events[0].baseline_task_id == "task-42"


def test_event_json_round_trips_from_disk(tmp_path: Path) -> None:
    config = TelemetryConfig(path=str(tmp_path / "telemetry.jsonl"))
    write_event(config, make_event())
    line = (tmp_path / "telemetry.jsonl").read_text().strip()
    parsed = json.loads(line)
    assert parsed["outcome"] == "accepted"
    assert "context" not in parsed
