"""Independent Claude baseline, captured before Jev inference.

A baseline is the action Claude would take on its own -- recorded from a
file the caller supplies *before* invoking the provider, never inferred
from a Jev result. It is a comparison point for offline evaluation (see
M3), never ground truth, and it is immutable once loaded.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_BASELINE_DIR = Path.home() / ".jev" / "baseline"
_RECORD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class BaselineError(Exception):
    """Raised for an unreadable, invalid, or out-of-order baseline."""


class Baseline(BaseModel):
    """An independently recorded action, immutable once constructed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1, max_length=2000)
    task_id: str | None = None
    context_fingerprint: str | None = None
    recorded_at: datetime


def load_baseline(path: Path, *, now: datetime | None = None) -> Baseline:
    """Load and validate a baseline file, enforcing it precedes ``now``."""
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError(f"could not read baseline file: {exc}") from exc

    try:
        baseline = Baseline.model_validate(raw)
    except ValidationError as exc:
        raise BaselineError(f"invalid baseline: {exc}") from exc

    now = now or datetime.now(UTC)
    recorded_at = baseline.recorded_at
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=UTC)
    if recorded_at > now:
        raise BaselineError(
            "baseline recorded_at is in the future; a baseline must be "
            "captured before the decision it precedes, never after"
        )
    return baseline


def _path_for(record_id: str, directory: Path) -> Path:
    if not _RECORD_ID_RE.match(record_id):
        raise ValueError(f"malformed record id: {record_id!r}")
    return directory / f"{record_id}.json"


def save_protected(baseline: Baseline, record_id: str, *, directory: Path | None = None) -> None:
    """Persist a baseline alongside its decision's record id."""
    directory = directory or DEFAULT_BASELINE_DIR
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _path_for(record_id, directory)
    path.write_text(baseline.model_dump_json())


def load_protected(record_id: str, *, directory: Path | None = None) -> Baseline | None:
    """Load a previously persisted baseline for a record id, if one exists."""
    directory = directory or DEFAULT_BASELINE_DIR
    path = _path_for(record_id, directory)
    if not path.is_file():
        return None
    return Baseline.model_validate_json(path.read_text())
