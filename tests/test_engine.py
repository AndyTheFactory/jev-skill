"""Tests proving shadow-result isolation at the engine level."""

from __future__ import annotations

from pathlib import Path

import pytest

from jev_decisions import engine, store
from jev_decisions.config import JevConfig
from jev_decisions.provider.openrouter import ProviderError
from jev_decisions.schemas import ChoiceOption, ChoiceRequest, DecisionError, ProviderChoiceResponse


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
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "DEFAULT_STORE_DIR", tmp_path)


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch) -> JevConfig:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return JevConfig()


@pytest.fixture
def request_() -> ChoiceRequest:
    return ChoiceRequest(
        question="Which?",
        options=(ChoiceOption(id="a", description="A"), ChoiceOption(id="b", description="B")),
    )


def test_shadow_result_never_exposes_selected_option(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = ProviderChoiceResponse(
        selected_option_id="a", probabilities={"a": 0.95, "b": 0.05}, confidence=0.99
    )
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: _StubAdapter(response=response))
    result = engine.run_shadow(config, request_)
    dumped = result.model_dump()
    assert "selected_option_id" not in dumped
    assert "probability" not in dumped
    assert "probabilities" not in dumped
    assert "confidence" not in dumped
    assert "reason" not in dumped
    assert result.outcome == "accepted"


def test_every_shadow_result_action_not_permitted(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    for outcome_response in [
        ProviderChoiceResponse(selected_option_id="a", probabilities={"a": 0.95, "b": 0.05}),
        ProviderChoiceResponse(selected_option_id="a", probabilities={"a": 0.5, "b": 0.5}),
    ]:
        monkeypatch.setattr(
            engine, "OpenRouterAdapter", lambda cfg, r=outcome_response: _StubAdapter(response=r)
        )
        result = engine.run_shadow(config, request_)
        assert result.action.permitted is False

    error = ProviderError(DecisionError(code="timeout", message="timeout"))
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: _StubAdapter(error=error))
    result = engine.run_shadow(config, request_)
    assert result.action.permitted is False


def test_full_decision_recoverable_from_protected_store(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    response = ProviderChoiceResponse(
        selected_option_id="b", probabilities={"a": 0.1, "b": 0.9}, confidence=0.9
    )
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: _StubAdapter(response=response))
    result = engine.run_shadow(config, request_)

    protected = store.load(result.record_id, directory=tmp_path)
    assert protected.selected_option_id == "b"
    assert protected.outcome == result.outcome


def test_each_call_gets_a_fresh_record_id(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    response = ProviderChoiceResponse(selected_option_id="a", probabilities={"a": 0.95, "b": 0.05})
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: _StubAdapter(response=response))
    first = engine.run_shadow(config, request_)
    second = engine.run_shadow(config, request_)
    assert first.record_id != second.record_id
