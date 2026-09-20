"""Tests for the deterministic acceptance/abstention policy engine."""

from __future__ import annotations

import pytest

from jev_decisions.config import PolicyConfig
from jev_decisions.policy import evaluate
from jev_decisions.provider.openrouter import ProviderError
from jev_decisions.schemas import ChoiceOption, ChoiceRequest, DecisionError, ProviderChoiceResponse


@pytest.fixture
def request_() -> ChoiceRequest:
    return ChoiceRequest(
        question="Which?",
        options=(
            ChoiceOption(id="a", description="A"),
            ChoiceOption(id="b", description="B"),
            ChoiceOption(id="c", description="C"),
        ),
    )


def test_confident_response_accepted(request_: ChoiceRequest) -> None:
    resp = ProviderChoiceResponse(
        selected_option_id="a", probabilities={"a": 0.9, "b": 0.06, "c": 0.04}, confidence=0.95
    )
    decision = evaluate(request_, resp)
    assert decision.outcome == "accepted"
    assert decision.selected_option_id == "a"


def test_low_probability_abstains(request_: ChoiceRequest) -> None:
    resp = ProviderChoiceResponse(
        selected_option_id="a", probabilities={"a": 0.5, "b": 0.3, "c": 0.2}
    )
    decision = evaluate(request_, resp)
    assert decision.outcome == "abstained"


def test_narrow_margin_abstains(request_: ChoiceRequest) -> None:
    # With min_probability satisfied but the top two options nearly tied.
    cfg = PolicyConfig(min_probability=0.4, margin=0.2, min_confidence=0.7)
    resp = ProviderChoiceResponse(
        selected_option_id="a", probabilities={"a": 0.45, "b": 0.40, "c": 0.15}
    )
    decision = evaluate(request_, resp, config=cfg)
    assert decision.outcome == "abstained"
    assert decision.margin == pytest.approx(0.05)




def test_tie_abstains(request_: ChoiceRequest) -> None:
    resp = ProviderChoiceResponse(
        selected_option_id="a", probabilities={"a": 0.5, "b": 0.5, "c": 0.0}
    )
    decision = evaluate(request_, resp)
    assert decision.outcome == "abstained"
    assert decision.margin == pytest.approx(0.0)


def test_low_confidence_abstains(request_: ChoiceRequest) -> None:
    resp = ProviderChoiceResponse(
        selected_option_id="a", probabilities={"a": 0.9, "b": 0.06, "c": 0.04}, confidence=0.3
    )
    decision = evaluate(request_, resp)
    assert decision.outcome == "abstained"


def test_missing_confidence_does_not_block_acceptance(request_: ChoiceRequest) -> None:
    resp = ProviderChoiceResponse(
        selected_option_id="a", probabilities={"a": 0.9, "b": 0.06, "c": 0.04}
    )
    decision = evaluate(request_, resp)
    assert decision.outcome == "accepted"
    assert decision.confidence is None


def test_explicit_abstention_option_honored(request_: ChoiceRequest) -> None:
    resp = ProviderChoiceResponse(
        selected_option_id="c", probabilities={"a": 0.1, "b": 0.1, "c": 0.8}, confidence=0.9
    )
    decision = evaluate(request_, resp, abstain_option_ids=frozenset({"c"}))
    assert decision.outcome == "abstained"


def test_provider_error_is_failed_not_rejected(request_: ChoiceRequest) -> None:
    error = ProviderError(DecisionError(code="timeout", message="provider request timed out"))
    decision = evaluate(request_, None, error=error)
    assert decision.outcome == "failed"
    assert decision.selected_option_id is None


def test_no_response_no_error_is_failed(request_: ChoiceRequest) -> None:
    decision = evaluate(request_, None)
    assert decision.outcome == "failed"


def test_selected_id_not_top_probability_rejected(request_: ChoiceRequest) -> None:
    resp = ProviderChoiceResponse.model_construct(
        selected_option_id="c", probabilities={"a": 0.9, "b": 0.06, "c": 0.04}, confidence=0.9
    )
    decision = evaluate(request_, resp)
    assert decision.outcome == "rejected"


def test_probabilities_not_matching_declared_options_rejected(request_: ChoiceRequest) -> None:
    resp = ProviderChoiceResponse.model_construct(
        selected_option_id="a", probabilities={"a": 1.0}, confidence=0.9
    )
    decision = evaluate(request_, resp)
    assert decision.outcome == "rejected"


def test_threshold_boundary_at_exact_minimum_accepted(request_: ChoiceRequest) -> None:
    cfg = PolicyConfig(min_probability=0.8, margin=0.2, min_confidence=0.7)
    resp = ProviderChoiceResponse(
        selected_option_id="a", probabilities={"a": 0.8, "b": 0.2, "c": 0.0}
    )
    decision = evaluate(request_, resp, config=cfg)
    assert decision.outcome == "accepted"


def test_threshold_just_below_minimum_abstains(request_: ChoiceRequest) -> None:
    cfg = PolicyConfig(min_probability=0.8, margin=0.2, min_confidence=0.7)
    resp = ProviderChoiceResponse(
        selected_option_id="a", probabilities={"a": 0.79, "b": 0.11, "c": 0.10}
    )
    decision = evaluate(request_, resp, config=cfg)
    assert decision.outcome == "abstained"


def test_two_option_request_margin_computed_correctly() -> None:
    req = ChoiceRequest(
        question="Q?",
        options=(ChoiceOption(id="a", description="A"), ChoiceOption(id="b", description="B")),
    )
    resp = ProviderChoiceResponse(selected_option_id="a", probabilities={"a": 0.95, "b": 0.05})
    decision = evaluate(req, resp)
    assert decision.outcome == "accepted"
    assert decision.margin == pytest.approx(0.90)
