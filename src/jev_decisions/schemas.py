"""Pydantic v2 contracts for Choice requests, provider responses and errors.

Only the "Choice" question type is supported: pick exactly one of 2-8 mutually
exclusive, distinctly-identified options given bounded context.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSIONS = ("1.0",)
MIN_OPTIONS = 2
MAX_OPTIONS = 8
MAX_CONTEXT_CHARS = 8000
PROBABILITY_SUM_TOLERANCE = 1e-3


class SchemaValidationError(Exception):
    """Raised when a request or response fails contract validation before any I/O."""


class ChoiceOption(BaseModel):
    """One selectable, mutually exclusive alternative."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)


def check_options(value: tuple[ChoiceOption, ...]) -> tuple[ChoiceOption, ...]:
    """Shared 2-8-distinct-options invariant for any Choice-option-bearing model."""
    if not (MIN_OPTIONS <= len(value) <= MAX_OPTIONS):
        raise ValueError(f"options must contain {MIN_OPTIONS}-{MAX_OPTIONS} entries")
    ids = [opt.id for opt in value]
    if len(set(ids)) != len(ids):
        raise ValueError(f"option ids must be distinct, got {ids}")
    return value


class ChoiceRequest(BaseModel):
    """Internal request for a single Choice decision."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    question: str = Field(min_length=1, max_length=1000)
    options: tuple[ChoiceOption, ...]
    context: str = Field(default="", max_length=MAX_CONTEXT_CHARS)
    profile: str | None = None

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: str) -> str:
        if value not in SCHEMA_VERSIONS:
            raise ValueError(f"unsupported schema_version {value!r}; supported: {SCHEMA_VERSIONS}")
        return value

    _check_options = field_validator("options")(check_options)

    def option_ids(self) -> frozenset[str]:
        return frozenset(opt.id for opt in self.options)


class ProviderChoiceResponse(BaseModel):
    """Normalized response from a provider adapter, before policy evaluation."""

    model_config = ConfigDict(extra="forbid")

    selected_option_id: str
    probabilities: dict[str, float]
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("probabilities")
    @classmethod
    def _check_probability_values(cls, value: dict[str, float]) -> dict[str, float]:
        for option_id, prob in value.items():
            if not (0.0 <= prob <= 1.0):
                raise ValueError(f"probability for {option_id!r} out of [0,1]: {prob}")
        total = sum(value.values())
        if abs(total - 1.0) > PROBABILITY_SUM_TOLERANCE:
            raise ValueError(f"probabilities must sum to 1.0, got {total}")
        return value

    def validate_against_request(self, request: ChoiceRequest) -> None:
        """Ensure the response only references declared option ids and is complete."""
        declared = request.option_ids()
        prob_ids = frozenset(self.probabilities)
        if prob_ids != declared:
            raise SchemaValidationError(
                f"probability map {sorted(prob_ids)} does not match declared "
                f"options {sorted(declared)}"
            )
        if self.selected_option_id not in declared:
            raise SchemaValidationError(
                f"selected_option_id {self.selected_option_id!r} not among "
                f"declared options {sorted(declared)}"
            )


class DecisionError(BaseModel):
    """Sanitized, non-sensitive error contract. Never includes credentials or payloads."""

    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "invalid_request",
        "timeout",
        "transport_error",
        "authentication_error",
        "rate_limited",
        "invalid_response",
        "unavailable",
    ]
    message: str = Field(max_length=500)
