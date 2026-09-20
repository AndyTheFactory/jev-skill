"""Mocked-transport tests for the OpenRouter Decisions adapter."""

from __future__ import annotations

import httpx
import pytest
import respx

from jev_decisions.config import JevConfig
from jev_decisions.provider.openrouter import DECISIONS_URL, OpenRouterAdapter, ProviderError
from jev_decisions.schemas import ChoiceOption, ChoiceRequest


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jev_decisions.provider.openrouter.time.sleep", lambda _s: None)


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch) -> JevConfig:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret-key")
    cfg = JevConfig()
    cfg.provider.max_retries = 2
    return cfg


@pytest.fixture
def request_() -> ChoiceRequest:
    return ChoiceRequest(
        question="Which approach?",
        options=(
            ChoiceOption(id="a", description="Approach A"),
            ChoiceOption(id="b", description="Approach B"),
        ),
    )


@respx.mock
def test_success(config: JevConfig, request_: ChoiceRequest) -> None:
    respx.post(DECISIONS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "answers": {
                    "decision": {
                        "selected": "a",
                        "probabilities": {"a": 0.7, "b": 0.3},
                        "confidence": 0.9,
                    }
                }
            },
        )
    )
    with OpenRouterAdapter(config) as adapter:
        result = adapter.decide(request_)
    assert result.selected_option_id == "a"
    assert result.confidence == 0.9


@respx.mock
def test_malformed_response(config: JevConfig, request_: ChoiceRequest) -> None:
    respx.post(DECISIONS_URL).mock(return_value=httpx.Response(200, json={"nope": True}))
    with OpenRouterAdapter(config) as adapter, pytest.raises(ProviderError) as exc:
        adapter.decide(request_)
    assert exc.value.error.code == "invalid_response"


@respx.mock
def test_response_referencing_undeclared_option(config: JevConfig, request_: ChoiceRequest) -> None:
    respx.post(DECISIONS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "answers": {
                    "decision": {
                        "selected": "not-declared",
                        "probabilities": {"a": 0.5, "b": 0.5},
                    }
                }
            },
        )
    )
    with OpenRouterAdapter(config) as adapter, pytest.raises(ProviderError) as exc:
        adapter.decide(request_)
    assert exc.value.error.code == "invalid_response"


@respx.mock
def test_timeout(config: JevConfig, request_: ChoiceRequest) -> None:
    respx.post(DECISIONS_URL).mock(side_effect=httpx.TimeoutException("boom"))
    with OpenRouterAdapter(config) as adapter, pytest.raises(ProviderError) as exc:
        adapter.decide(request_)
    assert exc.value.error.code == "timeout"


@respx.mock
def test_rate_limited_retries_then_fails(config: JevConfig, request_: ChoiceRequest) -> None:
    route = respx.post(DECISIONS_URL).mock(return_value=httpx.Response(429))
    with OpenRouterAdapter(config) as adapter, pytest.raises(ProviderError) as exc:
        adapter.decide(request_)
    assert exc.value.error.code == "rate_limited"
    assert route.call_count == config.provider.max_retries + 1


@respx.mock
def test_transient_5xx_then_success(config: JevConfig, request_: ChoiceRequest) -> None:
    respx.post(DECISIONS_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(
                200,
                json={
                    "answers": {
                        "decision": {"selected": "b", "probabilities": {"a": 0.2, "b": 0.8}}
                    }
                },
            ),
        ]
    )
    with OpenRouterAdapter(config) as adapter:
        result = adapter.decide(request_)
    assert result.selected_option_id == "b"


@respx.mock
def test_authentication_failure_no_retry(config: JevConfig, request_: ChoiceRequest) -> None:
    route = respx.post(DECISIONS_URL).mock(return_value=httpx.Response(401))
    with OpenRouterAdapter(config) as adapter, pytest.raises(ProviderError) as exc:
        adapter.decide(request_)
    assert exc.value.error.code == "authentication_error"
    assert route.call_count == 1


def test_missing_credential_never_calls_network(request_: ChoiceRequest) -> None:
    cfg = JevConfig()
    with OpenRouterAdapter(cfg) as adapter, pytest.raises(ProviderError) as exc:
        adapter.decide(request_)
    assert exc.value.error.code == "authentication_error"


@respx.mock
def test_credential_never_in_error_message(config: JevConfig, request_: ChoiceRequest) -> None:
    respx.post(DECISIONS_URL).mock(return_value=httpx.Response(400, json={"error": "bad payload"}))
    with OpenRouterAdapter(config) as adapter, pytest.raises(ProviderError) as exc:
        adapter.decide(request_)
    assert "test-secret-key" not in str(exc.value)
    assert exc.value.error.code == "invalid_request"


@respx.mock
def test_request_payload_shape(config: JevConfig, request_: ChoiceRequest) -> None:
    route = respx.post(DECISIONS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "answers": {"decision": {"selected": "a", "probabilities": {"a": 1.0, "b": 0.0}}}
            },
        )
    )
    with OpenRouterAdapter(config) as adapter:
        adapter.decide(request_)
    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer test-secret-key"
    import json

    body = json.loads(sent.content)
    assert body["model"] == config.provider.model
    assert body["questions"]["decision"]["type"] == "choice"
    assert {o["id"] for o in body["questions"]["decision"]["criteria"]["options"]} == {"a", "b"}
