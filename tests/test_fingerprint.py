"""Tests for fingerprint stability and sensitivity to relevant changes."""

from __future__ import annotations

import pytest

from jev_decisions.config import JevConfig
from jev_decisions.fingerprint import compute_fingerprint
from jev_decisions.schemas import ChoiceOption, ChoiceRequest


@pytest.fixture
def config() -> JevConfig:
    return JevConfig()


@pytest.fixture
def request_() -> ChoiceRequest:
    return ChoiceRequest(
        question="Which?",
        options=(ChoiceOption(id="a", description="A"), ChoiceOption(id="b", description="B")),
        context="some evidence",
    )


def test_identical_requests_have_identical_fingerprints(
    config: JevConfig, request_: ChoiceRequest
) -> None:
    assert compute_fingerprint(request_, config) == compute_fingerprint(request_, config)


def test_option_order_does_not_affect_fingerprint(config: JevConfig) -> None:
    a = ChoiceRequest(
        question="Q?",
        options=(ChoiceOption(id="a", description="A"), ChoiceOption(id="b", description="B")),
    )
    b = ChoiceRequest(
        question="Q?",
        options=(ChoiceOption(id="b", description="B"), ChoiceOption(id="a", description="A")),
    )
    assert compute_fingerprint(a, config) == compute_fingerprint(b, config)


def test_context_change_invalidates_fingerprint(config: JevConfig, request_: ChoiceRequest) -> None:
    changed = request_.model_copy(update={"context": "different evidence"})
    assert compute_fingerprint(request_, config) != compute_fingerprint(changed, config)


def test_question_change_invalidates_fingerprint(
    config: JevConfig, request_: ChoiceRequest
) -> None:
    changed = request_.model_copy(update={"question": "A different question?"})
    assert compute_fingerprint(request_, config) != compute_fingerprint(changed, config)


def test_model_change_invalidates_fingerprint(config: JevConfig, request_: ChoiceRequest) -> None:
    other = JevConfig()
    other.provider.model = "typesafe/jev-1.13"
    assert compute_fingerprint(request_, config) != compute_fingerprint(request_, other)


def test_policy_change_invalidates_fingerprint(config: JevConfig, request_: ChoiceRequest) -> None:
    other = JevConfig()
    other.policy.min_probability = 0.5
    assert compute_fingerprint(request_, config) != compute_fingerprint(request_, other)


def test_profile_change_invalidates_fingerprint(config: JevConfig, request_: ChoiceRequest) -> None:
    with_profile = request_.model_copy(update={"profile": "task-routing"})
    assert compute_fingerprint(request_, config) != compute_fingerprint(with_profile, config)


def test_abstain_option_ids_change_invalidates_fingerprint(
    config: JevConfig, request_: ChoiceRequest
) -> None:
    without = compute_fingerprint(request_, config)
    with_abstain = compute_fingerprint(request_, config, abstain_option_ids=frozenset({"a"}))
    assert without != with_abstain


def test_abstain_option_ids_order_does_not_affect_fingerprint(
    config: JevConfig, request_: ChoiceRequest
) -> None:
    a = compute_fingerprint(request_, config, abstain_option_ids=frozenset({"a", "b"}))
    b = compute_fingerprint(request_, config, abstain_option_ids=frozenset({"b", "a"}))
    assert a == b
