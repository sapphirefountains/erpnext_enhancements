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
| `doctype/address/address.js` | Live full-address build + Google Maps embed; attaches the global Places autocomplete to `address_line1` (widget: `public/js/global_enhancements/address_autocomplete.js`) and records the picked place in `custom_google_place_id` / `custom_latitude` / `custom_longitude`. The coordinates are **user-editable** (v1.207.0) for sites the address cannot locate; `custom_location_source` records whether the point came from Google (discarded when the address text is edited) or was typed (kept) | Address form handlers | `doctype_js["Address"]` |
| `doctype/project_dashboard_settings/*.py` | Single doctype: legacy permitted-roles list for the dashboard | `ProjectDashboardSettings` | controller |
| `doctype/project_dashboard_permitted_role/*.py` | Child table: one `role` per row | `ProjectDashboardPermittedRole` | child-table controller |
| `page/project_dashboard/project_dashboard.py` | Shared backend for the dashboard (data / permission / inline-edit endpoints) **plus the Scope-tab task-tree export**: `_flatten_task_tree` reads the whole project in one `get_list` and links it in memory, because the on-screen grid loads children one level at a time and a file built from that would omit every branch the user did not expand | `check_permission`, `get_project_data`, `get_gantt_tasks_for_project`, `get_master_project_projects`, `update_task_*`, `add_task_dependency`, `publish_realtime_update`, `get_project_task_tree`, `export_project_tasks`, … | Whitelisted (called by the Custom HTML Block); `publish_realtime_update` via `doc_events`. NB the folder no longer defines a desk Page — only this module + `test_project_dashboard.py` remain. |
| `print_data.py` | Pre-computed rows for the two Project Print Formats, including each Gantt bar's `left_pct`/`width_pct`. Computed in Python because the print sandbox has no date arithmetic to derive them per row, and a Print Format renders **server-side with no JavaScript**, so the browser SVG renderer cannot help | `project_schedule_rows`, `project_task_rows` | `jinja.methods` in `hooks.py` (callable from any Print Format / web template) |
| `setup_print_formats.py` | Ships the **Project Schedule** (task tree + HTML/CSS Gantt bars) and **Project Task List** formats, idempotently upserted so template edits deploy on the next migrate | `ensure_project_print_formats` | `after_migrate` (above `ensure_chrome_pdf_generator`, which must see them) |
| `report/supplier_pickup_list/` | **Supplier Pickup List** Script Report — unreceived Purchase Order lines by vendor, plus `supplier_pickup_list.html`, the driver-facing checklist print template | `execute`, `get_data` | Standard report (synced on migrate) |
| `report/pending_items_by_project/` | **Pending Items by Project** Query Report — unreceived Purchase Order lines for one job. The whole report is the SQL in its `.json`; the `.js` holds the filter, the colouring and the reasoning | — | Standard report (synced on migrate) |

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

- **The gate (2026-08-06)** — step 2, *Hold Hand-Off Meeting*, is recorded on the
  **Opportunity**, and a Project cannot be created from a Closed-Won Opportunity until
  it is. This reverts the June decision that allowed project-first creation: the tracker
  lived on the Project, so the step meant to gate project creation only existed after it,
  and the August audit found manual steps completing 5% of the time against 100% for the
  automated ones. Enforcement is `process_steps.enforce_handoff_gate` on Project
  `before_insert` — **not `validate`**, because `create_project_from_opportunity_background`
  sets `flags.ignore_validate`, so a `validate` hook never fires on the app's own creation
  path. `make_project` and the background creator refuse too, for a readable message.
  See [`crm_enhancements/handoff.py`](../crm_enhancements/handoff.py).
  Only deals that *transition* into Closed Won after this shipped are gated
  (`custom_handoff_gate_applies`); the pre-existing backlog is exempt (WI-024 owns it).
  A System Manager can skip with a mandatory reason, stored on the record and carried
  onto the step row prefixed `[SKIPPED]` — skipping is allowed, silence is not.
