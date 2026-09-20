---
name: jev-decisions
description: >
  Get a second, independent opinion on a bounded either/or/few-way decision
  (task routing, workflow choice, review-finding severity, next debugging
  step, or a similar 2-8-option judgment call) by consulting the Jev
  decision engine. Use opportunistically when such a decision is genuinely
  ambiguous and you'd otherwise be guessing; not required for every
  decision, and never for deterministic, verifiable, destructive/permission,
  or broad-architecture judgments. Invoke explicitly with `/jev-decisions
  [decision]`.
---

# Jev decisions

Jev is a standalone decision engine reachable through the `jev` CLI. This
skill tells you when and how to consult it. It never executes anything on
its own -- it returns a status, and by default (`shadow` execution mode)
not even that status is informative about the answer. **You always remain
the one who reasons about and acts on the task.**

## Ground rules

1. **Claude-owned execution.** Nothing Jev returns is ever self-executing.
   You decide what to do next; Jev is, at most, one input among many you
   may choose to weigh.
2. **Never overrides permissions, user instructions, or safety controls.**
   A Jev result -- accepted or not -- never authorizes a destructive action,
   a deployment, a credential use, or anything the user's permission
   settings or explicit instructions would otherwise block. If in doubt,
   treat the result as if it doesn't exist.
3. **Non-recursive.** Don't invoke this skill from within the reasoning
   you're doing to answer a Jev question, and don't chain multiple Jev
   calls into a loop hoping for a different answer. One call per decision
   point.
4. **Safe no-call fallback.** If the `jev` CLI isn't installed, isn't on
   PATH, has no credentials configured, or the provider is unavailable
   (`jev doctor` reports the problem) -- just continue with your own
   reasoning. Never block, delay, or degrade the task because Jev is
   unavailable.
5. **Opportunistic, not mandatory.** Auto-discovery means you may reach for
   this skill when a relevant ambiguous decision comes up. It does not mean
   every decision needs a Jev call -- most don't.

## When it's suitable

See `references/dynamic-choice.md` for the full suitability checklist. In
short: 2-8 mutually exclusive options, genuinely uncertain (not something you
can just check or work out), and not a permission/destructive/deployment/
credential/broad-architecture decision.

## How to invoke

### Explicit invocation

`/jev-decisions [decision]` — with or without an argument:

- **With an argument**: treat it as the decision to formulate a dynamic
  Choice question about (see `references/dynamic-choice.md`).
- **Without an argument**: look at the most recent genuinely ambiguous,
  bounded decision in the current task. If none exists, say so and do
  nothing -- don't invent a decision just to have something to call.

### Using a starter profile

Four starter profiles cover common cases -- list them with:

```bash
jev profile list
jev profile show task-routing
```

(`task-routing`, `workflow-selection`, `review-triage`, `investigation`.)
To use one:

```bash
echo '{"profile": "task-routing", "context": "<relevant evidence, concise>"}' \
  | jev decide --stdin
```

### Formulating a dynamic question

When no profile fits, build the request yourself per
`references/dynamic-choice.md`:

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

### Reading the result

`jev decide` prints only `{record_id, outcome, action: {permitted: false}}`
to stdout -- never the selected option or probability -- and the outcome to
stderr, exiting with a code you can branch on (0 accepted, 1 abstained, 2
failed, 3 rejected). Treat anything other than `accepted` as "no usable
signal" and proceed on your own judgment; treat `accepted` as, at most, one
input to weigh alongside everything else you know about the task. You are
not expected to reveal or read the actual selected option -- shadow mode
exists precisely so it doesn't bias your reasoning.

**Active mode (opt-in, per profile).** An operator can explicitly configure
a specific profile to also expose `selected_option_id`/`probability` for an
`accepted` outcome (see `README.md` for the config). `action.permitted` is
still always `false` even then -- ground rule 2 above applies exactly the
same way: the visible recommendation is one more input to weigh, never
something that authorizes a destructive action, a deployment, a credential
use, or anything else your permission settings or the user's explicit
instructions would otherwise block.

### Diagnosing problems

```bash
jev doctor                  # config + credential presence, no secrets printed
jev doctor --check-provider # also attempt a live connectivity probe
```

If `jev doctor` reports a problem, fall back per rule 4 above -- don't try
to work around it.
