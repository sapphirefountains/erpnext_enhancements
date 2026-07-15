# WI-058: Percentage-of-budget PO escalation (75% fire / 85% cap, per-project override)
**Phase:** 2   **Type:** APP_CODE   **Size:** M
**Blocked by:** WI-057 (budgets populated), WI-013 (dollar rule stays as the floor), the full kiosk→timesheet→labor-actuals chain live (WI-021/WI-016) and material actuals via WI-014   **Blocks:** nothing

## Why
This is the control the CEO originally asked for: escalate when a Purchase Order would push a project's cumulative committed cost (submitted POs + timesheet labor actuals) past 75% of its labor/materials budgets, with a hard stop configurable at 85% and a per-project override. It sits at the END of the dependency chain — kiosk adoption → project-tagged Timesheets → labor actuals → credible budgets → this rule — which is why it is Phase 2 and was never buildable at cutover (all 625 projects had zero budgets).

## Native-first check
- **Authorization Rule** (used for the day-one dollar threshold in WI-013): fixed `value` only — cannot express "% of a per-project field." Insufficient.
- **Native Budget doctype + Budget Variance**: budgets by GL account/cost center/fiscal year, and its stop/warn actions key on account budgets, not per-project labor/materials splits. Insufficient.
- Verdict: **justified APP_CODE** — a thin `validate`/`before_submit` hook on Purchase Order in `erpnext_enhancements` comparing (existing submitted PO totals for the project + `Timesheet Detail.costing_amount` actuals for the project) against `Project.custom_materials_budget` / `Project.estimated_costing`. The artifacts it produces (a blocked/warned PO submit) reimplement no native report or feature.

## Preconditions
- WI-057 acceptance green and ≥3 months of clean labor + material actuals on projects (so thresholds fire on believable numbers).
- Thresholds ratified by the CEO (defaults proposed: warn/escalate 75%, block 85%).
- WI-013's dollar-threshold Authorization Rule remains active as the absolute floor (independent of budgets).

## Scope
All in `erpnext_enhancements`, shipped dormant behind the app's master-switch pattern:
- Purchase Order `validate`/`before_submit` hook (new module function, wired via hooks.py doc_events) computing committed+actual vs budget per project on the PO's item lines.
- Two threshold fields + an enable Check on the 'ERPNext Enhancements Settings' single (defaults 75/85, default OFF).
- Per-project override: one Check Custom Field on Project (ships as **FIXTURE** per repo convention), bypassing the block with an auto-logged Comment for audit.
- Unit tests in the bench-free CI suite (the repo's CI runs bench-free tests only).

## Acceptance criteria
- On TEST: a PO pushing committed+actual > 75% of the project budget produces the escalation warning/route; > 85% is blocked for non-override projects; setting the override Check on the Project allows submit and writes an audit comment.
- Feature flag OFF ⇒ zero behavior change (regression check).
- Unit tests green in CI; fixture export contains the override Custom Field.

## Rollback
Feature flag default OFF (flip off = instant restore); revert the release commit to remove the code entirely.

## Explicitly NOT in this work item
Budget population or PM process (WI-057); changes to the dollar-threshold rule (WI-013 remains the floor); Sales Order or Expense Claim escalation; retroactive evaluation of already-submitted POs.
