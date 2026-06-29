# Travel Management

Crew-based trip planning, logistics and travel finance. A non-submittable **Travel Trip** hub document carries a crew of travelers, the booking segments (flights / lodging / ground / misc costs / personal-vehicle mileage), a day-by-day agenda with CRM outcome capture, and rolls everything into native **Expense Claims**, **Employee Advances** and **Vehicle Logs**. Redesigned ground-up in v1.15.0 (the old submittable + Workflow version is retired; its 2 production drafts were deleted by patch).

## Design decisions (the "why")

- **No Workflow, no submit.** Trips are edited *collaboratively* (admins and travelers alike) until Closed; a submittable doc would freeze mid-trip edits behind amend cycles. Lifecycle is a plain `status` Select: **Planning → Booked → In Progress → Completed → Closed**. In Progress/Completed auto-advance from the trip dates (daily job); Booked and Closed are manual. Closed locks the doc (controller check); `api.reopen_trip` is the coordinator escape hatch.
- **The parent has NO `employee` field — and must never get one.** Travelers live in the `travelers` child table; the Employee dashboard's Travel Trip count works because frappe's link-count filter falls back to the `Trip Traveler.employee` child column. A parent field named `employee` would silently zero that dashboard count.
- **Documents are created explicitly, never as save side-effects** — `travel_management/api.py` methods back the form's Create buttons. Claim dedupe is stamp-based (see below) so re-running is always safe.
- **Multi-currency is out of scope for v1**: all costs are company-currency; advances are created at exchange rate 1. Foreign receipts get entered converted.

## Data model

```
Travel Trip (parent, NOT submittable, autoname TRIP-.YYYY.-.#####)
│   status Planning/Booked/In Progress/Completed/Closed; booked_on/closed_on stamps
│   travel_for_doctype/travel_for_name  → Project | Opportunity | Lead | Customer
│   project (read-only mirror when travel_for is a Project) · customer (derived)
│   billable (default for new cost rows) · 8 read-only financial rollups
├── travelers        → Trip Traveler         (employee, own from/to dates, per-diem calc,
│                                             expense_claim/advance back-links + statuses,
│                                             reminder/nudge idempotency stamps)
├── flights          → Trip Flight           (airline → Supplier, PNR, cost block)
├── accommodations   → Trip Accommodation    (hotel → Supplier, confirmation, cost block)
├── ground_transport → Trip Ground Transport (typed links: supplier OR vehicle+vehicle_log;
│                                             Company Fleet rows forced company-paid)
├── other_costs      → Trip Expense          (misc: parking, tolls, fees; cost block)
├── mileage          → Trip Mileage          (personal vehicle only: distance × settings rate)
└── itinerary        → Trip Agenda           (related party dyn-link, location → Travel POI,
                                              visit_notes, outcome_doctype/outcome_name)

Travel POI        reusable Point of Interest (Geolocation field feeds the maps)
Travel Settings   Single: per-diem rate rules (Travel Per Diem Rate child), mileage rate,
                  Expense Claim Type mapping, auto-advance + notifications master switches
```

**Shared cost block** (identical on the four cost tables): `estimated_cost`, `cost`, `paid_by` (Company/Employee), `paid_by_traveler` (required+validated when Employee), `billable`, and the hidden `expense_claim` stamp.

## Money flow

- **Per diem** — per traveler, computed in `validate` from the traveler's own date range: `rate × (days−2) + 2 × rate × first_last_day_pct` (single day = one edge day). Rate comes from Travel Settings by `travel_type`, overridable per traveler. Frozen once claimed (`per_diem_claimed`); later date changes warn instead of silently recomputing.
- **Mileage** — `distance × rate` (settings default, row-overridable). Company fleet never goes here — it's a Ground Transport row + draft **Vehicle Log** (`api.create_vehicle_log`; HRMS validates odometer continuity on submit).
- **Expense Claims** — `api.create_expense_claim(s)` gathers, per traveler: employee-paid cost rows (by `paid_by_traveler`), unclaimed mileage, unclaimed per diem → one draft claim per traveler (extends an existing draft). Header gets `company`, `project`, `custom_travel_trip`; detail rows get real dates/descriptions and `project` when billable. **Throws a configuration error if an Expense Claim Type is unset in Travel Settings** (no silent fallback).
- **Dedupe guard (3 layers):** row-level `expense_claim` stamps + traveler `per_diem_claimed` mark claimed material; `integrations.py` doc_events clear every stamp when a claim is cancelled/deleted, making rows claimable again. Stamps are written with `frappe.db.set_value` (never a full parent save) so a colleague's concurrently open form still saves cleanly.
- **Advances** — `api.create_employee_advance` drafts a native Employee Advance per traveler; status mirrors back onto the traveler row via doc_events.
- **Rollups** on the trip (read-only): estimated/actual, company-paid vs employee-paid splits, per-diem and mileage totals, claimed and advance totals (the last two also refreshed by doc_events).

## File map

