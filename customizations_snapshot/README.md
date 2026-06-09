# Customizations Snapshot

A version-controlled snapshot of **all manual Customize Form customizations** on the
live Sapphire Fountains ERPNext site — every Custom Field and Property Setter that was
created by a person (via Customize Form or the UI) rather than by an installed app.

**This directory is record-keeping only.** It sits outside the app package's
`erpnext_enhancements/fixtures/` directory, so nothing in here is imported, synced, or
applied to any site by `bench migrate`. It is Phase 1 of moving all customizations into
version control; Phase 2 (a separate, deliberate change) will promote this content into
the auto-synced `fixtures/` directory and make the repo the source of truth.

| File | Records | Contents |
|---|---|---|
| `custom_field.json` | 425 | Manual Custom Fields (largest: Project 115, Opportunity 66, Lead 50, Customer 34, Task 27, Contact 23) |
| `property_setter.json` | 349 | Manual Property Setters (largest: Opportunity 82, Project 59, Lead 40, Customer 38, Contact 28) |

## How this was exported

- Pulled from the live site on **2026-06-09** via the Frappe Assistant Core MCP (read-only).
- Scope: `is_system_generated = 0`, minus the explicit exclusions below.
- Records sorted by `name`; serialized like `bench export-fixtures` output
  (`indent=1`, sorted keys) so a future server-side re-export diffs cleanly.
- Volatile metadata stripped: `modified`, `modified_by`, `creation`, `owner`, `idx`,
  user tags/comments/assignments. Everything else is kept verbatim — including `null`
  values — so that a future fixture import reproduces the records exactly.

To refresh the snapshot: ask Claude to re-run the export via the MCP with the same
filters, ordering, and exclusions (this README is the spec).

## Excluded records (manual-flagged, but owned elsewhere)

These six records have `is_system_generated = 0` but were created by installed apps or
the framework, not by us. They are deliberately **not** in the snapshot and must not be
added to fixtures — the owning app manages them:

| Record | Owner |
|---|---|
| `User-user_category` | `lms` app (module-tagged LMS) |
| `User-verify_terms` | `lms` app (install batch 2026-06-08 17:25) |
| `User-hide_my_private_information_from_others` | `lms` app (install batch) |
| `User-assistant_enabled` | `frappe_assistant_core` app (install batch) |
| `Sapphire Maintenance Record-workflow_state` | auto-created by Frappe's workflow engine |
| `LMS Certificate-main-default_print_format` (Property Setter) | `lms` app (its doctype, its print format) |

## Overlaps with content already synced from this repo

Some records in this snapshot are *also* managed by existing repo mechanisms. The
snapshot intentionally includes them (it is the complete live picture); Phase 2 must
de-duplicate ownership:

- **`erpnext_enhancements/fixtures/custom_field.json`** (61 records, curated list +
  all-Project filter) and **`fixtures/property_setter.json`** (2 records).
- **`erpnext_enhancements/crm_enhancements/custom/opportunity.json`** and
  **`project_enhancements/custom/project.json`** — `export_customizations` files,
  auto-synced on migrate.

## Findings to resolve in Phase 2

1. **`Project-total_expense_claim` must leave the fixtures hook scope.** It is in
   `fixtures/custom_field.json` today, but on the site it is `is_system_generated = 1`
   — it belongs to HRMS. It was swept in by the broad `dt = Project` fixture filter.
2. **`fixtures/custom_field.json` has drifted from the live site** (7 records):
   `insert_after` for the comments tab/field on Project, Task, and Lead has since been
   moved in the UI (e.g. Task tab now after `template_task`, not `description`), and
   `Project-custom_master_project` no longer sets `show_title_field_in_link`. The live
   values in this snapshot are the current truth; refresh the fixture from it.
3. **`fixtures/property_setter.json`** (Project status options/default) matches the
   live site exactly and is subsumed by this snapshot.

## Oddities (faithfully captured, no action needed)

- `Account-customer_type-mandatory`: the record *name* disagrees with its content
  (`doc_type = Customer`, `property = options`) — the record was evidently hand-edited
  on the site after creation. Imports fine; the name is just historical.
