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
| `page/project_planner/` | **Project Planner** desk page (v1.577.0): month, week and crew-timeline views of project Tasks, a *Resources available* panel, Needs crew and Unscheduled trays, drag to reschedule or to add a person. Phase 6A (`PP6A_METHODS`): a click on a name, a date or a project opens a side drawer over the calendar, and a card's editor is a side panel. See [Project Planner](#project-planner-v15770) and [Quick looks](#quick-looks-and-polish-phase-6a--task-2026-02467) | `ProjectPlanner` (JS) | Page; backend [`api/project_planner.py`](../api/project_planner.py) and [`api/planner_views.py`](../api/planner_views.py) |
| `crew_availability.py` | The availability engine **both planners share**: capacity per Planner Resource per day (work pattern, holidays, approved time off, all-day personal blocks) against everything that uses it (project tasks, rental crew tasks, maintenance visits, travel, timed personal blocks), with conflicts and work-restriction warnings | `availability`, `preview_conflicts`, `block_notes` (the only reader of a block's note, per viewer); pure `pattern_hours`, `day_capacity`, `task_span`, `task_slot`, `allocate_task`, `day_conflicts`, `day_conflict_details`, `equipment_findings`, `block_bookings`, `next_free_day`, `span_for_estimate`, `resolve_crew` | Called by `api/project_planner.py`, `api/maintenance_planner.py`, `api/planner_blocks.py` and `api/planner_conflicts.py` |
| `routing.py` | Daily routes and drive time (v1.578.0): where each booking is (task address → project site; rental venue; visit site), the shop's coordinates (geocoded once, cached in Settings), drive times from **Google Routes** (`computeRouteMatrix`, cached in `Planner Drive Time`) with a straight-line estimate whenever Google is off or refuses, stop ordering (time slots are anchors, the rest by cheapest insertion + 2-opt), arrival times, insertion cost for date suggestions, and a daily coordinate backfill | `plan_routes`, `drive_matrix`, `start_point`, `routes_status`, `backfill_coordinates`; pure `order_stops`, `route_times`, `insertion_cost`, `haversine_km`, `estimate_minutes`, `pair_key` | Called by `crew_availability` and `api/project_planner.py`; `scheduler_events.daily` → `backfill_coordinates` |
| `doctype/planner_drive_time/` | Cache of Google drive times between two points, keyed `lat,lng~lat,lng` (5 decimals; never `<` or `>`, which Frappe refuses in a document name — the original `>` failed every write, v1.578.1). Only Google answers are stored; rows older than 90 days are refreshed lazily | `PlannerDriveTime` | written by `routing.drive_matrix` |
| `planner_notices.py` | Draft-and-publish notices (one per affected person per publish) and the 48-hour change alerts (v1.581.0); both write a bell notification and use the email shell, with SMS through the dispatch digest's helper | `send_publish_notices`, `queue_task_change` (Task `on_update`), `flush_alerts`, `send_change_alerts` | `api/project_planner.publish_drafts`; `doc_events["Task"]["on_update"]` |
| `planner_digest.py` | The combined 6 AM digest (v1.581.0): one message per person with their whole day from the engine, led by the day's notes for their group and their own blocks (Phase 6D); at most once a day by claiming a `Planner Digest Log` row before sending | `send_daily_digests`, `send_preview`, `covered_users` | `scheduler_events.cron` 6 AM; off unless Settings → *One combined morning digest* |
| `planner_tracking.py` | Phase 4 tracking (v1.582.0): actual hours per task and person from the kiosk's Job Intervals, *running over*, the project labor forecast (booked from today + clocked, at each day's pay rate, cost only for `COST_ROLES`), and the Task equipment validator | `task_actuals`, `labor_forecast`, `can_see_cost`, `validate_equipment` | `api/project_planner` (`get_actuals`, `get_labor_forecast`); `budget_rollup.refresh_labor_forecast`; `doc_events["Task"]["validate"]` |
| `doctype/task_equipment/` | Child table on Task (`custom_equipment`): a Fleet Vehicle or an Asset the task uses, with a label filled by `validate_equipment` (one `fetch_from` cannot serve two links) | `TaskEquipment` | child-table controller |
| `report/crew_utilization/` | **Crew Utilization** Script Report (v1.582.0): per person per week or month, capacity vs booked (tasks, visits, rental, travel, driving) vs clocked, with **Other clocked** for time on non-customer work. Hours only, never money; runs the engine with `google=False` | `execute`, `periods_for`, `aggregate` | Linked from the planner's toolbar |
| `planner_weather.py` | Phase 5 weather flags (v1.583.0): one Open-Meteo request per load for every uncached site, each point cached 3 hours; flags rain ≥ 60%, a low ≤ 0 °C and wind ≥ 40 km/h on tasks ticked *Outdoor work*. Never fails a read and never logs a request URL | `day_flags`, `forecasts`, `forecast_for_tasks`, `span_weather` | `api/project_planner` (`get_planner`, `get_route`, `suggest_dates`, `who_is_free`) |
| `customer_confirmations.py` | Phase 5 customer date confirmations for **both** planners (v1.583.0), **off until Settings → *Email customers their visit date* is ticked**: triggers on a customer-facing task or a draft visit, the at-most-once send, the 10-minute sweep and the preview | `on_task_update`, `on_visit_update`, `keep_stamps`, `send_one`, `send_due_confirmations`, `preview` | `doc_events` (Task, Sapphire Maintenance Record); `scheduler_events`; `api/project_planner.preview_customer_confirmation` |
| `task_templates.py` | Phase 5 (v1.583.0): a Task made from a template Task gets the template's hours, crew size, qualifications and the two flags wherever it has none of its own; never overwrites, never copies the crew | `copy_template_planning` | `doc_events["Task"]["before_insert"]` |
| `doctype/planner_draft_change/` | One pending change per planner per task while in draft mode; Publish applies them, Discard drops them | `PlannerDraftChange` | `api/project_planner` |
| `doctype/planner_block/` | Phase 6D: time one person is not available on one day ("unavailable 2–4 pm", "shop day"), all day or from–to, with a private note. `PBLK-.YYYY.-.#####`. Desk: System Manager writes, the schedulers read; every planner write goes through `api/planner_blocks` (own-or-scheduler) | `PlannerBlock` (validates the person is active and the times, warns on a past day) | read by the engine (never the note) |
| `doctype/planner_day_note/` | Phase 6D: a note on a day for the whole crew or one group ("Shop meeting 7 am"), optionally about a project. `PDN-.YYYY.-.#####`. The schedulers write it | `PlannerDayNote` | `api/planner_blocks`; both planners, My week, the digests |
| `doctype/planner_conflict_ack/` | Phase 6D: a conflict kept on purpose from the Conflict center, with its reason and a fingerprint of what was kept. `PCA-.YYYY.-.#####`, never edited | `PlannerConflictAck` | `api/planner_conflicts.acknowledge_conflict` |
| `doctype/planner_digest_log/` | `user|date` claim (unique) that keeps the combined digest to once per person per day across workers and deploys | `PlannerDigestLog` | `planner_digest` |
| `crew_sync.py` | Mirrors a Task's crew rows into ordinary assignments (ToDos), adding only people new to the crew and removing only people taken off it; tidies the crew table on validate | `on_task_update`, `validate_crew` | `doc_events["Task"]` `on_update` / `validate` |
| `doctype/planner_resource/` | A bookable person or outside crew: Employee or Subcontractor, group (Field / PM / Design / Subcontractor), home team, and a weekly **work pattern** with optional date ranges. One active resource per employee | `PlannerResource` | Doctype controller; seeded by `patches/seed_planner_resources` |
| `doctype/planner_resource_work_pattern/` | Child table: hours for each weekday, optionally between two dates (a seasonal schedule) | `PlannerResourceWorkPattern` | child-table controller |
| `doctype/task_crew_member/` | Child table on Task (`custom_crew`): resource, hours (blank = an even share, or a full day), lead | `TaskCrewMember` | child-table controller |
| `doctype/task_required_credential/` | Child table behind Task's *Qualifications needed* (`custom_required_credentials`, a Table MultiSelect on Credential Type) | `TaskRequiredCredential` | child-table controller |
| `doctype/project_planner_settings/` | Single: full-day hours (8), maintenance visit hours (2), and the default hours of each rental crew task kind | `ProjectPlannerSettings` | read through `get_cached_doc` by the engine |
| `report/supplier_pickup_list/` | **Supplier Pickup List** Script Report — unreceived Purchase Order lines by vendor, plus `supplier_pickup_list.html`, the driver-facing checklist print template (on the print design system's chrome minus the wordmark, since the report wrapper prints the letter head above it — see [docs/print-design-system.md](../../docs/print-design-system.md)) | `execute`, `get_data` | Standard report (synced on migrate) |
| `report/pending_items_by_project/` | **Pending Items by Project** Query Report — unreceived Purchase Order lines for one job. The whole report is the SQL in its `.json`; the `.js` holds the filter, the colouring and the reasoning | — | Standard report (synced on migrate) |

Related code outside this folder:
- `api/planner_views.py` — the Phase 6A quick looks both planners open in a drawer: a person's week, everyone's day, a project, a maintenance site. Read-only and Google-free. See [Quick looks](#quick-looks-and-polish-phase-6a--task-2026-02467).
- `public/js/planner_kit/` + `public/js/planner_kit.bundle.js` — the planner kit, the small UI layer both planner pages share (drawer, side panel, toast, menu, legend, hints, hover glow, the person and day peeks). Loaded only by the two planner pages. See [Planner kit](#planner-kit).
- `public/js/planner_kit/conflicts.js` and `blocks.js` — Phase 6D UI: the Conflict center drawer and the personal-block and day-note markup and forms both planners and My week use. See [On screen](#on-screen-the-conflict-center-blocks-and-day-notes-phase-6d-ui--task-2026-02470).
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
- **Branding** (`contract_style.py`, v1.194.0; on the print design system since v1.495.0) —
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
Since v1.495.0 the sheet wears the print design system's chrome — the pillar stripe for the
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
- **Printing (v1.517.0):** every document row has **Print** (Frappe's print view, new tab) and
  every group header has **Print**, which prints the group as one PDF — **All**, or **Open**
  where the doctype has one (Material Request, Purchase Order, Purchase Invoice). "Open" is
  `procurement_quantities.document_is_open`, sent down as `is_open`; for Purchase Orders it is
  the Receive rule exactly. The PDF is Frappe's own `download_multi_pdf` behind
  [`procurement_print.py`](../procurement_print.py), which only adds the Project read gate and
  names the file for the job (`PRJ-00706-Open-Purchase-Orders.pdf`). Documents Frappe would
  refuse to print are filtered out in the browser and named in the dialog, because Frappe's
  multi-PDF drops them without a word.

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

## Project Planner (v1.577.0)

A drag-and-drop calendar for planning project Tasks and booking people by the hour
(`/app/project-planner`). It is linked from the Project Enhancements workspace and, since v1.583.1, from
the core **Projects** module sidebar right after Task, with Crew Utilization among its reports
(`setup/workspace_tweaks.add_core_sidebar_items`). Built so a PM cannot send one technician to two
places at once without being told. Scope set by Nik on 2026-10-08; the whole build is tracked
under TASK-2026-02427 in PRJ-00580, Phase 1 being TASK-2026-02428.

**Who can be booked.** A **Planner Resource** per person or outside crew, not Employee: the pool
includes subcontractors, who have no HR record, and only some staff (field techs, PMs on site,
designers). `patches/seed_planner_resources` created one for each active technician, Project
Manager and Design employee; anyone else is added by hand. Each has a weekly **work pattern** —
hours per weekday, optionally between two dates for a seasonal schedule. With no pattern rows a
person works Monday to Friday at the Settings' full-day hours. Austin works Monday to Wednesday.

**How a task books hours** (`crew_availability.allocate_task`):

- Its crew is the Task's **Crew** table (`custom_crew`) when it has rows, otherwise its open
  assignees who are Planner Resources. The second rule is how the tasks assigned before this
  existed, and the rental crew tasks, still count.
- `expected_time` is shared evenly across the crew (minus any row's own hours), then spread over
  each person's working days within the task's dates.
- **No estimate books a full day** for each person on each day — their pattern hours, or 8 when the
  pattern says they do not work. A rental crew task (Delivery, Setup, Take-down, Cleaning — made by
  `asset_management/rental_logistics.sync_tasks`) with no estimate books its kind's Settings hours
  instead.
- A task whose `custom_start_datetime`/`custom_end_datetime` fall on one day books that **time
  slot**, and two overlapping slots for one person are a double booking.
- A blank crew-row `hours` is 0 (a Frappe Float is never NULL), so 0 means "an even share", not
  "no hours".

**What else uses a person's day:** maintenance visits (Settings, 2h; clock-out minus clock-in once
recorded; plus the visits the scheduler has not drafted yet, projected exactly as the Maintenance
Planner projects them), Travel Trip days (the whole working day; a non-working day of a trip books
nothing and is not a conflict), company holidays and approved Time Off Requests (0, or half for a
half day — never naming the type or reason). Work Restrictions are warnings only.

**Overbooking warns and never blocks.** A change that *creates* a conflict — over hours, an
overlapping slot, a booking on a day off — comes back from `save_task` as `needs_reason`
unsaved. The page asks for a reason, sends it again, and the reason goes on the Task timeline.
Conflicts that were already there before the change do not ask again.

**Both planners share one engine.** The Maintenance Planner reads the same `availability()` for its
technicians' free hours and shows their project work read-only; each planner moves only its own
records. That is the whole point of the engine living here rather than in either API.

**Customer jobs only** (Nik, 2026-10-09, v1.582.0). The planners show Design, Build, Service,
Events (Rent) and Delivery work and nothing internal. A task counts when its project's
`project_type` is in `crew_availability.PLANNER_PROJECT_TYPES`, or the project has a `Value Stream`
row in `PLANNER_VALUE_STREAMS` (either the `value_stream` or the older `value_streams` column). A
rental crew task always counts, project or not. Internal, Group Projects, Overhead, a blank type
with no qualifying stream, and a task with no project are left out — PRJ-00580, PRJ-00739, the IDP
and Stage 1–4 projects among them. The condition lives once in the engine
(`planner_job_condition`, re-checked in Python by `is_planner_job`) and is applied in `read_tasks`,
so availability, conflicts, the heatmap, suggestions and the Maintenance Planner's project items
all agree; the Unscheduled and Overdue trays, the project filter and Copy week apply the same rule.
On 2026-10-09 production had 373 open customer-job projects and 26 internal ones.

Things that look like bugs and are not:

- An undated task dropped from the Unscheduled tray gets enough days for its estimate
  (`span_for_estimate`: 24h for one person is three weekdays), not always one day.
- A rental crew task cannot be moved here: its dates belong to its Rental Booking.
- Tasks on Completed, Invoiced, Paid or Canceled projects are not bookings; Client Hold and
  Parked ones still are, because a PM moving paused work needs to see it.
- Adding someone to a crew sends the standard "New ToDo Created" email, exactly as assigning from
  the sidebar does. Batching those notices is planned (draft and publish, TASK-2026-02448).

### Daily routes and drive time (v1.578.0, Phase 2 — TASK-2026-02435)

Every person's day is a route: **from the shop and back** (85 W 300 S, Bountiful — the same
`ERPNext Enhancements Settings.pickup_route_start_address` the pick-up runs start from, so "the
shop" is one setting), through each located task, rental crew task and maintenance visit.

- **Where a stop is**: the task's own address (`custom_locationaddress_of_task`), else its project's
  site (`workforce/sites.site_coordinates_bulk`: Maintenance Profile → linked Address → geocoded
  Project); a rental crew task's booking venue; a visit's site. A stop with no coordinates is
  listed (and counted as `unlocated`) but adds no driving. `backfill_coordinates` geocodes missing
  task and venue addresses daily, because a deploy's Redis flush kills any queued geocode.
- **Drive time** comes from Google Routes' `computeRouteMatrix` on the server key, cached per pair in
  `Planner Drive Time`. **One planner load makes one matrix request** for every pair it is missing,
  not one per person-day: each costs money. If Google refuses or fails, routing stops asking for an
  hour, logs once, and uses a straight-line estimate (`km × 1.3 at 56 km/h + 4 min`); every figure
  carries its source (`google` / `estimate` / `mixed`) and the page says which. Settings → *Drive
  times from Google Routes* turns Google off entirely.
- **Driving counts against hours.** With *Count drive time against people's hours* on (the default),
  each person-day gets a `drive` booking, so free hours, overbooking and the reason prompt all include
  it. A day with more driving than *Flag a day with more driving than* (90 min) carries `long_drive`.
  A travel day has no route.
- **Order of stops**: a task with a same-day time slot is an anchor at its time; everything else is
  placed by cheapest insertion and improved with 2-opt. Arrival times count from *Day starts at*
  (08:00); arriving early for a slot shows the wait instead of hiding it.
- **The route view** (`/app/project-planner/route/<person>/<date>`, linked from both planners) shows the
  stops in driving order on a Google map, with arrival times, drive per leg and *Open in Google Maps*.
  The map polyline is straight segments; the times are Google's.
- **Suggest dates** ranks the next 10 days (≤ 30) by the driving the task would add to each person's
  route (`insertion_cost`), among people with the free hours — the task's crew, else the Field
  group. It suggests **when**; it never picks a crew on its own (Nik declined that). "Book" saves the
  date and adds the person in one `save_task`, through the same reason prompt as a drag. Also a
  read-only AI tool: `crew_schedule_suggestions`, with `crew_day_route` beside it.
- **The Google key never reaches a log.** Only `routing._google_post` (Routes) and
  `workforce.sites.geocode_text` (Geocoding — the app's one server-side geocoder) touch it, and they
  report only a status or an exception's class name: a `requests` connection error contains the
  whole request URL. `api/project_planner.check_routes` makes one tiny call to confirm the console
  setting without showing the key.
- GET requests roll back their transaction in Frappe, so after caching a drive time or the shop's
  coordinates during one, routing sets `frappe.local.flags.commit`; otherwise the cache would vanish
  and every load would pay Google again.

### Planning helpers (v1.580.0, Phase 3A — TASK-2026-02441)

- **Pencil bookings** (`Task.custom_tentative`): a tentative task's hours are *soft load*. Each day
  cell has `soft_booked`, while `booked`, `free` and conflicts count firm work only. Pencilled work
  that would overbook someone is a warning, never a reason prompt, and it books no drive time.
  `crew_sync` gives a tentative task no assignments, and adds the whole crew when it is firmed up,
  so nobody is notified of work that may not happen.
- **Dependencies** (`depends_on`): moving a task to start before a predecessor ends adds a
  "Starts before …" line under *Dependencies* in the reason prompt. After a move later, the page
  offers to shift the tasks that follow by the same number of **working** days
  (`shift_successors`, all-or-nothing, furthest first). ERPNext's own
  `Task.on_update → reschedule_dependent_tasks` already pushes Open same-project dependents by
  calendar days on every save. The planner detects those (`successors.auto_moved`) and leaves
  them out of its offer, so nothing moves twice.
- **Qualification gaps**: a task's *Qualifications needed* that no crew member holds as a current
  Employee Credential (Valid or Expiring, not expired by the task's last day) shows as a chip.
  It is a warning only.
- **Heatmap** (`/app/project-planner/heatmap/<date>`): 8 weeks of booked vs available per person,
  with pencil load hatched. It runs the engine with `google=False`, so cached and estimated drive
  times only and never a fan-out of Routes calls. Weeks start on the site's **first weekday**
  (Sunday on production), the same as the calendar.
- **Overdue tray**: open, dated tasks on Active projects that ended before today, grouped by
  project, with bulk *Reschedule to…*, *Mark done* and *Cancel* (`bulk_update`: one savepoint per
  task, failures reported per task, writes "Canceled" with one l). ERPNext refuses to complete a
  task whose dependency is "Canceled", because its check knows only "Cancelled"; that shows as a
  per-task failure.
- **Copy week**: copies chosen tasks into another week as new Tasks, with crew, hours,
  qualifications, pencil flag, location and a "Copied from" note. It shows a dry-run preview
  first, and a conflict needs a reason.

### Telling people (v1.581.0, Phase 3B — TASK-2026-02441)

- **Draft and publish.** With *Draft mode* on, a drag or a dialog save writes a `Planner Draft
  Change` row for the planner instead of the Task. The planner overlays the caller's drafts
  (`get_planner(draft=1)`), and conflicts come back as information. **Publish** applies them all
  through the normal save path, one savepoint each. A draft whose task changed meanwhile is
  skipped and reported. A conflict across the batch needs one reason, all-or-nothing. Each
  affected person then gets **one** notice. Assignments happen at publish, not while drafting.
- **One combined morning message** (`planner_digest`): each person's whole day from the engine
  (tasks, visits, rental crew tasks, travel), with crewmates, addresses and a route link. **Off
  until Settings → *One combined morning digest* is ticked.** When on, the maintenance and rental
  digests skip the people it covers, so nobody gets three texts. It is sent at most once per
  person per day by inserting a `Planner Digest Log` row (unique `user|date`) **before** sending,
  committed; Redis would not survive a deploy's flush. *Send me a preview* on the Settings form
  emails the caller their own digest and texts nobody.
- **Change alerts** (`planner_notices`): a saved or published change to someone's bookings inside
  the next 48 hours queues one alert per person ("Your Thursday changed: …"): a bell notification
  plus a text, or email when there is no cell number. **Off until Settings → *Change alerts* is
  ticked.** Best effort: an alert queued as a deploy flushes Redis is lost.
- **Weekly crew sheet**: `crew_sheet_html(start)` renders the week on the print design system's
  chrome, landscape, and the page prints it from a browser window (server PDF is broken on
  production).
- **My week** (`/app/project-planner/my-week/<date>`): the signed-in person's week on a phone,
  with time, site, address (opens Google Maps), crewmates and a route link per day.

### Tracking (v1.582.0, Phase 4 — TASK-2026-02452)

- **Planned vs actual** (`planner_tracking.task_actuals`, `get_actuals`): actual hours are the
  kiosk's Job Intervals per task and person, net of pauses (an Open interval counts to now). Cards
  read "14h of 12h"; a task that is not finished and is more than 10% over its plan is **running
  over** (`over_plan`) and has a filter chip. Planned is `expected_time`, or the engine's allocated
  hours when there is no estimate. Someone who clocked onto the task but is not a Planner Resource
  is still listed, with `resource: None`.
- **Labor forecast** (`get_labor_forecast`, and `Project Budget Line.labor_forecast` on the Labor
  line): hours booked from today to the project's last open task (at most 180 days), plus hours
  already clocked, each at that day's `Employee Pay Rate`. Today counts once — a booking today
  counts only beyond what has been clocked today. A rate is burdened only where `burden_pct` is set;
  otherwise it is the base rate and the answer says "base rate" (Time Kiosk Settings' default
  burden is deliberately not used). Subcontractors and people without a rate show hours and
  `no_rate`. It is a forecast, not spend: the line's Actual and Coverage are untouched.
  - **Who sees money.** With one person on a job, the project's labor total *is* their wage, so
    costs go only to `planner_tracking.COST_ROLES` (System Manager, Finance Team, Executive Team,
    Estimator, HR Manager — whichever exist). Everyone else gets hours and nothing else, and their
    request never reads a pay rate.
  - **Why the stored field is permlevel 2, not 1.** ERPNext v16's own Project permissions give
    **Desk User** read at permlevel 1, and every desk user holds Desk User. A child-table field
    answers to its parent's permissions, so level 1 would show the forecast to everyone.
    `patches/grant_labor_forecast_visibility` grants level-2 read on Project to the cost roles (after
    `setup_custom_perms`, so a site without Custom DocPerm rows keeps its standard ones; production
    already had 27). Frappe strips level-2 fields from the save response for anyone else.
  - The stored figure is refreshed when the Project is saved (`budget_rollup.refresh_labor_forecast`,
    its own `try`), so it can go stale between saves; the planner's strip always reads it live.
- **Crew Utilization** report: capacity vs booked vs clocked per person per week (site's first
  weekday) or month. Clocked time on non-customer work is its own **Other clocked** column, never
  dropped. Hours only.
- **Equipment** (`Task.custom_equipment`, rows of Fleet Vehicle or Asset): two firm tasks on one
  vehicle or asset on the same day are a conflict (pencils are ignored on both sides), as is a
  vehicle In Shop or Retired, or an Asset with a submitted Asset Booking that overlaps — unless the
  booking is for the task's own project. Conflicts come through the same reason prompt, keyed
  `Equipment`, and publishing drafts checks the whole batch. `get_equipment` feeds the timeline's
  equipment row group.
- **No Google calls**: actuals, the forecast and the report all run the engine with `google=False`.

### Extras (v1.583.0, Phase 5 — TASK-2026-02457)

- **Templates carry planning** (`task_templates`): ERPNext's `create_task_from_template` sets
  `template_task`; on `before_insert` the new Task gets the template's `expected_time`,
  `custom_crew_size`, qualifications and the *Outdoor work* / *Customer-facing visit* flags wherever
  it has none of its own. It never overwrites, never copies the crew (people are a planning
  decision), and copies only from a Task with `is_template`. `before_insert` rather than
  `after_insert`, so the values go in with the one insert and pass the Task's own validation.
- **Weather** (`planner_weather`, Open-Meteo: free and keyless): a task ticked *Outdoor work*
  (`custom_outdoor`) carries `weather`, the days of its span with rain ≥ 60%, a low ≤ 0 °C or wind
  ≥ 40 km/h. `[]` means the forecast is clear and `null` means there is none, which the page draws
  as no chip. Route stops on outdoor tasks show it too, and *Suggest dates* scores each flagged day
  as 30 more minutes of driving and says why ("Forecast: Rain 70%") — a worse suggestion, never an
  impossible one. A failure hides the chip, backs off for 10 minutes and logs once an hour with a
  status or class name only.
- **Who is free** (`who_is_free`, and the read-only AI tool `crew_who_is_free`): per day, who has
  the hours free and why everyone else does not (day off, travelling, "Only 2h free (6h booked of
  8h)"). At most 31 days, and Google-free. It answers **when** and **who could**; it never books
  anyone ("suggest a crew" was declined).
- **Customer date confirmation, both planners** (`customer_confirmations`). **Off until Settings →
  *Email customers their visit date* is ticked**, and it stays off until Nik has designed the
  email (2026-10-08). With it on:
  - A customer-facing task's firm start date being set or moved, a pencil task being firmed up, or
    the flag being ticked on a dated task emails the customer once. So does a draft visit that has
    not started having its scheduled date set or moved. Tasks created from a template are skipped
    on insert, because their dates are ERPNext's arithmetic, not a person's decision. **Every visit
    the scheduler drafts with a date, and every customer-facing task Copy week makes, would be
    emailed.**
  - The date is first written as *due* and the send is queued after commit; a sweep every 10
    minutes sends whatever is still due, so a deploy's FLUSHDB delays an email rather than losing
    it. The send locks the row and writes the Email Queue row and the *confirmed for* stamp in one
    transaction, so each document and date is emailed at most once. `keep_stamps` stops a stale
    Desk form from writing the stamps back.
  - The words come from the Email Template **Planner Date Confirmation** (seeded once, never
    overwritten), inside the shared email shell. The recipient is the customer's primary contact,
    then the customer's email, then the project's `custom_customer_email`; the phone number is
    Settings' *Phone number in the email*, else the company's.
  - **Preview** (`preview_customer_confirmation`, System Manager or Projects Manager): the subject,
    HTML and recipient in a sandboxed frame, from the Task form and both planners' dialogs. It
    never sends and works with the switch off.
- `set_task_flags` writes the two checkboxes without moving `modified`, so ticking *Outdoor work*
  never makes an open card or a draft look "changed by someone else". It adds a timeline note but
  no Version row, and is never drafted: the flags are not bookings.

### Quick looks and polish (Phase 6A — TASK-2026-02467)

Nik, 2026-10-09: "make it as user friendly as possible … one feature I want is to be able to click
the Technicians name or something and see what their specific schedule is in a pop up or something
so as not to lose context overall." Everything here applies to **both** planners and opens in the
planner kit's side drawer (below), which leaves the calendar visible and usable behind it: it still
scrolls, and cards still move by drag.

- **Person peek.** Click a name anywhere it appears (Resources available, a crew-view row, a crew
  badge on a card, the route view's title; on the Maintenance Planner also a visit's initials and an
  "off" badge) and the drawer shows that person's week, starting on the site's first weekday: each
  booking (task, visit, rental crew task, travel, driving) with its slot, hours and site; free hours;
  days off as **"Off" / "Holiday" only** (`planner_views.off_label` maps anything else to "Off", so a
  time-off reason can never pass through; since Phase 6D UI an all-day personal block reads
  "Unavailable", the engine's exact word); conflicts; the chosen day's stops in driving order with a
  **Full route** button to `/app/project-planner/route/<resource>/<date>`; group and home team; and
  call / text / email buttons only where a number or address exists. Its own ‹ › step its own week
  and never move the calendar. `get_person_schedule` takes a Planner Resource or a User (the
  Maintenance Planner names people by User) and resolves one to the other through
  `Planner Resource.user`.
- **Day peek.** Click a date (the week and month day numbers, Resources available's day heads, the
  crew view's column heads) to see everyone's day side by side: stops in order with times, drive
  minutes, booked and free hours, off. `get_day_overview` makes **one** engine call for everybody.
  This replaces the day number's old "show this week"; the drawer has an *Open this week* button.
- **Project peek** (Project Planner). Click a project's name on a card, *At a glance* beside the
  project filter, or *Project at a glance* in a task's panel: project, account, PM, status and type;
  every open dated task on a timeline over the project's span (or the next eight weeks when the
  span is longer), the undated ones under it; crew initials, planned against worked hours (Phase 4's
  `get_actuals`), running over and pencil; the labor forecast (Phase 4's `get_labor_forecast`: hours
  for everyone, **money only for `planner_tracking.COST_ROLES`**). Customer jobs only: anything
  `crew_availability.planner_projects` refuses answers "not on the planner". At most 300 tasks.
- **Site peek** (Maintenance Planner). *Site at a glance* in a visit's panel: the site's coming,
  projected (90 days) and recent visits, its default technician and crew, and a link to its
  Maintenance Profile (`get_site_overview`, the Maintenance Planner's own roles). Its access codes
  and safety notes are never in the answer.
- **Edit in a side panel.** A card's editor (the Project Planner's task dialog, the Maintenance
  Planner's visit dialog) opens in the drawer instead of a modal. `planner_kit.panel` hosts the
  **same** `frappe.ui.FieldGroup` the dialog built and offers the Dialog methods the pages call, so
  the field list, `save_dialog`, the reason prompt, the crew editors, equipment, the outdoor and
  customer flags, Phase 4's rows and Phase 5's links all run unchanged (`p6a_dialog` falls back to
  `frappe.ui.Dialog` until the kit has loaded). After every load the open panel is reopened from its
  new card when the task changed (a drag of the same task), so its `modified` lock and its fields
  are never stale; a task or visit no longer on screen (another week) keeps its panel as it was.
- **Drag out of a drawer.** Tasks in a person, day or project drawer (Project Planner) and visits in
  a person, day or site drawer (Maintenance Planner) can be dragged onto a day or a person's row.
  The page binds its own `on_down` to the drawer, and `drag_source` hands a `.pk-drawer` element to
  `p6a_drag_source`, so `plan_drop`, `drop`, the reason prompt and Undo are the page's own. Only what
  the page owns and may move is offered: a task's full planner card comes with the person and day
  answers (`cards`) and as the project answer's tasks, so a task not on the board moves exactly like
  one that is; a visit must be on the calendar.
- **Undo toast.** Every change that goes onto the Undo stack (`push_undo`) shows
  `planner_kit.toast("<what changed>", {action_label: "Undo"})` in place of the green alert, and its
  Undo undoes only the change it announced (the toolbar Undo is still there). A projected visit the
  move drafted gets the toast without Undo, as Undo itself cannot reverse it.
- **Hover glow.** Hovering a person's name lights up every booking of theirs on screen (`pk-glow`); on
  the Project Planner hovering a project's name lights up its cards. The class is generic on purpose:
  6C's click-to-highlight uses it too.
- **Sticky headers.** On a screen wider than 760px the crew view, the week grid and the Resources
  available panel scroll inside themselves (a capped height), so their day header row and the people
  column stay in place; `position: sticky` needs the scroll container to be the element that scrolls,
  and `.pp-scroll` (`overflow-x: auto`) already was one in both directions. A drag near their top or
  bottom edge scrolls them (`p6a_edge_scroll`). Phones keep the page's own scroll.
- **Legend and help.** A "?" toolbar button opens `planner_kit.legend(...)`, which explains every card,
  chip, badge, bar, outline and icon the page draws, using the page's own classes, so the legend shows
  exactly what the calendar shows. Two first-time tips at a time (`planner_kit.hint`).

The page work is in `PP6A_METHODS` / `MP6A_METHODS`, each hooked into the class by one line per
method (`init_phase6a`, `render_phase6a`, `p6a_route` in `go`, `p6a_drag_source`, `p6a_edge_scroll`,
`p6a_undo_toast` in `push_undo`, `p6a_close_toast` in `undo`, and `p6a_dialog` / `p6a_links` /
`p6a_action` in `open_card`); `send` reads `push_undo`'s answer so the alert is not shown twice.
`tests/test_planner_phase6a.py` pins the endpoints (gates, `google=False`, the 31-day cap, user to
resource, no time-off reason, no money without a cost role, internal projects refused) and, under
node, drives the kit's history handling against a fake browser history.

### Planner kit

`public/js/planner_kit/` with the entry `public/js/planner_kit.bundle.js`: the small UI layer both
planner pages share, and that Phases 6B–6D build on. A content-hashed esbuild bundle (raw `/assets`
paths are cached for a year), loaded only by the two planner pages with
`frappe.require("planner_kit.bundle.js", callback)`, and deliberately **not** in `app_include_js`.
It installs `window.planner_kit` (use it through `window.` — eslint does not know the global):

| Part | Use | Notes |
|---|---|---|
| `escape(value)` | The one HTML-escaping helper of the kit's renderers (`& < > " ' \``) | Pages keep their own `pp_esc` / `mp_esc`; `safe_color`, `glow_key(s)` and `format.{hours, drive, day_label, add_days}` beside it |
| `drawer.open({title, subtitle, body, tools, actions, width, key, owner, push, reopen, on_click, on_close})` | The right-side drawer. `body` is an HTML string (already escaped), an Element or jQuery; `actions` are footer buttons `{label, primary, on_click(handle)}`; `push` is an element squeezed by the drawer's width on a wide screen so the calendar is not hidden under it; `on_click(e, handle)` is one delegated listener for whatever the body shows (Enter/Space on a `role="button"` counts); `reopen` is how Forward opens it again | Returns a handle (`set_title`, `set_subtitle`, `set_body`, `set_tools`, `set_actions`, `close`, `is_open`, `body`). A second `open` replaces the first in place. Esc and Back close it. Full width under 768px. No backdrop: the page stays usable. `owner` says which page opened it (the planners share one drawer element) |
| `drawer.route(fn, keep)` / `drawer.navigate(fn)` | A route change of the page's own while a drawer may be open: `route(() => frappe.set_route(...), true)` keeps the drawer open over the new route; `navigate(fn)` closes it first (an "Open project" button) | Either way the kit's history entry is replaced by the route, so Back never lands on a drawer that has gone |
| `panel({title, subtitle, fields, width, key, owner, push, reopen})` | A `frappe.ui.FieldGroup` in the drawer, shaped like a `frappe.ui.Dialog`: `set_primary_action`, `$wrapper`, `show`, `hide`, `get_value(s)`, `set_value(s)`, `fields_dict`, `onhide` | The fields are made at once in a hidden holder, so `$wrapper.find(...)` works before `show()` as it does on a Dialog |
| `toast(message, {action_label, on_action, timeout, tone})` | One small notice at the bottom with one action ("… · Undo"); a new one replaces the old; 8 s by default; stays while hovered | `toast.close()` |
| `menu({anchor, items, title, owner, on_close})` | An anchored menu: `anchor` is an element or `{x, y}` (a right-click); items `{label, on_click, hint, disabled, danger}` or `{divider: true}` | Closes on an item (before running it), Esc, a click elsewhere and Back; arrow keys move between items. Built for 6B's right-click menu |
| `legend(sections, {title, owner})` | The help drawer: sections `{title, note, items: [{sample_html, text}]}`, an item's sample being the page's own markup, or `{swatch: {color, style, fill}}` / `{chip: {text, tone}}` | `text`, titles and chip text are escaped; `sample_html` is trusted, built by the page from fixed strings. `legend_html` is the pure renderer |
| `hint(key, text, {container})` | A first-time tip with a *Got it* button, remembered per user and browser (`pk_hint:<user>:<key>`, every storage access in try/catch) | `hint.seen(key)`, `hint.reset(key?)` |
| `hover_glow(container, "person" \| "project" \| {source, carriers, cls, scope})` | Hovering an element with `data-pk-<kind>="<key>"` adds `pk-glow` to every element whose `data-pk-<kind>s` list holds that key | Keys are written with `glow_key` (encoded: no spaces, no quotes). Returns `{clear, destroy}` |
| `peeks.person({resource \| user, label, start, days, selected, owner, push, can_drag, on_full_route, actions})` and `peeks.day({date, group, owner, push, person_key, only, can_drag, on_person, on_week, head_html, on_head_click, actions})` | The two quick looks both planners share, drawn from `api/planner_views.py` | Return a controller `{refresh, is_open, data}` the page refreshes after a change (the day's also `render()` and `date()`). `can_drag(booking, day, person, data)` decides what the page owns; those rows carry `data-pk-drag="1"` with `data-pk-kind/ref/key/date/resource/user` for the page's drag code (never a drive or a personal block, whatever `can_drag` says). Phase 6D UI's extension points: `actions` are footer buttons (the person's get `{resource, user, date, data}`, the day's the date), `head_html(date, data)` is drawn above everyone's columns and `on_head_click(e, date)` sees a click first (true when it took it) |
| `conflicts.center({planner, owner, push, width, range, can_schedule, draft, block_note, run_fix, open_item, exclude, pick_context, on_list})` | The Conflict center drawer (Phase 6D UI), one per page | `open(focus)`, `schedule()`, `refresh()`, `list()`, `count()`, `index()`, `find(doctype, name, key)`, `is_open()`. Pure renderers beside it: `html`, `picker_html`, `free_people`, `fix_args`, `open_count`, `index_by_ref` |
| `blocks.form({...})`, `blocks.note_form({...})`, `blocks.chip_html(block, ctx)`, `blocks.notes_html(notes, ctx)`, `blocks.note_list_html(notes, ctx)` | Personal blocks and day notes on screen (Phase 6D UI): the two small forms (frappe Dialogs, so the drawer under them stays) and the markup | The note on a chip is only `ctx.note` (the page's `block_notes`); `get_notes(date)` reads one day's notes; `block_text`, `block_window`, `block_slot` |

**How Back closes a drawer without reloading the planner** (`history.js`). While any overlay is
open there is one kit history entry on top, `history.pushState({planner_kit: <id>}, "")` with no
URL, pushed from the click that opened it. frappe v16's router (read from
`git show origin/version-16:frappe/public/js/frappe/router.js`) has one `popstate` listener, added at
Desk boot, that calls `frappe.router.route()`: a re-render that would re-run the planner's
`handle_route` (a reload) and scroll to the top. A browser runs the listeners on `window` in the order
they were added, capture or not (checked in Chromium 152: capture-first holds on an element, not on
`window`), so no listener of the kit's could run first. Instead the kit wraps `frappe.router.route`
once: called during a popstate (`window.event`), it asks the kit first, and for the kit's own
popstates (a Back off its entry on the same route, its own cleanup Back, a Forward onto a drawer that
closed) the router does not route; every other call passes straight through. An overlay closed any
other way steps back off its entry on a timer of 0 with `route_flags.replace_route` set meanwhile, so
`dialog.hide(); frappe.set_route(...)` replaces the entry instead of stacking on it. Where
`window.event` is not set or there is no router to wrap, no entry is pushed (Esc and × still work).
A route change nobody announced (a sidebar link) closes every overlay; an entry it buried is then the
router's, which costs at most one Back that re-renders the same planner.
### Personal blocks, day notes and the Conflict center (Phase 6D — TASK-2026-02470)

The backend half, for both planners; the page UI is the next section, on top of the Phase 6A kit.
Nik's decisions of 2026-10-09.

- **Personal blocks** (`Planner Block`, [`api/planner_blocks.py`](../api/planner_blocks.py)):
  "unavailable 2–4 pm", "shop day". One person, one day (several days away belong in HR time off),
  all day or from–to, with a note.
  - **Who may write.** `crew_availability.SCHEDULER_ROLES` (System Manager, Projects Manager,
    Projects User, Maintenance Supervisor) block anyone. Everyone else with planner access, the
    technicians, creates, edits and deletes only blocks on their own Planner Resource (matched by its
    `user`), an existing block included. They have no Desk permission on the doctype: the API's
    explicit checks are the rule.
  - **Who sees the note.** Others see "Unavailable"; the schedulers and the person see the note. The
    engine reads blocks **without** the note, so no booking, conflict sentence, heatmap, route, digest
    line for someone else or AI answer can carry it. `crew_availability.block_notes(names, viewer)` is
    the one reader, so an endpoint that forgets to call it leaks nothing.
  - **How they count.** An all-day block makes the day like time off: capacity 0, `off`
    "Unavailable", a multi-day task spreads round it, and a firm booking on it is "Booked on a day off
    (Unavailable)". A timed block is a `block` booking whose hours count (never twice where two
    overlap, never past the day's capacity, so a block alone is never "Over by"), and a firm slot
    overlapping it is "Unavailable 2–4 pm (TASK-1)". A pencil never conflicts. A block is not a stop,
    so it adds no driving. Who is free says "Unavailable" or "Unavailable 2–4 pm; only 3h free"; the
    Crew Utilization report takes a timed block off capacity and booked alike, being absence, not work.
  - **Saving never refuses over a conflict.** `save_block` returns the conflicts the block makes with
    firm work already booked (two one-person, one-day engine passes, with and without it) and the
    sentence for the page ("This overlaps Dig at Riverwalk; your PM will see it in the Conflict
    center"). **It writes no timeline comment** on the tasks or visits it overlaps: that would put a
    technician's appointment on a customer job's timeline and go stale when the block moves, and the
    Conflict center is where a PM deals with it.
- **Day notes** (`Planner Day Note`): for everyone or one Planner Resource group, optionally about a
  project, written by the schedulers. Both planners' `get_planner` return every note in range
  (`day_notes`), `block_notes` for the caller and `can_schedule`; `get_my_week` returns the person's
  own `blocks` with their notes and the week's `day_notes` for their group. The Maintenance Planner's
  day cells carry `blocks` beside `items` (an item opens a Task or a trip; a block has nothing to open).
- **Digests.** The combined 6 AM message leads with the day's notes for the person's group, then
  their own blocks with their own note. A day note is worth a message on its own; a person's own
  block never sends one by itself. The `Planner Digest Log` claim is untouched, so it is still at most
  once a day. The maintenance and rental digests, which reach only the people the combined one does
  not cover, add the same lines (`planner_blocks.digest_note_lines`).
- **The Conflict center** ([`api/planner_conflicts.py`](../api/planner_conflicts.py)): one list of
  everything wrong in a range of up to 60 days, from **one** engine pass with Google off that reaches
  30 days past the range: `overbooked`, `overlap`, `day_off` (holidays and time off, never their
  type), `blocked`, `equipment` (Phase 4's rules, through the new structured `equipment_findings`)
  and `qualification` (Phase 3A's). Keys are stable (`kind|date|person or type:name|hash of the
  records`); the dates of task-level conflicts come from whole spans, so they do not move as "today"
  does. An item is `own` only for the calling planner's records (project Tasks; maintenance visits
  and the contract behind the movable projected visit); rental crew tasks follow their booking and
  travel its trip, so they are nobody's here. Fixes, only on owned items, name the **existing**
  endpoint and its arguments, so the reason prompt, drafts, alerts and Undo all apply:
  - *Move to next free day* (`crew_availability.next_free_day`: the person's next day with the hours
    free, skipping days off, blocks and full days, the task's own hours given back, within 30 days):
    `save_task(task, modified, start)` for a one-day task, `move_visit(record, date, modified)` for a
    visit not started, `move_projected(contract, from_date, to_date, serial_no)` for the next projected
    visit. Not offered for a multi-day task, where "the next day with the hours" says nothing about
    where the whole job fits.
  - *Pick someone who's free*: the fix carries the date and hours for `who_is_free`; a person picks,
    then `swap_crew(task, from_resource, to_resource, modified)`, `move_visit(record, technician,
    from_user, modified)` (who-is-free entries now carry `user`) or, for a qualification gap,
    `add_crew(task, resource, modified)`. Nothing is filled in automatically.
  - *Make it pencil* (project tasks only): `save_task(task, modified, tentative=1)`.
  - *Keep it with a reason*: `acknowledge_conflict(key, reason, items, planner, fingerprint)` stores a
    `Planner Conflict Ack` and comments "Kept a conflict on Thu Oct 12: … Reason: …" (escaped) on each
    owned item, after `check_permission("write")` on it. A conflict with nothing of the caller's
    planner in it can be kept only by a scheduler. It hides the conflict only while its fingerprint
    (the records' `modified`, any block's, the wording) still matches; a stale page is refused.
  - `get_next_free_day(resource, hours, after, task)` answers the same question for any task, for
    the Phase 6B "Move to next free day" menu.
- **AI tool** `crew_conflicts` (read-only): the list for a range, without fixes and **without any
  block's note**, even for a caller allowed to read it. Triton needs a tool snapshot refresh.

### On screen: the Conflict center, blocks and day notes (Phase 6D UI — TASK-2026-02470)

The page half of Phase 6D, for **both** planners and My week. The drawer, the two forms and the
markup are the planner kit's (`planner_kit/conflicts.js`, `planner_kit/blocks.js`), so both planners
show the same thing; each page's `PP6D_METHODS` / `MP6D_METHODS` block wires it to its own data and
its own write path. No new endpoint: everything calls the Phase 6D backend above.

- **Conflict center.** A toolbar button **Conflicts (N)** (red above 0; N is what nobody has kept)
  opens a kit drawer with `get_conflicts(start, end, planner)` for the dates on screen, grouped by
  day: each conflict's icon, the person (or vehicle, or task for a qualification), the sentence, the
  block's note when `block_notes` has it, and its records. The page's own record opens in its side
  panel; anything else is a read-only link (*Open in Maintenance Planner* / *Open in Project Planner*
  / *Open trip* / *Open task*). Kept conflicts hide behind **Show kept (N)**, which shows who kept
  each and why. The count is the same call, read once after each load (debounced, `silent`), never on
  a plain re-render; an open drawer reads it again after every change.
- **Fixes run through the page's own write path.** Each button sends exactly the method and arguments
  the server worked out (`fixes_for`), looked up again in the newest list when it runs so `modified`
  is fresh, through the Project Planner's `commit()` (or `send()` for a task not on the board) or the
  Maintenance Planner's `send()` with the same Undo snapshot `save_move` takes. So a fix that makes a
  conflict asks for a reason, Draft mode drafts it (`draft=1` on `save_task`/`add_crew`/`swap_crew`)
  and Undo puts it back. Each page runs only its own writes (`PP6D.fix_methods`: `save_task`,
  `swap_crew`, `add_crew`; `MP6D.fix_methods`: `move_visit`, `move_projected`); the kit never calls a
  fix's method itself.
  - *Move to Thu Oct 15*: one tap. *Make it pencil*: one tap.
  - *Pick someone who's free* opens a section of the same drawer (‹ Back to the conflicts) listing
    `who_is_free` for the fix's dates and hours: free people first with their free hours, busy ones
    greyed with their reason (still choosable: a planner may overbook on purpose, and the reason
    prompt follows), the person it is booked on and anyone already on it disabled, and on the
    Maintenance Planner anyone without a user. **Nothing is selected or applied** until a person is
    tapped; over several days (a qualification gap) a person is free only when free on all of them.
  - *Keep it with a reason* opens a small required reason box; a blank reason never reaches the
    server. A refusal because the conflict changed reloads the list and says "That conflict changed;
    here is the current list". A conflict with nothing of this planner's in it can be kept only by a
    scheduler, so the button is off for anyone else, with the reason as its tooltip.
- **Card markers.** A card in a conflict nobody kept gets its existing red *Conflict* chip (or, for a
  missing qualification, its amber *Missing …* chip; on a visit, its *Day off* chip) turned into the
  way in: it opens the Conflict center at that conflict. A card with no such chip gets one small
  *Conflict* marker. Never two marks for one conflict.
- **Personal blocks on the calendar.** The crew view draws a block in the person's day as a hatched
  *Unavailable 2–4 pm* bar (*Unavailable all day* for an all-day one, whose day already reads
  "Unavailable" rather than "Off" in *Resources available*); week and month draw a chip per blocked
  person (initials and the window in month). The note shows only when the payload's `block_notes` has
  it (`p6d_block_note`, the one reader); everyone else sees "Unavailable". A block is never a card:
  it carries no drag attribute and neither page's drag code can pick it up. Clicking one you may
  edit (a scheduler, or your own) opens the block form with **Delete**; anyone else's says who is
  unavailable when.
- **Blocking time.** The form (person, day, all day or from–to, note) comes from a toolbar **Block
  time** button (schedulers only, `can_schedule`), **Block time…** in a person's drawer (a kit
  extension point, `peeks.person({actions})`) and the right-click menu. A scheduler picks anyone on the
  planner; a technician's form has no person at all and sends none, so the server makes the block
  their own. A note the page could not read is never wiped: the form sends `note` only when it
  changed. Saving never refuses: the server's sentence ("This overlaps Dig at Riverwalk; your PM will
  see it in the Conflict center") shows as a warning toast. Blocks save straight away, even in Draft
  mode.
- **Day notes.** A thin, truncated row under each day's head (week), a row under the crew view's
  column heads and a small 🗒 marker (month), with the full text on hover; a tap opens the day drawer,
  where the 6A day peek now shows the day's notes in full at the top (`peeks.day({head_html})`; a day
  the drawer steps past the dates on screen is read once with `get_day_notes`). The audience shows as
  a tag ("Field"); a linked project opens the 6A project peek (Project Planner). Schedulers add notes
  with **+ note** in the row and **Add a note** in the day drawer, and edit or delete them from the
  drawer.
- **My week** (the phone view): each day lists the person's own blocks with their notes (from
  `get_my_week`, filled through `block_notes` on the server), the day's notes for their group, and a
  **Block my time** button that opens the same form limited to themselves (`self_only`: no person is
  sent).
- **Right-click menu** (6B's registry, `p6_menu_providers`): *Show conflicts* on a card in a conflict,
  *Conflicts this day*, *Add a day note…* (schedulers) and *Block time…* on a cell, *Block time…* on a
  name. Every one also has a way in without the menu.
- The person drawer reads a block day as "Unavailable" (`planner_views.off_label` passes that exact
  engine word; anything else that is not a known label still reads "Off", so no time-off reason can
  ride in on it) and lists a block as *Blocked*, never with its note.

Hooks into the classes, one line each: `init_phase6d`, `render_phase6d`, `p6d_set_mode` (Project
Planner `set_mode`), `p6d_state` (`avail_state`), `p6d_block_html` (`booking_html`), `p6d_day_html`
(`render_calendar`), `p6d_crew_notes_row` (both `render_crew`s), `p6d_my_week_day`
(`render_my_week`), the Maintenance Planner's `off_label` line, `p6d_day_head` and `p6d_cell_blocks`,
and in Phase 6A's blocks `p6d_person_actions`, `p6d_day_peek_opts` and `p6d_legend_sections` (the
legend explains the bar, the chip, the note row and the marker). `tests/test_planner_phase6d_ui.py`
pins it, and runs the kit's renderers and forms under node.

## `hooks.py` touchpoints

- `doc_events`: Project `after_save` → `sync_attachments_from_opportunity`; Project/Task `on_update` → `…project_dashboard.publish_realtime_update`.
- `doc_events["Task"]`: `on_update` → `crew_sync.on_task_update` (crew rows → assignments) and `validate` → `crew_sync.validate_crew` (Project Planner, v1.577.0), then `planner_tracking.validate_equipment` (one row per vehicle/asset, label filled, v1.582.0) and `customer_confirmations.keep_stamps`; `on_update` also runs `customer_confirmations.on_task_update` before `crew_sync`; `before_insert` → `task_templates.copy_template_planning` (v1.583.0).
- `scheduler_events` (every 10 minutes) → `customer_confirmations.send_due_confirmations`, a no-op while the switch is off (v1.583.0).
- `scheduler_events.daily` → `routing.backfill_coordinates` (geocode task and venue addresses the routes need, v1.578.0).
- `scheduler_events.daily` → `send_project_start_reminders`.
- `assistant_tools` → `crew_conflicts.CrewConflicts` (Phase 6D: the Conflict center's list, read-only, never a block's note). Personal blocks and day notes need no hook: the engine reads them, and the planners' reads and the three digests call `api/planner_blocks` directly.
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
