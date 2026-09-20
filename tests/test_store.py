"""Tests for the protected shadow-result store."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from jev_decisions import store
from jev_decisions.policy import Decision


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    decision = Decision(outcome="accepted", selected_option_id="a", probability=0.9, reason="ok")
    record_id = store.new_record_id()
    store.save(decision, record_id, directory=tmp_path)
    loaded = store.load(record_id, directory=tmp_path)
    assert loaded == decision


def test_file_permissions_owner_only(tmp_path: Path) -> None:
    decision = Decision(outcome="failed", reason="provider error")
    record_id = store.new_record_id()
    store.save(decision, record_id, directory=tmp_path)
    mode = (tmp_path / f"{record_id}.json").stat().st_mode
    assert stat.S_IMODE(mode) == stat.S_IRUSR | stat.S_IWUSR


def test_load_unknown_record_raises(tmp_path: Path) -> None:
    with pytest.raises(store.RecordNotFoundError):
        store.load(store.new_record_id(), directory=tmp_path)


def test_load_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(store.InvalidRecordIdError):
        store.load("../../../../etc/passwd", directory=tmp_path)


def test_save_rejects_malformed_record_id(tmp_path: Path) -> None:
    decision = Decision(outcome="failed", reason="x")
    with pytest.raises(store.InvalidRecordIdError):
        store.save(decision, "not-a-hex-id", directory=tmp_path)


def test_record_ids_are_unique() -> None:
    ids = {store.new_record_id() for _ in range(100)}
    assert len(ids) == 100


def test_trailing_newline_record_id_rejected(tmp_path: Path) -> None:
    with pytest.raises(store.InvalidRecordIdError):
        store.load("a" * 32 + "\n", directory=tmp_path)


def test_corrupt_record_file_raises_not_found_not_crash(tmp_path: Path) -> None:
    record_id = store.new_record_id()
    (tmp_path / f"{record_id}.json").write_text("{not valid json")
    with pytest.raises(store.RecordNotFoundError, match="corrupt"):
        store.load(record_id, directory=tmp_path)
