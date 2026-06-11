# Sapphire Maintenance

A field-service maintenance subsystem. Technicians fill out a **Maintenance Record** on site — a modular visit form instantiated from a **Template** that is *composed of reusable Sections* (chemical dosing, water chemistry, equipment inspection, cleaning tasks). On submit, the record drives downstream ERP automation — Stock Entry, Timesheet, Warranty Claim, and a draft Sales Invoice — and is exposed to customers through the portal.

Visits are scheduled by the **Maintenance Contract**, the operational counterpart of two existing documents: the signed **Project Contract** (Maintenance Services Agreement — legal terms, service options, access info) and the **Sales Order** (the commercial source per-visit invoices are drawn against). Contracts are built for fast fill-out: a **Service Plan** preset stamps the standard offering in one pick, a batch dialog adds all of a project's water features at once, and the standard seasonal pair (startup/winterization) are two checkboxes.

Technicians fill visits through the **Visit Wizard** (`/app/visit-wizard`) — a guided, touch-first desk page (steppers, segmented buttons, tap checklists; no grids) that reads and writes the same Maintenance Record, so the desk form stays the supervisor/review surface and all automation is shared.

The module ships its own workspace and `module_def`. Notifications, the print format, and the portal route are installed as fixtures (see [`../hooks.py`](../hooks.py)).

## Data model

```
Sapphire Maintenance Section (parent)            reusable form building block, typed. Also holds
│                                                step_instructions (Text Editor how-to) shown in the
│                                                Visit Wizard's collapsible per-step panel.
├── Sapphire Section Item (child, istable)        Chemical Dosing | Water Chemistry | Equipment Inspection | Cleaning Tasks
│                                                  one line: label (+item / uom+min+max / options+mandatory by type)
└── Sapphire Section Image (child, istable)       step_images: how-to photo (Attach Image) + caption

Sapphire Maintenance Template (parent)           a visit form = ordered composition of Sections. Also
│                                                holds visit-type Safety/Wrap-up step guidance
│                                                (safety_instructions/wrapup_instructions Text Editors
│                                                + safety_images/wrapup_images — Sapphire Section Image)
└── Sapphire Template Section (child, istable)     link to a Section (idx = order) + the step's on-site
                                                   location: location_note, location_photo, lat/lng
                                                   (wizard shows 📍 line + tap-to-navigate Map link)

Sapphire Service Plan (master)                   one-pick contract preset: default frequency/template,
                                                 visit shape, invoicing cadence, seasonal startup/
                                                 winterization defaults. STAMP semantics: picking a plan
                                                 copies values onto the contract; later plan edits never
                                                 ripple to contracts that already applied it.

Sapphire Maintenance Contract (parent)           operational contract: who/what/when/how billed
│   customer, project, service_plan, default_frequency (materializes onto blank feature rows in
│   validate), project_contract (legal), sales_order (commercial), status, visit_shape (Per Feature |
│   Per Site Visit), default_template, invoicing_frequency. The standard seasonal pair are FLAT fields:
│   seasonal_startup/startup_month/startup_template (+ winterization trio) with hidden
│   *_last_generated_year stamps — iter_seasonal_visits() unifies them with the custom rows below.
├── Sapphire Contract Feature (child, istable)     covered Serial No: frequency, last/next visit dates
│                                                  (scheduler state); template + chemical warehouse
│                                                  overrides folded into a collapsed row section
└── Sapphire Seasonal Visit (child, istable)       CUSTOM annual visits beyond the standard pair: label,
                                                   template, target_month, last_generated_year

Sapphire Maintenance Record (parent, submittable, route: maintenance-records)
│   the on-site visit: Customer/Project/Contract/Serial No/Technician, clock in/out, paused_duration,
│   safety_acknowledged gate, client_sign_off, warranty_rma_flag, has_out_of_range_readings,
│   sales_invoice, workflow_state. Per Site Visit contracts leave serial_no empty — child rows are
│   tagged with their feature's serial_no instead (child tables can't nest). All four row types carry
│   section_title (render grouping) and is_mandatory (submit gate, except consumables).
├── Sapphire Maintenance Result (child, istable)        inspection row: question, selection (Data —
│                                                       standard Pass/Fail/Replace/Other or the row's
│                                                       custom options from the template)
├── Sapphire Chemistry Reading (child, istable)         reading vs target range; out_of_range computed in validate
├── Sapphire Cleaning Task (child, istable)             done / not done
└── Sapphire Maintenance Consumable (child, istable)    dosing row: item (+item_name/uom for display),
                                                        warehouse, qty (prefilled 0), default_qty/qty_step
                                                        (wizard stepper hints)

Sapphire Maintenance Profile          one per Project (unique): safety_instructions, access_codes
Sapphire Reading Range Override       child table on Serial No (custom_reading_overrides):
                                      per-feature chemistry target ranges, matched by reading label
```

