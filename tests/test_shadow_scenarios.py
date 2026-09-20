"""M2 scenario suite: outage, timeout, invalid output, abstention, repeated
question, context change, privacy-restricted context, and task continuity.

Demonstrates that none of these failure/edge cases ever authorizes action,
that Claude's own (baseline) next step is never displaced by a contrary Jev
result, and that dedup/budget behave as specified end to end.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from jev_decisions import baseline as baseline_module
from jev_decisions import budget as budget_module
from jev_decisions import cache as cache_module
from jev_decisions import engine, store
from jev_decisions.baseline import Baseline
from jev_decisions.config import JevConfig
from jev_decisions.provider.openrouter import DECISIONS_URL
from jev_decisions.schemas import ChoiceOption, ChoiceRequest


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "default_store_dir", lambda: tmp_path / "shadow")
    monkeypatch.setattr(baseline_module, "default_baseline_dir", lambda: tmp_path / "baseline")
    monkeypatch.setattr(cache_module, "default_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(budget_module, "default_budget_dir", lambda: tmp_path / "budget")
    monkeypatch.setattr("jev_decisions.provider.openrouter.time.sleep", lambda _s: None)


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> JevConfig:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = JevConfig()
    cfg.provider.max_retries = 0
    cfg.telemetry.path = str(tmp_path / "telemetry.jsonl")
    return cfg


@pytest.fixture
def request_() -> ChoiceRequest:
    return ChoiceRequest(
        question="Which caching strategy fits?",
        options=(
            ChoiceOption(id="lru", description="LRU"),
            ChoiceOption(id="redis", description="Redis"),
        ),
        context="read-heavy endpoint",
    )


def _decision_response(selected: str, probabilities: dict[str, float]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"answers": {"decision": {"selected": selected, "probabilities": probabilities}}},
    )


def _assert_never_actionable(result: engine.ShadowResult) -> None:
    assert result.action.permitted is False
    assert result.outcome != "accepted" or result.action.permitted is False


@respx.mock
def test_api_outage_never_authorizes_action(config: JevConfig, request_: ChoiceRequest) -> None:
    respx.post(DECISIONS_URL).mock(return_value=httpx.Response(503))
    result = engine.run_shadow(config, request_)
    assert result.outcome == "failed"
    _assert_never_actionable(result)


@respx.mock
def test_timeout_never_authorizes_action(config: JevConfig, request_: ChoiceRequest) -> None:
    respx.post(DECISIONS_URL).mock(side_effect=httpx.TimeoutException("boom"))
    result = engine.run_shadow(config, request_)
    assert result.outcome == "failed"
    _assert_never_actionable(result)


@respx.mock
def test_invalid_choice_output_never_authorizes_action(
    config: JevConfig, request_: ChoiceRequest
) -> None:
    respx.post(DECISIONS_URL).mock(
        return_value=httpx.Response(200, json={"answers": {"decision": {"selected": "not-real"}}})
    )
    result = engine.run_shadow(config, request_)
    assert result.outcome == "failed"
    _assert_never_actionable(result)


@respx.mock
def test_explicit_abstention_never_authorizes_action(
    config: JevConfig, request_: ChoiceRequest
) -> None:
    respx.post(DECISIONS_URL).mock(
        return_value=_decision_response("lru", {"lru": 0.5, "redis": 0.5})
    )
    result = engine.run_shadow(config, request_)
    assert result.outcome == "abstained"
    _assert_never_actionable(result)


@respx.mock
def test_repeated_question_one_provider_call(config: JevConfig, request_: ChoiceRequest) -> None:
    route = respx.post(DECISIONS_URL).mock(
        return_value=_decision_response("lru", {"lru": 0.9, "redis": 0.1})
    )
    first = engine.run_shadow(config, request_)
    second = engine.run_shadow(config, request_)
    assert route.call_count == 1
    assert first.outcome == second.outcome == "accepted"
    assert first.record_id != second.record_id


@respx.mock
def test_context_change_triggers_new_call(config: JevConfig, request_: ChoiceRequest) -> None:
    route = respx.post(DECISIONS_URL).mock(
        return_value=_decision_response("lru", {"lru": 0.9, "redis": 0.1})
    )
    engine.run_shadow(config, request_)
    changed = request_.model_copy(update={"context": "write-heavy endpoint now"})
    engine.run_shadow(config, changed)
    assert route.call_count == 2


@respx.mock
def test_privacy_restricted_context_never_in_telemetry(
    config: JevConfig, request_: ChoiceRequest
) -> None:
    secret_context = request_.model_copy(
        update={"context": "internal secret project codename OSPREY-7"}
    )
    respx.post(DECISIONS_URL).mock(
        return_value=_decision_response("lru", {"lru": 0.9, "redis": 0.1})
    )
    engine.run_shadow(config, secret_context)

    telemetry_raw = Path(config.telemetry.path).read_text()
    assert "OSPREY-7" not in telemetry_raw
    assert "secret" not in telemetry_raw.lower()


@respx.mock
def test_task_continuity_baseline_preserved_despite_contrary_jev_output(
    config: JevConfig, request_: ChoiceRequest
) -> None:
    # Jev prefers "redis" confidently, but Claude's baseline already chose "lru".
    respx.post(DECISIONS_URL).mock(
        return_value=_decision_response("redis", {"lru": 0.05, "redis": 0.95})
    )
    baseline = Baseline(action="use an in-process LRU cache", recorded_at=datetime.now(UTC))
    result = engine.run_shadow(config, request_, baseline=baseline)

    # The original task's baseline is untouched and independently recoverable,
    # regardless of what Jev preferred -- nothing here ever surfaces or acts
    # on the selected option outside an explicit `jev reveal`.
    recovered = baseline_module.load_protected(result.record_id)
    assert recovered is not None
    assert recovered.action == "use an in-process LRU cache"
    _assert_never_actionable(result)


@respx.mock
def test_bounded_per_task_budget_enforced(config: JevConfig, request_: ChoiceRequest) -> None:
    route = respx.post(DECISIONS_URL).mock(
        return_value=_decision_response("lru", {"lru": 0.9, "redis": 0.1})
    )
    # Each call must have distinct context so the fingerprint cache doesn't
    # itself dedupe them -- we're testing the budget, not the cache.
    for i in range(20):
        baseline = Baseline(
            action=f"step {i}", task_id="task-under-budget", recorded_at=datetime.now(UTC)
        )
        req = request_.model_copy(update={"context": f"evidence for step {i}"})
        engine.run_shadow(config, req, baseline=baseline)
    assert route.call_count == 20

    baseline = Baseline(
        action="step 21", task_id="task-under-budget", recorded_at=datetime.now(UTC)
    )
    req = request_.model_copy(update={"context": "evidence for step 21"})
    result = engine.run_shadow(config, req, baseline=baseline)

    assert route.call_count == 20  # 21st call did not reach the provider
    assert result.outcome == "failed"
    _assert_never_actionable(result)


@respx.mock
def test_shadow_execution_preserves_baseline_regardless_of_jev_choice(
    config: JevConfig, request_: ChoiceRequest
) -> None:
    respx.post(DECISIONS_URL).mock(
        return_value=_decision_response("redis", {"lru": 0.02, "redis": 0.98})
    )
    baseline = Baseline(action="use an in-process LRU cache", recorded_at=datetime.now(UTC))
    result = engine.run_shadow(config, request_, baseline=baseline)

    saved = baseline_module.load_protected(result.record_id)
    assert saved is not None
    assert saved.action == "use an in-process LRU cache"  # baseline itself is never rewritten
