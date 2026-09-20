"""Exhaustive validation tests for Choice request/response contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jev_decisions.schemas import (
    MAX_CONTEXT_CHARS,
    ChoiceOption,
    ChoiceRequest,
    ProviderChoiceResponse,
    SchemaValidationError,
)


def make_request(n_options: int = 2, **overrides: object) -> ChoiceRequest:
    options = tuple(ChoiceOption(id=f"opt{i}", description=f"Option {i}") for i in range(n_options))
    fields: dict[str, object] = {"question": "Which approach?", "options": options}
    fields.update(overrides)
    return ChoiceRequest.model_validate(fields)


def test_valid_request_round_trips() -> None:
    req = make_request(3)
    assert len(req.options) == 3
    assert req.option_ids() == {"opt0", "opt1", "opt2"}


@pytest.mark.parametrize("n", [0, 1, 9, 12])
def test_option_count_out_of_bounds_rejected(n: int) -> None:
    with pytest.raises(ValidationError, match="2-8 entries"):
        make_request(n)


def test_duplicate_option_ids_rejected() -> None:
    options = (
        ChoiceOption(id="dup", description="a"),
        ChoiceOption(id="dup", description="b"),
    )
    with pytest.raises(ValidationError, match="distinct"):
        ChoiceRequest.model_validate({"question": "Q?", "options": options})


def test_context_over_limit_rejected() -> None:
    with pytest.raises(ValidationError):
        make_request(2, context="x" * (MAX_CONTEXT_CHARS + 1))


def test_context_at_limit_accepted() -> None:
    req = make_request(2, context="x" * MAX_CONTEXT_CHARS)
    assert len(req.context) == MAX_CONTEXT_CHARS


def test_unsupported_schema_version_rejected() -> None:
    with pytest.raises(ValidationError, match="unsupported schema_version"):
        make_request(2, schema_version="99.0")


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        make_request(2, unexpected="oops")


def test_request_fails_before_any_network_call() -> None:
    # Validation happens in the constructor; no transport is involved.
    with pytest.raises(ValidationError):
        make_request(0)


def test_valid_provider_response() -> None:
    resp = ProviderChoiceResponse(
        selected_option_id="opt0",
        probabilities={"opt0": 0.6, "opt1": 0.4},
        confidence=0.9,
    )
    req = make_request(2)
    resp.validate_against_request(req)


def test_response_probability_not_summing_to_one_rejected() -> None:
    with pytest.raises(ValidationError, match="sum to 1.0"):
        ProviderChoiceResponse(
            selected_option_id="opt0",
            probabilities={"opt0": 0.6, "opt1": 0.6},
        )


def test_response_probability_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError, match=r"out of \[0,1\]"):
        ProviderChoiceResponse(
            selected_option_id="opt0",
            probabilities={"opt0": 1.4, "opt1": -0.4},
        )


def test_response_missing_option_in_probabilities_rejected() -> None:
    req = make_request(3)
    resp = ProviderChoiceResponse(
        selected_option_id="opt0",
        probabilities={"opt0": 0.5, "opt1": 0.5},  # missing opt2: incomplete distribution
    )
    with pytest.raises(SchemaValidationError, match="does not match declared"):
        resp.validate_against_request(req)


def test_response_extra_option_in_probabilities_rejected() -> None:
    req = make_request(2)
    resp = ProviderChoiceResponse(
        selected_option_id="opt0",
        probabilities={"opt0": 0.4, "opt1": 0.3, "extra": 0.3},
    )
    with pytest.raises(SchemaValidationError, match="does not match declared"):
        resp.validate_against_request(req)


def test_response_selected_option_not_declared_rejected() -> None:
    req = make_request(2)
    resp = ProviderChoiceResponse(
        selected_option_id="not-declared",
        probabilities={"opt0": 0.5, "opt1": 0.5},
    )
    with pytest.raises(SchemaValidationError, match="not among declared options"):
        resp.validate_against_request(req)


def test_confidence_never_fabricated_when_absent() -> None:
    resp = ProviderChoiceResponse(
        selected_option_id="opt0",
        probabilities={"opt0": 0.6, "opt1": 0.4},
    )
    assert resp.confidence is None
