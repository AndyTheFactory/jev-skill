"""End-to-end test for `jev evaluate`: decide -> manifest -> evaluate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jev_decisions import cli, engine
from jev_decisions.schemas import ChoiceRequest, ProviderChoiceResponse


class _StubAdapter:
    def __enter__(self) -> _StubAdapter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
        option_ids = [o.id for o in request.options]
        probabilities = {oid: 0.0 for oid in option_ids}
        probabilities["coding"] = 0.9
        remainder = 0.1 / (len(option_ids) - 1) if len(option_ids) > 1 else 0.0
        for oid in option_ids:
            if oid != "coding":
                probabilities[oid] = remainder
        return ProviderChoiceResponse(selected_option_id="coding", probabilities=probabilities)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(cli, "OpenRouterAdapter", lambda config: _StubAdapter())
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda config: _StubAdapter())


def _write_dataset(directory: Path) -> None:
    directory.mkdir(parents=True)
    (directory / "e1.yaml").write_text(
        """
id: e1
dataset_version: v1
split: dev
category: typical
profile: task-routing
context: "fix the off-by-one bug in pagination"
label:
  correct_option_id: coding
  source: reviewed_label
  notes: "matches actual merged PR"
"""
    )


def test_decide_then_evaluate_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir)

    req_file = tmp_path / "req.json"
    req_file.write_text(json.dumps({"profile": "task-routing", "context": "..."}))
    cli.main(["decide", "--input", str(req_file)])
    record_id = json.loads(capsys.readouterr().out)["record_id"]

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps([{"example_id": "e1", "record_id": record_id}]))

    code = cli.main(["evaluate", "--input", str(manifest_path), "--dataset", str(dataset_dir)])
    assert code == 0
    report: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert report["overall"]["n"] == 1
    assert report["overall"]["accuracy"] == 1.0
    assert report["overall"]["accuracy_n"] == 1
    assert report["missing_records"] == []


def test_evaluate_missing_manifest_exit_65(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["evaluate", "--input", "/nonexistent/manifest.json"])
    assert code == cli.EX_DATAERR
