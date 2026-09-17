# 0014. AI write gating decides per call, not per tool

- **Status:** Accepted
- **Date:** 2026-09-17
- **Amends:** [0006](0006-ai-writes-need-desk-confirmation.md)

## Context

[ADR 0006](0006-ai-writes-need-desk-confirmation.md) makes every mutating MCP tool wait for a
desk confirmation. `is_mutating(tool)` takes a **tool** and returns a boolean, so the
granularity of the whole mechanism is the tool.

Letting Triton clock people in and out broke that granularity, because one tool spans two
quite different kinds of authority:

- **Self-service.** "Clock me out." A person acting on their own time record. They can
  already do exactly this, unsupervised, by tapping a button in the Time Kiosk. Routing it
  through a desk confirmation would not add a check — the same person would be confirming
  their own action — and it would land on a technician holding a phone in a mechanical room,
  which is where this feature is meant to be useful.
- **On behalf.** "Clock Dave out." Authority over somebody else's record, and somebody else's
  pay. That is the case a human should see before it lands.

Splitting these into two tools was considered and rejected: the model chooses which tool to
call, so a tool-level split makes the model the thing that decides whether a human is
consulted — the same collapse ADR 0006 refuses when it refuses a model-callable confirm. The
distinction has to be drawn from the **arguments**, on the server, after the model has
spoken.

Verified while writing this (2026-09-17): `_gated_execute(tool, original, arguments)` already
receives the arguments, and two existing branches — the private-context denylist and the
`EXEMPTABLE_TOOLS` doctype allowlist — already branch on argument content. The seam was
therefore additive rather than structural.

Also verified on production the same day: `ai_write_gating_enabled` is **0**. The gate ships
dormant and is currently off, so nothing described here is presently executing on prod.

## Decision

`_gate.py` gains a `PER_CALL_GATED` registry mapping a tool name to a **pure, total** decider
over that call's arguments, returning whether *this call* needs a human.

It is consulted inside `_gated_execute` only on the path that would otherwise create an
AI Pending Action — after the confirm-flow bypass, after the `ai_write_gating_enabled` check,
and after the read-through for non-mutating tools. A decider that returns False executes the
call and still writes an `AI Action Log` row marked auto-approved. A decider that raises falls
through to the Pending Action path.

The two clock tools remain in `APP_MUTATING`. `PER_CALL_GATED` answers "does this call need a
human", never "is this tool a write".

## Consequences

- **The gate is not the authorization, and must never be mistaken for it.** It ships dormant
  and is off in production today, so a rule enforced only here is enforced nowhere. Every
  actual permission check for clocking lives in `api/time_kiosk.py` —
  `close_interval_for_employee` checks `TIMELINE_MANAGER_ROLES` itself, and
  `clock_in_with_stashed_fix` has no `employee` parameter at all. Keep it that way: the gate
  is the human-in-the-loop layer on top of authorization, not a substitute for it.
- **A decider must be pure and total.** It runs inside the gate's own try/except, and the
  fail-closed default is deliberate — an exception must mean "ask a human", never "run it".
- **Gating off stays byte-identical.** The registry is consulted below the enabled check, so a
  site with the flag at 0 behaves exactly as it did before this ADR.
- **The blast radius of a wrong decider is a skipped confirmation, silently.** That is why a
  False decision still writes an Action Log row; it is the only evidence the call happened.
  Do not "tidy" that logging away.
- **`EXEMPTABLE_TOOLS` was deliberately not widened.** Its two invariants (a subset of
  `EXPLICIT_MUTATING`, disjoint from `HIGH_RISK`) are pinned by tests, and reusing it for this
  would have overloaded a mechanism that means something else.
- Adding a tool to `PER_CALL_GATED` is a governance decision, not a refactor. The registry
  should stay short enough to read in one screen; if it grows, that is the signal to revisit
  whether tool granularity was the right unit after all.
