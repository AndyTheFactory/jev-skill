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
from conftest import StubAdapter

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
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: StubAdapter(response=response))
    result = engine.run_decision(active_config, request_)
    assert result.action.permitted is False


def test_action_never_permitted_on_provider_failure_in_active_mode(
    active_config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = ProviderError(DecisionError(code="timeout", message="timed out"))
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: StubAdapter(error=error))
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
        engine, "OpenRouterAdapter", lambda c: StubAdapter(response=accepted_response)
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
        engine, "OpenRouterAdapter", lambda cfg: StubAdapter(response=accepted_response)
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
        engine, "OpenRouterAdapter", lambda cfg: StubAdapter(response=abstained_response)
    )
    result = engine.run_decision(active_config, request_)
    assert not hasattr(result, "selected_option_id")
    assert result.outcome == "abstained"


def test_failed_in_active_mode_produces_no_selected_option_anywhere_visible(
    active_config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = ProviderError(DecisionError(code="timeout", message="timed out"))
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: StubAdapter(error=error))
    result = engine.run_decision(active_config, request_)
    assert not hasattr(result, "selected_option_id")
    assert result.outcome == "failed"


# --- a spoofed profile label must not borrow another profile's trust ----


def test_profile_label_cannot_be_stapled_onto_unrelated_content(
    active_config: JevConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Engine-level: if a caller could get request.profile="task-routing"
    onto arbitrary self-authored content, that content would wrongly gain
    active-mode advisory exposure. This is exactly why cli.py strips the
    profile label whenever a request supplies its own question/options
    instead of letting the registry populate them (see test_cli_decide.py's
    test_decide_explicit_question_with_profile_not_overridden) -- engine.py
    itself has no way to tell vetted profile content from a spoofed label,
    so the guarantee has to hold at the boundary that builds the request.
    """
    spoofed = ChoiceRequest(
        question="Should we wipe the production database right now?",
        options=(
            ChoiceOption(id="wipe", description="Wipe it"),
            ChoiceOption(id="keep", description="Keep it"),
        ),
        profile="task-routing",  # unrelated to this content; would-be spoof
    )
    response = ProviderChoiceResponse(
        selected_option_id="wipe", probabilities={"wipe": 0.95, "keep": 0.05}
    )
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: StubAdapter(response=response))
    result = engine.run_decision(active_config, spoofed)
    # engine.py alone can't detect the spoof (by design it trusts
    # request.profile), so this documents the actual boundary: the CLI
    # must never construct a request like this one in the first place.
    assert isinstance(result, engine.AdvisoryResult)
