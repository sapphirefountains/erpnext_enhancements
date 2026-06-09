# Sapphire Maintenance

A field-service maintenance subsystem. Technicians fill out a **Maintenance Record** (a checklist seeded from a reusable **Template**) on site; on submit, the record drives downstream ERP automation — Stock Entry, Timesheet, warranty RMA, and a draft Sales Invoice — and is exposed to customers through the portal.

The module ships its own workspace and `module_def`. Notifications, the print format, and the portal route are installed as fixtures (see [`../hooks.py`](../hooks.py)).

## Data model — Template → Record → Result

```
Sapphire Maintenance Template (parent)          reusable checklist, scoped to Customer/Project
└── Sapphire Template Item (child, istable)     one prompt: sequence, question, field_type, options, mandatory

Sapphire Maintenance Record (parent, submittable, route: maintenance-records)
│   the on-site visit: Customer/Project/Serial No/Technician, clock in/out, paused_duration,
│   safety_acknowledged gate, client_sign_off signature, warranty_rma_flag, sales_invoice, workflow_state
├── Sapphire Maintenance Result (child, istable)        answered row: question, selection (Pass/Fail/Replace/Other), answer
├── Sapphire Maintenance Consumable (child, istable)    part used: item, warehouse, qty, serial_and_batch_bundle
└── Sapphire Historical Visit (child, istable + virtual) computed: last 5 submitted visits for the Project

Sapphire Maintenance Profile          one per Project (unique): safety_instructions, access_codes
```

## File map

| File | Purpose | Key functions |
|---|---|---|
| `doctype/sapphire_maintenance_record/sapphire_maintenance_record.py` | Record controller + portal helpers | `historical_visits` (virtual table, cached), `on_submit`, `get_context`, whitelisted `get_template_items`, `get_dashboard_context` |
| `doctype/sapphire_maintenance_record/sapphire_maintenance_record.js` | Desk form: safety gate, checklist seeding, in-form dashboard | `setup`, `toggle_safety_gate`, `populate_checklist`, `render_dashboard` |
| `doctype/sapphire_maintenance_record/sapphire_maintenance_record.html` | Portal/print format ("Maintenance Record Print") | header + status badge, optional Service Duration, checklist, consumables, sign-off |
| `doctype/sapphire_maintenance_template/…py` | Template controller | (no custom logic) |
| `doctype/sapphire_template_item/…py` | Checklist-line child | (stub) |
| `doctype/sapphire_maintenance_result/…py` | Answered-row child | (stub) |
| `doctype/sapphire_maintenance_consumable/…py` | Consumable child | (stub) |
| `doctype/sapphire_historical_visit/…py` | Virtual history child | (stub) |
| `doctype/sapphire_maintenance_profile/…py` / `.js` | Site profile controller / placeholder form | (stub) |

## Lifecycle — what happens on submit

`SapphireMaintenanceRecord.on_submit`:

1. **Enqueues** [`api/maintenance_workflow.py::process_maintenance_submission`](../api/README.md) (background, "default" queue). That worker runs four independent, isolated steps:
   - `create_stock_entry` — Material Issue for the consumables.
   - `create_timesheet` — labour = `clock_out − clock_in − paused_duration`; writes `total_labor_cost`.
   - `check_warranty_and_rma` — if the Serial No is in warranty and any result is Fail/Replace → set `warranty_rma_flag` + draft a Material Request (unmatched parts fall back to item code `WARRANTY-RETURN-PENDING`).
   - `create_sales_invoice` — draft SI from the maintenance-fee item + consumables, using defaults from **ERPNext Enhancements Settings**.

   Per-step failures are logged + commented; the owner is notified via `publish_realtime`.
2. **Synchronously** calls [`api/maintenance_scheduling.py::update_sales_order_next_visit`](../api/README.md) to back-fill `custom_last_visit_date` / `custom_next_predictive_visit` on the originating Sales Order Item.

A daily scheduler job `tasks.predictive_maintenance_scheduling` (repo-root `tasks.py`) generates upcoming maintenance records from Sales Order Item cadence fields.

## Workflow, notifications, portal

- **Workflow state** is stored in `workflow_state`; the maintenance workflow's states/notifications ship as fixtures.
- **Notifications:** "Maintenance Review Needed" and "Maintenance Finalized" (fixtures).
- **Print format:** "Maintenance Record Print" (the `.html` above; exported as a fixture).
- **Portal:** route `/maintenance-records` is registered for the **Customer** role (`portal_menu_items` in `hooks.py`); `get_context` sets `show_labor` from the Sales Order's `custom_display_labor_hours`.

## Gotchas

- **Double-fire:** `update_sales_order_next_visit` runs twice on submit (once from the controller, once from the `Sapphire Maintenance Record` `on_submit` hook in `hooks.py`). It only writes dates, so it's idempotent, but the "Updated Sales Order…" msgprint shows from both paths.
- The historical-visits child table is **virtual** (computed on read, not stored).
- Warranty RMA item-matching falls back to the placeholder item code `WARRANTY-RETURN-PENDING` when a part name can't be matched.
