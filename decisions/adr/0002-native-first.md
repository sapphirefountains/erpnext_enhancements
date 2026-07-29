# 0002. Native ERPNext first; a custom mechanism where a native one suffices is a defect

- **Status:** Accepted
- **Date:** 2026-07-29 (recorded retroactively)

## Context

This app exists to customise ERPNext, which makes it structurally tempting to solve every
problem inside it. Custom code is faster to write than configuration is to learn, it does
exactly what you want, and it is entirely under our control.

It is also the thing that makes ERPNext upgrades painful, splits the permission model in two,
and produces features that behave subtly differently from every other part of the system the
user already knows.

The concrete case that fixed the rule: WI-013 needed dollar-threshold escalation on Purchase
Orders. A custom approval doctype was the obvious build. ERPNext's native **Authorization
Rule** already does it — verified present and empty (0 rows) on the v16 build — with
`transaction='Purchase Order'`, `based_on='Grand Total'`, and an `approving_role` whose
holders are exempt. That yields "PM saves the PO, CEO submits it" with no workflow, no new
doctype, and permissions that ERPNext already understands.

## Decision

Before building anything, check whether ERPNext already does it. Every work item carries a
**Native-first check** section recording that investigation against the live instance, with
the date and the source it was verified against.

Where a native mechanism is sufficient, building a custom one is **not a judgment call**.
WI-013 states it as the standing rule: *"A custom approval doctype would be a defect."*

`PLAN.md` §2 is the standing native-first audit.

## Consequences

- **Investigation is part of the work, not overhead.** "I couldn't find a native way" is only
  a conclusion after looking; the plan records what was checked so the next person doesn't
  repeat it.
- Configuration still ships version-controlled. Native-first does not mean clicking it into
  the site — a FIXTURE or CONFIG work item lands as a fixture entry or an idempotent `seed_*`
  patch, deployed through `main`, so it reaches test and prod without hand replay. See ADR
  [0003](0003-repo-is-source-of-truth-for-customizations.md).
- Some native mechanisms are worse than what we'd build. The rule is native-*first*, not
  native-only — but the burden is on the custom build to justify itself, in writing, in the
  work item.
- Native mechanisms carry their own constraints and those must be recorded too. Authorization
  Rule is per-company, so a second company means cloning the rule — noted in WI-013 against
  the OD-1 branch rather than discovered later.
