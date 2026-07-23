# Project Enhancements

Customizes ERPNext's **Project** doctype and the workflow around it. The headline feature is a custom, realtime **Project Dashboard** desk page; the module also adds a **Master Project** doctype (groups projects into a program/portfolio), procurement-status rollups, a project-merge tool, Opportunity→Project conversion, and doctype-dashboard overrides.

Most server entry points are `@frappe.whitelist()` methods called from the page/form scripts; a few are wired through [`../hooks.py`](../hooks.py).

## File map

| File | Purpose | Key functions / classes | Wiring |
|---|---|---|---|
| `__init__.py` | Module helpers: procurement status, attachment sync, start reminders, dashboard override, project comments | `get_procurement_status`, `get_procurement_documents`, `sync_attachments_from_opportunity`, `send_project_start_reminders`, `get_dashboard_data`, `get_project_comments`/`add`/`delete`/`update` | Project `after_save` → `sync_attachments_from_opportunity`; `scheduler.daily` → `send_project_start_reminders`; `override_doctype_dashboards["Project"]` → `get_dashboard_data` |
| `doctype/master_project/master_project.py` | Master Project controller; rollup of member Projects + Tasks | `MasterProject.get_projects_and_tasks` | Doctype controller |
| `doctype/master_project/master_project.js` | Read-only Projects/Tasks rollup tables on the form | `render_projects_table`, `render_tasks_table` | Doctype form script |
| `doctype/project_notes`, `project_stakeholder`, `{build,design,rent,service}_customer_requests`, `{build,design,rent,service}_deliverables` | Project child tables ported from DB-only custom DocTypes (v0.7.0) so fresh installs can import the Custom Field fixtures that reference them | stub controllers | synced on migrate |
| `doctype/project/project.py` | List-view grouping + printable Project Brief data | `get_project_grouping_option`, `get_project_brief_data` | Whitelisted (client scripts) |
| `doctype/project/project.js` | Health banner + reminder button (the Schedule-tab Gantt it used to render in `custom_gantt_chart_html` is now the embeddable widget — `public/js/project_enhancements/project_gantt_widget.js`) | two `frappe.ui.form.on("Project", {refresh})` handlers | `doctype_js["Project"]` |
| `doctype/project/project_list.js` | Project list-view tweaks | — | list view |
| `doctype/address/address.js` | Live full-address build + Google Maps embed | Address form handlers | `doctype_js["Address"]` |
| `doctype/project_dashboard_settings/*.py` | Single doctype: legacy permitted-roles list for the dashboard | `ProjectDashboardSettings` | controller |
| `doctype/project_dashboard_permitted_role/*.py` | Child table: one `role` per row | `ProjectDashboardPermittedRole` | child-table controller |
| `page/project_dashboard/project_dashboard.py` | Shared backend for the dashboard (data / permission / inline-edit endpoints) | `check_permission`, `get_project_data`, `get_gantt_tasks_for_project`, `get_master_project_projects`, `update_task_*`, `add_task_dependency`, `publish_realtime_update`, … | Whitelisted (called by the Custom HTML Block); `publish_realtime_update` via `doc_events`. NB the folder no longer defines a desk Page — only this module + `test_project_dashboard.py` remain. |

