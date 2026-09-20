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

DEFAULT_STORE_DIR = Path.home() / ".jev" / "shadow"
_RECORD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


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
    directory = directory or DEFAULT_STORE_DIR
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _path_for(record_id, directory)
    path.write_text(json.dumps(decision.model_dump(mode="json")))
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def load(record_id: str, *, directory: Path | None = None) -> Decision:
    directory = directory or DEFAULT_STORE_DIR
    path = _path_for(record_id, directory)
    if not path.is_file():
        raise RecordNotFoundError(f"no protected result for record id {record_id!r}")
    return Decision.model_validate(json.loads(path.read_text()))
