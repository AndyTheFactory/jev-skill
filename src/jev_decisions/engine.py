"""Shadow-mode engine: isolates the full Decision from the caller.

`run_shadow` always persists the full policy Decision to the protected
store and returns only a :class:`ShadowResult` -- record id and outcome --
never the selected option, probability, confidence or reasoning. It is
unconditional: every caller of `run_shadow` gets this regardless of config,
which is what makes it safe for things like the benchmark runner
(`scripts/run_benchmark.py`) that must never reveal a result during a task
no matter how the operator's config is set.

`run_decision` is what `jev decide` actually calls. It shares `run_shadow`'s
internal `_run` (one fingerprint/cache/provider/budget/telemetry/persist
pass -- no re-reading what was just written), then -- only for an
"accepted" outcome, only when
``config.execution.mode == "active"``, and only when the request's profile
is explicitly listed in ``config.execution.active_profiles`` -- upgrades the
result to an :class:`AdvisoryResult` that also exposes the selected option
and its probability. This makes the recommendation visible for Claude's own
reasoning to weigh; Jev never executes anything itself. Every other case
(shadow mode, a profile not explicitly enabled, or any non-"accepted"
outcome) gets the ordinary `ShadowResult`.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from jev_decisions import baseline as baseline_module
from jev_decisions import budget as budget_module
from jev_decisions import cache as cache_module
from jev_decisions import store
from jev_decisions import telemetry as telemetry_module
from jev_decisions.baseline import Baseline
from jev_decisions.budget import BudgetExceededError
from jev_decisions.config import JevConfig
from jev_decisions.fingerprint import compute_fingerprint
from jev_decisions.policy import Decision, DecisionOutcome, evaluate
from jev_decisions.provider.factory import create_adapter
from jev_decisions.provider.openrouter import OpenRouterAdapter, ProviderError
from jev_decisions.schemas import ChoiceRequest
from jev_decisions.schemas import DecisionError as _DecisionError


class ShadowResult(BaseModel):
    """Everything a standard `jev decide` call in shadow mode may reveal."""

    model_config = ConfigDict(extra="forbid")

    record_id: str
    outcome: DecisionOutcome


class AdvisoryResult(BaseModel):
    """Surfaced only for outcome="accepted" on an explicitly active-mode-enabled
    profile. A visible recommendation for Claude's own reasoning, never an
    execution authorization, a permission grant, or a substitute for the
    user's explicit instructions.
    """

    model_config = ConfigDict(extra="forbid")

    record_id: str
    outcome: DecisionOutcome
    selected_option_id: str
    probability: float


def _profile_is_active(config: JevConfig, request: ChoiceRequest) -> bool:
    return (
        config.execution.mode == "active"
        and request.profile is not None
        and request.profile in config.execution.active_profiles
    )


def _run(
    config: JevConfig,
    request: ChoiceRequest,
    *,
    abstain_option_ids: frozenset[str],
    baseline: Baseline | None,
) -> tuple[str, Decision]:
    """Do the actual work: fingerprint/cache, provider call, budget, persist,
    telemetry. Returns the record id and the full in-memory Decision, so
    callers never need to re-read what was just written back off disk.
    """
    record_id = store.new_record_id()
    if baseline is not None:
        baseline_module.save_protected(baseline, record_id)

    fingerprint = compute_fingerprint(request, config, abstain_option_ids=abstain_option_ids)
    started = time.monotonic()
    decision = cache_module.get(fingerprint)
    if decision is None:
        task_id = baseline.task_id if baseline is not None else None
        budget_error: ProviderError | None = None
        if task_id is not None:
            try:
                budget_module.check_and_increment(task_id)
            except BudgetExceededError as exc:
                budget_error = ProviderError(
                    _DecisionError(code="unavailable", message=str(exc))
                )

        if budget_error is not None:
            decision = evaluate(request, None, error=budget_error, config=config.policy)
        else:
            try:
                with create_adapter(config, openrouter_cls=OpenRouterAdapter) as adapter:
                    response = adapter.decide(request)
            except ProviderError as exc:
                decision = evaluate(request, None, error=exc, config=config.policy)
            else:
                decision = evaluate(
                    request,
                    response,
                    config=config.policy,
                    abstain_option_ids=abstain_option_ids,
                )
        if decision.outcome in ("accepted", "abstained"):
            cache_module.put(fingerprint, decision)
    latency_ms = (time.monotonic() - started) * 1000

    store.save(decision, record_id)
    try:
        telemetry_module.write_event(
            config.telemetry,
            telemetry_module.TelemetryEvent(
                id=record_id,
                fingerprint=fingerprint,
                timestamp=datetime.now(UTC),
                profile=request.profile,
                model=config.provider.resolved_model,
                provider=config.provider.name,
                mode=config.execution.mode,
                outcome=decision.outcome,
                latency_ms=latency_ms,
                baseline_task_id=baseline.task_id if baseline is not None else None,
            ),
        )
    except Exception:  # telemetry must never break a completed decision
        pass
    return record_id, decision


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
    model/policy/abstain-option-ids) is reused instead of calling the
    provider again -- each call still gets its own fresh record id. Only
    "accepted"/"abstained" outcomes are cached; "failed" and "rejected" are
    one-off anomalies (transient error, internally-inconsistent response)
    that deserve a fresh attempt next time, not a stale cached verdict. When
    ``baseline.task_id`` is set, actual provider calls (not cache hits) for
    that task are capped per rolling window; over budget fails closed the
    same as a provider error, never as an authorization to act.
    """
    record_id, decision = _run(
        config, request, abstain_option_ids=abstain_option_ids, baseline=baseline
    )
    return ShadowResult(record_id=record_id, outcome=decision.outcome)


def run_decision(
    config: JevConfig,
    request: ChoiceRequest,
    *,
    abstain_option_ids: frozenset[str] = frozenset(),
    baseline: Baseline | None = None,
) -> ShadowResult | AdvisoryResult:
    """Entry point for `jev decide`: shadow by default, advisory only when
    explicitly enabled for this exact profile and the outcome is "accepted".

    Never returns anything different from `run_shadow` for shadow mode, a
    non-enabled profile, or a non-"accepted" outcome -- those always fall
    back to `ShadowResult`, with no selected option visible, so normal
    (Claude's own) reasoning is what actually continues the task.
    """
    record_id, decision = _run(
        config, request, abstain_option_ids=abstain_option_ids, baseline=baseline
    )
    if decision.outcome != "accepted" or not _profile_is_active(config, request):
        return ShadowResult(record_id=record_id, outcome=decision.outcome)

    if decision.selected_option_id is None or decision.probability is None:
        # policy.evaluate only ever sets outcome="accepted" together with
        # both fields, so this is an internal invariant, not a normal
        # failure -- raised explicitly (not `assert`) so it can't silently
        # vanish under -O and fall through to a ValidationError instead.
        raise RuntimeError(
            "internal invariant violated: accepted decision missing "
            "selected_option_id/probability"
        )
    return AdvisoryResult(
        record_id=record_id,
        outcome=decision.outcome,
        selected_option_id=decision.selected_option_id,
        probability=decision.probability,
    )
