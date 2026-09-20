"""Reproducible labeled decision evaluation dataset.

Each example is a Choice decision scenario (profile-based or dynamic) with
an optional label. A missing label means "no reviewed ground truth" and
must be reported by the evaluator as "not available", never scored as
wrong -- this is what lets evaluation distinguish an unlabeled example from
a true error. Labels are set independently of any Jev prediction: they come
from task evidence (e.g. the PR that was actually merged) or a human
reviewer, never from re-running the provider and copying its answer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from jev_decisions.schemas import MAX_OPTIONS, MIN_OPTIONS, ChoiceOption, check_options

DEFAULT_DATASET_DIR = Path(__file__).parent.parent.parent.parent / "eval" / "dataset" / "v1"

ExampleCategory = Literal[
    "typical", "ambiguous", "out_of_distribution", "insufficient_context", "unsuitable_invocation"
]
LabelSource = Literal["task_evidence", "reviewed_label"]

_SECRET_PATTERN = re.compile(
    r"sk-[A-Za-z0-9]{10,}|Bearer\s+\S+|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}"
)


class DatasetError(Exception):
    """Raised for invalid, conflicting, or secret-containing dataset content."""


class Label(BaseModel):
    """Reviewed ground truth for one example. Present only when defensible."""

    model_config = ConfigDict(extra="forbid")

    correct_option_id: str | None
    source: LabelSource
    notes: str = ""


class DatasetExample(BaseModel):
    """One evaluation scenario: either a profile reference or a dynamic question."""

    model_config = ConfigDict(extra="forbid")

    id: str
    dataset_version: str
    split: Literal["dev", "eval"]
    category: ExampleCategory
    profile: str | None = None
    question: str | None = None
    options: tuple[ChoiceOption, ...] | None = None
    context: str = ""
    label: Label | None = None

    @model_validator(mode="after")
    def _check_profile_xor_dynamic(self) -> DatasetExample:
        if self.profile is None:
            if not self.question or not self.options:
                raise ValueError(
                    "dynamic examples (profile=null) require both question and options"
                )
            if not (MIN_OPTIONS <= len(self.options) <= MAX_OPTIONS):
                raise ValueError(f"options must contain {MIN_OPTIONS}-{MAX_OPTIONS} entries")
            check_options(self.options)
        elif self.question is not None or self.options is not None:
            raise ValueError(
                "profile-based examples must not also embed question/options "
                "(they come from the profile; keep one source of truth)"
            )
        if self.label is not None and self.profile is None and self.options is not None:
            declared = {opt.id for opt in self.options}
            if self.label.correct_option_id is not None and (
                self.label.correct_option_id not in declared
            ):
                raise ValueError(
                    f"label.correct_option_id {self.label.correct_option_id!r} not "
                    f"among this example's declared options {sorted(declared)}"
                )
        return self


def _check_no_secrets(raw_text: str, source: Path) -> None:
    if _SECRET_PATTERN.search(raw_text):
        raise DatasetError(
            f"{source} appears to contain a credential-shaped secret; refusing to load"
        )


def load_dataset(directory: Path | None = None) -> list[DatasetExample]:
    """Load and validate every example in ``directory``, rejecting id conflicts."""
    directory = directory or DEFAULT_DATASET_DIR
    if not directory.is_dir():
        return []

    examples: dict[str, DatasetExample] = {}
    for path in sorted(directory.glob("*.yaml")):
        raw_text = path.read_text()
        _check_no_secrets(raw_text, path)
        raw = yaml.safe_load(raw_text)
        if not isinstance(raw, dict):
            raise DatasetError(f"{path} must contain a mapping at top level")
        try:
            example = DatasetExample.model_validate(raw)
        except ValidationError as exc:
            raise DatasetError(f"invalid dataset example in {path}: {exc}") from exc
        if example.id in examples:
            raise DatasetError(f"duplicate example id {example.id!r} (seen again in {path})")
        examples[example.id] = example

    return sorted(examples.values(), key=lambda e: e.id)
