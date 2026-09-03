# Demo Runbook

A 5-minute demo script for EchoWard. Optimized to be *used while recording*, not read as
documentation — see [README.md](../README.md) and [ARCHITECTURE.md](../ARCHITECTURE.md) for that.

## Setup before recording

1. Confirm the backend is awake (Render free tier spins down after ~15 min idle — hit `/health`
   once and wait for a 200 before recording) and the frontend is loaded.
2. Confirm `GEMINI_API_KEY` and Agora credentials are configured in the deployed environment (see
   README's "Deployment" section) — the whole demo depends on both being live.
3. Open the app in **two browser tabs** side by side, or have a second device ready — one to speak
   from and drive the call, one to show the dashboard updating live for a "second viewer."
4. Create a fresh incident (clear title, e.g. "Payments outage") so the dashboard starts empty.
5. Join the voice room from the tab that will speak, click **Start EchoWard**, and confirm it
   appears in the participant list before you start talking.
6. Do a quick sound check — one throwaway sentence — to confirm EchoWard responds audibly before
   recording for real.

## Demo scenario: payment outage

The scripted flow: **503 errors → hypothesis → contradictory database evidence → conflict → human
resolution → rollback decision → prepare → confirmation → execution → result → status.**

### 1. Report the symptom (fact)

**Say:** *"We're seeing a spike in 503 errors on the payment service, started about ten minutes
ago."*

**Expect:** A new `Fact` appears under **Facts** (blue "CONFIRMED" badge), and a timeline entry is
added. No conflict, no hypothesis yet — just an observation.

### 2. Propose a theory (hypothesis)

**Say:** *"I think the database connection pool is exhausted — that's probably the root cause."*

**Expect:** A new item appears under **Hypotheses** (violet "UNCONFIRMED" badge), *not* under Facts
— even though it was stated confidently. This is the moment to point out to judges: EchoWard never
lets confident phrasing become a fact.

### 3. Introduce contradictory evidence (conflict)

**Say:** *"Actually, I just checked — the database connection pool looks completely healthy, that's
not it."*

**Expect:** A **Conflict** card appears (red, full-width, prominent) referencing both statements,
labeled `UNRESOLVED`. Point out: EchoWard surfaces the contradiction instead of silently picking a
side or overwriting the earlier hypothesis.

**When EchoWard should speak here:** within the cooldown window, EchoWard proactively says
something like *"I'm seeing conflicting reports about the database connection pool. Can someone
confirm which one is accurate?"* — call this out as the proactive-voice capability, not just a
dashboard notification.

### 4. Human conflict resolution

On the dashboard, click **Resolve** on the conflict card. **Expect:** the card shows "Resolved by a
human — EchoWard did not decide this," and the Conflicts panel (and its mirrored Coordination
finding) clears immediately on both open tabs — the live WebSocket update, not a page refresh.

### 5. Make a decision and propose an action

**Say:** *"Let's roll back the payment service to the last known-good version."*

**Expect:** A new item under **Decisions**, and a corresponding **Action** ("Roll back the payment
service") appears, initially unowned/pending. If it sits unowned, point out the Coordination panel
flagging `Action has no owner`.

**Say (assigning an owner):** *"I'll own the rollback."*

**Expect:** The action now shows an owner, and the "no owner" coordination finding disappears.

### 6. Prepare the action

On the dashboard, click **Prepare: Roll back payment service** on the action card. **Expect:** the
action moves to `awaiting_confirmation`, a timeline event is added, and — this is the safety
story — **nothing has executed yet**. If a live voice room is active, EchoWard may proactively say
*"I've prepared to roll back the payment service. Can I get confirmation to proceed?"*

### 7. Human confirmation

Type a confirming name (or leave the default) and click **Confirm & Execute**. **Expect:** the
action transitions `confirmed → executing → completed` (or `failed`, if you deliberately used a
reason containing "force_failure" for the failure-path demo), with a `[DEMO]`-labeled result
message. Call out explicitly: this is the *only* button in the entire system that causes anything
to actually execute, and it required an explicit human click.

### 8. Execution result

**Expect:** the action card shows the tool result, a timeline event records completion, and — with
the voice room active — EchoWard announces it out loud: *"Done — roll back the payment service
completed successfully."*

### 9. Final status request

**Say:** *"EchoWard, what's the status?"*

**Expect:** EchoWard responds with the situational-summary checkpoint (e.g. action/conflict counts,
resolved state) — a spoken answer generated deterministically from the same coordination data on
the dashboard, not a fresh LLM call.

## What to emphasize to judges

- **Uncertainty is structural, not cosmetic.** Facts vs. hypotheses vs. conflicts are different
  data types with different badges — not one text summary with adjectives.
- **EchoWard never picks a side.** The conflict in step 3 is only ever resolved by an explicit human
  click, never inferred from later conversation.
- **Nothing executes without a human click.** Steps 6 and 7 are two separate, explicit steps for a
  reason — highlight that a completed/failed action can't be re-confirmed (safe against double
  execution).
- **It's genuinely voice-native.** EchoWard hears the conversation live through Agora (not a
  transcript pasted in after the fact) and speaks back into the same room, both reactively and
  proactively.
- **Two browsers, one live picture.** Whatever updates on one tab appears on the other within about
  a second, with no manual refresh — the shared incident state is real, not per-viewer.
