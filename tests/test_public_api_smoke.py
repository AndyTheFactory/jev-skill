"""M5 #22: public API surface has zero Claude Code coupling, and a clean
(built-wheel, --no-deps) install actually works end to end.
"""

from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
import venv
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent

_FORBIDDEN_SUBSTRINGS = ("claude_code", "claude-code", "anthropic_cli")


def test_package_source_has_no_claude_code_coupling() -> None:
    for path in sorted((REPO_ROOT / "src" / "jev_decisions").rglob("*.py")):
        text = path.read_text().lower()
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in text, f"{path} references {forbidden!r}"


def test_pyproject_dependencies_have_no_claude_code_package() -> None:
    text = (REPO_ROOT / "pyproject.toml").read_text().lower()
    for forbidden in _FORBIDDEN_SUBSTRINGS:
        assert forbidden not in text


def test_external_consumer_uses_only_documented_public_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly the example from docs/public-api.md: no CLI, no subprocess,
    no network -- pure library usage an external Python project would do.
    """
    from jev_decisions.config import JevConfig
    from jev_decisions.policy import evaluate
    from jev_decisions.schemas import ChoiceOption, ChoiceRequest, ProviderChoiceResponse

    request = ChoiceRequest(
        question="Which caching strategy fits?",
        options=(
            ChoiceOption(id="lru", description="In-process LRU cache."),
            ChoiceOption(id="redis", description="Shared Redis cache."),
        ),
    )
    response = ProviderChoiceResponse(
        selected_option_id="lru", probabilities={"lru": 0.9, "redis": 0.1}
    )
    decision = evaluate(request, response, config=JevConfig().policy)
    assert decision.outcome == "accepted"
    assert decision.selected_option_id == "lru"


def test_documented_public_symbols_are_importable() -> None:
    # If docs/public-api.md names it, it must actually exist -- catches
    # doc/code drift directly rather than by inspection.
    from jev_decisions.baseline import Baseline, BaselineError, load_baseline  # noqa: F401
    from jev_decisions.config import (  # noqa: F401
        ConfigError,
        ExecutionConfig,
        JevConfig,
        PolicyConfig,
        ProviderConfig,
        TelemetryConfig,
        load_config,
    )
    from jev_decisions.dynamic import (  # noqa: F401
        DynamicRequestRejected,
        build_dynamic_request,
        validate_dynamic_request,
    )
    from jev_decisions.engine import (  # noqa: F401
        ActionPermission,
        AdvisoryResult,
        ShadowResult,
        run_decision,
        run_shadow,
    )
    from jev_decisions.policy import Decision, DecisionOutcome, evaluate  # noqa: F401
    from jev_decisions.profiles import (  # noqa: F401
        DEFAULT_PROFILES_DIR,
        Profile,
        ProfileError,
        ProfileRegistry,
        load_registry,
    )
    from jev_decisions.schemas import (  # noqa: F401
        MAX_CONTEXT_CHARS,
        MAX_OPTIONS,
        MIN_OPTIONS,
        SCHEMA_VERSIONS,
        ChoiceOption,
        ChoiceRequest,
        DecisionError,
        ProviderChoiceResponse,
        SchemaValidationError,
    )
    from jev_decisions.store import (  # noqa: F401
        InvalidRecordIdError,
        RecordNotFoundError,
        load,
    )


@pytest.mark.skipif(os.name != "posix", reason="uses a POSIX venv layout")
def test_clean_wheel_install_smoke(tmp_path: Path) -> None:
    """Build a real wheel, install it (--no-deps, into a venv that reuses
    this environment's already-installed dependencies) and run against it
    -- not the editable dev install, an actual clean install.
    """
    dist_dir = tmp_path / "dist"
    build = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert build.returncode == 0, build.stderr

    wheels = list(dist_dir.glob("*.whl"))
    assert len(wheels) == 1, wheels

    venv_dir = tmp_path / "venv"
    venv.create(venv_dir, with_pip=True)
    venv_python = venv_dir / "bin" / "python"

    # `system_site_packages=True` would inherit this *process's* base
    # interpreter, not the dev virtualenv actually running these tests (we
    # are almost certainly already inside one). Point the new venv at this
    # interpreter's site-packages directly instead, so it can import the
    # runtime deps (pydantic, pyyaml, httpx) already installed there
    # without any network access.
    new_site_packages = subprocess.run(
        [str(venv_python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    dev_site_packages = sysconfig.get_paths()["purelib"]
    (Path(new_site_packages) / "_dev_env.pth").write_text(dev_site_packages + "\n")

    install = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--no-deps", "--no-index", str(wheels[0])],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert install.returncode == 0, install.stderr

    check = subprocess.run(
        [
            str(venv_python),
            "-c",
            "from jev_decisions.config import JevConfig\n"
            "from jev_decisions.policy import evaluate\n"
            "from jev_decisions.schemas import ChoiceOption, ChoiceRequest\n"
            "from jev_decisions.schemas import ProviderChoiceResponse\n"
            "req = ChoiceRequest(question='Q?', options=("
            "ChoiceOption(id='a', description='A'), ChoiceOption(id='b', description='B')))\n"
            "resp = ProviderChoiceResponse(\n"
            "    selected_option_id='a', probabilities={'a': 0.9, 'b': 0.1}\n"
            ")\n"
            "decision = evaluate(req, resp, config=JevConfig().policy)\n"
            "assert decision.outcome == 'accepted'\n"
            "print('smoke-ok')\n",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert check.returncode == 0, check.stderr
    assert "smoke-ok" in check.stdout

    version_check = subprocess.run(
        [str(venv_dir / "bin" / "jev"), "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert version_check.returncode == 0, version_check.stderr
    assert version_check.stdout.startswith("jev ")
