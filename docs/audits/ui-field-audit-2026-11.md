# UI field audit: unused, duplicate and inconsistent fields (November 2026)

Nik, 2026-11-08: "do a full audit of the UI of ERPNext and look for inconsistencies and duplicate
fields and unused fields and recommend fields to get rid of. Like the Scope Rank, Schedule Rank, and
Budget Rank is never used in practicality."

This document is the audit. It covers every Custom Field and Property Setter Sapphire added or
changed on the Desk (the repo's fixtures plus what exists on prod and not in the repo), and the own
fields of Sapphire Maintenance Record, the one custom DocType among the ten most-used. It
recommends, per field, **Keep**, **Hide**, **Merge into** another field, **Remove** or **Fix
label**, each backed by a prod fill rate or a reference count. **It removes nothing.** The removal
plan at the end follows the repo's two-step rule (fixture change plus a `frappe.delete_doc` patch)
and names the PRs in order.

Evidence was read from prod on 2026-11-08 (frappe 16.53.0, erpnext 16.52.1, this app 1.798.0) with
read-only `SELECT`s through the ERPNext connector. Nothing on prod was changed.

## Summary

| Measure | Count |
|---|---|
| Custom Fields audited (prod) | 924 |
| of which in the repo's fixtures | 677 |
| of which on prod but not in fixtures | 247 |
| Sapphire Maintenance Record own fields audited | 44 |
| Property Setters audited (prod) | 667 |
| Recommended **Remove** | 137 (89 data fields, 48 layout/HTML) |
| Recommended **Merge** | 27 |
| Recommended **Hide** | 7 |
| Recommended **Fix label / options** | 10 |
| Not Sapphire's (ERPNext, payments, FAC, framework): leave | 34 |
| Keep | 753 |

## How to read the evidence

- **Filled / rows**: non-empty values over the table's row count. `''` and `NULL` count as empty;
  for Check, Int, Float, Currency and Percent fields a `0` counts as empty too, so a Check column
  reports how many rows are ticked. For Table fields the count is the number of parent rows that
  have at least one child row. "no column" means the Custom Field exists but `bench migrate` never
  added the column (none of those turned up outside layout fields).
- **Last value edit**: the latest `modified` of a row holding a value. Many legacy Project fields
  show 2026-10-12, which is a migration that re-saved every project, not a person typing.
- **Versions 180d**: `tabVersion` rows in the last 180 days whose `data` mentions the fieldname,
  and how many of those were saved by someone other than Administrator. A field with values but
  zero human versions is written by automation (QuickBooks, Stripe, Drive, the Zoho import), not
  by people, and the Evidence column says so.
- **Read by**: files in this repo that mention the fieldname, not counting fixtures, patches,
  `setup/`, `hooks.py`, tests, READMEs and form-kit help text (those create or document a field,
  they do not use it), plus anything on prod that consumes it: the saved list settings, Kanban
  cards, Dashboard Charts, Number Cards, Notifications, Report Builder reports, Print Formats and
  Web Forms. Reference counts are by fieldname, so a name shared across DocTypes (`primary_contact`,
  `custom_utm_source`) carries the whole family's count; the Evidence column says which doctype's
  code actually reads it where that matters.
- Every legacy Desk **Server Script (30) and Client Script (36 of 37)** on prod is disabled; their
  logic was moved into the app (`script_migrations/`, `public/js/**/_migrated_scripts.js`). A field
  referenced only by those disabled scripts counts as unreferenced.
- **Rules applied where no judgement call was needed**: a field some live code reads or writes is
  Keep (with "no row holds a value yet" when fill is 0); a field with no live reference and no value
  on any row is Remove; under 2% filled with no human edit in 180 days is Remove (archive the values
  first); under 10% filled is Hide; the rest is Keep. Layout breaks follow the fields under them:
  a section or tab whose every field is removed is removed with them, a hidden section with nothing
  under it is removed, and a visible one with nothing custom under it stays (it hosts standard
  fields moved by a `field_order` setter). Every other row carries a hand-written reason.

## Nik's example: Scope Rank, Schedule Rank, Budget Rank

The fieldnames are `custom_scope_rank`, `custom_schedule_rank` and `custom_budget_rank`: three
Select fields (options 1, 2, 3) that exist twice, on Opportunity and on Project.

| Where | Rows | Ranked | Versions (all time) | Last person edit | What writes them | What reads them |
|---|---|---|---|---|---|---|
| Opportunity | 855 | 206 (24%) | 122 | 2026-11-06 | people, on the form, the Kanban's Closed Won side panel and the guided flow's Close step | nothing downstream |
| Project | 668 | 97 (14.5%) | 10 | 2026-07-01 | only the Opportunity to Project hand-off copy (`crm_enhancements/api.py`, `direct_mappings`) | nothing |

Why they get filled anyway: `hooks.py` runs `script_migrations.opportunity.validate_ranks_on_won`
on every Opportunity save, and it refuses Closed Won unless all three are 1, 2 or 3 (a Service
Call's deal is exempt since v1.778.0). So of the 312 Closed Won opportunities, 105 are ranked, and
of the 40 created in the last 180 days, all 40 are ranked: the field is filled under compulsion.
The form kit's Closed Won step, the Opportunity board's "won" side panel and the migrated client
script (unique-rank check, `reqd` toggle) exist only to satisfy that validator. The two option
lists are the only consumers; no report, chart, Kanban card, Number Card, Notification, Print
Format, dashboard block or page reads a rank value. The Projects Dashboard ranks by
`custom_company_priority`, which is a different field.

**Recommendation: Remove all six**, together with the code that exists only for them:
`validate_ranks_on_won` (and its `hooks.py` entry), the rank functions in
`public/js/crm_enhancements/opportunity_migrated_scripts.js`, the three `CLOSED_WON` entries, the
`won.fields` list and the `distinct` rule in `form_kit/config/opportunity.py`, the three entries in
`form_kit/help_text/opportunity.json`, the three `direct_mappings` lines in `crm_enhancements/api.py`,
and the tests that assert the rank gate (`tests/test_form_kit.py`, `test_opportunity_board_panel.py`,
`test_opportunity_board_focus.py`, `test_service_calls.py`). Data: 206 Opportunities and 97 Projects
hold a value; export them to a CSV attached to the removal PR before the patch runs. This is PR 1 of
the removal plan because it is the clearest case and it unblocks Closed Won without a ritual.

## Recommendations by DocType

Recommendation words: **Keep** (used by people or by live code), **Hide** (rarely
used, keep the data, take it off the form), **Merge into X** (same meaning under two names; move
the values, then remove), **Remove** (nothing reads it; the removal plan says what to archive),
**Fix label** (keep, but the label is wrong or inconsistent), **Leave** (not Sapphire's field:
ERPNext, the payments app, Frappe Assistant Core or the Workflow engine created it).

The ten most-used DocTypes come first (by 90-day human edits: Task, Project, Opportunity, Customer,
Supplier, Item, Purchase Order, Sapphire Maintenance Record, Material Request, Stock Entry), then
the rest alphabetically.

### Task (8,703 rows on prod)

52 fields: Keep 38, Remove 14.

Task's one dead feature is recurring tasks: `custom_is_recurring` is unticked on all 8,703 rows, so the `Daily` in `custom_frequency` (2,923 rows) and the `1` in `custom_repeat_every` (required, 8,703 rows) are defaults, not choices. Everything else with a low fill rate is a planner feature that shipped in October (crew, equipment, baselines, customer confirmations, rental logistics) and has live code behind it.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_rental_booking` | Rental Booking | Link | read-only | 0/8703 (0%) |  | 0 (0 by people) | 16 code | **Keep** | 0/8,703; rental logistics writes them |
| `custom_rental_task_kind` | Rental Task | Select | read-only | 0/8703 (0%) |  | 0 (0 by people) | 5 code | **Keep** | 0/8,703; rental logistics writes them; values: (empty) 8703 |
| `custom_rental_digest_sent_on` | Crew Digest Sent On | Date | hidden, read-only | 0/8703 (0%) |  | 0 (0 by people) | 1 code | **Keep** | 0/8,703; rental logistics writes them |
| `custom_subtask_order` | Subtask Order | Float | read-only | 1049/8703 (12%) | 2026-11-08 | 1388 (1388 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_child_tasks` | Child Tasks | Section Break |  | - |  |  | none | **Keep** | holds 2 custom field(s) |
| `custom_create_child_task_btn` | + Create Child Task | Button |  | - |  |  | 1 code | **Keep** | rendered by code (1 files) |
| `custom_child_tasks_table` | Child Tasks Table | HTML |  | - |  |  | 3 code | **Keep** | rendered by code (3 files) |
| `custom_section_break_8wegw` |  | Section Break |  | - |  |  | none | **Remove** | unlabeled section with no fields under it |
| `custom_locationaddress_of_task` | Location/Address of Task | Link |  | 75/8703 (0.9%) | 2026-10-12 | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_map_placeholder` | Map Placeholder | HTML |  | - |  |  | 2 code | **Keep** | rendered by code (2 files) |
| `custom_zoho_task_id` | Zoho Task ID | Data | hidden, read-only | 5153/8703 (59%) | 2026-11-03 | 0 (0 by people) | none | **Keep** | hidden import key, 5,153/8,703 |
| `custom_enhancement_request` | Enhancement Request | Link | read-only | 51/8703 (0.6%) | 2026-11-05 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_timeline` | Timeline | Tab Break |  | - |  |  | none | **Keep** | tab that hosts the moved standard date fields (field_order setter) |
| `custom_start_datetime` | Start Datetime | Datetime |  | 31/8703 (0.4%) | 2026-10-12 | 0 (0 by people) | 9 code | **Keep** | code reads/writes it (9 files) |
| `custom_crew_section` | Crew | Section Break |  | - |  |  | setup/patch only | **Keep** | holds 8 custom field(s) |
| `custom_end_datetime` | End Datetime | Datetime |  | 31/8703 (0.4%) | 2026-10-12 | 0 (0 by people) | 9 code | **Keep** | code reads/writes it (9 files) |
| `custom_planner_template_section` | Planner Template | Section Break |  | - |  |  | 1 code | **Keep** | holds 5 custom field(s) |
| `custom_template_line` | Template Line | Select |  | 30/8703 (0.3%) | 2026-10-13 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); values: (empty) 8673, Labor 12, Subcontract 11, Project management 7 |
| `custom_cost_code` | Cost Code | Data |  | 30/8703 (0.3%) | 2026-10-13 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_phase_number` | Phase Number | Int |  | 7/8703 (0.1%) | 2026-10-13 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_phase_runs_after` | Runs After Phases | Data |  | 6/8703 (0.1%) | 2026-10-13 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_procurement_checklist` | Procurement Checklist | Small Text |  | 7/8703 (0.1%) | 2026-10-13 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_crew_size` | Crew needed | Int |  | 51/8703 (0.6%) | 2026-10-30 | 1 (1 by people) | 7 code | **Keep** | code reads/writes it (7 files) |
| `custom_tentative` | Tentative (pencil) | Check |  | 49/8703 (0.6%) | 2026-10-30 | 1 (1 by people) | 7 code | **Keep** | code reads/writes it (7 files) |
| `custom_required_credentials` | Qualifications needed | Table MultiSelect |  | 0/8703 (0%) |  |  | 4 code | **Keep** | 0/8,703 so far; planner features that write them shipped in October |
| `custom_outdoor` | Outdoor work | Check |  | 2/8703 (0.0%) | 2026-10-21 | 0 (0 by people) | 6 code | **Keep** | code reads/writes it (6 files) |
| `custom_customer_visit` | Customer-facing visit | Check |  | 0/8703 (0%) |  | 0 (0 by people) | 5 code | **Keep** | 0/8,703 so far; planner features that write them shipped in October |
| `custom_customer_confirmation_due` | Customer Confirmation Due | Date | hidden, read-only | 0/8703 (0%) |  | 0 (0 by people) | 1 code | **Keep** | 0/8,703 so far; planner features that write them shipped in October |
| `custom_customer_confirmed_for` | Customer Confirmed For | Date | hidden, read-only | 0/8703 (0%) |  | 0 (0 by people) | 1 code | **Keep** | 0/8,703 so far; planner features that write them shipped in October |
| `custom_crew_column` |  | Column Break |  | - |  |  | setup/patch only | **Keep** | layout |
| `custom_crew` | Crew | Table |  | 42/8703 (0.5%) | 2026-11-06 |  | 6 code | **Keep** | code reads/writes it (6 files) |
| `custom_equipment_section` | Equipment | Section Break |  | - |  |  | setup/patch only | **Keep** | 0/8,703 so far; planner features that write them shipped in October |
| `custom_equipment` | Vehicles and equipment | Table |  | 0/8703 (0%) |  |  | 5 code | **Keep** | 0/8,703 so far; planner features that write them shipped in October |
| `custom_baseline_section` | Baseline | Section Break |  | - |  |  | setup/patch only | **Keep** | 0/8,703; the planner's baseline feature writes them |
| `custom_automation_settings` | Automation Settings | Section Break |  | - |  |  | setup/patch only | **Remove** | recurring-task feature never used: custom_is_recurring is 0 on all 8,703 tasks, custom_frequency and custom_repeat_every only hold their defaults, the weekday checks are all 0, so tasks.py's next-task hook never fires |
| `custom_baseline_start` | Baseline start | Date | read-only | 0/8703 (0%) |  | 0 (0 by people) | 4 code | **Keep** | 0/8,703; the planner's baseline feature writes them |
| `custom_baseline_column` |  | Column Break |  | - |  |  | setup/patch only | **Keep** | 0/8,703; the planner's baseline feature writes them |
| `custom_baseline_end` | Baseline end | Date | read-only | 0/8703 (0%) |  | 0 (0 by people) | 1 code | **Keep** | 0/8,703; the planner's baseline feature writes them |
| `custom_is_recurring` | Is Recurring | Check |  | 0/8703 (0%) |  | 0 (0 by people) | 3 code | **Remove** | recurring-task feature never used: custom_is_recurring is 0 on all 8,703 tasks, custom_frequency and custom_repeat_every only hold their defaults, the weekday checks are all 0, so tasks.py's next-task hook never fires |
| `custom_next_task_created` | Next Task Created | Link | read-only | 0/8703 (0%) |  | 0 (0 by people) | 1 code | **Remove** | recurring-task feature never used: custom_is_recurring is 0 on all 8,703 tasks, custom_frequency and custom_repeat_every only hold their defaults, the weekday checks are all 0, so tasks.py's next-task hook never fires |
| `custom_column_break_zkulk` |  | Column Break |  | - |  |  | none | **Remove** | recurring-task feature never used: custom_is_recurring is 0 on all 8,703 tasks, custom_frequency and custom_repeat_every only hold their defaults, the weekday checks are all 0, so tasks.py's next-task hook never fires |
| `custom_frequency` | Frequency | Select |  | 2923/8703 (34%) | 2026-11-08 | 0 (0 by people) | 1 code | **Remove** | recurring-task feature never used: custom_is_recurring is 0 on all 8,703 tasks, custom_frequency and custom_repeat_every only hold their defaults, the weekday checks are all 0, so tasks.py's next-task hook never fires; values: (empty) 5780, Daily 2923 |
| `custom_repeat_every` | Repeat Every | Int | required | 8703/8703 (100%) | 2026-11-08 | 0 (0 by people) | 1 code | **Remove** | recurring-task feature never used: custom_is_recurring is 0 on all 8,703 tasks, custom_frequency and custom_repeat_every only hold their defaults, the weekday checks are all 0, so tasks.py's next-task hook never fires |
| `custom_sunday` | Sunday | Check |  | 0/8703 (0%) |  | 0 (0 by people) | 1 code | **Remove** | recurring-task feature never used: custom_is_recurring is 0 on all 8,703 tasks, custom_frequency and custom_repeat_every only hold their defaults, the weekday checks are all 0, so tasks.py's next-task hook never fires |
| `custom_monday` | Monday | Check |  | 0/8703 (0%) |  | 0 (0 by people) | 1 code | **Remove** | recurring-task feature never used: custom_is_recurring is 0 on all 8,703 tasks, custom_frequency and custom_repeat_every only hold their defaults, the weekday checks are all 0, so tasks.py's next-task hook never fires |
| `custom_tuesday` | Tuesday | Check |  | 0/8703 (0%) |  | 0 (0 by people) | 1 code | **Remove** | recurring-task feature never used: custom_is_recurring is 0 on all 8,703 tasks, custom_frequency and custom_repeat_every only hold their defaults, the weekday checks are all 0, so tasks.py's next-task hook never fires |
| `custom_wednesday` | Wednesday | Check |  | 0/8703 (0%) |  | 0 (0 by people) | 1 code | **Remove** | recurring-task feature never used: custom_is_recurring is 0 on all 8,703 tasks, custom_frequency and custom_repeat_every only hold their defaults, the weekday checks are all 0, so tasks.py's next-task hook never fires |
| `custom_thursday` | Thursday | Check |  | 0/8703 (0%) |  | 0 (0 by people) | 1 code | **Remove** | recurring-task feature never used: custom_is_recurring is 0 on all 8,703 tasks, custom_frequency and custom_repeat_every only hold their defaults, the weekday checks are all 0, so tasks.py's next-task hook never fires |
| `custom_friday` | Friday | Check |  | 0/8703 (0%) |  | 0 (0 by people) | 1 code | **Remove** | recurring-task feature never used: custom_is_recurring is 0 on all 8,703 tasks, custom_frequency and custom_repeat_every only hold their defaults, the weekday checks are all 0, so tasks.py's next-task hook never fires |
| `custom_saturday` | Saturday | Check |  | 0/8703 (0%) |  | 0 (0 by people) | 1 code | **Remove** | recurring-task feature never used: custom_is_recurring is 0 on all 8,703 tasks, custom_frequency and custom_repeat_every only hold their defaults, the weekday checks are all 0, so tasks.py's next-task hook never fires |
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Project (668 rows on prod)

181 fields: Fix 6, Keep 138, Merge 5, Remove 32.

Project carries the most customization (181 Custom Fields on prod). Three families stand out: the legacy Zoho import fields (ALL-CAPS labels, written once on 2026-10-12 by a migration, read by nothing), three generations of contact/address fields (the hidden `custom_primary_*` set, the hidden ERPNext-style `custom_address_display`/`custom_contact_display` block, and the live unified Contacts & Addresses tab), and the hand-off copy of Opportunity fields (ranks, scope tables, rental dates), which is why many Project fields have values but no human Version rows. `custom_project_priority` and `custom_company_priority` share one option list that mixes ranks (1..30) with job types (Maintenance, Repair Visit, Delivery); see the inconsistencies list.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `primary_address` | Primary Address | Link | not in fixtures | 32/668 (5%) | 2026-11-06 | 23 (23 by people) | 12 code | **Keep** | code reads/writes it (12 files) |
| `custom_zoho_data` | Zoho Data | Tab Break | hidden | - |  |  | none | **Remove** | hidden tabs with no fields under them |
| `custom_master_project` | Master Project | Link |  | 80/668 (12%) | 2026-08-13 | 8 (8 by people) | 8 code | **Keep** | code reads/writes it (8 files) |
| `custom_project_id` | Project ID | Data | read-only | 220/668 (33%) | 2026-11-06 | 89 (89 by people) | 1 code; prod: Report Builder (Active Projects w/ TL and PM) | **Keep** | used on prod: Report Builder (Active Projects w/ TL and PM) |
| `custom_value_stream` | Value Stream | Table MultiSelect |  | 138/668 (21%) | 2026-11-06 |  | 21 code | **Keep** | code reads/writes it (21 files) |
| `custom_feature_this_job` | Feature This Job | Check |  | 1/668 (0.1%) | 2026-10-30 | 1 (1 by people) | 2 code | **Keep** | Featured Jobs report and fountain-move jobs read them; 1/668 each so far |
| `custom_feature_note` | Feature Note | Small Text |  | 0/668 (0%) |  | 0 (0 by people) | 1 code | **Keep** | Featured Jobs report and fountain-move jobs read them; 1/668 each so far |
| `custom_is_fountain_move` | Fountain Move | Check |  | 1/668 (0.1%) | 2026-10-21 | 0 (0 by people) | 1 code | **Keep** | Featured Jobs report and fountain-move jobs read them; 1/668 each so far |
| `custom_project_description` | Project Description | Data |  | 566/668 (85%) | 2026-11-06 | 78 (78 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_project_priority` | Project Priority | Select | list | 666/668 (100%) | 2026-11-06 | 36 (36 by people) | 4 code | **Fix options** | 666/668 each; the shared option list mixes a rank (1..30) with job types (Maintenance 33/21, Repair Visit 146/17, Delivery); move the type values to project_type and keep the Selects numeric; values: Not Assigned 350, Repair Visit 146, Maintenance 33, 10 28, 1 19, 3 19 |
| `custom_company_priority` | Company Priority | Select | list | 666/668 (100%) | 2026-11-06 | 35 (35 by people) | 6 code | **Fix options** | 666/668 each; the shared option list mixes a rank (1..30) with job types (Maintenance 33/21, Repair Visit 146/17, Delivery); move the type values to project_type and keep the Selects numeric; values: Not Assigned 584, Maintenance 21, Repair Visit 17, 7 5, 10 3, 11 3 |
| `custom_project_owner` | Project Manager | Link |  | 466/668 (70%) | 2026-11-06 | 129 (129 by people) | 18 code; prod: Report Builder (Active Projects w/ TL and PM) | **Keep** | used on prod: Report Builder (Active Projects w/ TL and PM) |
| `custom_technical_lead` | Technical Lead | Link |  | 113/668 (17%) | 2026-11-06 | 143 (143 by people) | 6 code; prod: Report Builder (Active Projects w/ TL and PM) | **Keep** | used on prod: Report Builder (Active Projects w/ TL and PM) |
| `custom_column_break_l026q` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_scope_rank` | Scope Rank | Select |  | 97/668 (15%) | 2026-11-06 | 2 (2 by people) | 4 code | **Remove** | 97/668 rows (14.5%) hold a value and every one came from the Opportunity hand-off copy (crm_enhancements/api.py); 10 Version rows ever, last 2026-07-01; nothing reads them (the Projects Dashboard ranks by custom_company_priority); values: (empty) 571, 1 57, 2 25, 3 15 |
| `custom_schedule_rank` | Schedule Rank | Select |  | 97/668 (15%) | 2026-11-06 | 2 (2 by people) | 4 code | **Remove** | 97/668 rows (14.5%) hold a value and every one came from the Opportunity hand-off copy (crm_enhancements/api.py); 10 Version rows ever, last 2026-07-01; nothing reads them (the Projects Dashboard ranks by custom_company_priority); values: (empty) 571, 3 41, 2 34, 1 22 |
| `custom_budget_rank` | Budget Rank | Select |  | 97/668 (15%) | 2026-11-06 | 2 (2 by people) | 4 code | **Remove** | 97/668 rows (14.5%) hold a value and every one came from the Opportunity hand-off copy (crm_enhancements/api.py); 10 Version rows ever, last 2026-07-01; nothing reads them (the Projects Dashboard ranks by custom_company_priority); values: (empty) 571, 3 41, 2 38, 1 18 |
| `custom_drive_folder_id` | Drive Folder ID | Data | hidden | 260/668 (39%) | 2026-11-06 | 1 (1 by people) | 11 code | **Keep** | code reads/writes it (11 files) |
| `primary_contact_section` | Primary Contact | Section Break | not in fixtures | - |  |  | 1 code | **Keep** | no custom field under it; hosts moved standard fields |
| `custom_drive_folder_missing` | Drive Folder Missing | Check | hidden, read-only | 11/668 (2%) | 2026-11-06 | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `primary_contact` | Full Name | Link | not in fixtures | 21/668 (3%) | 2026-11-06 | 13 (13 by people) | 14 code | **Keep** | code reads/writes it (14 files) |
| `section_break_contacts` | Contact Directory | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `primary_contact_job_title` | Job Title | Data | not in fixtures | 7/668 (1%) | 2026-11-06 | 3 (3 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_reminder_action` | Reminder Action | HTML |  | - |  |  | 4 code | **Keep** | rendered by code (4 files) |
| `primary_contact_col_break` |  | Column Break | not in fixtures | - |  |  | none | **Keep** | layout |
| `primary_contact_phone` | Phone | Data | not in fixtures | 8/668 (1%) | 2026-11-06 | 3 (3 by people) | 4 code | **Keep** | code reads/writes it (4 files) |
| `custom_contacts__addresses` | Contacts & Addresses | Tab Break |  | - |  |  | setup/patch only | **Keep** | holds 5 custom field(s) |
| `primary_contact_email` | Email Address | Data | not in fixtures | 5/668 (0.7%) | 2026-11-06 | 4 (4 by people) | 6 code | **Keep** | code reads/writes it (6 files) |
| `address_and_contact` | Address and Contact | HTML | not in fixtures | - |  |  | 1 code | **Keep** | rendered by code (1 files) |
| `contact_list_html` | Contact List HTML | HTML | not in fixtures | - |  |  | 5 code | **Keep** | rendered by code (5 files) |
| `section_break_map` | Location | Section Break | not in fixtures | - |  |  | 2 code | **Keep** | holds 3 custom field(s) |
| `location_map_html` | Location Map HTML | HTML | not in fixtures | - |  |  | 2 code | **Keep** | rendered by code (2 files) |
| `location_map_col_break` |  | Column Break | not in fixtures | - |  |  | setup/patch only | **Keep** | layout |
| `section_break_address_list` | Address Directory | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `address_list_html` | Address List HTML | HTML | not in fixtures | - |  |  | 6 code | **Keep** | rendered by code (6 files) |
| `custom_billing_address` | Billing Address | Link |  | 4/668 (0.6%) | 2026-11-05 | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files) |
| `custom_primary_contact_field` | Primary Contact | Section Break | hidden | - |  |  | none | **Remove** | 0/668 on all four; superseded by the unified Contacts & Addresses tab (primary_contact_*); desk_search.py, travel.py and normalize_fields.py still list them and need the same edit |
| `custom_primary_first_name` | Full Name | Data |  | 0/668 (0%) |  | 0 (0 by people) | 2 code | **Remove** | 0/668 on all four; superseded by the unified Contacts & Addresses tab (primary_contact_*); desk_search.py, travel.py and normalize_fields.py still list them and need the same edit |
| `custom_primary_role` | Job Title | Data |  | 0/668 (0%) |  | 0 (0 by people) | none | **Remove** | 0/668 on all four; superseded by the unified Contacts & Addresses tab (primary_contact_*); desk_search.py, travel.py and normalize_fields.py still list them and need the same edit |
| `custom_column_break_k8xix` |  | Column Break |  | - |  |  | none | **Remove** | 0/668 on all four; superseded by the unified Contacts & Addresses tab (primary_contact_*); desk_search.py, travel.py and normalize_fields.py still list them and need the same edit |
| `custom_primary_phone` | Phone | Data |  | 0/668 (0%) |  | 0 (0 by people) | 4 code | **Remove** | 0/668 on all four; superseded by the unified Contacts & Addresses tab (primary_contact_*); desk_search.py, travel.py and normalize_fields.py still list them and need the same edit |
| `custom_primary_email_address` | Email Address | Data |  | 0/668 (0%) |  | 0 (0 by people) | 4 code | **Remove** | 0/668 on all four; superseded by the unified Contacts & Addresses tab (primary_contact_*); desk_search.py, travel.py and normalize_fields.py still list them and need the same edit |
| `custom_contacts__address` |  | Section Break | hidden | - |  |  | none | **Fix label** | hidden, unlabeled section around the Stakeholders table |
| `custom_contacts__address_table` | Table | Table |  | 32/668 (5%) | 2026-11-03 |  | 1 code | **Fix label** | label is 'Table'; 32/668 projects have stakeholder rows (Project Stakeholder), still written by the hand-off copy |
| `custom_address__contact` | Address & Contact | Section Break | hidden | - |  |  | none | **Remove** | dead copy of ERPNext's address/contact block: 2/0 of 668 filled, HTML never rendered (no code), section hidden |
| `custom_address_html` | Address HTML | HTML |  | - |  |  | none | **Remove** | dead copy of ERPNext's address/contact block: 2/0 of 668 filled, HTML never rendered (no code), section hidden |
| `custom_customer__lead_address` | Customer / Lead Address | Link |  | 1/668 (0.1%) | 2026-10-12 | 0 (0 by people) | 7 code | **Merge into primary_address** | 1/668; calendar sync, pickup routing and workforce/sites.py read it as a second site-address field |
| `custom_address_display` | Address Display | Small Text |  | 2/668 (0.3%) | 2026-10-12 | 0 (0 by people) | none | **Remove** | dead copy of ERPNext's address/contact block: 2/0 of 668 filled, HTML never rendered (no code), section hidden |
| `custom_column_break_em6sv` |  | Column Break |  | - |  |  | none | **Remove** | dead copy of ERPNext's address/contact block: 2/0 of 668 filled, HTML never rendered (no code), section hidden |
| `custom_contact_html` | Contact HTML | HTML |  | - |  |  | none | **Remove** | dead copy of ERPNext's address/contact block: 2/0 of 668 filled, HTML never rendered (no code), section hidden |
| `custom_contact_display` | Contact Display | Small Text |  | 0/668 (0%) |  | 0 (0 by people) | none | **Remove** | dead copy of ERPNext's address/contact block: 2/0 of 668 filled, HTML never rendered (no code), section hidden |
| `custom_site_location_section` | Site Location | Section Break |  | - |  |  | setup/patch only | **Keep** | holds 4 custom field(s) |
| `custom_site_latitude` | Site Latitude | Float |  | 73/668 (11%) | 2026-10-12 | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_site_longitude` | Site Longitude | Float |  | 73/668 (11%) | 2026-10-12 | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_site_location_source` | Site Location Source | Select |  | 73/668 (11%) | 2026-10-12 | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); values: (empty) 595, Geocoded 73 |
| `custom_site_geocoded_from` | Geocoded From | Data | read-only | 73/668 (11%) | 2026-10-12 | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_opportunity` | Opportunity | Link |  | 119/668 (18%) | 2026-11-06 | 0 (0 by people) | 20 code | **Keep** | code reads/writes it (20 files) |
| `custom_scope` | Scope | Tab Break |  | - |  |  | 2 code | **Keep** | no custom field under it; hosts moved standard fields |
| `custom_gantt_chart_section` | Gantt Chart | Section Break |  | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `custom_tasks_section` | Tasks Tree | Section Break |  | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `custom_tasks_html` | Tasks Tree View | HTML |  | - |  |  | 2 code | **Keep** | rendered by code (2 files) |
| `custom_section_break_s5jqn` |  | Section Break |  | - |  |  | none | **Remove** | unlabeled section with no fields under it |
| `custom_section_break_w4acr` | Scope Description | Section Break | hidden | - |  |  | setup/patch only | **Keep** | holds 4 custom field(s) |
| `custom_general_scope_description` | Detailed Scope Description | Text Editor |  | 319/668 (48%) | 2026-11-06 | 7 (7 by people) | 6 code | **Fix label** | labeled 'Detailed Scope Description' here and 'General Scope Description' on Opportunity for the same copied field |
| `custom_scope_contributors` | Scope Contributors | Table |  | 1/668 (0.1%) | 2026-10-09 |  | 3 code | **Remove** | 1/668 (and 1/855 on Opportunity); Nik 2026-10-13 kept it hidden; only the hand-off copy and an info block list it |
| `custom_design_scope` | Design Scope | Section Break |  | - |  |  | 2 code | **Keep** | holds 2 custom field(s) |
| `custom_scope_of_work` | Scope of Work | Link | read-only | 0/668 (0%) |  | 0 (0 by people) | 2 code | **Keep** | 0/668; written by Project Scope of Work locking (new) |
| `custom_scope_locked_on` | Scope Locked On | Datetime | read-only | 0/668 (0%) |  | 0 (0 by people) | 1 code | **Keep** | 0/668; written by Project Scope of Work locking (new) |
| `custom_design_customer_requests` | Design Customer Requests | Table |  | 14/668 (2%) | 2026-11-06 |  | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_column_break_qbuwv` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_design_deliverables` | Design Deliverables | Table |  | 9/668 (1%) | 2026-10-23 |  | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_build_scope` | Build Scope | Section Break |  | - |  |  | 2 code | **Keep** | holds 2 custom field(s) |
| `custom_build_customer_requests` | Build Customer Requests | Table |  | 5/668 (0.7%) | 2026-10-23 |  | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_column_break_w4xqd` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_build_deliverables` | Build Deliverables | Table |  | 3/668 (0.4%) | 2026-10-23 |  | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_service_scope` | Service Scope | Section Break |  | - |  |  | 2 code | **Keep** | holds 2 custom field(s) |
| `custom_production_section` | Production Tracking | Section Break | hidden | - |  |  | setup/patch only | **Keep** | hidden Production Tracking section; 0 rows yet but project_brief.py and the quality module read them |
| `custom_build_status` | Build Status | Select |  | 1/668 (0.1%) | 2026-10-12 | 1 (1 by people) | 4 code; prod: Kanban 'Production Builds' | **Keep** | 1/668; drives the 'Production Builds' Kanban and quality scheduling; values: (empty) 667, Installed 1 |
| `custom_bid_cost` | Bid Cost | Currency |  | 0/668 (0%) |  | 0 (0 by people) | 3 code | **Keep** | hidden Production Tracking section; 0 rows yet but project_brief.py and the quality module read them |
| `custom_column_break_production` |  | Column Break |  | - |  |  | setup/patch only | **Keep** | hidden Production Tracking section; 0 rows yet but project_brief.py and the quality module read them |
| `custom_production_started` | Production Started | Date |  | 0/668 (0%) |  | 0 (0 by people) | 1 code | **Keep** | hidden Production Tracking section; 0 rows yet but project_brief.py and the quality module read them |
| `custom_production_completed` | Production Completed | Date |  | 0/668 (0%) |  | 0 (0 by people) | 1 code | **Keep** | hidden Production Tracking section; 0 rows yet but project_brief.py and the quality module read them |
| `custom_quality_sign_off_by` | Quality Sign-Off By | Link |  | 0/668 (0%) |  | 0 (0 by people) | 1 code | **Keep** | hidden Production Tracking section; 0 rows yet but project_brief.py and the quality module read them |
| `custom_quality_sign_off_date` | Quality Sign-Off Date | Date |  | 0/668 (0%) |  | 0 (0 by people) | 1 code | **Keep** | hidden Production Tracking section; 0 rows yet but project_brief.py and the quality module read them |
| `custom_service_customer_requests` | Service Customer Requests | Table |  | 18/668 (3%) | 2026-11-03 |  | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_column_break_xu1wk` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_service_deliverables` | Service Deliverables | Table |  | 3/668 (0.4%) | 2026-10-09 |  | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_rent_scope` | Events Scope | Section Break |  | - |  |  | 2 code | **Keep** | holds 2 custom field(s) |
| `custom_rent_customer_requests` | Events Customer Requests | Table |  | 4/668 (0.6%) | 2026-11-06 |  | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_column_break_tbqyo` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_rent_deliverables` | Events Deliverables | Table |  | 1/668 (0.1%) | 2026-08-19 |  | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_schedule` | Schedule | Tab Break |  | - |  |  | 2 code | **Keep** | no custom field under it; hosts moved standard fields |
| `custom_schedule_state_section` | Planner schedule | Section Break | hidden | - |  |  | setup/patch only | **Keep** | holds 3 custom field(s) |
| `custom_schedule_status` | Schedule status | Select | read-only | 668/668 (100%) | 2026-11-06 | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files); values: Draft 611, Published 57 |
| `custom_schedule_published_on` | Schedule published on | Datetime | read-only | 0/668 (0%) |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); no row holds a value yet |
| `custom_schedule_state_column` |  | Column Break |  | - |  |  | setup/patch only | **Keep** | layout |
| `custom_baseline_set_on` | Baseline set on | Datetime | read-only | 0/668 (0%) |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); no row holds a value yet |
| `custom_project_start_date` | Requested Start Date | Date |  | 15/668 (2%) | 2026-11-06 | 4 (4 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_project_end_date` | Requested End Date | Date |  | 11/668 (2%) | 2026-11-06 | 3 (3 by people) | 4 code | **Keep** | code reads/writes it (4 files) |
| `custom_column_break_aukmw` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_column_break_dyph7` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_rent_schedule` | Events Schedule | Section Break |  | - |  |  | 1 code | **Keep** | holds 8 custom field(s) |
| `custom_delivery_date_time` | Delivery Date Time | Datetime |  | 12/668 (2%) | 2026-11-06 | 2 (2 by people) | 8 code | **Keep** | code reads/writes it (8 files) |
| `custom_delivery_date_time_notes` | Delivery Date Time Notes | Small Text |  | 10/668 (1%) | 2026-11-06 | 3 (3 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_column_break_iuiqz` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_setup_date_time` | Setup Date Time | Datetime |  | 13/668 (2%) | 2026-11-06 | 3 (3 by people) | 7 code | **Keep** | code reads/writes it (7 files) |
| `custom_setup_date_time_notes` | Setup Date Time Notes | Small Text |  | 8/668 (1%) | 2026-11-06 | 2 (2 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_column_break_ujl7w` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_procurement_section` | Procurement | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 6 custom field(s) |
| `custom_event_date_time` | Event Date Time | Datetime |  | 12/668 (2%) | 2026-11-06 | 2 (2 by people) | 7 code | **Keep** | code reads/writes it (7 files) |
| `custom_btn_material_request` | + Material Request | Button | not in fixtures | - |  |  | 1 code | **Keep** | rendered by code (1 files) |
| `custom_btn_request_quote` | + Request Quote | Button | not in fixtures | - |  |  | 1 code | **Keep** | rendered by code (1 files) |
| `custom_event_date_time_notes` | Event Date Time Notes | Small Text |  | 6/668 (0.9%) | 2026-10-12 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_col_break_1` |  | Column Break | not in fixtures | - |  |  | setup/patch only | **Keep** | layout |
| `custom_column_break_bhtot` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_btn_supplier_quotation` | + Supplier Quotation | Button | not in fixtures | - |  |  | 1 code | **Keep** | rendered by code (1 files) |
| `custom_btn_purchase_order` | + Purchase Order | Button | not in fixtures | - |  |  | 1 code | **Keep** | rendered by code (1 files) |
| `custom_take_down_date_time` | Take Down Date Time | Datetime |  | 12/668 (2%) | 2026-11-06 | 2 (2 by people) | 8 code | **Keep** | code reads/writes it (8 files) |
| `custom_btn_purchase_receipt` | + Purchase Receipt | Button | not in fixtures | - |  |  | 1 code | **Keep** | rendered by code (1 files) |
| `custom_take_down_date_time_notes` | Take Down Date Time Notes | Small Text |  | 7/668 (1%) | 2026-11-06 | 2 (2 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_col_break_2` |  | Column Break | not in fixtures | - |  |  | setup/patch only | **Keep** | layout |
| `custom_btn_purchase_invoice` | + Purchase Invoice | Button | not in fixtures | - |  |  | 1 code | **Keep** | rendered by code (1 files) |
| `custom_gantt_chart_html` | Gantt Chart View | HTML |  | - |  |  | 3 code | **Keep** | rendered by code (3 files) |
| `custom_section_break_nyo2c` | Schedule Notes | Section Break |  | - |  |  | none | **Keep** | holds 1 custom field(s) |
| `custom_notes_for_scheduling` | Notes for Scheduling | Text Editor |  | 37/668 (6%) | 2026-11-06 | 1 (1 by people) | 4 code | **Keep** | code reads/writes it (4 files) |
| `custom_budget` | Budget | Tab Break |  | - |  |  | setup/patch only | **Keep** | no custom field under it; hosts moved standard fields |
| `custom_section_break_kyvkd` |  | Section Break | hidden | - |  |  | none | **Remove** | every field under it is removed (1) |
| `custom_quickbooks_jobcode_id` | QuickBooks Jobcode ID | Data | read-only | 0/668 (0%) |  | 0 (0 by people) | 1 code | **Remove** | 0/668; QuickBooks Time job-code sync never wrote one (the webhook targets a missing Time Log) |
| `custom_customer_payment_section` | Customer Payment | Section Break |  | - |  |  | none | **Keep** | holds 3 custom field(s) |
| `custom_project_estimated_amount` | Project Estimated Amount | Section Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_payment_received` | Payment Received | Check |  | 1/668 (0.1%) | 2026-10-09 | 1 (1 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_payment_column_break` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_payment_received_on` | Payment Received On | Date |  | 1/668 (0.1%) | 2026-10-09 | 1 (1 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_payment_method` | Payment Method | Select |  | 1/668 (0.1%) | 2026-10-09 | 1 (1 by people) | 2 code | **Keep** | code reads/writes it (2 files); values: (empty) 667, Check 1 |
| `custom_initial_invoice_section` | Initial Invoice | Section Break |  | - |  |  | 1 code | **Keep** | holds 2 custom field(s) |
| `custom_first_invoice_percentage` | Down Payment Percentage | Percent |  | 0/668 (0%) |  | 2 (2 by people) | 4 code | **Keep** | code reads/writes it (4 files); no row holds a value yet |
| `custom_initial_invoice_column_break` |  | Column Break |  | - |  |  | setup/patch only | **Keep** | layout |
| `custom_billing_email` | Billing Email | Data |  | 0/668 (0%) |  | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files); no row holds a value yet |
| `custom_project_dollar_amount` | Project Dollar Amount | Currency |  | 81/668 (12%) | 2026-11-06 | 3 (3 by people) | 12 code | **Keep** | code reads/writes it (12 files) |
| `custom_column_break_dgj8s` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_project_spend` | Project Spend | Currency |  | 0/668 (0%) |  | 0 (0 by people) | none | **Remove** | 0/668, no code |
| `custom_total_time_elapsed` | Total Time Elapsed | Duration | read-only | 495/668 (74%) | 2026-11-06 | 9 (9 by people) | 4 code; prod: Number Card 'Average Project Completion Time' | **Keep** | used on prod: Number Card 'Average Project Completion Time' |
| `custom_project_estimated_cost` | Project Estimated Cost | Section Break |  | - |  |  | none | **Keep** | holds 3 custom field(s) |
| `custom_materials_budget` | Materials Budget | Currency |  | 5/668 (0.7%) | 2026-10-30 | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_column_break_z8y1h` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_time_budget_in_hours` | Time Budget (in hours) | Data |  | 6/668 (0.9%) | 2026-11-06 | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_column_break_chnxt` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_time__materials` | Time & Materials | Check |  | 3/668 (0.4%) | 2026-10-09 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_budget_lines_section` | Budget by Category | Section Break |  | - |  |  | none | **Keep** | 0/668 so far; quality/budgets.py reads it (budget by category, new) |
| `custom_budget_lines_intro` | Budget by Category Intro | HTML |  | - |  |  | none | **Keep** | 0/668 so far; quality/budgets.py reads it (budget by category, new) |
| `custom_budget_lines` | Budget Lines | Table |  | 0/668 (0%) |  |  | 1 code | **Keep** | 0/668 so far; quality/budgets.py reads it (budget by category, new) |
| `custom_pickup_routing_section` | Material Pickup | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `custom_btn_pick_routing_map` | Pick Routing Map | Button | not in fixtures | - |  |  | 1 code | **Keep** | rendered by code (1 files) |
| `custom_section_break_xbww4` |  | Section Break |  | - |  |  | none | **Keep** | holds 1 custom field(s) |
| `custom_material_request_feed` | Material Request Feed | HTML | read-only | - |  |  | 2 code | **Keep** | rendered by code (2 files) |
| `custom_budget_notes` | Budget Notes | Section Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_notes_relating_to_budget` | Notes Relating to Budget | Text Editor |  | 9/668 (1%) | 2026-11-06 | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_process_tab` | Hand-Off Process | Tab Break |  | - |  |  | 1 code | **Keep** | holds 3 custom field(s) |
| `custom_process_progress` | Process Progress | HTML |  | - |  |  | 3 code | **Keep** | rendered by code (3 files) |
| `custom_process_steps` | Process Steps | Table |  | 113/668 (17%) | 2026-11-06 |  | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_launch_event` | Launch Meeting Event | Link | read-only | 0/668 (0%) |  | 0 (0 by people) | 1 code | **Keep** | 0/668; hand-off writes it when a launch meeting is booked |
| `custom_misc` | Misc | Tab Break | hidden | - |  |  | none | **Remove** | hidden tabs with no fields under them |
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |
| `custom_section_break_unn4x` |  | Section Break |  | - |  |  | none | **Remove** | legacy Project Notes table (hidden, 124/668, last write 2026-10-12) replaced by the Comments App; archive the rows as Comments first |
| `custom_project_address` | PROJECT ADDRESS | Data |  | 79/668 (12%) | 2026-10-12 | 1 (1 by people) | 9 code | **Fix label; then Merge into primary_address** | 79/668, ALL-CAPS label; free-text Zoho address that pickup routing and calendar sync fall back to when primary_address is empty |
| `custom_customer_name` | CUSTOMER NAME | Data |  | 118/668 (18%) | 2026-10-30 | 0 (0 by people) | 3 code | **Merge into customer** | 118/668 duplicates the standard Customer link; no human edit in 180 days |
| `custom_customer_phone` | CUSTOMER PHONE | Data |  | 6/668 (0.9%) | 2026-10-12 | 0 (0 by people) | 3 code | **Merge into primary_contact_phone / primary_contact_email** | 6/2/1 of 668; code reads them only as a last-resort fallback (travel.py, project_contract.py, customer_confirmations.py) |
| `custom_customer_email` | CUSTOMER EMAIL | Data |  | 2/668 (0.3%) | 2026-10-12 | 0 (0 by people) | 5 code | **Merge into primary_contact_phone / primary_contact_email** | 6/2/1 of 668; code reads them only as a last-resort fallback (travel.py, project_contract.py, customer_confirmations.py) |
| `custom_contact_phone` | CONTACT PHONE | Data |  | 1/668 (0.1%) | 2026-10-12 | 0 (0 by people) | 3 code | **Merge into primary_contact_phone / primary_contact_email** | 6/2/1 of 668; code reads them only as a last-resort fallback (travel.py, project_contract.py, customer_confirmations.py) |
| `custom_zoho_creation_date` | Zoho Creation Date | Datetime | hidden, read-only | 343/668 (51%) | 2026-10-30 | 0 (0 by people) | 2 code | **Keep** | hidden, read-only import keys (343 and 456 of 668); keep for traceability |
| `custom_column_break_vp5qd` |  | Column Break |  | - |  |  | none | **Remove** | legacy Project Notes table (hidden, 124/668, last write 2026-10-12) replaced by the Comments App; archive the rows as Comments first |
| `custom_deliverable1` | DELIVERABLE-1 | Small Text |  | 33/668 (5%) | 2026-10-12 | 0 (0 by people) | none | **Remove** | Zoho import leftovers with ALL-CAPS labels; 33/22/13/7/2 of 668 filled, last write 2026-10-12 (a migration), no code reads them; archive the text to the Comments App first |
| `custom_deliverable2` | DELIVERABLE-2 | Small Text |  | 22/668 (3%) | 2026-10-12 | 0 (0 by people) | none | **Remove** | Zoho import leftovers with ALL-CAPS labels; 33/22/13/7/2 of 668 filled, last write 2026-10-12 (a migration), no code reads them; archive the text to the Comments App first |
| `custom_deliverable3` | DELIVERABLE-3 | Small Text |  | 13/668 (2%) | 2026-10-12 | 0 (0 by people) | none | **Remove** | Zoho import leftovers with ALL-CAPS labels; 33/22/13/7/2 of 668 filled, last write 2026-10-12 (a migration), no code reads them; archive the text to the Comments App first |
| `custom_deliverable4` | DELIVERABLE-4 | Small Text |  | 7/668 (1%) | 2026-10-12 | 0 (0 by people) | none | **Remove** | Zoho import leftovers with ALL-CAPS labels; 33/22/13/7/2 of 668 filled, last write 2026-10-12 (a migration), no code reads them; archive the text to the Comments App first |
| `custom_deliverable5` | DELIVERABLE-5 | Small Text |  | 2/668 (0.3%) | 2026-10-12 | 0 (0 by people) | none | **Remove** | Zoho import leftovers with ALL-CAPS labels; 33/22/13/7/2 of 668 filled, last write 2026-10-12 (a migration), no code reads them; archive the text to the Comments App first |
| `custom_column_break_8lbte` |  | Column Break |  | - |  |  | none | **Remove** | legacy Project Notes table (hidden, 124/668, last write 2026-10-12) replaced by the Comments App; archive the rows as Comments first |
| `custom_zoho_projects_id` | Zoho Projects ID | Data | hidden, read-only | 456/668 (68%) | 2026-10-30 | 0 (0 by people) | none | **Keep** | hidden, read-only import keys (343 and 456 of 668); keep for traceability |
| `custom_notes_html` | Notes HTML | HTML |  | - |  |  | none | **Remove** | legacy Project Notes table (hidden, 124/668, last write 2026-10-12) replaced by the Comments App; archive the rows as Comments first |
| `custom_comments_table` | Comments Table | Table | hidden | 124/668 (19%) | 2026-10-12 |  | 1 code | **Remove** | legacy Project Notes table (hidden, 124/668, last write 2026-10-12) replaced by the Comments App; archive the rows as Comments first |

### Opportunity (855 rows on prod)

117 fields: Fix 2, Keep 104, Merge 4, Remove 7.

Opportunity has three description fields (`custom_opportunity_summary`, `custom_general_scope_description` and the hidden legacy `custom_description`) and two links to the created Project. 582 of 855 rows are historical imports (`custom_is_historical`), so fill rates on fields added in 2026 should be read against the ~270 live deals. The attribution fields (`custom_utm_*`, `custom_gclid`, landing page, referrer) are 0/855 while `custom_attribution_captured_on` is 34/855: the website capture fires but the visits carried no UTM tags. They are automation fields and stay.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `primary_address` | Primary Address | Link | not in fixtures | 39/855 (5%) | 2026-11-07 | 38 (38 by people) | 12 code | **Keep** | code reads/writes it (12 files) |
| `custom_drive_folder_id` | Drive Folder ID | Data | hidden, read-only | 843/855 (99%) | 2026-11-08 | 2 (2 by people) | 11 code | **Keep** | code reads/writes it (11 files) |
| `custom_drive_folder_missing` | Drive Folder Missing | Check | hidden, read-only | 1/855 (0.1%) | 2026-07-10 | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_value_stream` | Value Stream | Table MultiSelect | required | 309/855 (36%) | 2026-11-08 |  | 21 code | **Keep** | code reads/writes it (21 files) |
| `custom_reminder_action` | Reminder Action | HTML |  | - |  |  | 4 code | **Keep** | rendered by code (4 files) |
| `custom_opportunity_name` | Opportunity Name | Data | required, list | 855/855 (100%) | 2026-11-08 | 79 (79 by people) | 16 code; prod: two personal Report Builder reports (Closed Won, Sales Past Week) | **Keep** | used on prod: two personal Report Builder reports (Closed Won, Sales Past Week) |
| `custom_opportunity_summary` | Opportunity Summary | Data |  | 323/855 (38%) | 2026-11-08 | 170 (170 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_lead_source` | Lead Source | Link |  | 827/855 (97%) | 2026-11-08 | 4 (4 by people) | 13 code | **Keep** | code reads/writes it (13 files) |
| `custom_scope_rank` | Scope Rank | Select |  | 206/855 (24%) | 2026-11-08 | 45 (45 by people) | 4 code | **Remove** | 206/855 filled; every Closed Won since May is ranked only because validate_ranks_on_won refuses the save without them; no report, chart, Kanban or page reads the value; values: (empty) 649, 1 125, 2 53, 3 28 |
| `custom_schedule_rank` | Schedule Rank | Select |  | 206/855 (24%) | 2026-11-08 | 45 (45 by people) | 4 code | **Remove** | 206/855 filled; every Closed Won since May is ranked only because validate_ranks_on_won refuses the save without them; no report, chart, Kanban or page reads the value; values: (empty) 649, 3 88, 2 80, 1 38 |
| `custom_budget_rank` | Budget Rank | Select |  | 206/855 (24%) | 2026-11-08 | 45 (45 by people) | 4 code | **Remove** | 206/855 filled; every Closed Won since May is ranked only because validate_ranks_on_won refuses the save without them; no report, chart, Kanban or page reads the value; values: (empty) 649, 3 90, 2 73, 1 43 |
| `custom_date_closed_won` | Date Closed Won | Date | read-only | 119/855 (14%) | 2026-11-07 | 95 (93 by people) | 19 code; prod: a personal Report Builder report (Closed Won) | **Keep** | used on prod: a personal Report Builder report (Closed Won) |
| `custom_stage_changed_on` | Stage Changed On | Datetime | read-only | 855/855 (100%) | 2026-11-08 | 223 (223 by people) | 7 code | **Keep** | code reads/writes it (7 files) |
| `custom_is_historical` | Historical (pre-ERPNext) | Check | read-only | 582/855 (68%) | 2026-11-02 | 1 (1 by people) | 6 code; prod: Dashboard Charts and Number Cards filter on it | **Keep** | used on prod: Dashboard Charts and Number Cards filter on it |
| `custom_guided_client_id` | Guided Entry Create ID | Data | hidden, read-only | 15/855 (2%) | 2026-11-06 | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_snoozed_until` | Snoozed Until | Date | hidden | 0/855 (0%) |  | 0 (0 by people) | 1 code | **Keep** | 0/855 so far; the Opportunity board's snooze writes them |
| `custom_snooze_reason` | Snooze Reason | Small Text | hidden | 0/855 (0%) |  | 0 (0 by people) | 1 code | **Keep** | 0/855 so far; the Opportunity board's snooze writes them |
| `custom_probability_by_hand` | Probability Set by Hand | Check | not in fixtures | 10/855 (1%) | 2026-11-06 | 2 (2 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_project` | Project | Link | hidden | 16/855 (2%) | 2026-10-21 | 0 (0 by people) | 12 code | **Merge into custom_created_project** | hidden duplicate link to the created Project: 16/855 vs 116/855 on custom_created_project |
| `custom_created_project` | Created Project | Link |  | 116/855 (14%) | 2026-11-07 | 52 (0 by people) | 17 code | **Keep** | code reads/writes it (17 files) |
| `custom_attribution_section` | Attribution | Section Break |  | - |  |  | 1 code | **Keep** | holds 7 custom field(s) |
| `custom_utm_source` | UTM Source (raw) | Data | read-only | 0/855 (0%) |  | 0 (0 by people) | 9 code | **Keep** | code reads/writes it (9 files); no row holds a value yet |
| `custom_utm_medium` | UTM Medium (raw) | Data | read-only | 0/855 (0%) |  | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files); no row holds a value yet |
| `custom_utm_campaign` | UTM Campaign (raw) | Data | read-only | 0/855 (0%) |  | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files); no row holds a value yet |
| `custom_utm_id` | UTM ID (Campaign ID) | Data | read-only | 0/855 (0%) |  | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files); no row holds a value yet |
| `custom_column_break_attribution` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `primary_contact` | Full Name | Link | not in fixtures | 189/855 (22%) | 2026-11-08 | 33 (33 by people) | 14 code; prod: Kanban 'Opportunity' card | **Keep** | used on prod: Kanban 'Opportunity' card |
| `custom_utm_content` | UTM Content (raw) | Data | read-only | 0/855 (0%) |  | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files); no row holds a value yet |
| `custom_utm_term` | UTM Term | Data | read-only | 0/855 (0%) |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); no row holds a value yet |
| `primary_contact_job_title` | Job Title | Data | not in fixtures | 34/855 (4%) | 2026-11-08 | 35 (33 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_gclid` | Google Click ID | Data | read-only | 0/855 (0%) |  | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files); no row holds a value yet |
| `section_break_contacts` | Contact Directory | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `primary_contact_col_break` |  | Column Break | not in fixtures | - |  |  | none | **Keep** | layout |
| `primary_contact_phone` | Phone | Data | not in fixtures | 42/855 (5%) | 2026-11-06 | 39 (39 by people) | 4 code | **Keep** | code reads/writes it (4 files) |
| `custom_landing_page` | Landing Page | Small Text | read-only | 0/855 (0%) |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); no row holds a value yet |
| `primary_contact_email` | Email Address | Data | not in fixtures | 45/855 (5%) | 2026-11-08 | 36 (36 by people) | 6 code | **Keep** | code reads/writes it (6 files) |
| `custom_first_referrer` | First Referrer | Small Text | read-only | 0/855 (0%) |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); no row holds a value yet |
| `address_and_contact` | Address and Contact | HTML | not in fixtures | - |  |  | 1 code | **Keep** | rendered by code (1 files) |
| `custom_attribution_captured_on` | Attribution Captured On | Datetime | read-only | 34/855 (4%) | 2026-11-08 | 27 (26 by people) | 6 code | **Keep** | code reads/writes it (6 files) |
| `contact_list_html` | Contact List HTML | HTML | not in fixtures | - |  |  | 5 code | **Keep** | rendered by code (5 files) |
| `section_break_map` | Location | Section Break | not in fixtures | - |  |  | 2 code | **Keep** | holds 2 custom field(s) |
| `location_map_html` | Location Map HTML | HTML | not in fixtures | - |  |  | 2 code | **Keep** | rendered by code (2 files) |
| `location_map_col_break` |  | Column Break | not in fixtures | - |  |  | setup/patch only | **Keep** | layout |
| `section_break_address_list` | Address Directory | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `address_list_html` | Address List HTML | HTML | not in fixtures | - |  |  | 6 code | **Keep** | rendered by code (6 files) |
| `custom_zoho_crm_opportunity_id` | Zoho CRM Opportunity ID | Data | hidden, read-only | 575/855 (67%) | 2026-11-06 | 0 (0 by people) | setup/patch only | **Keep** | hidden import key, 575/855 |
| `custom_travel_trip` | Created from Travel Trip | Link | hidden, read-only | 0/855 (0%) |  | 0 (0 by people) | 4 code | **Keep** | 0/855; Travel Trip creates Opportunities through it |
| `custom_opportunity_description` | Opportunity Description | Section Break | hidden | - |  |  | none | **Merge into custom_general_scope_description** | hidden Text Editor with 288/855 legacy values; third description field beside custom_opportunity_summary (323) and custom_general_scope_description (115) |
| `custom_description` | Description | Text Editor | hidden | 288/855 (34%) | 2026-11-08 | 126 (126 by people) | 2 code | **Merge into custom_general_scope_description** | hidden Text Editor with 288/855 legacy values; third description field beside custom_opportunity_summary (323) and custom_general_scope_description (115) |
| `custom_column_break_yvxkd` |  | Column Break |  | - |  |  | none | **Merge into custom_general_scope_description** | hidden Text Editor with 288/855 legacy values; third description field beside custom_opportunity_summary (323) and custom_general_scope_description (115) |
| `custom_contacts__address` |  | Section Break | hidden | - |  |  | none | **Fix label** | hidden, unlabeled section around the Stakeholders table |
| `custom_contact_mobile_number` | Contact Mobile Number | Data | hidden, read-only, list | 88/855 (10%) | 2026-11-07 | 17 (17 by people) | 2 code; prod: Kanban 'Opportunity' card; two personal Report Builder reports (Closed Won, Sales Past Week) | **Keep** | hidden fetch used by the Kanban card and two Report Builder reports |
| `custom_contacts__address_table` | Table | Table |  | 18/855 (2%) | 2026-11-06 |  | 1 code | **Fix label** | label is 'Table'; 18/855 rows (Project Stakeholder) |
| `custom_scope` | Scope | Tab Break |  | - |  |  | 2 code | **Keep** | no custom field under it; hosts moved standard fields |
| `custom_section_break_zozu1` |  | Section Break | hidden | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_general_scope_description` | General Scope Description | Text Editor |  | 115/855 (13%) | 2026-11-08 | 143 (143 by people) | 6 code | **Keep** | code reads/writes it (6 files) |
| `custom_section_break_ieovn` |  | Section Break | hidden | - |  |  | 1 code | **Remove** | 1/855; Nik 2026-10-13 kept it hidden; nothing reads it but the hand-off copy |
| `custom_scope_contributors` | Scope Contributors | Table |  | 1/855 (0.1%) | 2026-06-30 |  | 3 code | **Remove** | 1/855; Nik 2026-10-13 kept it hidden; nothing reads it but the hand-off copy |
| `custom_design_scope` | Design Scope | Section Break |  | - |  |  | 2 code | **Keep** | holds 2 custom field(s) |
| `custom_design_customer_requests` | Design Customer Requests | Table |  | 46/855 (5%) | 2026-11-06 |  | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_column_break_kb4nu` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_design_deliverables` | Design Deliverables | Table |  | 7/855 (0.8%) | 2026-11-04 |  | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_build_scope` | Build Scope | Section Break |  | - |  |  | 2 code | **Keep** | holds 2 custom field(s) |
| `custom_build_customer_requests` | Build Customer Requests | Table |  | 11/855 (1%) | 2026-10-30 |  | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_column_break_rsv2q` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_build_deliverables` | Build Deliverables | Table |  | 2/855 (0.2%) | 2026-10-30 |  | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_service_scope` | Service Scope | Section Break |  | - |  |  | 2 code | **Keep** | holds 2 custom field(s) |
| `custom_service_customer_requests` | Service Customer Requests | Table |  | 35/855 (4%) | 2026-11-06 |  | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_column_break_unfie` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_service_deliverables` | Service Deliverables | Table |  | 1/855 (0.1%) | 2026-06-30 |  | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_rent_scope` | Events Scope | Section Break |  | - |  |  | 2 code | **Keep** | holds 2 custom field(s) |
| `custom_rent_customer_requests` | Events Customer Requests | Table |  | 20/855 (2%) | 2026-10-05 |  | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_column_break_uu0wc` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_rent_deliverables` | Events Deliverables | Table |  | 0/855 (0%) |  |  | 5 code | **Keep** | code reads/writes it (5 files); no row holds a value yet |
| `custom_schedule` | Schedule | Tab Break |  | - |  |  | 2 code | **Keep** | holds 2 custom field(s) |
| `custom_project_start_date` | Requested Start Date | Date |  | 55/855 (6%) | 2026-11-06 | 14 (14 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_column_break_scfkx` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_project_end_date` | Requested End Date | Date |  | 40/855 (5%) | 2026-11-06 | 10 (10 by people) | 4 code | **Keep** | code reads/writes it (4 files) |
| `custom_schedule_notes` | Schedule Notes | Section Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_notes_for_scheduling` | Notes for Scheduling | Text Editor |  | 103/855 (12%) | 2026-11-08 | 106 (106 by people) | 4 code | **Keep** | code reads/writes it (4 files) |
| `custom_rent` | Events Schedule | Section Break |  | - |  |  | 2 code | **Keep** | holds 8 custom field(s) |
| `custom_delivery_date_time` | Delivery Date Time | Datetime |  | 11/855 (1%) | 2026-08-10 | 0 (0 by people) | 8 code | **Keep** | code reads/writes it (8 files) |
| `custom_delivery_date_time_notes` | Delivery Date Time Notes | Small Text |  | 7/855 (0.8%) | 2026-08-10 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_column_break_uysp3` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_setup_date_time` | Setup Date Time | Datetime |  | 10/855 (1%) | 2026-08-10 | 0 (0 by people) | 7 code | **Keep** | code reads/writes it (7 files) |
| `custom_setup_date_time_notes` | Setup Date Time Notes | Small Text |  | 6/855 (0.7%) | 2026-08-10 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_column_break_z4bpu` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_event_date_time` | Event Date Time | Datetime |  | 11/855 (1%) | 2026-08-10 | 0 (0 by people) | 7 code | **Keep** | code reads/writes it (7 files) |
| `custom_event_date_time_notes` | Event Date Time Notes | Small Text |  | 7/855 (0.8%) | 2026-08-10 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_column_break_l03lu` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_take_down_date_time` | Take Down Date Time | Datetime |  | 10/855 (1%) | 2026-08-10 | 0 (0 by people) | 8 code | **Keep** | code reads/writes it (8 files) |
| `custom_take_down_date_time_notes` | Take Down Date Time Notes | Small Text |  | 4/855 (0.5%) | 2026-08-10 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_budget` | Budget | Tab Break |  | - |  |  | setup/patch only | **Keep** | no custom field under it; hosts moved standard fields |
| `custom_customer_budget` | Opportunity Amount | Section Break |  | - |  |  | 1 code | **Keep** | no custom field under it; hosts moved standard fields |
| `custom_column_break_safj2` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_estimated_cost` | Estimated Cost | Currency | hidden | 2/855 (0.2%) | 2026-06-30 | 0 (0 by people) | 1 code | **Remove** | hidden, 2/855, only the hand-off copy mentions it |
| `custom_materials_budget` | Materials Budget | Currency |  | 5/855 (0.6%) | 2026-10-21 | 2 (2 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_time_budget_in_hours` | Time Budget (in hours) | Data |  | 10/855 (1%) | 2026-10-13 | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_time__materials` | Time & Materials | Check | hidden | 3/855 (0.4%) | 2026-06-30 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_budget_notes` | Budget Notes | Section Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_notes_relating_to_budget` | Notes Relating to Budget | Text Editor |  | 123/855 (14%) | 2026-11-08 | 118 (118 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_initial_invoice_section` | Initial Invoice | Section Break |  | - |  |  | 1 code | **Keep** | holds 2 custom field(s) |
| `custom_first_invoice_percentage` | Down Payment Percentage | Percent |  | 0/855 (0%) |  | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files); no row holds a value yet |
| `custom_initial_invoice_column_break` |  | Column Break |  | - |  |  | setup/patch only | **Keep** | layout |
| `custom_billing_email` | Billing Email | Data |  | 0/855 (0%) |  | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files); no row holds a value yet |
| `custom_process_tab` | Hand-Off Process | Tab Break |  | - |  |  | 1 code | **Keep** | holds 10 custom field(s) |
| `custom_process_progress` | Process Progress | HTML |  | - |  |  | 3 code | **Keep** | rendered by code (3 files) |
| `custom_handoff_meeting_held` | Hand-Off Meeting Held | Check | read-only | 25/855 (3%) | 2026-11-07 | 1 (1 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_handoff_meeting_on` | Hand-Off Meeting Recorded On | Datetime | read-only | 25/855 (3%) | 2026-11-07 | 1 (1 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_handoff_meeting_by` | Hand-Off Recorded By | Link | read-only | 25/855 (3%) | 2026-11-07 | 1 (1 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_handoff_event` | Hand-Off Meeting Event | Link | read-only | 19/855 (2%) | 2026-11-07 | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_handoff_skip_reason` | Hand-Off Skip Reason | Small Text | read-only | 5/855 (0.6%) | 2026-11-03 | 1 (1 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_handoff_due_by` | Hand-Off Due By | Datetime | read-only | 26/855 (3%) | 2026-11-07 | 26 (26 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_launch_deadline` | Project Launch Deadline | Datetime | read-only | 91/855 (11%) | 2026-11-07 | 26 (26 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_handoff_gate_applies` | Hand-Off Gate Applies | Check | read-only | 27/855 (3%) | 2026-11-07 | 27 (27 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_handoff_last_reminded_on` | Hand-Off Last Reminded On | Datetime | hidden, read-only | 10/855 (1%) | 2026-11-04 | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_section_break_bqypn` | Tasks and Events | Section Break |  | - |  |  | none | **Remove** | 'Tasks and Events' section with no fields under it |

### Customer (1,722 rows on prod)

72 fields: Keep 57, Leave 1, Merge 1, Remove 13.

Customer is relabeled 'Account' on prod. It carries the hidden `custom_primary_*` contact set (0 to 3 rows each) that the unified Contacts & Addresses tab replaced, plus a hidden Read Only `custom_phone` fetch that nothing displays. The Stripe and dunning fields were created by `stripe_payments/setup.py` on migrate and are not in fixtures; one customer has a Stripe id.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_service_hold` | Service Hold | Check | not in fixtures | 0/1722 (0%) |  | 0 (0 by people) | 4 code | **Keep** | 0/1722; dunning, missed visits and the planner read it |
| `custom_stripe_section` | Stripe | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 5 custom field(s) |
| `exempt_from_sales_tax` | Is customer exempted from sales tax? | Check | not in fixtures | 0/1722 (0%) |  | 0 (0 by people) | none | **Leave (ERPNext)** | ERPNext US regional field, not Sapphire's |
| `custom_stripe_customer_id` | Stripe Customer ID | Data | read-only, not in fixtures | 1/1722 (0.1%) | 2026-07-27 | 0 (0 by people) | 2 code | **Keep** | automation writes it (2 files) |
| `custom_stripe_column_break` |  | Column Break | not in fixtures | - |  |  | setup/patch only | **Keep** | layout |
| `custom_stripe_autopay_enabled` | Stripe Autopay Enabled | Check | not in fixtures | 0/1722 (0%) |  | 0 (0 by people) | 9 code | **Keep** | code reads/writes it (9 files); no row holds a value yet |
| `custom_stripe_payment_method_label` | Saved Method | Data | read-only, not in fixtures | 0/1722 (0%) |  | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files); no row holds a value yet |
| `custom_stripe_default_payment_method` | Stripe Saved Payment Method | Data | read-only, not in fixtures | 0/1722 (0%) |  | 0 (0 by people) | 7 code | **Keep** | code reads/writes it (7 files); no row holds a value yet |
| `custom_accounts_phone_number` | Account Phone Number | Data |  | 421/1722 (24%) | 2026-11-07 | 38 (38 by people) | 12 code | **Keep** | code reads/writes it (12 files) |
| `custom_drive_folder_id` | Drive Folder ID | Data | hidden, read-only | 1722/1722 (100%) | 2026-11-07 | 21 (19 by people) | 11 code | **Keep** | code reads/writes it (11 files) |
| `custom_drive_folder_missing` | Drive Folder Missing | Check | hidden, read-only | 13/1722 (0.8%) | 2026-11-07 | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_value_stream` | Value Stream | Table MultiSelect |  | 539/1722 (31%) | 2026-11-07 |  | 21 code | **Keep** | code reads/writes it (21 files) |
| `custom_prospect` | Prospect | Check |  | 16/1722 (0.9%) | 2026-11-06 | 15 (15 by people) | 3 code | **Merge into custom_account_status** | 16/1722 checked; custom_account_status already has a 'Prospect' option (124 filled) and the standard prospect_name link is hidden |
| `custom_parent_account` | Parent Account | Link |  | 5/1722 (0.3%) | 2026-08-19 | 1 (1 by people) | setup/patch only | **Remove** | 5/1722, no code, no human edit in 180 days |
| `custom_account_status` | Account Status | Select | list | 124/1722 (7%) | 2026-11-07 | 7 (7 by people) | 7 code | **Keep** | code reads/writes it (7 files); values: (empty) 1598, Prospect 52, Lead 43, Client 14, Champion 8, Vendor 4 |
| `custom_accounts_email_address` | Account Email Address | Data |  | 67/1722 (4%) | 2026-11-05 | 10 (10 by people) | 8 code | **Keep** | code reads/writes it (8 files) |
| `custom_preferred_contact_method` | Preferred Contact Method | Select |  | 880/1722 (51%) | 2026-11-06 | 4 (4 by people) | 1 code | **Keep** | code reads/writes it (1 files); values: Email 869, (empty) 842, Phone 8, Text 3 |
| `custom_lead_source` | Lead Source | Link | hidden | 732/1722 (43%) | 2026-11-07 | 2 (2 by people) | 13 code | **Keep** | hidden but 732/1722 filled; attribution reads it |
| `custom_description` | Description | Small Text |  | 621/1722 (36%) | 2026-11-07 | 27 (27 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_attribution_section` | Attribution | Section Break |  | - |  |  | 1 code | **Keep** | holds 10 custom field(s) |
| `custom_utm_source` | UTM Source (raw) | Data |  | 0/1722 (0%) |  | 0 (0 by people) | 9 code | **Keep** | code reads/writes it (9 files); no row holds a value yet |
| `custom_utm_medium` | UTM Medium (raw) | Data |  | 0/1722 (0%) |  | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files); no row holds a value yet |
| `custom_utm_campaign` | UTM Campaign (raw) | Data |  | 0/1722 (0%) |  | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files); no row holds a value yet |
| `custom_utm_id` | UTM ID (Campaign ID) | Data |  | 0/1722 (0%) |  | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files); no row holds a value yet |
| `custom_column_break_attribution` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_utm_content` | UTM Content (raw) | Data |  | 0/1722 (0%) |  | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files); no row holds a value yet |
| `custom_utm_term` | UTM Term | Data |  | 0/1722 (0%) |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); no row holds a value yet |
| `custom_gclid` | Google Click ID | Data |  | 0/1722 (0%) |  | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files); no row holds a value yet |
| `custom_landing_page` | Landing Page | Small Text |  | 0/1722 (0%) |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); no row holds a value yet |
| `custom_first_referrer` | First Referrer | Small Text |  | 0/1722 (0%) |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); no row holds a value yet |
| `primary_contact_section` | Primary Contact | Section Break | not in fixtures | - |  |  | 1 code | **Keep** | no custom field under it; hosts moved standard fields |
| `custom_attribution_captured_on` | Attribution Captured On | Datetime | read-only | 36/1722 (2%) | 2026-11-05 | 1 (1 by people) | 6 code | **Keep** | code reads/writes it (6 files) |
| `primary_contact` | Full Name | Link | not in fixtures | 35/1722 (2%) | 2026-11-04 | 28 (28 by people) | 14 code | **Keep** | code reads/writes it (14 files) |
| `section_break_contacts` | Contact Directory | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `primary_contact_job_title` | Job Title | Data | not in fixtures | 8/1722 (0.5%) | 2026-11-04 | 13 (13 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `primary_contact_col_break` |  | Column Break | not in fixtures | - |  |  | none | **Keep** | layout |
| `primary_contact_phone` | Phone | Data | not in fixtures | 8/1722 (0.5%) | 2026-11-04 | 12 (12 by people) | 4 code | **Keep** | code reads/writes it (4 files) |
| `contact_list_html` | Contact List HTML | HTML | not in fixtures | - |  |  | 5 code | **Keep** | rendered by code (5 files) |
| `primary_contact_email` | Email Address | Data | not in fixtures | 13/1722 (0.8%) | 2026-11-02 | 15 (15 by people) | 6 code | **Keep** | code reads/writes it (6 files) |
| `section_break_map` | Location | Section Break | not in fixtures | - |  |  | 2 code | **Keep** | holds 1 custom field(s) |
| `address_and_contact` | Address and Contact | HTML | not in fixtures | - |  |  | 1 code | **Keep** | rendered by code (1 files) |
| `location_map_html` | Location Map HTML | HTML | not in fixtures | - |  |  | 2 code | **Keep** | rendered by code (2 files) |
| `custom_section_break_evswr` | Other Links | Section Break |  | - |  |  | none | **Keep** | read-only link lists maintained by customer.js; they stand in for the hidden Connections tab |
| `custom_projects` | Projects | Table MultiSelect | read-only | 82/1722 (5%) | 2026-11-07 |  | 1 code | **Keep** | read-only link lists maintained by customer.js; they stand in for the hidden Connections tab |
| `custom_leads` | Leads | Table MultiSelect | read-only | 0/1722 (0%) |  |  | 1 code | **Remove** | 0/1722 rows in the read-only Leads list |
| `custom_column_break_xxa0r` |  | Column Break |  | - |  |  | none | **Keep** | read-only link lists maintained by customer.js; they stand in for the hidden Connections tab |
| `custom_opportunities` | Opportunities | Table MultiSelect | read-only | 211/1722 (12%) | 2026-11-07 |  | 2 code | **Keep** | read-only link lists maintained by customer.js; they stand in for the hidden Connections tab |
| `custom_activity_reminder` | Activity Reminder | Section Break |  | - |  |  | none | **Keep** | holds 3 custom field(s) |
| `custom_reminder_days` | Reminder Days | Int |  | 40/1722 (2%) | 2026-11-07 | 18 (18 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_column_break_nqigy` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_last_activity_date` | Last Activity Date | Date | read-only | 1341/1722 (78%) | 2026-11-07 | 2993 (2541 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_reminder_assignee` | Reminder Assignee | Link |  | 9/1722 (0.5%) | 2026-11-07 | 1 (1 by people) | 1 code | **Keep** | automation writes it (1 files) |
| `location_map_col_break` |  | Column Break | not in fixtures | - |  |  | setup/patch only | **Keep** | layout |
| `section_break_address_list` | Address Directory | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `address_list_html` | Address List HTML | HTML | not in fixtures | - |  |  | 6 code | **Keep** | rendered by code (6 files) |
| `custom_billing_address` | Billing Address | Link |  | 63/1722 (4%) | 2026-11-07 | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files) |
| `custom_shipping_address` | Shipping Address | Link |  | 25/1722 (1%) | 2026-10-26 | 0 (0 by people) | setup/patch only | **Remove** | 25/1722, no live code (only the 2026 contact-data migration); the Address Directory lists the addresses |
| `custom_map_placeholder` | Map Placeholder | HTML |  | - |  |  | 2 code | **Keep** | rendered by code (2 files) |
| `custom_contacts` | Contacts | Section Break | hidden | - |  |  | none | **Remove** | 3/2/1/1 of 1722; superseded by primary_contact_* (desk_search.py, travel.py and normalize_fields.py still list them) |
| `custom_primary_contact` | Primary Contact | Section Break | hidden | - |  |  | none | **Remove** | 3/2/1/1 of 1722; superseded by primary_contact_* (desk_search.py, travel.py and normalize_fields.py still list them) |
| `custom_primary_first_name` | Full Name | Data |  | 3/1722 (0.2%) | 2026-06-23 | 0 (0 by people) | 2 code | **Remove** | 3/2/1/1 of 1722; superseded by primary_contact_* (desk_search.py, travel.py and normalize_fields.py still list them) |
| `custom_primary_position` | Job Title | Data |  | 2/1722 (0.1%) | 2026-04-08 | 0 (0 by people) | none | **Remove** | 3/2/1/1 of 1722; superseded by primary_contact_* (desk_search.py, travel.py and normalize_fields.py still list them) |
| `custom_column_break_z913g` |  | Column Break |  | - |  |  | none | **Remove** | 3/2/1/1 of 1722; superseded by primary_contact_* (desk_search.py, travel.py and normalize_fields.py still list them) |
| `custom_primary_phone` | Phone | Data |  | 1/1722 (0.1%) | 2026-06-23 | 0 (0 by people) | 4 code | **Remove** | 3/2/1/1 of 1722; superseded by primary_contact_* (desk_search.py, travel.py and normalize_fields.py still list them) |
| `custom_primary_email_address` | Email Address | Data |  | 1/1722 (0.1%) | 2026-04-08 | 0 (0 by people) | 4 code | **Remove** | 3/2/1/1 of 1722; superseded by primary_contact_* (desk_search.py, travel.py and normalize_fields.py still list them) |
| `custom_section_break_bhxic` |  | Section Break | hidden | - |  |  | none | **Remove** | hidden Stakeholders table, 5/1722 |
| `custom_contacts__address_table` | Table | Table |  | 5/1722 (0.3%) | 2026-09-23 |  | 1 code | **Remove** | hidden Stakeholders table, 5/1722 |
| `custom_phone` | Phone | Read Only | hidden | 520/1722 (30%) | 2026-11-05 | 42 (42 by people) | 2 code | **Remove** | hidden Read Only fetch of customer_primary_contact.phone (520/1722) that nothing displays; sync_contact.py writes it |
| `custom_comments_tab` | Comments | Tab Break |  | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `custom_zoho_accounts_id` | Zoho Accounts ID | Data | hidden, read-only | 854/1722 (50%) | 2026-11-07 | 0 (0 by people) | none | **Keep** | hidden import key, 854/1722 |
| `contacts_and_addresses_tab` | Contacts & Addresses | Tab Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 7 custom field(s) |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Supplier (1,214 rows on prod)

42 fields: Hide 1, Keep 29, Leave 1, Remove 11.

Supplier mirrors Customer: a hidden `custom_primary_contact` section with four empty duplicates of the `primary_contact_*` set, a hidden Stakeholders table with 3 rows, and the supplier-group search fields that `setup/supplier_groups.py` creates on migrate (not in fixtures).

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `irs_1099` | Is IRS 1099 reporting required for supplier? | Check | not in fixtures | 0/1214 (0%) |  | 0 (0 by people) | none | **Leave (ERPNext)** | ERPNext US regional field |
| `custom_drive_folder_id` | Drive Folder ID | Data | hidden, read-only, not in fixtures | 0/1214 (0%) |  | 0 (0 by people) | 11 code | **Keep** | 0/1214 so far; Drive sync writes it |
| `custom_comments_tab` | Comments | Tab Break |  | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `custom_our_account_number` | Our Account Number | Data |  | 1/1214 (0.1%) | 2026-06-08 | 1 (1 by people) | none | **Hide** | 1/1214, no code; useful field but unused; hide until purchasing asks for it |
| `custom_phone_number` | Phone Number | Data |  | 254/1214 (21%) | 2026-10-26 | 6 (6 by people) | 18 code | **Keep** | code reads/writes it (18 files) |
| `custom_additional_supplier_groups` | Additional Supplier Groups | Table MultiSelect | not in fixtures | 1/1214 (0.1%) | 2026-09-16 |  | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_store_run_vendor` | Store-Run Vendor | Check | not in fixtures | 7/1214 (0.6%) | 2026-09-24 | 5 (5 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_supplier_groups_search` | All Supplier Groups (Search) | Small Text | hidden, read-only, not in fixtures | 361/1214 (30%) | 2026-11-03 | 2911 (1981 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_additional_supplier_groups_list` | Additional Groups | Small Text | read-only, list, not in fixtures | 1/1214 (0.1%) | 2026-09-16 | 91 (90 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_email` | Email | Data |  | 40/1214 (3%) | 2026-09-16 | 2 (2 by people) | 14 code | **Keep** | code reads/writes it (14 files) |
| `primary_contact_section` | Primary Contact | Section Break | not in fixtures | - |  |  | 1 code | **Keep** | no custom field under it; hosts moved standard fields |
| `primary_contact` | Full Name | Link | not in fixtures | 7/1214 (0.6%) | 2026-09-16 | 5 (5 by people) | 14 code | **Keep** | code reads/writes it (14 files) |
| `primary_contact_job_title` | Job Title | Data | not in fixtures | 2/1214 (0.2%) | 2026-08-31 | 4 (4 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `primary_contact_col_break` |  | Column Break | not in fixtures | - |  |  | none | **Keep** | layout |
| `primary_contact_phone` | Phone | Data | not in fixtures | 2/1214 (0.2%) | 2026-08-18 | 3 (3 by people) | 4 code | **Keep** | code reads/writes it (4 files) |
| `custom_supplier_notes` | Supplier Notes | Text Editor |  | 25/1214 (2%) | 2026-09-16 | 0 (0 by people) | none | **Keep** | 25/1214, people write it |
| `primary_contact_email` | Email Address | Data | not in fixtures | 3/1214 (0.2%) | 2026-08-31 | 4 (4 by people) | 6 code | **Keep** | code reads/writes it (6 files) |
| `address_and_contact` | Address and Contact | HTML | not in fixtures | - |  |  | 1 code | **Keep** | rendered by code (1 files) |
| `custom_quality_section` | Quality | Section Break |  | - |  |  | none | **Keep** | 0/1214; quality/scorecard_build.py writes them (subcontractor scorecards, new) |
| `custom_section_break_gl6o7` |  | Section Break | hidden | - |  |  | none | **Remove** | hidden, unlabeled, empty section |
| `custom_scorecard_summary` | Latest scorecard | Data | read-only | 0/1214 (0%) |  | 0 (0 by people) | 1 code | **Keep** | 0/1214; quality/scorecard_build.py writes them (subcontractor scorecards, new) |
| `custom_scorecard_state` | Measurability | Select | read-only | 0/1214 (0%) |  | 0 (0 by people) | 1 code | **Keep** | 0/1214; quality/scorecard_build.py writes them (subcontractor scorecards, new); values: (empty) 1214 |
| `custom_latest_scorecard` | Scorecard | Link | read-only | 0/1214 (0%) |  | 0 (0 by people) | 1 code | **Keep** | 0/1214; quality/scorecard_build.py writes them (subcontractor scorecards, new) |
| `custom_column_break_ycp3n` |  | Column Break |  | - |  |  | none | **Remove** | 0/1214 on all four; superseded by primary_contact_* |
| `location_map_col_break` |  | Column Break | not in fixtures | - |  |  | setup/patch only | **Keep** | layout |
| `custom_column_break_rrn0r` |  | Column Break |  | - |  |  | none | **Remove** | 0/1214 on all four; superseded by primary_contact_* |
| `custom_primary_contact` | Primary Contact | Section Break | hidden | - |  |  | none | **Remove** | 0/1214 on all four; superseded by primary_contact_* |
| `custom_full_name` | Full Name | Data |  | 0/1214 (0%) |  | 0 (0 by people) | none | **Remove** | 0/1214 on all four; superseded by primary_contact_* |
| `custom_job_title` | Job Title | Data |  | 0/1214 (0%) |  | 0 (0 by people) | none | **Remove** | 0/1214 on all four; superseded by primary_contact_* |
| `custom_column_break_ncqbr` |  | Column Break |  | - |  |  | none | **Remove** | 0/1214 on all four; superseded by primary_contact_* |
| `section_break_address_list` | Address Directory | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `custom_phone` | Phone | Data |  | 0/1214 (0%) |  | 0 (0 by people) | 2 code | **Remove** | 0/1214 on all four; superseded by primary_contact_* |
| `address_list_html` | Address List HTML | HTML | not in fixtures | - |  |  | 6 code | **Keep** | rendered by code (6 files) |
| `custom_email_address` | Email Address | Data |  | 0/1214 (0%) |  | 0 (0 by people) | 2 code | **Remove** | 0/1214 on all four; superseded by primary_contact_* |
| `custom_addresses` |  | Section Break | hidden | - |  |  | none | **Remove** | hidden Stakeholders table, 3/1214 |
| `custom_address_list` | Table | Table |  | 3/1214 (0.2%) | 2026-06-12 |  | none | **Remove** | hidden Stakeholders table, 3/1214 |
| `contacts_and_addresses_tab` | Contacts & Addresses | Tab Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 5 custom field(s) |
| `section_break_contacts` | Contact Directory | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `contact_list_html` | Contact List HTML | HTML | not in fixtures | - |  |  | 5 code | **Keep** | rendered by code (5 files) |
| `section_break_map` | Location | Section Break | not in fixtures | - |  |  | 2 code | **Keep** | no custom field under it; hosts moved standard fields |
| `location_map_html` | Location Map HTML | HTML | not in fixtures | - |  |  | 2 code | **Keep** | rendered by code (2 files) |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Item (1,574 rows on prod)

82 fields: Fix 1, Keep 73, Merge 7, Remove 1.

66 of Item's 82 Custom Fields are the aquatic-equipment spec schema that `water_engineering/setup.py` creates on migrate (not in fixtures). The catalog load has filled the pump, light and aerator fields (19 to 392 rows); the heater, filter, meter, VGB and chemical feed fields hold 0 to 3 rows because the catalog has not reached those classes yet, so they are Keep with that caveat. The duplicates are the pump-specific electrical fields (`custom_pump_hp`, `custom_pump_voltage`, `custom_pump_phase`, `custom_pump_fla_amps`) beside the generic `custom_elec_*` set, two Cut Sheet attachments and two pump-curve HTML widgets.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_lumens` | Lumens | Float | not in fixtures | 22/1574 (1%) | 2026-11-02 | 0 (0 by people) | 2 code | **Keep** | automation writes it (2 files) |
| `custom_elec_hp` | HP | Float | not in fixtures | 1/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_elec_hz` | Hz | Select | not in fixtures | 1/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has; values: (empty) 1573, 60 1 |
| `custom_pump_hp` | HP | Float | not in fixtures | 156/1574 (10%) | 2026-11-02 | 6 (6 by people) | 3 code | **Merge into custom_elec_hp** | two 'HP' fields: 156 pump rows vs 1 generic; keep the generic electrical set and move the pump values |
| `custom_beam_deg` | Beam Angle (deg) | Float | not in fixtures | 4/1574 (0.3%) | 2026-11-02 | 0 (0 by people) | 2 code | **Keep** | automation writes it (2 files) |
| `custom_model_no` | Model / Part No. | Data | not in fixtures | 391/1574 (25%) | 2026-11-02 | 3 (3 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_heater_kw` | Heater kW | Float | not in fixtures | 1/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_ip_rating` | IP Rating | Data | not in fixtures | 9/1574 (0.6%) | 2026-11-02 | 0 (0 by people) | 2 code | **Keep** | automation writes it (2 files) |
| `custom_rated_gpm` | Rated Flow (GPM) | Float | not in fixtures | 174/1574 (11%) | 2026-11-02 | 5 (5 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_vgb_rated` | VGB Compliant | Check | not in fixtures | 1/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_catalog_id` | Catalog Row ID | Data | not in fixtures | 380/1574 (24%) | 2026-11-02 | 3 (3 by people) | 2 code | **Keep** | automation writes it (2 files) |
| `custom_elec_phase` | Phase | Select | not in fixtures | 2/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has; values: (empty) 1572, 1 2 |
| `custom_elec_watts` | Watts | Float | not in fixtures | 36/1574 (2%) | 2026-11-02 | 0 (0 by people) | 2 code | **Keep** | automation writes it (2 files) |
| `custom_meter_kind` | Meter Kind | Select | not in fixtures | 3/1574 (0.2%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has; values: (empty) 1571, Flowmeter 1, Thermometer 1, Vacuum 1 |
| `custom_pump_curve` | Pump Curve (legacy table) | Table | not in fixtures | 0/1574 (0%) |  |  | 3 code | **Remove** | labeled 'legacy table', 0/1574 rows after patches/migrate_item_pump_curves.py moved them |
| `custom_pump_phase` | Phase | Select | not in fixtures | 127/1574 (8%) | 2026-11-02 | 7 (7 by people) | 1 code | **Merge into custom_elec_phase** | two 'Phase' fields (127 vs 2); values: (empty) 1447, 1 70, 3 57 |
| `custom_ul_listing` | UL / ETL Listing | Data | not in fixtures | 165/1574 (10%) | 2026-11-02 | 3 (3 by people) | 2 code | **Keep** | automation writes it (2 files) |
| `custom_heater_fuel` | Heater Fuel | Select | not in fixtures | 2/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has; values: (empty) 1572, Electric 1, Gas 1 |
| `custom_meter_range` | Meter Range | Data | not in fixtures | 3/1574 (0.2%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_nec680_bond` | Bond/Ground per NEC 680 | Check | not in fixtures | 2/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_nsf_listing` | NSF / VGB Listing | Data | not in fixtures | 2/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_spec_method` | Spec Reading Method | Data | not in fixtures | 345/1574 (22%) | 2026-11-02 | 3 (3 by people) | 2 code | **Keep** | automation writes it (2 files) |
| `custom_elec_voltage` | Voltage | Data | not in fixtures | 39/1574 (2%) | 2026-11-02 | 0 (0 by people) | 2 code | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_filter_media` | Filter Media | Select | not in fixtures | 1/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has; values: (empty) 1573, Cartridge 1 |
| `custom_jet_flow_gpm` | Jet Flow (GPM) | Float | not in fixtures | 1/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_max_depth_ft` | Max Submersion Depth (ft) | Float | not in fixtures | 7/1574 (0.4%) | 2026-11-02 | 0 (0 by people) | 2 code | **Keep** | automation writes it (2 files) |
| `custom_pump_section` | Pump Specifications | Section Break | not in fixtures | - |  |  | 1 code | **Keep** | holds 10 custom field(s) |
| `custom_pump_voltage` | Voltage | Data | not in fixtures | 150/1574 (10%) | 2026-11-02 | 10 (10 by people) | 1 code | **Merge into custom_elec_voltage** | two 'Voltage' fields (150 vs 39) |
| `custom_rated_tdh_ft` | Rated Head (ft TDH) | Float | not in fixtures | 170/1574 (11%) | 2026-11-02 | 4 (4 by people) | 1 code | **Keep** | automation writes it (1 files) |
| `custom_elec_fla_amps` | FLA (A) | Float | not in fixtures | 3/1574 (0.2%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_feed_rate_gpd` | Feed Rate (GPD) | Float | not in fixtures | 0/1574 (0%) |  | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_heater_btu_hr` | Heater BTU/hr | Float | not in fixtures | 1/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_iapmo_listing` | IAPMO / UPC Listing | Data | not in fixtures | 0/1574 (0%) |  | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_light_control` | Light Control | Select | not in fixtures | 0/1574 (0%) |  | 0 (0 by people) | 2 code | **Keep** | automation writes it (2 files); no row holds a value yet; values: (empty) 1574 |
| `custom_pump_fla_amps` | FLA (A) | Float | not in fixtures | 124/1574 (8%) | 2026-11-02 | 7 (7 by people) | 1 code | **Merge into custom_elec_fla_amps** | two 'FLA (A)' fields (124 vs 3) |
| `custom_requires_gfci` | Requires GFCI | Check | not in fixtures | 2/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | 1 code | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_spec_revision` | Spec Revision | Small Text | not in fixtures | 380/1574 (24%) | 2026-11-02 | 3 (3 by people) | 2 code | **Keep** | automation writes it (2 files) |
| `custom_pump_cut_sheet` | Cut Sheet | Attach | not in fixtures | 0/1574 (0%) |  | 0 (0 by people) | setup/patch only | **Merge into custom_spec_cut_sheet** | two 'Cut Sheet' attachments, both 0/1574 |
| `custom_spec_cut_sheet` | Cut Sheet | Attach | not in fixtures | 0/1574 (0%) |  | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_spec_source_cb` |  | Column Break | not in fixtures | - |  |  | setup/patch only | **Keep** | layout |
| `custom_aquatic_section` | Aquatic Equipment | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 41 custom field(s) |
| `custom_equipment_class` | Equipment Class | Select | not in fixtures | 392/1574 (25%) | 2026-11-02 | 3 (3 by people) | 3 code | **Keep** | code reads/writes it (3 files); values: (empty) 1182, Pump 169, Nozzle 157, Light 35, Aerator 19, Gauge / Meter 3 |
| `custom_spec_confidence` | Spec Confidence | Data | not in fixtures | 345/1574 (22%) | 2026-11-02 | 3 (3 by people) | 2 code | **Keep** | automation writes it (2 files) |
| `custom_spec_source_url` | Spec Source URL | Small Text | not in fixtures | 380/1574 (24%) | 2026-11-02 | 3 (3 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_filter_area_sqft` | Filter Area (sq ft) | Float | not in fixtures | 1/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_generic_estimate` | Generic Estimate | Check | not in fixtures | 0/1574 (0%) |  | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files); no row holds a value yet |
| `custom_jet_pressure_psi` | Jet Pressure (PSI) | Float | not in fixtures | 1/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_pump_curve_chart` | Pump Curve Chart | HTML | not in fixtures | - |  |  | 2 code | **Merge into custom_pump_curves_html** | two pump-curve HTML widgets on the same form |
| `custom_pump_curves_html` | Pump Curves | HTML | not in fixtures | - |  |  | 2 code | **Keep** | rendered by code (2 files) |
| `custom_spec_verified_by` | Spec Verified By | Link | not in fixtures | 380/1574 (24%) | 2026-11-02 | 3 (3 by people) | 2 code | **Keep** | automation writes it (2 files) |
| `custom_spec_verified_on` | Spec Verified On | Date | not in fixtures | 380/1574 (24%) | 2026-11-02 | 3 (3 by people) | 2 code | **Keep** | automation writes it (2 files) |
| `custom_vgb_max_flow_gpm` | VGB Max Flow (GPM) | Float | not in fixtures | 0/1574 (0%) |  | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_equipment_elec_cb` |  | Column Break | not in fixtures | - |  |  | setup/patch only | **Keep** | layout |
| `custom_vgb_open_area_sqin` | VGB Open Area (sq in) | Float | not in fixtures | 1/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_controller_channels` | Controller Channels | Data | not in fixtures | 1/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_filter_backwash_gpm` | Filter Backwash (GPM) | Float | not in fixtures | 0/1574 (0%) |  | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_filter_max_flow_gpm` | Filter Max Flow (GPM) | Float | not in fixtures | 1/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_spec_error_band_pct` | Spec Error Band (%) | Float | not in fixtures | 162/1574 (10%) | 2026-11-02 | 3 (3 by people) | 2 code | **Keep** | automation writes it (2 files) |
| `custom_spec_source_section` | Spec Source | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 9 custom field(s) |
| `custom_aerator_cord_lengths` | Aerator Cord Lengths | Data | not in fixtures | 0/1574 (0%) |  | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_aerator_min_depth_ft` | Aerator Min Water Depth (ft) | Float | not in fixtures | 19/1574 (1%) | 2026-11-02 | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files) |
| `custom_skimmer_max_flow_gpm` | Skimmer Max Flow (GPM) | Float | not in fixtures | 1/1574 (0.1%) | 2026-08-28 | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_aerator_pattern_height_ft` | Aerator Pattern Height (ft) | Float | not in fixtures | 19/1574 (1%) | 2026-11-02 | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files) |
| `custom_aerator_pattern_diameter_ft` | Aerator Pattern Diameter (ft) | Float | not in fixtures | 19/1574 (1%) | 2026-11-02 | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files) |
| `custom_filter_design_rate_gpm_sqft` | Filter Design Rate (GPM/sq ft) | Float | not in fixtures | 0/1574 (0%) |  | 0 (0 by people) | setup/patch only | **Keep** | aquatic-equipment spec schema created by water_engineering/setup.py (2026-08-28); 0 to 3 rows because the catalog load has not reached this equipment class yet; review again when it has |
| `custom_sku` | SKU | Data | list | 15/1574 (1.0%) | 2026-09-18 | 2 (2 by people) | 2 code; prod: List View Settings (Item) | **Keep** | 15/1574 but on the saved Item list settings; decide with the catalog work |
| `custom_item_identifier` | Item Code | Data | read-only | 515/1574 (33%) | 2026-11-04 | 164 (164 by people) | 3 code | **Fix label** | labeled 'Item Code' like the standard item_code; 515/1574 and 164 human edits in 180 days, so rename rather than remove |
| `custom_guided_client_id` | Guided Entry Create ID | Data | hidden, read-only | 3/1574 (0.2%) | 2026-10-20 | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_sds_section` | Safety Data | Section Break |  | - |  |  | 1 code | **Keep** | holds 4 custom field(s) |
| `custom_is_chemical` | Has a safety data sheet | Check |  | 1/1574 (0.1%) | 2026-10-30 | 1 (1 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_sds_document` | Safety data sheet | Attach |  | 0/1574 (0%) |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); no row holds a value yet |
| `custom_hazard_summary` | Hazard in one line | Small Text |  | 0/1574 (0%) |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); no row holds a value yet |
| `custom_sds_reviewed_on` | Sheet reviewed | Date |  | 0/1574 (0%) |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); no row holds a value yet |
| `custom_maintenance_billing_section` | Maintenance Billing | Section Break |  | - |  |  | 1 code | **Keep** | holds 4 custom field(s) |
| `custom_maintenance_billing` | Bill On Visits | Select |  | 1574/1574 (100%) | 2026-11-04 | 0 (0 by people) | 1 code | **Keep** | 1574/1574 because of the default; chemical billing reads it; values: Per Dose 1574 |
| `custom_price_per_dose` | Price per Dose | Currency |  | 0/1574 (0%) |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); no row holds a value yet |
| `custom_size_family` | Size Family | Data |  | 2/1574 (0.1%) | 2026-11-02 | 2 (2 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_size` | Size | Data |  | 2/1574 (0.1%) | 2026-11-02 | 2 (2 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_source_configuration` | Source Configuration | Link | hidden, read-only, not in fixtures | 0/1574 (0%) |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); no row holds a value yet |
| `custom_purchase_link` | Purchase Link | Text Editor |  | 27/1574 (2%) | 2026-11-02 | 1 (1 by people) | none | **Merge into Item Supplier.purchase_url** | 27/1574 vs 157/226 Item Supplier rows; the Buy widgets read purchase_url |
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments Field | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Purchase Order (285 rows on prod)

13 fields: Keep 13.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_expected_delivery_date` | Expected Delivery Date | Date |  | 44/285 (15%) | 2026-11-06 | 33 (33 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_contract_section` | Contracted under | Section Break |  | - |  |  | none | **Keep** | holds 4 custom field(s) |
| `custom_msa_contract` | Master agreement | Link |  | 0/285 (0%) |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); no row holds a value yet |
| `custom_sow_contract` | Statement of work | Link |  | 0/285 (0%) |  | 0 (0 by people) | setup/patch only | **Keep** | 0/285 and no reader yet; part of the contracts set (msa, scope, change order) that landed in #1417 |
| `custom_column_break_contract` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_scope_of_work` | Project scope of work | Link |  | 0/285 (0%) |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); no row holds a value yet |
| `custom_change_order` | Change order | Link |  | 0/285 (0%) |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); no row holds a value yet |
| `custom_order_stage` | Order Stage | Select | list, not in fixtures | 285/285 (100%) | 2026-11-06 | 192 (192 by people) | 5 code; prod: List View Settings (Purchase Order) | **Keep** | used on prod: List View Settings (Purchase Order); values: Received 221, Created 34, Awaiting Confirmation 21, Partially Fulfilled 4, Waiting for Delivery 4, Waiting for Pickup 1 |
| `custom_approval_stamp_section` | Approval | Section Break | not in fixtures | - |  |  | 1 code | **Keep** | holds 2 custom field(s) |
| `custom_approved_by` | Approved By | Link | read-only, not in fixtures | 155/285 (54%) | 2026-11-06 | 153 (153 by people) | 2 code; prod: Print Format 'Purchase Order - Sapphire' | **Keep** | used on prod: Print Format 'Purchase Order - Sapphire' |
| `custom_approved_on` | Approved On | Datetime | read-only, not in fixtures | 155/285 (54%) | 2026-11-06 | 155 (155 by people) | 2 code; prod: Print Format 'Purchase Order - Sapphire' | **Keep** | used on prod: Print Format 'Purchase Order - Sapphire' |
| `custom_comments_tab` | Comments | Tab Break |  | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Sapphire Maintenance Record (35 rows on prod)

45 fields: Keep 43, Leave 2.

Own fields of the app's DocType (35 visits on prod, module live since October). Fill rates are low because the module is new, not because fields are dead: every field is read by the visit wizard, the review page or the sign-off flow. Two things to watch: `crew` (1/35) beside `technician` (35/35) is the same concept twice, and the client sign-off trio (`client_sign_off_method`, `client_signed_by`, `client_signed_on`) is 0/35 while `client_sign_off` has 2 signatures.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `workflow_state` | Workflow State | Link | hidden, not in fixtures | 35/35 (100%) | 2026-11-05 | 0 (0 by people) | 26 code; prod: Notifications 'Maintenance Finalized', 'Maintenance Review Needed' | **Leave (Workflow)** | created by the Workflow engine |
| `customer` | Customer | Link | list, own doctype | 35/35 (100%) | 2026-11-05 | 0 (0 by people) | 361 code | **Keep** | code reads/writes it (361 files) |
| `project` | Project | Link | list, own doctype | 35/35 (100%) | 2026-11-05 | 0 (0 by people) | 396 code | **Keep** | code reads/writes it (396 files) |
| `legacy_name` | Previous Number | Data | read-only, own doctype | 20/35 (57%) | 2026-11-04 | 0 (0 by people) | 6 code | **Keep** | code reads/writes it (6 files) |
| `maintenance_contract` | Maintenance Contract | Link | own doctype | 35/35 (100%) | 2026-11-05 | 0 (0 by people) | 23 code | **Keep** | code reads/writes it (23 files) |
| `service_call` | Service Call | Link | own doctype | 0/35 (0%) |  | 0 (0 by people) | 9 code | **Keep** | code reads/writes it (9 files); no row holds a value yet |
| `serial_no` | Serial No | Link | list, own doctype | 2/35 (6%) | 2026-11-04 | 0 (0 by people) | 39 code | **Keep** | code reads/writes it (39 files) |
| `template` | Form Template | Link | read-only, own doctype | 24/35 (69%) | 2026-11-04 | 0 (0 by people) | 289 code | **Keep** | code reads/writes it (289 files) |
| `visit_label` | Visit Type | Data | read-only, own doctype | 13/35 (37%) | 2026-11-04 | 0 (0 by people) | 36 code | **Keep** | code reads/writes it (36 files) |
| `technician` | Technician | Link | list, own doctype | 35/35 (100%) | 2026-11-05 | 0 (0 by people) | 146 code | **Keep** | code reads/writes it (146 files) |
| `crew` | Crew | Table | own doctype | 1/35 (3%) | 2026-11-04 |  | 156 code | **Keep** | code reads/writes it (156 files) |
| `planned_hours` | Planned hours | Float | own doctype | 0/35 (0%) |  | 0 (0 by people) | 12 code | **Keep** | code reads/writes it (12 files); no row holds a value yet |
| `full_day` | Full day | Check | own doctype | 2/35 (6%) | 2026-11-04 | 0 (0 by people) | 13 code | **Keep** | code reads/writes it (13 files) |
| `tentative` | Tentative (pencil) | Check | own doctype | 0/35 (0%) |  | 0 (0 by people) | 36 code | **Keep** | code reads/writes it (36 files); no row holds a value yet |
| `scheduled_visit_date` | Scheduled Visit Date | Date | list, own doctype | 34/35 (97%) | 2026-11-05 | 0 (0 by people) | 30 code | **Keep** | code reads/writes it (30 files) |
| `visit_date` | Visit Date | Date | own doctype | 17/35 (49%) | 2026-11-04 | 0 (0 by people) | 35 code | **Keep** | code reads/writes it (35 files) |
| `dispatch_digest_sent_on` | Dispatch Digest Sent On | Date | hidden, read-only, own doctype | 0/35 (0%) |  | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files); no row holds a value yet |
| `customer_confirmation_due` | Customer Confirmation Due | Date | hidden, read-only, own doctype | 0/35 (0%) |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); no row holds a value yet |
| `customer_confirmed_for` | Customer Confirmed For | Date | hidden, read-only, own doctype | 0/35 (0%) |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); no row holds a value yet |
| `clock_in_time` | Clock In Time | Datetime | own doctype | 0/35 (0%) |  | 0 (0 by people) | 16 code | **Keep** | code reads/writes it (16 files); no row holds a value yet |
| `clock_out_time` | Clock Out Time | Datetime | own doctype | 0/35 (0%) |  | 0 (0 by people) | 15 code | **Keep** | code reads/writes it (15 files); no row holds a value yet |
| `paused_duration` | Paused Duration (Seconds) | Float | own doctype | 0/35 (0%) |  | 0 (0 by people) | 7 code | **Keep** | code reads/writes it (7 files); no row holds a value yet |
| `total_labor_cost` | Total Labor Cost | Currency | hidden, own doctype | 0/35 (0%) |  | 0 (0 by people) | 7 code | **Keep** | code reads/writes it (7 files); no row holds a value yet |
| `safety_acknowledged` | Safety Procedures & PPE Acknowledged | Check | own doctype | 21/35 (60%) | 2026-11-04 | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `dashboard` | Dashboard | HTML | own doctype | - |  |  | 209 code | **Keep** | rendered by code (209 files) |
| `chemistry_readings` | Water Chemistry | Table | own doctype | 16/35 (46%) | 2026-11-04 |  | 17 code | **Keep** | code reads/writes it (17 files) |
| `cleaning_tasks` | Cleaning Tasks | Table | own doctype | 24/35 (69%) | 2026-11-04 |  | 14 code | **Keep** | code reads/writes it (14 files) |
| `maintenance_results` | Maintenance Results | Table | own doctype | 24/35 (69%) | 2026-11-04 |  | 16 code | **Keep** | code reads/writes it (16 files) |
| `consumables` | Consumables | Table | own doctype | 21/35 (60%) | 2026-11-04 |  | 34 code | **Keep** | code reads/writes it (34 files) |
| `visit_notes` | Visit Notes | Small Text | own doctype | 2/35 (6%) | 2026-10-20 | 0 (0 by people) | 12 code | **Keep** | code reads/writes it (12 files) |
| `fountain_photo` | Fountain As We Left It | Attach Image | own doctype | 1/35 (3%) | 2026-10-29 | 0 (0 by people) | 12 code | **Keep** | code reads/writes it (12 files) |
| `follow_ups` | Follow-ups and Parts Needed | Table | own doctype | 0/35 (0%) |  |  | 11 code | **Keep** | code reads/writes it (11 files); no row holds a value yet |
| `historical_visits` | Historical Visits | HTML | own doctype | - |  |  | 2 code | **Keep** | rendered by code (2 files) |
| `completion_percent` | Completed | Percent | read-only, list, own doctype | 16/35 (46%) | 2026-11-04 | 0 (0 by people) | 29 code | **Keep** | code reads/writes it (29 files) |
| `warranty_rma_flag` | Warranty RMA Triggered | Check | read-only, own doctype | 0/35 (0%) |  | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files); no row holds a value yet |
| `has_out_of_range_readings` | Readings Out of Range | Check | read-only, list, own doctype | 9/35 (26%) | 2026-11-04 | 0 (0 by people) | 11 code; prod: Notification 'Maintenance Reading Out of Range' | **Keep** | used on prod: Notification 'Maintenance Reading Out of Range' |
| `sales_invoice` | Generated Sales Invoice | Link | read-only, own doctype | 0/35 (0%) |  | 0 (0 by people) | 23 code | **Keep** | code reads/writes it (23 files); no row holds a value yet |
| `workflow_state` | Workflow State | Link | read-only, own doctype | 35/35 (100%) | 2026-11-05 | 0 (0 by people) | 26 code; prod: Notifications 'Maintenance Finalized', 'Maintenance Review Needed' | **Leave (Workflow)** | created by the Workflow engine |
| `technician_signature` | Technician Signature | Signature | own doctype | 8/35 (23%) | 2026-11-04 | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `technician_signed_by` | Technician Signed By | Link | read-only, own doctype | 8/35 (23%) | 2026-11-04 | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `technician_signed_on` | Technician Signed On | Datetime | read-only, own doctype | 8/35 (23%) | 2026-11-04 | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `client_sign_off` | Client Sign-off | Signature | own doctype | 2/35 (6%) | 2026-10-29 | 0 (0 by people) | 12 code | **Keep** | code reads/writes it (12 files) |
| `client_sign_off_method` | Client Signed | Select | read-only, own doctype | 0/35 (0%) |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); no row holds a value yet; values: (empty) 35 |
| `client_signed_by` | Client Signed By | Data | read-only, own doctype | 0/35 (0%) |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); no row holds a value yet |
| `client_signed_on` | Client Signed On | Datetime | read-only, own doctype | 0/35 (0%) |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); no row holds a value yet |

### Material Request (26 rows on prod)

3 fields: Keep 3.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_project` | Project | Link |  | 17/26 (65%) | 2026-10-20 | 0 (0 by people) | 12 code; prod: Print Format 'Material Request - Sapphire' | **Keep** | used on prod: Print Format 'Material Request - Sapphire' |
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments Field | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Stock Entry

2 fields: Keep 2.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Account (392 rows on prod)

9 fields: Remove 9.

This is the chart-of-accounts DocType, not the Customer that is relabeled 'Account'. On 2026-04-20 it received the Customer's Primary Contact section, contact widgets and a Comments tab (9 Custom Fields, none in fixtures). 0 of 392 accounts hold a value. Remove all nine.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_comments_tab` | Comments | Tab Break |  | - |  |  | setup/patch only | **Remove** | the chart-of-accounts doctype got the Customer ('Account') contact tab by mistake on 2026-04-20: 0/392 filled |
| `primary_contact_section` | Primary Contact | Section Break | not in fixtures | - |  |  | 1 code | **Remove** | the chart-of-accounts doctype got the Customer ('Account') contact tab by mistake on 2026-04-20: 0/392 filled |
| `primary_contact` | Full Name | Link | not in fixtures | 0/392 (0%) |  | 0 (0 by people) | 14 code | **Remove** | the chart-of-accounts doctype got the Customer ('Account') contact tab by mistake on 2026-04-20: 0/392 filled |
| `primary_contact_job_title` | Job Title | Data | not in fixtures | 0/392 (0%) |  | 0 (0 by people) | 3 code | **Remove** | the chart-of-accounts doctype got the Customer ('Account') contact tab by mistake on 2026-04-20: 0/392 filled |
| `primary_contact_col_break` |  | Column Break | not in fixtures | - |  |  | none | **Remove** | the chart-of-accounts doctype got the Customer ('Account') contact tab by mistake on 2026-04-20: 0/392 filled |
| `primary_contact_phone` | Phone | Data | not in fixtures | 0/392 (0%) |  | 0 (0 by people) | 4 code | **Remove** | the chart-of-accounts doctype got the Customer ('Account') contact tab by mistake on 2026-04-20: 0/392 filled |
| `primary_contact_email` | Email Address | Data | not in fixtures | 0/392 (0%) |  | 0 (0 by people) | 6 code | **Remove** | the chart-of-accounts doctype got the Customer ('Account') contact tab by mistake on 2026-04-20: 0/392 filled |
| `address_and_contact` | Address and Contact | HTML | not in fixtures | - |  |  | 1 code | **Remove** | the chart-of-accounts doctype got the Customer ('Account') contact tab by mistake on 2026-04-20: 0/392 filled |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Remove** | the chart-of-accounts doctype got the Customer ('Account') contact tab by mistake on 2026-04-20: 0/392 filled |

### Address (905 rows on prod)

12 fields: Keep 10, Leave 2.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_full_address` | Full Address | Data | read-only | 905/905 (100%) | 2026-11-07 | 23 (23 by people) | 11 code | **Keep** | code reads/writes it (11 files) |
| `custom_latitude` | Latitude | Float |  | 21/905 (2%) | 2026-11-04 | 8 (8 by people) | 8 code | **Keep** | code reads/writes it (8 files) |
| `custom_map_placeholder` | Map Placeholder | HTML |  | - |  |  | 2 code | **Keep** | rendered by code (2 files) |
| `custom_longitude` | Longitude | Float |  | 21/905 (2%) | 2026-11-04 | 8 (8 by people) | 8 code | **Keep** | code reads/writes it (8 files) |
| `custom_location_source` | Location Source | Select | read-only | 17/905 (2%) | 2026-11-04 | 4 (4 by people) | 5 code | **Keep** | code reads/writes it (5 files); values: (empty) 888, Google 17 |
| `tax_category` | Tax Category | Link | not in fixtures | 0/905 (0%) |  | 0 (0 by people) | 1 code | **Leave (ERPNext)** | ERPNext's own custom field (regional or CRM setup), not Sapphire's |
| `custom_google_place_id` | Google Place ID | Data | read-only | 21/905 (2%) | 2026-11-04 | 10 (10 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `is_your_company_address` | Is Your Company Address | Check | not in fixtures | 1/905 (0.1%) | 2026-10-09 | 0 (0 by people) | none | **Leave (ERPNext)** | ERPNext's own custom field (regional or CRM setup), not Sapphire's |
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_section_break_qcwmy` |  | Section Break |  | - |  |  | none | **Keep** | no custom field under it; hosts moved standard fields |
| `custom_description` | Description | Small Text |  | 10/905 (1%) | 2026-11-04 | 2 (2 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Asset (10 rows on prod)

13 fields: Keep 7, Remove 6.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_current_event_location` | Current Event Location | Data |  | 0/10 (0%) |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); no row holds a value yet |
| `custom_map_placeholder` | Map Placeholder | HTML |  | - |  |  | 2 code | **Keep** | rendered by code (2 files) |
| `custom_pump_make_model` | Pump Make/Model | Data |  | 0/10 (0%) |  | 0 (0 by people) | setup/patch only | **Remove** | 0/10; migrated to Serial No by patches/migrate_assets_to_serial_no.py |
| `custom_filtration_media_type` | Filtration Media Type | Data |  | 0/10 (0%) |  | 0 (0 by people) | setup/patch only | **Remove** | 0/10; migrated to Serial No by patches/migrate_assets_to_serial_no.py |
| `custom_water_volume` | Water Volume | Data |  | 0/10 (0%) |  | 0 (0 by people) | setup/patch only | **Remove** | 0/10; migrated to Serial No by patches/migrate_assets_to_serial_no.py |
| `custom_site_instructions` | Site Instructions | Small Text |  | 0/10 (0%) |  | 0 (0 by people) | 3 code | **Remove** | 0/10; migrated to Serial No by patches/migrate_assets_to_serial_no.py |
| `custom_under_warranty` | Under Warranty | Check |  | 0/10 (0%) |  | 0 (0 by people) | setup/patch only | **Remove** | 0/10; migrated to Serial No by patches/migrate_assets_to_serial_no.py |
| `custom_warranty_expiry_date` | Warranty Expiry Date | Date |  | 0/10 (0%) |  | 0 (0 by people) | setup/patch only | **Remove** | 0/10; migrated to Serial No by patches/migrate_assets_to_serial_no.py |
| `custom_rental_status` | Rental Status | Select | read-only | 10/10 (100%) | 2026-09-14 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); values: Available 10 |
| `custom_rentable` | Available for Event Rental | Check |  | 10/10 (100%) | 2026-09-14 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_rental_prep_hours` | Rental Prep Hours | Int |  | 0/10 (0%) |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); no row holds a value yet |
| `custom_rental_turnaround_hours` | Rental Turnaround Hours | Int |  | 10/10 (100%) | 2026-09-14 | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_guided_client_id` | Guided Entry Create ID | Data | hidden, read-only | 0/10 (0%) |  | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files); no row holds a value yet |

### Assignment Rule (3 rows on prod)

2 fields: Remove 2.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `assign_condition_json` | Assign Condition JSON | Code | not in fixtures | 3/3 (100%) | 2026-02-07 | 0 (0 by people) | none | **Remove** | Helpdesk app leftovers; Helpdesk is uninstalled and its three rules are disabled |
| `unassign_condition_json` | Unassign Condition JSON | Code | not in fixtures | 0/3 (0%) |  | 0 (0 by people) | none | **Remove** | Helpdesk app leftovers; Helpdesk is uninstalled and its three rules are disabled |

### Batch

2 fields: Keep 2.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### CRM Deal

1 fields: Remove 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `erpnext_customer` | Customer in ERPNext | Data | not in fixtures | n/a |  | n/a | 6 code | **Remove** | frappe CRM leftover; tabCRM Deal no longer exists |

### CRM Note (1,092 rows on prod)

1 fields: Remove 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_attachment` | Attachment | Attach |  | 0/1092 (0%) |  | 0 (0 by people) | none | **Remove** | 0/1092, no code |

### Call Log (204 rows on prod)

15 fields: Keep 15.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_call_intelligence_section` | Call Intelligence | Section Break |  | - |  |  | none | **Keep** | holds 7 custom field(s) |
| `custom_caller_name` | Caller Name | Data | read-only, list | 204/204 (100%) | 2026-11-05 | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_sentiment` | Sentiment | Select | read-only, list | 123/204 (60%) | 2026-11-05 | 0 (0 by people) | 2 code; prod: Dashboard Chart 'Call Sentiment' | **Keep** | used on prod: Dashboard Chart 'Call Sentiment'; values: (empty) 81, Neutral 65, Positive 52, Negative 6 |
| `custom_escalation_risk` | Escalation Risk | Select | read-only, list | 123/204 (60%) | 2026-11-05 | 0 (0 by people) | 2 code; prod: Dashboard Chart, Number Card, Notification | **Keep** | used on prod: Dashboard Chart, Number Card, Notification; values: Low 116, (empty) 81, Medium 7 |
| `custom_csat_score` | CSAT Score | Int | read-only | 123/204 (60%) | 2026-11-05 | 0 (0 by people) | 1 code; prod: Number Card 'Avg CSAT' | **Keep** | used on prod: Number Card 'Avg CSAT' |
| `custom_ci_column_break` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_agent_name` | Agent Name | Data | read-only | 0/204 (0%) |  | 0 (0 by people) | 1 code | **Keep** | 0/204 so far; call_intelligence.py writes it |
| `custom_voicemail_url` | Voicemail URL | Data | read-only | 1/204 (0.5%) | 2026-09-28 | 0 (0 by people) | 2 code | **Keep** | automation writes it (2 files) |
| `custom_communication` | Transcript Communication | Link | read-only | 204/204 (100%) | 2026-11-05 | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_followups_section` | Follow-ups & QA | Section Break |  | - |  |  | none | **Keep** | holds 5 custom field(s) |
| `custom_follow_up_actions` | Follow-up Actions | Small Text | read-only | 113/204 (55%) | 2026-11-05 | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files) |
| `custom_topics` | Topics | Small Text | read-only | 123/204 (60%) | 2026-11-05 | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files) |
| `custom_compliance_flags` | Compliance Flags | Small Text | read-only | 1/204 (0.5%) | 2026-07-23 | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files) |
| `custom_has_compliance_flags` | Has Compliance Flags | Check | hidden, read-only | 1/204 (0.5%) | 2026-07-23 | 0 (0 by people) | 2 code; prod: Notification 'Compliance Flag on Call' | **Keep** | used on prod: Notification 'Compliance Flag on Call' |
| `custom_analysis_json` | Analysis JSON | JSON | read-only | 123/204 (60%) | 2026-11-05 | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files) |

