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
├── Sapphire Maintenance Consumable (child, istable)    dosing row: item, warehouse, qty (prefilled at 0)
└── Sapphire Historical Visit (child, istable + virtual) computed: last 5 submitted visits for the Project

Sapphire Maintenance Profile          one per Project (unique): safety_instructions, access_codes
Sapphire Reading Range Override       child table on Serial No (custom_reading_overrides):
                                      per-feature chemistry target ranges, matched by reading label
```

Configuration lives in **ERPNext Enhancements Settings → Maintenance**: fee item, services item group, consumables item group (item-picker filter), default consumables warehouse, and the water-feature Item (Serial No picker filters). **No item codes are hardcoded anywhere in this module** — items travel as Link fields, which survive renames.

## File map

| File | Purpose | Key functions |
|---|---|---|
| `doctype/sapphire_maintenance_record/sapphire_maintenance_record.py` | Record controller + portal helpers | `historical_visits` (virtual, cached), `validate` (reading ranges), `on_submit`, `get_context`, whitelisted `get_visit_payload`, `get_dashboard_context`; module-level `evaluate_reading_ranges`, `resolve_template` |
| `doctype/sapphire_maintenance_record/sapphire_maintenance_record.js` | Desk form: safety gate, template instantiation, in-form dashboard | `toggle_safety_gate`, `populate_from_template`, `render_dashboard` |
| `doctype/sapphire_maintenance_record/sapphire_maintenance_record.html` | Portal/print format ("Maintenance Record Print") | header, Service Duration, checklist, chemistry, cleaning tasks, consumed consumables, sign-off |
| `doctype/sapphire_maintenance_contract/sapphire_maintenance_contract.py` | Contract controller + mapped-doc creators | `validate` (one Active per project), whitelisted `make_contract_from_sales_order`, `make_contract_from_project_contract`, `get_active_contract` |
| `doctype/sapphire_maintenance_section/…py` / `.js` | Section controller / type-driven grid columns | `validate` (dosing rows need an Item; min ≤ max) |
| `doctype/sapphire_maintenance_template/…py` | Template controller | (no custom logic) |
| other `doctype/…` children | istable stubs | — |

Buttons creating contracts live on the source forms: `public/js/sales_order_enhancements.js` (submitted SO with water-feature items) and `project_enhancements/doctype/project_contract/project_contract.js` (Signed maintenance agreements).

## Lifecycle

**Authoring:** compose Sections once, reuse them across Templates. Assign templates per contract feature (falling back to the contract default, then the project/customer Active template).

**Scheduling:** the daily job `tasks.predictive_maintenance_scheduling` reads Active Maintenance Contracts — Per Feature shape drafts one record per due feature, Per Site Visit drafts one per site, seasonal rows draft once a year in their target month. Drafts are bare headers; the form instantiates its tables from the template on first open (`get_visit_payload`). Projects without an Active contract fall back to the legacy Sales Order Item cadence fields.

**Time Kiosk integration:** clocking into a project with an Active contract (or Active template) surfaces a **Maintenance Form** button on the kiosk's active-job card (`api/time_kiosk.py::get_maintenance_context` — links the newest open draft, else a prefilled new record, opened in a new tab so the clock keeps running). Clock-out and cross-project switches re-check the server and warn (non-blocking confirm) when the technician hasn't submitted a record for that project since clock-in. Field technicians need the native **Maintenance User** role (create/write/submit on the record, read on contracts/templates).

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
