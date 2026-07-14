# WI-065: Rename the 'Rent' value stream to 'Events'
**Phase:** 1   **Type:** APP_CODE   **Size:** M
**Blocked by:** nothing (OD-3 resolved 2026-07-14: same stream, rename to 'Events')   **Blocks:** WI-027 (backfill uses the new name), WI-004 income-account naming, WI-022 (UAT scripts reference the stream)

## Why
OD-3 was resolved by the business on 2026-07-14: Rent and Events are the same value stream, and the term changes to **Events**. The name 'Rent' is load-bearing far beyond the Project Type master: a verified repo sweep found ~60 touch points, including SQL literals in the KPI snapshot engine (`project_type='Rent'` — these would silently return zero rentals after a bare master rename), JS constants in three dashboard surfaces, the Closed-Won handoff's stream sets, fixture `depends_on` expressions, seeded guideline prompts, and kanban color keys. The rename must land atomically (master rename + code sweep + data backfill in one release) or reporting and form logic break quietly.

## Native-first check
Native `frappe.rename_doc('Project Type', 'Rent', 'Events')` handles the master record and updates every `Project.project_type` link (61 projects) in one call — no custom rename tooling. The remainder is a code/fixture sweep in the custom app plus small data backfills; nothing native is reimplemented. Precedent honored: per the app's own Module-Def rename lesson, **internal identifiers are NOT renamed** — only user-visible names and comparison literals change.

## Preconditions
- OD-3 resolution recorded (decisions/OPEN-DECISIONS.md).
- Confirm no in-flight PR touches the same files (single atomic release).
- Pre-run counts captured on prod: `SELECT COUNT(*) FROM tabProject WHERE project_type='Rent'` (expect 61) and the KPI rentals snapshot values for later parity checks.

## Scope
**A. Master rename (DATA step, run at deploy):** patch calling `frappe.rename_doc('Project Type', 'Rent', 'Events', force=True)` (idempotent: skip if 'Events' already exists and 'Rent' doesn't).

**B. Code-literal sweep (verified inventory — every comparison/filter literal `'Rent'` → `'Events'`):**
- `crm_enhancements/api.py`: `value_stream_options` set (line ~82) and the handoff `priority_order = ["Design", "Build", "Service", "Rent"]` (line ~245).
- `kpi_dashboards/snapshots.py`: all `project_type='Rent'` SQL filters (~lines 895/903/914) + comments.
- `custom_html_blocks/projects_dashboard.js`: `PRIORITY_PROJECT_TYPES` and `VALUE_STREAM_ORDER` arrays.
- `public/js/project_enhancements/dashboard_components/priority_overview.js`: both arrays (lines ~30/297).
- `public/js/project_enhancements/project_form_script.js`: `EXTERNAL_PROJECT_TYPES`.
- `public/js/crm_enhancements/opportunity.js`: `core_tags` list (drives `_user_tags` sync).
- `public/js/crm_enhancements/opportunity_migrated_scripts.js`: the `Rent:` stream→fields map key.
- `public/js/kanban_customization.js`: color-map key `"Rent"` (keep the color).
- `project_enhancements/page/project_dashboard/project_dashboard.py` (+ its tests): `project_type in [...]` filters.
- `project_enhancements/doctype/project_contract/project_contract.py`: the stream-walker tuple `("Rent", "custom_rent_customer_requests", ...)` — change the STREAM NAME element only; fieldnames stay.
- `api/communication.py`: the `Rent: {rent_guidelines}` prompt lines → `Events: {rent_guidelines}` (template variable name stays).
- Tests referencing 'Rent' (`tests/test_project_contract.py`, `test_project_dashboard.py`) updated to 'Events'.

**C. Label/fixture sweep (user-visible text only — internal names unchanged):**
- `fixtures/custom_field.json`: `depends_on` evals (`doc.project_type == 'Rent'`, `doc.custom_service_interest=="Rent"`), labels 'Rent Schedule'/'Rent Scope'/'Rent Customer Requests'/'Rent Deliverables' → 'Events …', placeholder text. Fixture `modified` timestamps bumped (sync-skip gotcha).
- `fixtures/property_setter.json`: the scope placeholder value.
- `triton_settings.json` field LABEL 'Rent Guidelines' → 'Events Guidelines' (fieldname `rent_guidelines` stays).
- Select-option lists containing 'Rent' (e.g. Lead/Opportunity `custom_service_interest`) get 'Events' as the option.

**D. Explicitly NOT renamed (identifier stability, per the Module-Def-rename precedent):** the child DocTypes `Rent Customer Requests` / `Rent Deliverables`, all `custom_rent_*` fieldnames, the `rent_guidelines` fieldname, and python module paths. Labels change; identifiers do not.

**E. Data backfills (patch, `frappe.db.set_value`/SQL — wildcard after_save hook avoided):**
- Value-stream child rows storing 'Rent' (the `custom_value_stream` tables on Opportunity/Project) → 'Events'.
- `custom_service_interest` values 'Rent' → 'Events' on Lead/Opportunity.
- `_user_tags` containing 'Rent' on Opportunity → 'Events' (tag-sync parity).
- `seed_delivery_and_products_categories`-seeded category rows if they store 'Rent'.

## Acceptance criteria
- `SELECT COUNT(*) FROM tabProject WHERE project_type='Events'` = pre-run 'Rent' count (61 + any added since); `... WHERE project_type='Rent'` = 0.
- Repo grep: zero remaining comparison/filter literals `'Rent'` in .py/.js (labels in historical docs/process-map text may remain); CI green.
- KPI rentals snapshot values (active rentals, new-in-30d) equal the pre-rename baseline on the first post-deploy run.
- On TEST: the Opportunity value-stream field shows 'Events'; a Closed-Won handoff on an Events-stream opportunity sets `project_type='Events'`; the Projects Dashboard 'Events' column renders with the former Rent color; the Rent→Events `depends_on` sections appear on an Events project.
- `SELECT COUNT(*) FROM \`tabValue Streams\` WHERE ...='Rent'`-style checks on the child tables = 0 (exact child doctype/field verified during implementation).

## Rollback
Reverse rename (`frappe.rename_doc('Project Type', 'Events', 'Rent')`) + revert the release commit + reverse the data backfills from the pre-run export.

## Explicitly NOT in this work item
Renaming child DocTypes or any fieldname (deliberate — see D); backfilling the 71 untyped projects (WI-027); Item Group naming (WI-025 uses 'Events' where stream-named); the CoA 'Events' income account (WI-004/WI-029); any Events-vs-Rent business-process change (none — same stream, new name).