### Comment (50,262 rows on prod)

1 fields: Keep 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_parent_comment` | Parent Comment | Link | hidden, read-only | 3/50262 (0.0%) | 2026-10-13 | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |

### Communication (6,612 rows on prod)

1 fields: Leave 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `company` | Company | Link | read-only, not in fixtures | 6317/6612 (96%) | 2026-11-07 | 0 (0 by people) | 239 code | **Leave (ERPNext)** | ERPNext's own custom field (regional or CRM setup), not Sapphire's |

### Contact (2,879 rows on prod)

30 fields: Fix 1, Hide 3, Keep 19, Remove 7.

The app deliberately replaced ERPNext's `email_ids`/`phone_nos` child tables with flat fields (`custom_email`, `custom_phone_number`, `custom_mobile_number`) and hid the standard ones with Property Setters; that is a design decision, not a duplicate. The dead weight is the hidden legacy set (`custom_vendor_name`, `custom_full_name_and_title`, `custom_date_of_birth`, `custom_disabled`) and a second, empty Comments tab.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `primary_address` | Primary Address | Link | not in fixtures | 20/2879 (0.7%) | 2026-10-26 | 3 (3 by people) | 12 code | **Keep** | code reads/writes it (12 files) |
| `custom_preferred_contact_method` | Preferred Contact Method | Select |  | 999/2879 (35%) | 2026-11-06 | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); values: (empty) 1880, Email 984, Phone 14, Text 1 |
| `custom_email` | Email | Data | list | 1769/2879 (61%) | 2026-11-07 | 52 (50 by people) | 14 code | **Keep** | the app's flat replacements for ERPNext's email_ids/phone_nos tables (hidden by setters); 1769/1400/834 of 2879 |
| `custom_secondary_email_address` | Secondary Email | Data | list | 16/2879 (0.6%) | 2026-08-19 | 1 (1 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_map_placeholder` | Map Placeholder | HTML |  | - |  |  | 2 code | **Keep** | rendered by code (2 files) |
| `custom_vendor_name` | Vendor Name | Data | hidden | 3/2879 (0.1%) | 2026-02-25 | 0 (0 by people) | none | **Remove** | hidden, 3/2879, no code |
| `custom_full_name_and_title` | Full Name and Title | Data | hidden, read-only | 1/2879 (0.0%) | 2025-10-19 | 0 (0 by people) | none | **Remove** | hidden, 1/2879; custom_full_name_and_role (2879/2879) is the live computed title |
| `custom_account` | Account | Link | list | 1884/2879 (65%) | 2026-11-07 | 31 (22 by people) | 6 code | **Keep** | code reads/writes it (6 files) |
| `custom_title` | Title | Data |  | 1218/2879 (42%) | 2026-11-07 | 41 (41 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_date_of_birth` | Date of Birth | Date |  | 0/2879 (0%) |  | 0 (0 by people) | none | **Remove** | 0/2879, no code |
| `custom_mobile_number` | Contact Mobile Number | Data | list | 834/2879 (29%) | 2026-11-07 | 12 (10 by people) | 11 code | **Keep** | the app's flat replacements for ERPNext's email_ids/phone_nos tables (hidden by setters); 1769/1400/834 of 2879 |
| `custom_phone_number` | Phone Number | Data | list | 1400/2879 (49%) | 2026-11-07 | 36 (34 by people) | 18 code | **Keep** | the app's flat replacements for ERPNext's email_ids/phone_nos tables (hidden by setters); 1769/1400/834 of 2879 |
| `custom_description` | Description | Small Text |  | 262/2879 (9%) | 2026-11-02 | 20 (20 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_comments_tab` | Additional Information | Tab Break |  | - |  |  | setup/patch only | **Fix label** | labeled 'Additional Information' while every other doctype calls the same tab 'Comments' |
| `is_billing_contact` | Is Billing Contact | Check | not in fixtures | 7/2879 (0.2%) | 2026-10-29 | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |
| `custom_more_information` | More Information | Section Break | hidden | - |  |  | none | **Remove** | hidden empty section |
| `custom_zoho_contact_id` | Zoho Contact ID | Data | hidden, read-only | 1433/2879 (50%) | 2026-11-07 | 0 (0 by people) | none | **Keep** | hidden import key, 1433/2879 |
| `custom_column_break_byvnt` |  | Column Break |  | - |  |  | none | **Remove** | hidden empty section |
| `custom_full_name_and_role` | Full Name and Role | Data | read-only | 2879/2879 (100%) | 2026-11-07 | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_home_phone` | Home Phone | Data |  | 12/2879 (0.4%) | 2025-10-19 | 0 (0 by people) | 2 code | **Hide** | 67/12/6 of 2879, no human edit in 180 days; desk search still matches them |
| `section_break_map` | Location | Section Break | not in fixtures | - |  |  | 2 code | **Keep** | holds 2 custom field(s) |
| `custom_other_phone` | Other Phone | Data |  | 6/2879 (0.2%) | 2025-11-30 | 0 (0 by people) | 2 code | **Hide** | 67/12/6 of 2879, no human edit in 180 days; desk search still matches them |
| `custom_fax` | Fax | Data |  | 67/2879 (2%) | 2025-10-28 | 0 (0 by people) | 1 code | **Hide** | 67/12/6 of 2879, no human edit in 180 days; desk search still matches them |
| `location_map_html` | Location Map HTML | HTML | not in fixtures | - |  |  | 2 code | **Keep** | rendered by code (2 files) |
| `location_map_col_break` |  | Column Break | not in fixtures | - |  |  | setup/patch only | **Keep** | layout |
| `custom_disabled` | Disabled | Check |  | 1/2879 (0.0%) | 2025-10-28 | 0 (0 by people) | none | **Remove** | 1/2879, no code |
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Remove** | second 'Comments' tab with nothing under it; the Comments App sits in custom_comments_tab |
| `section_break_address_list` | Address Directory | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `address_list_html` | Address List HTML | HTML | not in fixtures | - |  |  | 6 code | **Keep** | rendered by code (6 files) |

### Custom DocPerm (398 rows on prod)

1 fields: Leave 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `impersonate` | Impersonate | Check | not in fixtures | 0/398 (0%) |  | 0 (0 by people) | 2 code | **Leave (framework)** | impersonation permission flag, 0 rows set; Triton clients reference it |

### Delivery Note

2 fields: Keep 2.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### DocPerm (2,208 rows on prod)

1 fields: Leave 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `impersonate` | Impersonate | Check | not in fixtures | 0/2208 (0%) |  | 0 (0 by people) | 2 code | **Leave (framework)** | impersonation permission flag, 0 rows set; Triton clients reference it |

### DocShare (53 rows on prod)

1 fields: Leave 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `impersonate` | Impersonate | Check | not in fixtures | 0/53 (0%) |  | 0 (0 by people) | 2 code | **Leave (framework)** | impersonation permission flag, 0 rows set; Triton clients reference it |

### Email Account (1 rows on prod)

2 fields: Leave 2.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `company` | Company | Link | not in fixtures | 1/1 (100%) | 2026-07-22 | 0 (0 by people) | 239 code | **Leave (ERPNext)** | ERPNext's own custom field (regional or CRM setup), not Sapphire's |
| `create_lead_from_incoming_email` | Create Lead from Incoming Emails | Check | not in fixtures | 0/1 (0%) |  | 0 (0 by people) | none | **Leave (ERPNext)** | ERPNext's own custom field (regional or CRM setup), not Sapphire's |

### Employee (22 rows on prod)

15 fields: Keep 14, Remove 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_quickbooks_user_id` | QuickBooks User ID | Data | read-only | 0/22 (0%) |  | 0 (0 by people) | 1 code | **Remove** | 0/22; the QuickBooks Time integration never mapped a user |
| `custom_default_vehicle_warehouse` | Default Vehicle Warehouse | Link |  | 0/22 (0%) |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); no row holds a value yet |
| `custom_managed_devices_section` | Assigned Devices | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `custom_managed_devices_html` | Assigned Devices | HTML | not in fixtures | - |  |  | 1 code | **Keep** | rendered by code (1 files) |
| `custom_payroll_section` | Payroll Export | Section Break |  | - |  |  | none | **Keep** | holds 3 custom field(s) |
| `custom_hr_section` | Position & Competency | Section Break |  | - |  |  | none | **Keep** | holds 2 custom field(s) |
| `custom_position` | Position | Link |  | 20/22 (91%) | 2026-10-29 | 3 (3 by people) | 15 code | **Keep** | code reads/writes it (15 files) |
| `custom_position_tier` | Tier | Int | read-only | 20/22 (91%) | 2026-10-29 | 4 (4 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_reimbursement_section` | Reimbursement | Section Break |  | - |  |  | none | **Keep** | holds 1 custom field(s) |
| `custom_reimbursement_supplier` | Reimbursement Supplier | Link |  | 9/22 (41%) | 2026-10-29 | 3 (3 by people) | 1 code | **Keep** | automation writes it (1 files) |
| `custom_comments_tab` | Comments | Tab Break |  | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |
| `custom_payroll_classification` | Payroll Classification | Select |  | 22/22 (100%) | 2026-10-29 | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); values: Hourly 22 |
| `custom_healthcare_stipend` | Healthcare Stipend (per period) | Currency |  | 0/22 (0%) |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); no row holds a value yet |
| `custom_pay_rates` | Pay Rates | Table |  | 0/22 (0%) |  |  | 3 code | **Keep** | code reads/writes it (3 files); no row holds a value yet |

### Expense Claim

1 fields: Keep 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_travel_trip` | Travel Trip | Link | read-only | n/a |  | n/a | 4 code | **Keep** | fixture for HRMS, which is not installed on prod (no table) |

### File (44,375 rows on prod)

3 fields: Keep 2, Leave 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `fac_pending_chat_attachment` | FAC Pending Chat Attachment | Check | hidden, not in fixtures | 0/44375 (0%) |  | 0 (0 by people) | none | **Leave (FAC)** | Frappe Assistant Core's field |
| `custom_drive_file_id` | Drive File ID | Data | hidden, read-only | 35216/44375 (79%) | 2026-11-07 | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_qbo_attachable_id` | QBO Attachable ID | Data | read-only | 5184/44375 (12%) | 2026-11-06 | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files) |

### GoCardless Mandate (0 rows on prod)

1 fields: Leave 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `customer` | Customer | Link | required, list, not in fixtures | 0 rows |  | 0 (0 by people) | 361 code | **Leave (payments app)** | created by the payments app |

### Issue (1 rows on prod)

1 fields: Remove 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_zoho_issue_id` | Zoho Issue ID | Data | hidden, read-only | 0/1 (0%) |  | 0 (0 by people) | none | **Remove** | 0/1; Issues are not used |

### Item Supplier (226 rows on prod)

1 fields: Keep 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `purchase_url` | Purchase URL | Data | list, not in fixtures | 157/226 (69%) | 2026-11-02 | 0 (0 by people) | 6 code | **Keep** | code reads/writes it (6 files) |

### Job Card

2 fields: Keep 2.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments Field | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Journal Entry

2 fields: Keep 2.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments Field | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Kanban Board (13 rows on prod)

1 fields: Remove 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_swimlane_field` | Swimlane Field | Data |  | 0/13 (0%) |  | 0 (0 by people) | none | **Remove** | 0/13, no code |

### Kanban Board Column (88 rows on prod)

1 fields: Remove 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_wip_limit` | WIP Limit | Int |  | 0/88 (0%) |  | 0 (0 by people) | none | **Remove** | 0/88, no code |

### Lead (227 rows on prod)

74 fields: Keep 41, Merge 10, Remove 23.

Lead is the least-used of the party doctypes (227 rows, 11 Version rows in 180 days) and the most duplicated: seven `custom_contact_*`/`custom_account_*` fields repeat standard Lead fields (`first_name`, `last_name`, `email_id`, `phone`, `job_title`, `company_name`, `website`), four `custom_primary_*` fields are empty duplicates of those, and the `primary_contact_*` set from the 2026-04 contact migration is 0/227 because `setup/custom_fields.py` never meant to give Lead the unified tab. Nearly every value was written by the Zoho import.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_contact_last_name` | Last Name | Data | list | 184/227 (81%) | 2026-10-06 | 0 (0 by people) | 1 code | **Merge into first_name / last_name** | 178/184 of 227 duplicate the standard name fields |
| `custom_column_break_jmqyy` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_service_interest` | Service Interest | Select |  | 13/227 (6%) | 2026-10-29 | 2 (2 by people) | 2 code | **Keep** | code reads/writes it (2 files); values: (empty) 214, Design 7, Service 4, Events 1, Products 1 |
| `custom_column_break_khhkf` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_first_response_due` | First Response Due | Datetime | read-only | 0/227 (0%) |  | 0 (0 by people) | 2 code | **Keep** | 0/227; lead triage SLA writes them |
| `custom_first_response_at` | First Response At | Datetime | read-only | 0/227 (0%) |  | 0 (0 by people) | 1 code | **Keep** | 0/227; lead triage SLA writes them |
| `custom_sla_alert` | SLA Alert | Select | read-only | 0/227 (0%) |  | 0 (0 by people) | 1 code | **Keep** | 0/227; lead triage SLA writes them; values: (empty) 227 |
| `custom_contact_first_name` | Contact First Name | Data | hidden, list | 178/227 (78%) | 2026-10-06 | 0 (0 by people) | 1 code | **Merge into first_name / last_name** | 178/184 of 227 duplicate the standard name fields |
| `custom_website_lead_fields` | Website Lead Fields | Section Break | hidden | - |  |  | none | **Remove** | hidden section, 0/227, no code |
| `custom_website_image_links` | Website Image Links | Text Editor |  | 0/227 (0%) |  | 0 (0 by people) | setup/patch only | **Remove** | hidden section, 0/227, no code |
| `custom_address_line_1` | Address Line 1 | Data |  | 7/227 (3%) | 2026-10-06 | 0 (0 by people) | 1 code | **Merge into Address** | 7/1/7 of 227; conversion.py copies them into an Address record |
| `custom_address_line_2` | Address Line 2 | Data |  | 1/227 (0.4%) | 2025-10-19 | 0 (0 by people) | 1 code | **Merge into Address** | 7/1/7 of 227; conversion.py copies them into an Address record |
| `custom_postal_code` | Postal Code | Data |  | 7/227 (3%) | 2026-10-06 | 0 (0 by people) | 1 code | **Merge into Address** | 7/1/7 of 227; conversion.py copies them into an Address record |
| `custom_rental_start_date` | Rental Start Date | Date |  | 0/227 (0%) |  | 0 (0 by people) | 2 code | **Keep** | 0/227; the website rental request writes them |
| `custom_rental_end_date` | Rental End Date | Date |  | 0/227 (0%) |  | 0 (0 by people) | 2 code | **Keep** | 0/227; the website rental request writes them |
| `custom_venue_zip_code` | Venue Zip Code | Data |  | 0/227 (0%) |  | 0 (0 by people) | 1 code | **Keep** | 0/227; the website rental request writes them |
| `custom_product_interested_in` | Product Interested In | Small Text |  | 0/227 (0%) |  | 0 (0 by people) | none | **Remove** | 0/227, no code |
| `custom_column_break_uhw2x` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_column_break_npwcq` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_contact_email` | Contact Email | Data | list | 164/227 (72%) | 2026-10-06 | 0 (0 by people) | 3 code | **Merge into email_id / phone** | 164/157 of 227 duplicate the standard fields |
| `custom_contact_phone_number` | Contact Phone Number | Data | list | 157/227 (69%) | 2026-10-06 | 0 (0 by people) | 3 code | **Merge into email_id / phone** | 164/157 of 227 duplicate the standard fields |
| `custom_column_break_hf2ls` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_contact_title` | Contact Title | Data | list | 175/227 (77%) | 2026-08-19 | 0 (0 by people) | none | **Merge into job_title** | 175/227; the standard field is relabeled 'Position Title' |
| `custom_contact_method` | Contact Method | Select |  | 17/227 (7%) | 2026-10-29 | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); values: (empty) 210, Email 17 |
| `custom_account_details` | Account Details | Section Break |  | - |  |  | none | **Keep** | holds 2 custom field(s) |
| `custom_account_link` | Account Link | Link | hidden, read-only | 178/227 (78%) | 2026-10-06 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_account_name` | Account Name | Data | list | 180/227 (79%) | 2026-10-06 | 0 (0 by people) | 1 code | **Merge into company_name** | 180/227; the standard field is hidden by a Property Setter |
| `custom_account_phone_number` | Account Phone Number | Data |  | 2/227 (0.9%) | 2026-07-27 | 1 (1 by people) | 2 code | **Remove** | 2 and 3 of 227; only normalize_fields lists them |
| `custom_column_break_oc19a` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_account_email` | Account Email | Data |  | 3/227 (1%) | 2026-07-27 | 2 (2 by people) | 2 code | **Remove** | 2 and 3 of 227; only normalize_fields lists them |
| `custom_account_website` | Account Website | Data |  | 16/227 (7%) | 2025-10-19 | 0 (0 by people) | 2 code | **Merge into website** | 16/227 duplicates the standard website field |
| `custom_column_break_bf7wx` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_account_type` | Account Type | Select |  | 18/227 (8%) | 2026-10-29 | 1 (1 by people) | 1 code | **Keep** | code reads/writes it (1 files); values: (empty) 209, Commercial 13, Residential 5 |
| `custom_lead_information` | Lead Information | Section Break |  | - |  |  | none | **Remove** | sections and a tab with no fields under them |
| `custom_section_break_z3gle` | Lead Details | Section Break |  | - |  |  | none | **Keep** | holds 1 custom field(s) |
| `custom_lead_details` | Lead Details | Text Editor |  | 38/227 (17%) | 2026-10-06 | 3 (3 by people) | 2 code; prod: Web Form 'contact-us' (unpublished) | **Keep** | used on prod: Web Form 'contact-us' (unpublished) |
| `custom_section_break_yal3u` |  | Section Break | hidden | - |  |  | none | **Remove** | sections and a tab with no fields under them |
| `custom_lead_source_details` | Lead Source Details | Small Text | hidden | 10/227 (4%) | 2026-10-06 | 0 (0 by people) | 1 code | **Keep** | hidden; conversion.py reads it |
| `custom_lead_source` | Lead Source | Link |  | 225/227 (99%) | 2026-10-06 | 0 (0 by people) | 13 code | **Keep** | code reads/writes it (13 files) |
| `custom_attribution_section` | Attribution | Section Break |  | - |  |  | 1 code | **Keep** | holds 10 custom field(s) |
| `custom_utm_source` | UTM Source (raw) | Data |  | 0/227 (0%) |  | 0 (0 by people) | 9 code | **Keep** | code reads/writes it (9 files); no row holds a value yet |
| `custom_utm_medium` | UTM Medium (raw) | Data |  | 0/227 (0%) |  | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files); no row holds a value yet |
| `custom_utm_campaign` | UTM Campaign (raw) | Data |  | 0/227 (0%) |  | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files); no row holds a value yet |
| `custom_utm_id` | UTM ID (Campaign ID) | Data |  | 0/227 (0%) |  | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files); no row holds a value yet |
| `custom_column_break_attribution` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_utm_content` | UTM Content (raw) | Data |  | 0/227 (0%) |  | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files); no row holds a value yet |
| `custom_utm_term` | UTM Term | Data |  | 0/227 (0%) |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); no row holds a value yet |
| `custom_gclid` | Google Click ID | Data |  | 0/227 (0%) |  | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files); no row holds a value yet |
| `custom_landing_page` | Landing Page | Small Text |  | 0/227 (0%) |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); no row holds a value yet |
| `custom_first_referrer` | First Referrer | Small Text |  | 0/227 (0%) |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); no row holds a value yet |
| `custom_attribution_captured_on` | Attribution Captured On | Datetime | read-only | 0/227 (0%) |  | 0 (0 by people) | 6 code | **Keep** | code reads/writes it (6 files); no row holds a value yet |
| `custom_zoho_id` | Zoho ID | Data | read-only | 186/227 (82%) | 2026-06-22 | 0 (0 by people) | none | **Keep** | import key, 186/227 |
| `custom_travel_trip` | Created from Travel Trip | Link | read-only | 0/227 (0%) |  | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files); no row holds a value yet |
| `primary_contact_section` | Primary Contact | Section Break | not in fixtures | - |  |  | 1 code | **Remove** | 0/227; Lead is not in setup/custom_fields.py's unified-tab matrix, these came from the 2026-04 contact migration |
| `primary_contact` | Full Name | Link | not in fixtures | 0/227 (0%) |  | 0 (0 by people) | 14 code | **Remove** | 0/227; Lead is not in setup/custom_fields.py's unified-tab matrix, these came from the 2026-04 contact migration |
| `primary_contact_job_title` | Job Title | Data | not in fixtures | 0/227 (0%) |  | 0 (0 by people) | 3 code | **Remove** | 0/227; Lead is not in setup/custom_fields.py's unified-tab matrix, these came from the 2026-04 contact migration |
| `primary_contact_col_break` |  | Column Break | not in fixtures | - |  |  | none | **Remove** | 0/227; Lead is not in setup/custom_fields.py's unified-tab matrix, these came from the 2026-04 contact migration |
| `primary_contact_phone` | Phone | Data | not in fixtures | 0/227 (0%) |  | 0 (0 by people) | 4 code | **Remove** | 0/227; Lead is not in setup/custom_fields.py's unified-tab matrix, these came from the 2026-04 contact migration |
| `primary_contact_email` | Email Address | Data | not in fixtures | 0/227 (0%) |  | 0 (0 by people) | 6 code | **Remove** | 0/227; Lead is not in setup/custom_fields.py's unified-tab matrix, these came from the 2026-04 contact migration |
| `address_and_contact` | Address and Contact | HTML | not in fixtures | - |  |  | 1 code | **Remove** | 0/227; Lead is not in setup/custom_fields.py's unified-tab matrix, these came from the 2026-04 contact migration |
| `custom_contacts__addresses_personal` | Contacts & Addresses | Tab Break | hidden | - |  |  | none | **Remove** | sections and a tab with no fields under them |
| `custom_contact_details` | Contact Details | Section Break |  | - |  |  | none | **Keep** | holds 2 custom field(s) |
| `custom_contact_link` | Contact Link | Link | hidden, read-only | 178/227 (78%) | 2026-10-06 | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_opportunity` | Opportunity | Link | read-only | 14/227 (6%) | 2026-10-06 | 0 (0 by people) | 20 code | **Keep** | code reads/writes it (20 files) |
| `custom_primary_contact` | Primary Contact | Section Break | hidden | - |  |  | none | **Remove** | 0/227 on all four; duplicates of the standard lead_name, job_title, phone and email_id |
| `custom_full_name` | Full Name | Data |  | 0/227 (0%) |  | 0 (0 by people) | none | **Remove** | 0/227 on all four; duplicates of the standard lead_name, job_title, phone and email_id |
| `custom_primary_job_title` | Job Title | Data |  | 0/227 (0%) |  | 0 (0 by people) | none | **Remove** | 0/227 on all four; duplicates of the standard lead_name, job_title, phone and email_id |
| `custom_column_break_gxi32` |  | Column Break |  | - |  |  | none | **Remove** | 0/227 on all four; duplicates of the standard lead_name, job_title, phone and email_id |
| `custom_primary_phone` | Phone | Data |  | 0/227 (0%) |  | 0 (0 by people) | 4 code | **Remove** | 0/227 on all four; duplicates of the standard lead_name, job_title, phone and email_id |
| `custom_email_address` | Email Address | Data |  | 0/227 (0%) |  | 0 (0 by people) | 2 code | **Remove** | 0/227 on all four; duplicates of the standard lead_name, job_title, phone and email_id |
| `custom_section_break_qnpv9` |  | Section Break |  | - |  |  | none | **Remove** | 'Custom Table' (Project Stakeholder), 0/227 |
| `custom_custom_table` | Custom Table | Table |  | 0/227 (0%) |  |  | none | **Remove** | 'Custom Table' (Project Stakeholder), 0/227 |
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Master Project (11 rows on prod)

12 fields: Keep 12.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_contacts__addresses` | Contacts & Addresses | Tab Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `primary_contact` | Primary Contact | Link | not in fixtures | 0/11 (0%) |  | 0 (0 by people) | 14 code | **Keep** | 0/11; created by setup/custom_fields.py's unified tab; Master Project is barely used (11 rows) |
| `section_break_contacts` | Contact Directory | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `contact_list_html` | Contact List HTML | HTML | not in fixtures | - |  |  | 5 code | **Keep** | rendered by code (5 files) |
| `section_break_map` | Location | Section Break | not in fixtures | - |  |  | 2 code | **Keep** | holds 2 custom field(s) |
| `primary_address` | Primary Address | Link | not in fixtures | 0/11 (0%) |  | 0 (0 by people) | 12 code | **Keep** | 0/11; created by setup/custom_fields.py's unified tab; Master Project is barely used (11 rows) |
| `location_map_col_break` |  | Column Break | not in fixtures | - |  |  | setup/patch only | **Keep** | layout |
| `location_map_html` | Location Map HTML | HTML | not in fixtures | - |  |  | 2 code | **Keep** | rendered by code (2 files) |
| `section_break_address_list` | Address Directory | Section Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `address_list_html` | Address List HTML | HTML | not in fixtures | - |  |  | 6 code | **Keep** | rendered by code (6 files) |
| `custom_comments_tab` | Comments | Tab Break | not in fixtures | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments App | HTML | not in fixtures | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Material Request Item

