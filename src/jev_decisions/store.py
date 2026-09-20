"""Protected result store: full Decision content, kept out of ordinary stdout.

Shadow mode persists the complete policy Decision (selected option,
probabilities-derived fields, reasoning) here, keyed by a fresh record ID
per call. It is only ever readable back through an explicit, separate
command (`jev reveal`), never through the normal `jev decide` output path.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from uuid import uuid4

from jev_decisions.policy import Decision

# \Z (not a bare $) so a trailing "\n" (e.g. from `$(cat file)`) is rejected
# instead of silently matching.
_RECORD_ID_RE = re.compile(r"^[0-9a-f]{32}\Z")


def default_store_dir() -> Path:
    """Resolved at call time (not import time) so ``$HOME`` overrides take effect."""
    return Path.home() / ".jev" / "shadow"


class RecordNotFoundError(Exception):
    """Raised when reveal is asked for a record ID that has no stored result."""


class InvalidRecordIdError(Exception):
    """Raised for a record id that isn't a well-formed id (defense against path traversal)."""


def new_record_id() -> str:
    return uuid4().hex


def _path_for(record_id: str, directory: Path) -> Path:
    if not _RECORD_ID_RE.match(record_id):
        raise InvalidRecordIdError(f"malformed record id: {record_id!r}")
    return directory / f"{record_id}.json"


def save(decision: Decision, record_id: str, *, directory: Path | None = None) -> None:
    directory = directory if directory is not None else default_store_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _path_for(record_id, directory)
    tmp_path = path.with_suffix(f".{uuid4().hex}.tmp")
    tmp_path.write_text(json.dumps(decision.model_dump(mode="json")))
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
    tmp_path.replace(path)  # atomic: a crash mid-write never leaves a truncated record


def load(record_id: str, *, directory: Path | None = None) -> Decision:
    directory = directory if directory is not None else default_store_dir()
    path = _path_for(record_id, directory)
    if not path.is_file():
        raise RecordNotFoundError(f"no protected result for record id {record_id!r}")
    try:
        return Decision.model_validate(json.loads(path.read_text()))
    except (ValueError, OSError) as exc:
        raise RecordNotFoundError(
            f"protected result for record id {record_id!r} is corrupt: {exc}"
        ) from None
