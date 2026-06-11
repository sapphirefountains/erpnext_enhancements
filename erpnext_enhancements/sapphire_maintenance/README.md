# Sapphire Maintenance

A field-service maintenance subsystem. Technicians fill out a **Maintenance Record** on site — a modular visit form instantiated from a **Template** that is *composed of reusable Sections* (chemical dosing, water chemistry, equipment inspection, cleaning tasks). On submit, the record drives downstream ERP automation — Stock Entry, Timesheet, Warranty Claim, and a draft Sales Invoice — and is exposed to customers through the portal.

Visits are scheduled by the **Maintenance Contract**, the operational counterpart of two existing documents: the signed **Project Contract** (Maintenance Services Agreement — legal terms, service options, access info) and the **Sales Order** (the commercial source per-visit invoices are drawn against).

The module ships its own workspace and `module_def`. Notifications, the print format, and the portal route are installed as fixtures (see [`../hooks.py`](../hooks.py)).

## Data model

```
Sapphire Maintenance Section (parent)            reusable form building block, typed:
└── Sapphire Section Item (child, istable)         Chemical Dosing | Water Chemistry | Equipment Inspection | Cleaning Tasks
                                                   one line: label (+item / uom+min+max / options+mandatory by type)

Sapphire Maintenance Template (parent)           a visit form = ordered composition of Sections
└── Sapphire Template Section (child, istable)     link to a Section (idx = order)

Sapphire Maintenance Contract (parent)           operational contract: who/what/when/how billed
│   customer, project, project_contract (legal), sales_order (commercial), status,
│   visit_shape (Per Feature | Per Site Visit), default_template, invoicing_frequency
├── Sapphire Contract Feature (child, istable)     covered Serial No: frequency, template, chemical
│                                                  warehouse, last/next visit dates (scheduler state)
└── Sapphire Seasonal Visit (child, istable)       annual visit (startup/winterization): label, template,
                                                   target_month, last_generated_year

Sapphire Maintenance Record (parent, submittable, route: maintenance-records)
│   the on-site visit: Customer/Project/Contract/Serial No/Technician, clock in/out, paused_duration,
│   safety_acknowledged gate, client_sign_off, warranty_rma_flag, has_out_of_range_readings,
│   sales_invoice, workflow_state. Per Site Visit contracts leave serial_no empty — child rows are
│   tagged with their feature's serial_no instead (child tables can't nest).
├── Sapphire Maintenance Result (child, istable)        inspection row: question, selection (Pass/Fail/Replace/Other)
├── Sapphire Chemistry Reading (child, istable)         reading vs target range; out_of_range computed in validate
├── Sapphire Cleaning Task (child, istable)             done / not done
└── Sapphire Maintenance Consumable (child, istable)    dosing row: item, warehouse, qty (prefilled at 0)

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
| `doctype/sapphire_maintenance_contract/sapphire_maintenance_contract.py` | Contract controller + mapped-doc creators | `validate` (one Active per project), whitelisted `make_contract_from_sales_order`, `make_contract_from_project_contract`, `get_active_contract` |
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

**Scheduling:** the daily job `tasks.predictive_maintenance_scheduling` reads Active Maintenance Contracts — Per Feature shape drafts one record per due feature, Per Site Visit drafts one per site, seasonal rows draft once a year in their target month. Drafts are bare headers; the form instantiates its tables from the template on first open (`get_visit_payload`). Projects without an Active contract fall back to the legacy Sales Order Item cadence fields.

**Time Kiosk integration:** clocking into a project with an Active contract (or Active template) surfaces a **Maintenance Form** button on the kiosk's active-job card (`api/time_kiosk.py::get_maintenance_context` — links the newest open draft, else a prefilled new record, opened in a new tab so the clock keeps running). Clock-out and cross-project switches re-check the server and warn (non-blocking confirm) when the technician hasn't submitted a record for that project since clock-in. Field technicians need the native **Maintenance User** role (create/write/submit on the record, read on contracts/templates).

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

- **Workflow state** is stored in `workflow_state`; the maintenance workflow's states/notifications ship as fixtures.
- **Notifications:** "Maintenance Review Needed", "Maintenance Finalized", and "Maintenance Reading Out of Range" (fires on submit when `has_out_of_range_readings`; emails the **Maintenance Supervisor** role — assign it to a real user).
- **Print format:** "Maintenance Record Print" (the `.html` above; exported as a fixture).
- **Portal:** route `/maintenance-records` is registered for the **Customer** role (`portal_menu_items` in `hooks.py`); `get_context` sets `show_labor` from the Sales Order's `custom_display_labor_hours`.

## Seeding

`patches/seed_maintenance_sections.py` (insert-only, idempotent) creates the four sample Sections (chemicals existence-guarded by item code *at seed time only*), three Draft Templates (Standard Fountain Maintenance, Seasonal Startup, Winterization), the Maintenance Supervisor role, and Settings defaults. `patches/remove_template_item_doctype.py` drops the superseded flat `Sapphire Template Item` child doctype.

## Gotchas

- A chemistry reading of **0 means "not measured"** — Float fields can't distinguish blank from zero, so zero values are never flagged out-of-range; a true zero reading belongs in the row's notes.
- Reading-range overrides on Serial No match section items **by label string**; renaming a reading label silently orphans its overrides.
- The historical-visits child table is **virtual** (computed on read, not stored).
- Only one **Active** contract per project (validated); the scheduler auto-expires contracts past their `end_date`.
