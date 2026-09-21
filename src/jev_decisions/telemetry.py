"""Minimal local telemetry: JSONL events, privacy-controlled, with retention.

Events carry only the fields needed to evaluate the system offline (see
M3): id/fingerprint, timestamp, profile/model, mode, policy outcome and
latency. They never carry raw context, provider payloads, or credentials --
those fields simply don't exist on :class:`TelemetryEvent`, so there is
nothing to leak by construction. A write failure never corrupts or blocks a
completed decision: it's swallowed and the caller proceeds as normal.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from jev_decisions.config import TelemetryConfig

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platform
    fcntl = None  # type: ignore[assignment]

_SECRET_PATTERN = re.compile(
    r"sk-[A-Za-z0-9]{10,}|Bearer\s+\S+|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}"
)


class TelemetryEvent(BaseModel):
    """One JSONL record. No raw context or provider payload fields exist here."""

    model_config = ConfigDict(extra="forbid")

    id: str
    fingerprint: str
    timestamp: datetime
    profile: str | None
    model: str
    provider: str | None = None
    mode: str
    outcome: str
    latency_ms: float
    cost: float | None = None
    baseline_task_id: str | None = None


def _redact(value: str | None) -> str | None:
    if value is None:
        return None
    return _SECRET_PATTERN.sub("[REDACTED]", value)


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Exclusive advisory lock so an append and a prune-rewrite never interleave."""
    if fcntl is None:  # pragma: no cover - non-POSIX platform: best effort, no lock
        yield
        return
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def write_event(config: TelemetryConfig, event: TelemetryEvent) -> None:
    """Append one redacted JSONL line. Never raises: failures are swallowed."""
    if not config.enabled:
        return
    try:
        path = Path(config.path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = event.model_dump(mode="json")
        payload["profile"] = _redact(payload["profile"])
        payload["model"] = _redact(payload["model"])
        payload["baseline_task_id"] = _redact(payload["baseline_task_id"])
        with _locked(path), path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        return


def prune_expired(config: TelemetryConfig, *, now: datetime | None = None) -> int:
    """Drop events older than ``retention_days``. Returns the number of events kept."""
    path = Path(config.path).expanduser()
    if not path.is_file():
        return 0

    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=config.retention_days)

    with _locked(path):
        kept: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = TelemetryEvent.model_validate_json(line)
            except ValueError:
                continue  # drop unparsable lines rather than fail retention
            timestamp = event.timestamp
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            if timestamp >= cutoff:
                kept.append(line)

        tmp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        tmp_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
        tmp_path.replace(path)  # atomic swap under the same lock write_event takes
    return len(kept)


def read_events(config: TelemetryConfig) -> list[TelemetryEvent]:
    path = Path(config.path).expanduser()
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(TelemetryEvent.model_validate_json(line))
    return events
