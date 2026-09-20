"""Versioned YAML profile loader and registry.

A profile pre-defines a Choice question, its mutually exclusive options, an
optional explicit abstention alternative, and a fallback description of what
happens when the decision is not accepted. Loading a profile never grants it
authority to act -- it only ever produces a policy-evaluated recommendation,
same as any other Choice decision.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from jev_decisions.schemas import ChoiceOption, check_options

DEFAULT_PROFILES_DIR = Path(__file__).parent / "data"


class ProfileError(Exception):
    """Raised for invalid or conflicting profile definitions, or unknown profile lookups."""


class Profile(BaseModel):
    """A reusable, named Choice definition."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: str = "1.0"
    description: str
    question: str
    options: tuple[ChoiceOption, ...]
    abstain_option_ids: frozenset[str] = frozenset()
    fallback: str

    _check_options = field_validator("options")(check_options)

    def model_post_init(self, __context: Any) -> None:
        declared = {opt.id for opt in self.options}
        unknown_abstain = self.abstain_option_ids - declared
        if unknown_abstain:
            raise ValueError(
                f"abstain_option_ids {sorted(unknown_abstain)} not among declared options"
            )


class ProfileRegistry:
    """Loads and indexes profiles by id, detecting conflicts on load."""

    def __init__(self, profiles: dict[str, Profile]) -> None:
        self._profiles = profiles

    def list(self) -> list[Profile]:
        return sorted(self._profiles.values(), key=lambda p: p.id)

    def get(self, profile_id: str) -> Profile:
        try:
            return self._profiles[profile_id]
        except KeyError:
            raise ProfileError(f"unknown profile: {profile_id!r}") from None

    def __contains__(self, profile_id: str) -> bool:
        return profile_id in self._profiles


def _load_one(path: Path) -> Profile:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ProfileError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileError(f"profile file {path} must contain a mapping at top level")
    try:
        return Profile.model_validate(raw)
    except (ValidationError, ValueError) as exc:
        raise ProfileError(f"invalid profile in {path}: {exc}") from exc


def load_registry(directory: Path | None = None) -> ProfileRegistry:
    """Load every ``*.yaml`` profile in ``directory``, rejecting id conflicts."""
    directory = directory or DEFAULT_PROFILES_DIR
    profiles: dict[str, Profile] = {}
    if not directory.is_dir():
        return ProfileRegistry(profiles)

    for path in sorted(directory.glob("*.yaml")):
        profile = _load_one(path)
        if profile.id in profiles:
            raise ProfileError(
                f"duplicate profile id {profile.id!r}: defined in both "
                f"{directory} entries; profile ids must be unique"
            )
        profiles[profile.id] = profile

    return ProfileRegistry(profiles)
