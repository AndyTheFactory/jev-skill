"""Structural checks for the installable SKILL.md (no Claude Code runtime here)."""

from __future__ import annotations

from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).parent.parent / "skill" / "jev-decisions"
SKILL_MD = SKILL_DIR / "SKILL.md"


def _frontmatter(text: str) -> dict[str, object]:
    assert text.startswith("---\n")
    _, fm, _ = text.split("---\n", 2)
    return yaml.safe_load(fm)  # type: ignore[no-any-return]


def test_skill_md_exists() -> None:
    assert SKILL_MD.is_file()


def test_frontmatter_has_name_and_description() -> None:
    fm = _frontmatter(SKILL_MD.read_text())
    assert fm["name"] == "jev-decisions"
    assert isinstance(fm["description"], str)
    assert len(fm["description"]) > 20


def test_documents_explicit_invocation_with_and_without_args() -> None:
    text = SKILL_MD.read_text()
    assert "/jev-decisions [decision]" in text
    assert "With an argument" in text
    assert "Without an argument" in text


def test_documents_non_execution_and_no_permission_override() -> None:
    text = SKILL_MD.read_text().lower()
    assert "never overrides permissions" in text or "never authorizes" in text
    assert "non-recursive" in text


def test_documents_safe_no_call_fallback() -> None:
    text = SKILL_MD.read_text().lower()
    assert "no-call fallback" in text or "continue with your own reasoning" in text


def test_referenced_dynamic_choice_doc_exists() -> None:
    assert (SKILL_DIR / "references" / "dynamic-choice.md").is_file()
