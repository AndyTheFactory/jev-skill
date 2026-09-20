"""Deterministic acceptance/abstention policy.

Turns a provider response (or its absence) into one of four outcomes --
``accepted``, ``abstained``, ``failed``, ``rejected`` -- which are policy
judgments only. Whether an outcome may drive any action is a separate
decision made by execution-mode gating (see M2/M4), never by this module.

Initial thresholds (min_probability=0.80, margin=0.20, min_confidence=0.70)
are provisional defaults, not calibrated values.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict

from jev_decisions.config import PolicyConfig
from jev_decisions.provider.openrouter import ProviderError
from jev_decisions.schemas import ChoiceRequest, ProviderChoiceResponse, SchemaValidationError

DecisionOutcome = Literal["accepted", "abstained", "failed", "rejected"]


class Decision(BaseModel):
    """Policy-evaluated outcome. Never implies permission to act."""

    model_config = ConfigDict(extra="forbid")

    outcome: DecisionOutcome
    selected_option_id: str | None = None
    probability: float | None = None
    margin: float | None = None
    confidence: float | None = None
    reason: str


def _failed(reason: str) -> Decision:
    return Decision(outcome="failed", reason=reason)


def _rejected(reason: str) -> Decision:
    return Decision(outcome="rejected", reason=reason)


def evaluate(
    request: ChoiceRequest,
    response: ProviderChoiceResponse | None,
    *,
    error: ProviderError | None = None,
    config: PolicyConfig | None = None,
    abstain_option_ids: frozenset[str] = frozenset(),
) -> Decision:
    """Evaluate a provider outcome against policy. Fails closed on any anomaly."""
    config = config or PolicyConfig()

    if error is not None:
        return _failed(f"provider error: {error.error.code}")
    if response is None:
        return _failed("no provider response")

    try:
        response.validate_against_request(request)
    except SchemaValidationError as exc:
        return _rejected(str(exc))

    ranked_values = sorted(response.probabilities.values(), reverse=True)
    top_prob = ranked_values[0]
    runner_up_prob = ranked_values[1] if len(ranked_values) > 1 else 0.0
    margin = top_prob - runner_up_prob
    selected_prob = response.probabilities[response.selected_option_id]

    if not math.isclose(selected_prob, top_prob, abs_tol=1e-9):
        return _rejected(
            "selected_option_id does not match the highest-probability option; "
            "response is internally inconsistent"
        )

    if response.selected_option_id in abstain_option_ids:
        return Decision(
            outcome="abstained",
            selected_option_id=response.selected_option_id,
            probability=top_prob,
            margin=margin,
            confidence=response.confidence,
            reason="provider selected an explicit abstention option",
        )

    if top_prob < config.min_probability:
        return Decision(
            outcome="abstained",
            selected_option_id=response.selected_option_id,
            probability=top_prob,
            margin=margin,
            confidence=response.confidence,
            reason=f"top probability {top_prob:.3f} below threshold {config.min_probability}",
        )

    if margin < config.margin:
        return Decision(
            outcome="abstained",
            selected_option_id=response.selected_option_id,
            probability=top_prob,
            margin=margin,
            confidence=response.confidence,
            reason=f"margin {margin:.3f} below threshold {config.margin}",
        )

    if response.confidence is not None and response.confidence < config.min_confidence:
        return Decision(
            outcome="abstained",
            selected_option_id=response.selected_option_id,
            probability=top_prob,
            margin=margin,
            confidence=response.confidence,
            reason=f"confidence {response.confidence:.3f} below threshold {config.min_confidence}",
        )

    return Decision(
        outcome="accepted",
        selected_option_id=response.selected_option_id,
        probability=top_prob,
        margin=margin,
        confidence=response.confidence,
        reason="thresholds satisfied",
    )
