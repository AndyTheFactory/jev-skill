"""Tests for the labeled evaluation dataset loader and schema."""

from __future__ import annotations

from pathlib import Path

import pytest

from jev_decisions.dynamic import DynamicRequestRejected, validate_dynamic_request
from jev_decisions.evaluation.dataset import DatasetError, DatasetExample, Label, load_dataset
from jev_decisions.profiles import load_registry
from jev_decisions.schemas import ChoiceRequest


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_committed_dataset_loads_and_validates() -> None:
    examples = load_dataset()
    assert len(examples) >= 12
    ids = [e.id for e in examples]
    assert len(set(ids)) == len(ids)  # no duplicate ids


def test_committed_dataset_covers_all_four_starter_profiles() -> None:
    examples = load_dataset()
    profiles = {e.profile for e in examples if e.profile is not None}
    assert profiles == {"task-routing", "workflow-selection", "review-triage", "investigation"}


def test_committed_dataset_covers_all_categories() -> None:
    examples = load_dataset()
    categories = {e.category for e in examples}
    assert categories == {
        "typical",
        "ambiguous",
        "out_of_distribution",
        "insufficient_context",
        "unsuitable_invocation",
    }


def test_committed_dataset_has_both_labeled_and_unlabeled_examples() -> None:
    examples = load_dataset()
    assert any(e.label is not None for e in examples)
    assert any(e.label is None for e in examples)


def test_labeled_examples_reference_declared_options_when_dynamic() -> None:
    for example in load_dataset():
        if example.label and example.label.correct_option_id and example.options:
            ids = {o.id for o in example.options}
            assert example.label.correct_option_id in ids


def test_profile_based_examples_reference_real_profiles() -> None:
    registry = load_registry()
    for example in load_dataset():
        if example.profile is not None:
            assert example.profile in registry


def test_dynamic_examples_build_valid_choice_requests() -> None:
    for example in load_dataset():
        if example.profile is None:
            assert example.options is not None
            assert example.question is not None
            ChoiceRequest(
                question=example.question, options=example.options, context=example.context
            )


def test_unsuitable_invocation_examples_are_rejected_by_dynamic_validation() -> None:
    unsuitable = [e for e in load_dataset() if e.category == "unsuitable_invocation"]
    assert unsuitable
    for example in unsuitable:
        assert example.profile is None
        assert example.options is not None
        assert example.question is not None
        request = ChoiceRequest(
            question=example.question, options=example.options, context=example.context
        )
        with pytest.raises(DynamicRequestRejected):
            validate_dynamic_request(request)


def test_unsuitable_invocation_examples_never_labeled() -> None:
    for example in load_dataset():
        if example.category == "unsuitable_invocation":
            assert example.label is None


def test_insufficient_context_examples_never_labeled() -> None:
    for example in load_dataset():
        if example.category == "insufficient_context":
            assert example.label is None


def test_empty_directory_yields_empty_dataset(tmp_path: Path) -> None:
    assert load_dataset(tmp_path) == []


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    body = """
id: dup
dataset_version: v1
split: dev
category: typical
profile: task-routing
context: x
"""
    write(tmp_path / "a.yaml", body)
    write(tmp_path / "b.yaml", body)
    with pytest.raises(DatasetError, match="duplicate example id"):
        load_dataset(tmp_path)


def test_secret_shaped_content_rejected(tmp_path: Path) -> None:
    write(
        tmp_path / "leaky.yaml",
        """
id: leaky
dataset_version: v1
split: dev
category: typical
profile: task-routing
context: "leaked key sk-abcdefghijklmnopqrstuvwxyz"
""",
    )
    with pytest.raises(DatasetError, match="secret"):
        load_dataset(tmp_path)


def test_dynamic_example_missing_options_rejected() -> None:
    with pytest.raises(Exception, match="require both question and options"):
        DatasetExample(
            id="bad",
            dataset_version="v1",
            split="dev",
            category="typical",
            profile=None,
            question="Which?",
        )


def test_profile_example_with_embedded_options_rejected() -> None:
    from jev_decisions.schemas import ChoiceOption

    with pytest.raises(Exception, match="must not also embed"):
        DatasetExample(
            id="bad",
            dataset_version="v1",
            split="dev",
            category="typical",
            profile="task-routing",
            options=(ChoiceOption(id="a", description="A"), ChoiceOption(id="b", description="B")),
        )


def test_label_with_unknown_option_id_rejected() -> None:
    from jev_decisions.schemas import ChoiceOption

    with pytest.raises(Exception, match="not among this example's declared options"):
        DatasetExample(
            id="bad",
            dataset_version="v1",
            split="dev",
            category="typical",
            profile=None,
            question="Which?",
            options=(ChoiceOption(id="a", description="A"), ChoiceOption(id="b", description="B")),
            label=Label(correct_option_id="not-declared", source="reviewed_label"),
        )
