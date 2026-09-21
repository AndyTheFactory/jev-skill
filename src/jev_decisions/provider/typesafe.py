"""Native TypeSafe SDK adapter for one Choice question.

The optional SDK owns HTTP and retry behavior; errors are deliberately sanitized.
This module can be imported without installing the SDK (OpenRouter remains usable).
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from pydantic import ValidationError

from jev_decisions.config import JevConfig
from jev_decisions.provider.openrouter import ProviderError
from jev_decisions.schemas import (
    ChoiceRequest,
    DecisionError,
    ProviderChoiceResponse,
    SchemaValidationError,
)

QUESTION_KEY = "decision"


def _error(code: str, message: str) -> ProviderError:
    return ProviderError(DecisionError(code=code, message=message))  # type: ignore[arg-type]


class TypeSafeAdapter:
    """Use TypeSafeClient.system_one while preserving the shared response contract."""

    def __init__(self, config: JevConfig, client: Any = None) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None

    def __enter__(self) -> TypeSafeAdapter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
        key = self._config.get_api_key()
        if not key:
            raise _error(
                "authentication_error",
                f"missing credential: set {self._config.provider.credential_env}",
            )

        try:
            sdk = import_module("typesafe_sdk")
        except ImportError:
            raise _error(
                "unavailable", "TypeSafe SDK is not installed; install jev-decisions[typesafe]"
            ) from None

        if self._client is None:
            try:
                # The SDK handles bounded retries; do not add an outer retry loop.
                retry = sdk.RetryPolicy(
                    max_retries=self._config.provider.max_retries,
                    timeout=self._config.provider.timeout_seconds
                    * (self._config.provider.max_retries + 1),
                )
                self._client = sdk.TypeSafeClient(
                    api_key=key,
                    model=self._config.provider.resolved_model,
                    timeout=self._config.provider.timeout_seconds,
                    retry=retry,
                )
            except Exception as exc:
                raise _map_error(exc) from None

        try:
            result = self._client.system_one(
                state={"context": request.context},
                questions={
                    QUESTION_KEY: sdk.Choice(
                        instructions=request.question,
                        criteria={opt.id: opt.description for opt in request.options},
                    )
                },
                model=self._config.provider.resolved_model,
            )
            answer = result.choices[QUESTION_KEY]
            response = ProviderChoiceResponse(
                selected_option_id=answer.choice,
                probabilities=answer.probabilities,
                confidence=getattr(answer, "confidence", None),
            )
            response.validate_against_request(request)
            return response
        except (ValidationError, SchemaValidationError, KeyError, AttributeError, TypeError):
            # Never include SDK response bodies or user context in diagnostics.
            raise _error("invalid_response", "malformed TypeSafe Choice response") from None
        except Exception as exc:
            raise _map_error(exc) from None


def _map_error(exc: Exception) -> ProviderError:
    """Use SDK exception categories without copying sensitive exception messages."""
    name = type(exc).__name__
    if name in {"TypeSafeAuthenticationError", "TypeSafePermissionDeniedError"}:
        return _error("authentication_error", "TypeSafe rejected credential")
    if name == "TypeSafeRateLimitError":
        return _error("rate_limited", "TypeSafe rate limit exceeded")
    if name == "TypeSafeAPITimeoutError":
        return _error("timeout", "TypeSafe request timed out")
    if name in {"TypeSafeAPIConnectionError", "TypeSafeInternalServerError"}:
        return _error("transport_error", "TypeSafe transport failure")
    if name == "TypeSafeAPIResponseValidationError":
        return _error("invalid_response", "TypeSafe returned an invalid response")
    if name in {"TypeSafeBadRequestError", "TypeSafeNotFoundError",
                "TypeSafeUnprocessableEntityError"}:
        return _error("invalid_request", "TypeSafe rejected request or model")
    return _error("unavailable", "TypeSafe SDK request failed")
