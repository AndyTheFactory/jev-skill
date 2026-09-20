"""Bounded per-task call budget: caps actual provider calls per task id.

Only counted against cache misses (an actual provider call), since a cache
hit costs nothing extra. Enforced only when a task id is supplied (via a
baseline), so single ad-hoc decisions outside any tracked task are never
budget-limited.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_MAX_CALLS_PER_WINDOW = 20
DEFAULT_WINDOW_SECONDS = 3600


def default_budget_dir() -> Path:
    """Resolved at call time (not import time) so ``$HOME`` overrides take effect."""
    return Path.home() / ".jev" / "budget"


class BudgetExceededError(Exception):
    """Raised when a task id has used up its provider-call budget for the window."""


def _key_path(task_id: str, directory: Path) -> Path:
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    return directory / f"{digest}.json"


def check_and_increment(
    task_id: str,
    *,
    directory: Path | None = None,
    max_calls: int = DEFAULT_MAX_CALLS_PER_WINDOW,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    now: datetime | None = None,
) -> None:
    """Record one provider call for ``task_id``, raising if over budget for the window."""
    directory = directory if directory is not None else default_budget_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _key_path(task_id, directory)
    now = now or datetime.now(UTC)

    timestamps: list[datetime] = []
    if path.is_file():
        try:
            timestamps = [datetime.fromisoformat(t) for t in json.loads(path.read_text())]
        except (ValueError, OSError):
            timestamps = []

    window_start = now - timedelta(seconds=window_seconds)
    timestamps = [t for t in timestamps if t >= window_start]

    if len(timestamps) >= max_calls:
        raise BudgetExceededError(
            f"task {task_id!r} exceeded {max_calls} Jev calls within {window_seconds}s"
        )

    timestamps.append(now)
    path.write_text(json.dumps([t.isoformat() for t in timestamps]))
