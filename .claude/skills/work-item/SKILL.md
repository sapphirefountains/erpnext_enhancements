---
name: work-item
description: Work on or write a WI-xxx migration work item in erpnext_enhancements. Use when a task references a WI number, when planning migration work, when checking whether something is blocked on a business decision, or when deciding whether to build a customization at all.
---

# Work items

The ERPNext migration is planned as numbered work items. [`PLAN.md`](../../../PLAN.md) is the
master plan — §4 is the dependency graph, §5 the critical path, §6 the index, §7 the risk
register. Each item is a file in [`work-items/`](../../../work-items/).

If a task references a WI number, **read that file first**. It carries the acceptance
criteria, the rollback, and — importantly — an explicit list of what is *not* in scope.

## The native-first rule

This is the single most important convention in the repository, and it constrains what you
are allowed to build.

Before building anything, check whether ERPNext already does it. Every work item carries a
**Native-first check** section recording that investigation against the live instance. When
a native mechanism is sufficient, using a custom one is not a judgment call —
WI-013 states it plainly: *"A custom approval doctype would be a defect."*

So when a task sounds like "add a doctype that tracks approvals" or "build a workflow for
X", the first move is to find out whether Authorization Rule, Workflow, Role Permissions, or
an existing ERPNext feature already covers it. `PLAN.md` §2 is the standing audit.

## Business decisions are not yours to make

[`decisions/OPEN-DECISIONS.md`](../../../decisions/OPEN-DECISIONS.md) is the register of
**OD-n** open business decisions — tax treatment, company scope, cutover date. These are
explicitly *business* decisions, not engineering ones.

A work item gated on an OD carries it as a stated precondition and is written to execute
under **any** branch of that decision. If you hit an unresolved OD, do not pick a branch to
unblock yourself. Build the branch-proof part, and say what is blocked.

Some ODs are resolved (see the resolutions table) but still carry a sign-off gate — OD-2's
direction is set, yet the tax matrix needs written CPA confirmation before go-live. Direction
resolved ≠ free to ship.

## Work-item file shape

```markdown
# WI-0NN: Title
**Phase:** 0   **Type:** FIXTURE|CONFIG|DATA|CODE   **Size:** S|M|L
**Blocked by:** …   **Blocks:** …

## Why
## Native-first check
## Preconditions
## Scope
## Acceptance criteria
## Rollback
## Explicitly NOT in this work item
```

Two sections do the most work and are the most often skipped:

- **Acceptance criteria must be checkable**, ideally as a query or a concrete test a person
  performs. `SELECT COUNT(*) FROM \`tabAuthorization Rule\`` = 1, then "PM submitting a PO
  above threshold receives the authorization error; CEO submits the same PO successfully."
  Not "the rule works."
- **Explicitly NOT in this work item** is what keeps scope from drifting. Write it.

Every claim about the live system should say what it was verified against and when — the
plan's facts are dated and sourced (`prod_finance_native`, "verified 14 Jul 2026") precisely
so a stale assumption is visible rather than inherited.

## Runbooks

Items with an operational procedure get a runbook under
[`docs/migration/`](../../../docs/migration/) — e.g. `wi011-apply-runbook.md`. If your item
requires someone to do things in a specific order on a live site, it needs one.

## Configuration ships version-controlled

A work item typed **FIXTURE** or **CONFIG** does not mean "click it in the UI". The rule is
that the definition lives in the repo and deploys through `main` — a fixture entry or an
idempotent `seed_*` patch — so it reaches both test and prod without hand replay. See the
`fixtures-and-patches` skill.

## When you finish

Update the item's status where the plan tracks it, keep `PLAN.md` §6 accurate, and bump the
version with a changelog entry — see `release-prep`.
