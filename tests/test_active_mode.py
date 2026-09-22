"""M4: explicit active-mode configuration, per-profile gating, and fallback.

Proves accepted results affect only the next-action suggestion for
explicitly enabled profiles, and every other outcome/profile/mode combination
falls back to the ordinary shadow result -- an AdvisoryResult is a visible
recommendation, never an execution authorization.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import StubAdapter

from jev_decisions import baseline as baseline_module
from jev_decisions import cache as cache_module
from jev_decisions import engine, store
from jev_decisions.config import JevConfig
from jev_decisions.provider.openrouter import ProviderError
from jev_decisions.schemas import ChoiceOption, ChoiceRequest, ProviderChoiceResponse


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "default_store_dir", lambda: tmp_path)
    monkeypatch.setattr(baseline_module, "default_baseline_dir", lambda: tmp_path / "baseline")
    monkeypatch.setattr(cache_module, "default_cache_dir", lambda: tmp_path / "cache")


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> JevConfig:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = JevConfig()
    cfg.telemetry.path = str(tmp_path / "telemetry.jsonl")
    return cfg


@pytest.fixture
def request_() -> ChoiceRequest:
    return ChoiceRequest(
        question="Which?",
        options=(ChoiceOption(id="a", description="A"), ChoiceOption(id="b", description="B")),
        profile="task-routing",
    )


ACCEPTED = ProviderChoiceResponse(selected_option_id="a", probabilities={"a": 0.95, "b": 0.05})
ABSTAINED = ProviderChoiceResponse(selected_option_id="a", probabilities={"a": 0.5, "b": 0.5})


def test_default_shadow_mode_never_advises_even_if_active_profiles_set(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    config.execution.active_profiles = ("task-routing",)  # mode is still "shadow"
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: StubAdapter(response=ACCEPTED))
    result = engine.run_decision(config, request_)
    assert isinstance(result, engine.ShadowResult)
    assert not isinstance(result, engine.AdvisoryResult)


def test_active_mode_profile_not_enabled_never_advises(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    config.execution.mode = "active"
    config.execution.active_profiles = ("review-triage",)  # not this request's profile
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: StubAdapter(response=ACCEPTED))
    result = engine.run_decision(config, request_)
    assert isinstance(result, engine.ShadowResult)
    assert not isinstance(result, engine.AdvisoryResult)


def test_active_mode_enabled_profile_accepted_advises(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    config.execution.mode = "active"
    config.execution.active_profiles = ("task-routing",)
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: StubAdapter(response=ACCEPTED))
    result = engine.run_decision(config, request_)
    assert isinstance(result, engine.AdvisoryResult)
    assert result.selected_option_id == "a"
    assert result.probability == pytest.approx(0.95)


@pytest.mark.parametrize("response", [ABSTAINED])
def test_active_mode_enabled_profile_abstained_falls_back_to_shadow(
    config: JevConfig,
    request_: ChoiceRequest,
    monkeypatch: pytest.MonkeyPatch,
    response: ProviderChoiceResponse,
) -> None:
    config.execution.mode = "active"
    config.execution.active_profiles = ("task-routing",)
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: StubAdapter(response=response))
    result = engine.run_decision(config, request_)
    assert isinstance(result, engine.ShadowResult)
    assert not isinstance(result, engine.AdvisoryResult)
    assert result.outcome == "abstained"


def test_active_mode_enabled_profile_provider_failure_falls_back_to_shadow(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jev_decisions.schemas import DecisionError

    config.execution.mode = "active"
    config.execution.active_profiles = ("task-routing",)
    error = ProviderError(DecisionError(code="timeout", message="timed out"))
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: StubAdapter(error=error))
    result = engine.run_decision(config, request_)
    assert isinstance(result, engine.ShadowResult)
    assert result.outcome == "failed"


def test_active_mode_enabled_profile_rejected_response_falls_back_to_shadow(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    config.execution.mode = "active"
    config.execution.active_profiles = ("task-routing",)
    inconsistent = ProviderChoiceResponse.model_construct(
        selected_option_id="b", probabilities={"a": 0.9, "b": 0.1}, confidence=None
    )
    monkeypatch.setattr(
        engine, "OpenRouterAdapter", lambda cfg: StubAdapter(response=inconsistent)
    )
    result = engine.run_decision(config, request_)
    assert isinstance(result, engine.ShadowResult)
    assert result.outcome == "rejected"


def test_active_mode_dynamic_request_never_advises(
    config: JevConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No profile at all: active_profiles gating has nothing to match against.
    config.execution.mode = "active"
    config.execution.active_profiles = ("task-routing",)
    dynamic_request = ChoiceRequest(
        question="Which?",
        options=(ChoiceOption(id="a", description="A"), ChoiceOption(id="b", description="B")),
    )
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: StubAdapter(response=ACCEPTED))
    result = engine.run_decision(config, dynamic_request)
    assert isinstance(result, engine.ShadowResult)
    assert not isinstance(result, engine.AdvisoryResult)


def test_run_shadow_is_always_unconditionally_shadow_regardless_of_config(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    # run_shadow (used directly by the benchmark runner) must never advise,
    # even with active mode fully configured -- only run_decision may.
    config.execution.mode = "active"
    config.execution.active_profiles = ("task-routing",)
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: StubAdapter(response=ACCEPTED))
    result = engine.run_shadow(config, request_)
    assert isinstance(result, engine.ShadowResult)
    assert not hasattr(result, "selected_option_id")


def test_advisory_result_never_exposes_confidence_or_reason(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    config.execution.mode = "active"
    config.execution.active_profiles = ("task-routing",)
    confident = ProviderChoiceResponse(
        selected_option_id="a", probabilities={"a": 0.95, "b": 0.05}, confidence=0.99
    )
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: StubAdapter(response=confident))
    result = engine.run_decision(config, request_)
    dumped = result.model_dump()
    assert "confidence" not in dumped
    assert "reason" not in dumped
