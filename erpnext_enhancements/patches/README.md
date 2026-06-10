# `patches/` — One-time migration scripts

These are Frappe **patches**: scripts that run **once** during `bench migrate` and are recorded so they never run again. They are ordered in [`../patches.txt`](../patches.txt), which splits them into two phases:

- **`pre_model_sync`** — runs *before* DocType schema changes are applied (use for renames that must happen before the new JSON syncs).
- **`post_model_sync`** — runs *after* schema changes (the default; use for data backfills and Custom Field/Property Setter creation).

Each patch's module docstring describes what it migrates. This README is the index.

| Patch | Phase | What it migrates |
|---|---|---|
| `rename_poseidon_settings_doctype` | **pre** | Renames the Single DocType "Poseidon Settings" → **"Triton Settings"** before its JSON syncs, preserving stored config/secrets. |
| `remove_google_calendar_fields` | post | Deletes stale Google Calendar custom fields + orphaned log/map DocTypes; clears cache. |
| `rename_poseidon_service_user` | post | Renames the telephony service user `poseidon@…` → `triton@…` (or creates it) and repoints residual references (Communication.sender, owners, ToDo) and "Poseidon" branding. |
| `add_project_procurement_buttons` | post | Adds a Project "Procurement" section + 6 create-document buttons in 3 columns. |
| `reorder_procurement_buttons` | post | Re-applies the Procurement layout to fix `insert_after` ordering. |
| `reset_travel_child_table_fields` | post | Deletes `in_list_view` Property Setters on the 4 Trip child tables. |
| `add_item_purchase_links` | post | Adds `purchase_url` to Item Supplier + a read-only `purchase_links` "Buy" HTML field to Purchase Order / Material Request Item. |
| `create_home_workspace` | post | Creates the standard public "Home" Workspace if missing. |
| `migrate_assets_to_serial_no` | post | Moves "SF Water Feature" Assets onto Serial No, repoints Maintenance Records + SO Items, marks the old Assets Scrapped. |
| `migrate_contact_data` | post | Backfills the Contact/Address directory model from legacy scalar links, the Project Stakeholder child table, and legacy address fields. |
| `update_project_statuses` | post | Bulk-updates Projects with status `'Open'` → `'Active'`. |
| `delete_abandoned_doctypes` | post | Deletes the abandoned DB-only DocTypes "Materials", "Rental Status", "Water Feature Types" (unreferenced, 0–1 rows; metadata only — the orphaned tables remain until a `bench trim-database`) and the superseded "Mermaid.js Render" Client Script. |
| `seed_collab_doctypes` | post | Seeds the live-collab doctype allowlist in ERPNext Enhancements Settings and enables the feature (v1.0.0 launch list). |
| `backfill_stage_changed_on` | post | Sets `Opportunity.custom_stage_changed_on = modified` where empty so the Sales Pipeline board's days-in-stage aging starts sane; creates the Custom Field first if missing (patches run before fixture sync). |
| `seed_process_step_templates` | post | Seeds the seven PRO-0204 "Won Opportunity Hand-Off" Process Step Template records (insert-only by `step_number`, so site edits survive). |
| `backfill_project_opportunity_link` | post | Fills empty `Project.custom_opportunity` from the reverse `Opportunity.custom_created_project` stamp — the forward link was never persisted before v1.3.0 (the old mapping wrote to a non-existent `custom_sales_opportunity` field). |
| `seed_task_dashboard_block` | post | Creates the "Task Dashboard" Custom HTML Block from the repo-root `Custom HTML Block/task_dashboard.*` sources (insert-only; UI edits survive). The block must then be added to a Workspace once by hand. |
| `seed_contract_templates` | post | Creates the five Contract Template records (MSA, SOW, Owner, Rental, Maintenance) from `templates/contracts/*.html` (insert-only by `template_key`; site-side legal edits survive). |

## Important note from `patches.txt`

The legacy `delete_master_project` and `rename_master_projects` patches from the original standalone `project_enhancements` app are **intentionally not carried over**. Master Project is now a first-class, actively-used doctype (shipped DocType + the Project link field `custom_master_project` + Project Dashboard logic). Those one-time patches already ran on legacy sites; re-running them here under the new module path would delete the active doctype/field and break new installs.

## Related deletion utilities

`delete_utils.py` (repo root) and `utils/patch_delete.py` are **not** patches — they implement the runtime "Unlink and Delete" flow (enumerate blocking links, clear them, then force-delete). See `utils/patch_delete.py` and the [public README](../public/README.md) (`unlink_and_delete.js`).
