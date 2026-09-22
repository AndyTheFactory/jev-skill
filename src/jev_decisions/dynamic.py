"""Dynamic Choice formulation: build and validate ad-hoc questions.

Used when no static profile (see :mod:`jev_decisions.profiles`) fits. Claude
formulates the question and options itself; this module is defense in depth,
not the primary safeguard -- the primary safeguard is the prompt guidance in
``skill/jev-decisions/references/dynamic-choice.md``, which instructs Claude
not to invoke Jev for unsuitable decisions in the first place.

A dynamically supplied request carries no policy or security fields of its
own (:class:`~jev_decisions.schemas.ChoiceRequest` has none), so it can never
relax the policy thresholds or the active-mode trust ceiling configured
globally -- there is nothing on the request for it to override.
"""

from __future__ import annotations

from jev_decisions.schemas import MAX_OPTIONS, ChoiceOption, ChoiceRequest

UNCERTAIN_OPTION_IDS = frozenset({"unknown", "insufficient_context"})

# Reserved profile id for caller-formulated questions. Unlike the packaged
# profiles it has no fixed question/options: the request supplies both, and
# the CLI runs validate_dynamic_request on it. Listing it in
# execution.active_profiles is the explicit opt-in for active-mode output on
# dynamic questions; like any active profile, only a trusted config layer
# (user/env/cli) can list it.
DYNAMIC_PROFILE_ID = "dynamic"

# Keyword categories this module rejects, per the issue's suitability
# criteria: unverifiable, deterministic, high-risk-permission, or
# broad-architecture judgments. Kept small and explicit; the prompt guidance
# is the primary control, this is a fail-closed backstop.
_HIGH_RISK_PERMISSION_KEYWORDS = (
    "delete",
    "destroy",
    "drop table",
    "force push",
    "deploy",
    "production database",
    "production deployment",
    "production credentials",
    "push to production",
    "credential",
    "secret",
    "rm -rf",
    "revoke",
    "payment",
)
_BROAD_ARCHITECTURE_KEYWORDS = (
    "redesign the architecture",
    "rewrite the entire",
    "migrate the whole system",
)


class DynamicRequestRejected(Exception):
    """Raised when a dynamically formulated Choice question is unsuitable."""


def build_dynamic_request(
    question: str,
    options: list[ChoiceOption],
    *,
    context: str = "",
    include_uncertain_option: bool = True,
) -> ChoiceRequest:
    """Build a Choice request, optionally adding an ``insufficient_context`` option.

    Raises :class:`DynamicRequestRejected` if the resulting request is
    structurally unsuitable (too many options) or references content this
    module treats as unsuitable for a Choice decision.
    """
    opts = list(options)
    if include_uncertain_option and not any(o.id in UNCERTAIN_OPTION_IDS for o in opts):
        if len(opts) >= MAX_OPTIONS:
            raise DynamicRequestRejected(
                f"cannot add an insufficient_context option: already at the "
                f"{MAX_OPTIONS}-option limit"
            )
        opts.append(
            ChoiceOption(
                id="insufficient_context",
                description="Not enough information to choose among the other options.",
            )
        )

    request = ChoiceRequest(question=question, options=tuple(opts), context=context)
    validate_dynamic_request(request)
    return request


def validate_dynamic_request(request: ChoiceRequest) -> None:
    """Reject requests unsuitable for a dynamic Choice decision.

    Fails closed: any high-risk-permission or broad-architecture keyword
    match in the question or option text is rejected, since such judgments
    require Claude's own reasoning and explicit user authority, not a
    probabilistic classification.
    """
    haystack = " ".join(
        [request.question, *(opt.description for opt in request.options)]
    ).lower()

    for keyword in _HIGH_RISK_PERMISSION_KEYWORDS:
        if keyword in haystack:
            raise DynamicRequestRejected(
                f"question references a high-risk/permission action ({keyword!r}); "
                "dynamic Choice must not be used for destructive, deployment, "
                "credential or payment decisions"
            )
    for keyword in _BROAD_ARCHITECTURE_KEYWORDS:
        if keyword in haystack:
            raise DynamicRequestRejected(
                f"question references a broad architecture judgment ({keyword!r}); "
                "dynamic Choice must not be used for system-wide design decisions"
            )
