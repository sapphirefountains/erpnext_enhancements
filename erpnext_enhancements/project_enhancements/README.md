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
| `page/contract_builder/`, `contract_builder.py` | **Contract Builder** desk page (v1.611.0): any contract type filled in one step at a time, the steps being the Project Contract's own sections for that type, with the agreement beside the form lighting up the part being filled in (v1.612.0); "With the client" mode has the client initial each clause in the agreement as it is filled in and sign at the end, executed in the builder (`sign_here`); or *Send it to them to sign*. See [Contract Builder](#contract-builder-initials-and-signing-in-person-v16110) | `builder_steps`, `agreement_sections`, `progress`, `get_builder`, `save_builder`, `issue_contract`, `sign_here`, `start_card_on_file`, `get_signing_status`; `ContractBuilder` (JS) | Page; Generate Contract opens new drafts in it |
| `page/project_planner/` | **Project Planner** desk page (v1.577.0): month, week and crew-timeline views of project Tasks, a *Resources available* panel, Needs crew and Unscheduled trays, drag to reschedule or to add a person. Phase 6A (`PP6A_METHODS`): a click on a name, a date or a project opens a side drawer over the calendar, and a card's editor is a side panel. Phase 6C (`PP6C_METHODS`): Color by, highlight a project, saved views, the team strip, tap to move and Print. See [Project Planner](#project-planner-v15770), [Quick looks](#quick-looks-and-polish-phase-6a--task-2026-02467) and [Big picture](#big-picture-tablet-mode-and-print-phase-6c--task-2026-02469) | `ProjectPlanner` (JS) | Page; backend [`api/project_planner.py`](../api/project_planner.py), [`api/planner_views.py`](../api/planner_views.py) and [`api/planner_views_prefs.py`](../api/planner_views_prefs.py) |
| `crew_availability.py` | The availability engine **both planners share**: capacity per Planner Resource per day (work pattern, holidays, approved time off, all-day personal blocks) against everything that uses it (project tasks, rental crew tasks, maintenance visits, travel, timed personal blocks), with conflicts and work-restriction warnings | `availability`, `preview_conflicts`, `block_notes` (the only reader of a block's note, per viewer); pure `pattern_hours`, `day_capacity`, `task_span`, `task_slot`, `allocate_task`, `day_conflicts`, `day_conflict_details`, `equipment_findings`, `block_bookings`, `next_free_day`, `span_for_estimate`, `resolve_crew` | Called by `api/project_planner.py`, `api/maintenance_planner.py`, `api/planner_blocks.py` and `api/planner_conflicts.py` |
| `routing.py` | Daily routes and drive time (v1.578.0): where each booking is (task address → project site; rental venue; visit site), the shop's coordinates (geocoded once, cached in Settings), drive times from **Google Routes** (`computeRouteMatrix`, cached in `Planner Drive Time`) with a straight-line estimate whenever Google is off or refuses, stop ordering (time slots are anchors, the rest by cheapest insertion + 2-opt), arrival times, insertion cost for date suggestions, and a daily coordinate backfill | `plan_routes`, `drive_matrix`, `start_point`, `routes_status`, `backfill_coordinates`; pure `order_stops`, `route_times`, `insertion_cost`, `haversine_km`, `estimate_minutes`, `pair_key` | Called by `crew_availability` and `api/project_planner.py`; `scheduler_events.daily` → `backfill_coordinates` |
| `doctype/planner_drive_time/` | Cache of Google drive times between two points, keyed `lat,lng~lat,lng` (5 decimals; never `<` or `>`, which Frappe refuses in a document name — the original `>` failed every write, v1.578.1). Only Google answers are stored; rows older than 90 days are refreshed lazily | `PlannerDriveTime` | written by `routing.drive_matrix` |
| `planner_notices.py` | Draft-and-publish notices (one per affected person per publish) and the 48-hour change alerts (v1.581.0); both write a bell notification and use the email shell, with SMS through the dispatch digest's helper | `send_publish_notices`, `queue_task_change` (Task `on_update`), `flush_alerts`, `send_change_alerts` | `api/project_planner.publish_drafts`; `doc_events["Task"]["on_update"]` |
| `planner_digest.py` | The combined 6 AM digest (v1.581.0): one message per person with their whole day from the engine, led by the day's notes for their group and their own blocks (Phase 6D) when the person has something booked, and never sending on a note or block alone (Phase 6E); at most once a day by claiming a `Planner Digest Log` row before sending | `send_daily_digests`, `send_preview`, `covered_users` | `scheduler_events.cron` 6 AM; off unless Settings → *One combined morning digest* |
| `planner_tracking.py` | Phase 4 tracking (v1.582.0): actual hours per task and person from the kiosk's Job Intervals, *running over*, the project labor forecast (booked from today + clocked, at each day's pay rate, cost only for `COST_ROLES`) with its nightly refresh of the stored Labor-line figure, and the Task equipment validator | `task_actuals`, `labor_forecast`, `refresh_labor_forecasts`, `can_see_cost`, `validate_equipment` | `api/project_planner` (`get_actuals`, `get_labor_forecast`); `budget_rollup.refresh_labor_forecast`; `scheduler_events.daily_long`; `doc_events["Task"]["validate"]` |
| `doctype/task_equipment/` | Child table on Task (`custom_equipment`): a Fleet Vehicle or an Asset the task uses, with a label filled by `validate_equipment` (one `fetch_from` cannot serve two links) | `TaskEquipment` | child-table controller |
| `report/crew_utilization/` | **Crew Utilization** Script Report (v1.582.0): per person per week or month, capacity vs booked (tasks, visits, rental, travel, driving) vs clocked, with **Other clocked** for time on non-customer work. Hours only, never money; runs the engine with `google=False` | `execute`, `periods_for`, `aggregate` | Drawn as the Project Planner's **Utilization** view (v1.687.0), which reads it through `frappe.desk.query_report.run` and links to it |
| `planner_weather.py` | Phase 5 weather flags (v1.583.0): one Open-Meteo request per load for every uncached site, each point cached 3 hours; flags rain ≥ 60%, a low ≤ 32 °F and wind ≥ 25 mph (imperial units, asked of Open-Meteo directly) on tasks ticked *Outdoor work*. Never fails a read and never logs a request URL | `day_flags`, `forecasts`, `forecast_for_tasks`, `span_weather` | `api/project_planner` (`get_planner`, `get_route`, `suggest_dates`, `who_is_free`) |
| `customer_confirmations.py` | Phase 5 customer date confirmations for **both** planners (v1.583.0), **off until Settings → *Email customers their visit date* is ticked**: triggers on a customer-facing task or a draft visit, the at-most-once send, the 10-minute sweep and the preview | `on_task_update`, `on_visit_update`, `keep_stamps`, `send_one`, `send_due_confirmations`, `preview` | `doc_events` (Task, Sapphire Maintenance Record); `scheduler_events`; `api/project_planner.preview_customer_confirmation` |
| `planner_templates.py` | T3 (v1.608.0): the Build out grid's phase templates and task bundles, pure (no frappe): a Project Template's template Tasks as phases or a bundle (`structure_from_tasks`), and as grid rows with `row:N` dependencies (`template_rows`: in order inside a group, PM aside; 500 and 600 side by side; 800 never); the seed's checks; the checklist as a Quill tick list | `template_rows`, `structure_from_tasks`, `checklist_html`, `seed_problems` | `api/planner_templates.py`, `api/planner_actions.build_out_tasks`, `patches/seed_planner_templates.py` |
| `data/planner_templates.json` | T3 seed: the DOC-0041 phase template (100–700) and six starter bundles, derived by `scripts/derive_planner_templates.py` | — | `patches/seed_planner_templates.py` |
| `task_templates.py` | Phase 5 (v1.583.0): a Task made from a template Task gets the template's hours, crew size, qualifications and the two flags wherever it has none of its own; never overwrites, never copies the crew | `copy_template_planning` | `doc_events["Task"]["before_insert"]` |
| `fountain_move_jobs.py` | Fountain Move jobs (Nik, 2026-10-21): a **Service** project with `custom_is_fountain_move` ticked. A new ticked project gets the **Fountain Move** template when none was chosen (and that template ticks the box; an Opportunity from the /fountain-move intake ticks it and preselects the template in Create Project); its task cards are moved on the **Maintenance Planner** and are read-only on the Project Planner (`mark_read_only`, and `_load` refuses a single-task edit there). Seeds the template (Confirm move date 0 h; Move day 4 h, crew 2, Outdoor; Invoice 0 h) and converts PRJ-00775 | `apply_on_insert`, `insert_defaults`, `opportunity_defaults`, `planner_cards`, `move_task`, `mark_read_only`, `refuse_on_project_planner`, `seed_template`, `make_move_job` | `doc_events["Project"]["before_insert"]`; `api/project_planner` (`get_planner`, `_load`); `api/maintenance_planner` (`get_planner`, `move_fountain_move`); `patches/seed_fountain_move_template.py`, `patches/make_prj_00775_a_move_job.py` |
| `doctype/planner_draft_change/` | One pending change per planner per task (Project Planner) or per maintenance visit (Maintenance Planner, v1.601.0: `visit`, with `task` no longer mandatory and `validate` requiring exactly one) while in draft mode; Publish applies them, Discard drops them, each planner its own, except on a project whose schedule is still Draft, where they are the project's and every scheduler shares them (P3) | `PlannerDraftChange` | `api/project_planner`, `api/maintenance_drafts` |
| `doctype/planner_block/` | Phase 6D: time one person is not available on one day ("unavailable 2–4 pm", "shop day"), all day or from–to, with a private note. `PBLK-.YYYY.-.#####`. Desk: System Manager writes, the schedulers read; every planner write goes through `api/planner_blocks` (own-or-scheduler) | `PlannerBlock` (validates the person is active and the times, warns on a past day; C4: a block with `source` "Google" is read-only to everyone but the import) | read by the engine (never the note) |
| `doctype/planner_day_note/` | Phase 6D: a note on a day for the whole crew or one group ("Shop meeting 7 am"), optionally about a project. `PDN-.YYYY.-.#####`. The schedulers write it | `PlannerDayNote` | `api/planner_blocks`; both planners, My week, the digests |
| `doctype/planner_conflict_ack/` | Phase 6D: a conflict kept on purpose from the Conflict center, with its reason and a fingerprint of what was kept. `PCA-.YYYY.-.#####`, never edited | `PlannerConflictAck` | `api/planner_conflicts.acknowledge_conflict` |
| `doctype/planner_digest_log/` | `user|date` claim (unique) that keeps the combined digest to once per person per day across workers and deploys | `PlannerDigestLog` | `planner_digest` |
| `calendar_sync/` | **Calendar sync** (C2, v1.604.0): committed planner work pushed one way to the shared Google Calendars, with snap-back. `payload.py` (pure: item → event body, ids, hashes, the snap-back table), `sources.py` (ERPNext → items), `google_api.py` (OAuth + Calendar REST), `engine.py` (reconcile, nightly repair, snap-back, preview, health), `triggers.py` (`doc_events`). C4 (v1.606.0): `time_off_rules.py` (pure: who is off and when, from one vacations-calendar event) and `time_off.py` (read it into Unavailable Planner Blocks, the review list, alerts, the dry run). See [Calendar sync](#calendar-sync-c2-v16040) and [Time off in](#time-off-read-in-from-google-c4-v16070) | `engine.reconcile`, `run_item_job`, `run_full_repair`, `nightly_repair`, `snapback`, `plan`, `health`; `triggers.on_change`, `on_contract_change`, `on_project_change`; `time_off.incremental_read`, `nightly_read`, `apply_review`, `preview`; `time_off_rules.classify`, `event_days`, `diff_blocks` | `doc_events` (Task, visit, contract, day note, block, Project); `scheduler_events`; `api/calendar_sync.py` |
| `doctype/calendar_sync_settings/` | Single, System Manager only: the master switch (**off**), the Google account (refresh token in a Password field), the three calendar IDs, the repair window, colors per value stream, and the sync state. Its form has the health panel, *Authorize with Google*, *Pick calendars*, *Preview* and *Re-sync everything* | `CalendarSyncSettings`, `materialize_defaults` | `calendar_sync.engine`; `api/calendar_sync` |
| `calendar_sync/` | **Calendar sync** (C2, v1.604.0): committed planner work pushed one way to the shared Google Calendars, with snap-back. `payload.py` (pure: item → event body, ids, hashes, the snap-back table), `sources.py` (ERPNext → items), `google_api.py` (OAuth + Calendar REST), `engine.py` (reconcile, nightly repair, snap-back, preview, health), `triggers.py` (`doc_events`). See [Calendar sync](#calendar-sync-c2-v16040) | `engine.reconcile`, `run_item_job`, `run_full_repair`, `nightly_repair`, `snapback`, `plan`, `health`; `triggers.on_change`, `on_contract_change`, `on_project_change` | `doc_events` (Task, visit, contract, day note, block, Project); `scheduler_events`; `api/calendar_sync.py` |
| `calendar_sync/timing.py` | **Calendar sync C3** (v1.605.0): which events are timed (a firm single-day route stop in the near window), the lead rule, past days and finished work keeping their stored times, the per-job route cache and the day fan-out. See [Route times and leave-by](#route-times-and-leave-by-c3-v16060) | `attach`, `pick`, `prime`, `load`, `fan_out_days`, `near_window` | `calendar_sync.engine.reconcile`, `run_item_job`, `run_day_job` |
| `route_slots.py` | C3: the route view's own clock times for every stop of a person's day (`route_payload`'s two passes), keyed by stop; leave-by = arrival less the drive. Pure core, one engine call per range | `day_slots`, `stop_key`, `first_slot`, `compute` | `calendar_sync.timing`, `leave_by` |
| `leave_by.py` | C3: the opt-in leave-by ping, every five minutes, **off until Project Planner Settings → *Leave-by pings*** and each person's *Ping me at leave-by time*. Bell, then text, else email; once per person per stop per day (`Planner Ping Log`); no Google request | `send_leave_by_pings`, `select`, `due`, `message` | `scheduler_events` cron `4-59/5` |
| `doctype/planner_ping_log/` | `user|date|stop` claim (unique) that keeps a leave-by ping to once per person per stop per day across workers and deploys | `PlannerPingLog` | `leave_by` |
| `doctype/calendar_sync_settings/` | Single, System Manager only: the master switch (**off**), the Google account (refresh token in a Password field), the three calendar IDs, the repair window, the near window for timed route stops (`timed_days_ahead`, 14), colors per value stream, and the sync state. Its form has the health panel, *Authorize with Google*, *Pick calendars*, *Preview* and *Re-sync everything* | `CalendarSyncSettings`, `materialize_defaults` | `calendar_sync.engine`; `api/calendar_sync` |
| `doctype/calendar_sync_link/` | One Google event ERPNext owns: source (`doctype|name|key`, unique), calendar, event id, content hash, Google etag of our last write, last synced, last error | `CalendarSyncLink` | written only by `calendar_sync.engine` |
| `doctype/calendar_time_off_review/` | C4: an event on the vacations calendar the time-off read could not put on one person. `CTOR-.#####`, one per Google event id (unique). The schedulers pick a person, Everyone or Ignored, optionally for every event with the same title | `CalendarTimeOffReview` (queues `time_off.apply_review` after commit) | made by `calendar_sync.time_off` |
| `doctype/calendar_sync_alert_recipient/` | C4: child of Calendar Sync Settings' *Time off alerts*: a user who gets the time-off bells | `CalendarSyncAlertRecipient` | `calendar_sync.time_off.recipients` |
| `doctype/calendar_sync_color/` | Child of Calendar Sync Settings: a value stream (or Day Note / Unavailable) and its Google `colorId` | `CalendarSyncColor` | `calendar_sync.engine.color_overrides` |
| `calendar_sync/cutover.py` | **Calendar cutover** (C5, v1.607.0): scan the hand-made Maintenance series into a review list, apply approved rows to Maintenance Profiles (append only), end the series with a dry run and a confirm token, undo. See [Cutover](#cutover-from-the-hand-made-maintenance-calendar-c5-v16090) | `scan`, `set_status`, `apply_approved`, `end_series`, `undo_end`; pure `parse_series`, `match_profile`, `rewrite_recurrence` | `api/calendar_cutover.py` |
| `doctype/calendar_cutover_item/` | One hand-made series under review: the proposed profile and why, the Sites links, instructions, codes and address to copy, Pending/Approved/Applied/Ignored, and whether it is ended at cutover. System Manager only; only the scan creates rows. Its list view is the cutover page | `CalendarCutoverItem` | `calendar_sync.cutover` |
| `doctype/calendar_cutover_log/` | Each change to a series' recurrence (End or Undo) with the lines before and after: what Undo restores. Read-only | `CalendarCutoverLog` | written only by `calendar_sync.cutover` |
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
- `api/planner_actions.py` — the Phase 6B writes (duplicate, split, quick add, move several, the Undo of a new task) and the search box of both planners. See [Faster scheduling](#faster-scheduling-phase-6b--task-2026-02468).
- `api/planner_ai_draft.py` — the Build out grid's **AI draft…** (T4, v1.609.0): one POST endpoint, `draft_task_breakdown`, that asks the app's Gemini client for a task breakdown and returns grid rows. It writes nothing and never picks a person or a day. See [AI draft](#ai-draft-a-task-breakdown-for-the-grid-t4-v1609).
- `public/js/planner_kit/` + `public/js/planner_kit.bundle.js` — the planner kit, the small UI layer both planner pages share (drawer, side panel, toast, menu, legend, hints, hover glow, the person and day peeks). Loaded only by the two planner pages. See [Planner kit](#planner-kit).
- `api/planner_views_prefs.py` — Phase 6C: each person's saved views for both planners, in their own `__UserSettings` row, and the print design system's chrome for *Print this view*. See [Big picture](#big-picture-tablet-mode-and-print-phase-6c--task-2026-02469).
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
Agreement** (base rate plus chemicals, emergency rates, a 12% increase at renewal and any
payment method on file since v1.611.0; a card authorization prints as a secure-link
instruction and/or a blank card form — card data never enters ERPNext). Plus the three retained originals (per
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

### Contract Builder, initials and signing in person (v1.611.0)

**Who does what (v1.615.0).** Projects Manager, Sales Manager and Sales User build and save drafts
(create, write) and can *Let a Contract Manager know* (`contract_builder.ask_contract_manager`, a bell
notification to every holder). Only **Contract Manager** (and System Manager) issues, cancels,
amends or deletes a contract, takes or sends for a signature (`esign.api.SEND_ROLES`,
`contract_builder.SIGN_ROLES`), marks one signed on paper (DocPerm: Frappe 16 needs `submit` for an update after submit; plus a
check in `before_update_after_submit` for `submit` granted through a document Share) and edits a Contract
Template's wording. The role and a one-role "Contract Manager" Role Profile are seeded by
`patches/seed_contract_manager_role` (pre_model_sync, grants nothing); a user who already has role
profiles gets the role by adding that profile, since their roles are rebuilt from profiles on save.

Nik, 2026-10-13: every contract generator should be easy to fill out and preview, as a
walkthrough you can do alone or with the client, ending in a full contract signed right there
or sent for the client to sign in their own time.

- **The walk** (`/desk/contract-builder/<contract>/<step>`, each step its own history entry).
  `contract_builder.builder_steps` reads the Project Contract meta and keeps the sections whose
  `depends_on` shows them for this contract's type, so a field added to the doctype appears
  with nothing to change; only the step titles and one line of guidance (`STEP_COPY`) are
  hand-written. Fixed-row tables (the maintenance plan's options, the owner contract's phases)
  show as tick-and-price cards. The page saves onto the real draft on every step change
  (`save_builder` assigns only the fields the steps offer, one by one), and the preview beside
  it is `get_contract_html` over the values on screen. The review step lists every field the
  agreement prints that is still empty, read from the template's own `doc.<field>` references.
  "Generate Contract" now opens the new draft here; the form has *Open in Contract Builder*.
- **The page, v1.612.0.** Nik, on the first version: "the interface is not good … smoother
  transitions and highlighting where they are at in the agreement process as they go through it
  and let them initial and sign as it gets filled out with information." Three panes that stay
  put: the steps (each with what is left to fill, and a progress bar over the fields the
  agreement prints, `contract_builder.progress`), the step's form (it slides between steps; the
  doctype's column breaks are dropped for a responsive grid), and the agreement. The agreement is
  cut into one `<section>` per `<h1>` and the parts the step fills in are lit up and scrolled to
  (`contract_builder.agreement_sections` reads which `<h1>` parts mention each `doc.<field>`, from
  the contract's own template); focusing a field brings its part into view, and a value that
  changes flashes where it lands. The preview re-renders in place, keeping its scroll.
- **With the client** (a toggle in the header, remembered per tab): each initials box in the
  agreement becomes a pad once its clause has no blank left in it, quoting the clause above the
  pad, and moves on to the next box after each set. The review step takes the signature (typed or
  drawn, the name, title, the email the signed copy goes to, the consent and disclosure text from
  Settings), shows it on the signature line as it is captured, and **Sign & execute** calls
  `contract_builder.sign_here`: issue and execute in one transaction through
  `esign.api.sign_in_person`, which opens the in-person request (`open_in_person_request`, shared
  with the tab flow below), checks every set of initials was drawn beside the wording now in the
  frozen snapshot (`initials.stale_initials`, reading the clause text the same way the page does:
  `CLAUSE_BLOCKS` = `CB_BLOCKS`), validates them as the signing page does, locks the contract row and
  runs `lifecycle.execute_contract`. A refusal rolls the issue back and names the clauses to
  initial again. The evidence records `email_confirmed = 0` and no bot check, because neither
  happened: identity is the presenter's, recorded as `presented_by` and printed on the
  certificate. Initials drawn beside a clause that changes afterwards are dropped on the page too.
  After signing, *Save a card on file now* opens the same Stripe enrolment the signing page offers
  (`portal.begin_autopay`, via `start_card_on_file`) on its own tab. Signing here needs only
  e-signing switched on (`can_sign_here`); the public page is needed only to send or hand over a
  link (`can_send`). The preview (only the preview: `_preview_doc` sets
  `flags.contract_preview`) wraps the unsigned signature line in `ct-sig-line`, so a snapshot and
  its drift check are unchanged.
- **Hand them another device** (was *Sign it here now*) issues (submits) the contract, then `esign.api.start_in_person_signing`
  makes a signing link without emailing it and opens `/contract-sign` in a new tab: a tab and
  not a frame, because the page can go on to Stripe to save a card and Stripe refuses frames.
  Everything else is the emailed flow: the client re-types their address, the bot check runs,
  the snapshot is hashed, the signed copy is emailed to them. The request records
  `signing_channel = In Person` and `presented_by`, the certificate prints both, and the link
  lives `IN_PERSON_LINK_HOURS` (12). The builder polls `get_signing_status` and moves to the
  executed copy when it is signed. *Send it to them* is the existing `send_for_signature`; a
  resend always resets the channel to Email Link.
- **Executed copies and template changes.** An e-signed contract prints the copy stored on its
  Contract Signature Request; one marked signed on paper now stores its own (`executed_html`, frozen by
  `freeze_executed_copy` in `on_update_after_submit`). A template patch freezes paper-signed contracts and
  refuses to swap a body while a signing link for that template is out, because the executed copy is
  re-rendered from the live template at signing.
- **Initials** (`esign/initials.py`). A template marks a clause with
  `{{ initial('renewal', 'Automatic renewal and the annual rate increase') }}`. Unsigned, it
  prints a blank box (deterministic, because execution's drift check re-renders and compares).
  The signing page turns each box into its own pad, which starts empty every time; the server
  requires exactly the boxes in the request's stored snapshot and refuses two identical
  drawings ("each initial needs to be done as its own, not copy paste"). The drawings are
  stored on the request (`initials_json`, `initials_count`, both read-only evidence, never
  returned by the assistant tool) and printed in their boxes in the executed copy.
- **The plain-language summary** (`contract_summary.py`, `{{ summary() }}` after each customer
  agreement's title): "What you are agreeing to, in short", built from this contract's own figures, part
  of the signed body, ending with "where the two differ, the agreement governs". Every contract type since v1.613.0
  (Maintenance, Rental and Owner first). Each sentence restates a clause of the shipped template; change both together (a test
  checks the quoted figures against the template text). The signing page scrolls to the next initials
  box after each one, then to the signature.
- **The maintenance agreement's terms** (template + `patches/update_maintenance_agreement_terms`,
  which replaces the live body only when it is exactly the v1.609.0 shipped body):
  Standard Maintenance is the base rate per visit plus chemicals, always billed in addition
  (§2.1, §5.1); As-Needed is a plan option ($180/hr seeded); emergency calls are
  `emergency_rate` ($250) in `business_hours` and `after_hours_emergency_rate` ($350) otherwise,
  minimum `emergency_minimum_hours` (§8.2); every rate rises `renewal_increase_percent` (12%)
  at each automatic renewal (§9.2, §4.5); and any `payment_method` satisfies §4.3 — card, bank
  account (ACH), pay by invoice (`invoice_due_days`) or other — but one is required, and
  Exhibit B prints only the chosen method's authorization. Eight clauses carry initials:
  access (§3.3), payment (§4.3), chemicals (§5.1), emergency rates (§8.2), renewal (§9.2),
  cancellation fee (§10.4), liability (§11) and arbitration (§14). The "save a card" offer
  after signing is withheld from a client who chose to pay by invoice.
- **The rental and owner agreements, the same way** (template +
  `patches/refresh_rental_owner_agreement_layout`, which replaces each live body only when it is
  exactly the v1.611.0 shipped one). Both use the Word-copy markup (`ct-clause` with a hanging
  `ct-num`, bold clause titles, `ct-list`, `ct-option`, `ct-sub`; the owner contract's old `<h2>`
  sub-headings are titled clauses now, one number series). Every typed blank prints a field:
  `agreement_party_name` and `billing_city_state_zip` (both, as maintenance), rental §3.2
  `balance_due_days`; owner `property_type_other`, §2.1 `design_revision_rounds` /
  `additional_revision_rate`, §3.1 and Exhibit A `construction_scope`, and §4.2 the same
  `service_options` rows and `visit_frequency` as the maintenance agreement (seeded for owner
  contracts too; §4.3's annual fee derives from them the same way and never overwrites a typed
  one). What stays blank is completed by hand: the rental Exhibit A condition report and the
  delivery/return inspections. Eight initials each — rental: overholding, late payment, deposit
  deductions, damage, risk, liability, cancellation, arbitration; owner: construction payments,
  change orders, utilities, liability, termination, design license, arbitration, lien rights — and
  the owner contract now has the `sig()` block, so it can be e-signed.
  `tests/test_rental_owner_agreement_layout` asserts every `doc.<field>` the three customer
  agreements print is shown for that type by its own and its section's `depends_on`.
- **The NDA, Architect and Employee-Contractor agreements, the same theme** (v1.613.0; templates +
  `patches/refresh_general_agreement_layout`, which replaces each live body only when it is exactly
  the shipped one). Each opens with the title block, `{{ summary() }}` and an information table, then
  numbered `<h1>` sections of `ct-clause` clauses. The Word numbering lost in conversion is restored
  from the documents' own cross-references (NDA "Paragraph 6"; architect "Sections 5 … and 10"), so
  those references still land; the employee-contractor agreement is numbered in order and its
  expectations are a `ct-list`. New inputs: `nda_entity_type` (the NDA's "a ____ with its principal
  place of business"), and an architect step (`architect_section`) for the notice Attention line,
  Sapphire's contact and email, and the embedded Statement of Work's Architect services, term and
  payment terms; `agreement_party_name`, `billing_city_state_zip` and the Scope of Work step are
  shown for these types too. Initials — NDA (the Company's): obligations, return, remedies,
  attorneys' fees, jurisdiction; architect: reliance on its information, payment, warranty,
  liability, force majeure, venue; employee-contractor: confidentiality, assignment,
  non-solicitation, non-compete. Each now has the `sig()` block, so it can be e-signed.
  `tests/test_general_agreement_layout` pins all of it.
- **The Master Subcontractor Agreement and Statement of Work, the same theme** (v1.613.0; templates
  + `patches/refresh_subcontractor_agreement_layout`, which replaces each live body only when it is
  exactly a shipped one — prod's SOW was still the v1.5.0 body, so this is also what first brings
  v1.461.0's §9.4 hold-point table there). Title block, `{{ summary() }}` (written to the
  subcontractor: "you" is the sub, "we" is Sapphire), an information table, `ct-clause` clauses
  (the MSA's §5.2 coverages a `ct-list`, the SOW's old `<h2>` sub-headings clauses in one series,
  and a new SOW §11 General Provisions holding its Electronic Signatures clause). Every SOW box and
  typed blank prints a `sow_*` field: §1.2/§1.3 project and feature type (details step), §4.1
  schedule days and §4.3 liquidated damages (`sow_section`), §2.1 services (`sow_services_section`),
  §2.3 exclusions and Section 6 Company-furnished items (`scope_section`), Section 3 due dates
  (`sow_deliverables_section`), §5.1 type, §5.3 retention and §5.5 invoice day
  (`sow_compensation_section`), and §8.1 safety, §9.3 correction days, the §9.4 fallback boxes and
  Section 10 special terms (`sow_terms_section`); `agreement_party_name` and
  `billing_city_state_zip` are shown for both. Left blank on purpose: SOW "Accepted By" (signed on
  acceptance), the MSA's Exhibit B guarantee (completed by the guarantor) and Exhibit C risk register
  (kept per job). Initials — MSA: pay if paid, insurance, indemnity, liability and delay, flow-down,
  take-over, arbitration, plus Tier 2's retainage; SOW: liquidated damages (unless N/A), retention,
  corrections, and special terms when there are any. Both have the `sig()` block, so both can be
  e-signed. `tests/test_subcontractor_agreement_layout` pins all of it.

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

**Opened from a Project** (v1.667.0). The Project form's *View > Open in Project Planner*
(`public/js/project_enhancements/project_planner_link.js`) routes to `/desk/project-planner?project=<name>`,
or to `/lookahead/<expected_start_date>` for a project that has not started yet (the week view before P5). The button is only on
an open customer job (a `PLANNER_PROJECT_TYPES` type, not Completed or Canceled) and only for
those whose roles open the page. The planner reads `project` as one of its address filters (below):
after the last-used view is restored, so it wins over that, with the PM filter at All so it cannot
hide that project's cards. It is a one-off: the remembered filters are not saved, and nor is the
"last used" view while the link's filters are on screen. A linked project the planner does not list
stays the filter, and a filtered project with nothing in range or unscheduled is named in a "No
work for … in this range" note over the calendar. (Until v1.676.0 the key was taken out of the
address; it now stays there, as the Project filter.)

**Its filters are in the address** (custom pages S5c, v1.676.0, TASK-2026-02807). The Project,
Project manager and Group filters ride on every planner screen as `?project=`, `?pm=` and
`?group=`, e.g. `/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001&group=Field`, so a copied
link shows what its sender saw and a reload keeps it. The Maintenance Planner does the same for its
Technician (`?technician=`, see the Sapphire Maintenance README). The rules, in the page's "filters
in the address" block:

- `take_route_filters` (from `handle_route`, after `p6c_before_route`) reads the keys from
  `frappe.route_options` and consumes them, so none is written into the next route's address
  (v16.50's `set_route` writes whatever is left there). Keys that are not the planner's are left
  alone. An address that names any filter is the whole filter: one it does not name is All. An
  address that names none (the sidebar, another page's path-only link) keeps the filters on screen,
  and they are written into it. A group that is not one of the four, an overlong value or an object
  is ignored; a PM the loaded planner does not list becomes All, and the address follows.
- **A link's filters are that visit's only** (S5b's rule for the form link, made general).
  `url_filters` holds the keys the address decided. None is saved to the remembered filters, and
  "last used" keeps the person's own value for each.
  - A filter the person picks (`pick_filter`: the three selects, and "Filter to this project" in a
    project's drawer) is theirs and saved as before. Only that one: the link's others stay one-off.
  - A key the loaded planner resets to All stops being the link's.
  - Back onto the person's own filters makes them theirs again.
  - A bare planner address after a link brings back the person's own filters. The planner's own
    next screen (`self_route`) is not such an address, even when it carries no query.
  - Back onto an entry the planner left bare (all All, and the person's own) keeps the newer
    filters on screen.
  - A saved view, used or restored, is theirs too.
- **A pick replaces, a screen pushes.** `pick_filter` rewrites the entry on screen in place
  (`write_filter_url`, `history.replaceState`, the page's one history write). A filter is not a
  screen: a push per pick would make Back step through every select change before it reached the
  week before, and Nik's rule is an entry per screen. The entry keeps saying what it shows, so Back
  onto it, a reload and a copied link all show it again. A week, month, view, heatmap, My week or a
  person's route is a screen and moves by `set_route`, carrying the filters (`filter_options`), so
  Back and Forward walk the screens, each with the filters it had. The write happens only on the
  planner's own path and never over keys the router has put in `route_options` that the page has
  not read yet. `watch_filter_url` re-runs it after a popstate the router does not route (the
  planner kit closing a drawer), so a pick made under a drawer reaches the entry below.
- Guarded by `tests/test_planner_url_filters.py`, which runs `scripts/test_planner_url_filters.js`:
  the real page scripts against a model of v16.50's router (and the router before it).

**Its stylesheet** (custom pages S3.4d, v1.664.0) is `page/project_planner/project_planner.css`,
which Frappe's page loader sends with the page; the script writes no `<style>` element. The file is
on the app's `--sf-*` tokens ([docs/app-design-system.md](../../docs/app-design-system.md)), so it
follows the Desk's light or dark theme, and keeps one section per phase in the order the script used
to inject them. A page's CSS stays in `<head>` for the session, so every selector starts with one of
the page's own families: `pp-`, `p6b-`, `p6e-`, `t1-` (Build out) or `t4-` (AI draft). Two colors
are data rather than design, a task's own and a planner resource's: the script hands them over as
`style="--pp-color: ..."` and the CSS paints with `var(--pp-color)`; Color by sets `--pp-p6c-c`. The
route map's markers and line take color values, so `PP_ROUTE_COLORS()` reads the same tokens the
stop list paints. `tests/test_s3_page_css.py` holds all of it.

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
- **Distances are shown in miles** (Nik, 2026-10-09, v1.589.0), measured in km underneath: routing, the
  drive-time cache and the straight-line estimate all keep km, and only what people read converts —
  the route view's legs and total (`pp_mi`), the date suggestions' "2 mi away" (`api/project_planner.fmt_miles`),
  and `get_route`'s `miles` beside each `km` for the `crew_day_route` AI tool. The kiosk's distance to a
  site and the location timeline show feet or miles and mph the same way.
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
  The line on the map follows the roads (v1.600.1): the browser asks Google on the map's own key,
  through the Routes library when Travel Settings → *Use Routes API* is on, else the Directions
  service, and only if both fail joins the stops with straight segments and says so. The stops keep
  the planner's order, and every time and mile stays the server's.
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
  "Starts before …" line under *Dependencies* in the reason prompt. Since P4 a move, later or
  earlier, takes the tasks that follow along by the same number of **working** days in the same
  request (`dependents=1`; Ctrl/Cmd moves one task), which replaced the offer to shift them
  (`shift_successors`, still an endpoint). See
  [P4](#tasks-that-follow-draft-schedules-and-notify-crew-p4-planner-ux-redesign).
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
    its own `try`) **and nightly** (`planner_tracking.refresh_labor_forecasts`, `scheduler_events`
    `daily_long`; Nik, 2026-10-09), so it no longer goes stale as bookings and clock-ins change; the
    planner's strip always reads it live. The nightly job takes every Project that is not closed
    (`crew_availability.CLOSED_PROJECT_STATUSES`) and has a Labor budget line, computes the forecast
    with cost (`google=False`: no Google calls) and writes it with `frappe.db.set_value` on the child
    row, `update_modified=False`, and only when the figure changed. It never saves the Project (that
    would fire every Project hook and bump `modified` nightly). One project at a time, each in its
    own `try` (a failure is logged with `frappe.log_error` and the rest continue) and committed as
    it goes; capped at 500 projects and 20 minutes, newest activity first, with a one-line summary
    in the `planner_tracking` log. A no-op while the Project Budget Line table or its
    `labor_forecast` column does not exist.
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
  (`custom_outdoor`) carries `weather`, the days of its span with rain ≥ 60%, a low ≤ 32 °F or wind
  ≥ 25 mph. Units are imperial (Nik, 2026-10-09: "keep the weather thresholds but use imperial"):
  the request asks Open-Meteo for `temperature_unit=fahrenheit` and `wind_speed_unit=mph`, nothing
  is converted, and the chips read "Freezing 28 °F" and "Wind 28 mph". The thresholds are the same
  lines as before (32 °F is freezing; 25 mph is the old 40 kilometres an hour, rounded). Each
  point's three-hour cache key carries the unit (`CACHE_PREFIX`), so an answer cached under metric
  units is never read back as imperial. `[]` means the forecast is clear and `null` means there is none, which the page draws
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
  exactly what the calendar shows. Its first section is the first-time tips (since v1.687.0; they
  were banners under the toolbar on load, two at a time, through `planner_kit.hint`).

The page work is in `PP6A_METHODS` / `MP6A_METHODS`, each hooked into the class by one line per
method (`init_phase6a`, `render_phase6a`, `p6a_route` in `go`, `p6a_drag_source`, `p6a_edge_scroll`,
`p6a_undo_toast` in `push_undo`, `p6a_close_toast` in `undo`, and `p6a_dialog` / `p6a_links` /
`p6a_action` in `open_card`); `send` reads `push_undo`'s answer so the alert is not shown twice.
`tests/test_planner_phase6a.py` pins the endpoints (gates, `google=False`, the 31-day cap, user to
resource, no time-off reason, no money without a cost role, internal projects refused) and, under
node, drives the kit's history handling against a fake browser history.

### The toolbar (v1.687.0, v2)

Nik picked the redesign from a mockup on 2026-10-14. Every phase had appended its own buttons to the
bar (6A's help, 6B's search, 6C's Color by, Tap to move, Views and Print, 3B's Draft mode, My week and
crew sheet, 6D's Conflicts and Block time, Phase 4's Running over and Utilization, Copy week, Undo),
so it had grown into one long flat row. v1.687.0 made it **one bar** with four menus
(`planner_kit.toolbar.menu`). **Toolbar v2** (P1 of the planner UX redesign, `docs/planner-ux-redesign.md`,
approved 2026-10-21) folds those four into two, and gives both planners the same structure:

```
‹ Today ›  title   [Month] Week Crew Heatmap Utilization   search  Project  PM  Group   [alert chips]  + Add ▾  More ▾  ↶  ?  ↻
```

- **The view buttons are a tablist** (`planner_kit.toolbar.tabs`): `role="tab"`, `aria-selected`, a
  roving tabindex, ArrowLeft/Right/Home/End move focus (choosing one is a route change with its own
  history entry, so Enter does it). Later PRs of the redesign swap the list for Lookahead, Gantt,
  Crew, Calendar and Overview; the control stays.
- **+ Add ▾** holds *Quick add task…* (the double-click's dialog, opened on today or the first day on
  screen), *Build out tasks…* (T1's grid; for the project in the filter, else a Project is asked for
  first) and *Day note…* (6D). The Maintenance Planner's holds *Add visit…* and *Day note…*.
- **More ▾** holds four sections, built by `tb_more_sections()` and flattened by
  `planner_kit.toolbar.sections_to_items` (a section with nothing on the screen is left out with its
  heading):
  **Display** (*Color by*, whose default is now **Job type**, and the key of its colors; *Highlight a
  project…*; *Maintenance, rentals, travel*), **Views** (*My week*, the saved views, *Save current view
  as…*, *Manage views…*), **Tools** (*Copy week…* on week and crew only, *Block time…*, *Conflicts…* at
  any count, *Tap to move*, *Hold all my changes*, which is the old board-wide Draft mode, and *Undo*) and
  **Print** (*Print this view*, *selection only*, *Print crew sheet*).
- Each menu's items are built when it opens, from the page's own state, through the methods the
  phases already had, and an item whose control was hidden on a screen (Copy week in the month view,
  Block time for someone who cannot schedule) is left out there.
- **Gone from the bar and kept elsewhere (D12):** *Split* is in the right-click menu and the task
  panel only; the *Move by* switch (calendar or working days) is the first field of the **Move…**
  panel on the selection bar, and a drag still counts in the unit it picks; Color by, the saved views,
  Copy week and Print are in More.
- **Job type is the default color.** The stored value `default` is the old *Task color*, which every
  "last used" view carried whether or not anyone picked it, so a view now carries `color_chosen` (a bool in
  `api/planner_views_prefs.SCHEMA["project"]`, on the server, so it follows the person to any device) and the
  planner honors a saved `default` only when it is set.

| Old control | Project Planner now | Maintenance Planner now |
|---|---|---|
| ‹ Today ›, title, view switcher, search, filters, ?, ↻ | in the bar (Utilization joins the switcher) | in the bar |
| Views (saved views) | **More ▸ Views** with *My week*, *Save current view as…*, *Manage views…* | **More ▸ Views** |
| My week | More ▸ Views | (none) |
| Color by, and its key row | **More ▸ Display**: radio items, then the key (a project in it is a switch for its highlight) | More ▸ Display, likewise (a site) |
| Highlight a project | More ▸ Display (*Highlight a project…*, or *Highlighting X* to switch it off), a project's name on a card, the project drawer, Esc | a site's panel, the site drawer, the key |
| Maintenance, rentals, travel / Projected visits, Project work | More ▸ Display (check marks) | More ▸ Display |
| The visit-kind legend row | | More ▸ Display (*Visit kinds*) and "?" |
| Draft mode, Tap to move | **More ▸ Tools** (*Hold all my changes*, *Tap to move*; check marks) | More ▸ Tools (*Draft mode*, *Tap to move*) |
| Block time, Copy week | More ▸ Tools (Copy week on week and crew only) | More ▸ Tools (*Block time…*) |
| Quick add, Build out, Day note / Add visit | **+ Add** | **+ Add** (*Add visit…*, *Day note…*) |
| Conflicts (N) | a **danger chip** above 0, and *Conflicts…* in Tools at any count | the same |
| Running over (N) | a **warn chip** while above 0 or while it filters | (visits have no planned-vs-clocked hours) |
| Overdue | a **danger chip** opening the Overdue tray | a **danger chip** opening a side panel listing the overdue visits on screen |
| Undo | More ▸ Tools, and a small ↶ icon while there is something to undo | the same |
| Print, Print crew sheet | **More ▸ Print** | More ▸ Print (*Print this view*, *selection only*, *Day Board*) |
| Move by (calendar or working days) | the Move… panel's first field | the same |
| Utilization (button to the report) | the fifth **view**, `/project-planner/utilization/<date>` | (none: the Project Planner's view counts visits already) |
| First-time tip banners | the first section of "?" | the same |
| Draft mode's bottom bar | a strip **under the toolbar**: "Holding your changes: N not published yet", Publish, Discard; since P4 also a line per project with drafts (*Publish…* on a Draft schedule) | "Draft mode on: N changes not published yet", Publish, Discard |

**On a phone** (767px and under) the bar is ‹ Today ›, the title, the view switcher and **Filters and
tools** (with the alerts' total), which opens `planner_kit.toolbar.sheet`: the filters and search
(moved in from the bar, and back when it closes), the alert chips, + Add and More's four sections as sheet sections, and the
help, refresh (and the Day Board, on the Maintenance Planner). It takes the kit's one history entry,
so the phone's Back closes it.

**Utilization** reads the Crew Utilization report itself (`frappe.desk.query_report.run`, so the
report's roles decide who sees it and the figures always match): the month holding the date, a row
per person per week, with capacity, booked and clocked hours and the two percentages, a week past
capacity carrying an *Over* chip and one at 75% or more *Near full*. ‹ › step a month. Group narrows
it; Project and Project manager do not apply to a person's week, so they are hidden there and kept in
the address for the next calendar view. *Open the full report* opens the report on the same month and
group. It is not a saved-view view (`planner_views_prefs.SCHEMA` is unchanged), so "last used" keeps
the calendar view before it.

The page side is `PPTB_METHODS` / `MPTB_METHODS` and `PPU_METHODS`; `tests/test_planner_toolbar.py`
runs `scripts/test_planner_toolbar.js` (the kit module on a fake DOM and a fake browser, and both
pages' blocks with an explicit list of every old control) and `scripts/test_planner_url_filters.js`
covers the Utilization view's address.

### Planner kit

`public/js/planner_kit/` with the entry `public/js/planner_kit.bundle.js`: the small UI layer both
planner pages share, and that Phases 6B–6D build on. A content-hashed esbuild bundle (raw `/assets`
paths are cached for a year), loaded only by the two planner pages with
`frappe.require("planner_kit.bundle.js", callback)`, and deliberately **not** in `app_include_js`.
Its stylesheet is a bundle of its own, `public/css/planner_kit.bundle.css` (custom pages S3.4e,
v1.673.0), on the `--sf-*` tokens, which each planner loads in the same require as the kit
(`PP6A.kit` and `MP6A.kit` list both bundles): its `<link>` lands after the page's own stylesheet,
so the kit's rules still come after the page's, as the injected text did, and the callback waits
for it, so nothing the kit draws flashes unstyled. **Each page asks for it once**, through
`p6a_require` (custom pages S5c, v1.676.0), which 6A's and 6D's start-up both call: v16.50's
`frappe.require` counts a file as loaded only once it has finished loading (`assets.js` pushes to
`_executed` after `onload`), so the two requires the constructor used to make in one tick each
appended a `<script>` and a `<link>`, and the kit's stylesheet was on the page twice. The request is
kept on `window.planner_kit_request`, one per Desk, shared by both planners, so opening the other
planner while it is on its way does not ask again either. It installs `window.planner_kit` (use it through `window.` — eslint does not know the global):

| Part | Use | Notes |
|---|---|---|
| `escape(value)` | The one HTML-escaping helper of the kit's renderers (`& < > " ' \``); it is sf_kit's `esc` (`public/js/sf_kit/esc.js`), re-exported | Pages keep their own `pp_esc` / `mp_esc`; `safe_color`, `glow_key(s)` and `format.{hours, drive, day_label, add_days}` beside it |
| `drawer.open({title, subtitle, body, tools, actions, width, key, owner, push, reopen, on_click, on_close})` | The right-side drawer. `body` is an HTML string (already escaped), an Element or jQuery; `actions` are footer buttons `{label, primary, on_click(handle)}`; `push` is an element squeezed by the drawer's width on a wide screen so the calendar is not hidden under it; `on_click(e, handle)` is one delegated listener for whatever the body shows (Enter/Space on a `role="button"` counts); `reopen` is how Forward opens it again | Returns a handle (`set_title`, `set_subtitle`, `set_body`, `set_tools`, `set_actions`, `close`, `is_open`, `body`). A second `open` replaces the first in place. Esc and Back close it. Full width under 768px. No backdrop: the page stays usable. `owner` says which page opened it (the planners share one drawer element) |
| `drawer.route(fn, keep)` / `drawer.navigate(fn)` | A route change of the page's own while a drawer may be open: `route(() => frappe.set_route(...), true)` keeps the drawer open over the new route; `navigate(fn)` closes it first (an "Open project" button) | Either way the kit's history entry is replaced by the route, so Back never lands on a drawer that has gone |
| `panel({title, subtitle, fields, width, key, owner, push, reopen})` | A `frappe.ui.FieldGroup` in the drawer, shaped like a `frappe.ui.Dialog`: `set_primary_action`, `$wrapper`, `show`, `hide`, `get_value(s)`, `set_value(s)`, `fields_dict`, `onhide` | The fields are made at once in a hidden holder, so `$wrapper.find(...)` works before `show()` as it does on a Dialog |
| `toast(message, {action_label, on_action, timeout, tone})` | One small notice at the bottom with one action ("… · Undo"); a new one replaces the old; 8 s by default; stays while hovered. It is sf_kit's toast (`public/js/sf_kit/toast.js`) with the `pk-` class prefix, markup unchanged | `toast.close()` |
| `menu({anchor, items, title, owner, on_close})` | An anchored menu: `anchor` is an element or `{x, y}` (a right-click); items `{label, on_click, hint, disabled, danger}` or `{divider: true}` | Closes on an item (before running it), Esc, a click elsewhere and Back; arrow keys move between items. Built for 6B's right-click menu |
| `legend(sections, {title, owner})` | The help drawer: sections `{title, note, items: [{sample_html, text}]}`, an item's sample being the page's own markup, or `{swatch: {color, style, fill}}` / `{chip: {text, tone}}` | `text`, titles and chip text are escaped; `sample_html` is trusted, built by the page from fixed strings. `legend_html` is the pure renderer |
| `hint(key, text, {container})` | A first-time tip with a *Got it* button, remembered per user and browser (`pk_hint:<user>:<key>`, every storage access in try/catch) | `hint.seen(key)`, `hint.reset(key?)` |
| `hover_glow(container, "person" \| "project" \| {source, carriers, cls, scope})` | Hovering an element with `data-pk-<kind>="<key>"` adds `pk-glow` to every element whose `data-pk-<kind>s` list holds that key | Keys are written with `glow_key` (encoded: no spaces, no quotes). Returns `{clear, destroy}` |
| `peeks.person({resource \| user, label, start, days, selected, owner, push, can_drag, on_full_route, actions})` and `peeks.day({date, group, owner, push, person_key, only, can_drag, on_person, on_week, head_html, on_head_click, actions})` | The two quick looks both planners share, drawn from `api/planner_views.py` | Return a controller `{refresh, is_open, data}` the page refreshes after a change (the day's also `render()` and `date()`). `can_drag(booking, day, person, data)` decides what the page owns; those rows carry `data-pk-drag="1"` with `data-pk-kind/ref/key/date/resource/user` for the page's drag code (never a drive or a personal block, whatever `can_drag` says). Phase 6D UI's extension points: `actions` are footer buttons (the person's get `{resource, user, date, data}`, the day's the date), `head_html(date, data)` is drawn above everyone's columns and `on_head_click(e, date)` sees a click first (true when it took it) |
| `conflicts.center({planner, owner, push, width, range, can_schedule, draft, block_note, run_fix, open_item, exclude, pick_context, on_list})` | The Conflict center drawer (Phase 6D UI), one per page | `open(focus)`, `schedule()`, `refresh()`, `list()`, `count()`, `index()`, `find(doctype, name, key)`, `is_open()`. Pure renderers beside it: `html`, `picker_html`, `free_people`, `fix_args`, `open_count`, `index_by_ref` |
| `blocks.form({...})`, `blocks.note_form({...})`, `blocks.chip_html(block, ctx)`, `blocks.notes_html(notes, ctx)`, `blocks.note_list_html(notes, ctx)` | Personal blocks and day notes on screen (Phase 6D UI): the two small forms (frappe Dialogs, so the drawer under them stays) and the markup | The note on a chip is only `ctx.note` (the page's `block_notes`); `get_notes(date)` reads one day's notes; `block_text`, `block_window`, `block_slot` |
| `toolbar.menu(button, {label, items, on_close})` | The toolbar's menu buttons (v1.687.0): `items()` is called each time the menu opens and returns `{label, on_click, hint, title, disabled}`, `{label, checked}` (a `menuitemcheckbox`), `{label, radio: true, checked}` (a `menuitemradio`), `{label, info: true, sample_html}` (read-only, a key entry), `{heading}` or `{divider: true}` | The WAI-ARIA menu button: `aria-haspopup` / `aria-expanded` / `aria-controls` on the button, `aria-checked` and a check mark on a switch; arrow keys wrap, Home/End jump, Esc closes and gives focus back, Tab and a click elsewhere close it, an item closes it before it runs. **No history entry** (unlike `menu`, which is an overlay of `history.js`). Its list carries `pk-menu`, so the planners' shortcut handlers leave its keys alone. Returns `{open, close, is_open, element, items}` |
| `toolbar.sheet({title, sections, top, on_close, return_focus, max_width})` | The phone's "Filters and tools" bottom sheet: `top(slot)` lets the page move its own fields in (and `on_close` back), `sections()` are `{title, items}` with the same item shapes as a menu | A modal dialog and an overlay of `history.js`: it takes the kit's one history entry, Back and Esc close it, an item closes it first (its entry is handed to a drawer the item opens), an item with `keep_open` (a switch) redraws it in place, and it closes past `max_width` |
| `big_picture` | Phase 6C's pure half: `hash_color(key)` (FNV-1a over UTF-16 code units into the 12-color `HASH_PALETTE`), the fixed palettes `JOB_TYPES` / `TASK_STATUSES` / `VISIT_STATUSES` with `job_type`, `task_status`, `visit_status`, `palette_color`, `palette_legend`, `safe_paint` (a hex or a page's `hsl()` person color, else the fallback), `team_day(cells)` / `team_text` / `team_tip` (the capacity strip), `pick_view(state, schema)` (a saved view's keys), `print_document(model)` (a whole printable HTML document from a page's model, every value escaped) | No DOM, no frappe: tests run it under node. A page with an older kit already loaded (no `big_picture`) simply shows none of Phase 6C's colors, strip or print |

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
  their own blocks with their own note. **Neither a note nor a block sends a message by itself**
  (Phase 6E, Nik, 2026-10-09: a note is included only if the person gets a text anyway; Phase 6D had
  made a day note alone message everyone it applied to): a person with nothing booked gets nothing and
  no claim is made. The `Planner Digest Log` claim is untouched, so it is still at most once a day. The
  maintenance and rental digests, which reach only the people the combined one does not cover, add the
  same lines (`planner_blocks.digest_note_lines`) inside the message they were already sending for a
  visit or a job.
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

### Faster scheduling (Phase 6B — TASK-2026-02468)

Nik picked four things on 2026-10-09: a right-click menu, drag-to-resize with double-click quick add,
moving several tasks at once, and search with keyboard shortcuts. The Maintenance Planner gets what
applies to visits (see its README). Two rules run through all of it: **nobody is picked for anyone**
("suggest a crew" was declined: Assign to… lists who is free and the planner clicks one), and **every
write goes through the page's own `send()`**, so the reason prompt, draft mode and Undo apply as they
do to a drag.

- **Right-click menu.** On a card, an empty spot of a person's day (or a day) and a person's name, with
  `planner_kit.menu`; on a touch screen a press of about half a second that does not move (the card
  lifts at 0.3 s as before, and is only dropped back where it was). On a task card: *Edit* (the 6A side
  panel), *Assign to…*, *Move to next free day*, *Pencil* or *Firm up*, *Duplicate*, *Duplicate to…*,
  *Split across days…* (one-day task: *Split in two…*), *Project at a glance*, *Open project*. On an empty
  spot: *Add task here…* and *Everyone's day*. On a name: *See their week*.
  - *Assign to…* opens a drawer listing who has the hours free on that day (`who_is_free`, the hours
    being the task's estimate shared by the crew over its working days, a full day with no estimate)
    and who does not, with the reason. Click one to add them (`add_crew`), or pick "Replace <person>"
    first (`swap_crew`). Someone who is not free can still be booked; the reason prompt asks.
  - *Move to next free day* asks Phase 6D's `planner_conflicts.get_next_free_day` for the crew-view row
    the card sits in, else the lead, else the only person, as the menu opens; the hint fills in with
    the day ("Thu, Oct 15"), and the item is disabled with the reason when there is no crew, several
    people and no lead, or nothing free within 30 days. It saves `save_task(start=)`, so a multi-day
    task keeps its length and a conflict still asks.
  - *Duplicate* (same days) and *Duplicate to…* call `duplicate_task`, which copies with Copy week's own
    `_copy_values`: crew, hours, crew size, qualifications, pencil flag, location, equipment, the two
    Phase 5 flags. *Split* calls `split_task`: the task keeps the days before the chosen day and a new
    task gets the rest, `depends_on` the first (and, since Phase 6E, every open same-project task that
    waited on the original now waits on this one), hours (and each crew member's own hours) shared by
    working days, a note on both timelines, all or nothing in a savepoint. A one-day task splits into
    two halves; the second goes on the person's next free day unless the planner picks another. The
    split refuses a rental crew task and a task with time on a timesheet or clocked at the kiosk.
  - Other phases add items without touching this code: each page has `this.p6_menu_providers`, an array
    of `function (target) { return [menu items] }`; `target` is `{kind: "card", card, ymd, resource, el}`,
    `{kind: "cell", resource, user, ymd, el}` or `{kind: "person", resource, user, el}`. Each provider's
    items come after the page's own, a divider before each group; a provider that throws is skipped.
- **Resize.** Week view: a grip on the left edge of a multi-day task's first day and on the right edge
  of its last day. Crew view: on every card's first and last day, one-day cards included. Dragging one
  moves that day by whole days (never past the other edge: one day at least), with the new span lit up
  in the card's row and a label by the pointer; on release `save_task(start, end)` through `commit`, so
  overbooking asks and Undo restores the old span. Pointer events only; `drag_source` ignores a grip, so
  a resize never starts a move. No grips on a rental crew task, a time-slot task (its slot decides its
  day), in the month view or, on a phone, in the stacked week view.
- **Quick add.** Double-click an empty spot (or *Add task here…*): a side panel with the project (the
  planner's own list of Active customer jobs), subject, hours, person (the row's, in the crew view),
  date and pencil. `quick_add_task` creates it as the user (`check_permission("create")`), books only the
  person named, spreads more hours than a day holds over the next weekdays, and answers `needs_reason`
  when it overbooks them. The new card flashes; Undo deletes it again.
- **Several at once.** Shift-, Ctrl- or ⌘-click cards to select them (`this.p6_selection`, a Set of task
  names; `$(document).trigger("p6-selection-changed", [page])` on every change, which 6C's print reads).
  Selected cards get a purple outline and a bar offers *Move…*, *Pencil* (or *Firm up*) and *Clear*; Esc
  or a click on empty space clears it. Drag any selected card and they all move by **the same number of
  calendar days**, each keeping its length: the card in hand lands exactly where it is dropped, as a
  single drag does, and a weekend is not skipped (working days would land the others somewhere the
  drag does not show; since Phase 6E the *Move by: Calendar days | Working days* choice (a field of the Move… panel since toolbar v2) counts
  working days instead). A drop on another person's row is refused while several are selected (hand one
  over with the selection cleared). `move_many` checks the lot together (`preview_batch`, as
  `shift_successors` and `copy_week` do), asks for one reason, saves all or nothing, furthest first when
  moving later so ERPNext's own `reschedule_dependent_tasks` finds nothing to push, and is one Undo.
- **Undo of a new task.** A duplicate, a quick add or a split's second half is undone with
  `remove_created_task`: it deletes the task (`frappe.delete_doc`, never `force`, delete permission, so it
  lands in Deleted Documents) only when its creator undoes it, it is unchanged, and nothing was attached
  since: no timesheet, no clocked time, no sub-task, no task depending on it, no comment by anyone else,
  no file, no draft change. Otherwise it refuses with the reason. A split's Undo also puts the first half
  back (its old end, hours and crew) through `save_task`'s own path, and (Phase 6E) the tasks the split
  had moved onto the second half back onto the first.
- **Draft mode.** Duplicate, quick add and the Build out grid are drafted since v1.603.0 (T2, below);
  only a **split** is refused while Draft mode is on, because it changes a task that exists and creates
  another at once (the page refuses before it asks, and the server refuses too); `move_many` drafts each
  move properly.
- **Search.** A box in the toolbar (`/` jumps to it): tasks (subject, name), projects (name, title) and
  people on screen first, then `search_planner` for tasks outside the range (customer jobs and open work
  only, nearest the week on screen first, at most 20). Arrow keys and Enter pick. A task goes to its
  week through `go`, a real route, and its card flashes; a project opens the 6A project drawer, a person
  their week.
- **Shortcuts**, only while focus is not in a box and no dialog is open: `T` today, `←` `→` the previous
  and next period, `1` `2` `3` week, month and crew, `Ctrl`/`⌘`+`Z` Undo, `?` the legend, `/` search, `Esc`
  close a menu or drawer (the kit's) or clear the selection. One `keydown` listener on the document in
  the capture phase, added when the page shows and removed when it hides. `frappe.ui.keys` did not fit:
  frappe binds `Shift+/` globally to its own shortcut list, and both would open. `Ctrl+S`, `Ctrl+K`,
  `Ctrl+G` and `Shift+T` (frappe's console) are left alone. The legend has a *Keyboard* section.
- **Registries for the other phases** (all created by whichever init runs first): `p6_menu_providers`
  (above), `p6_legend_providers` (functions returning legend sections, appended after 6B's own),
  `p6_send_modules` (`{method: python module}`: `send()` calls `<module>.<method>` instead of
  `project_planner.<method>`, which is how the `planner_actions` writes reach the reason prompt, drafts
  and Undo) and `p6_selection`.

The page work is in `PP6B_METHODS` / `MP6B_METHODS`, hooked in by one line each: `init_phase6b`
(constructor), `render_phase6b` (render), `p6b_lift` (lift), `p6b_drop` (drop), `p6b_undo` (undo),
`p6b_legend_sections` (`p6a_legend`, whose section list was not extensible) and, on the Project Planner,
the grip check in `drag_source` and the module lookup in `send`. The pure helpers (the day-offset math,
the shortcut table, the selection set, the menu's provider groups, local search) are `PP6B_PURE` /
`MP6B_PURE`; `tests/test_planner_phase6b.py` runs both through the same cases under node, checks the
split preview against the server's weights, and pins the endpoints (gates, `needs_reason`, all or
nothing, the split's refusals, pro-rata hours and `depends_on`, the Undo's checks, customer jobs only in
search).

Things that look like bugs and are not:

- The split's second task has the same subject as the first; the timeline notes and `depends_on` say
  which is which.
- A one-day task with no estimate books a full day; split in two, each half gets half of Settings'
  full-day hours per person, written as an estimate, so the two halves do not book two full days.
- Since Phase 6E, tasks that depended on the original wait on the second half after a split (see
  [Follow-ups](#follow-ups-phase-6e--task-2026-02471)); before that they stayed on the first.
### Big picture, tablet mode and print (Phase 6C — TASK-2026-02469)

Nik picked all six on 2026-10-09, for **both** planners. The page work is `PP6C_METHODS` /
`MP6C_METHODS`, the pure half is the kit's `big_picture`, and the one backend module is
[`api/planner_views_prefs.py`](../api/planner_views_prefs.py).

- **Color by** (More ▸ Display; the default is Job type since toolbar v2). The Project Planner colors each card's left edge by *Task color* (the
  default, today's look: the task's own color), *Person* (the lead, or in the crew view the row's
  person, in the same color as their dot and badges), *Job type*, *Project*, *Project manager* or
  *Status*; the Maintenance Planner by *Visit type* (the default, today's colors), *Technician*, *Site*,
  *Visit status* or *Contract* (read-only project, rental and travel bookings keep their colors except
  under Technician). Fixed palettes, mid-tones that read on light and dark cards:
  - job type: Design `#9333ea`, Build `#ea580c`, Service `#0891b2`, Events (Rent projects and rental
    crew tasks) `#db2777`, Delivery `#65a30d`, Other (a blank type that counts through a value stream)
    `#64748b`;
  - task status: Open `#2563eb`, Working `#16a34a`, Pending review `#ca8a04`, Overdue (and any overdue
    card) `#dc2626`; visit status: Scheduled `#2563eb`, Next visit `#4f46e5`, Projected `#94a3b8`,
    Pending review `#ca8a04`, Done `#16a34a`, Overdue `#dc2626`;
  - project, PM, site and contract: `hash_color`, so the same project is the same color on every
    device and every day. A key in Display lists what is on screen (a project in it highlights
    the project); the "?" legend lists it all.

  The job type comes from `project_type`, which `build_card` now carries (one line); the PM from the
  `projects` payload the PM filter already uses. No new request. The color goes on through a CSS
  variable with `!important`, so an overdue card keeps its *Overdue* chip but takes the mode's color.
- **Highlight a project** (Maintenance Planner: a site). A click on a project's name on a card (6A's
  project drawer opens from the same click), *Highlight* in the project or site drawer's header,
  *Highlight this project/site* in a card's panel or 6B's right-click menu (`p6_menu_providers`), or a
  project in the color key: every card of it lights up and the rest fade, in every view and week on
  screen. A toolbar pill *Highlighting …* ✕ and Esc clear it (Esc closes a drawer or menu first, and
  yields to a Frappe dialog and a drag). Not remembered: a reload starts clear.
- **Saved views, per person, on the server.** The toolbar's *Views* menu: *Save view as…* (a name; the
  same name replaces), switch, *Manage views…* (use, delete). A view holds the calendar view, the
  filters (Project Planner: project, PM, group, *Maintenance, rentals, travel*, *Running over*,
  Resources available open; Maintenance Planner: technician, projected visits, project work, Resources
  available open) and the color-by. The view **last used** is saved a moment after any change and
  applied when the planner opens, on any device. Two rules:
  - **The route wins.** A URL that names a view and a date is used as it is, and only the filters are
    restored on the first open. Only a bare `/app/project-planner` (or `/app/maintenance-planner`)
    takes the saved view, and it **replaces** that history entry (`route_flags.replace_route` before
    `set_route`), so Back from the planner leaves it exactly as it would have.
  - *Running over* is never part of "last used" (Phase 4's reason: a chip that quietly hides most of
    the board the next morning is a trap); a named view may hold it.

  Draft mode and Tap to move are not part of a view: one is a way of working, the other a property of
  the device. The pages' older per-browser filter memory still fills the filters until the server's
  answer arrives (at most 4 s; after that the planner opens without it).
- **Team capacity strip.** A row over the week view and inside the crew view, under its day heads: per
  day, the free hours and capacity of everyone the group filter shows (Maintenance Planner: every
  technician with hours, or the one the technician filter picks), summed from the loaded `days` /
  `bookings`. **Red** when the team as a whole is booked past its capacity, **amber** at 90% or more;
  one person over and another free is not red. Pencil hours hatched after the firm ones, as on the
  heatmap (the Maintenance Planner's day cells now carry `soft_booked`, one line). Tooltip "Field crew
  Thu: 46h free of 120h · 3 people off": a holiday, time off or an all-day block counts as off, a day
  the person's pattern does not work does not, and the kind of time off is never said. A click opens
  6A's day peek.
- **Tap to move** (tablet and phone). With *Tap to move* on (the default where the pointer is coarse;
  remembered per device), a tap on a card selects it ("Moving: Dig at Riverwalk · Open · Cancel") and
  a tap on a day, or a person's day in the crew view, moves it through the drag's own
  `drop` → `plan_drop` → `commit`/`apply` → `send`, so the past-day question, the reason prompt, drafts
  and Undo all apply. Long-press stays 6B's menu (and the drag's lift); a modifier-click stays 6B's
  multi-select; a name or a date still opens its quick look; *Move with taps…* in the right-click menu
  starts the same thing from a mouse. With it off, a tap opens the card as before. Layout: toolbar
  controls, cards and the strip are at least 40 px tall under a coarse pointer; the planner clips any
  sideways overflow at tablet width and below, and the crew view scrolls inside its own container.
- **Print this view.** *Print* opens a browser window (on the click itself: one opened after a
  request is a blocked pop-up) with exactly what is on screen: the view and range, the filters and
  highlight as faded or lit cards, the color-by and its key, the team strip's figure in each day; the
  crew view and the heatmap print as tables. With 6B's selection non-empty it offers *Print selection
  only* (a list). The body is built from the loaded data by `big_picture.print_document`, never by
  cloning the live DOM (no drag handle or drawer can reach paper), every value escaped; the server
  adds only the print design system's chrome (`get_print_chrome`, the same stripe, letterhead and ruled
  tables as the weekly crew sheet; the Service pillar on the Maintenance Planner). Landscape Letter.

Hooks, one line each: `init_phase6c` (constructor), `p6c_before_route` (first line of `handle_route`),
`p6c_set_mode` (Project Planner `set_mode`), `render_phase6c` (`render`), `p6c_links` / `p6c_action`
(`open_card`), `p6c_ready` (6A's `p6a_ready`), `p6c_project_drawer` / `p6c_site_drawer` (6A's project
and site drawers) and `p6c_legend_sections` (first in 6A's legend). `tests/test_planner_phase6c.py`
pins the views store (own row, straight to the table, cleaned, capped, gated), and under node drives
the real page code: the bare-route replace and the route winning, the colors per mode, the strip's
arithmetic and filter, the tap's drop and the printed selection.
### On screen: the Conflict center, blocks and day notes (Phase 6D UI — TASK-2026-02470)

The page half of Phase 6D, for **both** planners and My week. The drawer, the two forms and the
markup are the planner kit's (`planner_kit/conflicts.js`, `planner_kit/blocks.js`), so both planners
show the same thing; each page's `PP6D_METHODS` / `MP6D_METHODS` block wires it to its own data and
its own write path. No new endpoint: everything calls the Phase 6D backend above.

- **Conflict center.** A toolbar chip **N Conflicts** (shown above 0; N is what nobody has kept;
  *Conflicts…* in Edit opens it at any count)
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
- **Today (the phone view, P8 of the Planner UX redesign, `docs/planner-ux-redesign.md` §4.4).**
  `/desk/project-planner/today/<date>`: the signed-in person's stops for the day and the day after, in
  route order, each with its leave-by and arrival times, site, what the work is ("Excavate basin · day
  2/3", "Visit, 2h"), address (tap for Maps), crewmates, equipment and weather chips, and the day's
  notes; the day's route map (the route view's own `draw_route_map`, fed by the `get_route` payload
  `get_today` carries) with *Full route* and *Open in Google Maps*; **Block my time** (the My week form,
  `self_only`) and **This week**. Distances are miles on screen (routing stays in km). Read-only: its one
  write is the person's own block, and a Fountain Move task shows a *Move* chip and stays the
  Maintenance Planner's to schedule. The declined field buttons ("running late", "can't make it") and
  the phone ICS feed are not here.
  - **Server:** `project_planner.get_today(date)` (a GET, planner roles). Found through Planner Resource
    `user`; `{resource, label, date, tomorrow, today, can_schedule, days: [2], route, blocks, day_notes}`.
    Each day is a `day_entry` plus `today_items`: `order`, `leave_by` (`leave_by_clock`: arrival less the
    drive, `route_slots`' arithmetic), `miles`, `weather`, `equipment` (`engine.read_equipment`), `day_of`
    (`task_day_of`: working days, so a weekend is not a day) and `fountain_move`.
  - **Landing (Nik, 2026-10-21: "remember the last-used tab per device ... filters can stay per person"):**
    a bare `/project-planner` opens the tab **this device** last showed (`p8_landing`, called from
    `p6c_before_route` once the person's saved filters are in): week, month, crew, heatmap or Today, kept
    in the browser's storage under `ee_project_planner_tab:<user>` (`p8_tab`, `p8_remember_tab`, written
    in `handle_route`); a blocked or empty store is a first visit. First visit: a phone opens Today
    (schedulers and technicians alike), a desktop the default view (the week). The server's "last used"
    row keeps the person's **filters and colors** only: `p6c_state(true)` no longer saves a `view`, and the
    landing never reads one (an old row's is ignored), so `planner_views_prefs.SCHEMA["project"]["view"]`
    is unchanged (named views from *Save view as…* still carry their view). The redirect uses
    `replace_route`, so Back leaves the planner. A link that names a filter (`?project=`, the Project
    form's *Open in Project Planner*) opens the device's calendar tab with it, and never Today (it has no
    filters to show). Someone the planner
    does not list, sent to Today by the landing, gets the planner (`p8_landed`, `render_today`).
  - **History:** the day arrows, *This week*, *Planner* and *Full route* each take one entry; Back and
    Forward step through them. *Block my time* takes none of its own (the kit's form owns its Back).
  - **Tests:** `tests/test_planner_today.py` and `scripts/test_planner_today.js` (the real page script
    against a model of v16.50's router).
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

### Follow-ups (Phase 6E — TASK-2026-02471)

Four small changes Nik decided on 2026-10-09, after using Phases 6B and 6D. The page work is
`PP6E_METHODS` / `MP6E_METHODS` at the end of each page file (the Maintenance Planner's *Add visit* is
its own section in [sapphire_maintenance/README.md](../sapphire_maintenance/README.md#maintenance-planner)).

- **Move by: Calendar days | Working days** (both planners; a field of the Move… panel since toolbar v2, it was a switch on the selection bar beside
  *Clear*); the choice is kept per device with the page's `save_pref` (`ee_project_planner_move_by`,
  `ee_maintenance_planner_move_by`) and **calendar days stay the default**, exactly Phase 6B's move.
  With working days (Monday to Friday) every selected task moves by the same number of working days
  **counted from its own start**: a Friday plus one is the Monday, a landing on a weekend goes to the
  next weekday going forwards (a card dropped on a Saturday lands on the Monday after) and **the Friday
  before going backwards** (the move is backwards when the drop is earlier than where the card was: a
  card dragged from Wednesday onto the Saturday before lands on that Friday; Nik, 2026-10-09), and each
  task **keeps its length in working days** (Wednesday to Friday, three working days, moved two working days on is Friday to
  Tuesday). A task that is only a weekend has no working days and keeps its calendar length. The
  offsets are worked out on the page and sent to `move_many` as explicit starts and ends, so the server
  contract is unchanged; the *Move…* panel, the drop, the confirmation, the reason prompt and the one
  Undo are all Phase 6B's. A visit moves by that many weekdays. The pure maths (`PP6E_PURE` /
  `MP6E_PURE`: `add_working_days`, `working_between`, `next_weekday`, `previous_weekday`,
  `snap_weekday`, `drop_days`, `moves_for`) steps with 6B's `ymd_add` and counts with its
  `working_days`, mirrors `project_planner.add_working_days` / `working_days_between` (a test compares
  them) and `shift_successors` is untouched. The weekend snap exists only on the page, where a drop
  becomes an offset (`drop_days` -> `snap_weekday`); the server helper has no drop to snap, already steps
  in the direction it is given (a Saturday minus one is the Friday), and was not changed. A card's own
  new start is `add_working_days(start, offset)`, which never lands on a weekend either way.
- **A day note never sends a message on its own.** See *Digests* under Phase 6D: the combined 6 AM
  digest (`planner_digest.digest_lines`) now returns no lines for a person with nothing booked, so a
  note or block rides along only with a message the person gets anyway. The maintenance and rental
  digests were already sent per visit or per job, with the note lines
  (`planner_blocks.digest_note_lines`) added inside, so they needed no change; a test now pins each. The `Planner Digest Log` claim is exactly as it was.
- **A split's dependents follow the second part.** `planner_actions.split_task` keeps both parts' names
  (no "(part 2)") and, inside the split's savepoint, points every **open task of the same project**
  (not Completed, Canceled, Cancelled, Invoiced or Template) whose `depends_on` lists the original at
  the second part instead. A task already waiting on the second part loses its old row rather than
  getting a second one; another project's tasks are not touched; each gets the timeline note "Now waits
  on TASK-… (second part of a split)" and the second part says which tasks now wait on it. The row is
  rewritten **straight in `Task Depends On`** (and the parent's `depends_on_tasks` text, which
  ERPNext rebuilds on save) rather than by `Task.save()`: a save runs `on_update` →
  `reschedule_dependent_tasks`, which pushes the dependent's own Open successors by calendar days
  whenever one starts before it ends, and re-pointing a dependency must move no date. The answer
  carries `repointed` (the task names). A dependent the caller cannot write fails the request, and the
  split rolls back with it.
  - **Undo.** The page keeps `repointed` in the split's Undo entry (`restore.dependents`).
    `remove_created_task` puts those that still wait on the second part back on the first **before**
    it deletes (so the delete's link check still guards everything else), and they are the only
    dependents that do not stop it: a task that started waiting on the second part after the split
    still refuses the Undo ("another task depends on it"), as does any dependent when the Undo has no
    restore plan (a duplicate, a quick add). Restoring the first half afterwards can make ERPNext push
    a dependent that already started before the first half's old end; that is its own rule for any save
    of the first half, not something the Undo adds.
- **Add visit here** (Maintenance Planner): see the Maintenance Planner section of
  [sapphire_maintenance/README.md](../sapphire_maintenance/README.md). Backend:
  [`api/maintenance_actions.py`](../api/maintenance_actions.py) (`get_visit_defaults`, `create_visit`,
  `remove_created_visit`, `sites_query`); `tasks._draft_maintenance_record` is now
  `tasks._new_maintenance_record` (the builder) plus the scheduler's insert, so both make a visit the
  same way.
- **A second open visit for the same fountain warns** (Maintenance Planner, Nik, 2026-10-09: warn,
  never block). Submitting two open visits for one fountain would roll the contract's next visit date
  forward twice, so when the site's active contract already has an open (not submitted, not canceled)
  regular visit for the fountain, `create_visit` answers `needs_reason` unsaved, like an overbooking,
  with one more line in the same `conflicts` list under **Duplicate visit**: "<fountain> already has an
  open visit, <SMR>, on Wed Oct 14. Submitting both moves the contract's next visit date twice." Sent
  again with a `reason` it creates the visit and the reason goes on its timeline. The page needed no new
  UI: its `ask_reason` dialog prints whatever `conflicts` holds. The open visit is found by
  `maintenance_actions._open_regular_visit`, the lookup `get_visit_defaults` already reports as
  `open_visit` (the scheduler's own de-dup), so the form and the create check cannot disagree. Only a
  visit linked to the contract can double a date, so a plain visit (no contract, or a fountain the
  contract does not cover) never warns. A Per Site Visit contract, and a whole-site visit with no
  fountain, match on that lookup's own rules (the contract, or any open regular visit of the site).

Hooks into the classes and into Phase 6B's blocks, one line each: `p6e_bar_switch` / `p6e_bar_note`
(`p6b_render_bar`), `p6e_drop_days` (`p6b_drop`), `p6e_move_label` / `p6e_move_default` /
`p6e_move_note` (`p6b_move_panel`), `p6e_moves` / `p6e_message` (Project Planner `p6b_move_selection`),
`p6e_date` / `p6e_question` (Maintenance Planner `p6b_move_selection`), `p6e_dependents`
(`p6b_split_save`); and on the Maintenance Planner `init_phase6e`, `render_phase6e` and `p6e_undo`. The
Project Planner needs no constructor hook (its style is in `project_planner.css`). The existing
`p6b_undo` line is untouched. `tests/test_planner_phase6e.py` pins all four.

### Build out tasks, Needs tasks and the richer quick add (T1, v1.602.0)

Nik, 2026-10-12: "I definitely want to be able to easily create tasks for projects in the project
planner screen so I don't have to leave it to build out tasks for a project." The page work is
`PPT1_PURE` / `PPT1_METHODS` at the end of `project_planner.js` (the **T1** block, methods `t1_*`); the
server is `api/planner_actions.py`.

- **Build out tasks.** A side panel (the planner kit's drawer, so Esc and Back close it and Forward
  reopens it) with a spreadsheet-style grid, one row per task: **Subject** (required), **Hours**,
  **Phase / parent**, **Depends on** and **Crew size**. *Enter* drops to the same cell of the next
  row (a new row at the bottom, in the phase of the row above), *Tab* goes along the row, *Shift+Enter*
  goes up, *Ctrl/Cmd+Enter* creates; lines pasted from a spreadsheet into a Subject cell
  (`Subject<TAB>Hours<TAB>Phase<TAB>Crew size`) fill rows from there down. **Phase** suggests the
  project's open phases (group Tasks); a name that is one of them (or its subject, in any capitals)
  files the task under it, **any other name makes a new phase** (a group Task, made first and shared by
  every row that typed it). **Depends on** lists the other rows of the grid ("3. Wire the pumps") and
  the project's own open tasks; a loop of rows, a row waiting on itself or on a row that was emptied
  is marked on the screen and refused by the server with the rows named. The button says what it will
  make ("Create 5 tasks and 2 phases"); blank rows are skipped. What was typed is kept per project in
  `this.t1.drafts` so a Back that closes the panel does not lose it. In Draft mode (v1.603.0) Create
  sends `draft=1` and the rows are kept as drafts (T2, below).
  - **The tasks are undated and crewless**, so they appear in the Unscheduled tray, ready to drag onto
    days. Nobody and no day is picked: no conflict check runs.
  - **One request, all or nothing**: `build_out_tasks(project, rows)` checks every row first (a mistake
    is named by its row number), makes the new phases, then the tasks in an order where each comes after
    what it waits on (the grid's own order when it already is; `build_order`, a stable topological sort
    the page runs too and a test compares), with the `depends_on` rows in the insert. A task ERPNext
    refuses ("Row 3 (Wire them) could not be created, so nothing was") fails the request and Frappe rolls
    it back. ERPNext lists every sub-task among its phase's dependencies by itself
    (`Task.populate_depends_on`), so a phase is saved again as each child is added; the stamp the Undo
    compares is read last.
  - **Undo** (`remove_built_out_tasks`) takes the lot back or nothing: every task and phase goes through
    `remove_created_task`'s own test (its creator, unchanged since, no timesheet or clocked time, no
    other dependent, no other people's comments, file or draft). Inside a build-out, tasks waiting on each
    other and a phase's own list of sub-tasks do not count; a sub-task somebody else added to a new phase,
    or anything attached to one task, stops **all** of it and the answer says which. Each task comes off
    its phase's dependency list (straight in `Task Depends On`, the phase is not saved), the waiting task
    is deleted before what it waits on, then the phases; `frappe.delete_doc` as the caller, never `force`.
    `remove_created_task` got the same allowance for a quick add filed under a phase: the phase's own
    `depends_on` row is no longer "another task depends on it" (before this version, undoing a
    duplicate or quick add of a sub-task always refused).
  - **Where it opens from**: the project's side panel (*Build out tasks*, Phase 6A's drawer actions via
    `t1_project_actions`), the Needs tasks tray, and a card's right-click menu (*Build out tasks for this
    project…*, a `p6_menu_providers` entry).
- **Needs tasks** (tray, below Unscheduled; collapsed with its count until opened): the Active
  customer-job projects with **no task at all** (no leaf task that is not a template or canceled: a
  project whose phases exist but whose work does not is listed; the line says "No tasks yet"). A
  project whose tasks are all finished is **left out**, by Nik's decision (2026-10-12, "Only no-task
  projects"): on production the first definition listed 304 stale Active customer jobs with every task
  finished against 17 with no task, and buried the 17. Each row has a *Build out* button
  and the name that opens the project's side panel. `get_projects_needing_tasks` is **one** query for the
  lot (`needs_tasks_query`: `NOT EXISTS` over `tabTask`, the planners' own `planner_job_condition`, all
  bound, at most 200); the page re-reads it after a load at most every 15 seconds, and straight away
  after a build-out or an Undo, and applies its own project and project-manager filters
  (`PPT1_PURE.needs_visible`). Hidden for anyone who cannot create Tasks.
- **Richer quick add** (Phase 6B's *Add a task* dialog, `p6b_quick_add`): a collapsed "More about the
  task" section adds **Phase** (a group Task of the chosen project), **Depends on**, **Starts at /
  Ends at** (a time slot on the date: `custom_start_datetime`/`custom_end_datetime`, one day, the
  estimate defaulting to the slot's length), **Others on the crew** (the *Person* above is the lead
  when there are others; nobody is picked for you, every person is conflict-checked together and any
  conflict asks for a reason as before), **Crew size**, **Address**, **Outdoor work**,
  **Customer-facing visit** and a **Description** (stored escaped). A plain add sends exactly what it
  always sent; `quick_add_task`'s new arguments are all optional. A dependency still running when the new
  task starts is a `Dependencies` line in the reason prompt (a pencil needs no reason).

Hooks into the class and Phase 6A, one line each: `init_t1` (constructor), `t1_load_needs` (`load`),
`t1_render_tray` (`render_trays`), `t1_undo` (`undo`, before `p6b_undo`) and `t1_project_actions`
(`p6a_open_project`). `tests/test_planner_build_out.py` pins the server and the page.

### New tasks in Draft mode, Paste a list and Copy from project (T2, v1.603.0)

Nik, 2026-10-12: "build out a whole new schedule as a draft and publish it once." Publishing is also
when work reaches Google Calendar (the sync follows saved Tasks), so **a drafted new task is never a
Task**. Server: `api/project_planner.py` (the "T2" block) and `api/planner_actions.py`; page: the draft-mode
block, Phase 6B's menu and the T1 block. `tests/test_planner_draft_new_tasks.py` pins it.

- **The data model.** `Planner Draft Change` has a `kind`: `Change` (the default, and what every
  existing row already is, since a normal doctype's new column default is written into every existing row
  by the `ALTER`: no backfill patch) or `New task`. A `New task` row names neither a `task` nor a `visit`,
  carries its `project`, and keeps in `payload` the Task it will become as a *spec* (`load_spec`: whitelisted
  Task fields, crew, qualifications, equipment, `depends_on`, where it came from). The Project Planner
  reads its `task` rows plus its own `New task` rows; the Maintenance Planner reads `visit` rows only.
- **Drafting.** In Draft mode quick add, duplicate and build out send `draft=1`. The server runs the live
  call's checks, builds the document through `task_from_spec` (the one function that builds a Task from a
  spec, also used by the live calls and by Publish), and, instead of inserting it, keeps a `New task` row
  and answers with a card named **`draft-new:<row>`**. A phase or prerequisite that is another drafted task
  is written that way in the spec. Conflicts come back as warnings; drafting never asks for a reason.
  Split stays refused.
- **Showing and editing.** `get_planner(draft=1)` draws the card (dated ones on their day, undated ones in
  the Unscheduled tray) with a "New" chip and counts it in the engine as an `extra`, so free hours and
  conflicts include it. Drag, resize, pencil, re-crew and the multi-select move go through the usual
  `save_task`/`add_crew`/`swap_crew`/`move_many` drafts and rewrite the spec. Its menu says "Not published
  yet" for what needs a Task (duplicate, split, next free day), and its dialog offers no Task lookups. A
  draft that can no longer be drawn (project no longer a customer job, a person gone, its phase or
  prerequisite discarded) is "out of date" and cleared with *Discard out-of-date*.
- **Undo and discard.** The Undo of a drafted create discards the row(s), refusing while another drafted
  task is filed under or waits on one. *Discard* and *Discard out-of-date* cover `New task` rows.
- **Publish.** The new tasks join the `Change` rows in the one all-or-nothing conflict check and the
  per-person notices, then are created, phases first and each after what it waits on (`new_task_order`),
  each in its own savepoint with `planner_publishing` up. One ERPNext refuses is reported and stays a
  Draft (and so is what waits on it). Published rows record `created_task`.
- **Paste a list.** A Build out footer action: lines like `Excavate basin, 16h`, `Pour slab - 8 hours`,
  `• Pull wire (6 hrs)`, `3. Commission system`, `Electrical: Pull wire, 6h` become rows
  (`PPT1_PURE.parse_lines`: bullets, checkboxes and numbers dropped, trailing hours read, `Phase: task`
  sets the phase). The tab-separated spreadsheet paste is kept.
- **Copy from project.** A Build out footer action: pick any project you can read; `get_copy_source`
  returns its leaf tasks as rows (subject, hours, phase, crew size, dependencies as `row:N`), never dates,
  people, notes or addresses; templates and canceled tasks are left out, finished ones kept, at most 100.
  The grid is replaced when empty, otherwise you choose Append or Replace; then edit and Create as usual
  (drafted in Draft mode), on the current project.
- **Limits worth knowing.** A drafted task routes from its project's site (a custom task address is
  applied when it is published). The Needs tasks list still lists a project whose only tasks are drafts.
  Up to 300 unpublished new tasks per person.

### Phase templates and task bundles (T3, v1.608.0)

Nik, 2026-10-12: start a job's plan from DOC-0041's phases instead of typing it, and keep reusable,
composable **bundles** of tasks ("Control panel design + build" and its subtasks) to pick from. Server:
`api/planner_templates.py` (two reads) and `planner_templates.py` here (the rules, pure); page: the **T3**
block at the end of `project_planner.js` (`PPT3_PURE` / `PPT3_METHODS`, methods `t3_*`), reached from the
Build out footer. `tests/test_planner_templates.py` pins it.

- **Start from template…** (a Build out footer action). The dialog is built from a catalog read first
  (`get_planner_templates`): a phase template with its phases ticked (pick the ones this job needs), any
  number of bundles, and the phase the bundles go under (a phase of the grid or the project, or a new
  name). `get_template_rows` answers rows in `get_copy_source`'s shape (subject, default hours, phase, crew
  size, `row:N` dependencies), so they go in through Copy from project's own path: an empty grid is
  replaced, otherwise **Append** or Replace. Then edit, and Create (or draft, in Draft mode) as usual.
- **The sequence** (`planner_templates.template_rows`). Inside a phase or a bundle the tasks run in order,
  each after the one before, unless the template's own dependencies say otherwise; a *Project
  management* task runs alongside its phase (it waits on the phase before, nothing waits on it). A
  phase's first tasks wait on the last tasks of the nearest **picked** phase(s) it runs after, walking
  through any that were not picked. DOC-0041: 100 > 200 > 300 > 400, then 500 and 600 side by side, then
  700, which waits on both. **Phase 800 is never offered**: maintenance contracts own it. Bundles come
  after the phase rows, under the chosen phase, waiting on nothing outside themselves.
- **Phases are `Phase 100` … `Phase 700`, matched by number** (ClaudeBrain decision 2026-10-12, "Project
  task trees use Phase 100–700 top-level groups"; the 10/1 schedule import and PRJ-00597 already have
  them). The rows' phase is `Phase 300`; the dialog shows "Phase 300 — Rough-in". In `build_out_tasks`
  (and the grid's `PPT1_PURE.phase_match`, which mirrors it) a phase name starting with a three-digit
  number ("Phase 300", "300 Rough-in", "phase 300 - design": `planner_templates.phase_number`, re-exported
  as `planner_actions.phase_number`, the one helper) is the project's open phase with that number,
  whatever its spelling (the one named exactly `Phase 300` first); with none, ONE new `Phase 300` is made.
  So a template loaded onto a project that has its phases never makes a second set. Names without a
  number match by name or subject, as before.
- **Materials are not calendar tasks.** Each template phase has a **procurement checklist** (a Task field,
  one item per line: materials, equipment, expenses, subcontracts nobody schedules). The grid keeps the
  loaded phases' checklists in `state.checklists`, and Create sends those of the phases it will MAKE as
  `build_out_tasks(phase_checklists=...)`, which writes each into the new phase's description as a tick
  list (a Quill task list, items escaped). A phase the project already has is never written to. Why the
  description: it is the phase's own, visible on its form and on print, and a tick list there can be
  ticked off as things are ordered without any new doctype.
- **Default hours** come from the template Task's *Expected Time*; a template row also carries its
  `cost_code`, the seam T5 (hours from a live estimate, blocked) will use (`apply_estimate_hours`).

**The data model: ERPNext Project Templates.** A template is a `Project Template` whose tasks are template
Tasks (`is_template`, status *Template*). ERPNext's own model already holds the phases (a group template
Task, its tasks under it as `parent_task`), the order (the template's task table) and dependencies (the
Tasks' *Depends On*), so no custom doctype was needed. Custom fields (fixtures) carry the rest:

| Field | On | What it is |
|---|---|---|
| *Planner Template Kind* (`custom_template_kind`) | Project Template | *Phase template* or *Bundle*. Blank: an ordinary ERPNext template, which the planner never offers (and the Create Project dialog offers only those). |
| *Review Status* (`custom_review_status`) | Project Template | *Needs review* (the dialog says so) or *Reviewed*. |
| *Template Notes* (`custom_template_notes`) | Project Template | Where it came from. |
| *Template Line* (`custom_template_line`) | template Task | *Labor*, *Subcontract* or *Project management*. |
| *Cost Code* (`custom_cost_code`) | template Task | The corrected catalog's code (`quality/cost_codes.py`), never a DOC-0041 one. |
| *Phase Number*, *Runs After Phases*, *Procurement Checklist* | template phase (group Task) | 300; "500, 600" (blank: waits on nothing; when NO phase has one, each runs after the one above it); one item per line. |

**Seeded** by `patches/seed_planner_templates.py` (insert-only by template name, never over an edit): the
phase template **Fountain build phases (DOC-0041)** (`Phase 100`–`Phase 700`, 30 tasks) and six starter bundles
(Control panel design + build, Interior fountain, Splash pad, Lighting package, Equipment room (pump +
filter skid), Commissioning + hand-off), all *Needs review*. The content is
`data/planner_templates.json`, derived from DOC-0041 Sapphire Cost Worksheet v1.0 by
`scripts/derive_planner_templates.py` (its `source.interpretations` lists every judgement made: the two
cost-code overrides, the defects not copied, the build order of 600 and 700). Every DOC-0041 v1.0 labor
line has 1 in "# Units", the blank worksheet's placeholder rather than an estimate, so **the hours are
blank: fill them in per template** (a 1-hour default would book every templated task as a 1-hour job).

#### How to edit the templates and bundles

System Manager and Projects Manager (`patches/grant_project_template_editing.py`) edit them in the Desk:

1. **Project Template** list → filter *Planner Template Kind* (Phase template or Bundle). Open one.
2. **A task's hours, crew size, line or cost code:** click its row in the *Tasks* table to open the
   template Task, change *Expected Time* (default hours), *Crew Size*, *Template Line* or *Cost Code*,
   and save. A subject edited on the Task shows in the planner straight away. **Every seeded task's
   hours are blank: fill them in per template**; until then the grid's Hours cell loads empty, showing
   what still needs an estimate.
3. **A phase's checklist, number or place in the sequence:** open the phase (a group template Task) and
   edit *Procurement Checklist* (one item per line), *Phase Number* or *Runs After Phases* (e.g. `500,
   600`). **Keep a phase's subject `Phase <number>`** (`Phase 300`): that is the name of the phase on
   every project, and how a project's existing phase is found. Its descriptive name ("Rough-in") goes
   in the phase's *Description*; the planner shows it beside the number.
4. **Order and dependencies:** the rows of the template's *Tasks* table are the order. To make tasks run
   side by side instead of one after another, edit the template Tasks' *Depends On*: once any task of a
   phase or bundle has a dependency on another of the same group, the planner uses exactly those.
5. **Add a task:** create a Task with *Is Template* ticked (status *Template*), under the phase (*Parent
   Task*) for a phase template, then add it to the template's *Tasks* table. **A new bundle:** a new
   Project Template with *Planner Template Kind* = Bundle and its template Tasks.
6. **When a seeded one is right**, set *Review Status* to *Reviewed*; the planner stops flagging it.
   Ticking *Disabled* on a template hides it from the planner.

### AI draft: a task breakdown for the grid (T4, v1.609.0)

Nik, 2026-10-12: the Build out grid can propose a task breakdown, which the planner edits and creates as
usual. A footer action, **AI draft…** (`t4_actions`, the `PPT4` block at the end of `project_planner.js`),
opens a dialog (*Describe the job*, optional; Append or Replace when the grid has tasks), shows a spinner,
and fills the grid through `t1_set_rows`, the path Copy from project uses. Rows carry a quiet mark
(`row.ai`, a coloured edge and a tooltip) and the status line says *AI draft — review before creating*.
Server side it is one endpoint, [`api/planner_ai_draft.draft_task_breakdown`](../api/planner_ai_draft.py)
(POST).

- **It never picks crew and never picks dates, and it writes nothing.** The answer has five keys per
  row, `subject`, `hours`, `phase`, `crew_size` (a number) and `needs` (`row:N`), and
  `validate_breakdown` drops every other key the model sends. Nothing is inserted; the rows become tasks
  only through `build_out_tasks` (or drafts, in Draft mode), like every other row.
- **The answer is untrusted**: 100 rows at most, hours 0–400, crew size 0–50, subjects cut to 140, blank
  and repeated subjects (also of tasks the project has) dropped, phases named like the house template
  (`Phase 100`…`Phase 700`, the project's own spelling wins, no 800), and a dependency on itself, on a
  missing row or round a loop loses the link, never the row; the answer's `note` says how many.
- **Prompt inputs**, all read as the caller: project name, type, value streams, the customer's *type*,
  description and notes fields, the Project Scope of Work (not superseded) with its criteria, open phases
  and open task subjects, the grid's own subjects (Append), the planner's words. **Not read:** customer
  name, contacts, addresses, emails, phones, project owner, crew and employee records; free text is
  scrubbed of emails, links and phone numbers.
- **Failure is a sentence** (`ok: false`, no rows): the Google service account not set up (there is no AI
  on/off switch in this app), a timeout, another error, a reply that is not JSON, nothing new, or 20 drafts
  in an hour. The Error Log gets the exception's class and a scrubbed message, never the prompt and never
  `frappe.get_traceback()` (it renders locals, and the prompt is one).
- **Gate**: the planner roles, Task create, Project read, customer jobs only. **Model**: the app's
  `api/gemini.py` client, so its `MODEL_ID`; usage is an AI Model Usage row under `planner_task_breakdown`.
- Tests: `tests/test_planner_ai_draft.py` (own `ci.yml` step; fake Gemini; nothing real is called).

### Per-project publish and baseline (P3, v1.702.0, Planner UX redesign)

The back end for Nik's D5, D8 and D11 of 2026-10-21 (`docs/planner-ux-redesign.md` §4.8). The page
that calls it is P4; until then nothing on screen changes.

- **Each project's schedule is *Draft* or *Published*** (`Project.custom_schedule_status`, read-only,
  `no_copy`, default Draft; a blank reads Draft). `get_planner`'s `projects` carry it as `schedule:
  {status, published_on, baseline_set_on}`. The patch `mark_project_schedule_states` published the
  projects that already had dated work with someone on it (10 of 661 on production) and left the
  rest Draft.
- **A Draft project's drafts are shared.** `_draft_rows` also returns the other schedulers' rows on a
  Draft project (`shared: True`, raw SQL after the role gate), so one person's draft schedule is the
  project's: anyone can see it, edit it (the row is saved with `ignore_permissions`, since
  `if_owner` would refuse it) and publish it. On a Published project rows stay private, as before.
- **`publish_drafts(project=)` / `discard_drafts(project=)`** act on one project's rows, shared ones
  included. Without `project` both act on the caller's own rows only, exactly as before.
- **`publish_project_schedule(project, reason=None)`** publishes that project's drafts (one conflict
  check, one reason, one notice per person), then marks it Published and **sets the baseline**: each
  dated task's first and last day into `Task.custom_baseline_start` / `custom_baseline_end`, copied
  **once** (a task that has a baseline keeps it). A publish that asks for a reason changes nothing;
  one that leaves a draft behind (skipped or failed) keeps the project Draft so it can be finished.
- **`reset_baseline(project, dry_run=0)`** re-copies every task's days now (an undated task loses
  its baseline); `dry_run=1` only counts them, for the confirmation. It is also the first *Set
  baseline* of a project the patch published without one.
- **Baseline and state writes are `db.set_value(update_modified=False)`**, never a save: copying a
  date is not an edit, so no assignment sync, change alert, calendar sync or ERPNext reschedule, and
  no planner with the task open is told it changed. The project's timeline gets a comment.
- **"Notify crew?"**: `save_task`, `add_crew`, `swap_crew`, `shift_successors`,
  `planner_actions.move_many` and `maintenance_planner.move_visit` take `notify`. Unset changes
  nothing. `1` sends the publish notice to the people whose bookings moved (never the editor); `0`
  tells nobody; `auto` is Notify when the change touches today or the 13 days after it, before or
  after the move. Any answer holds back that save's 48-hour change alert.

### Tasks that follow, Draft schedules and "Notify crew?" (P4, Planner UX redesign)

Nik's D8, D10, D11 and D15 (2026-10-21). The page side is the `PPAS` block at the end of
`project_planner.js` (and `MPAS` at the end of `maintenance_planner.js`); the server side is in
`api/project_planner.py` ("P4: the tasks that follow") and `planner_actions.move_many`.

- **The tasks that follow move with a task, both ways.** A drag, a resize, a dialog's dates, *Next free
  day*, a booked suggestion and *Tap to move* send `dependents=1` on `save_task` / `swap_crew`. The server
  moves every open, dated task that waits on it, directly or through others, by the **working days its
  end moved** (`dependent_days`). That covers a move later or earlier and a resize of the right edge;
  a resize of the left edge moves nothing after it. It happens in the same request: one conflict check
  for the lot (`needs_reason` then lists the `moves`), one reason, one notice per person and one Undo.
  The answer carries `shifted`, `shift_skipped` and `shift_days`. Finished, rental, undated and
  Fountain Move followers, and those the person cannot change, stay where they are and are named in an
  orange alert. **The walk only goes on through tasks that move** (`_walk_followers`), so a task that
  waits on the moved one only through a task that stays, stays too. Tasks past the cap (200, 25 levels)
  are named. **Working days skip the company's holidays** (its default Holiday List,
  `_shift_holidays`). **Every follower is checked against every task it waits on**, where those go
  (`_follower_dependency_lines`). Drafted followers get one batch overbooking check
  (`_draft_checks`), as warnings. **Hold Ctrl (Cmd on a Mac) as the card or its
  edge is dropped** and just that task moves. A multi-select move does the same through
  `move_many(dependents=1)`. A task that is itself in the selection is moved where it was put, never
  again as a follower. This replaces the old "Shift N tasks that follow?" offer. `shift_successors`
  stays as an endpoint for other callers and shares the helpers.
- **The order of the saves is the point.** Moving later, the furthest follower is saved first and the
  task itself last. ERPNext's own `Task.on_update → reschedule_dependent_tasks` pushes an Open dependent
  the saved task now overlaps to the day after its end, by the calendar. Done in this order, it finds
  nothing to push, so every follower moves by working days as D10 says. Moving earlier, the task goes
  first. **Ctrl/Cmd cannot stop ERPNext's own push.** A lone task dragged later into its Open dependent
  still pushes that dependent by calendar days, as it always did.
- **A Draft schedule drafts every write** (D8). The page sends `draft="auto"` on `save_task`, `add_crew`,
  `swap_crew` and `move_many` unless *Hold all my changes* is on (then `draft=1`, as before). The server
  drafts a task whose project schedule is Draft, blank included (`_drafting`), and saves the rest.
  `move_many` splits one batch between the two. Its followers are drafted along (`_draft_followers`,
  each from its own drafted days). A follower in a project whose schedule says otherwise is left alone
  and named, so a draft never moves a published schedule and a live save never reaches into a draft one.
  The creating writes (quick add, duplicate, build out, AI draft) send `draft=1` for a Draft schedule's
  project; the page decides there, because those endpoints take only `draft=1`. **Still live on a Draft
  schedule:** a split, Copy week and the Overdue tray's bulk actions, which have no draft form.
- **The read always asks for drafts** (`get_planner(draft=1)`), so a Draft schedule's drafts are seen by
  everyone, another scheduler's with a "Draft · Clegg" chip (`drafted_by`). `drafts.projects` counts the
  rows per project (`shared`: how many are other people's). **The strip under the toolbar** has one line
  per project with drafts. A Draft schedule's line offers *Publish…*, which runs
  `publish_project_schedule` after a confirmation, sets the baseline and tells each person once, and
  *Discard*, which drops everyone's drafts on it after saying how many are other people's. A published
  project with held changes gets *Publish* (`publish_drafts(project=)`) and *Discard*. With one project
  filtered whose schedule is Draft, the line shows even before anything is drafted. While the hold is
  on, its own line comes first ("Holding your changes: N not published yet") and only Draft schedules get
  a line of their own. The **project peek** offers *Publish schedule…* (Draft) and *Set baseline…* /
  *Reset baseline…* (a dry run first, then a confirmation that names how many tasks change).
- **"Notify crew?"** (D11). On a published schedule, a change to the dates or crew of a task somebody is
  on asks **once per gesture**, however many tasks it moves. The dialog names the people. Notify is the
  default (the primary button, so Enter takes it) when the change touches today through the 14th day,
  the old days counting as much as the new; past that, Don't notify is the default. **Esc puts the change
  back**, and nothing is sent. The answer goes as `notify=1` or `0`, the Undo entry keeps it, and Undo
  sends it again. Nothing is asked on a Draft schedule, while the hold is on, for a task nobody is on, or
  for a pencil or firm-up. In those cases `notify` is not sent and the save behaves as before P3. The
  Maintenance Planner asks the same way before `move_visit` (`mpas_with_notify` in its `send()`), once
  for a multi-select move (`mpas_batch`), and never for an Undo (which sends `notify: null`).
- **Publish keeps a drafted chain on its drafted days.** `_apply_plans` saves drafts furthest first.
  A push by ERPNext's own reschedule made during the publish is not "changed by someone else"
  (`since`): the push is undone and the draft applied. A push after a task was saved is put back
  (`_settle_plans`).
- **Undo** of a change that moved followers is one `move_many` that puts every task back on its days,
  with the same Notify answer. When the change was more than dates, such as a crew handover with a move
  or a dialog save, the plain Undo's `save_task` then restores the rest of the task.
- **Tests:** `tests/test_planner_autoshift.py` covers the server on the Phase 3B harness, and
  `scripts/test_planner_autoshift.js` drives both real pages under node.
### The Lookahead, the planner's home tab (P5, v1.708.0, Planner UX redesign)

Nik's D2 of 2026-10-21 (`docs/planner-ux-redesign.md` §4.2): the screen a PM lands on is a lookahead
across all jobs. Rows are projects, then their phases, then tasks; three weeks run across, starting on
the site's first weekday (Sunday on production). It is the page's `PPL` block and
`api/planner_lookahead.get_lookahead`.

- **Addresses.** `/desk/project-planner/lookahead/<date>` (three weeks from the week holding that day;
  `/<date>/4` for 2, 4 or 6 weeks), `/calendar/week/<date>` and `/calendar/month/<date>` for the old
  week and month views, now under the **Calendar** tab with a Week/Month switch. The old `/week/<date>`
  and `/month/<date>` addresses (bookmarks, the digests, the Project form's link) are **replaced**, not
  pushed, by their new ones, with the filters and any other query keys. A bare `/project-planner` opens
  this device's tab (P8's store; the Lookahead is one of them) and, with none, the Lookahead on a
  desktop and Today on a phone, each replacing the bare address with its own (a bare entry would be
  taken over by the device's tab on the way Back, so Back would bounce off it). Today's *Planner*
  button opens the Lookahead. The tabs are Lookahead, Crew, Calendar, Heatmap and Utilization until
  P7 folds the last two into Crew. Each tab, week and range is a route, so Back and Forward step
  through them. Collapsing a row and the Needs crew chip are not routes.
- **The bars are the planner's cards.** `get_lookahead` is `get_planner` over the window plus the tree,
  so every bar is a `.pp-card[data-task]` the page already knows. A drag (measured from the day under
  the pointer), an edge (6B's resize), a click (the side panel), a right-click (6B's menu), selection,
  search, Color by and Tap to move all work unchanged, and every write goes through `commit`/`send`
  with P4's auto-draft, tasks that follow, Ctrl/⌘ and "Notify crew?". There is no second save path.
- **A bar** shows the crew's initials, the task's size in days (D7: working days across its span, at
  least one, as P11's calendar labels count), and the chips a card
  carries: Conflict, Overdue, Running over, Needs crew, Starts early, Missing a qualification, and
  weather. A pencil is hatched. The baseline (P3) is a grey ghost behind the bar, and a slip past it
  is a thin red tail. A bar that runs past the window is cut, dashed and without a grip on that end.
  A Fountain Move task is read-only.
- **Hovering or focusing a bar** draws arrows from what it waits on and to what waits on it. With
  hundreds of links, only that chain is drawn.
- **The project row** carries its schedule (D8):
  - ● *Published* with the date;
  - ◐ *Draft schedule* with **Publish…** (P4's own confirmation and `publish_project_schedule`);
  - *Fountain Move*;
  - *No tasks yet* with **Build out** (T1's grid). This replaces the Needs tasks tray.
  Clicking the name opens the project peek.
- **Not scheduled** is a line per project whose chips drag onto a day, as the Unscheduled tray's
  cards did. A **Needs crew** chip in the board's own bar narrows it to the tasks short of crew,
  which replaces the Needs crew tray. The Overdue tray stays. The Resources panel is not on this tab.
- **Collapsing** a project or a phase draws its work as plain runs, and is remembered in this browser.
- The **range picker** offers 2, 3, 4 or 6 weeks. The choice is remembered for the next bare open.
- **Drop targets.** Every row's days take a drop, a project's and a phase's too, so a project with no
  dated work yet still has days to drop its Not scheduled chips on.
- **On a phone** the board is a list by day, and each day is a drop target. With undated work to place,
  every day of the window is listed.
- **Google is off** on this tab (spec §6): `get_lookahead` calls `get_planner(google=0)`.
- **Print this view** prints the board's rows as a list.
- **The Project, PM and Group filters** apply as on every tab. Group keeps the tasks with someone of
  that group on the crew.

## Calendar sync (C2, v1.604.0)

When planners commit work, ERPNext pushes it **one way** to Sapphire's existing shared Google
Calendars so the team can see the schedule there. Nik's decisions of 2026-10-12, settled:

- **ERPNext is the only source of truth.** Nothing ever flows from Google into ERPNext; two-way sync was
  withdrawn. (The one exception came later, by design: C4 reads people's own time off off the vacations
  calendar, see [Time off in](#time-off-read-in-from-google-c4-v16070).) An edit made in Google is undone by snap-back (below).
- **Three calendars that already exist**, owned by Nik's account, all America/Denver: **Projects** (firm
  and pencil project tasks, day notes), **Maintenance** (visits, projected contract visits) and **Company
  Holidays & Vacations** (unavailable blocks). Their IDs go in Calendar Sync Settings, typed or picked
  from the account's own list (*Pick calendars*, which shows only calendars it can change).
- **Identity: one Google account by OAuth, "for now"** (Nik's). A service account cannot do it: Workspace
  caps sharing outside the domain at "See all event details". *Authorize with Google* uses the OAuth client
  in **Google Settings** (Frappe's) and Frappe v16's shared callback,
  `/api/method/frappe.integrations.google_oauth.callback` (v16.31+), with a server-side state naming our
  callback (`api/calendar_sync.oauth_callback`, deliberately not whitelisted). Scopes:
  `calendar.events` and `calendar.readonly`, nothing else, and **no `include_granted_scopes`** (it would
  hand the token every scope the account ever granted the client). The refresh token is a Password field;
  switching accounts is one more click. **This is not Frappe's own Google Calendar doctype sync**, whose
  three production rows stay disabled by decision; and the v1.346.0 removal of the every-Task broadcast
  stands (`tests/test_hooks_integrity.py`).
- **Off by default**, with a dry-run **Preview** that lists what a full re-sync of a date range would
  create, update or delete, calling no Google API and writing nothing.

**What goes on a calendar** (all-day events in C2; C3 swaps in timed route slots through the one function
`payload.event_times`):

| Item | Calendar | Dates | Tentative | Removed when |
|---|---|---|---|---|
| Project Planner task on a customer job (`crew_availability.is_planner_job`, so never an internal project; rental crew tasks count, as on the planner) | Projects | `crew_availability.task_span`: `exp_start_date` → `exp_end_date` as one event (Google's end is exclusive), or a same-day slot's day | pencil (`custom_tentative`), "✎ " | Canceled, a template or group, undated, its project Canceled, deleted |
| Maintenance visit | Maintenance | the Maintenance Planner's own day (scheduled while a draft; the visit date once done or in review) | pencil (`tentative`), "✎ " | canceled, unscheduled, deleted |
| Projected contract visit (`api/maintenance_planner._projections` + `_decorate`) | Maintenance | the projected day | always, "(projected)" | no longer projected: drafted, moved, paused, ended, or the customer is on service hold |
| Day note (`Planner Day Note`) | Projects | its date | no | deleted |
| Unavailable block (`Planner Block`) | Company Holidays & Vacations | its date ("Austin — Unavailable 2 pm–4 pm") | no | deleted |

Finished work (a task Completed or Invoiced, or on a Completed / Invoiced / Paid project; a visit
submitted or in Pending Review) **keeps its event with "✓ "** in front.

**Drafts never sync.** Both planners keep unpublished changes in `Planner Draft Change` rows and never
touch the record until Publish, so reading saved records is exactly "committed work". No module of the
sync names that doctype or either draft overlay, and the test fails the build if one starts to.

**The event.** Title `"<site> · <subject or visit label> — <crew first names>"` (`short_site_name`; a
subcontractor keeps its whole name). Location: the stop's address the way the route view finds it
(`routing.locate`), else the project's (`workforce/sites.project_address_text`). Description: status,
hours (and days), crew (lead marked), equipment (`custom_equipment`), qualifications needed
(`custom_required_credentials`), flagged weather for outdoor tasks and visits from `planner_weather`
(cached three hours per site, one Open-Meteo request for every uncached site, never raises), and always
"Managed by ERPNext — edits made here are undone. Change it in the planner: <absolute link to that day's
week on the Project or Maintenance Planner>". Color by value stream (Settings' color rows over the
defaults: Design 3 Grape, Build 9 Blueberry, Service 10 Basil, Events 6 Tangerine, Delivery 5 Banana, Day
Note and Unavailable 8 Graphite). **No attendees, ever** (`sendUpdates=none`), no reminders on the
writing account, transparent.

**Nothing secret.** These calendars are shared. The description is built only from structured fields;
the readers never select a task description, project or visit notes, a Maintenance Profile's
`access_codes` / safety or wrap-up instructions, or a block's `note` (private to the person and the
schedulers). The free text that does show (a subject, an address, a day note) goes through
`payload.scrub`, which drops any segment naming a gate, door, alarm, lockbox or keypad code, a code or
PIN followed by digits, a password or a combination. That is defence in depth: a bare number in a
subject cannot be told from a part number.

**Identity, idempotency, re-drivability.**

- Every event carries `extendedProperties.private` `{erpnext_site, erpnext_doctype, erpnext_name,
  erpnext_kind[, erpnext_key]}` and a **deterministic client-supplied id**: `"erp"` + SHA-1 of site,
  doctype, name and key in hex (base32hex-safe). A re-run inserts the same id, gets 409, and updates,
  which also restores an event deleted in Google. An id Google refuses outright gets a `g<n>` suffix.
- `Calendar Sync Link` holds one row per item (`source_ref` unique) with the content hash of what was
  written: an unchanged item costs no Google call. A changed calendar ID moves the event.
- **Triggers** (`calendar_sync/triggers.py`): `doc_events` on Task, Sapphire Maintenance Record (update,
  submit, cancel, update after submit, trash), Sapphire Maintenance Contract (re-projects), Planner Day
  Note, Planner Block, and Project (name, status, type or address changed). Each enqueues **once per record
  per request** with `enqueue_after_commit=True` and a stable `job_id`, so a Planner publish
  (`frappe.flags.planner_publishing`) that saves a task five times queues it once. Skipped in import,
  patch, install, migrate and tests; never raises into the save; nothing while the switch is off.
- **The deploy FLUSHDBs redis**, destroying queued jobs, so there is a **nightly full repair**
  (`scheduler_events.daily_long`): every item in the window (Settings: 14 days back, 180 ahead) and every
  live link in it, then the projections, removing projected events that are no longer projected. Capped at
  20 minutes; what is left waits for the next run. **Re-sync everything** on the Settings page runs the same thing now.
- **Snap-back** (every 10 minutes): `events.list` per calendar with the stored `syncToken` and deleted
  events shown. An event carrying this site's properties that somebody changed is rewritten from ERPNext;
  one deleted in Google is recreated; one whose source is gone is deleted; a stray copy is deleted. **Our own
  write echoing back is recognised by the etag stored on the link**, not by comparing text (Google may
  normalize text, and a comparison that never matches would rewrite the same event forever). Events without
  our properties, or with another site's (staging), are other people's and are never touched. A 410
  drops the token and lists in full. At most 300 fixes per run. A maintenance visit's name in an event is
  read through `sapphire_maintenance/visit_names.current_visit_name` (v1.610.0): the visits renamed from
  their random names keep the old name in their events until the next sync rewrites them, and an edit
  made in Google meanwhile would otherwise find no visit and delete the event.
- **Errors**: 429, 5xx and rate-limit 403s back off (up to four retries, honouring `Retry-After`); a 401
  refreshes the token once. A failing item is marked Error on its link and logged once when it starts
  failing. A refused refresh token stops the run and is logged once an hour. No error or log carries a
  token, secret or code (`raise ... from None`, `log_error` with keywords).
- **Health panel** (Settings): switch, Google Settings readiness, connected account, a test refresh
  (*Test connection*), the redirect URI to register, the window, last snap-back check, last full repair and
  its counts, events by status and kind, and the last ten errors.

**Out of scope here** (later tasks): timed route slots and leave-by (C3), reading hand-made time off from
the holidays calendar into the planner (C4, below), cutover and ending the old hand-made series (C5), the Triton
**Out of scope here** (later tasks): timed route slots and leave-by (C3, below), reading hand-made time off from
the holidays calendar into the planner (C4), cutover and ending the old hand-made series (C5), the Triton
dedupe (C6).

`tests/test_calendar_sync.py` (bench-free, own CI step) pins the payload, the secrets rules, ids, hashes,
the snap-back table, colors, the readers' states, the master switch, the dry run, the triggers, the
client's backoff and the wiring.

### Time off read in from Google (C4, v1.606.0)

People already put their own time off on **Company Holidays & Vacations** by hand ("Austin Off", "Clegg -
ooo"). `calendar_sync/time_off.py` reads those events into the planners as **Unavailable Planner Blocks**
(Nik, 2026-10-12, settled). Off by default: Calendar Sync Settings → *Read time off from Google*, which
also needs the master switch, an authorized account and the calendar ID.

- **Reading**: C2's client, `events.list` with `singleEvents=true`. Every 10 minutes from its own
  `syncToken` (`time_off_sync_token`; snap-back's query differs, and Google ties a token to its query);
  nightly the whole window again, decisions re-applied, and imported blocks whose event has gone removed
  (only after a complete listing). Re-drivable after the deploy's `FLUSHDB`.
- **Matching** (`time_off_rules.py`, pure, read its docstring): ERPNext's own events (C2's private
  properties, any site's, or its footer) are never read; birthdays skipped; a planner's decision (event
  id, then title); "Company holiday" is everyone; a first name / full name / alias (*Names on the shared
  calendar* on Planner Resource) **plus** a time-off word; with no name, a time-off word and the creator's
  (then organizer's) address → User → active Planner Resource; anything else goes to review.
- **Blocks**: one per person per day, all day or the slot (cut at midnight in the site's zone), `source`
  "Google", `google_event_id`, a private note "From Google Calendar: <title>". Each read writes only the
  difference, inside the window. **Hand-made blocks are never read, changed or deleted**: every query
  filters `source = "Google"` and the update and delete paths re-check the row. `source` defaults to
  "Planner" and is on a normal doctype, so every block that existed before v1.606.0 reads "Planner".
- **Loop guard**: an imported block is never synced back out (`sources.block_item` reads it as gone,
  `refs_in_window` skips it, `triggers.on_change` does not queue it), is read-only (`PlannerBlock`;
  a System Manager may still delete one) and is shown on both planners without the edit form.
- **Company holiday**: a block for every active staff resource (not subcontractors), except where their
  Holiday List (what the engine already reads) already has the day. The Holiday List is never written.
- **Review list** (`Calendar Time Off Review`): Assigned + person, Everyone, or Ignored; kept per event id,
  and per title with *Same for this title*. Applied after commit from the event stored on the row.
- **Alerts**: the *Time off alerts* users (else whoever authorized the account) get a bell when events
  wait for review, and when newly imported time off lands on firm bookings, once per block
  (`time_off_alerted`), by the Conflict center's own check (`planner_blocks._conflicts_made`).
- **Preview** (*Time off → Preview time off*): one read-only listing; creates, updates, deletes and the
  review list, with **no write anywhere**.

`tests/test_calendar_time_off.py` (bench-free, own CI step) pins the matching, the creator fallback, that
ERPNext's own events are ignored, block create/update/delete, hand-made blocks untouched, the loop guard,
the review list, one alert per block, no read with a switch off, and a dry run that writes nothing.
### Route times and leave-by (C3, v1.605.0)

C2 writes every item all-day. C3 gives **route stops real times** (Nik's decisions, 2026-10-12):

- **Which events are timed.** A single-day, firm (not pencil), active task, visit or projected visit in
  the **near window** (Calendar Sync Settings `timed_days_ahead`, default 14 days, today included; 0 turns
  timing off) that is a stop on its timing person's route that day. It becomes a `dateTime` event in the
  site's time zone (System Settings, else America/Denver), on site from the route's arrival to its
  departure. **Times are computed on read and never stored on the task or visit.**
- **Where the times come from.** `route_slots.py` runs the planner's own engine call
  (`crew_availability._compute`, the one `get_route` makes) and the route view's own two passes
  (`routing.route_times`, `merge_unlocated`, `route_times`), so the calendar and the route view agree stop
  by stop (a test compares them). Leave the shop at Project Planner Settings `day_start_time`, drive each
  leg, work the booked hours, wait for a slotted stop. Drive padding (`pad_drive_time`) only decides whether
  driving counts against a person's hours; it does not move the clock.
- **What stays all-day:** multi-day tasks, full-day work (a task with no estimate, no slot, no rental kind
  and no crew hours; a full-day visit), day notes, blocks, pencils (never routed), a day past the near
  window, and a day whose route failed (logged once an hour). Projected visits are routed by the planner,
  so they are timed, and stay tentative.
- **Past days and finished work keep what they had**: the times last written are stored on the
  `Calendar Sync Link` (`timing`, JSON) and reused while the item is on that day, so a ✓ leaves the slot
  alone and the nightly repair never prices a route in the past.
- **"Leave by 7:35 am"** leads the description of the first stop of the timing person's day: the arrival
  less the drive. Google reminders are not used: they belong to the account that writes the events.
- **One event per task, timed from the lead's route** (a judgement call Nik can revisit): no lead, the
  first crew member; a lead with no route that day, the next who has one. When another crew member's slot
  differs the description adds "Times are <name>'s route (times differ by person)". A visit's technician is
  its lead.
- **A change re-times the whole day.** An item job queues `engine.run_day_job` for each day it touched
  (now and before), after commit, `job_id` `calendar_sync::day::<date>` with `deduplicate`; a running day
  job allows one more (`::again`). A publish of thirty tasks on one day is one or two day jobs, never
  thirty squared; each re-time is hash-checked, so an unchanged stop costs no Google call.
- **Bounded Google Routes cost.** Only the near window is ever routed: the nightly repair primes it in one
  engine call, an item job works out only its own day, the preview asks Google nothing. Legs come from the
  Planner Drive Time cache; Google is asked only for a missing leg and only where the planner would.
- **Leave-by ping** (`leave_by.py`, cron every five minutes). Each person who ticks *Ping me at leave-by
  time* on their Planner Resource gets one notice *Ping this many minutes before leave-by* (default 10)
  before they must leave for the first stop of their day, while Project Planner Settings *Leave-by pings*
  is on (both off by default). Bell, then a text, or an email without a cell number (`planner_notices`'
  rules). Never for a pencil, a day with no hours (day off, holiday, time off, all-day block), a travel day
  or an unlocated first stop. At most once per person per stop per day through a `Planner Ping Log` row
  claimed before sending, so the deploy's FLUSHDB cannot re-send. It does **not** follow the change alerts
  switch (that governs messages nobody asked for; this is asked for per person), does not need Calendar
  Sync on, and asks Google nothing (cached legs and the estimate).

`tests/test_calendar_route_timing.py` (bench-free, own CI step, reusing C2's stubs) pins all of it.
### Cutover from the hand-made Maintenance calendar (C5, v1.607.0)

Before the sync, the maintenance schedule was kept by hand on the shared **Maintenance** calendar: one
recurring series per site, with occurrences dragged around by hand, and descriptions holding site
knowledge written down nowhere else (Google Sites links, special instructions, gate codes). Nik's plan
(2026-10-12, settled): copy that knowledge into the Maintenance Profiles **through a review list**, fill a
missing site address the same way, **end the hand-made series at the cutover** (past occurrences stay as
history, future ones stop), and switch the sync on by hand. Deleting the old "ERPNext Tasks / Project /
ToDo" calendars is Nik's job too. Everything is in `calendar_sync/cutover.py`, behind
`api/calendar_cutover.py` (System Manager only, POST only), and the page **is the Calendar Cutover Item
list** (*Cutover* menu; also *Sync → Cutover from the old Maintenance calendar* on the Settings form).

1. **Scan** (read-only on Google; needs an authorized account and the Maintenance calendar ID, not the
   switch). One `events.list` with `singleEvents=false`, so each series comes back as its **master with
   its RRULE**. Every event with C2's private `erpnext_*` properties (any site) is skipped; single events,
   moved occurrences (`recurringEventId`) and series that already ended before today are counted, not
   listed. Each running hand-made series becomes a **Calendar Cutover Item** (`CCI-#####`, `series_id`
   unique): its title, recurrence lines, first date, time zone, location and moved-occurrence count, and
   the proposals — the profile, the Sites links (Google's `google.com/url?q=` wrapper unwrapped), the
   description as text (read with `HTMLParser`, never a regex; the links taken out), any codes (C2's own
   `payload._SENSITIVE` rule) and, only when the matched project has no `custom_project_address`, the
   event's location **scrubbed of codes** (a project address becomes the location of its events).
   **A rescan refreshes Pending rows only**; an Approved, Applied or Ignored row keeps every proposal,
   and a profile picked by hand is kept even on a Pending row. A row whose series vanished from the
   calendar is flagged, never deleted.
2. **Matching** (`match_profile`, explained in the row's *Why this profile*). Names are compared as word
   sets without stop words (cadence, "fountain", "maintenance", legal suffixes, years) against the project
   name, its short site name, the customer and the project id: same words 100, the title holds every word
   of the name 90, the reverse 80, otherwise a share of 75 by overlap. A matching street address
   (house number + first street word, so "3000 E 1st Ave" = "3000 East 1st Avenue") scores 85, and adds
   10 when the name agrees too. **Matched** at 60 or more with no other profile within 10; **Ambiguous**
   (nothing proposed, the close ones named) when one is; **Unmatched** otherwise (the nearest named).
3. **Review**: *Approve* (needs a profile), *Ignore* (copy nothing), *Back to Pending*, per row on the form
   or ticked rows in the list. `status` is read-only on the form. *End this series at cutover* is a
   separate tick: Ignore alone does not keep a series running.
4. **Apply approved** (shows what it would write, then writes): on the profile's new *Site links and
   special instructions* section, the links are added to **Google Sites links** one per line and never
   twice, the instructions are **appended** to **Special Instructions** under "From the old Maintenance
   calendar (“title”, copied date)" after a blank line, never over what is there and never twice; codes
   go to **Access Codes** after " | " when they fit its 140 characters, else into the appended block. The
   address is written to the project **only if it is still empty**, then `workforce.sites.geocode_project`
   is queued. A comment on the profile says where the text came from (never the codes). Rows become
   Applied; one failure leaves its row Approved with the error shown.
5. **End the old series** (the only Google write). Pick the cutover date (today or later: an earlier one
   would erase occurrences that already happened). The **dry run** lists, per reviewed row still ticked,
   the rule now and the rule after, what is left alone and why (Pending, unticked, already ended, an
   invitation copy organized elsewhere, an unreadable rule), moved occurrences after the cutover, and any
   running series not on the review list. **Confirming re-plans and refuses unless the plan's token is
   the one the dry run showed**, and needs the master switch on, an authorized account and the Maintenance
   calendar ID. It sends `events.patch` with only `recurrence` and `sendUpdates=none` to each series
   master: never a delete, never a single event, never an ERPNext event (those are never series masters
   here, whatever a row says). Each change is logged in **Calendar Cutover Log** with the original lines.
6. **Undo end** (dry run, then confirm with its token) patches the original lines back, but only on a
   series whose rule is still the one we wrote; one changed in Google since is skipped and its original
   shown. It needs the account, **not** the switch: it is the way back when the sync itself is the problem.

**RRULE handling** (`rewrite_recurrence`, pure): an open rule gets `UNTIL` appended; an `UNTIL` on or
after the cutover is moved back, one already before it is left alone; `COUNT` is replaced by `UNTIL` (RFC
5545 forbids both) **only when Google's `instances` list shows an occurrence whose original day is on or
after the cutover**, because swapping the COUNT of a rule that already ran out would extend it. Every
other part keeps its place; EXDATE and EXRULE lines are kept; RDATE dates on or after the cutover are
dropped. `UNTIL` matches the start's type, as Google requires: the day before the cutover for an all-day
series, and for a timed one the UTC instant one second before local midnight of the cutover in the
event's time zone (so DST is right: `20261020T055959Z` in October, `20261102T065959Z` in November). A
rule with several RRULE lines, or a timed one with no usable time zone, is not touched.

**Moved occurrences.** Ending a series does not delete the occurrences people dragged in Google (they are
separate events). The dry run counts the ones after the cutover per series; check them by hand.

**Codes stay inside.** The profile is internal, so codes may land there; C2's readers and event builder
never name `special_instructions`, `google_sites_links` or `access_codes`, and the test fails the build if
one starts to.

`tests/test_calendar_cutover.py` (bench-free, own CI step, fake Google client) pins all of the above,
including the two dialogs built under node through a stand-in that asks every `get_data`/`get_query`
while they are being constructed.

## `hooks.py` touchpoints

- `doc_events`: Project `after_save` → `sync_attachments_from_opportunity`; Project/Task `on_update` → `…project_dashboard.publish_realtime_update`.
- `doc_events["Task"]`: `on_update` → `crew_sync.on_task_update` (crew rows → assignments) and `validate` → `crew_sync.validate_crew` (Project Planner, v1.577.0), then `planner_tracking.validate_equipment` (one row per vehicle/asset, label filled, v1.582.0) and `customer_confirmations.keep_stamps`; `on_update` also runs `customer_confirmations.on_task_update` before `crew_sync`; `before_insert` → `task_templates.copy_template_planning` (v1.583.0).
- `scheduler_events` (every 10 minutes) → `customer_confirmations.send_due_confirmations`, a no-op while the switch is off (v1.583.0).
- `scheduler_events.daily` → `routing.backfill_coordinates` (geocode task and venue addresses the routes need, v1.578.0).
- `scheduler_events.daily` → `send_project_start_reminders`.
- `scheduler_events.daily_long` → `planner_tracking.refresh_labor_forecasts` (the nightly refresh of the Labor line's stored `labor_forecast`, no Google calls, never saves the Project; Phase 4 follow-up).
- Calendar sync (C2, v1.604.0): `doc_events` Task (`on_update`, `on_trash`), Sapphire Maintenance Record (`on_update`, `on_submit`, `on_cancel`, `on_update_after_submit`, `on_trash`), Planner Day Note and Planner Block (`on_update`, `on_trash`) → `calendar_sync.triggers.on_change`; Sapphire Maintenance Contract `on_update` → `on_contract_change`; Project `on_update` → `on_project_change`; `scheduler_events` `*/10` → `calendar_sync.engine.snapback`; `daily_long` → `calendar_sync.engine.nightly_repair`. All no-ops while Calendar Sync Settings is off.
- Calendar time off in (C4, v1.606.0): `scheduler_events` `*/10` → `calendar_sync.time_off.incremental_read`; `daily_long` → `calendar_sync.time_off.nightly_read`. No-ops unless *Read time off from Google* is ticked. The Planner Block trigger skips imported blocks.
- Calendar sync (C2, v1.604.0): `doc_events` Task (`on_update`, `on_trash`), Sapphire Maintenance Record (`on_update`, `on_submit`, `on_cancel`, `on_update_after_submit`, `on_trash`), Planner Day Note and Planner Block (`on_update`, `on_trash`) → `calendar_sync.triggers.on_change`; Sapphire Maintenance Contract `on_update` → `on_contract_change`; Project `on_update` → `on_project_change`; `scheduler_events` `*/10` → `calendar_sync.engine.snapback`; `daily_long` → `calendar_sync.engine.nightly_repair`. All no-ops while Calendar Sync Settings is off. C3 (v1.605.0) adds no `doc_events`: an item job queues `calendar_sync.engine.run_day_job` itself.
- Leave-by pings (C3, v1.605.0): `scheduler_events` cron `4-59/5 * * * *` (its own key, every five minutes) → `leave_by.send_leave_by_pings`, a no-op while Project Planner Settings *Leave-by pings* is off.
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
