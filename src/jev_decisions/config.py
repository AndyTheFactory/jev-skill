"""Versioned YAML configuration with explicit precedence and credential handling.

Precedence (highest wins): CLI overrides > environment > project file > user file > defaults.
Trust ceiling: the whole ``execution.*`` section (``mode`` and
``active_profiles``) may only come from a source at or above ``user`` trust
(user file, environment, CLI) -- never from a project file, since project
files may be untrusted repository content. This is deliberately broader than
just ``mode``: a project file that could edit ``active_profiles`` while a
trusted layer separately enables ``mode: active`` would otherwise be able to
widen which profiles act, without ever being trusted to turn active mode on
itself.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSIONS = ("1.0",)

# Precedence (user < project < env < cli, applied via the `layers` list in
# load_config) governs which value wins on conflict. Trust for *enabling*
# active mode is separate: project files are repository content and may be
# untrusted, so they cannot flip execution to active even though they outrank
# user config in ordinary precedence.
_ACTIVE_MODE_TRUSTED_SOURCES = frozenset({"user", "env", "cli"})


class ConfigError(Exception):
    """Raised for missing, invalid or untrusted configuration."""


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["openrouter"] = "openrouter"
    model: str = "openrouter/auto"
    timeout_seconds: float = Field(default=10.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)
    api_key_env: str = "OPENROUTER_API_KEY"


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["shadow", "active"] = "shadow"
    active_profiles: tuple[str, ...] = ()


class TelemetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    include_context: bool = False
    retention_days: int = Field(default=30, ge=1, le=365)
    path: str = "~/.jev/telemetry.jsonl"


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_probability: float = Field(default=0.80, gt=0, le=1)
    margin: float = Field(default=0.20, ge=0, le=1)
    min_confidence: float = Field(default=0.70, ge=0, le=1)


class JevConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)

    def get_api_key(self) -> str | None:
        """Read the provider credential from the environment only. Never logged."""
        return os.environ.get(self.provider.api_key_env) or None


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config file {path} must contain a mapping at top level")
    return raw


def _env_overrides() -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    mode = os.environ.get("JEV_EXECUTION_MODE")
    if mode is not None:
        overrides.setdefault("execution", {})["mode"] = mode
    active_profiles = os.environ.get("JEV_EXECUTION_ACTIVE_PROFILES")
    if active_profiles is not None:
        overrides.setdefault("execution", {})["active_profiles"] = [
            p.strip() for p in active_profiles.split(",") if p.strip()
        ]
    model = os.environ.get("JEV_PROVIDER_MODEL")
    if model is not None:
        overrides.setdefault("provider", {})["model"] = model
    return overrides


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _requested_mode(layer: dict[str, Any]) -> str | None:
    execution = layer.get("execution")
    if isinstance(execution, dict):
        mode = execution.get("mode")
        if isinstance(mode, str):
            return mode
    return None


def _requests_active_profiles(layer: dict[str, Any]) -> bool:
    execution = layer.get("execution")
    return isinstance(execution, dict) and "active_profiles" in execution


def load_config(
    *,
    user_path: Path | None = None,
    project_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> JevConfig:
    """Load configuration honoring precedence and the active-mode trust ceiling.

    Any layer below ``user`` trust that requests ``execution.mode: active`` is
    rejected with :class:`ConfigError` rather than silently downgraded, so a
    tampered project file cannot activate execution.
    """
    user_path = user_path or Path.home() / ".jev" / "config.yaml"
    project_path = project_path or Path.cwd() / ".jev.yaml"
    cli_overrides = cli_overrides or {}

    layers: list[tuple[str, dict[str, Any]]] = [
        ("user", _load_yaml_file(user_path)),
        ("project", _load_yaml_file(project_path)),
        ("env", _env_overrides()),
        ("cli", cli_overrides),
    ]

    for trust, layer in layers:
        requested = _requested_mode(layer)
        if requested == "active" and trust not in _ACTIVE_MODE_TRUSTED_SOURCES:
            raise ConfigError(
                f"execution.mode=active cannot be set from {trust} config; "
                f"trusted sources are {sorted(_ACTIVE_MODE_TRUSTED_SOURCES)}"
            )
        if _requests_active_profiles(layer) and trust not in _ACTIVE_MODE_TRUSTED_SOURCES:
            raise ConfigError(
                f"execution.active_profiles cannot be set from {trust} config; "
                f"trusted sources are {sorted(_ACTIVE_MODE_TRUSTED_SOURCES)} "
                "(a project file could otherwise widen which profiles act even "
                "without being trusted to enable active mode itself)"
            )

    merged: dict[str, Any] = {}
    for _trust, layer in layers:
        merged = _deep_merge(merged, layer)

    if merged.get("schema_version", "1.0") not in SCHEMA_VERSIONS:
        raise ConfigError(
            f"unsupported schema_version {merged.get('schema_version')!r}; "
            f"supported: {SCHEMA_VERSIONS}"
        )

    try:
        return JevConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration: {exc}") from exc