1 fields: Keep 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `purchase_links` | Buy | HTML | read-only, list, not in fixtures | - |  |  | 1 code | **Keep** | rendered by code (1 files) |

### Non Conformance (0 rows on prod)

21 fields: Keep 21.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_project` | Project | Link | required, list | 0 rows |  | 0 (0 by people) | 12 code | **Keep** | code reads/writes it (12 files) |
| `custom_severity` | Severity | Select | required, list | 0 rows |  | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_responsible_party` | Responsible party | Select | required | 0 rows |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_supplier` | Subcontractor | Link |  | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_source` | Source | Select |  | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_inspection` | Raised by inspection | Link | read-only | 0 rows |  | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_inspection_source_key` | Check | Data | read-only | 0 rows |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_scope_of_work` | Scope of Work | Link | read-only | 0 rows |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_raised_on` | Raised on | Date | read-only | 0 rows |  | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_raised_by` | Raised by | Link | read-only | 0 rows |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_due_date` | Due | Date |  | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_quality_action` | Corrective action | Link | read-only | 0 rows |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_recovery_section` | Cost recovery | Section Break |  | - |  |  | setup/patch only | **Keep** | holds 5 custom field(s) |
| `custom_critical_alert_sent_on` | Critical alert sent on | Datetime | read-only | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_recovery_status` | Recovery status | Select |  | 0 rows |  | 0 (0 by people) | setup/patch only | **Keep** | no row count (table missing or empty) |
| `custom_recovery_claimed` | Claimed | Currency |  | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_column_break_recovery` |  | Column Break |  | - |  |  | none | **Keep** | layout |
| `custom_recovery_recovered` | Recovered | Currency |  | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_recovery_closed_on` | Settled on | Date |  | 0 rows |  | 0 (0 by people) | setup/patch only | **Keep** | no row count (table missing or empty) |
| `custom_recovery_note` | Recovery note | Small Text |  | 0 rows |  | 0 (0 by people) | setup/patch only | **Keep** | no row count (table missing or empty) |
| `custom_acknowledgments` | Alert acknowledgements | Table | read-only | 0 rows |  |  | 1 code | **Keep** | code reads/writes it (1 files) |

### Payment Entry (1,489 rows on prod)

7 fields: Keep 6, Leave 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_qbo_id` | QuickBooks ID | Data | read-only, not in fixtures | 0/1489 (0%) |  | 0 (0 by people) | 2 code | **Keep** | automation writes it (2 files); no row holds a value yet |
| `workflow_state` | Workflow State | Link | hidden, not in fixtures | 1489/1489 (100%) | 2026-11-06 | 0 (0 by people) | 26 code | **Leave (Workflow)** | created by the Workflow engine |
| `custom_stripe_charge_id` | Stripe Charge ID | Data | read-only, not in fixtures | 0/1489 (0%) |  | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files); no row holds a value yet |
| `custom_stripe_payment_intent` | Stripe Payment Intent | Data | read-only, not in fixtures | 0/1489 (0%) |  | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files); no row holds a value yet |
| `custom_source_document_intake` | Source Document Intake | Link | read-only, not in fixtures | 0/1489 (0%) |  | 0 (0 by people) | 2 code | **Keep** | automation writes it (2 files); no row holds a value yet |
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments Field | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Print Settings

