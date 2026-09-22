"""End-to-end mocked tests for `jev decide`, `jev reveal` and `jev doctor`."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import pytest

from jev_decisions import cli, engine
from jev_decisions.provider.openrouter import ProviderError
from jev_decisions.schemas import ChoiceRequest, DecisionError, ProviderChoiceResponse

REQUEST = {
    "question": "Which approach?",
    "options": [
        {"id": "a", "description": "Approach A"},
        {"id": "b", "description": "Approach B"},
    ],
}


class _StubAdapter:
    def __init__(self, outcome: str) -> None:
        self._outcome = outcome

    def __enter__(self) -> _StubAdapter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
        if self._outcome == "accepted":
            return ProviderChoiceResponse(
                selected_option_id="a", probabilities={"a": 0.9, "b": 0.1}, confidence=0.95
            )
        if self._outcome == "abstained":
            return ProviderChoiceResponse(
                selected_option_id="a", probabilities={"a": 0.5, "b": 0.5}
            )
        if self._outcome == "failed":
            raise ProviderError(DecisionError(code="timeout", message="timed out"))
        raise AssertionError(self._outcome)


def _patch_adapter(monkeypatch: pytest.MonkeyPatch, factory: Any) -> None:
    monkeypatch.setattr(cli, "OpenRouterAdapter", factory)
    monkeypatch.setattr(engine, "OpenRouterAdapter", factory)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("JEV_EXECUTION_MODE", raising=False)


def _write_request(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload))
    return path


def test_decide_accepted_exit_zero_reveals_no_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_adapter(monkeypatch, lambda config: _StubAdapter("accepted"))
    req_file = _write_request(tmp_path / "req.json", REQUEST)
    code = cli.main(["decide", "--input", str(req_file)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["outcome"] == "accepted"
    assert out["action"]["permitted"] is False
    assert "record_id" in out
    assert set(out) == {"record_id", "outcome", "action"}  # never selected/probability/reason


def test_decide_abstained_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_adapter(monkeypatch, lambda config: _StubAdapter("abstained"))
    req_file = _write_request(tmp_path / "req.json", REQUEST)
    code = cli.main(["decide", "--input", str(req_file)])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["outcome"] == "abstained"


def test_decide_provider_failure_exit_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_adapter(monkeypatch, lambda config: _StubAdapter("failed"))
    req_file = _write_request(tmp_path / "req.json", REQUEST)
    code = cli.main(["decide", "--input", str(req_file)])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["outcome"] == "failed"


def test_decide_invalid_request_exit_65(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = {"question": "Q?", "options": [{"id": "only-one", "description": "x"}]}
    req_file = _write_request(tmp_path / "req.json", bad)
    code = cli.main(["decide", "--input", str(req_file)])
    assert code == cli.EX_DATAERR
    assert "invalid request" in capsys.readouterr().err


def test_decide_missing_file_exit_65(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["decide", "--input", "/nonexistent/path.json"])
    assert code == cli.EX_DATAERR


def test_decide_non_object_json_exit_65(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    req_file = tmp_path / "req.json"
    req_file.write_text("[1, 2, 3]")
    code = cli.main(["decide", "--input", str(req_file)])
    assert code == cli.EX_DATAERR
    assert "invalid request" in capsys.readouterr().err


def test_decide_reads_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_adapter(monkeypatch, lambda config: _StubAdapter("accepted"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(REQUEST)))
    code = cli.main(["decide", "--stdin"])
    assert code == 0


def test_decide_requires_input_or_stdin() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["decide"])
    assert exc.value.code == 2  # argparse usage error


def test_reveal_shows_full_protected_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_adapter(monkeypatch, lambda config: _StubAdapter("accepted"))
    req_file = _write_request(tmp_path / "req.json", REQUEST)
    cli.main(["decide", "--input", str(req_file)])
    record_id = json.loads(capsys.readouterr().out)["record_id"]

    code = cli.main(["reveal", record_id])
    assert code == 0
    revealed = json.loads(capsys.readouterr().out)
    assert revealed["selected_option_id"] == "a"
    assert revealed["probability"] == pytest.approx(0.9)


def test_decide_with_baseline_file_shows_in_reveal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_adapter(monkeypatch, lambda config: _StubAdapter("accepted"))
    req_file = _write_request(tmp_path / "req.json", REQUEST)
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(
        json.dumps(
            {
                "action": "reproduce the bug first",
                "recorded_at": "2020-01-01T00:00:00Z",
            }
        )
    )
    code = cli.main(
        ["decide", "--input", str(req_file), "--baseline-file", str(baseline_file)]
    )
    assert code == 0
    record_id = json.loads(capsys.readouterr().out)["record_id"]

    cli.main(["reveal", record_id])
    revealed = json.loads(capsys.readouterr().out)
    assert revealed["baseline"]["action"] == "reproduce the bug first"


def test_decide_with_invalid_baseline_file_exit_65(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_adapter(monkeypatch, lambda config: _StubAdapter("accepted"))
    req_file = _write_request(tmp_path / "req.json", REQUEST)
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(json.dumps({"action": "x", "recorded_at": "2999-01-01T00:00:00Z"}))
    code = cli.main(
        ["decide", "--input", str(req_file), "--baseline-file", str(baseline_file)]
    )
    assert code == cli.EX_DATAERR
    assert "future" in capsys.readouterr().err


def test_reveal_without_baseline_shows_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_adapter(monkeypatch, lambda config: _StubAdapter("accepted"))
    req_file = _write_request(tmp_path / "req.json", REQUEST)
    cli.main(["decide", "--input", str(req_file)])
    record_id = json.loads(capsys.readouterr().out)["record_id"]
    cli.main(["reveal", record_id])
    assert json.loads(capsys.readouterr().out)["baseline"] is None


def test_decide_active_mode_enabled_profile_exposes_selected_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    user_config = tmp_path / ".jev" / "config.yaml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("execution:\n  mode: active\n  active_profiles: [task-routing]\n")

    class _ProfileAdapter(_StubAdapter):
        def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
            option_ids = [o.id for o in request.options]
            probs = {oid: 0.0 for oid in option_ids}
            probs[option_ids[0]] = 1.0
            return ProviderChoiceResponse(selected_option_id=option_ids[0], probabilities=probs)

    _patch_adapter(monkeypatch, lambda config: _ProfileAdapter("accepted"))
    payload = {"profile": "task-routing", "context": "x"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = cli.main(["decide", "--stdin"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert "selected_option_id" in out
    assert out["action"]["permitted"] is False


def test_decide_active_mode_without_enabled_profile_stays_shadow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    user_config = tmp_path / ".jev" / "config.yaml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("execution:\n  mode: active\n  active_profiles: [review-triage]\n")

    _patch_adapter(monkeypatch, lambda config: _StubAdapter("accepted"))
    req_file = _write_request(tmp_path / "req.json", REQUEST)
    code = cli.main(["decide", "--input", str(req_file)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert "selected_option_id" not in out


def test_reveal_unknown_record_id_exit_65(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["reveal", "0" * 32])
    assert code == cli.EX_DATAERR


def test_reveal_corrupt_record_exit_65_not_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record_id = "1" * 32
    shadow_dir = tmp_path / ".jev" / "shadow"
    shadow_dir.mkdir(parents=True)
    (shadow_dir / f"{record_id}.json").write_text("{not valid json")
    code = cli.main(["reveal", record_id])
    assert code == cli.EX_DATAERR
    assert "corrupt" in capsys.readouterr().err


def test_reveal_rejects_path_traversal(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["reveal", "../../../../etc/passwd"])
    assert code == cli.EX_DATAERR
    assert "malformed record id" in capsys.readouterr().err


def test_doctor_reports_config_and_credential(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["doctor"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["config"] == "ok"
    assert report["credential_present"] is True
    assert report["execution_mode"] == "shadow"


def test_doctor_loads_explicit_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENROUTER_API_KEY=from-file\n"
        "JEV_EXECUTION_MODE=active\n"
        "JEV_EXECUTION_ACTIVE_PROFILES=task-routing\n"
    )

    code = cli.main(["--env-file", str(env_file), "doctor"])

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["credential_present"] is True
    assert report["execution_mode"] == "active"
    assert report["active_profiles"] == ["task-routing"]
    assert "from-file" not in json.dumps(report)
    assert "OPENROUTER_API_KEY" not in os.environ


def test_existing_environment_overrides_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("JEV_PROVIDER_MODEL", "environment-model")
    env_file = tmp_path / ".env"
    env_file.write_text("JEV_PROVIDER_MODEL=file-model\n")

    code = cli.main(["--env-file", str(env_file), "doctor"])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["model"] == "environment-model"


def test_missing_env_file_exits_65(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.env"
    code = cli.main(["--env-file", str(missing), "doctor"])
    assert code == cli.EX_DATAERR
    assert str(missing) in capsys.readouterr().err


def test_doctor_warns_on_unknown_active_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    user_config = tmp_path / ".jev" / "config.yaml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text(
        "execution:\n  mode: active\n  active_profiles: [task_routing]\n"  # typo: underscore
    )
    code = cli.main(["doctor"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert "task_routing" in report["active_profiles_warning"]


def test_doctor_no_warning_for_known_active_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    user_config = tmp_path / ".jev" / "config.yaml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("execution:\n  mode: active\n  active_profiles: [task-routing]\n")
    code = cli.main(["doctor"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert "active_profiles_warning" not in report


def test_doctor_never_leaks_credential(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["doctor"])
    assert code == 0
    assert "test-key" not in capsys.readouterr().out


def test_doctor_check_provider_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "OpenRouterAdapter", lambda config: _StubAdapter("accepted"))
    code = cli.main(["doctor", "--check-provider"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["provider_connectivity"] == "ok"


def test_doctor_check_provider_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "OpenRouterAdapter", lambda config: _StubAdapter("failed"))
    code = cli.main(["doctor", "--check-provider"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["provider_connectivity"] == "error: timeout"


def test_profile_list(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["profile", "list"])
    assert code == 0
    ids = json.loads(capsys.readouterr().out)
    assert "task-routing" in ids


def test_profile_show(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["profile", "show", "task-routing"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["id"] == "task-routing"
    assert len(data["options"]) >= 2


def test_profile_show_unknown_exit_65(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["profile", "show", "does-not-exist"])
    assert code == cli.EX_DATAERR


def test_decide_with_profile_resolves_question_and_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, ChoiceRequest] = {}

    class _CapturingAdapter(_StubAdapter):
        def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
            captured["request"] = request
            option_ids = [o.id for o in request.options]
            probs = {oid: 0.0 for oid in option_ids}
            probs[option_ids[0]] = 1.0
            return ProviderChoiceResponse(selected_option_id=option_ids[0], probabilities=probs)

    _patch_adapter(monkeypatch, lambda config: _CapturingAdapter("accepted"))
    payload = {"profile": "task-routing", "context": "add a new CLI flag"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = cli.main(["decide", "--stdin"])
    assert code in (0, 1)
    assert captured["request"].question
    assert len(captured["request"].options) >= 2


def test_decide_non_string_profile_field_exit_65(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"profile": ["a", "b"]})))
    code = cli.main(["decide", "--stdin"])
    assert code == cli.EX_DATAERR
    assert "profile must be a string" in capsys.readouterr().err


def test_decide_explicit_question_with_profile_not_overridden(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, ChoiceRequest] = {}

    class _CapturingAdapter(_StubAdapter):
        def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
            captured["request"] = request
            return ProviderChoiceResponse(
                selected_option_id="a", probabilities={"a": 1.0, "b": 0.0}
            )

    _patch_adapter(monkeypatch, lambda config: _CapturingAdapter("accepted"))
    payload = {"profile": "task-routing", **REQUEST}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = cli.main(["decide", "--stdin"])
    assert code in (0, 1)
    assert captured["request"].question == REQUEST["question"]
    # The profile label is unverified against this self-authored content,
    # so it must not survive onto the request: active-mode gating trusts
    # request.profile completely and would otherwise treat arbitrary
    # caller-supplied content as if it came from the vetted profile.
    assert captured["request"].profile is None


def test_doctor_check_provider_without_credential(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    code = cli.main(["doctor", "--check-provider"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["provider_connectivity"] == "skipped: no credential"


def _enable_active(monkeypatch: pytest.MonkeyPatch, profiles: str) -> None:
    monkeypatch.setenv("JEV_EXECUTION_MODE", "active")
    monkeypatch.setenv("JEV_EXECUTION_ACTIVE_PROFILES", profiles)


def test_dynamic_profile_active_shows_recommendation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _enable_active(monkeypatch, "dynamic")
    _patch_adapter(monkeypatch, lambda config: _StubAdapter("accepted"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"profile": "dynamic", **REQUEST})))
    assert cli.main(["decide", "--stdin"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["selected_option_id"] == "a"
    assert out["action"]["permitted"] is False


def test_dynamic_profile_not_listed_stays_shadow(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _enable_active(monkeypatch, "task-routing")
    _patch_adapter(monkeypatch, lambda config: _StubAdapter("accepted"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"profile": "dynamic", **REQUEST})))
    assert cli.main(["decide", "--stdin"]) == 0
    assert set(json.loads(capsys.readouterr().out)) == {"record_id", "outcome", "action"}


def test_other_profile_label_on_own_content_stays_shadow_even_if_active(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _enable_active(monkeypatch, "task-routing,dynamic")
    _patch_adapter(monkeypatch, lambda config: _StubAdapter("accepted"))
    payload = {"profile": "task-routing", **REQUEST}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert cli.main(["decide", "--stdin"]) == 0
    assert "selected_option_id" not in json.loads(capsys.readouterr().out)


def test_dynamic_profile_rejects_high_risk_question(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {"profile": "dynamic", **REQUEST, "question": "Should I force push to main?"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert cli.main(["decide", "--stdin"]) == cli.EX_DATAERR
    assert "high-risk" in capsys.readouterr().err


def test_dynamic_profile_requires_question_and_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"profile": "dynamic"})))
    assert cli.main(["decide", "--stdin"]) == cli.EX_DATAERR
    assert "requires its own question and options" in capsys.readouterr().err


def test_dynamic_profile_uncertain_option_abstains(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _PicksUnknown(_StubAdapter):
        def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse:
            return ProviderChoiceResponse(
                selected_option_id="insufficient_context",
                probabilities={"a": 0.0, "b": 0.0, "insufficient_context": 1.0},
            )

    _enable_active(monkeypatch, "dynamic")
    _patch_adapter(monkeypatch, lambda config: _PicksUnknown("accepted"))
    options = [*REQUEST["options"], {"id": "insufficient_context", "description": "Unsure"}]
    payload = {"profile": "dynamic", "question": REQUEST["question"], "options": options}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert cli.main(["decide", "--stdin"]) == 1
    assert json.loads(capsys.readouterr().out)["outcome"] == "abstained"


def test_profile_list_and_show_include_dynamic(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["profile", "list"]) == 0
    assert "dynamic" in json.loads(capsys.readouterr().out)
    assert cli.main(["profile", "show", "dynamic"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "dynamic"
