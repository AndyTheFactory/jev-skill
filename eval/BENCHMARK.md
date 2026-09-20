# M3 shadow benchmark: procedure and active-mode gate

**Status: no live benchmark run has been executed yet.** This document is
the reproducible procedure and the decision rule for M4; it intentionally
contains no completion-rate or overhead numbers, so nothing here is
fabricated. Fill in a dated results section (template below) each time this
is actually run.

## What this benchmark is for

Comparing representative coding/debugging/review/routing tasks completed
**with** the jev-decisions skill available (shadow mode -- Jev never acts,
only its telemetry/record is observed afterward) versus **without** it, to
build the evidence base M4 (#19/#20) needs before any profile can be
considered for active mode. This is necessary input, not sufficient by
itself: enabling active mode additionally requires explicit config at
user/env/CLI trust (see `config.py`) plus a human review of this evidence,
never an automatic threshold.

## Environment to record for every run

- `jev --version` (also written to the runner's `.meta.json`)
- Python version (`python --version`)
- OS/platform (`platform.platform()`)
- `eval/dataset/<version>` (dataset directory + version)
- `provider.model` from the active config
- Date and git commit of this repository

## Task set

Use `eval/dataset/v1`, `split: eval` (held out, not tuned against). Each
dataset example already encodes its profile/question, context, and --
where defensible -- a reviewed label (see `eval/dataset/RUBRIC.md`).
`scripts/run_benchmark.py --split eval` runs the *shadow decision* half of
the benchmark automatically and writes a manifest + `.meta.json`.

For each dataset example that maps to a real task (coding/debugging/
review/routing), also identify or construct a small, self-contained task
with an **external check** -- something that verifies completion without
relying on the model's own say-so:

- coding/debugging: a failing test that must pass, or a reproducible bug
  that must no longer reproduce.
- review: a known-injected finding that must (or must not) be flagged.
- routing: the actually-correct next action, per the dataset label.

## Procedure (per task, run twice)

1. **Without Jev**: run the task in a fresh Claude Code session with the
   `jev-decisions` skill not installed/available. Record: did the external
   check pass, wall-clock latency, number of tool calls, and (if available)
   token/cost usage.
2. **With Jev**: run the *same* task in a fresh session with the skill
   available, in its default shadow mode. Record the same measures, plus
   whether the skill was invoked at all (it's opportunistic, not
   mandatory -- non-invocation on a suitable task is itself a data point).
3. Do not reveal (`jev reveal`) the shadow result before or during the
   task; that would defeat the isolation M2 exists to guarantee.
4. After the task, run `jev evaluate --input <manifest> --dataset
   eval/dataset/v1` for the decision-quality half (accuracy where labeled,
   abstention, calibration, baseline agreement -- baseline agreement is a
   loose heuristic, not a correctness proxy).

## Reporting a run

Document, per profile and overall:

- **Sample size** (N tasks) and why it is what it is (time/cost
  constraints) -- small-N results are directional, not conclusive; say so.
- **Uncertainty**: this is observational, single-annotator-path data
  unless explicitly run by multiple people; note that.
- **Failures**: every task where the external check failed either way,
  and whether Jev's shadow result (revealed only for this write-up, not
  during the task) would have pointed toward the working or the failing
  option.
- Unnecessary tool calls / latency / cost overhead attributable to the
  skill being present (even at zero actions taken, invoking it costs a
  CLI call).

### Results template

```
## Run: <date>, dataset_version=<v>, jev_decisions_version=<v>, commit=<sha>

| Profile          | N  | With-Jev pass | Without-Jev pass | Notes |
|------------------|----|----------------|-------------------|-------|
| task-routing     |    |                |                   |       |
| workflow-selection |  |                |                   |       |
| review-triage    |    |                |                   |       |
| investigation    |    |                |                   |       |

Decision-quality (from `jev evaluate`): accuracy=<x or "not available">
(n=<n>), coverage=<x>, calibration_error=<x or "not available">.

Uncertainties: <...>
Failures: <...>
```

## Active-mode gate (explicit, per profile)

Per #19/#20, active mode:

- Defaults to `shadow` after install/upgrade and stays there unless
  explicitly enabled at user/env/CLI trust (never from a project file).
- Should only be considered, **profile by profile**, after this benchmark
  has been run with a sample size and failure analysis a reviewer finds
  convincing for *that* profile -- there is no universal probability/
  margin threshold that applies across profiles by assumption; the
  provisional 0.80/0.20/0.70 defaults in `policy.py` are not calibrated
  and this benchmark is part of what calibrating them for a given profile
  would require.
- Remains disabled for every profile until both conditions hold: explicit
  trusted config *and* a documented review of this benchmark's results for
  that profile. Neither alone is sufficient.
