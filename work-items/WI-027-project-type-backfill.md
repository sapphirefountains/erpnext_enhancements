# WI-027: Project type backfill — 71 untyped projects, Rent/Events reconciliation, 2 'Group Projects'
**Phase:** 1   **Type:** DATA   **Size:** S
**Blocked by:** WI-065 (OD-3 resolved 2026-07-14: one stream, renamed 'Events' — the rename itself is WI-065; run this backfill AFTER it)   **Blocks:** WI-054

## Why
Project-stream reporting keys on `project_type`, but 71 of 625 projects are untyped. OD-3 was resolved 2026-07-14: Rent and Events are ONE stream, renamed **'Events'** — WI-065 performs the rename (master + code sweep), after which the 61 former-Rent projects already carry `project_type='Events'`. This item backfills the 71 untyped rows (typing rentals/events work as 'Events') and dispositions the 2 anomalous 'Group Projects' rows (prod_projects_opps baseline: Service 348, Build 75, none 71, Rent→Events 61, Design 47, Internal 13, Other 8, Group Projects 2).

## Native-first check
Native `Project.project_type` (Link → Project Type). Verdict: native field, DATA backfill only.

## Preconditions
- **OD-3 resolved (2026-07-14): one stream, renamed 'Events'.** WI-065 deployed (Project Type renamed, code literals swept) — verify `SELECT COUNT(*) FROM tabProject WHERE project_type='Rent'` = 0 before starting; the classification worksheet uses 'Events' as the type name for rental/event work.
- Ops produces the classification worksheet for the 71 untyped rows (name, customer, era, proposed type) and a disposition for the 2 'Group Projects' rows (expected: re-home to 'Internal' or convert their role to the existing `Project.custom_master_project` / Master Project doctype linkage — field verified in prod_projects_opps).

## Scope
- `frappe.db.set_value('Project', name, 'project_type', <type>)` for the 71 + reclass list (hazard H1 — the wildcard `'*'` after_save Triton sync hook — plus Project's heavy on_update hooks: contact sync, dashboard realtime, payment-received notify — repo_app_inventory — all bypassed by db.set_value).

## Acceptance criteria
- `SELECT COUNT(*) FROM tabProject WHERE project_type IS NULL OR project_type=''` = 0.
- `SELECT COUNT(*) FROM tabProject WHERE project_type='Group Projects'` = 0.
- Branch (b) only: `SELECT COUNT(*) FROM tabProject WHERE project_type='Events'` equals the ratified list count.

## Rollback
Keyed restore from the pre-run export of (name, project_type).

## Explicitly NOT in this work item
Budget/estimated_costing population (Phase-2 budget discipline per rule 5); project status cleanup ('Paid'/'Canceled' anomalies); expected_end_date backfill.