3 fields: Leave 3.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `compact_item_print` | Compact Item Print | Check | not in fixtures | n/a |  | n/a | none | **Leave (ERPNext)** | ERPNext's own custom field (regional or CRM setup), not Sapphire's |
| `print_uom_after_quantity` | Print UOM after Quantity | Check | not in fixtures | n/a |  | n/a | none | **Leave (ERPNext)** | ERPNext's own custom field (regional or CRM setup), not Sapphire's |
| `print_taxes_with_zero_amount` | Print taxes with zero amount | Check | not in fixtures | n/a |  | n/a | none | **Leave (ERPNext)** | ERPNext's own custom field (regional or CRM setup), not Sapphire's |

### Production Plan

2 fields: Keep 2.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments Field | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Project Template (16 rows on prod)

3 fields: Keep 3.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_template_kind` | Planner Template Kind | Select | list | 7/16 (44%) | 2026-10-13 | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files); values: (empty) 9, Bundle 6, Phase template 1 |
| `custom_review_status` | Review Status | Select | list | 7/16 (44%) | 2026-10-13 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files); values: (empty) 9, Needs review 7 |
| `custom_template_notes` | Template Notes | Small Text |  | 7/16 (44%) | 2026-10-13 | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |

### Prospect

1 fields: Keep 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_comments_field` | Comments Field | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Purchase Invoice (10 rows on prod)

