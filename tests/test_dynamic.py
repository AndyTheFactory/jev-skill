"""Scenario tests for dynamic Choice formulation and validation."""

from __future__ import annotations

import pytest

from jev_decisions.dynamic import (
    DynamicRequestRejected,
    build_dynamic_request,
    validate_dynamic_request,
)
from jev_decisions.schemas import MAX_OPTIONS, ChoiceOption, ChoiceRequest


def test_valid_dynamic_question_accepted() -> None:
    request = build_dynamic_request(
        "Which caching strategy fits this endpoint?",
        [
            ChoiceOption(id="lru", description="In-process LRU cache."),
            ChoiceOption(id="redis", description="Shared Redis cache."),
        ],
        context="Endpoint is read-heavy, single-process deployment.",
    )
    assert request.question.startswith("Which caching")
    ids = {o.id for o in request.options}
    assert "insufficient_context" in ids


def test_uncertain_option_not_duplicated_when_already_present() -> None:
    request = build_dynamic_request(
        "Which?",
        [
            ChoiceOption(id="a", description="A"),
            ChoiceOption(id="unknown", description="Not enough info."),
        ],
    )
    ids = [o.id for o in request.options]
    assert ids.count("unknown") == 1


def test_can_disable_uncertain_option() -> None:
    request = build_dynamic_request(
        "Which?",
        [ChoiceOption(id="a", description="A"), ChoiceOption(id="b", description="B")],
        include_uncertain_option=False,
    )
    ids = {o.id for o in request.options}
    assert ids == {"a", "b"}


@pytest.mark.parametrize(
    "question",
    [
        "Should we delete the staging database now?",
        "Which credentials should be used to deploy to production?",
        "Is it safe to force push over main?",
    ],
)
def test_high_risk_permission_question_rejected(question: str) -> None:
    with pytest.raises(DynamicRequestRejected, match="high-risk"):
        build_dynamic_request(
            question,
            [ChoiceOption(id="a", description="A"), ChoiceOption(id="b", description="B")],
            include_uncertain_option=False,
        )


def test_high_risk_keyword_in_option_description_rejected() -> None:
    with pytest.raises(DynamicRequestRejected, match="high-risk"):
        build_dynamic_request(
            "Which option should we take?",
            [
                ChoiceOption(id="a", description="Delete the old backups."),
                ChoiceOption(id="b", description="Keep everything."),
            ],
            include_uncertain_option=False,
        )


def test_broad_architecture_question_rejected() -> None:
    with pytest.raises(DynamicRequestRejected, match="architecture"):
        build_dynamic_request(
            "Should we redesign the architecture of the billing system?",
            [ChoiceOption(id="a", description="A"), ChoiceOption(id="b", description="B")],
            include_uncertain_option=False,
        )


def test_valid_request_passes_direct_validation() -> None:
    request = ChoiceRequest(
        question="Which log level fits this message?",
        options=(
            ChoiceOption(id="warn", description="Recoverable, needs attention."),
            ChoiceOption(id="error", description="Operation failed."),
        ),
    )
    validate_dynamic_request(request)  # does not raise


def test_uncertain_option_not_added_past_max_options() -> None:
    options = [ChoiceOption(id=f"o{i}", description=f"Option {i}") for i in range(MAX_OPTIONS)]
    with pytest.raises(DynamicRequestRejected, match="already at the"):
        build_dynamic_request("Which?", options)
