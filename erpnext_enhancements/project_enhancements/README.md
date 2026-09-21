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
| `doctype/project/project.py` | List-view grouping + printable Project Brief data (the half that is the same for every job) | `get_project_grouping_option`, `get_project_brief_data` | Whitelisted (client scripts) |
| `project_brief.py` | The half of the Project Brief that depends on the *kind* of work — one section per line of work the job involves (Design / Build / Products / Service / Events), returned as render-neutral blocks. See [Project Brief sections](#project-brief-sections-v14440) | `brief_sections`, `applicable_types` | Called by `get_project_brief_data` |
| `doctype/project/project.js` | Health banner + reminder button (the Schedule-tab Gantt it used to render in `custom_gantt_chart_html` is now the embeddable widget — `public/js/project_enhancements/project_gantt_widget.js`) | two `frappe.ui.form.on("Project", {refresh})` handlers | `doctype_js["Project"]` |
| `doctype/project/project_list.js` | Project list-view tweaks | — | list view |
| `doctype/address/address.js` | Live full-address build + Google Maps embed; attaches the global Places autocomplete to `address_line1` (widget: `public/js/global_enhancements/address_autocomplete.js`) and records the picked place in `custom_google_place_id` / `custom_latitude` / `custom_longitude`. The coordinates are **user-editable** (v1.207.0) for sites the address cannot locate; `custom_location_source` records whether the point came from Google (discarded when the address text is edited) or was typed (kept) | Address form handlers | `doctype_js["Address"]` |
| `doctype/change_order/change_order.py` | The `Change Order` controller (WI-075 sub-phase K): per-project numbering, a derived status, an impact type read from the **sign** of the money, and an outright refusal to be amended. Judgement in [`quality/change_orders.py`](../quality/change_orders.py), which imports no `frappe` — `project_enhancements/__init__` does, which would put it out of reach of the bench-free tests | `ChangeOrder`, `insert_with_retry` | Doctype controller; approvals via [`api/change_order.py`](../api/change_order.py) |
| `doctype/change_order/change_order.js` | The approval buttons. **Not optional** — every approval stamp is read-only and `before_submit` refuses a change order neither party has approved, so until v1.465.0 the four endpoints in `api/change_order.py` had no caller and no change order could be locked at all. Auto-loaded by frappe; deliberately **not** in `doctype_js`, which would append it a second time with no dedupe | form script | — |
| `doctype/project_budget_category/project_budget_category.py` | The budget-category catalog (WI-075 sub-phase M). Its one real job: **the protection flag cannot be quietly unticked** — `budgets.PROTECTED` is the rule and the Check is its display, so `validate` restores a flag that has been removed and never clears one that has been added | `ProjectBudgetCategory` | Doctype controller; seeded by `seed_budget_categories` |
| `doctype/project_budget_line/project_budget_line.py` | Child table on Project: one category, what it is budgeted at, and the computed committed/actual with a **coverage verdict** beside them. No controller logic — everything that decides anything lives on the parent, and a child controller with opinions would be a third place the same rules could disagree | `ProjectBudgetLine` | child-table controller |
| `doctype/budget_reallocation/budget_reallocation.py` | Submittable record of money moving between two categories. Net zero by construction and checked at apply time; a protected category on either side needs a second approval that cannot come from the requester or the approving PM; amendment refused, and a cancellation that would drive a line negative refused too | `BudgetReallocation` | Doctype controller; approvals via [`api/project_budget.py`](../api/project_budget.py) |
| `doctype/budget_reallocation/budget_reallocation.js` | The approval buttons. **Not optional** — every approval field is read-only and `before_submit` refuses a reallocation with no PM approval, so without these no reallocation could be submitted at all. Auto-loaded by frappe; deliberately **not** in `doctype_js`, which would append it a second time with no dedupe | form script | — |
| `budget_rollup.py` | The frappe glue that fills in each budget line's committed, actual and coverage, then derives `Project.estimated_costing` from the lines. Returns on its first line when a project has no budget lines — and deliberately does **not** zero a total it cannot derive. Judgement in [`quality/budgets.py`](../quality/budgets.py), which imports no `frappe` | `refresh`, `spend_for_project`, `unclassified_for_project`, `on_project_validate` | `doc_events["Project"]["validate"]` |
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

## Scope of Work (WI-075, v1.447.0)

`Project Scope of Work` is the scope authored once and locked — one submitted record per
project, whose `Scope Acceptance Criterion` rows are the measurable standards an inspector
later passes or fails. **Submit is the lock**; there is deliberately no separate approval flag
to fall out of step with `docstatus`.

It lives here rather than in the `quality` module because this is where the contract machinery
already is, and a second contract system is precisely what the programme exists to prevent.
The Statement of Work reads from it; so, later, does the inspection template.

**`criterion_key` is the field everything joins on.** Minted once, read-only, never
regenerated — because the inspection result, the NCR and the Quality Action that follow all
point at *a criterion*, and inserting a row mid-list a year later must not repoint a closed
NCR at a different standard. The rules live in
[`quality/scope_criteria.py`](../quality/scope_criteria.py), which imports no `frappe` so that
[`tests/test_scope_criteria.py`](../tests/test_scope_criteria.py) can run without a bench.

Locking refuses a scope with no criteria, and refuses any criterion missing a criterion, a
pass standard or a verification method. Those blank-checks are Python, not SQL: under PAD SPACE
collation `<> ''` treats `"   "` as present, and that check would report clean forever.

Locking stamps `Project.custom_scope_of_work` and `custom_scope_locked_on`, and cancelling
clears them. That mirror swallows and logs rather than raising — a failure to stamp must not
undo a lock — and uses `frappe.db.set_value`, never `doc.save()`, because Project carries heavy
`on_update` hooks and a wildcard `after_save`.

**It is deliberately not a hand-off step.** That was considered and declined for now on blast
radius: the record needs a Project so it cannot precede step 3, anywhere but last means
renumbering steps 707 live rows already carry, and `hand_off_sla_compliance` hardcodes
`LAUNCH_STEP_NUMBER = 7`. The step, if it comes, is its own change.


## Change Order

A first-class submittable record rather than a `Project Contract` revision, and the whole reason
is one field: **cause**. A revision counter can tell you a contract changed; it can never tell you
whether the customer asked for something, the site turned out different, or we got it wrong.
"How much did the job change" and "how much change did we cause" are different questions, and only
the second is a quality signal.

Submit is the lock, exactly as on `Project Scope of Work`. After submit the added acceptance
criteria are contracted, and **the inspection generator starts putting them on inspections for the
milestones they name** — which is the point. Without that wiring a change order would be scope
sold after the Scope of Work was locked and checked by nothing, the failure this whole programme
exists to end arriving through the one door nobody watches.

Four decisions worth not undoing:

- **The number is per project.** A naming series counter is global, so the third change order on
  one job would be `CO-047` — which tells a customer reading it how busy the rest of the company
  has been and nothing about their own job. Gaps are preserved: if `CO-002` was voided the next is
  `CO-004`, because reusing 3 puts two documents behind one number in somebody's inbox.
- **Money carries its own sign.** A credit is a negative `cost_impact`, and `cost_impact_type` is
  *derived* from it. The tempting shape — a positive magnitude plus an Addition/Credit Select — is
  two fields that can disagree about one fact, and the first time they do a credit is totalled as
  an addition.
- **Status is derived from `docstatus` and the approvals, never typed.** A stored status beside
  `docstatus` is a second state free to drift from the first, and on a commercial instrument that
  drift is the argument.
- **Amending is refused.** Frappe's amend path appends `-1`, so amending `PRJ-00580-CO-003` gives
  `PRJ-00580-CO-003-1` — a second commercial instrument with almost the same name as the first,
  in a place where the two get quoted at each other. A wrong change order is cancelled and
  re-raised.

The approval stamps are read-only and written only by `api/change_order.py`, from the server clock
and the session user. The old client path on `complete_step` let the browser propose a timestamp
and the audit found retroactive box-ticking.

Those endpoints are reached from `change_order.js` — *Approve as Project Manager*, *Record
Customer Approval* and a revoke beside each, offered only while the change order is unsubmitted.
The form proposes neither approver nor timestamp; it sends the change order's name and, for the
customer, **how** they approved, which the endpoint requires because an approval nobody can point
at is not a record. Recording the customer's approval is deliberately not the same act as the
customer clicking something: approval arrives by signature, by email, or in a meeting.

The judgement lives in `quality/change_orders.py` rather than beside the DocType, because
`project_enhancements/__init__` imports `frappe` at module scope and anything under it is
unreachable from the bench-free test tier — the same reason `quality/scope_criteria.py` sits there
while `Project Scope of Work` sits here.

## Project budget by category

Seven categories (`Project Budget Category`), a table of them on each Project
(`Project Budget Line`), and a submittable record of money moving between two of them
(`Budget Reallocation`). WI-075 sub-phase M.

**This is structure, not numbers.** WI-057 owns getting budgets onto projects and its acceptance
criterion is not restated here — re-measured 2026-09-14, `estimated_costing` is still zero on all
654 projects, two months after that work item counted it. What M adds is that once category lines
exist, the project total **is their sum** and is maintained from them, so filling in the
categories produces WI-057's denominator rather than being a second number to keep in step. A
project with no lines is left alone: deriving a total from an empty list would erase the figure
that backfill is about to write.

The native `Budget` doctype stays rejected for WI-057's reason — it budgets by GL account, cost
centre and fiscal year rather than per project. It holds 0 rows on prod.

### Why a spend figure never travels alone

The obvious shape for a budget line is `category | budgeted | committed | actual`, and on this
site three of those four columns would read `0.00` forever while looking entirely correct.
Measured 2026-09-14: `tabTimesheet` holds **0 rows**, so there are no labour actuals of any kind
for any project; **no Purchase Invoice has submitted lines**, so there are no material actuals
either; and purchase *orders*, which do exist (324 project-tagged lines, $106,242), cannot be
attributed to a category, because 217 of those 324 carry item group `Products` and the item group
tree has no labour / materials / equipment / subcontract axis anywhere in it.

That is the trailing-space failure in another costume — *it returns a number, the number is about
nothing, and nothing looks wrong* — and it is the same reason this programme rejected core
`Supplier Scorecard`. So each line carries a **coverage verdict**:

| Verdict | Means | A zero beside it means |
|---|---|---|
| `Tracked` | the source is in use on this site | nothing was spent on this project |
| `Not Tracked` | the source holds no rows anywhere | **nobody records this** — Labour, today |
| `No Source` | nothing could be spent against it directly | correct: Contingency and Fee are reserves money leaves by *reallocation*, never by purchase |

Coverage is judged **site-wide, not per project**, on purpose: "the instrument is not in use" is a
fact about the company, and a project that genuinely has no purchase orders should read `Tracked`
with zero rather than be told its data is missing. Separating *we looked and found nothing* from
*nobody ever recorded this* is the entire point.

The same discipline governs the variance percentage: a proportion of a zero budget comes back
**null**, never 0% ("on budget") and never a huge number. Sub-phase L made the identical call for
an MSA with no recorded expiry.

Purchase spend naming no category is reported in its own `unclassified` bucket — never folded
into a category, which would invent an attribution nobody made, and never dropped, which would
under-report the job silently in the direction that looks clean. Today it is all of it:
`Purchase Order Item.custom_budget_category` ships in this release, so every existing line
predates it.

### Reallocation

- **Net zero by construction**, and checked at apply time as well as in tests. The project total
  is the sum of the lines, so a move that changed it would silently rewrite the denominator
  WI-058 will eventually divide by.
- **Protected categories are protected on both sides.** General Conditions, Contingency and Fee:
  taking money out of contingency is how an overrun gets hidden, and moving money into fee
  converts contracted work into margin.
- **The second approval cannot come from the same hand** — not the requester, not the project
  manager who already approved. A control one person satisfies by clicking twice is a control in
  appearance only.
- **Balances are stamped, then never re-read.** The lines go on changing; this document records
  one move against the numbers as they stood that day.
- **Amendment is refused**, and so is a cancellation that would drive a line negative — if a later
  reallocation already spent the money, a compensating reallocation is the correct instrument.

Applying one saves the Project through the document API. That is **not** the thing WI-057 forbids:
that rule is about *bulk* patches walking hundreds of projects, which must batch
`frappe.db.set_value`. This is one project saved once because a person deliberately moved money on
it, and writing the child rows with `db.set_value` would skip the parent recalculation and could
not create a line the project does not have yet.

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
- **Branding** (`contract_style.py`, v1.194.0; on the print design system since v1.494.0) —
  the letterhead (the pillar stripe, the inline SVG wordmark, an eyebrow naming the pillar
  and the `@font-face` for the display face, all inside one `.ct-letterhead` element) and
  the running footer (contract number + page numbers) that wrap every agreement. The pillar
  comes from the template: owner / architect / SOW / MSA are Build, maintenance is Service,
  rental is Rent, the NDA and employee agreement take the neutral band (`PILLAR_BY_TEMPLATE`,
  `pillar_for`). Deliberately emitted by the *wrapper*, not by the templates: a signed contract
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

## Project Brief sections (v1.444.0)

The **Project Brief** (View ▸ Project Brief on the Project form) used to be one fixed
sheet for every job — Sapphire's scanned paper template, pre-filled. A Design job and an
Events job got the same page, so the four times that run an Events job were not on it and
neither were the design phase fees. `project_brief.py` adds a section per line of work the
job involves; `public/js/project_enhancements/project_brief.js` renders and prints them.
Since v1.494.0 the sheet wears the print design system's chrome — the pillar stripe for the
job's leading stream, the wordmark, the display-face title — rendered client-side from a
copy of `print_style.PILLARS` that `tests/test_print_style.py` keeps in agreement with the
Python source ([docs/print-design-system.md](../../docs/print-design-system.md)).

**Which sections a job gets is not `project_type` alone, and that is the load-bearing
detail.** `project_type` (labelled "Project Stage") is one Link; `custom_value_stream` is a
Table MultiSelect holding several. The brief takes the **union**, narrowed to the five
streams that have content, `project_type` first. Both sources are needed:

- **Products is a Value Stream and has never been a Project Type.** The
  [`seed_delivery_and_products_categories`](../patches/seed_delivery_and_products_categories.py)
  patch created the Project Type `Delivery` but only the *value streams* `Delivery` and
  `Products`. All 7 Products jobs on prod are `project_type = "Design"`, so keyed on
  `project_type` alone a Products brief could not exist.
- **A job routinely spans streams.** 21 jobs are stage Design carrying a Build stream, 6
  are the reverse, 7 are stage Design carrying Products. One value would drop half the brief.

| Section | Project's own fields | From a linked document |
|---|---|---|
| **Design** | scope + deliverables tables, hours budget | design retainer, the three phase fees and their calendar days, scope of work (Project Contract, `owner`/`architect`/`sow`) |
| **Build** | scope + deliverables, build status, production start/complete, quality sign-off, bid cost, materials budget, T&M | mobilization / substantial / final / anticipated completion, working hours, site access (Project Contract, `owner`/`sow`/`msa`); open Purchase Orders |
| **Products** | contract value, payment received + method | purchase-order item lines, Product Configurations, invoiced/outstanding totals |
| **Service** | scope + deliverables | plan, status, term, visit frequency, invoicing, recurring amount, next billing, seasonal months, covered features, last 5 visits (**Sapphire Maintenance Contract**, not the Project Contract's maintenance section — that is the signable paper, this is the live agreement the scheduler drives) |
| **Events** | delivery / setup / event / take-down datetimes, each with its notes; scheduling notes | rental dates, the fee schedule, security deposit, equipment list (Project Contract, `rental`) |

Four things worth knowing before editing it:

- **Blocks are data, not markup.** Each section is a list of `fields` / `list` / `text` /
  `table` blocks and the client renders them. Values travel unformatted with a `format`
  name beside them, so dates and currency come out in the *reader's* settings.
- **There are two kinds of empty.** A block of the Project's own fields renders even when
  every value is blank — the printed brief is a fillable form, and an Events sheet with
  four blank date slots is the sheet somebody writes the setup time onto. A block of a
  *linked document's* fields disappears when that document is absent: no `rental` contract
  means no rental agreement, and nine blank currency lines would invent one. All 16 Project
  Contracts on prod are `maintenance`, so this is live, not theoretical.

  **With one bound: a linked-document block may only disappear when its section keeps a
  Project-sourced block either way.** Every stream gets a section on a Project nobody has
  filled in yet — that is the point of a brief specific to the kind of job. Events keeps its
  four dates and Build its production line, so "Rental Terms" and "Construction Schedule" are
  free to drop. **Design and Service have no fields of their own on Project at all**, so
  their blocks are the section's anchor and stay `fillable=True`, printing as a blank design
  fee schedule and a blank agreement form — the case for 338 of the 354 Service jobs on prod.
  `test_every_stream_gets_a_section_on_an_empty_project` holds the whole rule, so a
  `fillable=False` on the wrong block fails the build instead of silently deleting a section.
- **A contract is matched to its section by template, with no fallback.** Project Contract
  carries every template's fields and fills only its own, so a `maintenance` contract under
  an Events heading would print another agreement's zeros as this job's rental terms.
- **Every cross-doctype read is gated on `frappe.has_permission` and swallows its own
  errors.** `frappe.get_all` ignores permissions (`get_list` is the checked one), and the
  brief now carries contract fees, committed PO value and maintenance terms — so
  `get_project_brief_data` also gained an explicit `check_permission("read")` on the
  Project, which it had never had. A reader without Purchase Order read gets a section
  short, not somebody else's numbers.

Covered by [`tests/test_project_brief_sections.py`](../tests/test_project_brief_sections.py)
(bench-free, own frappe stub, own CI step), which runs the builders rather than reading
them and checks every fieldname they read against the JSON that defines it — a renamed
Custom Field otherwise blanks a brief line forever and looks exactly like a project nobody
filled in.

## Master Project

A lightweight container doctype grouping ordinary Projects into a program/portfolio. Projects join via the **`Project.custom_master_project`** Link field (no child table on the Master side); **`Project.custom_subproject_order`** controls ordering under the master. `get_projects_and_tasks` returns member Projects and their Tasks for the form's read-only HTML tables. The dashboard's `get_master_project_projects` / `update_master_project_structure` reuse the same grouping (the latter persists drag-reordering).

## Procurement Tracker

The collapsible procurement tree at the bottom of the Budget tab. A self-contained **Vue 3** app
(no table library) mounted into the `custom_material_request_feed` HTML field by
[`public/js/project_enhancements.js`](../public/js/project_enhancements.js) — the field label
"Material Request Feed" is a historical misnomer, it renders all six procurement doctypes.
Not to be confused with ERPNext's standard **"Procurement Tracker"** Script Report (module
Buying), which is unrelated and not in this repo.

- **Server:** `get_procurement_documents` regroups the output of `get_procurement_status` from
  item-centric into document-centric — DocType → document → items, each item still carrying its
  full MR/RFQ/SQ/PO/PR/PI/Stock-Entry chain. Both are whitelisted and both gate on
  **Project read** via `require_project_read` (a login-only whitelist otherwise lets any
  authenticated user read any job's supplier/quotation/PO/invoice chain by enumerating the
  sequential `PRJ-xxxxx` names). The MCP tool `project_procurement_status` still runs its own
  `require_doc_read("Project", …)` upstream; the two checks are consistent. `procurement_project.
  get_receivable_purchase_orders` carries the same gate.
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

Since v1.338.0 the same machinery runs the question the other way round — see [Supplier Pick Sheet](#supplier-pick-sheet-v13380) below.

- **Server:** [`api/pickup_routing.py`](../api/pickup_routing.py) → `get_pickup_route_data(project, scope)`. One round-trip: the Google Maps browser key, the depot, the job-site address, and one *stop* per supplier pick-up address carrying the Purchase Orders and lines behind it. Gated on `Project.check_permission("read")` **and nothing more** — the purchasing reads use `frappe.get_all` (ignore-permissions), matching the Procurement Tracker higher up the same tab. See the [api README's security model](../api/README.md#security-model).
- **Which POs:** the union of the header `Purchase Order.project` and the item-row `Purchase Order Item.project` — they disagree on real data, and either alone drops POs. `scope` is `outstanding` (default: submitted, `status` not `Closed`/`Delivered`, `per_received < 100`), `submitted`, or `all` (drafts too).
- **Where each stop is:** a four-step chain — `po.dispatch_address` → `po.supplier_address` → `Supplier.supplier_primary_address` → the Address directory (`Dynamic Link`), preferring a `Shipping`/`Warehouse`/`Shop`/`Plant` address type. `po.shipping_address` is **excluded on purpose**: on this site it is our own yard on nearly every PO. The winning step comes back in `address_source`, and a supplier that resolves to nothing is still returned with `address: null` so the UI can link to the vendor record that needs an address.
- **Client:** [`public/js/project_enhancements/pick_routing_map.js`](../public/js/project_enhancements/pick_routing_map.js) — an extra-large dialog, ordered stop list beside the map. The optimisation is Google's `DirectionsService` with `optimizeWaypoints: true`, run in the browser, so the routing itself is still never done server-side. Stops whose Address was picked from the Places autocomplete arrive with coordinates and skip the geocode entirely — both for the fallback pins and as `DirectionsService` waypoints, which routes to the exact building rather than to Google's reading of the address text. Most Addresses have no stored point and geocode from text exactly as before; a run freely mixes the two. (The Routes API engine deliberately still sends text — see the comment on `intermediates`.) Finish at the shop (default), the job site, or a typed address.
- **Settings:** `ERPNext Enhancements Settings.pickup_route_start_address` (Purchasing Controls) is where the run starts; blank falls back to the shop. The map reuses `Travel Settings.google_maps_api_key`, which needs the **Directions API** enabled on it as well as Maps JavaScript. That key is the shared desk maps key — its own field description in Travel Settings lists every API the desk features need, including **Places API (New)** for address autocomplete.
- **Degrades in three steps, each still usable:** optimised route → geocoded pins in PO order (key without the Directions API) → an ordered list of Google Maps links (no key at all). "Open in Google Maps" works at every step.

## Supplier Pick Sheet (v1.338.0)

The Pick Routing Map is *one job, every vendor*. This is the same run turned inside out — **one vendor, every job** — because a crew already driving to Harrington should come back with everything Harrington is holding, not with one project's worth. Two entry points, both landing in the same dialog:

- **Supplier form → Pick Sheet**, a toolbar button ([`public/js/procurement/supplier_pick_sheet.js`](../public/js/procurement/supplier_pick_sheet.js)). A toolbar button rather than a Custom Field Button like the Project one, which needed a specific home on the Budget tab and paid a migration for it — Supplier's layout is stock, so a custom field would buy a fixture, a patch and a deletion procedure and nothing else.
- **Supplier list → Actions → Pick Sheet**, for several vendors at once, registered in [`supplier_list.js`](../public/js/global_enhancements/supplier_list.js)'s existing `onload` (a second file assigning `listview_settings.onload` would clobber the `get_args` override that lives there). Several suppliers means several stops, so the optimiser and the 23-waypoint ceiling apply exactly as on a project run.

- **Server:** `get_supplier_pick_data(suppliers, scope)` in the same [`api/pickup_routing.py`](../api/pickup_routing.py). Same stops, same `scope` rules, same four-step address chain, same money guard — sharing the code is the point, because two sheets that disagree about whether a PO is still outstanding is precisely what [`docs/pick-routing-map-po-details.md`](../../docs/pick-routing-map-po-details.md) rejected option (a) to avoid. `suppliers` takes a name, a list, or the JSON array a form-encoded caller sends; an empty list is **refused**, because an empty sheet reads as "nothing to collect".
- **Two differences, both structural.** `project` is `null` — there is no single job site to finish at, so that finish option greys itself out — and **every line carries its own `project`**, not its order's. One PO routinely spans jobs (`Purchase Order Item.project` is mandatory for exactly that reason), and the header is only a fallback for rows predating the rule.
- **The lines regroup by job, not by order.** At the counter the crew quotes PO numbers; at the tailgate the pile has to be split before anything is unloaded, and that is the sort the sheet prints. Each stop also carries a `projects` rollup — "Sorts into: PRJ-00566 (4 lines) · PRJ-00590 (2 lines)" — computed server-side from the same lines the tables print, so the summary and the tick boxes cannot drift.
- **Permission is `Purchase Order` read**, stricter than the project endpoint and for a reason: that one is anchored to a job the caller can already open, this one is anchored to nothing. See the [api README's security model](../api/README.md#security-model).
- **A supplier with nothing outstanding is still named on the sheet.** "Ferguson has nothing" and "we forgot Ferguson" look identical otherwise, and only one of them means the crew can skip the stop.

Related but not the same thing: the **Supplier Pickup List** report below answers the same question as a flat, filterable, printable grid. This is the map-and-tick-box surface — drive-time ordering, per-line tick boxes and a signature block — and it is what somebody hands a driver.

## Outstanding-material reports (v1.323.0)

The Pick Routing Map answers "what is still out there for **this job**, and how do I drive it".
Two reports answer the two questions either side of that, off the same data and the same rule.

- **Supplier Pickup List** (`report/supplier_pickup_list/`, Script Report) — one vendor, every
  job, as a filterable grid. Filters: Supplier, Job, Expected On or Before, Include Closed /
  Delivered. One row per unreceived Purchase Order line. The [Supplier Pick Sheet](#supplier-pick-sheet-v13380)
  answers the same question as a routed, tick-boxed sheet instead; the report is the one you
  filter and export, the sheet is the one you hand a driver.
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
- **The Schedule tab's Export dropdown carries a "Date range…" entry** (v1.441.0): a dialog with presets (whole schedule, remaining from today, next 30/90 days, this month, this quarter) plus From/To and a format, covering all five outputs. It narrows the rows *and* the calendar — see the [public README](../public/README.md) for why bars that cross an edge are clipped rather than clamped. The Projects Dashboard's portfolio Gantt has the same entry in its own toolbar markup (`widget.export_range_dialog(meta)`), seeded from that board's date-window filter.
- `project.js` no longer renders a Gantt: the drag-editable frappe-gantt (with heatmap, dependency linking and PNG export) was replaced by the read-only embeddable widget in `project_gantt_widget.js`; editing returns with the widget's per-embed edit opt-in milestone. `project.js` keeps the health banner (bound off the `custom_gantt_chart_html` field object, guarded by `__health_bound`) and the reminder button.