- **Seeding** — `before_insert` on Project copies enabled templates when the project
  has a `custom_opportunity`; steps anchored *Opportunity Won* / *Project Created*
  retro-complete, and step 2 carries its real who/when across from the Opportunity so
  project-side reporting measures the hand-off that happened rather than stamping it
  complete at creation time. In-flight projects are never back-filled (Jun 9 meeting
  decision); they opt in via the form button → whitelisted `start_process`.
- **Step 2 in the other order** — since v1.263.0 the gate opens when the hand-off meeting
  is *booked*, so the project is usually created while step 2 is still Pending. Recording
  the meeting on the Opportunity afterwards reaches across and closes that row
  (`process_steps.record_handoff_on_project`, called from `handoff._stamp_handoff`) — with
  the Opportunity's own timestamps, and saved `ignore_permissions` because owning the deal
  is not the same as holding write on the project. Without it the tracker and the daily
  sweep would nag forever about a meeting that already happened.
- **Anchors** — a *Payment Received* anchor completes its step when
  `custom_payment_received` is ticked (runs after `status_alerts.stamp_payment_received_date`
  in the `before_save` chain — order matters).
- **Completion** — manual steps complete through whitelisted `process_steps.complete_step`,
  which stamps `completed_on`/`completed_by` from the **server** clock and session and
  checks the step's responsible role. The old client path let the browser propose
  `completed_on`; the audit found retroactive box-checking.
- **Actions** — the current step carries a button that starts the work, not just one that
  records it: step 4 opens a billing email (or a draft Sales Invoice, per the
  `handoff_invoice_flow` setting — ERPNext is not the accounting system yet), step 6 the
  task list, step 7 the meeting scheduler shared with step 2
  (`public/js/crm_enhancements/handoff_meeting_dialog.js`). Both buttons render **inside
  that step's box in the bar** (v1.286.3), under its due date, rather than in a shared row
  beneath it — several steps can be actionable at once (5 and 6, then 7), and the shared
  row had to reprint each step's title on its buttons to say which box they belonged to.
  Step 4's email is prefilled by `crm_enhancements.handoff.billing_notice_context`
  (v1.327.0): it goes to the **Billing Email** on the Project, else the one on its
  Opportunity, else the configured Billing route (`billing@sapphirefountains.com`), and it
  names the **First Invoice Percentage** and the money that works out to against the
  project amount. Both fields are Custom Fields on Opportunity *and* Project — Sales
  agrees them on the deal, the PM can revise them on the project. Blank percentage prints
  "confirm the amount with Sales" rather than a figure nobody set.
- **Notifications** — completing a step notifies the *new* current step's responsible
  person (SMS + Notification Log via `status_alerts._deliver`); the last completion
  posts a "process complete" comment instead. Roles resolve per project at send time:
  PM → `custom_project_owner`, AE → source Opportunity's `opportunity_owner`,
  Finance & Accounting Manager → `handoff_ar_rep` in ERPNext Enhancements Settings
  (renamed from "Accounts Receivable" in v1.251.0; the *fieldname* deliberately keeps
  its old spelling, so the configured Employee survives the rename — only the label and
  the stored `responsible_role` values moved, via `patches.rename_handoff_ar_role`).
- **Escalation** — daily scheduler nags the current step's owner **and their manager**
  (`Employee.reports_to`, falling back to `handoff_escalation_fallback`) once it's past
  `due_by`, by email as well as in-app, repeating daily while late (max once/day per step).
  Step 5 is excluded — when a customer pays is not ours. `escalate_overdue_handoffs` does
  the same for step 2 while it still lives on the Opportunity with no project row to find.
- **SLAs** — step 2 is due 2 business days from Closed Won, stamped on the transition
  (`custom_handoff_due_by`); `custom_launch_deadline` carries the meeting's headline goal
  of launching within 7 business days of Closed Won, shown alongside step 7's own SLA
  because a step can be on time and still miss the launch goal. Steps 4 and 6 keep the
  chain rule (the clock starts when the prior step completes).
