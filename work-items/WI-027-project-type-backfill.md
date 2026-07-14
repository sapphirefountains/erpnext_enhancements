# WI-027: Project type backfill — 71 untyped projects, Rent/Events reconciliation, 2 'Group Projects'
**Phase:** 1   **Type:** DATA   **Size:** S
**Blocked by:** OD-3   **Blocks:** WI-054 (project-attribute branch)

## Why
Project-stream reporting keys on `project_type`, but 71 of 625 projects are untyped, and the taxonomy question OD-3 (Rent vs Events — the app models 'Rent'; 'Events' appears nowhere) is unresolved. Two projects carry the anomalous type 'Group Projects' (prod_projects_opps: Service 348, Build 75, none 71, Rent 61, Design 47, Internal 13, Other 8, Group Projects 2).

## Native-first check
Native `Project.project_type` (Link → Project Type). Verdict: native field, DATA backfill only.

## Preconditions
- **OD-3 resolved.** Branches: (a) Rent and Events are one stream → keep 'Rent', no new type; (b) separate streams → create Project Type 'Events' and reclassify the qualifying subset of the 61 Rent projects (business-provided list); (c) Events is a sub-flavor → keep 'Rent' + tag via an existing custom field (no new fields invented here).
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
