"""Tests proving shadow-result isolation at the engine level."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jev_decisions import baseline as baseline_module
from jev_decisions import cache as cache_module
from jev_decisions import engine, store
from jev_decisions.baseline import Baseline
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


def test_baseline_persisted_and_recoverable_alongside_decision(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    response = ProviderChoiceResponse(selected_option_id="a", probabilities={"a": 0.95, "b": 0.05})
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: _StubAdapter(response=response))
    baseline = Baseline(action="reproduce first", recorded_at=datetime.now(UTC))
    result = engine.run_shadow(config, request_, baseline=baseline)

    loaded = baseline_module.load_protected(result.record_id, directory=tmp_path / "baseline")
    assert loaded == baseline


def test_baseline_persisted_before_provider_call(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    order: list[str] = []

    class _OrderTrackingAdapter:
        def __enter__(self) -> _OrderTrackingAdapter:
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
            order.append("provider_call")
            return ProviderChoiceResponse(
                selected_option_id="a", probabilities={"a": 0.95, "b": 0.05}
            )

    original_save = baseline_module.save_protected

    def _tracking_save(*args: object, **kwargs: object) -> None:
        order.append("baseline_saved")
        original_save(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(baseline_module, "save_protected", _tracking_save)
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: _OrderTrackingAdapter())
    baseline = Baseline(action="reproduce first", recorded_at=datetime.now(UTC))
    engine.run_shadow(config, request_, baseline=baseline)

    assert order == ["baseline_saved", "provider_call"]


def test_no_baseline_by_default(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    response = ProviderChoiceResponse(selected_option_id="a", probabilities={"a": 0.95, "b": 0.05})
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: _StubAdapter(response=response))
    result = engine.run_shadow(config, request_)
    assert baseline_module.load_protected(result.record_id, directory=tmp_path / "baseline") is None


def test_repeated_identical_request_reuses_cache_one_provider_call(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = {"n": 0}

    class _CountingAdapter(_StubAdapter):
        def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
            call_count["n"] += 1
            return super().decide(request)

    response = ProviderChoiceResponse(selected_option_id="a", probabilities={"a": 0.95, "b": 0.05})
    monkeypatch.setattr(
        engine, "OpenRouterAdapter", lambda cfg: _CountingAdapter(response=response)
    )
    first = engine.run_shadow(config, request_)
    second = engine.run_shadow(config, request_)

    assert call_count["n"] == 1
    assert first.outcome == second.outcome == "accepted"
    assert first.record_id != second.record_id  # unique correlation id per call


def test_rejected_outcome_not_cached_retries_provider(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = {"n": 0}

    class _InconsistentAdapter(_StubAdapter):
        def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
            call_count["n"] += 1
            # selected_option_id disagrees with the top-probability option:
            # policy.evaluate treats this as "rejected", not a stable verdict.
            return ProviderChoiceResponse.model_construct(
                selected_option_id="b", probabilities={"a": 0.9, "b": 0.1}, confidence=None
            )

    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: _InconsistentAdapter())
    first = engine.run_shadow(config, request_)
    second = engine.run_shadow(config, request_)

    assert first.outcome == second.outcome == "rejected"
    assert call_count["n"] == 2


def test_different_abstain_option_ids_are_not_conflated_in_cache(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = {"n": 0}

    class _CountingAdapter(_StubAdapter):
        def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
            call_count["n"] += 1
            return super().decide(request)

    response = ProviderChoiceResponse(selected_option_id="a", probabilities={"a": 0.95, "b": 0.05})
    monkeypatch.setattr(
        engine, "OpenRouterAdapter", lambda cfg: _CountingAdapter(response=response)
    )
    engine.run_shadow(config, request_, abstain_option_ids=frozenset())
    engine.run_shadow(config, request_, abstain_option_ids=frozenset({"a"}))

    assert call_count["n"] == 2


def test_changed_context_invalidates_cache_new_provider_call(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = {"n": 0}

    class _CountingAdapter(_StubAdapter):
        def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
            call_count["n"] += 1
            return super().decide(request)

    response = ProviderChoiceResponse(selected_option_id="a", probabilities={"a": 0.95, "b": 0.05})
    monkeypatch.setattr(
        engine, "OpenRouterAdapter", lambda cfg: _CountingAdapter(response=response)
    )
    engine.run_shadow(config, request_)
    changed = request_.model_copy(update={"context": "new evidence changes things"})
    engine.run_shadow(config, changed)

    assert call_count["n"] == 2


def test_failed_outcome_not_cached_retries_provider(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = {"n": 0}
    error = ProviderError(DecisionError(code="timeout", message="timeout"))

    class _CountingFailingAdapter(_StubAdapter):
        def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
            call_count["n"] += 1
            raise error

    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: _CountingFailingAdapter())
    engine.run_shadow(config, request_)
    engine.run_shadow(config, request_)

    assert call_count["n"] == 2


def test_run_shadow_emits_telemetry_event(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config.telemetry.path = str(tmp_path / "telemetry.jsonl")
    response = ProviderChoiceResponse(selected_option_id="a", probabilities={"a": 0.95, "b": 0.05})
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: _StubAdapter(response=response))
    result = engine.run_shadow(config, request_)

    from jev_decisions.telemetry import read_events

    events = read_events(config.telemetry)
    assert len(events) == 1
    assert events[0].id == result.record_id
    assert events[0].outcome == "accepted"


def test_telemetry_write_failure_does_not_break_decide(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = ProviderChoiceResponse(selected_option_id="a", probabilities={"a": 0.95, "b": 0.05})
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: _StubAdapter(response=response))

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk is on fire")

    from jev_decisions import telemetry

    monkeypatch.setattr(telemetry, "write_event", _boom)
    result = engine.run_shadow(config, request_)  # must not raise
    assert result.outcome == "accepted"


def test_each_call_gets_a_fresh_record_id(
    config: JevConfig, request_: ChoiceRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    response = ProviderChoiceResponse(selected_option_id="a", probabilities={"a": 0.95, "b": 0.05})
    monkeypatch.setattr(engine, "OpenRouterAdapter", lambda cfg: _StubAdapter(response=response))
    first = engine.run_shadow(config, request_)
    second = engine.run_shadow(config, request_)
    assert first.record_id != second.record_id
