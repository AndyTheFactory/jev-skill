# jev-skill

General-purpose Jev decision skill for Claude Code, with a standalone Python decision engine.

**Status:** This branch implements only the Python project bootstrap for [issue #1](https://github.com/AndyTheFactory/jev-skill/issues/1). Jev API integration, policies, profiles, and the Claude Code skill are tracked in later issues. The CLI currently provides help and version information, not decision inference.

## Requirements

- Python 3.11 or newer
- pip

## Development setup

```bash
git clone https://github.com/AndyTheFactory/jev-skill.git
cd jev-skill
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
jev --help
jev --version
python -m jev_decisions --help
```

On Windows activate the virtual environment with `.venv\\Scripts\\activate`.

## Checks

```bash
python -m pytest
python -m ruff check .
python -m mypy
```

GitHub Actions runs the same checks on Python 3.11, 3.12 and 3.13.

## Layout

- `src/jev_decisions/`: importable package and CLI entry point. It has no Claude Code dependency.
- `tests/`: bootstrap smoke tests.
- `config/example.yaml`: **nonfunctional sample** of future configuration; the loader is tracked in issue #2.
- `.github/workflows/ci.yml`: Python checks.

No provider credentials are required for issue #1; subsequent issues will implement OpenRouter connectivity and decision execution.
