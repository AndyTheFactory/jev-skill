# Public API and versioning policy

`jev-decisions` is a standalone Python package: everything below is usable
from any Python project with `pip install jev-decisions` (or `-e .` from a
checkout) and **no Claude Code dependency whatsoever** -- nothing in
`src/jev_decisions/` imports, shells out to, or otherwise couples to Claude
Code. The `skill/jev-decisions/` directory is a separate, optional
integration layer on top of this package; see `README.md` for installing
it.

## Versioning

The package follows semantic versioning (`jev_decisions.__version__`,
currently pre-1.0). Before 1.0, minor version bumps may include breaking
changes to the public API below; from 1.0 onward, only a major version bump
will. The request/response *schema* is versioned independently via
`schema_version` (currently only `"1.0"` is accepted, see
`jev_decisions.schemas.SCHEMA_VERSIONS`) -- this covers the wire/JSON shape
a provider or a stored record uses, separate from the Python API's own
version.

**Migration policy:** a breaking change to anything listed as public below
gets a note in the version's changelog/PR description and, where
practical, a deprecation cycle (old name still works, emits nothing yet --
there's no deprecation-warning machinery pre-1.0, so "old name still works
for one more minor version" is the working rule). Anything not listed as
public may change or disappear in any release without notice.

## Public surface

Import these from their listed module; anything not listed (including
private, underscore-prefixed names within these modules) is internal.

### `jev_decisions.schemas`
The Choice request/response contract.
- `ChoiceRequest`, `ChoiceOption` -- build a request.
- `ProviderChoiceResponse` -- what a provider adapter returns; validate
  with `.validate_against_request(request)`.
- `DecisionError` -- sanitized error contract.
- `SCHEMA_VERSIONS`, `MIN_OPTIONS`, `MAX_OPTIONS`, `MAX_CONTEXT_CHARS` --
  the bounds the schema enforces.
- `SchemaValidationError`.

### `jev_decisions.policy`
Deterministic acceptance/abstention, pure and network-free.
- `evaluate(request, response, *, error=None, config=None,
  abstain_option_ids=frozenset()) -> Decision`
- `Decision`, `DecisionOutcome`.

### `jev_decisions.config`
- `JevConfig`, `ProviderConfig`, `ExecutionConfig`, `TelemetryConfig`,
  `PolicyConfig`.
- `load_config(...)`, `ConfigError`.

### `jev_decisions.engine`
The orchestration layer `jev decide` itself calls -- fingerprinting,
caching, budget, telemetry, protected storage, and (for `jev decide`)
active-mode gating, all wired together.
- `run_shadow(config, request, ...) -> ShadowResult` -- unconditionally
  shadow, regardless of config. What you want for anything that must never
  reveal a result (benchmarking, evaluation, anything where seeing the
  answer would bias what happens next).
- `run_decision(config, request, ...) -> ShadowResult | AdvisoryResult` --
  what `jev decide` calls; upgrades to `AdvisoryResult` only under the
  active-mode conditions documented in `README.md`.
- `ShadowResult`, `AdvisoryResult`, `ActionPermission`.

### `jev_decisions.profiles`
- `load_registry(directory=None) -> ProfileRegistry`.
- `Profile`, `ProfileRegistry`, `ProfileError`.
- `DEFAULT_PROFILES_DIR` -- the four starter profiles shipped with the
  package.

### `jev_decisions.baseline`
- `Baseline`, `load_baseline(path, *, now=None)`, `BaselineError`.
- `save_protected`/`load_protected` -- persistence keyed by record id, used
  internally by `engine` but usable directly by an external consumer that
  wants to manage its own baseline records.

### `jev_decisions.dynamic`
- `build_dynamic_request(...)`, `validate_dynamic_request(request)`,
  `DynamicRequestRejected`.

### `jev_decisions.store`
- `load(record_id, *, directory=None) -> Decision`,
  `RecordNotFoundError`, `InvalidRecordIdError` -- what `jev reveal` calls;
  useful for a consumer building their own reveal/audit tooling.

### CLI
`jev decide` / `jev reveal` / `jev doctor` / `jev profile` / `jev
evaluate` -- see `README.md`. The CLI's JSON output shapes
(`ShadowResult`/`AdvisoryResult`/`Decision`/`ProfileMetrics`/
`EvaluationReport` serialized via `model_dump`) are part of the public
contract at the same stability level as the Python classes they come from.

## Internal (may change without notice)

`jev_decisions.cache`, `jev_decisions.budget`, `jev_decisions.telemetry`,
`jev_decisions.fingerprint`, `jev_decisions.provider.*` (the OpenRouter
adapter's HTTP details), `jev_decisions.evaluation.*` (dataset/metrics --
used by the benchmark tooling, not a consumer-facing contract), and
`jev_decisions.cli` itself as a *module* (import the functions above, not
`jev_decisions.cli.cmd_decide` and friends).

## External consumer example

No Claude Code, no CLI subprocess -- just the library:

```python
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
# `response` would normally come from your own provider call; policy.evaluate
# is provider-agnostic and takes any schema-valid ProviderChoiceResponse.
response = ProviderChoiceResponse(
    selected_option_id="lru", probabilities={"lru": 0.9, "redis": 0.1}
)
decision = evaluate(request, response, config=JevConfig().policy)
print(decision.outcome)  # "accepted"
```

See `tests/test_public_api_smoke.py` for this exact usage exercised as an
automated smoke test, plus a from-a-built-wheel clean-install test.

## Clean install

```bash
pip install jev-decisions   # once published; for now: pip install -e ".[dev]"
jev --version
jev doctor
```

`jev` is a console-script entry point, so it works from any working
directory once the environment it was installed into is active -- no
dependency on the current directory being this repository. To uninstall:
`pip uninstall jev-decisions` (the Claude Code skill, if installed
separately under a skills directory, is removed independently -- see
`README.md`).

## Provider selection (change request #31–35)

The Choice request/response contract and policy engine are provider-independent.
Choose \`provider.name: openrouter\` (default) or \`provider.name: typesafe\`.
The native provider requires \`pip install 'jev-decisions[typesafe]'\` and
\`TYPESAFE_API_KEY\`; the OpenRouter provider uses \`OPENROUTER_API_KEY\`.
A missing SDK or credential returns a sanitized provider error.

\`ProviderConfig.resolved_model\` selects the matching model alias if omitted.
Changing the provider does not activate a profile and never changes the
public \`ChoiceRequest\` or \`ProviderChoiceResponse\` shape.

### Migration and evaluation

Decision fingerprints now include the provider name and resolved model, deliberately
invalidating legacy OpenRouter cache entries. New telemetry includes \`provider\`;
older telemetry records without provenance are parsed as \`unknown\`, **not**
automatically reclassified as TypeSafe or OpenRouter. Evaluation reports include
\`by_provider\` to keep the two providers' calibration, latency and cost separate.
Re-run shadow-mode evaluation after switching providers before enabling active mode.