5 fields: Keep 4, Leave 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_qbo_id` | QuickBooks ID | Data | read-only, not in fixtures | 0/10 (0%) |  | 0 (0 by people) | 2 code | **Keep** | automation writes it (2 files); no row holds a value yet |
| `workflow_state` | Workflow State | Link | hidden, not in fixtures | 10/10 (100%) | 2026-10-06 | 0 (0 by people) | 26 code | **Leave (Workflow)** | created by the Workflow engine |
| `custom_source_document_intake` | Source Document Intake | Link | read-only, not in fixtures | 0/10 (0%) |  | 0 (0 by people) | 2 code | **Keep** | automation writes it (2 files); no row holds a value yet |
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments Field | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Purchase Order Item (700 rows on prod)

3 fields: Keep 2, Remove 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `purchase_links` | Buy | HTML | read-only, list, not in fixtures | - |  |  | 1 code | **Keep** | rendered by code (1 files) |
| `custom_item_status` | Item Status | Select |  | 0/700 (0%) |  | 0 (0 by people) | setup/patch only | **Remove** | 0/700, no live code; values: (empty) 700 |
| `custom_budget_category` | Budget Category | Link |  | 0/700 (0%) |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); no row holds a value yet |

### Purchase Receipt (184 rows on prod)

5 fields: Keep 5.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_store_run` | Store Run | Data | read-only, not in fixtures | 0/184 (0%) |  | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files); no row holds a value yet |
| `custom_receipt_photo` | Receipt Photo | Attach Image | read-only, not in fixtures | 0/184 (0%) |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files); no row holds a value yet |
| `custom_receipt_total` | Receipt Total (Tax Included) | Currency | read-only, not in fixtures | 0/184 (0%) |  | 78 (78 by people) | 3 code | **Keep** | code reads/writes it (3 files); no row holds a value yet |
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments Field | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Quality Action (0 rows on prod)

