"""Shared test helpers."""

from __future__ import annotations

from jev_decisions.provider.openrouter import ProviderError
from jev_decisions.schemas import ChoiceRequest, ProviderChoiceResponse


class StubAdapter:
    """Drop-in replacement for OpenRouterAdapter: returns a canned response or raises."""

    def __init__(
        self,
        response: ProviderChoiceResponse | None = None,
        error: ProviderError | None = None,
    ) -> None:
        self._response = response
        self._error = error

    def __enter__(self) -> StubAdapter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response
