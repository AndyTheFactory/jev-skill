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

from jev_decisions import baseline as baseline_module
from jev_decisions import cache as cache_module
from jev_decisions import store
from jev_decisions.baseline import Baseline
from jev_decisions.config import JevConfig
from jev_decisions.fingerprint import compute_fingerprint
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
    baseline: Baseline | None = None,
) -> ShadowResult:
    """Run one Choice decision and return only the shadow-safe summary.

    ``baseline``, if given, is persisted under the same record id *before*
    the provider is called, so it is always recorded independently of
    whatever Jev returns. An equivalent, unexpired, previously cached
    decision (same fingerprint: schema/question/options/context/profile/
    model/policy) is reused instead of calling the provider again -- each
    call still gets its own fresh record id.
    """
    record_id = store.new_record_id()
    if baseline is not None:
        baseline_module.save_protected(baseline, record_id)

    fingerprint = compute_fingerprint(request, config)
    decision = cache_module.get(fingerprint)
    if decision is None:
        try:
            with OpenRouterAdapter(config) as adapter:
                response = adapter.decide(request)
        except ProviderError as exc:
            decision = evaluate(request, None, error=exc, config=config.policy)
        else:
            decision = evaluate(
                request, response, config=config.policy, abstain_option_ids=abstain_option_ids
            )
        if decision.outcome not in ("failed",):
            cache_module.put(fingerprint, decision)

    store.save(decision, record_id)
    return ShadowResult(record_id=record_id, outcome=decision.outcome)