14 fields: Keep 14.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_subject` | Subject | Data | required, list | 0 rows |  | 0 (0 by people) | 4 code | **Keep** | code reads/writes it (4 files) |
| `custom_project` | Project | Link |  | 0 rows |  | 0 (0 by people) | 12 code | **Keep** | code reads/writes it (12 files) |
| `custom_source_type` | Source | Select | required | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_non_conformance` | Non Conformance | Link |  | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_inspection` | Raised by inspection | Link | read-only | 0 rows |  | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_assigned_to` | Owner | Link | required | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_due_date` | Due | Date |  | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_priority` | Priority | Select | list | 0 rows |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_punch_list` | Punch list item | Check | list | 0 rows |  | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_client_visible` | Client visible | Check |  | 0 rows |  | 0 (0 by people) | none | **Keep** | no row count (table missing or empty) |
| `custom_verifying_inspection` | Verified at | Link | read-only | 0 rows |  | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_verification_result` | Verification result | Select | read-only | 0 rows |  | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_reopen_count` | Times reopened | Int | read-only | 0 rows |  | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_closed_on` | Closed on | Datetime | read-only | 0 rows |  | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |

### Quality Goal (0 rows on prod)

5 fields: Keep 5.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_goal_type` | Goal type | Select |  | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_project` | Project | Link |  | 0 rows |  | 0 (0 by people) | 12 code | **Keep** | code reads/writes it (12 files) |
| `custom_category` | Category | Data |  | 0 rows |  | 0 (0 by people) | none | **Keep** | no row count (table missing or empty) |
| `custom_goal_owner` | Goal owner | Link |  | 0 rows |  | 0 (0 by people) | none | **Keep** | no row count (table missing or empty) |
| `custom_annual_month` | Annual review month | Select |  | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |

### Quality Goal Objective (0 rows on prod)

1 fields: Keep 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_metric` | Measured by | Select | list | 0 rows |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |

### Quality Meeting (0 rows on prod)

4 fields: Keep 4.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_meeting_type` | Meeting type | Select |  | 0 rows |  | 0 (0 by people) | none | **Keep** | no row count (table missing or empty) |
| `custom_period_from` | Covers from | Date |  | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_period_to` | Covers to | Date |  | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_linked_review` | Period review | Link |  | 0 rows |  | 0 (0 by people) | none | **Keep** | no row count (table missing or empty) |

### Quality Review (0 rows on prod)

5 fields: Keep 5.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_project` | Project | Link | read-only | 0 rows |  | 0 (0 by people) | 12 code | **Keep** | code reads/writes it (12 files) |
| `custom_period_start` | Period start | Date | read-only | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_period_end` | Period end | Date | read-only | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_generated_by_system` | Generated by the scheduler | Check | read-only | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_summary` | Computation summary | Small Text | read-only | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |

### Quality Review Objective (0 rows on prod)

4 fields: Keep 4.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_metric` | Measured by | Data | read-only, list | 0 rows |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_actual_value` | Actual | Float | read-only, list | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_sample_size` | Sample | Int | read-only | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_computation_note` | Note | Small Text | read-only | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |

### Quotation (692 rows on prod)

3 fields: Keep 2, Leave 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `exempt_from_sales_tax` | Is customer exempted from sales tax? | Check | not in fixtures | 0/692 (0%) |  | 0 (0 by people) | none | **Leave (ERPNext)** | ERPNext's own custom field (regional or CRM setup), not Sapphire's |
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Request for Quotation (4 rows on prod)

1 fields: Keep 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_project` | Project | Link |  | 3/4 (75%) | 2026-07-21 | 0 (0 by people) | 12 code; prod: Print Format 'Request for Quotation - Sapphire' | **Keep** | used on prod: Print Format 'Request for Quotation - Sapphire' |

### Sales Invoice (1,671 rows on prod)

14 fields: Keep 13, Leave 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_dunning_state` | Dunning State | Select | read-only, not in fixtures | 0/1671 (0%) |  | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files); no row holds a value yet; values: (empty) 1671 |
| `exempt_from_sales_tax` | Is customer exempted from sales tax? | Check | not in fixtures | 0/1671 (0%) |  | 0 (0 by people) | none | **Leave (ERPNext)** | ERPNext's own custom field (regional or CRM setup), not Sapphire's |
| `custom_dunning_attempts` | Dunning Attempts | Int | read-only, not in fixtures | 0/1671 (0%) |  | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files); no row holds a value yet |
| `custom_dunning_opened_on` | Dunning Opened On | Date | read-only, not in fixtures | 0/1671 (0%) |  | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files); no row holds a value yet |
| `custom_dunning_last_error` | Dunning Last Error | Small Text | read-only, not in fixtures | 0/1671 (0%) |  | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files); no row holds a value yet |
| `custom_dunning_next_retry` | Dunning Next Retry | Date | read-only, not in fixtures | 0/1671 (0%) |  | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files); no row holds a value yet |
| `custom_stripe_payment_link` | Stripe Payment Link | Small Text | read-only, not in fixtures | 1/1671 (0.1%) | 2026-09-25 | 0 (0 by people) | 2 code; prod: Print Format 'Sales Invoice - Sapphire' | **Keep** | used on prod: Print Format 'Sales Invoice - Sapphire' |
| `custom_dunning_last_attempt` | Dunning Last Attempt | Datetime | read-only, not in fixtures | 0/1671 (0%) |  | 0 (0 by people) | 1 code | **Keep** | automation writes it (1 files); no row holds a value yet |
| `custom_stripe_payment_status` | Stripe Payment Status | Select | read-only, not in fixtures | 1/1671 (0.1%) | 2026-09-25 | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files); values: (empty) 1670, Unpaid 1 |
| `custom_maintenance_record` | Maintenance Record | Link |  | 0/1671 (0%) |  | 0 (0 by people) | 2 code | **Keep** | 0/1671; maintenance billing and rental invoicing write them |
| `custom_rental_booking` | Rental Booking | Link | read-only | 0/1671 (0%) |  | 0 (0 by people) | 16 code | **Keep** | 0/1671; maintenance billing and rental invoicing write them |
| `custom_rental_invoice_kind` | Rental Invoice | Select | read-only | 0/1671 (0%) |  | 0 (0 by people) | 5 code | **Keep** | 0/1671; maintenance billing and rental invoicing write them; values: (empty) 1671 |
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments Field | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Sales Order (0 rows on prod)

6 fields: Keep 5, Leave 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `exempt_from_sales_tax` | Is customer exempted from sales tax? | Check | not in fixtures | 0 rows |  | 0 (0 by people) | none | **Leave (ERPNext)** | ERPNext's own custom field (regional or CRM setup), not Sapphire's |
| `custom_display_labor_hours` | Display Labor Hours on Portal | Check |  | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_last_visit_date` | Last Visit Date | Date |  | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_next_predictive_visit` | Next Predictive Visit | Date |  | 0 rows |  | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments Field | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Sales Order Item (0 rows on prod)

5 fields: Keep 5.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_asset` | Asset | Link |  | 0 rows |  | 0 (0 by people) | setup/patch only | **Keep** | no row count (table missing or empty) |
| `custom_serial_no` | Serial No | Link |  | 0 rows |  | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files) |
| `custom_maintenance_frequency` | Maintenance Frequency | Select |  | 0 rows |  | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |
| `custom_last_visit_date` | Last Visit Date | Date | read-only | 0 rows |  | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_next_predictive_visit` | Next Predictive Visit | Date | read-only | 0 rows |  | 0 (0 by people) | 3 code | **Keep** | code reads/writes it (3 files) |

### Serial No (41 rows on prod)

8 fields: Hide 3, Keep 5.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_pump_make_model` | Pump Make/Model | Data |  | 0/41 (0%) |  | 0 (0 by people) | setup/patch only | **Hide** | 0/0/1 of 41 serials; the maintenance profile holds this now |
| `custom_filtration_media_type` | Filtration Media Type | Data |  | 1/41 (2%) | 2026-10-05 | 0 (0 by people) | setup/patch only | **Hide** | 0/0/1 of 41 serials; the maintenance profile holds this now |
| `custom_water_volume` | Water Volume | Data |  | 0/41 (0%) |  | 0 (0 by people) | setup/patch only | **Hide** | 0/0/1 of 41 serials; the maintenance profile holds this now |
| `custom_site_instructions` | Site Instructions | Small Text |  | 39/41 (95%) | 2026-10-13 | 2 (2 by people) | 3 code | **Keep** | code reads/writes it (3 files) |
| `custom_project` | Project | Link |  | 39/41 (95%) | 2026-10-13 | 0 (0 by people) | 12 code | **Keep** | code reads/writes it (12 files) |
| `custom_reading_overrides` | Chemistry Reading Overrides | Table |  | 8/41 (20%) | 2026-10-13 |  | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Supplier Quotation

2 fields: Keep 2.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_comments_tab` | Comments | Tab Break |  | - |  |  | setup/patch only | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments App | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Supplier Quotation Item (51 rows on prod)

1 fields: Remove 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_required_by` | Required By | Date |  | 0/51 (0%) |  | 0 (0 by people) | none | **Remove** | 0/51, no code |

### Timesheet

2 fields: Keep 2.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments Field | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

### Timesheet Detail (14 rows on prod)

1 fields: Keep 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_job_interval` | Job Interval | Link | read-only | 14/14 (100%) | 2026-10-27 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |

### ToDo (1,970 rows on prod)

5 fields: Keep 4, Remove 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_start_date` | Start Date | Date | hidden | 14/1970 (0.7%) | 2026-10-21 | 0 (0 by people) | none | **Remove** | hidden, 14/1970, no code |
| `custom_repeat` | Repeat | Select |  | 0/1970 (0%) |  | 0 (0 by people) | 5 code | **Keep** | code reads/writes it (5 files); no row holds a value yet; values: (empty) 1970 |
| `custom_checklist` | Checklist | Table |  | 0/1970 (0%) |  |  | 3 code | **Keep** | code reads/writes it (3 files); no row holds a value yet |
| `custom_triage_batch` | Closed by Backlog Triage | Data | hidden, read-only | 941/1970 (48%) | 2026-11-04 | 0 (0 by people) | 1 code | **Keep** | code reads/writes it (1 files) |
| `custom_escalated_on` | Escalated On | Date | hidden, read-only | 4/1970 (0.2%) | 2026-11-03 | 0 (0 by people) | 2 code | **Keep** | code reads/writes it (2 files) |

### UTM Campaign (0 rows on prod)

1 fields: Remove 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `crm_campaign` | Messaging CRM Campagin | Link | not in fixtures | 0 rows |  | 0 (0 by people) | none | **Remove** | frappe CRM leftover, 0 rows |

### User (48 rows on prod)

5 fields: Keep 1, Leave 2, Remove 2.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `assistant_enabled` | Enable Assistant Access | Check | not in fixtures | 44/48 (92%) | 2026-11-06 | 5 (5 by people) | setup/patch only | **Leave (FAC)** | Frappe Assistant Core's field; hooks.py reads it |
| `cover_image` | Cover Image | Attach Image | not in fixtures | 0/48 (0%) |  | 0 (0 by people) | 4 code | **Leave (other app)** | the training player reads it |
| `custom_dock_profile` | Dock Profile | Select |  | 3/48 (6%) | 2026-10-29 | 4 (4 by people) | setup/patch only | **Keep** | 3/48; setup/dock_profiles.py and hooks.py apply it to the Desk dock; values: (empty) 45, CEO 1, Management 1, Purchasing 1 |
| `twitter` | Twitter ID | Data | not in fixtures | 0/48 (0%) |  | 0 (0 by people) | none | **Remove** | orphans from an uninstalled app, 0/48 |
| `work_environment` | Work Environment | Section Break | not in fixtures | - |  |  | none | **Remove** | orphans from an uninstalled app, 0/48 |

### Vehicle Log

