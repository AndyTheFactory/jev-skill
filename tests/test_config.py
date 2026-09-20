"""Tests for config precedence, defaults, unknown fields and the active-mode ceiling."""

from __future__ import annotations

from pathlib import Path

import pytest

from jev_decisions.config import ConfigError, load_config


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_defaults_when_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_config(
        user_path=tmp_path / "missing-user.yaml",
        project_path=tmp_path / "missing-project.yaml",
    )
    assert cfg.execution.mode == "shadow"
    assert cfg.provider.name == "openrouter"
    assert cfg.provider.timeout_seconds == 10.0


def test_project_overrides_user(tmp_path: Path) -> None:
    user = write(tmp_path / "user.yaml", "provider:\n  model: user-model\n")
    project = write(tmp_path / "project.yaml", "provider:\n  model: project-model\n")
    cfg = load_config(user_path=user, project_path=project)
    assert cfg.provider.model == "project-model"


def test_env_overrides_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = write(tmp_path / "project.yaml", "provider:\n  model: project-model\n")
    monkeypatch.setenv("JEV_PROVIDER_MODEL", "env-model")
    cfg = load_config(user_path=tmp_path / "none.yaml", project_path=project)
    assert cfg.provider.model == "env-model"


def test_cli_overrides_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = write(tmp_path / "project.yaml", "provider:\n  model: project-model\n")
    monkeypatch.setenv("JEV_PROVIDER_MODEL", "env-model")
    cfg = load_config(
        user_path=tmp_path / "none.yaml",
        project_path=project,
        cli_overrides={"provider": {"model": "cli-model"}},
    )
    assert cfg.provider.model == "cli-model"


def test_unknown_field_rejected(tmp_path: Path) -> None:
    user = write(tmp_path / "user.yaml", "unexpected_field: 1\n")
    with pytest.raises(ConfigError):
        load_config(user_path=user, project_path=tmp_path / "none.yaml")


def test_invalid_yaml_produces_actionable_error(tmp_path: Path) -> None:
    user = write(tmp_path / "user.yaml", "provider: [unterminated\n")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(user_path=user, project_path=tmp_path / "none.yaml")


def test_project_cannot_enable_active_mode(tmp_path: Path) -> None:
    project = write(tmp_path / "project.yaml", "execution:\n  mode: active\n")
    with pytest.raises(ConfigError, match="cannot be set from project"):
        load_config(user_path=tmp_path / "none.yaml", project_path=project)


def test_user_config_can_enable_active_mode(tmp_path: Path) -> None:
    user = write(tmp_path / "user.yaml", "execution:\n  mode: active\n")
    cfg = load_config(user_path=user, project_path=tmp_path / "none.yaml")
    assert cfg.execution.mode == "active"


def test_env_can_enable_active_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_EXECUTION_MODE", "active")
    cfg = load_config(user_path=tmp_path / "none.yaml", project_path=tmp_path / "none2.yaml")
    assert cfg.execution.mode == "active"


def test_project_active_mode_rejected_even_if_env_shadow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = write(tmp_path / "project.yaml", "execution:\n  mode: active\n")
    monkeypatch.delenv("JEV_EXECUTION_MODE", raising=False)
    with pytest.raises(ConfigError):
        load_config(user_path=tmp_path / "none.yaml", project_path=project)


def test_unsupported_schema_version_rejected(tmp_path: Path) -> None:
    user = write(tmp_path / "user.yaml", "schema_version: '99.0'\n")
    with pytest.raises(ConfigError, match="unsupported schema_version"):
        load_config(user_path=user, project_path=tmp_path / "none.yaml")


def test_api_key_from_env_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = load_config(user_path=tmp_path / "none.yaml", project_path=tmp_path / "none2.yaml")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert cfg.get_api_key() is None
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
    assert cfg.get_api_key() == "secret-key"


def test_secret_never_in_repr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "super-secret-value")
    cfg = load_config(user_path=tmp_path / "none.yaml", project_path=tmp_path / "none2.yaml")
    assert "super-secret-value" not in repr(cfg)
    assert "super-secret-value" not in str(cfg.model_dump())