Configuration lives in **ERPNext Enhancements Settings → Maintenance**: fee item, services item group, consumables item group (item-picker filter), default consumables warehouse, and the water-feature Item (Serial No picker filters). **No item codes are hardcoded anywhere in this module** — items travel as Link fields, which survive renames.

## File map

| File | Purpose | Key functions |
|---|---|---|
| `doctype/sapphire_maintenance_record/sapphire_maintenance_record.py` | Record controller + portal helpers | `validate` (reading ranges), `on_submit`, `get_context`, whitelisted `get_visit_payload`, `get_dashboard_context`, `get_historical_visits` (last 5 visits for the `historical_visits` HTML field — not a child table); module-level `evaluate_reading_ranges`, `resolve_template` |
| `doctype/sapphire_maintenance_record/sapphire_maintenance_record.js` | Desk form: safety gate, template instantiation, in-form dashboard | `toggle_safety_gate`, `populate_from_template`, `render_dashboard`, `render_historical_visits` |
| `doctype/sapphire_maintenance_record/sapphire_maintenance_record.html` | Portal/print format ("Maintenance Record Print") | header, Service Duration, checklist, chemistry, cleaning tasks, consumed consumables, sign-off |
| `doctype/sapphire_maintenance_contract/sapphire_maintenance_contract.py` | Contract controller + mapped-doc creators | `validate` (one Active per project; materializes default frequency/next-visit onto blank rows), `iter_seasonal_visits` (flat + custom rows, one shape), whitelisted `make_contract_from_sales_order`, `make_contract_from_project_contract`, `get_project_water_features` (batch-add dialog), `get_active_contract` |
| `doctype/sapphire_maintenance_contract/sapphire_maintenance_contract.js` | Contract form: fast fill-out | `service_plan` stamp handler, `default_frequency` apply-to-rows, batch "Add Water Features" dialog, row-add defaults |
| `doctype/sapphire_service_plan/…` | Service Plan master (preset stamped by the form JS) | (no custom logic) |
| `page/visit_wizard/visit_wizard.js` | Visit Wizard desk page (tech touch-first flow) | step renderers (safety/chemistry/steppers/inspection/cleaning/wrap-up), per-feature tabs, autosave, signature pad |
| `../api/maintenance_visit.py` | Wizard backend | `get_visit_bootstrap` (load + server-side template instantiation), `save_visit` (allowlisted patch + optimistic lock), `finish_visit` (workflow-aware) |
| `doctype/sapphire_maintenance_section/…py` / `.js` | Section controller / type-driven grid columns | `validate` (dosing rows need an Item; min ≤ max) |
| `doctype/sapphire_maintenance_template/…py` | Template controller | (no custom logic) |
| other `doctype/…` children | istable stubs | — |

Buttons creating contracts live on the source forms — three entry points for three realities:

| You have… | Button | Prefills |
|---|---|---|
| a submitted **Sales Order** | SO → Create → Maintenance Contract (`public/js/sales_order_enhancements.js`) | features + frequencies from the order's water-feature items |
| a Signed **Maintenance Services Agreement** | Project Contract → Create → Maintenance Contract (`project_contract.js`) | frequency, invoicing cadence, seasonal options, start date |
| **only a Project** (verbal/legacy arrangement) | Project → Create → Maintenance Contract (`public/js/project.js`) | customer + covered features from the project's Serial Nos (`custom_project`), Active template; links an SO/agreement if one happens to exist |