1 fields: Keep 1.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_travel_trip` | Travel Trip | Link | read-only | n/a |  | n/a | 4 code | **Keep** | fixture for HRMS, which is not installed on prod (no table) |

### Web Form (7 rows on prod)

10 fields: Leave 10.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `accept_payment` | Accept Payment | Check | not in fixtures | 0/7 (0%) |  | 0 (0 by people) | none | **Leave (payments app)** | created by the payments app |
| `payments_tab` | Payments | Tab Break | not in fixtures | - |  |  | none | **Leave (payments app)** | created by the payments app |
| `amount` | Amount | Currency | not in fixtures | 0/7 (0%) |  | 0 (0 by people) | 146 code | **Leave (payments app)** | created by the payments app |
| `payments_cb` |  | Column Break | not in fixtures | - |  |  | none | **Leave (payments app)** | created by the payments app |
| `payment_gateway` | Payment Gateway | Link | not in fixtures | 0/7 (0%) |  | 0 (0 by people) | none | **Leave (payments app)** | created by the payments app |
| `payment_button_label` | Button Label | Data | not in fixtures | 7/7 (100%) | 2026-07-12 | 0 (0 by people) | none | **Leave (payments app)** | created by the payments app |
| `amount_field` | Amount Field | Select | not in fixtures | 0/7 (0%) |  | 0 (0 by people) | 4 code | **Leave (payments app)** | created by the payments app; values: (empty) 7 |
| `payment_button_help` | Button Help | Text | not in fixtures | 0/7 (0%) |  | 0 (0 by people) | none | **Leave (payments app)** | created by the payments app |
| `amount_based_on_field` | Amount Based On Field | Check | not in fixtures | 0/7 (0%) |  | 0 (0 by people) | none | **Leave (payments app)** | created by the payments app |
| `currency` | Currency | Link | not in fixtures | 0/7 (0%) |  | 0 (0 by people) | 81 code | **Leave (payments app)** | created by the payments app |

### Work Order

2 fields: Keep 2.

| Field | Label | Type | Flags | Filled / rows | Last value edit | Versions 180d | Read by | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| `custom_comments` | Comments | Tab Break |  | - |  |  | 1 code | **Keep** | holds 1 custom field(s) |
| `custom_comments_field` | Comments Field | HTML |  | - |  |  | 10 code | **Keep** | rendered by code (10 files) |

## Property Setters

Prod holds 667 Property Setters; the repo's fixtures manage 514 of them and every fixture setter
exists on prod. The 153 prod-only setters are almost all ERPNext's own (naming series options,
`rounded_total`/`in_words` visibility on the sales and purchase documents, `scan_barcode`) or
Workflow-created; the Sapphire-owned ones are listed under "On prod but not in the repo".

What the fixture setters do, by property: hidden 115, description 43, field_order 36, label 35,
allow_in_quick_entry 30, in_list_view 27, options 17, collapsible 17, in_standard_filter 16,
permlevel 15, default 12, reqd 8, and a long tail.

**Labels changed** (fixture setters): Customer is relabeled Account throughout (`customer_name`
"Account Name", `customer_type` "Account Type", `customer_group` "Account Group", Customer Group
"Account Group Name" / "Parent Account Group", Project `customer_details` "Account Details");
Project `project_type` "Project Stage", `expected_start_date`/`expected_end_date` "Scheduled Start
Date"/"Scheduled End Date"; Task `is_group` "Is Parent"; ToDo `allocated_to` "Assigned To"; Lead
`job_title` "Position Title", `title` "Lead Title"; Opportunity `customer_address` "Billing
Address", `contact_email` "Email Address", `opportunity_type` "Opportunity Type (Value Stream)";
the Contacts & Addresses tab is named the same on Customer, Supplier, Opportunity, Prospect and the
five purchase documents; Supplier `supplier_group` "Primary Supplier Group". Two are warnings
written into labels: Contact `mobile_no` "DONTUSE-Mobile No" and Opportunity `contact_mobile`
"DONOTUSE-Contact Mobile" (the Opportunity one is also hidden by a setter, so the label is dead).

**Options changed**: Project `status` (Active, Client Hold, Parked, Completed, Invoiced, Paid,
Canceled), Task `status` (Open, Working, Invoiced, Completed, Canceled, Pending Review, Overdue,
Template), Opportunity `status` (Qualification, Needs Analysis, Proposal/Price Quote,
Negotiation/Review, Lost, Closed Won, On Hold), Customer `customer_type` (Commercial, Residential,
Partnership), Non Conformance and Quality Action `status`, Quality Goal `frequency`; `website`
fields get `options: URL` on Company, Customer, Lead, Opportunity and Supplier; Contact `email_id`,
`mobile_no`, `phone` and Opportunity `job_title` have their options cleared.

**Required**: the fixture setters make nothing newly required except `Opportunity.opportunity_owner`;
on prod `Item.item_code` (reqd 1, set in the Desk in 2026), `Lead.first_name` and `Employee.naming_series` are
also required, and `Address.country`, `Customer.naming_series`, `Supplier.naming_series`,
`Item.naming_series`, `Employee.employee_number`, `Asset.purchase_date` are made optional.
No Property Setter makes a hidden field required.

**Hidden standard fields**: 110 on prod, 115 in fixtures (fixtures also hide fields on doctypes
outside this audit's row-count scope). By doctype: Opportunity 29, Project 22, Customer 20, Lead
14, Contact 8, Supplier 8, Task 3, ToDo 2, Employee 1, Item 1, Purchase Receipt 1, Sales Invoice 1.
Among them are whole standard tabs (Connections, Costing, Progress, More Info on Project; Portal
Users, Tax, Accounting on Customer and Supplier; Items on Opportunity) and the standard fields the
app replaced with its own (Contact `email_id`/`phone`/`mobile_no` and their tables; Lead
`company_name`, which `custom_account_name` then duplicates; Opportunity `utm_*`, which
`custom_utm_*` duplicate). Hiding the standard field and adding a custom twin is the pattern behind
most of the Merge recommendations above.

**Field order**: 36 fixture setters move fields (`field_order`) on Opportunity, Project, Task,
Customer, Lead, Contact, Item, Purchase Order Item and others. They are not listed per field; the
layout tables above show the resulting `insert_after` chains.

## On prod but not in the repo

**Custom Fields**: 247 exist on prod and not in `fixtures/custom_field.json`. They fall into four
groups (the per-DocType tables above flag each as "not in fixtures"):

1. **Created by this repo's own code on migrate (201)**: `setup/custom_fields.py` (the unified
   Contacts & Addresses tab: 43 widget/section fields plus `primary_contact`, `primary_address` and the
   three `primary_contact_*` copies on Customer, Supplier, Opportunity, Project, Master Project,
   Contact), `water_engineering/setup.py` (63 Item spec fields), `stripe_payments/setup.py` (17),
   `setup/supplier_groups.py` (3), `accounting_intake/setup.py` (6), `device_management/setup.py` (2),
   `product_configurator/setup.py` (1), and one-shot patches (`add_project_procurement_buttons`,
   `add_project_pick_routing_button`, `add_po_approval_stamp_fields`, `add_po_order_stage_field`,
   `add_store_run_receipt_fields`, `add_item_purchase_links`, `add_opportunity_probability_by_hand`,
   `migrate_contact_data`). These are version-controlled but **not fixture-managed**: a Desk edit to
   one of them is not overwritten by `bench migrate`, and removing one needs `delete_doc`, not a
   fixture change. Worth a follow-up of its own: move the stable ones into fixtures.
2. **Mistakes (9)**: the Primary Contact section, widgets and Comments tab on the chart-of-accounts
   DocType `Account` (0/392 filled). Remove.
3. **Leftovers of uninstalled apps (6)**: Assignment Rule `assign_condition_json` /
   `unassign_condition_json` (Helpdesk), CRM Deal `erpnext_customer` and UTM Campaign `crm_campaign`
   (frappe CRM; `tabCRM Deal` no longer exists), User `twitter` and `work_environment`. Remove.
4. **Other installed apps' and ERPNext's own fields (33)**: Web Form payment fields and GoCardless
   Mandate `customer` (payments app), File `fac_pending_chat_attachment` and User `assistant_enabled`
   (Frappe Assistant Core), Print Settings, `exempt_from_sales_tax` on Customer/Quotation/Sales
   Order/Sales Invoice and Supplier `irs_1099` (ERPNext US regional), Address `tax_category` /
   `is_your_company_address`, Communication and Email Account `company`, Email Account
   `create_lead_from_incoming_email`, `workflow_state` on three doctypes (Workflow engine), DocPerm /
   Custom DocPerm / DocShare `impersonate`, User `cover_image` (read by the training player). Leave.

One fixture field is missing on prod: `Employee Advance.custom_travel_trip` (HRMS is not installed;
`custom_field_hrms.json` is for a site that has it).

**Property Setters**: 153 on prod and not in fixtures, 0 the other way. 88 of them were made in
the Desk by a Sapphire user, not by a migration: the 2026-03 "rounded total" pass on the nine sales
and purchase documents (`rounded_total`, `base_rounded_total`, `in_words` hidden/print_hide,
`disable_rounded_total` default) and the barcode pass (`scan_barcode`, item `barcode`,
`from_warehouse`/`target_warehouse` hidden), plus Item `custom_sku` and `item_group` in_list_view
(a Sapphire user), Item `item_code` reqd/hidden and `naming_series` hidden, Address `country` reqd 0 (Nik,
2026-08-25), Quotation `quotation_to` link_filters and Warehouse `allow_rename`. None of these are in
the repo, so a fresh install would not reproduce the Desk Nik sees; they belong in
`fixtures/property_setter.json` (a fixture-only PR, no patch). The other 65 are owned by Administrator:
the per-doctype `naming_series` options that ERPNext's setup writes, Customer/Supplier/Employee
`naming_series` reqd/hidden, Asset `purchase_date`/`available_for_use_date` mandatory_depends_on, and
the 2026-11-06 batch that sets list-view and default properties on Opportunity, Project, Lead, Item
and Contact (the form-kit "guided view settings" work); that batch should be captured into fixtures
or confirmed as deliberate site settings. The full list is in the appendix table.

## Inconsistencies

**Same concept, different labels across DocTypes**
- The hand-off copies: `custom_general_scope_description` is "General Scope Description" on
  Opportunity and "Detailed Scope Description" on Project. `custom_scope_of_work` is "Scope of Work"
  on Project and Non Conformance but "Project scope of work" on Purchase Order.
- `custom_comments_field` is "Comments App" on 17 doctypes and "Comments Field" on 13; the tab
  around it is "Comments" everywhere except Contact ("Additional Information").
- `primary_contact` is labeled "Full Name" on six doctypes and "Primary Contact" on Master Project.
- The phone/email family: "Phone" is `primary_contact_phone`, `custom_primary_phone`, `custom_phone`
  (Customer and Supplier, two different fieldtypes); "Email Address" is `primary_contact_email`,
  `custom_primary_email_address`, `custom_email_address`; "Job Title" is `primary_contact_job_title`,
  `custom_primary_position` (Customer), `custom_primary_role` (Project), `custom_primary_job_title`
  (Lead), `custom_job_title` (Supplier). Account Phone Number is `custom_accounts_phone_number` on
  Customer and `custom_account_phone_number` on Lead. The Merge/Remove rows above collapse these to
  the `primary_contact_*` set.
- `custom_travel_trip` is "Travel Trip" on the HRMS doctypes and "Created from Travel Trip" on Lead
  and Opportunity (defensible: different direction of the link).

**Spelling and casing (American English, "Canceled" with one l)**
- `Sales Invoice.custom_dunning_state` has the option `Cancelled`. Fix to `Canceled`.
- `tabTask.status` holds 166 rows of `Canceled` (the option) and 14 rows of `Cancelled` (not an
  option any more). Backfill the 14.
- Project labels in ALL CAPS: PROJECT ADDRESS, CUSTOMER NAME, CUSTOMER PHONE, CUSTOMER EMAIL,
  CONTACT PHONE, DELIVERABLE-1..5 (all Zoho leftovers; removed or relabeled above).
- `Supplier.custom_supplier_groups_search` label "All Supplier Groups (Search)" and
  `Opportunity.custom_handoff_meeting_on` "Hand-Off Meeting Recorded On" are fine; "Hand-Off" vs
  "Handoff" is consistent (always hyphenated in labels).
- Lower-case starts are confined to the quality module's labels ("Raised on", "Settled on",
  "Covers from"), which is that module's house style, not an error.

**Select options that differ for the same concept**
- Contact method: `Contact.custom_preferred_contact_method` and
  `Customer.custom_preferred_contact_method` are "Email, Phone, Text"; `Lead.custom_contact_method`
  has the same options under a different fieldname and label ("Contact Method").
- Location source: `Address.custom_location_source` is "Google, Manual";
  `Project.custom_site_location_source` is "Geocoded, Manual" for the same fact.
- Priority: `Project.custom_project_priority` and `Project.custom_company_priority` share one
  option list of 1..30 plus "Not Assigned", "Maintenance", "Repair Visit", "Delivery": a rank and a
  job type in one Select. On prod `custom_project_priority` is Not Assigned 350, Repair Visit 146,
  Maintenance 33 and numbers 140; `custom_company_priority` is Not Assigned 584 and numbers 30. The
  type values belong on `project_type` ("Project Stage"), which already has Service/Build/Design/
  Events/Internal (and three stale values: Group Projects 3, Other 8, Overhead 5).
- Status: `Project.status` was reduced to Active, Client Hold, Parked, Completed, Invoiced, Paid,
  Canceled, but the prod Dashboard Charts "Active Projects Count", "Overdue Projects Count" and the
  Number Card "Open Projects" still filter on `Open` or `Cancelled`, so they count nothing. The Task
  charts "Task Velocity (Last 8 Weeks)" and "Task Status Breakdown by Project" filter on `Closed`,
  which Task's setter removed. Fix or delete those charts.
- `Opportunity.opportunity_type` is relabeled "Opportunity Type (Value Stream)" and then hidden by
  a setter; 583/855 rows are empty and 262 say "Sales". The real value stream is
  `custom_value_stream` (Table MultiSelect). Drop the label setter, keep it hidden.
- Value stream naming: the Lead `custom_service_interest` options are Design, Build, Service,
  Events, Products; the Value Stream child rows use the same words, and `project_type` adds
  Internal. Consistent, after the Rent to Events rename.

**Fields that duplicate a standard field**
- Lead: `custom_contact_first_name`/`last_name` vs `first_name`/`last_name`; `custom_contact_email`
  vs `email_id`; `custom_contact_phone_number` vs `phone`; `custom_contact_title` vs `job_title`;
  `custom_account_name` vs `company_name` (hidden); `custom_account_website` vs `website`;
  `custom_utm_*` (raw) vs the `utm_*` Links, by design (the raw capture is kept beside the
  normalized Link).
- Item: `custom_item_identifier` labeled "Item Code" beside `item_code`.
- Customer: `custom_prospect` (Check) vs the hidden `prospect_name` Link and the "Prospect" option
  of `custom_account_status`.
- Supplier: `primary_contact` vs the standard `supplier_primary_contact` (the app's unified tab
  owns the former; the standard section is hidden).
- Project: `custom_customer_name` vs `customer`.

**Fields that duplicate another custom field**: the `custom_primary_*` sets on Customer, Project,
Supplier and Lead vs `primary_contact_*`; Opportunity `custom_project` vs `custom_created_project`;
Opportunity `custom_description` vs `custom_general_scope_description` vs
`custom_opportunity_summary`; Item `custom_pump_hp`/`voltage`/`phase`/`fla_amps` vs
`custom_elec_*`; Item `custom_pump_cut_sheet` vs `custom_spec_cut_sheet`; Item
`custom_pump_curve_chart` vs `custom_pump_curves_html`; Item `custom_purchase_link` vs Item
Supplier `purchase_url`; Project `custom_project_address` vs `custom_customer__lead_address` vs
`primary_address` (three site-address fields); Contact `custom_full_name_and_title` vs
`custom_full_name_and_role`.

**Fields on a parent that repeat a child table's data**: Customer `custom_projects`,
`custom_leads`, `custom_opportunities` (read-only Table MultiSelects mirroring the hidden
Connections tab; kept because the Connections tab is hidden, except `custom_leads`, 0 rows);
Project `custom_contacts__address_table` (Project Stakeholder) beside the Contact Directory
widget; Sapphire Maintenance Record `technician` beside the `crew` table.

**Sections or tabs with one field or none** (approximate, by `insert_after` chains): empty hidden
sections and tabs on Project (`custom_zoho_data`, `custom_misc`, `custom_section_break_s5jqn`),
Opportunity (`custom_section_break_bqypn`, `custom_contacts__address`), Task
(`custom_section_break_8wegw`), Lead (`custom_lead_information`, `custom_section_break_yal3u`,
`custom_contacts__addresses_personal`), Contact (`custom_more_information`, `custom_comments`),
Supplier (`custom_section_break_gl6o7`), Address (`custom_section_break_qcwmy`), User
(`work_environment`); one-field sections that are fine because they wrap a widget (Gantt Chart,
Tasks Tree, Material Pickup, Contact Directory, Address Directory, Location, Assigned Devices) or a
single note field (Schedule Notes, Budget Notes, Lead Details, Reimbursement).

**Required fields that are empty on most rows**: `Opportunity.custom_value_stream` (required,
309/855 filled: the 582 historical imports predate it), `Task.custom_repeat_every` (required,
always 1 by default, feature dead), Non Conformance and Quality Action required fields (0 rows, new
module). `Lead.first_name` reqd by setter while `custom_contact_first_name` holds the Zoho names.

**Hidden fields that are still required**: none, on prod or in fixtures.

**Stale prod artefacts found on the way**: 13 Kanban Boards (two per-project Task boards, "Lead
Pipeline" and "Kanban View Leads", "Opportunity Pipeline" beside "Opportunity", "Project Kanban" on
a status field that no longer has those columns); the four charts above with impossible filters;
the "Opportunity Scope Schedule Budget Ranking", "Opportunity Status for Rankings Mandatory" and 60
other disabled Desk scripts that can be deleted now that the app owns the logic.

## Removal plan

Two rules frame every step. First, the repo's two-step deletion: removing a
record from `fixtures/custom_field.json` only stops managing it; the database row survives until a
one-shot patch calls `frappe.delete_doc("Custom Field", name)`. Second, `bench migrate` is the
deploy: a patch that raises aborts the deploy half-way, so each patch guards with
`frappe.db.exists` and deletes with `ignore_missing=True`, and a Custom Field that no longer exists
on prod is a no-op, not an error. Dropping a Custom Field drops its column, so values are gone
with it: every field below with rows holding a value gets an export (CSV attached to the PR, or a
Comment on each record for the free-text ones) before the patch runs.

The table lists, per DocType, the fields recommended **Remove**, how many rows hold a value, whether
the field is fixture-managed (fixture change plus patch) or code-created (patch only), and the live
code that mentions the field and must change in the same PR. Patches are not written here.

| DocType | Fields to remove | Rows holding a value (archive first) | Fixture change | Code to change |
|---|---|---|---|---|
| Task | `custom_section_break_8wegw`, `custom_automation_settings`, `custom_is_recurring`, `custom_next_task_created`, `custom_column_break_zkulk`, `custom_frequency`, `custom_repeat_every`, `custom_sunday`, `custom_monday`, `custom_tuesday`, `custom_wednesday`, `custom_thursday`, `custom_friday`, `custom_saturday` | custom_frequency 2923; custom_repeat_every 8703 | in fixtures | project_enhancements/page/project_dashboard/project_dashboard.py, public/js/project_enhancements/task_tree_manager.js, tasks.py |
| Project | `custom_zoho_data`, `custom_scope_rank`, `custom_schedule_rank`, `custom_budget_rank`, `custom_primary_contact_field`, `custom_primary_first_name`, `custom_primary_role`, `custom_column_break_k8xix`, `custom_primary_phone`, `custom_primary_email_address`, `custom_address__contact`, `custom_address_html`, `custom_address_display`, `custom_column_break_em6sv`, `custom_contact_html`, `custom_contact_display`, `custom_section_break_s5jqn`, `custom_scope_contributors`, `custom_section_break_kyvkd`, `custom_quickbooks_jobcode_id`, `custom_project_spend`, `custom_misc`, `custom_section_break_unn4x`, `custom_column_break_vp5qd`, `custom_deliverable1`, `custom_deliverable2`, `custom_deliverable3`, `custom_deliverable4`, `custom_deliverable5`, `custom_column_break_8lbte`, `custom_notes_html`, `custom_comments_table` | custom_scope_rank 97; custom_schedule_rank 97; custom_budget_rank 97; custom_address_display 2; custom_scope_contributors 1; custom_deliverable1 33; custom_deliverable2 22; custom_deliverable3 13; custom_deliverable4 7; custom_deliverable5 2; custom_comments_table 124 | in fixtures | api/desk_search.py, api/travel.py, crm_enhancements/api.py, form_kit/api.py, form_kit/blocks.py, public/js/crm_enhancements/opportunity_migrated_scripts.js, public/js/info_blocks/blocks.js, quickbooks_time/api.py, utils/normalize_fields.py |
| Opportunity | `custom_scope_rank`, `custom_schedule_rank`, `custom_budget_rank`, `custom_section_break_ieovn`, `custom_scope_contributors`, `custom_estimated_cost`, `custom_section_break_bqypn` | custom_scope_rank 206; custom_schedule_rank 206; custom_budget_rank 206; custom_scope_contributors 1; custom_estimated_cost 2 | in fixtures | crm_enhancements/api.py, form_kit/blocks.py, form_kit/config/opportunity.py, public/js/crm_enhancements/opportunity_migrated_scripts.js, public/js/info_blocks/blocks.js, script_migrations/opportunity.py |
| Customer | `custom_parent_account`, `custom_leads`, `custom_shipping_address`, `custom_contacts`, `custom_primary_contact`, `custom_primary_first_name`, `custom_primary_position`, `custom_column_break_z913g`, `custom_primary_phone`, `custom_primary_email_address`, `custom_section_break_bhxic`, `custom_contacts__address_table`, `custom_phone` | custom_parent_account 5; custom_shipping_address 25; custom_primary_first_name 3; custom_primary_position 2; custom_primary_phone 1; custom_primary_email_address 1; custom_contacts__address_table 5; custom_phone 520 | in fixtures | api/desk_search.py, api/travel.py, crm_enhancements/api.py, public/js/customer.js, sync_contact.py, utils/normalize_fields.py, utils/normalize_fixes.py |
| Supplier | `custom_section_break_gl6o7`, `custom_column_break_ycp3n`, `custom_column_break_rrn0r`, `custom_primary_contact`, `custom_full_name`, `custom_job_title`, `custom_column_break_ncqbr`, `custom_phone`, `custom_email_address`, `custom_addresses`, `custom_address_list` | custom_address_list 3 | in fixtures | sync_contact.py, utils/normalize_fields.py, utils/normalize_fixes.py |
| Item | `custom_pump_curve` | none | not in fixtures (delete_doc only) | public/js/water_engineering/pump_curve_chart.js, water_engineering/api/designer.py, water_engineering/catalog_reader.py |
| Account | `custom_comments_tab`, `primary_contact_section`, `primary_contact`, `primary_contact_job_title`, `primary_contact_col_break`, `primary_contact_phone`, `primary_contact_email`, `address_and_contact`, `custom_comments_field` | none | mixed: drop the fixture rows and delete_doc all | form_kit/blocks.py, form_kit/config/item.py, form_kit/config/opportunity.py, public/js/account.js, public/js/comments_auto.js, public/js/customer.js, public/js/form_kit/ui/record_tools.js, public/js/global_enhancements/contact_address_quick_entry.js, public/js/global_enhancements/unified_tab_controller.js, sync_contact.py and 1 more |
| Asset | `custom_pump_make_model`, `custom_filtration_media_type`, `custom_water_volume`, `custom_site_instructions`, `custom_under_warranty`, `custom_warranty_expiry_date` | none | in fixtures | sapphire_maintenance/doctype/sapphire_maintenance_record/sapphire_maintenance_record.js |
| Assignment Rule | `assign_condition_json`, `unassign_condition_json` | assign_condition_json 3 | not in fixtures (delete_doc only) | none |
| CRM Deal | `erpnext_customer` | none | not in fixtures (delete_doc only) | none |
| CRM Note | `custom_attachment` | none | in fixtures | none |
| Contact | `custom_vendor_name`, `custom_full_name_and_title`, `custom_date_of_birth`, `custom_more_information`, `custom_column_break_byvnt`, `custom_disabled`, `custom_comments` | custom_vendor_name 3; custom_full_name_and_title 1; custom_disabled 1 | in fixtures | public/js/global_enhancements/activity_first_tab_only.js |
| Employee | `custom_quickbooks_user_id` | none | in fixtures | quickbooks_time/api.py |
| Issue | `custom_zoho_issue_id` | none | in fixtures | none |
| Kanban Board | `custom_swimlane_field` | none | in fixtures | none |
| Kanban Board Column | `custom_wip_limit` | none | in fixtures | none |
| Lead | `custom_website_lead_fields`, `custom_website_image_links`, `custom_product_interested_in`, `custom_account_phone_number`, `custom_account_email`, `custom_lead_information`, `custom_section_break_yal3u`, `primary_contact_section`, `primary_contact`, `primary_contact_job_title`, `primary_contact_col_break`, `primary_contact_phone`, `primary_contact_email`, `address_and_contact`, `custom_contacts__addresses_personal`, `custom_primary_contact`, `custom_full_name`, `custom_primary_job_title`, `custom_column_break_gxi32`, `custom_primary_phone`, `custom_email_address`, `custom_section_break_qnpv9`, `custom_custom_table` | custom_account_phone_number 2; custom_account_email 3 | mixed: drop the fixture rows and delete_doc all | api/desk_search.py, api/travel.py, crm_enhancements/fountain_move/conversion.py, form_kit/blocks.py, form_kit/config/opportunity.py, public/js/global_enhancements/contact_address_quick_entry.js, public/js/global_enhancements/primary_contact.js, public/js/global_enhancements/unified_tab_controller.js, public/js/info_blocks/blocks.js, public/js/kanban_customization.js and 3 more |
| Purchase Order Item | `custom_item_status` | none | in fixtures | none |
| Supplier Quotation Item | `custom_required_by` | none | in fixtures | none |
| ToDo | `custom_start_date` | custom_start_date 14 | in fixtures | none |
| UTM Campaign | `crm_campaign` | none | not in fixtures (delete_doc only) | none |
| User | `twitter`, `work_environment` | none | not in fixtures (delete_doc only) | none |

### Order of PRs

1. **Ranks** (Nik's example): the six rank fields, `validate_ranks_on_won` and its hook, the
   migrated client-script functions, the form-kit Closed Won step and board panel entries, the
   `api.py` mapping lines and the four tests. Export 206 Opportunity and 97 Project rank triples.
2. **Dead features and empty layout**: Task recurring-task group (13 fields), Project
   `custom_quickbooks_jobcode_id`, Employee `custom_quickbooks_user_id`, Item `custom_pump_curve`
   (legacy table) and the empty sections and tabs on Project, Opportunity, Task, Lead, Contact,
   Supplier, Address. No data to archive except the Task defaults, which are not data.
3. **Duplicate contact sets**: the hidden `custom_primary_*` sections and fields on Customer,
   Supplier, Project and Lead, the `primary_contact_*` set on Lead and on `Account`, Customer
   `custom_phone`, the hidden Stakeholders tables on Customer, Supplier and Lead, Project's dead
   address/contact block. Update `api/desk_search.py`, `api/travel.py`, `utils/normalize_fields.py`,
   `utils/normalize_fixes.py` and `sync_contact.py` in the same PR; archive the handful of values
   (Customer 3/2/1/1, Supplier stakeholders 3, Customer stakeholders 5).
4. **Zoho leftovers on Project**: `custom_deliverable1..5`, `custom_comments_table` (Project Notes,
   124 projects) and `custom_notes_html`, `custom_project_spend`. Archive: write each project's
   deliverables and notes as Comments (the Comments App shows them) before the patch.
5. **Merges** (one PR per DocType, data move first, then the removal patch): Lead's eight standard
   duplicates (and unhide `company_name`); Opportunity `custom_description` into
   `custom_general_scope_description` and `custom_project` into `custom_created_project`; Project
   `custom_customer_name` into `customer`, `custom_customer__lead_address` and later
   `custom_project_address` into `primary_address`, `custom_customer_phone`/`email`/`contact_phone`
   into `primary_contact_*`; Customer `custom_prospect` into `custom_account_status`; Item pump
   electrical fields into `custom_elec_*`, cut sheets, pump-curve widgets, `custom_purchase_link`
   into Item Supplier `purchase_url`.
6. **Small doctypes**: Asset (6 migrated fields), Kanban Board / Kanban Board Column, CRM Note,
   Issue, Supplier Quotation Item, Purchase Order Item `custom_item_status`, ToDo
   `custom_start_date`, Customer `custom_parent_account`/`custom_shipping_address`/`custom_leads`,
   Contact's four legacy fields, User `twitter`/`work_environment`, Assignment Rule, CRM Deal, UTM
   Campaign.
7. **Hides and label fixes** can ride on any of the above as fixture-only changes (set `hidden`,
   change `label`): no patch needed, but the fixture JSON must carry the full record (sync erases
   omitted properties).
8. **Follow-ups outside this audit**: move the 198 code-created Custom Fields into fixtures (or
   document why each stays code-created); delete the 62 disabled Desk scripts and the stale Kanban
   boards and charts; backfill the 14 `Cancelled` Task rows; decide the three stale `project_type`
   values.

Each PR bumps PATCH (or MINOR where a feature is removed), adds its CHANGELOG entry, runs
`tests/test_layout_field_order_coverage.py` and the fixture tests, and opens as a draft.

## Appendix: how the numbers were produced

- Inventory: `fixtures/custom_field.json` (675 records), `fixtures/custom_field_hrms.json` (3),
  `fixtures/property_setter.json` (514); prod `tabCustom Field` (924) and `tabProperty Setter`
  (667), compared by `name`.
- Columns: `information_schema.COLUMNS` for every `tab<DocType>` with a Custom Field, so no query
  touched a missing column.
- Fill rates: one `SELECT COUNT(*), SUM(<non-empty>), MAX(IF(<non-empty>, modified, NULL)) FROM
  tab<DocType>` branch per field (601 branches, `UNION ALL` in chunks); child-table fields by
  `COUNT(DISTINCT parent)` on the child table filtered by `parenttype` and `parentfield`; Select
  fields by `GROUP BY` on the column.
- Versions: one scan of `tabVersion` per DocType restricted to the last 180 days, with
  `SUM(data LIKE '%"<fieldname>"%')` and the same split by `owner <> 'Administrator'`.
- Repo references: `git grep -w <fieldname>` over `erpnext_enhancements/`, excluding fixtures and
  the maintenance record's own doctype JSON; the Evidence column drops setup, patch, hook, test, README
  and help-text hits.
- Prod consumers: `tabList View Settings`, `tabKanban Board.fields`, `tabDashboard Chart`
  (`based_on`, `group_by_based_on`, `filters_json`), `tabNumber Card`, `tabNotification`
  (`value_changed`, `condition`), non-standard `tabReport` JSON/query, non-standard `tabPrint Format`
  HTML, `tabWeb Form Field`, and the text of every Server Script and Client Script.
- Standard field lists: `project.json`, `task.json`, `opportunity.json`, `lead.json`, `customer.json`,
  `supplier.json`, `item.json`, `purchase_order.json`, `material_request.json`, `stock_entry.json`,
  `asset.json`, `employee.json` at erpnext `v16.52.1`; `contact.json`, `address.json`, `todo.json` at
  frappe `v16.53.0` (prod's installed versions on the day).


## Appendix: Property Setters

### Fixture setters that change label, options, hidden, reqd, field order, list view, default, read-only, depends-on or permlevel

286 of the 514 fixture setters; the rest set description, collapsible, allow_in_quick_entry, in_standard_filter, print_hide, default_print_format and similar.

| DocType | Field | Property | Value |
|---|---|---|---|
| Activity Cost | `costing_rate` | permlevel | 1 |
| Address | `(doctype)` | field_order | ["address_details", "address_title", "custom_full_address", "address_type", "address_line1", "address_line2", "city", "c |
| Asset | `asset_type` | default | Existing Asset |
| Asset | `available_for_use_date` | mandatory_depends_on | eval:doc.docstatus==1 |
| Asset | `purchase_date` | mandatory_depends_on | eval:doc.docstatus==1 |
| Asset | `purchase_date` | reqd | 0 |
| Bank | `plaid_access_token` | hidden | 1 |
| Bank | `plaid_access_token` | read_only | 1 |
| Batch | `(doctype)` | field_order | ["sb_disabled", "disabled", "column_break_24", "use_batchwise_valuation", "sb_batch", "batch_id", "item", "item_name", " |
| CRM Note | `(doctype)` | field_order | ["note", "attachment", "added_by", "added_on"] |
| Call Log | `customer` | in_list_view | 1 |
| Company | `(doctype)` | field_order | ["details", "company_name", "abbr", "default_currency", "country", "is_group", "default_holiday_list", "cb0", "default_l |
| Company | `website` | options | URL |
| Contact | `(doctype)` | field_order | ["contact_section", "first_name", "last_name", "full_name", "custom_preferred_contact_method", "custom_email", "custom_s |
| Contact | `company_name` | hidden | 1 |
| Contact | `designation` | hidden | 1 |
| Contact | `email_id` | hidden | 1 |
| Contact | `email_id` | in_list_view | 0 |
| Contact | `email_id` | options |  |
| Contact | `email_ids` | hidden | 1 |
| Contact | `middle_name` | hidden | 1 |
| Contact | `mobile_no` | hidden | 1 |
| Contact | `mobile_no` | label | DONTUSE-Mobile No |
| Contact | `mobile_no` | options |  |
| Contact | `phone` | hidden | 1 |
| Contact | `phone` | in_list_view | 0 |
| Contact | `phone` | options |  |
| Contact | `phone_nos` | hidden | 1 |
| Customer | `(doctype)` | field_order | ["basic_info", "naming_series", "customer_name", "customer_type", "custom_account_status", "customer_group", "industry", |
| Customer | `account_manager` | hidden | 1 |
| Customer | `accounting_tab` | hidden | 1 |
| Customer | `address_contacts` | hidden | 1 |
| Customer | `address_html` | hidden | 1 |
| Customer | `column_break_wlbg` | hidden | 1 |
| Customer | `connections_tab` | hidden | 1 |
| Customer | `contact_and_address_tab` | label | Contacts & Addresses |
| Customer | `customer_group` | hidden | 1 |
| Customer | `customer_group` | in_list_view | 0 |
| Customer | `customer_group` | label | Account Group |
| Customer | `customer_name` | label | Account Name |
| Customer | `customer_type` | default |  |
| Customer | `customer_type` | label | Account Type |
| Customer | `customer_type` | options | Commercial Residential Partnership |
| Customer | `email_id` | hidden | 1 |
| Customer | `gender` | hidden | 1 |
| Customer | `lead_name` | hidden | 1 |
| Customer | `mobile_no` | hidden | 1 |
| Customer | `opportunity_name` | hidden | 1 |
| Customer | `portal_users_tab` | hidden | 1 |
| Customer | `primary_address` | hidden | 1 |
| Customer | `primary_address_and_contact_detail` | hidden | 1 |
| Customer | `primary_address_and_contact_detail` | label | Primary Contact |
| Customer | `primary_contact_section` | hidden | 1 |
| Customer | `prospect_name` | hidden | 1 |
| Customer | `sales_team_tab` | hidden | 1 |
| Customer | `section_break_map` | hidden | 1 |
| Customer | `tax_tab` | hidden | 1 |
| Customer | `website` | options | URL |
| Customer Group | `customer_group_name` | label | Account Group Name |
| Customer Group | `parent_customer_group` | label | Parent Account Group |
| Delivery Note | `(doctype)` | field_order | ["naming_series", "customer", "tax_id", "customer_name", "column_break1", "posting_date", "posting_time", "set_posting_t |
| Employee | `(doctype)` | field_order | ["basic_details_tab", "basic_information", "employee", "naming_series", "first_name", "middle_name", "last_name", "emplo |
| Employee | `bank_ac_no` | permlevel | 1 |
| Employee | `bank_name` | permlevel | 1 |
| Employee | `blood_group` | permlevel | 1 |
| Employee | `ctc` | permlevel | 1 |
| Employee | `custom_healthcare_stipend` | permlevel | 1 |
| Employee | `health_details` | permlevel | 1 |
| Employee | `iban` | permlevel | 1 |
| Employee | `passport_number` | permlevel | 1 |
| Employee | `salary_currency` | permlevel | 1 |
| Employee | `salary_mode` | permlevel | 1 |
| Event | `ends_on` | in_list_view | 1 |
| Event | `event_type` | default | Private |
| Event | `starts_on` | in_list_view | 1 |
| Issue | `(doctype)` | field_order | ["subject_section", "naming_series", "subject", "project", "customer", "raised_by", "cb00", "status", "priority", "issue |
| Issue | `project` | reqd | 1 |
| Item | `(doctype)` | field_order | ["details", "naming_series", "item_code", "custom_sku", "custom_item_identifier", "custom_guided_client_id", "item_name" |
| Item | `is_stock_item` | default | 0 |
| Item | `item_code` | in_list_view | 0 |
| Item | `item_name` | in_list_view | 1 |
| Item | `section_break_gjns` | label | Pricing |
| Item | `section_break_znra` | label | Tolerances |
| Job Card | `(doctype)` | field_order | ["naming_series", "work_order", "bom_no", "production_item", "employee", "column_break_4", "posting_date", "company", "f |
| Journal Entry | `(doctype)` | field_order | ["entry_type_and_date", "is_system_generated", "title", "voucher_type", "naming_series", "finance_book", "process_deferr |
| Lead | `(doctype)` | field_order | ["custom_lead_information", "first_name", "custom_contact_last_name", "title", "custom_column_break_jmqyy", "lead_owner" |
| Lead | `activities_tab` | hidden | 1 |
| Lead | `address_section` | hidden | 1 |
| Lead | `city` | depends_on | eval:doc.custom_service_interest=="Service" |
| Lead | `column_break_38` | hidden | 1 |
| Lead | `company_name` | hidden | 1 |
| Lead | `company_name` | mandatory_depends_on |  |
| Lead | `company_name` | reqd | 0 |
| Lead | `contact_html` | hidden | 1 |
| Lead | `contact_info_tab` | hidden | 1 |
| Lead | `contact_info_tab` | label | Contacts |
| Lead | `country` | hidden | 1 |
| Lead | `dashboard_tab` | hidden | 1 |
| Lead | `first_name` | in_list_view | 1 |
| Lead | `first_name` | mandatory_depends_on |  |
| Lead | `first_name` | reqd | 1 |
| Lead | `job_title` | in_list_view | 0 |
| Lead | `job_title` | label | Position Title |
| Lead | `notes_tab` | hidden | 1 |
| Lead | `organization_section` | hidden | 1 |
| Lead | `other_info_tab` | hidden | 1 |
| Lead | `primary_contact_section` | hidden | 1 |
| Lead | `qualification_tab` | hidden | 1 |
| Lead | `state` | depends_on | eval:doc.custom_service_interest=="Service" |
| Lead | `title` | label | Lead Title |
| Lead | `utm_analytics_section` | hidden | 1 |
| Lead | `website` | options | URL |
| Material Request | `(doctype)` | field_order | ["type_section", "naming_series", "title", "material_request_type", "customer", "company", "column_break_2", "transactio |
| Non Conformance | `procedure` | reqd | 0 |
| Non Conformance | `status` | options | Open Action Assigned PM Resolved Verified Closed |
| Opportunity | `(doctype)` | field_order | ["custom_opportunity_name", "naming_series", "opportunity_from", "party_name", "custom_drive_folder_id", "custom_drive_f |
| Opportunity | `address_contact_section` | hidden | 1 |
| Opportunity | `address_html` | hidden | 1 |
| Opportunity | `address_list_html` | hidden | 0 |
| Opportunity | `amended_from` | hidden | 1 |
| Opportunity | `annual_revenue` | hidden | 1 |
| Opportunity | `city` | hidden | 1 |
| Opportunity | `column_break_17` | hidden | 0 |
| Opportunity | `column_break_22` | hidden | 1 |
| Opportunity | `company` | default | Sapphire Fountains |
| Opportunity | `company` | hidden | 1 |
| Opportunity | `contact_email` | hidden | 1 |
| Opportunity | `contact_email` | label | Email Address |
| Opportunity | `contact_email` | read_only | 1 |
| Opportunity | `contact_info` | label | Contacts & Addresses |
| Opportunity | `contact_mobile` | hidden | 1 |
| Opportunity | `contact_mobile` | label | DONOTUSE-Contact Mobile |
| Opportunity | `contact_mobile` | read_only | 1 |
| Opportunity | `contact_person` | hidden | 1 |
| Opportunity | `contact_person` | in_list_view | 1 |
| Opportunity | `contact_person` | label | Contact Person |
| Opportunity | `country` | hidden | 1 |
| Opportunity | `currency` | hidden | 1 |
| Opportunity | `customer_address` | hidden | 0 |
| Opportunity | `customer_address` | label | Billing Address |
| Opportunity | `customer_name` | in_list_view | 1 |
| Opportunity | `dashboard_tab` | hidden | 1 |
| Opportunity | `items_section` | hidden | 1 |
| Opportunity | `job_title` | hidden | 1 |
| Opportunity | `job_title` | options |  |
| Opportunity | `job_title` | read_only | 1 |
| Opportunity | `lost_detail_section` | depends_on | eval:doc.status==="Lost" |
| Opportunity | `lost_reasons` | hidden | 0 |
| Opportunity | `market_segment` | hidden | 1 |
| Opportunity | `naming_series` | in_list_view | 0 |
| Opportunity | `no_of_employees` | hidden | 1 |
| Opportunity | `opportunity_from` | default | Customer |
| Opportunity | `opportunity_from` | in_list_view | 0 |
| Opportunity | `opportunity_owner` | reqd | 1 |
| Opportunity | `opportunity_type` | default |  |
| Opportunity | `opportunity_type` | hidden | 1 |
| Opportunity | `opportunity_type` | in_list_view | 0 |
| Opportunity | `opportunity_type` | label | Opportunity Type (Value Stream) |
| Opportunity | `order_lost_reason` | depends_on | eval:doc.status==="Lost" |
| Opportunity | `phone` | hidden | 1 |
| Opportunity | `phone` | in_list_view | 1 |
| Opportunity | `phone` | read_only | 1 |
| Opportunity | `phone_ext` | hidden | 1 |
| Opportunity | `phone_ext` | read_only | 1 |
| Opportunity | `primary_contact` | hidden | 0 |
| Opportunity | `primary_contact_email` | hidden | 0 |
| Opportunity | `primary_contact_job_title` | hidden | 0 |
| Opportunity | `primary_contact_phone` | hidden | 0 |
| Opportunity | `primary_contact_section` | hidden | 1 |
| Opportunity | `sales_stage` | hidden | 1 |
| Opportunity | `section_break_14` | label | Estimated Cost |
| Opportunity | `state` | hidden | 1 |
| Opportunity | `status` | default | Qualification |
| Opportunity | `status` | options | Qualification Needs Analysis Proposal/Price Quote Negotiation/Review Lost Closed Won On Hold |
| Opportunity | `utm_analytics_section` | hidden | 1 |
| Opportunity | `utm_campaign` | hidden | 1 |
| Opportunity | `utm_content` | hidden | 1 |
| Opportunity | `utm_medium` | hidden | 1 |
| Opportunity | `utm_source` | hidden | 1 |
| Opportunity | `website` | options | URL |
| Opportunity | `whatsapp` | hidden | 1 |
| Payment Entry | `(doctype)` | field_order | ["type_of_payment", "naming_series", "payment_type", "payment_order_status", "column_break_5", "posting_date", "company" |
| Production Plan | `(doctype)` | field_order | ["naming_series", "company", "get_items_from", "column_break1", "posting_date", "filters", "item_code", "customer", "war |
| Project | `(doctype)` | field_order | ["custom_section_break_s5jqn", "naming_series", "project_name", "customer", "custom_project_id", "status", "project_type |
| Project | `connections_tab` | hidden | 1 |
| Project | `costing_tab` | hidden | 1 |
| Project | `custom_project_notes` | hidden | 1 |
| Project | `customer_details` | hidden | 1 |
| Project | `customer_details` | label | Account Details |
| Project | `department` | hidden | 1 |
| Project | `expected_end_date` | label | Scheduled End Date |
| Project | `expected_end_date` | read_only | 1 |
| Project | `expected_start_date` | in_list_view | 1 |
| Project | `expected_start_date` | label | Scheduled Start Date |
| Project | `expected_start_date` | read_only | 1 |
| Project | `margin` | hidden | 1 |
| Project | `monitor_progress_tab` | hidden | 1 |
| Project | `more_info_tab` | hidden | 1 |
| Project | `naming_series` | default | PRJ-.##### |
| Project | `naming_series` | hidden | 1 |
| Project | `percent_complete` | read_only | 0 |
| Project | `percent_complete_method` | default | Manual |
| Project | `primary_contact_section` | hidden | 1 |
| Project | `priority` | hidden | 1 |
| Project | `project_details` | hidden | 1 |
| Project | `project_name` | in_list_view | 1 |
| Project | `project_template` | hidden | 1 |
| Project | `project_type` | in_list_view | 0 |
| Project | `project_type` | label | Project Stage |
| Project | `section_break0` | hidden | 1 |
| Project | `status` | default | Active |
| Project | `status` | in_list_view | 1 |
| Project | `status` | options | Active Client Hold Parked Completed Invoiced Paid Canceled |
| Project | `total_billable_amount` | hidden | 1 |
| Project | `total_billed_amount` | hidden | 1 |
| Project | `total_consumed_material_cost` | hidden | 1 |
| Project | `total_costing_amount` | hidden | 1 |
| Project | `total_expense_claim` | hidden | 1 |
| Project | `total_purchase_cost` | hidden | 1 |
| Project | `total_sales_amount` | hidden | 1 |
| Project | `users_section` | hidden | 1 |
| Prospect | `(doctype)` | field_order | ["overview_tab", "company_name", "customer_group", "no_of_employees", "annual_revenue", "column_break_4", "market_segmen |
| Prospect | `contacts_tab` | label | Contacts & Addresses |
| Prospect | `overview_tab` | label | Details |
| Purchase Invoice | `(doctype)` | field_order | ["naming_series", "supplier", "supplier_name", "tax_id", "column_break_6", "posting_date", "posting_time", "set_posting_ |
| Purchase Invoice | `address_and_contact_tab` | label | Contacts & Addresses |
| Purchase Invoice Item | `project` | in_list_view | 1 |
| Purchase Order | `(doctype)` | field_order | ["supplier_section", "naming_series", "supplier", "supplier_name", "custom_order_stage", "order_confirmation_no", "order |
| Purchase Order | `address_and_contact_tab` | label | Contacts & Addresses |
| Purchase Order | `project` | in_list_view | 1 |
| Purchase Order Item | `(doctype)` | field_order | ["fg_item", "fg_item_qty", "item_code", "supplier_part_no", "item_name", "brand", "product_bundle", "column_break_4", "s |
| Purchase Order Item | `expected_delivery_date` | in_list_view | 1 |
| Purchase Order Item | `item_name` | in_list_view | 1 |
| Purchase Order Item | `project` | in_list_view | 1 |
| Purchase Order Item | `project` | reqd | 1 |
| Purchase Order Item | `received_qty` | depends_on |  |
| Purchase Order Item | `received_qty` | in_list_view | 1 |
| Purchase Receipt | `(doctype)` | field_order | ["supplier_section", "naming_series", "supplier", "supplier_name", "supplier_delivery_note", "custom_store_run", "custom |
| Purchase Receipt | `address_and_contact_tab` | label | Contacts & Addresses |
| Quality Action | `corrective_preventive` | reqd | 0 |
| Quality Action | `status` | options | Open In Progress PM Resolved Verified at Next Inspection Closed |
| Quality Goal | `frequency` | options | None Daily Weekly Monthly Quarterly Annual |
| Quotation | `(doctype)` | field_order | ["customer_section", "naming_series", "quotation_to", "party_name", "crm_deal", "customer_name", "column_break_7", "tran |
| Request for Quotation | `(doctype)` | field_order | ["naming_series", "company", "custom_project", "vendor", "column_break1", "transaction_date", "schedule_date", "status", |
| Request for Quotation | `address_and_contact_tab` | label | Contacts & Addresses |
| Sales Invoice | `(doctype)` | field_order | ["customer_section", "title", "naming_series", "customer", "customer_name", "tax_id", "company", "company_tax_id", "colu |
| Sales Invoice | `project` | in_list_view | 1 |
| Sales Order | `(doctype)` | field_order | ["customer_section", "column_break0", "title", "naming_series", "customer", "customer_name", "tax_id", "order_type", "co |
| Serial No | `(doctype)` | field_order | ["details", "column_break0", "serial_no", "item_code", "batch_no", "warehouse", "purchase_rate", "customer", "column_bre |
| Stock Entry | `(doctype)` | field_order | ["stock_entry_details_tab", "naming_series", "stock_entry_type", "outgoing_stock_entry", "purpose", "add_to_transit", "w |
| Supplier | `(doctype)` | field_order | ["naming_series", "supplier_name", "gender", "country", "website", "custom_our_account_number", "column_break0", "custom |
| Supplier | `accounting_tab` | hidden | 1 |
| Supplier | `contact_and_address_tab` | hidden | 1 |
| Supplier | `contact_and_address_tab` | label | Contacts & Addresses |
| Supplier | `portal_users_tab` | hidden | 1 |
| Supplier | `primary_address` | hidden | 1 |
| Supplier | `primary_address_and_contact_detail_section` | hidden | 1 |
| Supplier | `settings_tab` | hidden | 1 |
| Supplier | `supplier_group` | label | Primary Supplier Group |
| Supplier | `tax_tab` | hidden | 1 |
| Supplier | `website` | options | URL |
| Supplier Quotation | `(doctype)` | field_order | ["supplier_section", "naming_series", "supplier", "supplier_name", "quotation_number", "company", "column_break1", "tran |
| Supplier Quotation | `address_and_contact_tab` | label | Contacts & Addresses |
| Supplier Quotation Item | `(doctype)` | field_order | ["item_code", "supplier_part_no", "item_name", "column_break_3", "lead_time_days", "expected_delivery_date", "custom_req |
| Task | `(doctype)` | field_order | ["custom_section_break_8wegw", "subject", "project", "department", "is_group", "custom_subtask_order", "column_break0",  |
| Task | `dependencies_tab` | hidden | 1 |
| Task | `google_calendar_events` | hidden | 1 |
| Task | `is_group` | label | Is Parent |
| Task | `old_parent` | options | Task |
| Task | `sb_more_info` | hidden | 1 |
| Task | `status` | options | Open Working Invoiced Completed Canceled Pending Review Overdue Template |
| Timesheet | `(doctype)` | field_order | ["title", "naming_series", "company", "customer", "currency", "exchange_rate", "sales_invoice", "column_break_3", "statu |
| Timesheet | `base_total_costing_amount` | permlevel | 1 |
| Timesheet | `total_costing_amount` | permlevel | 1 |
| Timesheet Detail | `base_costing_amount` | permlevel | 1 |
| Timesheet Detail | `base_costing_rate` | permlevel | 1 |
| ToDo | `(doctype)` | field_order | ["description_and_status", "status", "priority", "date", "custom_start_date", "column_break_2", "allocated_to", "assigne |
| ToDo | `allocated_to` | label | Assigned To |
| ToDo | `assigned_by_full_name` | hidden | 1 |
| ToDo | `assignment_rule` | depends_on | eval:doc.assignment_rule |
| ToDo | `color` | hidden | 1 |
| ToDo | `role` | depends_on | eval:doc.role |
| UOM | `uom_name` | default | Unit |
| Work Order | `(doctype)` | field_order | ["item", "naming_series", "status", "production_item", "item_name", "image", "bom_no", "sales_order", "column_break1", " |
| Workspace | `content` | hidden | 0 |
| Workspace | `public` | read_only | 0 |

### Setters on prod and not in the repo

| DocType | Field | Property | Value | Owner | Created |
|---|---|---|---|---|---|
| Address | `country` | reqd | 0 | Sapphire user | 2026-08-25 |
| Delivery Note | `base_rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Delivery Note | `base_rounded_total` | print_hide | 1 | Sapphire user | 2026-03-20 |
| Delivery Note | `disable_rounded_total` | default | 0 | Sapphire user | 2026-03-20 |
| Delivery Note | `in_words` | hidden | 0 | Sapphire user | 2026-03-20 |
| Delivery Note | `in_words` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Delivery Note | `rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Delivery Note | `rounded_total` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Delivery Note | `scan_barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Delivery Note Item | `barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Delivery Note Item | `target_warehouse` | hidden | 1 | Sapphire user | 2025-11-04 |
| Item | `custom_sku` | in_list_view | 1 | Sapphire user | 2026-05-26 |
| Item | `item_code` | hidden | 0 | Sapphire user | 2026-06-15 |
| Item | `item_code` | reqd | 1 | Sapphire user | 2026-06-15 |
| Item | `item_group` | in_list_view | 1 | Sapphire user | 2025-12-18 |
| Item | `naming_series` | hidden | 1 | Sapphire user | 2026-06-15 |
| Item | `naming_series` | reqd | 0 | Sapphire user | 2026-06-15 |
| Material Request | `scan_barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| POS Invoice | `scan_barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| POS Invoice Item | `barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Pick List | `scan_barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Purchase Invoice | `base_rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Purchase Invoice | `base_rounded_total` | print_hide | 1 | Sapphire user | 2026-03-20 |
| Purchase Invoice | `disable_rounded_total` | default | 0 | Sapphire user | 2026-03-20 |
| Purchase Invoice | `in_words` | hidden | 0 | Sapphire user | 2026-03-20 |
| Purchase Invoice | `in_words` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Purchase Invoice | `rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Purchase Invoice | `rounded_total` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Purchase Invoice | `scan_barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Purchase Invoice Item | `from_warehouse` | hidden | 1 | Sapphire user | 2025-11-04 |
| Purchase Order | `base_rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Purchase Order | `base_rounded_total` | print_hide | 1 | Sapphire user | 2026-03-20 |
| Purchase Order | `disable_rounded_total` | default | 0 | Sapphire user | 2026-03-20 |
| Purchase Order | `in_words` | hidden | 0 | Sapphire user | 2026-03-20 |
| Purchase Order | `in_words` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Purchase Order | `rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Purchase Order | `rounded_total` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Purchase Order | `scan_barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Purchase Receipt | `base_rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Purchase Receipt | `base_rounded_total` | print_hide | 1 | Sapphire user | 2026-03-20 |
| Purchase Receipt | `disable_rounded_total` | default | 0 | Sapphire user | 2026-03-20 |
| Purchase Receipt | `in_words` | hidden | 0 | Sapphire user | 2026-03-20 |
| Purchase Receipt | `in_words` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Purchase Receipt | `provisional_expense_account` | hidden | 1 | Sapphire user | 2026-09-22 |
| Purchase Receipt | `rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Purchase Receipt | `rounded_total` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Purchase Receipt | `scan_barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Purchase Receipt Item | `barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Purchase Receipt Item | `from_warehouse` | hidden | 1 | Sapphire user | 2025-11-04 |
| Quotation | `base_rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Quotation | `base_rounded_total` | print_hide | 1 | Sapphire user | 2026-03-20 |
| Quotation | `disable_rounded_total` | default | 0 | Sapphire user | 2026-03-20 |
| Quotation | `in_words` | hidden | 0 | Sapphire user | 2026-03-20 |
| Quotation | `in_words` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Quotation | `quotation_to` | link_filters | [["DocType","name","in", ["Customer", "Lead", "Prospect", "C | Sapphire user | 2025-07-23 |
| Quotation | `rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Quotation | `rounded_total` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Quotation | `scan_barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Sales Invoice | `base_rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Sales Invoice | `base_rounded_total` | print_hide | 1 | Sapphire user | 2026-03-20 |
| Sales Invoice | `disable_rounded_total` | default | 0 | Sapphire user | 2026-03-20 |
| Sales Invoice | `in_words` | hidden | 0 | Sapphire user | 2026-03-20 |
| Sales Invoice | `in_words` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Sales Invoice | `rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Sales Invoice | `rounded_total` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Sales Invoice | `scan_barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Sales Invoice Item | `barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Sales Invoice Item | `target_warehouse` | hidden | 1 | Sapphire user | 2025-11-04 |
| Sales Order | `base_rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Sales Order | `base_rounded_total` | print_hide | 1 | Sapphire user | 2026-03-20 |
| Sales Order | `disable_rounded_total` | default | 0 | Sapphire user | 2026-03-20 |
| Sales Order | `in_words` | hidden | 0 | Sapphire user | 2026-03-20 |
| Sales Order | `in_words` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Sales Order | `rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Sales Order | `rounded_total` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Sales Order | `scan_barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Stock Entry | `scan_barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Stock Entry Detail | `barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Stock Reconciliation | `scan_barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Stock Reconciliation Item | `barcode` | hidden | 0 | Sapphire user | 2026-06-15 |
| Supplier Quotation | `base_rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Supplier Quotation | `base_rounded_total` | print_hide | 1 | Sapphire user | 2026-03-20 |
| Supplier Quotation | `disable_rounded_total` | default | 0 | Sapphire user | 2026-03-20 |
| Supplier Quotation | `in_words` | hidden | 0 | Sapphire user | 2026-03-20 |
| Supplier Quotation | `in_words` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Supplier Quotation | `rounded_total` | hidden | 0 | Sapphire user | 2026-03-20 |
| Supplier Quotation | `rounded_total` | print_hide | 0 | Sapphire user | 2026-03-20 |
| Warehouse | `(doctype)` | allow_rename | 0 | Sapphire user | 2026-06-12 |
| Asset | `naming_series` | options | ACC-ASS-.YYYY.- | Administrator | 2026-09-23 |
| Assignment Rule | `assign_condition` | depends_on | eval: !doc.assign_condition_json | Administrator | 2025-09-22 |
| Assignment Rule | `unassign_condition` | depends_on | eval: !doc.unassign_condition_json | Administrator | 2025-09-22 |
| Assistant Audit Log | `naming_series` | default | ASST-AUDIT-.YYYY.-.MM.-.DD.- | Administrator | 2026-04-19 |
| Assistant Audit Log | `naming_series` | options | ASST-AUDIT-.YYYY.-.MM.-.DD.- | Administrator | 2026-04-19 |
| Customer | `naming_series` | options | CUST-.YYYY.- | Administrator | 2025-11-20 |
| Customer | `naming_series` | reqd | 0 | Administrator | 2025-07-08 |
| Delivery Note | `tax_id` | hidden | 0 | Administrator | 2025-07-08 |
| Delivery Note | `tax_id` | print_hide | 0 | Administrator | 2025-07-08 |
| Employee | `employee_number` | hidden | 1 | Administrator | 2026-02-07 |
| Employee | `employee_number` | reqd | 0 | Administrator | 2026-02-07 |
| Employee | `naming_series` | hidden | 0 | Administrator | 2026-02-07 |
| Employee | `naming_series` | options | HR-EMP- | Administrator | 2025-07-15 |
| Employee | `naming_series` | reqd | 1 | Administrator | 2026-02-07 |
| Inventory Count Session | `naming_series` | options | INV-CNT-.YYYY.-.##### | Administrator | 2026-10-23 |
| Issue | `naming_series` | options | ISS-.YYYY.- | Administrator | 2025-10-09 |
| Item | `naming_series` | options | STO-ITEM-.YYYY.- | Administrator | 2025-12-09 |
| Job Interval | `naming_series` | options | JOB-INT-.##### | Administrator | 2026-02-07 |
| Journal Entry | `naming_series` | options | ACC-JV-.YYYY.- | Administrator | 2026-10-06 |
| Knowledge Article | `(doctype)` | default_print_format | Article Document | Administrator | 2026-10-01 |
| Knowledge Article Version | `(doctype)` | default_print_format | Article Version Preview | Administrator | 2026-10-01 |
| LMS Certificate | `(doctype)` | default_print_format | Certificate | Administrator | 2025-07-08 |
| Lead | `naming_series` | options | CRM-LEAD-.YYYY.- | Administrator | 2026-02-07 |
| Material Request | `naming_series` | options | MAT-MR-.YYYY.- | Administrator | 2026-02-02 |
| Opportunity | `naming_series` | options | CRM-OPP-.YYYY.- | Administrator | 2025-08-14 |
| Packed Item | `rate` | read_only | 1 | Administrator | 2025-07-08 |
| Payment Entry | `naming_series` | options | ACC-PAY-.YYYY.- | Administrator | 2026-10-06 |
| Project | `naming_series` | options | PROJ-.#### | Administrator | 2025-08-26 |
| Project Contract | `naming_series` | options | SF-MSA-.YYYY.-.#### SF-SOW-.YYYY.-.#### SF-OC-.YYYY.-.#### S | Administrator | 2026-09-10 |
| Purchase Invoice | `naming_series` | options | ACC-PINV-.YYYY.- ACC-PINV-RET-.YYYY.- | Administrator | 2026-02-07 |
| Purchase Order | `naming_series` | options | PUR-ORD-.YYYY.- | Administrator | 2026-02-07 |
| Purchase Receipt | `naming_series` | options | MAT-PRE-.YYYY.- MAT-PR-RET-.YYYY.- | Administrator | 2025-11-25 |
| Quotation | `naming_series` | options | SAL-QTN-.YYYY.- | Administrator | 2026-08-31 |
| Request for Quotation | `naming_series` | options | PUR-RFQ-.YYYY.- | Administrator | 2026-01-24 |
| Salary Slip | `rounded_total` | hidden | 0 | Administrator | 2026-02-07 |
| Salary Slip | `rounded_total` | print_hide | 0 | Administrator | 2026-02-07 |
| Sales Invoice | `additional_discount_account` | hidden | 1 | Administrator | 2025-07-08 |
| Sales Invoice | `additional_discount_account` | mandatory_depends_on |  | Administrator | 2025-07-08 |
| Sales Invoice | `naming_series` | options | ACC-SINV-.YYYY.- ACC-SINV-RET-.YYYY.- | Administrator | 2026-08-31 |
| Sales Invoice | `tax_id` | hidden | 0 | Administrator | 2025-07-08 |
| Sales Invoice | `tax_id` | print_hide | 0 | Administrator | 2025-07-08 |
| Sales Invoice Item | `discount_account` | hidden | 1 | Administrator | 2025-07-08 |
| Sales Invoice Item | `discount_account` | mandatory_depends_on |  | Administrator | 2025-07-08 |
| Sales Order | `tax_id` | hidden | 0 | Administrator | 2025-07-08 |
| Sales Order | `tax_id` | print_hide | 0 | Administrator | 2025-07-08 |
| Sapphire Maintenance Contract | `naming_series` | default | MNT-CON-.YYYY.- | Administrator | 2026-07-27 |
| Sapphire Maintenance Contract | `naming_series` | options | MNT-CON-.YYYY.- | Administrator | 2026-07-27 |
| Stock Entry | `naming_series` | options | MAT-STE-.YYYY.- | Administrator | 2026-10-06 |
| Stock Reconciliation | `naming_series` | options | MAT-RECO-.YYYY.- | Administrator | 2026-02-07 |
| Supplier | `naming_series` | hidden | 1 | Administrator | 2025-07-08 |
| Supplier | `naming_series` | options | SUP-.YYYY.- | Administrator | 2026-02-07 |
| Supplier | `naming_series` | reqd | 0 | Administrator | 2025-07-08 |
| Supplier Quotation | `naming_series` | options | PUR-SQTN-.YYYY.- | Administrator | 2026-02-07 |
| Timesheet | `naming_series` | options | TS-.YYYY.- | Administrator | 2026-02-07 |
| Training Assignment | `naming_series` | options | TRN-ASG-.###### | Administrator | 2026-09-11 |
| Training Attempt | `naming_series` | options | TRN-ATT-.######## | Administrator | 2026-09-11 |
| Training Certificate | `naming_series` | options | TRN-CERT-.YYYY.-.##### | Administrator | 2026-09-11 |
| Training Completion | `naming_series` | options | TRN-CMP-.###### | Administrator | 2026-09-11 |
| Training Course | `naming_series` | options | TRN-CRS-.##### | Administrator | 2026-09-11 |
| Training Lesson | `naming_series` | options | TRN-LSN-.###### | Administrator | 2026-09-08 |
| Training Signoff | `naming_series` | options | TRN-SGN-.###### | Administrator | 2026-09-11 |
| Training Video Asset | `naming_series` | options | TRN-VID-.##### | Administrator | 2026-09-11 |
| Travel Trip | `(doctype)` | default_print_format | Trip Sheet | Administrator | 2026-09-27 |
| Travel Trip | `naming_series` | options | TRIP-.YYYY.-.##### | Administrator | 2025-12-23 |
| Water Feature Design | `(doctype)` | default_print_format | Water Feature Design - Design Report | Administrator | 2026-11-01 |

