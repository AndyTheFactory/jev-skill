"""Shadow-mode engine: isolates the full Decision from the caller.

`run_shadow` is the only path `jev decide` uses to produce a decision. It
always persists the full policy Decision to the protected store and returns
only a :class:`ShadowResult` -- record id, outcome, and
``action.permitted=False`` -- never the selected option, probability,
confidence or reasoning. Those are only readable back via `jev reveal`,
outside the original task.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from jev_decisions import store
from jev_decisions.config import JevConfig
from jev_decisions.policy import DecisionOutcome, evaluate
from jev_decisions.provider.openrouter import OpenRouterAdapter, ProviderError
from jev_decisions.schemas import ChoiceRequest


class ActionPermission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permitted: bool = False


class ShadowResult(BaseModel):
    """Everything a standard `jev decide` call in shadow mode may reveal."""

    model_config = ConfigDict(extra="forbid")

    record_id: str
    outcome: DecisionOutcome
    action: ActionPermission = ActionPermission()


def run_shadow(
    config: JevConfig,
    request: ChoiceRequest,
    *,
    abstain_option_ids: frozenset[str] = frozenset(),
) -> ShadowResult:
    """Run one Choice decision and return only the shadow-safe summary."""
    try:
        with OpenRouterAdapter(config) as adapter:
            response = adapter.decide(request)
    except ProviderError as exc:
        decision = evaluate(request, None, error=exc, config=config.policy)
    else:
        decision = evaluate(
            request, response, config=config.policy, abstain_option_ids=abstain_option_ids
        )

    record_id = store.new_record_id()
    store.save(decision, record_id)
    return ShadowResult(record_id=record_id, outcome=decision.outcome)
