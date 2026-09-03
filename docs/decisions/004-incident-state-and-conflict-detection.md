# 004 — Incident State Model and Conflict Detection

## Context

EchoWard's core product principle is that it "does not pretend to know what is true." A generic
chat-summarization approach — flattening everything said into plain text or a single "summary"
field — would erase exactly the distinction that matters most in an incident: whether something is
a confirmed observation, a suspected explanation, or an unresolved disagreement between two people.

## Decision

Model incident knowledge as distinct, explicitly-typed records rather than free text:

- **`Fact`** — a direct observation attributed to its speaker, always carrying `confidence =
  "reported"` (there is no verification mechanism, so nothing is ever automatically elevated to a
  stronger confidence level).
- **`Hypothesis`** — anything proposed, suspected, or inferred (including confidently-worded claims
  that aren't themselves verifiable observations), carrying a `status` of `proposed` / `supported` /
  `refuted`, never silently promoted to a fact.
- **`Decision`** and **`Action`** — explicit, trackable records with owners and status, distinct
  from either of the above.
- **`Conflict`** — recorded (both statements, both sources, `status = "unresolved"`) whenever a new
  statement contradicts something already known, rather than adjudicated automatically. Conflicts
  are resolved only through an explicit human action (`POST .../conflicts/{id}/resolve`) —
  EchoWard/Gemini never decides which side is correct.

There is deliberately **no `root_cause` field anywhere** in the data model — a suspected root cause
is just a `Hypothesis` like any other, confirmed or not.

Conflict *detection* is primarily LLM-driven (using recent facts/hypotheses/decisions/open actions
as prompt context), backed by a deterministic backstop that directly compares a new hypothesis
against existing facts for contradiction — so a clear contradiction can't be missed purely because
the LLM's judgment call didn't flag it on a given turn.

## Why

- A confidence/status field on every extracted item is what makes "surface uncertainty rather than
  hiding it" an enforceable data-model property instead of just a prompting aspiration.
- Treating a confidently-worded claim ("it's definitely the database") as a `Hypothesis`, not a
  `Fact`, prevents the system from ever accidentally treating strong phrasing as verified truth.
- Recording both sides of a conflict (rather than merging or silently preferring one) is what lets a
  human resolve it with full context, and is the direct data-model expression of "surface
  conflicts and missing information rather than pretending to know the root cause."
- Adding a deterministic contradiction backstop alongside the LLM-driven detection reduces reliance
  on the LLM's judgment for the single most safety-relevant classification in the system (missing an
  actual contradiction is worse than an occasional false positive, which a human can dismiss).

## Consequences

- The system deliberately produces *more* structure than a flat summary would — a dashboard has to
  render facts, hypotheses, decisions, actions, conflicts, and timeline as distinct sections rather
  than one text block. This is treated as a feature (see the dashboard's required visual hierarchy),
  not overhead.
- Classification quality (is this really a fact vs. a hypothesis; is this really a contradiction)
  is only as reliable as the LLM's adherence to the extraction prompt, for everything the
  deterministic backstop doesn't cover — an explicitly acknowledged limitation, not something
  independently verified by exhaustive test coverage against a live model.
- Because conflicts are never auto-resolved, an incident can accumulate multiple open conflicts if a
  team doesn't actively resolve them — this is intentional (visibility over silent resolution), but
  means the dashboard's Conflicts panel is deliberately prominent rather than tucked away.
