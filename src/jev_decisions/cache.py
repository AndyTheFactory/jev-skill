"""Bounded, TTL'd, file-backed cache of policy Decisions, keyed by fingerprint.

Persists to disk (not just in-process) since each `jev decide` invocation is
a fresh process. Writes are atomic (write-then-rename) so concurrent callers
never observe a partially written entry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from jev_decisions.policy import Decision

DEFAULT_TTL_SECONDS = 3600
DEFAULT_MAX_ENTRIES = 500


def default_cache_dir() -> Path:
    """Resolved at call time (not import time) so ``$HOME`` overrides take effect."""
    return Path.home() / ".jev" / "cache"


class CacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    decision: Decision
    created_at: datetime


def get(
    fingerprint: str,
    *,
    directory: Path | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: datetime | None = None,
) -> Decision | None:
    """Return the cached Decision for ``fingerprint``, or None on miss/expiry/corruption."""
    directory = directory if directory is not None else default_cache_dir()
    path = directory / f"{fingerprint}.json"
    if not path.is_file():
        return None

    try:
        entry = CacheEntry.model_validate_json(path.read_text())
    except (ValueError, OSError):
        return None  # corrupt or unreadable entry: treat as a miss, not a crash

    now = now or datetime.now(UTC)
    created_at = entry.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    if (now - created_at).total_seconds() > ttl_seconds:
        path.unlink(missing_ok=True)
        return None
    return entry.decision


def put(
    fingerprint: str,
    decision: Decision,
    *,
    directory: Path | None = None,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> None:
    directory = directory if directory is not None else default_cache_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    entry = CacheEntry(fingerprint=fingerprint, decision=decision, created_at=datetime.now(UTC))

    path = directory / f"{fingerprint}.json"
    tmp_path = directory / f".{fingerprint}.{uuid4().hex}.tmp"
    tmp_path.write_text(entry.model_dump_json())
    tmp_path.replace(path)  # atomic on POSIX: concurrent writers never see a partial file

    _evict_oldest_if_over_capacity(directory, max_entries)


def _evict_oldest_if_over_capacity(directory: Path, max_entries: int) -> None:
    files = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
    excess = len(files) - max_entries
    for stale in files[: max(0, excess)]:
        stale.unlink(missing_ok=True)
