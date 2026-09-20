# Evaluation dataset: annotation rubric

Dataset version: `v1`. Files live in `eval/dataset/v1/*.yaml`, one example per
file, loaded by `jev_decisions.evaluation.dataset.load_dataset`.

## Categories

- **typical**: an unambiguous, in-distribution case for its profile or
  question. Ground truth should usually be labelable.
- **ambiguous**: a real case where more than one option is defensible.
  Label only if a reviewer can still argue one option is clearly better on
  balance; otherwise leave `label` unset.
- **out_of_distribution**: a case the profile/question wasn't designed for
  (e.g. a `task-routing` scenario that's really a mix of two categories).
  Used to check abstention/rejection behavior, not to demand a "right"
  answer.
- **insufficient_context**: context genuinely doesn't support any option.
  The correct behavior is abstention (or selecting an explicit
  `insufficient_context`/`unknown` option where the profile has one); do not
  label a specific "correct" option.
- **unsuitable_invocation**: a question that should never have been asked as
  a Choice decision at all (deterministic, verifiable, high-risk-permission,
  or broad-architecture -- see `skill/jev-decisions/references/dynamic-choice.md`).
  Used to check that dynamic-question validation rejects it. Never labeled.

## When to set `label`

Set `label.correct_option_id` only when it is defensible from either:

- **`task_evidence`**: an external, checkable fact -- the PR that was
  actually merged, the root cause that was actually found, the workflow that
  was actually used and worked. Cite it in `notes`.
- **`reviewed_label`**: a human reviewer's considered judgment, recorded
  with their reasoning in `notes`. Two independent reviewers agreeing is
  stronger evidence than one.

Leave `label` unset (omit the field, don't set `correct_option_id: null`
under a `source`) whenever:

- The category is `ambiguous` without a defensible majority answer,
  `insufficient_context`, or `unsuitable_invocation`.
- No one has actually reviewed it yet.

`correct_option_id: null` under a present `label` is reserved for cases
where the reviewed truth is specifically "no option was correct, abstention
was" -- distinct from "we don't know" (an absent `label`).

## Independence from Jev

Never set a label by running `jev decide` and copying its answer. Labels
must be derivable without looking at any provider output, so the dataset
stays a valid, unbiased check on the provider rather than a tautology.

## Splits

- `dev`: free to iterate on, safe to look at while developing prompts/
  profiles.
- `eval`: held out; used only for the M3 shadow benchmark (#18) and later
  the M4 active-mode-gate decision. Don't tune profile wording against it.

## No secrets

Dataset files must never contain real credentials, tokens, or other secrets
-- these are fixtures about *what kind* of question was asked, not real
production data. `load_dataset` refuses to load a file that looks like it
contains a credential-shaped string, as a backstop.
