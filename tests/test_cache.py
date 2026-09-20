"""Tests for the bounded, TTL'd, file-backed decision cache."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jev_decisions import cache
from jev_decisions.policy import Decision


def make_decision() -> Decision:
    return Decision(outcome="accepted", selected_option_id="a", probability=0.9, reason="ok")


def test_put_then_get_round_trips(tmp_path: Path) -> None:
    decision = make_decision()
    cache.put("fp1", decision, directory=tmp_path)
    assert cache.get("fp1", directory=tmp_path) == decision


def test_missing_key_is_a_miss(tmp_path: Path) -> None:
    assert cache.get("nope", directory=tmp_path) is None


def test_expired_entry_not_reused(tmp_path: Path) -> None:
    decision = make_decision()
    old_now = datetime(2020, 1, 1, tzinfo=UTC)
    entry = cache.CacheEntry(fingerprint="fp1", decision=decision, created_at=old_now)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "fp1.json").write_text(entry.model_dump_json())

    result = cache.get(
        "fp1", directory=tmp_path, ttl_seconds=3600, now=old_now + timedelta(hours=2)
    )
    assert result is None
    assert not (tmp_path / "fp1.json").exists()  # expired entries are cleaned up


def test_entry_within_ttl_reused(tmp_path: Path) -> None:
    decision = make_decision()
    cache.put("fp1", decision, directory=tmp_path)
    # simulate elapsed time within TTL by reading with an explicit `now`
    result = cache.get("fp1", directory=tmp_path, ttl_seconds=3600, now=datetime.now(UTC))
    assert result == decision


def test_corrupt_entry_treated_as_miss_not_crash(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "fp1.json").write_text("{not valid json")
    assert cache.get("fp1", directory=tmp_path) is None


def test_eviction_keeps_bounded_entry_count(tmp_path: Path) -> None:
    for i in range(10):
        cache.put(f"fp{i}", make_decision(), directory=tmp_path, max_entries=5)
    remaining = list(tmp_path.glob("*.json"))
    assert len(remaining) == 5


def test_entry_file_permissions_owner_only(tmp_path: Path) -> None:
    import stat as stat_module

    cache.put("fp1", make_decision(), directory=tmp_path)
    mode = (tmp_path / "fp1.json").stat().st_mode
    assert stat_module.S_IMODE(mode) == stat_module.S_IRUSR | stat_module.S_IWUSR


def test_eviction_survives_concurrently_deleted_file(tmp_path: Path) -> None:
    for i in range(5):
        cache.put(f"fp{i}", make_decision(), directory=tmp_path, max_entries=100)
    (tmp_path / "fp2.json").unlink()  # simulate a concurrent writer removing it mid-eviction
    cache.put("fp5", make_decision(), directory=tmp_path, max_entries=3)  # must not raise


def test_concurrent_puts_do_not_corrupt_or_crash(tmp_path: Path) -> None:
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            cache.put(f"fp{i % 3}", make_decision(), directory=tmp_path, max_entries=100)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    # every written entry must still be individually valid JSON
    for fp in ("fp0", "fp1", "fp2"):
        assert cache.get(fp, directory=tmp_path) is not None