Each path back-fills the other links when the documents exist; none of them are required. A project that already has an Active contract shows a jump-to button instead. And the floor below all of this: an **Active template scoped to the Project/Customer alone** is enough for techs to fill forms — contracts only add scheduling, visit shape, and invoicing behavior.

## Lifecycle

**Authoring:** compose Sections once, reuse them across Templates. Assign templates per contract feature (falling back to the contract default, then the project/customer Active template).

**Contract fill-out** (built for "3 clicks or less" per input): pick a **Service Plan** — frequency, template, visit shape, invoicing and the seasonal checkboxes stamp in one dropdown; tap **Add Water Features** — the dialog lists the project's serials pre-checked with one shared frequency and first-visit date; per-feature tweaks stay single cell edits. New rows inherit the contract's Visit Frequency and anchor to the Start Date; `validate` backfills whatever arrives blank from any path, so the scheduler never sees a row it can't schedule. A 12-fountain contract is ~10 interactions end to end.

**Scheduling:** the daily job `tasks.predictive_maintenance_scheduling` reads Active Maintenance Contracts — Per Feature shape drafts one record per due feature, Per Site Visit drafts one per site, seasonal visits (flat checkboxes + custom rows, via `iter_seasonal_visits`) draft once a year in their target month. Drafts are bare headers; the form instantiates its tables from the template on first open (`get_visit_payload` — the wizard does this server-side in `get_visit_bootstrap`). Projects without an Active contract fall back to the legacy Sales Order Item cadence fields.

