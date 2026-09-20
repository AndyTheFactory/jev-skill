"""Optional adapter: map a CC-Enrutador router task state into a Jev Choice request.

Not imported by any default runtime path in this package (`cli`, `engine`,
or the `jev-decisions` skill) -- jev-skill has no dependency, forced or
otherwise, on CC-Enrutador. A CC-Enrutador integration opts in by importing
this module itself; not importing it changes nothing about how the skill
or CLI behave. See docs/cc-enrutador-integration.md for the full design
notes, privacy boundary, and why this stays a thin mapping function rather
than a live client.

The `RouterState` shape here is a reasonable generic guess (task id, a
description of the current step, and a set of candidate next actions) --
this repo has no access to CC-Enrutador's actual internal schema, so treat
the field names as illustrative and adjust them to the real router's state
shape when wiring this up for an actual integration.
"""

from __future__ import annotations

from typing import TypedDict

from pydantic import ValidationError

from jev_decisions.schemas import ChoiceOption, ChoiceRequest


class RouterCandidateAction(TypedDict):
    id: str
    description: str


class RouterState(TypedDict, total=False):
    task_id: str
    current_step: str
    candidate_actions: list[RouterCandidateAction]
    context: str


class RouterStateError(Exception):
    """Raised when a router state can't be mapped to a valid Choice request."""


def map_router_state_to_choice_request(
    state: RouterState, *, profile: str | None = None
) -> ChoiceRequest:
    """Map router state to a Choice request, forwarding only what's needed.

    Privacy boundary: only ``current_step``, ``candidate_actions`` and
    ``context`` are read -- nothing else in a router's state (credentials,
    unrelated task history, other users' data) is ever forwarded, even if
    present on the input dict. ``context`` still goes through
    :class:`ChoiceRequest`'s own 8,000-character bound.
    """
    candidates = state.get("candidate_actions") or []
    try:
        options = tuple(
            ChoiceOption(id=c["id"], description=c["description"]) for c in candidates
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise RouterStateError(f"malformed candidate_actions: {exc}") from exc

    question = state.get("current_step") or "Which candidate action fits best?"
    context = state.get("context", "")

    try:
        return ChoiceRequest(
            question=question, options=options, context=context, profile=profile
        )
    except ValidationError as exc:
        raise RouterStateError(
            f"router state did not map to a valid Choice request: {exc}"
        ) from exc
