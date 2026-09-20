"""Tests for the per-task provider-call budget."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jev_decisions.budget import BudgetExceededError, check_and_increment


def test_calls_within_budget_do_not_raise(tmp_path: Path) -> None:
    for _ in range(5):
        check_and_increment("task-1", directory=tmp_path, max_calls=5)


def test_exceeding_budget_raises(tmp_path: Path) -> None:
    for _ in range(3):
        check_and_increment("task-1", directory=tmp_path, max_calls=3)
    with pytest.raises(BudgetExceededError):
        check_and_increment("task-1", directory=tmp_path, max_calls=3)


def test_old_calls_outside_window_do_not_count(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for _ in range(3):
        check_and_increment(
            "task-1", directory=tmp_path, max_calls=3, window_seconds=3600, now=now
        )
    later = now + timedelta(hours=2)
    check_and_increment("task-1", directory=tmp_path, max_calls=3, window_seconds=3600, now=later)


def test_different_tasks_have_independent_budgets(tmp_path: Path) -> None:
    for _ in range(3):
        check_and_increment("task-1", directory=tmp_path, max_calls=3)
    check_and_increment("task-2", directory=tmp_path, max_calls=3)  # independent budget


def test_corrupt_budget_file_treated_as_empty(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    import hashlib

    digest = hashlib.sha256(b"task-1").hexdigest()
    (tmp_path / f"{digest}.json").write_text("not json")
    check_and_increment("task-1", directory=tmp_path, max_calls=1)  # does not crash
