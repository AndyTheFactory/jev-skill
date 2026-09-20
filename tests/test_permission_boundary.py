"""M4 #20: active-mode fallback and permission-boundary adversarial tests.

Proves a Jev recommendation -- accepted, in active mode, for an explicitly
enabled profile -- can never look like or carry permission: no result type
this codebase produces has any field that could be mistaken for
authorization, `action.permitted` is exhaustively False across every
outcome/mode combination, and dynamic-question validation rejects
destructive/deployment/credential/production content regardless of whether
active mode is configured (the two are orthogonal by construction: nothing
in dynamic.py reads execution config at all).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jev_decisions import baseline as baseline_module
from jev_decisions import cache as cache_module
from jev_decisions import engine, store
from jev_decisions.config import JevConfig
from jev_decisions.dynamic import DynamicRequestRejected, validate_dynamic_request
from jev_decisions.policy import Decision
from jev_decisions.profiles import load_registry
from jev_decisions.provider.openrouter import ProviderError
from jev_decisions.schemas import (
    ChoiceOption,
    ChoiceRequest,
    DecisionError,
    ProviderChoiceResponse,
)


class _StubAdapter:
    def __init__(
        self,
        response: ProviderChoiceResponse | None = None,
        error: ProviderError | None = None,
    ) -> None:
        self._response = response
        self._error = error

    def __enter__(self) -> _StubAdapter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "default_store_dir", lambda: tmp_path)
    monkeypatch.setattr(baseline_module, "default_baseline_dir", lambda: tmp_path / "baseline")
    monkeypatch.setattr(cache_module, "default_cache_dir", lambda: tmp_path / "cache")


@pytest.fixture
def active_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> JevConfig:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = JevConfig()
    cfg.telemetry.path = str(tmp_path / "telemetry.jsonl")
    cfg.execution.mode = "active"
    cfg.execution.active_profiles = ("task-routing",)
    return cfg


@pytest.fixture
def request_() -> ChoiceRequest:
    return ChoiceRequest(
        question="Which?",
        options=(ChoiceOption(id="a", description="A"), ChoiceOption(id="b", description="B")),
        profile="task-routing",
    )


# --- action.permitted is exhaustively False -----------------------------

_OUTCOME_RESPONSES = {
    "accepted": ProviderChoiceResponse(
        selected_option_id="a", probabilities={"a": 0.95, "b": 0.05}
    ),
    "abstained": ProviderChoiceResponse(selected_option_id="a", probabilities={"a": 0.5, "b": 0.5}),
    "rejected": ProviderChoiceResponse.model_construct(
        selected_option_id="b", probabilities={"a": 0.9, "b": 0.1}, confidence=None
    ),
}


@pytest.mark.parametrize("outcome", ["accepted", "abstained", "rejected"])
def test_action_never_permitted_in_active_mode_any_outcome(
    active_config: JevConfig,
    request_: ChoiceRequest,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    response = _OUTCOME_RESPONSES[outcome]
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: _StubAdapter(response=response))
    result = engine.run_decision(active_config, request_)
    assert result.action.permitted is False


def test_action_never_permitted_on_provider_failure_in_active_mode(
    active_config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = ProviderError(DecisionError(code="timeout", message="timed out"))
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: _StubAdapter(error=error))
    result = engine.run_decision(active_config, request_)
    assert result.outcome == "failed"
    assert result.action.permitted is False


def test_action_never_permitted_in_shadow_mode(
    request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = JevConfig()
    cfg.telemetry.path = str(tmp_path / "telemetry.jsonl")
    accepted_response = _OUTCOME_RESPONSES["accepted"]
    monkeypatch.setattr(
        engine, "OpenRouterAdapter", lambda c: _StubAdapter(response=accepted_response)
    )
    result = engine.run_decision(cfg, request_)
    assert result.action.permitted is False


def test_revealed_decision_has_no_permission_field(
    active_config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even the full protected Decision (only reachable via explicit `jev
    # reveal`) carries no permission-shaped field to begin with.
    assert "permitted" not in Decision.model_fields
    assert "authorized" not in Decision.model_fields
    assert "action" not in Decision.model_fields


def test_advisory_result_has_no_field_beyond_documented_ones(
    active_config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    accepted_response = _OUTCOME_RESPONSES["accepted"]
    monkeypatch.setattr(
        engine, "OpenRouterAdapter", lambda cfg: _StubAdapter(response=accepted_response)
    )
    result = engine.run_decision(active_config, request_)
    assert isinstance(result, engine.AdvisoryResult)
    expected_fields = {
        "record_id", "outcome", "selected_option_id", "probability", "action"
    }
    assert set(result.model_dump()) == expected_fields


# --- dynamic-question validation is orthogonal to active mode -----------

def _adversarial_request(question: str) -> ChoiceRequest:
    return ChoiceRequest(
        question=question,
        options=(
            ChoiceOption(id="a", description="Do it"),
            ChoiceOption(id="b", description="Do not"),
        ),
    )


_ADVERSARIAL_QUESTIONS = [
    "Should we delete the production database now?",
    "Which credentials should be used to deploy to the production database?",
    "Is it safe to force push over main in production?",
    "Should we redesign the architecture of the billing system?",
]


@pytest.mark.parametrize("question", _ADVERSARIAL_QUESTIONS)
def test_adversarial_questions_rejected_regardless_of_active_mode(
    active_config: JevConfig, question: str
) -> None:
    # active_config has active mode fully enabled for task-routing; dynamic
    # validation doesn't even look at execution config, so mode is
    # irrelevant here by construction -- this proves it stays irrelevant.
    request = _adversarial_request(question)
    with pytest.raises(DynamicRequestRejected):
        validate_dynamic_request(request)


@pytest.mark.parametrize("question", _ADVERSARIAL_QUESTIONS)
def test_adversarial_questions_rejected_in_plain_shadow_mode_too(question: str) -> None:
    request = _adversarial_request(question)
    with pytest.raises(DynamicRequestRejected):
        validate_dynamic_request(request)


def test_no_starter_profile_offers_a_destructive_or_permission_option() -> None:
    from jev_decisions.dynamic import _HIGH_RISK_PERMISSION_KEYWORDS

    for profile in load_registry().list():
        haystack = " ".join(
            [profile.question, *(opt.description for opt in profile.options)]
        ).lower()
        for keyword in _HIGH_RISK_PERMISSION_KEYWORDS:
            assert keyword not in haystack, (
                f"starter profile {profile.id!r} option text contains "
                f"high-risk keyword {keyword!r}: {haystack!r}"
            )


# --- fallback: non-accepted always defers to normal reasoning -----------


def test_abstained_in_active_mode_produces_no_selected_option_anywhere_visible(
    active_config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    abstained_response = _OUTCOME_RESPONSES["abstained"]
    monkeypatch.setattr(
        engine, "OpenRouterAdapter", lambda cfg: _StubAdapter(response=abstained_response)
    )
    result = engine.run_decision(active_config, request_)
    assert not hasattr(result, "selected_option_id")
    assert result.outcome == "abstained"


def test_failed_in_active_mode_produces_no_selected_option_anywhere_visible(
    active_config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = ProviderError(DecisionError(code="timeout", message="timed out"))
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: _StubAdapter(error=error))
    result = engine.run_decision(active_config, request_)
    assert not hasattr(result, "selected_option_id")
    assert result.outcome == "failed"
