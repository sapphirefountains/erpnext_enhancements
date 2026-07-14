# WI-057: Project budget discipline — populate budgets on active projects
**Phase:** 2   **Type:** DATA   **Size:** M
**Blocked by:** WI-021 (labor actuals flowing), WI-014 (material actuals flowing)   **Blocks:** WI-058

## Why
The CEO's percentage-of-budget escalation needs a denominator. Verified on prod (14 Jul 2026): zero of 625 projects has a non-zero `estimated_costing`; only 52 have `custom_project_dollar_amount` > 0. This is a **behavior change** — project managers producing labor and materials budgets at project creation — not a code problem. Until budgets exist and are calibrated against real actuals, the percentage rule (WI-058) has nothing to divide by.

## Native-first check
Native `Project.estimated_costing` (standard Currency field, verified) plus the already-existing custom fields `Project.custom_materials_budget` (Currency) and `Project.custom_time_budget_in_hours` (Data) — all verified present on prod — are the budget store. Native **Budget** doctype evaluated and rejected for this purpose: it budgets by GL account / cost center / fiscal year, not per-project labor/materials, so it cannot back a per-project percentage rule. Verdict: native/existing fields; this item is process + backfill only. Nothing is built.

## Preconditions
- WI-021 acceptance met: kiosk-fed Timesheets with `project` are flowing, so labor actuals exist to sanity-check budgets against.
- WI-014 in effect: PO/PI lines carry `project`, so material actuals exist.
- PM process agreed and written: every new Build/Design project receives a labor budget (hours) and materials budget (dollars) at handoff. `Opportunity.custom_estimated_cost` (verified field, read by the Closed-Won handoff) is the natural seed value.

## Scope
- Extend the Closed-Won handoff SOP: budget values keyed by the PM during the handoff step. An optional S-size APP_CODE branch — mapping `Opportunity.custom_estimated_cost` → `Project.estimated_costing` inside `crm_enhancements/api.py::create_project_from_opportunity_background` — is **enumerated only**, not built here; raise it separately if manual keying proves unreliable.
- Backfill budgets for ACTIVE projects only (~434 with status='Active'; verified distribution: Active 434, Completed 185, Paid 4, Canceled 2), from a PM-completed worksheet (project, labor hours budget, materials budget).
- Execution via `frappe.db.set_value('Project', name, {...})` in batches — never bulk `doc.save()` (Project has heavy on_update hooks and the wildcard `'*'` after_save → `global_triton_sync` hook fires per ORM save).

## Acceptance criteria
- `SELECT COUNT(*) FROM tabProject WHERE status='Active' AND project_type IN ('Build','Design') AND (estimated_costing IS NULL OR estimated_costing=0)` = 0 after backfill.
- Sustained discipline: 3 consecutive months in which >90% of newly created Build/Design projects have non-zero `estimated_costing` by day 7 (`SELECT` over creation month cohorts).
- Pre-run export of (name, estimated_costing, custom_materials_budget, custom_time_budget_in_hours) archived.

## Rollback
Keyed restore from the pre-run export via the same `frappe.db.set_value` script.

## Explicitly NOT in this work item
The escalation rule itself (WI-058); native Budget doctype rows (GL-axis budgeting is a separate, unrequested capability); retroactive budgets on Completed projects; changes to handoff engine code.
