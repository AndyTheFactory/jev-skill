# CC-Enrutador integration and MCP feasibility

## Status

No forced dependency on CC-Enrutador exists anywhere in this repository,
in either direction: `jev_decisions.integrations.cc_enrutador` is a thin,
optional mapping function that nothing in `cli.py`, `engine.py`, or the
`jev-decisions` skill imports (`tests/test_cc_enrutador_integration.py`
enforces this directly). If you never import it, nothing about how the
skill or CLI behave changes -- that satisfies "enabled/disabled without
changing skill runtime" trivially, because there is no flag to flip: the
coupling point is entirely on the *importing* side, not this package's.

This repo has no access to CC-Enrutador's actual internal state schema, so
what follows is a generic, illustrative mapping design. Treat the field
names in `RouterState` as a starting point to adjust against the real
router's schema when an actual integration is built, not as a claim about
what CC-Enrutador's state literally contains today.

## Mapping: router task state -> Jev Choice request

`map_router_state_to_choice_request(state, *, profile=None)` reads exactly
three fields off a router state dict:

| Router field         | Choice request field | Notes                                         |
|-----------------------|-----------------------|------------------------------------------------|
| `current_step`        | `question`            | Falls back to a generic prompt if absent.       |
| `candidate_actions`    | `options`              | `{id, description}` pairs, 2-8 required (same bound `ChoiceRequest` always enforces). |
| `context`              | `context`              | Passed through as-is, subject to the normal 8,000-char bound. |

Everything else on a router state dict -- `task_id`, credentials, other
users' task history, anything -- is never read or forwarded. This is the
privacy boundary: the mapping function is an allowlist (three fields in),
not a denylist, so a router state that happens to carry sensitive fields
can't leak them into a Jev request by accident. `task_id`, if you want it
recorded, belongs on a `Baseline.task_id` (see `jev_decisions.baseline`),
which is a separate, explicit choice the caller makes -- not something
this mapping does implicitly.

Malformed state (missing `candidate_actions`, an action missing `id`/
`description`, or content that fails `ChoiceRequest`'s own validation)
raises `RouterStateError` -- fails closed, same as everywhere else in this
codebase, rather than guessing at a partial request.

## Privacy boundary, restated

- No router field beyond the three listed above is ever read.
- The mapped request still goes through every existing Jev safety layer
  unchanged: 2-8 option bound, 8,000-char context bound, dynamic-question
  suitability validation if used dynamically, shadow-mode isolation,
  active-mode gating exactly as documented for any other request.
- A CC-Enrutador integration that wants baseline/task-id correlation opts
  into that separately and explicitly via `Baseline`, not implicitly via
  this mapping.

## Why no live client, no feature flag in this package

The issue asked for a prototype "behind a disabled feature flag if
practical." A live client needs a real CC-Enrutador instance to integrate
against, which this repository doesn't have -- so a "disabled flag" inside
`jev_decisions` itself would be a flag guarding code with no real target to
exercise, i.e. dead weight. The actual disabled-by-default mechanism is
architectural instead: the mapping function lives in an `integrations`
subpackage nothing else imports, so *not building CC-Enrutador-side glue
code* is the disabled state, and building that glue code (on the
CC-Enrutador side, importing this function) is the enabled state. No
runtime toggle needed because there's no code path to toggle.

## MCP feasibility: not needed for v1

MCP (Model Context Protocol) would let an MCP-capable client call Jev
directly as a tool server instead of shelling out to the `jev` CLI. Per the
issue's own criterion -- assess MCP only if CLI limitations are
demonstrated -- there's no such demonstration here:

- The CLI (`jev decide`/`reveal`/`doctor`/`profile`/`evaluate`) already
  covers every operation a consumer needs, with a documented, versioned
  JSON contract (see `docs/public-api.md`).
- A CC-Enrutador integration, or any other Python consumer, can also skip
  the CLI entirely and call the library directly (`jev_decisions.engine`,
  `.policy`, etc.) -- see `docs/public-api.md`'s external-consumer example
  -- which is strictly more direct than an MCP round trip would be for a
  same-process or same-host integration.
- No latency, protocol, or capability gap has come up in building or using
  this CLI that MCP would close.

Conclusion: MCP is not introduced into v1 core. If a future integration
target specifically needs MCP (e.g. a remote, cross-process client that
can't shell out or import the library), revisit with that concrete
limitation in hand rather than speculatively.
