"""OpenRouter Decisions API adapter (alpha endpoint, ``~typesafe/jev-latest``).

Sends a single Choice question, validates and normalizes the response, retries
bounded transient failures, and never leaks credentials or payloads in errors.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import ValidationError

from jev_decisions.config import JevConfig
from jev_decisions.schemas import ChoiceRequest, DecisionError, ProviderChoiceResponse

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
QUESTION_KEY = "decision"
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 524, 529})
_BACKOFF_SECONDS = 0.5


class ProviderError(Exception):
    """Wraps a sanitized :class:`DecisionError`; never carries raw payloads or keys."""

    def __init__(self, error: DecisionError) -> None:
        super().__init__(error.message)
        self.error = error


def _build_payload(config: JevConfig, request: ChoiceRequest) -> dict[str, Any]:
    return {
        "model": config.provider.model,
        "state": {"context": request.context},
        "questions": {
            QUESTION_KEY: {
                "type": "choice",
                "instructions": request.question,
                "criteria": {
                    "options": [
                        {"id": opt.id, "description": opt.description} for opt in request.options
                    ]
                },
            }
        },
    }


def _parse_response(request: ChoiceRequest, body: dict[str, Any]) -> ProviderChoiceResponse:
    try:
        answers = body["answers"]
        answer = answers[QUESTION_KEY]
        response = ProviderChoiceResponse(
            selected_option_id=answer["selected"],
            probabilities=answer["probabilities"],
            confidence=answer.get("confidence"),
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise ProviderError(
            DecisionError(code="invalid_response", message=f"malformed provider response: {exc}")
        ) from None
    try:
        response.validate_against_request(request)
    except Exception as exc:
        raise ProviderError(
            DecisionError(code="invalid_response", message=str(exc))
        ) from None
    return response


class OpenRouterAdapter:
    """Thin, testable HTTP adapter around the OpenRouter Decisions API."""

    def __init__(self, config: JevConfig, client: httpx.Client | None = None) -> None:
        self._config = config
        self._client = client or httpx.Client()
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OpenRouterAdapter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
        api_key = self._config.get_api_key()
        if not api_key:
            raise ProviderError(
                DecisionError(
                    code="authentication_error",
                    message=f"missing credential: set {self._config.provider.api_key_env}",
                )
            )

        payload = _build_payload(self._config, request)
        headers = {"Authorization": f"Bearer {api_key}"}
        timeout = self._config.provider.timeout_seconds
        max_retries = self._config.provider.max_retries

        last_error: ProviderError | None = None
        for attempt in range(max_retries + 1):
            try:
                resp = self._client.post(
                    DECISIONS_URL, json=payload, headers=headers, timeout=timeout
                )
            except httpx.TimeoutException:
                last_error = ProviderError(
                    DecisionError(code="timeout", message="provider request timed out")
                )
                self._sleep(attempt)
                continue
            except httpx.HTTPError as exc:
                last_error = ProviderError(
                    DecisionError(
                        code="transport_error", message=f"transport failure: {type(exc).__name__}"
                    )
                )
                self._sleep(attempt)
                continue

            if resp.status_code in (401, 403):
                raise ProviderError(
                    DecisionError(
                        code="authentication_error", message="provider rejected credential"
                    )
                )
            if resp.status_code == 429:
                last_error = ProviderError(
                    DecisionError(code="rate_limited", message="provider rate limit exceeded")
                )
                self._sleep(attempt)
                continue
            if resp.status_code in _RETRYABLE_STATUS:
                last_error = ProviderError(
                    DecisionError(
                        code="transport_error",
                        message=f"transient provider error (status {resp.status_code})",
                    )
                )
                self._sleep(attempt)
                continue
            if resp.status_code >= 400:
                raise ProviderError(
                    DecisionError(
                        code="invalid_request",
                        message=f"provider rejected request (status {resp.status_code})",
                    )
                )

            try:
                body = resp.json()
            except ValueError:
                raise ProviderError(
                    DecisionError(
                        code="invalid_response", message="provider returned non-JSON body"
                    )
                ) from None
            return _parse_response(request, body)

        assert last_error is not None
        raise last_error

    def _sleep(self, attempt: int) -> None:
        if attempt < self._config.provider.max_retries:
            time.sleep(_BACKOFF_SECONDS * (2**attempt))