**Visit Wizard** (`/app/visit-wizard?record=…`, or no param for the picker): Safety briefing + PPE acknowledge gate → Water Chemistry (numeric cards with range chips; out-of-range turns red with the server's verdict on autosave) → Chemicals Used (pre-listed from the template; `[−] qty [+]` steppers stepping by `qty_step`, first tap jumps to `default_qty`; ad-hoc item add) → Inspection (segmented buttons honoring custom per-question options; Fail/Replace reveal notes + photo) → Cleaning (tap checklist) → Wrap-up (notes + dictation, signature pad, workflow-aware Finish). Per Site Visit records get sticky per-feature tabs. Steps autosave with optimistic locking (`api/maintenance_visit.py`); a stale desk-side edit rejects the write instead of losing it.

The wizard's no-param picker shows two lists: **Today's Visits** (open drafts, `get_my_visits_today`) and **Upcoming — do one early** (`get_upcoming_visits`): Active-contract features due in the next 8–30 days with no draft yet (Per Site Visit contracts collapse to one earliest-due site entry). Tapping **Do Visit Today** calls `create_visit_today`, which spins up a record dated today carrying the `EXTRA_VISIT_LABEL` ("Extra Visit") `visit_label` — a labelled visit, so `update_next_visit_dates` skips it: the pull-forward is an **extra one-off** and the feature's originally scheduled visit still fires on its own date.

**Per-step instructions:** each section-backed step shows a collapsible (collapsed by default) "ℹ️ How to do this" panel built from the Section's `step_instructions` (Text Editor) and `step_images` (photo + caption). `get_visit_bootstrap` returns this per-section meta keyed by the `section` link every visit row carries; the wizard groups a step's rows by section, leading each group with its panel (and a sub-header when a step draws from more than one section). Author once on the Section — it appears everywhere that section is used. The **Safety and Wrap-up steps** get the same treatment from two stacked sources: the Template's `safety_instructions`/`wrapup_instructions` (+ image tables) carry the visit-type guidance ("Before you start" / "Wrapping up" panels), and the Maintenance Profile's `wrapup_instructions` adds the site-specific reminders (the profile's safety text stays prominent in the red banner, never collapsed).

**Step locations:** each Template step row can carry where on the property the step happens — `location_note` ("Pump vault behind the NE hedge"), `location_photo`, and optional lat/lng. The wizard shows an always-visible 📍 line above the step's cards (with a tap-to-navigate Google Maps link when coordinates are set) and folds the spot photo into the step's collapsible panel. Locations live on the **template** (not the section library) because templates are customer/project-scoped — each site's form carries its own spots. The wizard's CSS uses Frappe theme variables throughout, so it reads correctly in Timeless Night; the kiosk's Today's Visits links are given an explicit `--tk-text` colour (a bare `<a>` was falling back to default-blue, invisible on the dark card).

**Time Kiosk integration:** clocking into a project with an Active contract (or Active template) surfaces a **Maintenance Form** button on the kiosk's active-job card (`api/time_kiosk.py::get_maintenance_context` — links the newest open draft *into the Visit Wizard*, else a prefilled new desk record, opened in a new tab so the clock keeps running). Clock-out and cross-project switches re-check the server and warn (non-blocking confirm) when the technician hasn't submitted a record for that project since clock-in. Field technicians need the native **Maintenance User** role (create/write/submit on the record, read on contracts/templates; the workflow's Draft state is editable by this role).

**Field conveniences:**
- *Today's Visits* — the idle kiosk lists the tech's open visit drafts (`get_my_visits_today`), one tap from each form.
- *Geofenced suggestion* — with site coordinates on the Maintenance Profile and a radius in Settings, the idle kiosk suggests the nearby project when a visit is due (`get_nearby_visit`).
- *Offline tolerance* — the kiosk service worker serves the last good kiosk API responses when offline; on the desk, the app-wide autosave (localStorage + User Form Draft) preserves a half-filled record through connection loss.
- *Clock auto-fill* — a record's blank `clock_in_time`/`clock_out_time`/`paused_duration` fill themselves from the technician's Job Interval on save/submit.
- *Per-row photos* (`photo` Attach Image on results/readings/tasks) and a *🎤 Dictate Note* button (Web Speech API → `visit_notes`).
- *SMS nudge* — hourly job texts a tech (Triton gateway) who clocked out of a maintenance project 1–4h ago without submitting a form (Settings toggle; one evaluation per interval via `Job Interval.maintenance_nudge_sent`).

**Supervision:**
- `completion_percent` on every record (answered rows / total rows; consumable prefills excluded) — list column + form indicator.
- *Maintenance Day Board* (`/app/maintenance-day-board`, roles: System Manager / Maintenance Supervisor / Projects Manager): scheduled drafts, techs clocked in, submitted today, flagged last-7-days; auto-refreshes every 60s (`api/maintenance_board.py`).
- *Chemistry trend sparklines* on the record dashboard — last 5 visits per reading, red dots out-of-range (`_chemistry_trends`).
- *Out-of-range follow-up* — Settings days > 0 drafts a "Chemistry Follow-Up" visit + technician ToDo on submit of a flagged record (deduped; labelled visits don't advance the regular cadence).

**Stock:** weekly *truck restock suggestions* (Settings toggle + source warehouse) draft one Material Transfer request per technician vehicle warehouse, replenishing the past week's consumption (`tasks.suggest_truck_restocks`).

**On submit**, `SapphireMaintenanceRecord.on_submit` enqueues [`api/maintenance_workflow.py::process_maintenance_submission`](../api/README.md) (background, "default" queue) which runs isolated steps:

- `create_stock_entry` — Material Issue for consumables with qty > 0 (untouched dosing prefills don't move stock); per-row warehouse falls back feature store → technician's vehicle (`Employee.custom_default_vehicle_warehouse`) → settings default.
- `create_timesheet` — labour = `clock_out − clock_in − paused_duration`; writes `total_labor_cost`.
- `check_warranty_and_rma` — Fail/Replace rows grouped per in-warranty feature → one native **Warranty Claim** each + `warranty_rma_flag`.
- `create_sales_invoice` — only when the contract bills **Per Visit**; draft SI from the fee item/services group (Settings) + consumed consumables, against the contract's Sales Order.
- `log_out_of_range_readings` — timeline Comment listing flagged readings.

Next-visit dates roll forward via the `on_submit` doc-event [`api/maintenance_scheduling.py::update_next_visit_dates`](../api/README.md): contract feature rows are the source of truth, with the dates mirrored to the Sales Order Item custom fields for legacy reports.

## Workflow, notifications, portal

- **Workflow state** is stored in `workflow_state`; the maintenance workflow's states/notifications ship as fixtures. Roles: techs (**Maintenance User**) edit Draft and Request Review; **Projects Manager** edits Pending Review and runs Approve & Submit (docstatus 1 — this is when the automation fires). The wizard's Finish button applies whichever action the session user holds.
- **Mandatory items:** section items flagged `is_mandatory` block submit while unanswered (`before_submit`); consumables are exempt (qty 0 = "none used").
- **Notifications:** "Maintenance Review Needed", "Maintenance Finalized", and "Maintenance Reading Out of Range" (fires on submit when `has_out_of_range_readings`; emails the **Maintenance Supervisor** role — assign it to a real user).
- **Print format:** "Maintenance Record Print" (the `.html` above; exported as a fixture).
- **Portal:** route `/maintenance-records` is registered for the **Customer** role (`portal_menu_items` in `hooks.py`); `get_context` sets `show_labor` from the Sales Order's `custom_display_labor_hours`.

## Seeding

`patches/seed_maintenance_sections.py` (insert-only, idempotent) creates the four sample Sections (chemicals existence-guarded by item code *at seed time only*), three Draft Templates (Standard Fountain Maintenance, Seasonal Startup, Winterization), the Maintenance Supervisor role, and Settings defaults. `patches/seed_service_plans.py` (same model) seeds the four standard Service Plans (Weekly/Bi-Weekly/Monthly Full Service, Quarterly Inspection Only). `patches/seed_maintenance_catalog.py` (same model, v1.17.0) seeds the **expanded catalog** — nine more Sections (Advanced Water Chemistry, Pump & Filter Service, Lighting Inspection, Auto-Fill & Water Level, Algae & Water Clarity, Spring/Winter step lists, Interior Fountain Care, Safety & Electrical), six Templates (Spray Feature / Pondless / Interior Fountain / Large Display, plus full Spring Startup / Winterization), and five Plans (Weekly Spray Feature, Bi-Weekly Pondless, Monthly Interior Fountain, Monthly Large Display Per-Site, Seasonal Service Only). It seeds **no Chemical Dosing sections** — those need per-row Item links that differ by site, so dosing stays in the item-guarded original seed and the new templates reuse the existing "Chemical Dosing" section. Each new Section also gets example `step_instructions` (edit/replace in the UI and add how-to images via its Instruction Images table). `patches/remove_template_item_doctype.py` drops the superseded flat `Sapphire Template Item` child doctype. `patches/rename_maintenance_templates.py` (v1.17.0) renames legacy hash-named templates to their `template_name` after the doctype switched to `autoname: field:template_name`.

## Gotchas

- A chemistry reading of **0 means "not measured"** — Float fields can't distinguish blank from zero, so zero values are never flagged out-of-range; a true zero reading belongs in the row's notes.
- Reading-range overrides on Serial No match section items **by label string**; renaming a reading label silently orphans its overrides.
- The historical-visits panel is an **HTML field** rendered from `get_historical_visits` — not a stored child table.
- Only one **Active** contract per project (validated); the scheduler auto-expires contracts past their `end_date`.
- A **Service Plan is a stamp, not a live link** — editing a plan later does *not* update contracts that already applied it. Re-pick the plan on a contract to re-stamp.
- Feature-row **frequency is materialized** (visible on the row, the scheduler reads it directly). Changing the contract's Visit Frequency offers "apply to all rows"; there is no hidden inheritance at runtime.
- The seasonal visit labels **"Seasonal Startup"** and **"Winterization"** are load-bearing strings (draft dedup + cadence-skip key on them) — constants in the contract controller, never reword.