Related code outside this folder:
- `project_merge.py` (repo root) — merge one Project into another by re-pointing all linked docs. Whitelisted; called from `public/js/project_merge.js`.
- `opportunity_enhancements.py` (repo root) — `make_project` override (stamps the source Opportunity). Wired via `override_whitelisted_methods`.
- `dashboard_overrides.py` (repo root) — adds a "Travel" connections group to the **Employee** dashboard. Wired via `override_doctype_dashboards["Employee"]`.
- The dashboard UI is the **"Projects Dashboard" Custom HTML Block** (`custom_html_blocks/projects_dashboard.{js,html,css}`); the only front-end helpers left under `public/js/project_enhancements/dashboard_components/` are the shared `column_selector.js` / `column_resizer.js` — see the [public README](../public/README.md#project-dashboard-components).

## Projects Dashboard

- **One surface (consolidated in v1.159.8):** the dashboard is the **"Projects Dashboard" Custom HTML Block**, embedded on the **Home** and **Projects** workspaces (placed by `setup.custom_html_blocks.sync_custom_html_blocks`, which also *deploys* it — the repo `.js`/`.html`/`.css` become the block's `script`/`html`/`style` on migrate, no asset build). It renders a tabbed shell — Priority Overview (default), Active Internal Projects, Completed Projects, Portfolio Gantt, Dashboard — plus **New Project** / **New Master Project** buttons, all in one IIFE (`custom_html_blocks/projects_dashboard.js`). A *second*, parallel desk-page implementation (`/app/project-dashboard`) was **removed** here; the desk shortcut + Project Enhancements workspace link now point at the Projects workspace (`retire_project_dashboard_desk_page` patch).
- **Data source:** the whitelisted methods in `project_dashboard.py`. `get_project_data` uses bulk SQL/`get_all` for task counts and derives assignees from **ToDo** rows (Project has no `project_user` column — selecting one would raise "Unknown column"). The **Dashboard** tab computes its headline cards + status/type/completion breakdowns client-side from that same `get_project_data` payload (no separate endpoint). The Active Internal Projects tab shows only active projects whose `project_type` is internal (`INTERNAL_PROJECT_TYPES`, defined in the block JS).
- **Realtime:** `publish_realtime_update(doc, method)` fires `frappe.publish_realtime("project_dashboard_updated", …)` and is registered on both **Task** `on_update` and **Project** `on_update`.
- **Permission gating:** the block is visible to anyone who can see its workspace. `check_permission()` still gates the whitelisted reads (Custom Role + Has Role for the "Project Dashboard" page, falling back to the legacy `Project Dashboard Settings.permitted_roles`); list reads fetch with ignore-permissions (a portfolio view), while inline-edit/write endpoints enforce per-document `frappe.has_permission("Project", "write", …)`, and `update_project_details` restricts edits to a whitelisted `EDITABLE_PROJECT_FIELDS` set.

## Hand-Off Process engine (PRO-0204, v1.3.0)

The 7-step "Won Opportunity Hand-Off" tracker. Definition lives in **Process Step
Template** records (`doctype/process_step_template/`, seeded insert-only by the
`seed_process_step_templates` patch — site edits survive); per-project state lives in
the **Project Process Step** child table (`Project.custom_process_steps`, fixtures, on
the "Hand-Off Process" tab with a progress bar rendered by
`public/js/project_enhancements/process_steps.js`). The engine itself is the top-level
module [`process_steps.py`](../process_steps.py):

- **Seeding** — `before_insert` on Project copies enabled templates when the project
  has a `custom_opportunity`; steps anchored *Opportunity Won* / *Project Created*
  retro-complete. In-flight projects are never back-filled (Jun 9 meeting decision);
  they opt in via the form button → whitelisted `start_process`.
- **Anchors** — a *Payment Received* anchor completes its step when
  `custom_payment_received` is ticked (runs after `status_alerts.stamp_payment_received_date`
  in the `before_save` chain — order matters).
- **Notifications** — completing a step notifies the *new* current step's responsible
  person (SMS + Notification Log via `status_alerts._deliver`); the last completion
  posts a "process complete" comment instead. Roles resolve per project at send time:
  PM → `custom_project_owner`, AE → source Opportunity's `opportunity_owner`,
  AR → `handoff_ar_rep` in ERPNext Enhancements Settings.
- **Escalation** — daily scheduler nags the current step's owner once it's past
  `due_by` (now + SLA hours when the step became current), max once/day per step.
- **Visibility** — the Sales Pipeline board (`crm_enhancements/page/sales_pipeline/`)
  shows a "Hand-off in progress" rail of active projects with their current step,
  overdue ones glowing first.

## Contract generation (Phase 4, v1.5.0)

Eight agreements generate inside ERPNext. The revised suite (Apr 2026): **MSA**
(Master Subcontractor Agreement, per Supplier, Tier 1/Tier 2), **SOW** (Statement of
Work — only creatable under a *Signed* MSA for the same Supplier; the gate lives in
`ProjectContract.validate_msa_gate`), **Owner Contract** (phase-selectable
Design/Construction/Maintenance), **Rental Agreement**, and **Maintenance Services
Agreement** (payment authorization prints as a secure-link instruction and/or a blank
card form — card data never enters ERPNext). Plus the three retained originals (per
the Contract Comparison Report, no replacement in the revised suite): **Mutual NDA**
(DOC-0033, party = Customer/Supplier/Employee picked per contract), **Architect
Agreement** (DOC-0101, the architect engages Sapphire — party Customer; includes its
own embedded SOW page), and **Employee-Contractor Agreement** (DOC-0137). The
superseded originals (DOC-0032/0034/0099/0100/0102) are deliberately NOT templated.

- **`Contract Template`** (`doctype/contract_template/`) — the Jinja HTML bodies,
  seeded insert-only from `templates/contracts/` (regeneration pipeline:
  `scripts/contract_templates/`); legal-text edits happen on the site record.
- **`Project Contract`** (`doctype/project_contract/`) — submittable instance with
  per-type structured data (phase/milestone/equipment/service-option child tables,
  computed totals) and native revision lineage: submit = issued, cancel + amend =
  Revision N (`revision` + `amended_from`), `track_changes` for draft history. Naming
  series per type with the generation year, counters restarting yearly:
  `SF-{MSA,SOW,OC,RA,MAINT,NDA,ARCH,EC}-YYYY-####` (e.g. `SF-OC-2026-0001`).
- **Generation** — "Create > Generate Contract" on Opportunity/Project (customer
  types + SOW with a supplier picker) and Supplier (MSA/SOW), via `create_contract`
  (whitelisted): prefils party, contacts, addresses, description, value-stream phase
  preselection, rental dates and rent-deliverable equipment lines from the source.
  Every SOW path checks `get_signed_msa` up front and offers to create the MSA instead.
- **SOW scope of work** composes from the source's scope tables
  (`custom_{design,build,service,rent}_customer_requests` / `_deliverables` —
  requests are the customer's words, deliverables the PM/Design breakdown):
  prefilled at generation, auto-pulled when a Project/Opportunity is linked to an
  empty-scope draft (Project wins once it exists — "depending on which stage"), and
  re-pullable via the form's "Pull Scope from Source" button (`compose_scope_of_work`).
- **Printing** — the "Project Contract Print" Jinja print format (fixtures) calls
  `doc.render_body()`; blanks print as fillable lines so the paper flow still works.
  E-signature is a planned follow-up.

## Master Project

A lightweight container doctype grouping ordinary Projects into a program/portfolio. Projects join via the **`Project.custom_master_project`** Link field (no child table on the Master side); **`Project.custom_subproject_order`** controls ordering under the master. `get_projects_and_tasks` returns member Projects and their Tasks for the form's read-only HTML tables. The dashboard's `get_master_project_projects` / `update_master_project_structure` reuse the same grouping (the latter persists drag-reordering).

## `hooks.py` touchpoints

- `doc_events`: Project `after_save` → `sync_attachments_from_opportunity`; Project/Task `on_update` → `…project_dashboard.publish_realtime_update`.
- `scheduler_events.daily` → `send_project_start_reminders`.
- `override_doctype_dashboards`: `Project` → `get_dashboard_data`; `Employee` → `dashboard_overrides.get_data`.
- `override_whitelisted_methods`: `erpnext…opportunity.make_project` → `opportunity_enhancements.make_project`.
- `doctype_js["Project"]` includes `public/js/project_enhancements/project_gantt_widget.js` — the embeddable Gantt widget's first embed, mounted into `custom_gantt_chart_html` on the Schedule tab (read-only, status filter + Today; replaced the legacy interactive frappe-gantt renderer that lived in `doctype/project/project.js` — see the [public README](../public/README.md)).

## Gotchas

- **Mixed indentation:** most files use tabs; `dashboard_overrides.py` uses 4 spaces.
- `get_all_projects_for_gantt` deliberately drops the `check_permission()` gate (uses native Page roles) and filters the portfolio Gantt to client-facing `project_type in (Build, Design, Events, Service)`.
- Several Task fields are queried conditionally via `frappe.get_meta(...).has_field(...)` (`custom_is_recurring`, `baseline_start_date/baseline_end_date`) because they are optional site-level custom fields.
- `merge_projects` uses `frappe.db.set_value` for child tables/Singles (speed) but `doc.save()` for parents (to fire controller logic), and `log_error`s per-doc failures rather than aborting the whole merge. `get_linked_doctypes` discovers Project links dynamically from metadata, so any new Link-to-Project field automatically expands merge scope.
- `project.js` no longer renders a Gantt: the drag-editable frappe-gantt (with heatmap, dependency linking and PNG export) was replaced by the read-only embeddable widget in `project_gantt_widget.js`; editing returns with the widget's per-embed edit opt-in milestone. `project.js` keeps the health banner (bound off the `custom_gantt_chart_html` field object, guarded by `__health_bound`) and the reminder button.