| File | Purpose | Key functions / classes |
|---|---|---|
| `doctype/travel_trip/travel_trip.py` | Validation pipeline + rollups + status rules + Closed lock | `TravelTrip.validate` (`_validate_*`, `_compute_*`, `_handle_status_change`), `on_trash`, `get_travel_settings`, `user_is_travel_coordinator` |
| `doctype/travel_trip/travel_trip_dashboard.py` | Trip form connections (claims/advances/logs/outcomes) | `get_data` (fieldname `custom_travel_trip`) |
| `api.py` | Whitelisted document creation (form Create buttons) | `create_expense_claim(s)`, `create_employee_advance`, `create_outcome_from_stop`, `create_vehicle_log`, `reopen_trip`, `get_trip_financial_summary` |
| `permissions.py` | Crew-scoped row access (hooks) | `get_permission_query_conditions`, `has_permission` |
| `tasks.py` | Daily status auto-advance | `auto_advance_trip_statuses` |
| `integrations.py` | doc_events on Expense Claim / Employee Advance / Vehicle Log: status mirroring + stamp clearing | `sync_expense_claim_status`, `sync_employee_advance_status`, `sync_vehicle_log_unlink` |
| `notifications.py` | Code-driven travel emails (+ Notification Log), gated by Travel Settings switch | `on_trip_update` dispatcher, `deliver_*` jobs, `notify_expense_claims_generated`, `send_itinerary_emails` |
| `reminders.py` | Daily pre-travel itinerary email + single-shot post-trip expense nudge (stamp-first idempotency) | `send_pre_travel_reminders`, `send_post_trip_expense_nudges` |
| `ics.py` | Dependency-free RFC 5545 builder (METHOD:PUBLISH, stable UIDs) | `build_ics`, `trip_events_for_traveler`, `trip_ics_attachment` |
| `dashboard.py` | Travel group on Opportunity/Lead/Customer dashboards (dynamic-link counts) | `get_*_dashboard_data` |
| `report/…` | Script Reports | Travel Trip Cost Summary, Travel Spend by Category, Unclaimed Travel Expenses |
| `workspace/travel_management/` | "Travel" workspace (links, calendar/new-trip/itinerary shortcuts) | — |

Read-side endpoints (calendar events, `/itinerary` page data, the trip form's Google Maps agenda map) live in [`api/travel.py`](../api/README.md); the form scripts are `public/js/travel_trip.js` + `public/js/travel/travel_trip_map.js` (the latter needs the **Google Maps API Key** set in Travel Settings), the calendar config `public/js/travel_trip_calendar.js`, and the mobile page `www/itinerary.*` + `public/js|css/travel/itinerary.*`.

The **company travel policy** ships as a login-gated page at `/travel_guidelines` (`www/travel_guidelines.py`/`.html`) — policy text plus "In the system" callouts tying each rule to these flows (Accommodation rows one-per-room, the nearest Home Depot as a *Hardware Store* Travel POI linked to itinerary stops, receipts on cost rows, claims within a week, Time Kiosk clock-in at scheduled departure). It is linked from the Travel workspace, the `/itinerary` footer, and the booked/traveler-added emails (`guidelines_url` in the notification context).

## Permissions

| Role | Access |
|---|---|
| System Manager / **Travel Coordinator** (role seeded by patch) | every trip, full control, reopen Closed trips |
| HR Manager | every trip (read/write/create, no delete) |
| Employee | create trips; read/WRITE trips they own **or are travelling on** (collaborative crew editing); no delete |

Row scoping is hook-based (`permission_query_conditions` + `has_permission`), tracking the travelers table live — no `frappe.share` records to orphan. Employee links inside trip children carry `ignore_user_permissions` so the site's Employee user-permission cascade doesn't block crew members saving rows for colleagues.

## hooks.py touchpoints

- `doctype_js["Travel Trip"]`, `doctype_calendar_js["Travel Trip"]`.
- `doc_events`: Travel Trip `on_update` (notifications dispatcher); Expense Claim / Employee Advance (status sync + stamp clearing); Vehicle Log `on_trash`.
- `scheduler_events.daily`: `auto_advance_trip_statuses` **before** the two reminder jobs (they must see today's statuses).
- `permission_query_conditions` / `has_permission` for Travel Trip.
- `override_doctype_dashboards`: Opportunity/Lead/Customer (here), Project (in `project_enhancements`), Employee (`dashboard_overrides` — unchanged, child-table fallback).
- Fixtures: 5 `custom_travel_trip` back-link Custom Fields (Expense Claim, Employee Advance, Vehicle Log, Lead, Opportunity). The old Travel Trip Workflow fixtures are gone; patch `retire_travel_trip_workflow` deletes them from the DB.

## Gotchas

- Expense Claims / Advances / Vehicle Logs are **drafts** — HR submits natively.
- Claim generation **refuses to run** until the Expense Claim Types are picked in Travel Settings (they need company accounts, so they are not auto-seeded).
- Per-diem rates seed at **0** (patch `seed_travel_settings`) — finance must set real numbers before per diem produces amounts.
- All travel emails are off until **Travel Settings → Send Travel Notifications** is enabled; the form's "Send Itinerary" button works regardless (explicit user action).
- Deleting a traveler row with a linked claim/advance is blocked; cancel the documents first.
- The daily job also advances **Planning** trips inside their dates to In Progress (crews forget to click Booked); drop "Planning" from the tuple in `tasks.py` to require Booked.
