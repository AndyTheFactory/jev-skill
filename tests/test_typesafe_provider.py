"""Native TypeSafe SDK and cross-provider contract tests; no network traffic."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jev_decisions.config import JevConfig, ProviderConfig
from jev_decisions.fingerprint import compute_fingerprint
from jev_decisions.policy import evaluate
from jev_decisions.provider.factory import create_adapter
from jev_decisions.provider.openrouter import OpenRouterAdapter, ProviderError
from jev_decisions.provider.typesafe import TypeSafeAdapter
from jev_decisions.schemas import ChoiceOption, ChoiceRequest


@pytest.fixture
def question() -> ChoiceRequest:
    return ChoiceRequest(
        question="Where should we look first?",
        context="Database connection timed out.",
        options=(
            ChoiceOption(id="database", description="Check database connections."),
            ChoiceOption(id="frontend", description="Inspect rendering."),
        ),
    )


def _config(name: str = "typesafe") -> JevConfig:
    return JevConfig(provider=ProviderConfig(name=name))  # type: ignore[arg-type]


class FakeClient:
    def __init__(self, selected: str = "database", probabilities: dict[str, float] | None = None) -> None:
        self.selected = selected
        self.probabilities = probabilities or {"database": 0.95, "frontend": 0.05}
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def system_one(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(choices={
            "decision": SimpleNamespace(
                choice=self.selected,
                probabilities=self.probabilities,
                confidence=0.90,
            )
        })

    def close(self) -> None:
        self.closed = True


def test_factory_selects_requested_provider() -> None:
    assert isinstance(create_adapter(_config("openrouter")), OpenRouterAdapter)
    assert isinstance(create_adapter(_config("typesafe")), TypeSafeAdapter)


def test_sdk_choice_mapping_and_shared_policy(
    question: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    client = FakeClient()
    with TypeSafeAdapter(_config(), client=client) as adapter:
        result = adapter.decide(question)

    assert result.selected_option_id == "database"
    assert result.probabilities["database"] == 0.95
    assert result.confidence == 0.90
    assert evaluate(question, result, config=_config().policy).outcome == "accepted"
    assert client.calls[0]["state"] == {"context": question.context}
    assert client.calls[0]["model"] == "jev-latest"
    choice = client.calls[0]["questions"]["decision"]
    assert choice.criteria == {
        "database": "Check database connections.",
        "frontend": "Inspect rendering.",
    }
    assert not client.closed  # injected SDK clients belong to their caller


def test_missing_typesafe_credential_fails_closed(
    question: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ProviderError) as exc:
        TypeSafeAdapter(_config()).decide(question)
    assert exc.value.error.code == "authentication_error"
    assert "TYPESAFE_API_KEY" in exc.value.error.message


@pytest.mark.parametrize(
    "selected,probabilities",
    [
        ("unknown", {"database": 0.95, "frontend": 0.05}),
        ("database", {"database": 1.0}),
    ],
)
def test_invalid_choice_fails_closed(
    question: ChoiceRequest,
    monkeypatch: pytest.MonkeyPatch,
    selected: str,
    probabilities: dict[str, float],
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    with pytest.raises(ProviderError) as exc:
        TypeSafeAdapter(_config(), client=FakeClient(selected, probabilities)).decide(question)
    assert exc.value.error.code == "invalid_response"


def test_provider_is_in_cache_fingerprint(question: ChoiceRequest) -> None:
    openrouter = _config("openrouter")
    typesafe = _config("typesafe")
    openrouter.provider.model = "same-model"
    typesafe.provider.model = "same-model"
    assert compute_fingerprint(question, openrouter) != compute_fingerprint(question, typesafe)


def test_type_safe_model_and_env_defaults() -> None:
    assert _config("openrouter").provider.credential_env == "OPENROUTER_API_KEY"
    assert _config("typesafe").provider.credential_env == "TYPESAFE_API_KEY"
    assert _config("typesafe").provider.resolved_model == "jev-latest"
    with pytest.raises(ValueError, match="OpenRouter-style"):
        ProviderConfig(name="typesafe", model="~typesafe/jev-latest")


def test_sdk_error_does_not_leak_secret(
    question: ChoiceRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "sensitive-secret")
    class FailingClient(FakeClient):
        def system_one(self, **kwargs: Any) -> Any:
            raise type("TypeSafeRateLimitError", (Exception,), {})("sensitive-secret")

    with pytest.raises(ProviderError) as exc:
        TypeSafeAdapter(_config(), client=FailingClient()).decide(question)
    assert exc.value.error.code == "rate_limited"
    assert "sensitive-secret" not in str(exc.value)
