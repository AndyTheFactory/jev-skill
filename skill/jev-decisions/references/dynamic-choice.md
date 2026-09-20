# Formulating a dynamic Choice question

Use this when the decision you want a second opinion on does not match any
profile in `jev profile list` (`task-routing`, `workflow-selection`,
`review-triage`, `investigation`).

## When it's suitable

A dynamic Choice question is suitable only when **all** of these hold:

- It has 2-8 mutually exclusive, genuinely plausible answers.
- The right answer is not something you or the user can just verify directly
  (if you can check, check -- don't ask Jev to guess what a test run or a
  grep would tell you for free).
- It is not deterministic (if there's one objectively correct answer given
  the facts in front of you, work it out yourself).
- It is not a permission, deployment, credential, destructive-action or
  payment decision. Those require your own reasoning and the user's explicit
  authority -- never a probabilistic classification.
- It is not a broad architecture or system-wide redesign judgment. Those need
  deliberate reasoning and usually a conversation with the user, not a single
  Choice call.

If none of the above are obviously true, don't invoke Jev -- reason it out
yourself.

## Formulating the question

- **Options**: give each one a short `id` (letters/digits/hyphens) and a
  one-sentence `description`. Keep options mutually exclusive -- if two
  options could both be "correct" simultaneously, merge or re-split them.
- **Context**: include only the evidence relevant to *this* decision (error
  text, the specific code path, the specific constraint) -- not the whole
  file or the whole conversation. Context is capped at 8,000 characters and
  gets truncated/rejected past that, so keep it tight.
- **Uncertainty option**: when the options might not exhaust the real state
  of the world (you're not sure you've listed every plausible answer), add
  an explicit `insufficient_context` or `unknown` option rather than forcing
  a choice among the ones you did list. `jev_decisions.dynamic.build_dynamic_request`
  adds this automatically when `include_uncertain_option=True` (the default).

## What you get back, and what it means

By default (`shadow` execution mode) the CLI reports only a status
(`accepted`/`abstained`/`failed`/`rejected`), not the selected option or its
probability. Whatever the status is:

- Continue your own reasoning as if Jev had not been called; a non-`accepted`
  result never blocks or delays the task.
- Never treat an `accepted` result as authorization to skip a permission
  check, a test run, or an explicit user instruction. It is at most a
  recommendation for *which option to consider first* in your own reasoning.

## What the CLI/library enforce for you

`jev_decisions.dynamic.validate_dynamic_request` rejects (fails closed)
requests that mention destructive actions, deployment, credentials, payments,
or system-wide redesign, as a backstop in case this guidance was missed. A
dynamic request has no field for policy thresholds or execution mode -- those
only ever come from the trusted config layers (see `config.py`), so a
dynamically formulated question can never loosen security configuration.
