# jev-skill

A standalone Python decision engine ("Jev"), plus a Claude Code skill that
consults it for bounded, ambiguous either/or/few-way decisions. The engine
has no Claude Code dependency and can be used from any Python project or
shell.

**Default behavior is inert unless configured.** With no config and no
`OPENROUTER_API_KEY`, `jev doctor` reports the missing credential and
`jev decide` fails closed (`failed` outcome, non-zero exit) -- nothing about
using this project requires network access or a provider account.

## Requirements

- Python 3.11 or newer
- pip
- An [OpenRouter](https://openrouter.ai) API key, only if you want live
  decisions rather than just the CLI/library scaffolding

## Install

```bash
git clone https://github.com/AndyTheFactory/jev-skill.git
cd jev-skill
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
jev --help
jev --version
```

`jev` is installed as a console script by `pip`, so once the virtualenv is
active it works from **any** working directory, not just this repo:

```bash
cd /tmp && jev --version   # works from anywhere the venv is active
```

## Configuration

Set your credential in the environment -- never in a config file:

```bash
export OPENROUTER_API_KEY="sk-..."
```

Everything else is optional. Config loads with this precedence (highest
wins): CLI overrides > environment (`JEV_*`) > project file (`./.jev.yaml`)
> user file (`~/.jev/config.yaml`) > built-in defaults. See
`config/example.yaml` for the schema.

Execution defaults to `shadow` mode and stays there unless you explicitly
opt in at the user-config or environment level -- a project-committed
`.jev.yaml` cannot enable `active` mode, since project files may be
untrusted repository content. The same restriction applies to
`execution.active_profiles`: a project file cannot list a profile there
either, even if a trusted layer has separately enabled `active` mode --
otherwise a project file could widen which profiles act without ever being
trusted to turn active mode on.

### Active mode (opt-in, per profile)

```yaml
# ~/.jev/config.yaml (or set via JEV_EXECUTION_MODE / JEV_EXECUTION_ACTIVE_PROFILES)
execution:
  mode: active
  active_profiles: [task-routing]
```

Only for a request using an explicitly listed profile, and only on an
`accepted` outcome, `jev decide` additionally includes `selected_option_id`
and `probability` in its output. `action.permitted` is still always
`false` -- this makes the recommendation visible as one more input for
Claude's own reasoning, never an authorization. Every other case (shadow
mode, a profile not listed, or any non-`accepted` outcome) is unaffected
and behaves exactly as documented above. See `eval/BENCHMARK.md` for the
evidence a profile should have behind it before this is turned on.

## Usage

### Diagnose your setup

```bash
jev doctor                    # config validity, execution mode, credential presence
jev doctor --check-provider   # also attempts a live, minimal provider call
```

`jev doctor` never prints your API key, only whether one is configured.

### Manual decision, ad-hoc

```bash
cat <<'JSON' | jev decide --stdin
{
  "question": "Which caching strategy fits this endpoint?",
  "options": [
    {"id": "lru", "description": "In-process LRU cache."},
    {"id": "redis", "description": "Shared Redis cache."}
  ],
  "context": "Endpoint is read-heavy, single-process deployment."
}
JSON
```

Or from a file: `jev decide --input request.json`.

`jev decide` runs in shadow mode by default: it prints only `{record_id,
outcome, action: {permitted: false}}` to stdout -- never the selected
option, probability, confidence or reasoning -- plus the outcome to stderr.
It exits 0 (accepted), 1 (abstained), 2 (failed/provider unavailable), 3
(rejected/malformed), or 65 (invalid input, e.g. bad JSON or a validation
error -- fails before any network call). To see the full decision, run
`jev reveal RECORD_ID` as a separate, explicit step outside the original
task. (The one exception is a profile explicitly listed under active mode,
covered above -- see "Active mode" for what changes and what doesn't.)

### Manual decision, starter profile

```bash
jev profile list
jev profile show task-routing

echo '{"profile": "review-triage", "context": "..."}' | jev decide --stdin
```

Four starter profiles ship with the package: `task-routing`,
`workflow-selection`, `review-triage`, `investigation`. Each profile is a
pre-defined question/options pair; `jev profile show <id>` prints its full
definition, including its documented fallback behavior.

### Automatic (Claude Code) usage

Copy or symlink `skill/jev-decisions/` into your Claude Code skills
directory, then either let it auto-discover relevant decisions or invoke it
explicitly:

```
/jev-decisions
/jev-decisions "which of these two approaches should I take?"
```

See `skill/jev-decisions/SKILL.md` for the full invocation contract,
including when the skill is (and isn't) suitable, and
`skill/jev-decisions/references/dynamic-choice.md` for formulating ad-hoc
questions. Skill discovery is opportunistic: it's a tool Claude may reach
for, not one required for every decision.

To uninstall the skill, delete or unlink the `skill/jev-decisions/`
directory from your skills path; the Python package is independent and can
stay installed (or be removed separately with `pip uninstall jev-decisions`).

### No credentials / provider unavailable

This is an expected, handled case, not an error state to work around: `jev
doctor` reports it, `jev decide` returns a `failed` outcome (exit 2), and the
Claude Code skill falls back to normal reasoning without blocking the task
(see "Safe no-call fallback" in `SKILL.md`).

## Development

```bash
python -m pytest
python -m ruff check .
python -m mypy
```

GitHub Actions runs the same checks on Python 3.11, 3.12 and 3.13.

## Layout

- `src/jev_decisions/`: importable package and CLI entry point (`jev`). No
  Claude Code dependency.
- `src/jev_decisions/profiles/data/`: the four starter Choice profiles.
- `skill/jev-decisions/`: the installable Claude Code skill.
- `tests/`: unit and CLI tests, run with mocked provider transport (no
  network access required).
- `config/example.yaml`: annotated example of every config field.
- `docs/public-api.md`: the stable public Python API, versioning/migration
  policy, and a from-a-built-wheel install example.
- `.github/workflows/ci.yml`: Python checks.
