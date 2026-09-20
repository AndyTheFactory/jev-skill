"""M5 #23: CC-Enrutador integration is opt-in, forwards a bounded subset of
state, and is never imported by any default runtime path."""

from __future__ import annotations

from pathlib import Path

import pytest

from jev_decisions.integrations.cc_enrutador import (
    RouterState,
    RouterStateError,
    map_router_state_to_choice_request,
)

REPO_ROOT = Path(__file__).parent.parent


def test_maps_candidate_actions_to_options() -> None:
    state: RouterState = {
        "task_id": "router-task-1",
        "current_step": "Which fix approach should the agent take?",
        "candidate_actions": [
            {"id": "patch", "description": "Apply a minimal patch."},
            {"id": "rewrite", "description": "Rewrite the affected module."},
        ],
        "context": "Test failure: off-by-one in pagination.",
    }
    request = map_router_state_to_choice_request(state)
    assert request.question == state["current_step"]
    assert {o.id for o in request.options} == {"patch", "rewrite"}
    assert request.context == state["context"]


def test_forwards_only_documented_fields_not_arbitrary_state() -> None:
    state: RouterState = {
        "current_step": "Which?",
        "candidate_actions": [
            {"id": "a", "description": "A"},
            {"id": "b", "description": "B"},
        ],
        "context": "safe context",
    }
    state_with_extra_fields = {
        **state,
        "credentials": {"api_key": "sk-should-never-be-forwarded"},
        "unrelated_task_history": ["other user's private task"],
    }
    request = map_router_state_to_choice_request(state_with_extra_fields)  # type: ignore[arg-type]
    dumped = request.model_dump_json()
    assert "sk-should-never-be-forwarded" not in dumped
    assert "other user's private task" not in dumped


def test_missing_candidate_actions_raises_router_state_error() -> None:
    state: RouterState = {"current_step": "Which?"}
    with pytest.raises(RouterStateError):
        map_router_state_to_choice_request(state)


def test_malformed_candidate_action_raises_router_state_error() -> None:
    malformed = {
        "current_step": "Which?",
        "candidate_actions": [{"id": "a"}],  # missing description
    }
    with pytest.raises(RouterStateError):
        map_router_state_to_choice_request(malformed)  # type: ignore[arg-type]


def test_empty_candidate_id_raises_router_state_error_not_validation_error() -> None:
    state: RouterState = {
        "current_step": "Which?",
        "candidate_actions": [
            {"id": "", "description": "x"},  # ChoiceOption requires min_length=1
            {"id": "b", "description": "B"},
        ],
    }
    with pytest.raises(RouterStateError):
        map_router_state_to_choice_request(state)


def test_too_few_candidate_actions_raises_router_state_error() -> None:
    state: RouterState = {
        "current_step": "Which?",
        "candidate_actions": [{"id": "a", "description": "A"}],  # ChoiceRequest needs >= 2
    }
    with pytest.raises(RouterStateError):
        map_router_state_to_choice_request(state)


def test_profile_can_be_attached() -> None:
    state: RouterState = {
        "current_step": "Which?",
        "candidate_actions": [
            {"id": "a", "description": "A"},
            {"id": "b", "description": "B"},
        ],
    }
    request = map_router_state_to_choice_request(state, profile="task-routing")
    assert request.profile == "task-routing"


def test_integration_module_never_imported_by_default_runtime_path() -> None:
    default_runtime_files = [
        REPO_ROOT / "src" / "jev_decisions" / "cli.py",
        REPO_ROOT / "src" / "jev_decisions" / "engine.py",
        REPO_ROOT / "src" / "jev_decisions" / "__init__.py",
        REPO_ROOT / "src" / "jev_decisions" / "__main__.py",
    ]
    for path in default_runtime_files:
        assert "integrations" not in path.read_text(), f"{path} references integrations"
    skill_md = REPO_ROOT / "skill" / "jev-decisions" / "SKILL.md"
    assert "cc_enrutador" not in skill_md.read_text().lower()


def test_no_cc_enrutador_dependency_in_pyproject() -> None:
    text = (REPO_ROOT / "pyproject.toml").read_text().lower()
    assert "cc-enrutador" not in text
    assert "cc_enrutador" not in text
