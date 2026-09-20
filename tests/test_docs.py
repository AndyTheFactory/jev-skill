"""Guards docs/config examples against drifting from the real schema."""

from __future__ import annotations

from pathlib import Path

import yaml

from jev_decisions.config import JevConfig

REPO_ROOT = Path(__file__).parent.parent


def test_example_config_matches_real_schema() -> None:
    raw = yaml.safe_load((REPO_ROOT / "config" / "example.yaml").read_text())
    config = JevConfig.model_validate(raw)
    assert config.execution.mode == "shadow"
    assert config.provider.model == "~typesafe/jev-latest"


def test_benchmark_runbook_documents_active_mode_gate() -> None:
    text = (REPO_ROOT / "eval" / "BENCHMARK.md").read_text()
    assert "scripts/run_benchmark.py" in text
    assert "jev evaluate" in text
    assert "Sample size" in text
    assert "stays there unless" in text or "remains disabled" in text.lower()


def test_readme_documents_key_commands() -> None:
    text = (REPO_ROOT / "README.md").read_text()
    for snippet in (
        "jev doctor",
        "jev decide --stdin",
        "jev decide --input",
        "jev profile list",
        "OPENROUTER_API_KEY",
        "skill/jev-decisions/SKILL.md",
    ):
        assert snippet in text, f"README missing {snippet!r}"
