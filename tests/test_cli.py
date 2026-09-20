"""Smoke tests for the M0 bootstrap CLI and package."""

from __future__ import annotations

import subprocess
import sys

import pytest
from _pytest.capture import CaptureFixture

from jev_decisions import __version__
from jev_decisions.cli import main


def test_help(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "usage: jev" in capsys.readouterr().out


def test_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "jev_decisions", "--version"],
        check=True,
        text=True,
        capture_output=True,
    )
    assert result.stdout.strip() == f"jev {__version__}"


def test_library_import() -> None:
    assert __version__ == "0.1.0"
