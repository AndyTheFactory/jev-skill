"""Smoke test for scripts/run_benchmark.py: produces a reproducible manifest."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

from jev_decisions import cli, engine
from jev_decisions.schemas import ChoiceRequest, ProviderChoiceResponse

SCRIPT = Path(__file__).parent.parent / "scripts" / "run_benchmark.py"


class _StubAdapter:
    def __enter__(self) -> _StubAdapter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
        option_ids = [o.id for o in request.options]
        remainder = 0.1 / max(1, len(option_ids) - 1)
        probs = {oid: (0.9 if i == 0 else remainder) for i, oid in enumerate(option_ids)}
        return ProviderChoiceResponse(selected_option_id=option_ids[0], probabilities=probs)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda config: _StubAdapter())
    monkeypatch.setattr(cli, "OpenRouterAdapter", lambda config: _StubAdapter())


def test_run_benchmark_writes_manifest_and_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_path = tmp_path / "manifest.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_benchmark.py", "--split", "all", "--out", str(out_path)],
    )
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert exc.value.code == 0

    manifest = json.loads(out_path.read_text())
    assert len(manifest) >= 10  # dataset size minus unsuitable_invocation examples (never sent)
    assert all({"example_id", "record_id"} == set(entry) for entry in manifest)

    meta = json.loads(out_path.with_suffix(".meta.json").read_text())
    assert meta["n_decided"] == len(manifest)
    assert meta["n_unsuitable_skipped"] >= 2
    assert meta["dataset_version"] == "v1"
    assert "jev_decisions_version" in meta
    assert "python_version" in meta
