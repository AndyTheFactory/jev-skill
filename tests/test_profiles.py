"""Tests for the hybrid profile registry and the four starter profiles."""

from __future__ import annotations

from pathlib import Path

import pytest

from jev_decisions.profiles import DEFAULT_PROFILES_DIR, ProfileError, load_registry


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_starter_profiles_load_and_are_discoverable() -> None:
    registry = load_registry(DEFAULT_PROFILES_DIR)
    ids = {p.id for p in registry.list()}
    assert ids == {"task-routing", "workflow-selection", "review-triage", "investigation"}


def test_each_starter_profile_options_non_overlapping_ids() -> None:
    registry = load_registry(DEFAULT_PROFILES_DIR)
    for profile in registry.list():
        option_ids = [opt.id for opt in profile.options]
        assert len(option_ids) == len(set(option_ids))


def test_unknown_profile_raises(tmp_path: Path) -> None:
    registry = load_registry(tmp_path)
    with pytest.raises(ProfileError, match="unknown profile"):
        registry.get("does-not-exist")


def test_empty_directory_yields_empty_registry(tmp_path: Path) -> None:
    registry = load_registry(tmp_path)
    assert registry.list() == []


def test_missing_directory_yields_empty_registry(tmp_path: Path) -> None:
    registry = load_registry(tmp_path / "missing")
    assert registry.list() == []


def _profile_yaml(profile_id: str, **overrides: str) -> str:
    base = f"""
id: {profile_id}
description: test profile
question: which?
options:
  - id: a
    description: Option A
  - id: b
    description: Option B
fallback: continue with default reasoning
"""
    return base


def test_conflicting_ids_across_files_rejected(tmp_path: Path) -> None:
    write(tmp_path / "one.yaml", _profile_yaml("dup"))
    write(tmp_path / "two.yaml", _profile_yaml("dup"))
    with pytest.raises(ProfileError, match="duplicate profile id"):
        load_registry(tmp_path)


def test_invalid_alternatives_count_rejected(tmp_path: Path) -> None:
    write(
        tmp_path / "bad.yaml",
        """
id: bad
description: only one option
question: which?
options:
  - id: a
    description: Option A
fallback: continue
""",
    )
    with pytest.raises(ProfileError, match="2-8 entries"):
        load_registry(tmp_path)


def test_duplicate_option_ids_within_profile_rejected(tmp_path: Path) -> None:
    write(
        tmp_path / "bad.yaml",
        """
id: bad
description: dup options
question: which?
options:
  - id: a
    description: Option A
  - id: a
    description: Option A again
fallback: continue
""",
    )
    with pytest.raises(ProfileError, match="distinct"):
        load_registry(tmp_path)


def test_abstain_id_not_among_options_rejected(tmp_path: Path) -> None:
    write(
        tmp_path / "bad.yaml",
        """
id: bad
description: bad abstain id
question: which?
options:
  - id: a
    description: Option A
  - id: b
    description: Option B
abstain_option_ids:
  - not-declared
fallback: continue
""",
    )
    with pytest.raises(ProfileError, match="not among declared options"):
        load_registry(tmp_path)


def test_invalid_yaml_rejected(tmp_path: Path) -> None:
    write(tmp_path / "bad.yaml", "id: [unterminated\n")
    with pytest.raises(ProfileError, match="invalid YAML"):
        load_registry(tmp_path)


def test_non_mapping_yaml_rejected(tmp_path: Path) -> None:
    write(tmp_path / "bad.yaml", "- just\n- a\n- list\n")
    with pytest.raises(ProfileError, match="must contain a mapping"):
        load_registry(tmp_path)
