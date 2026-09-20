"""Smoke tests for the M0 bootstrap CLI and package."""

from __future__ import annotations

import subprocess
import sys

from jev_decisions import __version__
from jev_decisions.cli import main


def test_help(capsys: object) -> None:
    # capsys is a pytest fixture; the annotation keeps this test dependency-free at runtime.
    from typing import cast

    from _pytest.capture import CaptureFixture

    capture = cast(CaptureFixture[str], capsys)
    assert main(["--help"]) == 0  # argparse exits before returning
    assert "usage: jev" in capture.readouterr().out


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
