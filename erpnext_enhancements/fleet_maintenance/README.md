# Fleet Maintenance

Tracks routine maintenance for the company **vehicle fleet** — the weekly / 3-month /
6-month service cadence from the fleet maintenance schedule. A **Fleet Vehicle**
master holds each vehicle plus the "last done" date for every cadence; a submittable
**Vehicle Maintenance Log** records each service and, on submit, rolls the vehicle's
matching last-done date forward and recomputes when the next one is due. A nightly
job ages the **maintenance status** (OK → Due Soon → Overdue) and notifies fleet
managers when a vehicle slips.

The daily "check the gas, refill if ≤ half" task is a standing driver instruction
(documented on the in-desk **Fleet Maintenance Schedule** page and in
`docs/FLEET_VEHICLE_MAINTENANCE.md`), not a per-vehicle log — it's too high-frequency
to be worth a record.

## Design decisions (the "why")

- **A self-contained `Fleet Vehicle` master, not ERPNext's native `Vehicle`.** Native
  Fleet Management lives in HRMS, which this app treats as optional (see the
  HRMS-optional work in v1.77.0 / v1.133.2). A custom master has no such dependency
  and carries exactly the fields the schedule needs.
- **One log with a `maintenance_type` selector, not a doctype per cadence.** The type
  drives which standard checklist loads and which last-done date rolls forward. Crew
  learn one form.
- **Last-done dates are the source of truth; due dates + status are derived.**
  `status.compute_derived` sets each `*_due_date = last_* + interval` and the headline
  status on every save, so a manually seeded baseline lights up immediately.
- **Logs supersede, they don't clobber.** `recompute_vehicle_status` sets a cadence's
  last-done from its latest *submitted* log; a cadence with no log keeps its hand-seeded
  value. So nagging for a cadence only begins once a baseline exists for it.
- **Checklists are a server-side constant, not a template doctype.** The schedule's
  items are fixed; `checklists.py` is the single source and the form fills the grid from
  it. Crew can still add ad-hoc rows on any one log. (If per-vehicle custom checklists
  are ever needed, this is the seam to grow a template doctype.)
- **The whole automation is gated OFF by default.** The forms always work; the nightly
  refresh + reminders stay dormant until **ERPNext Enhancements Settings → Fleet
  Maintenance** is switched on.

## Data model

```
Fleet Vehicle                         (master, autoname field:vehicle_name, track_changes)
  ├── status                          Active / In Shop / Retired (Retired = not nagged)
  ├── make / model / year / license_plate / vin / color / fuel_type / assigned_driver
  ├── current_odometer                only ever moves forward (max of seed + submitted logs)
  ├── maintenance_status              read-only: No Data / OK / Due Soon / Overdue (derived)
  └── per cadence:  last_<x>_date (editable, seedable)  →  <x>_due_date (read-only, derived)
        weekly_service · oil_change · dealership_checkup · wiper_change

Vehicle Maintenance Log               (submittable, naming VML-.YYYY.-.#####, track_changes)
  ├── vehicle → Fleet Vehicle
  ├── maintenance_type                Weekly / Oil Change (3-Month) / Dealership Check-Up
  │                                   (6-Month) / Windshield Wipers (6-Month) / Other / Repair
  ├── service_date · performed_by · odometer
  ├── checklist : Vehicle Maintenance Task   (child, istable — auto-filled from the type)
  │     └── task · status (OK / Action Needed / N/A) · notes · is_mandatory (blocks submit)
  └── issues_found → follow_up_notes · notes
```

## File map

| File | Purpose | Key functions / classes |
|---|---|---|
| `doctype/fleet_vehicle/fleet_vehicle.py` | Vehicle master controller | `FleetVehicle.validate` → `compute_derived` |
| `doctype/fleet_vehicle/fleet_vehicle.js` | Form: "Log Maintenance" button + status headline | — |
| `doctype/vehicle_maintenance_log/vehicle_maintenance_log.py` | Log lifecycle | `before_submit` (mandatory gate), `on_submit`/`on_cancel` → `recompute_vehicle_status`, odometer-rollback warning |
| `doctype/vehicle_maintenance_log/vehicle_maintenance_log.js` | Form: load checklist on type change (pristine-guarded) | `load_checklist` |
| `doctype/vehicle_maintenance_task/` | Checklist line (child table) | `pass` |
| `checklists.py` | Standard per-cadence checklists | `CHECKLISTS`, `get_default_checklist` (whitelisted) |
| `status.py` | Due-date + status engine, reminders | `get_intervals`, `compute_derived`, `recompute_vehicle_status`, `_notify_fleet_managers` |
| `tasks.py` | Daily scheduler entry | `refresh_fleet_status` |
| `setup_print_formats.py` | Printable checklist Print Format (after_migrate) | `ensure_fleet_print_formats` |
| `page/fleet_maintenance_schedule/` | In-desk schedule reference page | — |
| `workspace/fleet_maintenance/` | Desk workspace (shortcuts + cards) | — |

## Access

| Role | Fleet Vehicle | Vehicle Maintenance Log |
|---|---|---|
| System Manager | full | full (+ submit/cancel/amend) |
| Fleet Manager *(seeded)* | full | full (+ submit/cancel/amend) |
| Maintenance Manager *(stock)* | full | full (+ submit/cancel/amend) |
| Maintenance User *(stock)* | read | create / write / submit |

Reminders go to Fleet Manager + Maintenance Manager users, falling back to System
Managers when none are assigned.

## hooks.py touchpoints

- `scheduler_events["daily"]` → `fleet_maintenance.tasks.refresh_fleet_status` (gated by the settings switch).
- `after_migrate` → `fleet_maintenance.setup_print_formats.ensure_fleet_print_formats`.
- `modules.txt` → `Fleet Maintenance`.
- Settings: `fleet_*` fields on **ERPNext Enhancements Settings**; flags in [`../feature_flags.py`](../feature_flags.py).
- Patches: `seed_fleet_manager_role`, `default_fleet_reminders_on` (see [`../patches.txt`](../patches.txt)).
- Form scripts auto-load (app-owned `<doctype>.js`) — no `doctype_js` entry needed.

## Gotchas

- **Nagging needs a baseline.** A cadence with no last-done date (and no submitted log)
  is *never* flagged — it can't know an oil change is overdue without a prior date. Seed
  `last_*` on the vehicle, or log the first service, to start the clock per cadence.
- **Automation is OFF by default.** Enable **ERPNext Enhancements Settings → Fleet
  Maintenance** or the nightly status refresh / reminders never run. Due dates still
  compute on save regardless.
- **Assign the Fleet Manager role post-deploy.** It is seeded empty; until someone holds
  it (or Maintenance Manager), reminders fall back to System Managers.
- **Odometer only moves forward.** A log odometer lower than the vehicle's current reading
  warns (doesn't block) and never lowers `current_odometer`.
- **Cancelling a log re-derives from what remains.** `on_cancel` recomputes last-done from
  the remaining submitted logs; a cadence whose only log is cancelled keeps its last
  stored value (it is not nulled).