- **Visibility** — the Sales Pipeline board (`crm_enhancements/page/sales_pipeline/`)
  shows a "Hand-off in progress" rail of active projects with their current step,
  overdue ones glowing first. The **Hand-Off SLA Compliance** Script Report
  (`report/hand_off_sla_compliance/` — the directory is `scrub()` of the report name,
  hyphen included) reports on-time % per role and per step, the overdue
  list, steps blocked upstream, and the 7-business-day launch metric; it is emailed
  Friday mornings to `handoff_report_recipients`.

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
- **Branding** (`contract_style.py`, v1.194.0) — the letterhead (inline SVG wordmark over a
  navy rule) and the running footer (contract number + page numbers) that wrap every
  agreement. Deliberately emitted by the *wrapper*, not by the templates: a signed contract
  prints its frozen `agreement_html` snapshot, so chrome inside the body could never reach
  one, and the templates themselves live in the site-editable `Contract Template` record
  rather than in this repo. The footer's `#footer-html` / `.page` / `.topage` names are
  frappe's PDF contract, not ours — `frappe.utils.pdf` extracts the div into wkhtmltopdf's
  `--footer-html` and its wrapper's `subst()` fills the spans.
- **One stylesheet, four surfaces** — the `Project Contract Print` record's CSS is the only
  definition of how a contract looks. `_contract_css()` serves it to the desk print, the
  on-screen viewer (`contract_viewer.js`), the public signing page (`www/contract_sign.py`,
  sanitised) and the executed PDF emailed after signing (`esign/lifecycle._print_wrapper`).
  Do not re-declare `.ct-*` rules anywhere else; all three copies that once existed had
  drifted into showing the customer a different document from the one staff printed.

## Master Project

A lightweight container doctype grouping ordinary Projects into a program/portfolio. Projects join via the **`Project.custom_master_project`** Link field (no child table on the Master side); **`Project.custom_subproject_order`** controls ordering under the master. `get_projects_and_tasks` returns member Projects and their Tasks for the form's read-only HTML tables. The dashboard's `get_master_project_projects` / `update_master_project_structure` reuse the same grouping (the latter persists drag-reordering).

## Procurement Tracker

The collapsible procurement tree at the bottom of the Budget tab. A self-contained **Vue 3** app
(no table library) mounted into the `custom_material_request_feed` HTML field by
[`public/js/project_enhancements.js`](../public/js/project_enhancements.js) — the field label
"Material Request Feed" is a historical misnomer, it renders all six procurement doctypes.
Not to be confused with ERPNext's standard **"Procurement Tracker"** Script Report (module
Buying), which is unrelated and not in this repo.

- **Server:** `get_procurement_documents` (`__init__.py:355-423`) regroups the output of
  `get_procurement_status` (`:11-219`) from item-centric into document-centric — DocType → document
  → items, each item still carrying its full MR/RFQ/SQ/PO/PR/PI/Stock-Entry chain. Both are
  whitelisted with **no permission check**; the MCP tool `project_procurement_status` adds its own
  `require_doc_read("Project", …)` gate precisely because the browser path has none.
- **Which documents:** one `UNION ALL` — the Material Request chain (matched on `mr_item.project`,
  `mr.custom_project` or `rfq.custom_project`) plus direct Purchase Orders with no MR link — then a
  per-doctype sweep for documents linked to the project that never appeared in a chain.
- **No caching**, server or client: a fresh round-trip on every form refresh.

Anything beyond a passing change here wants
[`docs/procurement-tracker-map.md`](../../docs/procurement-tracker-map.md) first — it maps the
render path, the chain SQL and its `OR`-join fan-out, the three separate things called "status",
and the production data volumes.

## Pick Routing Map (v1.190.0)

A job's material sits at several vendors' will-call counters at once, and nothing in Desk answered "what is still out there, and what is the shortest way round to collect it?". The **Pick Routing Map** button in the Budget tab's *Material Pickup* section now does.

- **Server:** [`api/pickup_routing.py`](../api/pickup_routing.py) → `get_pickup_route_data(project, scope)`. One round-trip: the Google Maps browser key, the depot, the job-site address, and one *stop* per supplier pick-up address carrying the Purchase Orders and lines behind it. Gated on `Project.check_permission("read")` **and nothing more** — the purchasing reads use `frappe.get_all` (ignore-permissions), matching the Procurement Tracker higher up the same tab. See the [api README's security model](../api/README.md#security-model).
- **Which POs:** the union of the header `Purchase Order.project` and the item-row `Purchase Order Item.project` — they disagree on real data, and either alone drops POs. `scope` is `outstanding` (default: submitted, `status` not `Closed`/`Delivered`, `per_received < 100`), `submitted`, or `all` (drafts too).
- **Where each stop is:** a four-step chain — `po.dispatch_address` → `po.supplier_address` → `Supplier.supplier_primary_address` → the Address directory (`Dynamic Link`), preferring a `Shipping`/`Warehouse`/`Shop`/`Plant` address type. `po.shipping_address` is **excluded on purpose**: on this site it is our own yard on nearly every PO. The winning step comes back in `address_source`, and a supplier that resolves to nothing is still returned with `address: null` so the UI can link to the vendor record that needs an address.
- **Client:** [`public/js/project_enhancements/pick_routing_map.js`](../public/js/project_enhancements/pick_routing_map.js) — an extra-large dialog, ordered stop list beside the map. The optimisation is Google's `DirectionsService` with `optimizeWaypoints: true`, run in the browser, so the routing itself is still never done server-side. Stops whose Address was picked from the Places autocomplete arrive with coordinates and skip the geocode entirely — both for the fallback pins and as `DirectionsService` waypoints, which routes to the exact building rather than to Google's reading of the address text. Most Addresses have no stored point and geocode from text exactly as before; a run freely mixes the two. (The Routes API engine deliberately still sends text — see the comment on `intermediates`.) Finish at the shop (default), the job site, or a typed address.
- **Settings:** `ERPNext Enhancements Settings.pickup_route_start_address` (Purchasing Controls) is where the run starts; blank falls back to the shop. The map reuses `Travel Settings.google_maps_api_key`, which needs the **Directions API** enabled on it as well as Maps JavaScript. That key is the shared desk maps key — its own field description in Travel Settings lists every API the desk features need, including **Places API (New)** for address autocomplete.
- **Degrades in three steps, each still usable:** optimised route → geocoded pins in PO order (key without the Directions API) → an ordered list of Google Maps links (no key at all). "Open in Google Maps" works at every step.

## Outstanding-material reports (v1.323.0)

The Pick Routing Map answers "what is still out there for **this job**, and how do I drive it".
Two reports answer the two questions either side of that, off the same data and the same rule.

- **Supplier Pickup List** (`report/supplier_pickup_list/`, Script Report) — one vendor, every
  job. The sheet somebody carries to a will-call counter. Filters: Supplier, Job, Expected On
  or Before, Include Closed / Delivered. One row per unreceived Purchase Order line.
- **Pending Items by Project** (`report/pending_items_by_project/`, Query Report) — one job,
  every vendor, as a flat list rather than a map. Project filter, required.

Three decisions are shared, and each one is load-bearing:

- **"Still outstanding" is `procurement_project.SETTLED_PO_STATUSES`**, imported by the script
  report and restated in the query report's SQL: submitted, `status` not `Closed`/`Delivered`,
  and quantity left on the line. Taking `per_received < 100` alone — which is what "all
  submitted POs not fully received" means literally — is not close: **81 of the 123** submitted
  orders matching it on production are `Closed`, worth 161 dead item rows against the 148 live
  ones. `Closed` is a deliberate "stop chasing this"; `Delivered` is a drop-ship.
- **The project match is a union** — `ifnull(nullif(poi.project, ''), po.project)`, the row
  winning and the header as fallback. 40 of the 148 live pending lines have no row project and
  32 of those sit under an order whose header names the job. On PRJ-00566 the union returns 63
  rows where a row-only match returns 37.
- **Pending quantity is in the line's own UOM.** ERPNext maintains `received_qty` against `qty`
  (`target_ref_field="qty"`), never `stock_qty`, so `qty - received_qty` is a number in `uom` —
  which is why UOM is a column on both reports.

`supplier_pickup_list.html` is the pickup checklist print format. Frappe wires it by filename
alone (`get_html_format` reads `<report>.html` from the report folder), so there is no Print
Format record and nothing to register: tick box first, supplier and date at the top, one page
per supplier, and a signature line. It is bypassed if the user picks explicit columns in the
print dialog — frappe falls back to `print_grid` then.

## `hooks.py` touchpoints

- `doc_events`: Project `after_save` → `sync_attachments_from_opportunity`; Project/Task `on_update` → `…project_dashboard.publish_realtime_update`.
- `scheduler_events.daily` → `send_project_start_reminders`.
- `override_doctype_dashboards`: `Project` → `get_dashboard_data`; `Employee` → `dashboard_overrides.get_data`.
- `override_whitelisted_methods`: `erpnext…opportunity.make_project` → `opportunity_enhancements.make_project`.
- `doctype_js["Project"]` includes `public/js/project_enhancements/project_gantt_widget.js` — the embeddable Gantt widget's first embed, mounted into `custom_gantt_chart_html` on the Schedule tab (read-only, status filter + Today; replaced the legacy interactive frappe-gantt renderer that lived in `doctype/project/project.js` — see the [public README](../public/README.md)).
- `doctype_js["Project"]` also includes `public/js/project_enhancements/pick_routing_map.js`, which binds the Budget tab's `custom_btn_pick_routing_map` Button (created by the `add_project_pick_routing_button` patch).

## Gotchas

- **Mixed indentation:** most files use tabs; `dashboard_overrides.py` uses 4 spaces.
- `get_all_projects_for_gantt` deliberately drops the `check_permission()` gate (reads with `get_all`) and filters to client-facing project types. **As of the widget-based portfolio Gantt it has no JS consumers** (the block now goes through the permission-checked `api/gantt.py::get_gantt_data`, which enforces the caller's Project/Task read permissions — a deliberate tightening); it remains whitelisted for now and is a removal candidate together with `update_project_dates_from_gantt` / `update_task_dates_from_gantt` / `update_task_progress_from_gantt` / `add_task_dependency` (their last consumers were the retired frappe-gantt embeds).
- Several Task fields are queried conditionally via `frappe.get_meta(...).has_field(...)` (`custom_is_recurring`, `baseline_start_date/baseline_end_date`) because they are optional site-level custom fields.
- `merge_projects` uses `frappe.db.set_value` for child tables/Singles (speed) but `doc.save()` for parents (to fire controller logic), and `log_error`s per-doc failures rather than aborting the whole merge. `get_linked_doctypes` discovers Project links dynamically from metadata, so any new Link-to-Project field automatically expands merge scope.
- **Export/print does not come from a server-rendered PDF, and that is deliberate.** Server-side PDF is non-functional on production — *both* backends fail for environment reasons this repo cannot fix (`docs/pdf-generation.md`). The two Print Formats above render their HTML print views correctly and browser print-to-PDF works, but the desk's **Download PDF** button will not until that runbook is executed on the VM. Every Print action added in v1.266.0 therefore opens a browser print window instead.
- **PNG export was here before and was removed** (added v1.166.0, removed v1.167.0 — "at request", no reason recorded). It captured the DHTMLX DOM with `dom-to-image` from a CDN, which could not have worked reliably at scale: **DHTMLX virtualises its rows**, so a large chart exported only the ~40 near the viewport, clipped to the current scroll position. v1.266.0 brought it back rendering vector SVG from the row data instead (`public/js/gantt_widget/gantt_export.js`). If you are tempted by DHTMLX's built-in `exportToPNG`/`exportToPDF`: they POST the chart to `export.dhtmlx.com`.
- `project.js` no longer renders a Gantt: the drag-editable frappe-gantt (with heatmap, dependency linking and PNG export) was replaced by the read-only embeddable widget in `project_gantt_widget.js`; editing returns with the widget's per-embed edit opt-in milestone. `project.js` keeps the health banner (bound off the `custom_gantt_chart_html` field object, guarded by `__health_bound`) and the reminder button.
