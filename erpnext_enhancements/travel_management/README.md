# Travel Management

Crew-based trip planning, logistics and travel finance. A non-submittable **Travel Trip** hub document carries a crew of travelers, the booking segments (flights / lodging / ground / misc costs / personal-vehicle mileage), a day-by-day agenda with CRM outcome capture, and rolls everything into native **Expense Claims**, **Employee Advances** and **Vehicle Logs**. Redesigned ground-up in v1.15.0 (the old submittable + Workflow version is retired; its 2 production drafts were deleted by patch).

Trips are entered on **Plan a Trip** (`/desk/plan-a-trip`, v1.520.0): a step-by-step desk page for the office booking a crew — the trip, who's going, getting there, getting back, where everyone sleeps, getting around, freight, the schedule — ending on a **trip checklist** (a bed every night, travel both ways, confirmation and tracking numbers, cost). Location boxes (drive from/to, freight ship-from/deliver-to, a stop's Place) search Google for place names and addresses through the same component as the Address form. The Travel workspace tile and the list's *+ Add* button open it; the form links to it and shows the checklist as a headline. The form is still the full record.

## Design decisions (the "why")

- **No Workflow, no submit.** Trips are edited *collaboratively* (admins and travelers alike) until Closed; a submittable doc would freeze mid-trip edits behind amend cycles. Lifecycle is a plain `status` Select: **Planning → Booked → In Progress → Completed → Closed**. In Progress/Completed auto-advance from the trip dates (daily job); Booked and Closed are manual. Closed locks the doc (controller check); `api.reopen_trip` is the coordinator escape hatch.
- **The parent has NO `employee` field — and must never get one.** Travelers live in the `travelers` child table; the Employee dashboard's Travel Trip count works because frappe's link-count filter falls back to the `Trip Traveler.employee` child column. A parent field named `employee` would silently zero that dashboard count.
- **Documents are created explicitly, never as save side-effects** — `travel_management/api.py` methods back the form's Create buttons. Claim dedupe is stamp-based (see below) so re-running is always safe.
- **Multi-currency is out of scope for v1**: all costs are company-currency; advances are created at exchange rate 1. Foreign receipts get entered converted.
- **One row per person, one card per booking.** Plan a Trip shows a flight with four people on it as one card; it stores four Trip Flight rows, each pinned to its traveler with that person's own confirmation number, sharing a hidden `booking_group`. That is what lets each traveler's `/itinerary`, itinerary email and calendar invite show their own booking and nobody else's (a pinned row is hidden from everyone else — the existing visibility rule). Rooms and vehicles work the same way; a room's or vehicle's total cost is split evenly across its rows. `planner.py`'s docstring has the write rules: a shared field is only copied across a booking's rows when the page changed it, the split is only redone when the total or the people changed, and a row on an Expense Claim is never deleted or re-priced.
- **The checklist flags, it never blocks.** The office asked to *see* what is missing, so `completeness.py` feeds the page and a form headline, and nothing refuses a save or a status change. It returns data, not sentences; the page words it.

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
├── freight          → Trip Freight          (carrier → Supplier, tracking/PRO/BOL #, pickup +
│                                             delivery windows, received-by traveler, cost block)
├── other_costs      → Trip Expense          (misc: parking, tolls, fees; cost block)
├── mileage          → Trip Mileage          (personal vehicle only: distance × settings rate)
└── itinerary        → Trip Agenda           (related party dyn-link, location → Travel POI,
                                              visit_notes, outcome_doctype/outcome_name)

Travel POI        reusable Point of Interest (Geolocation field feeds the maps)
Travel Settings   Single: per-diem rate rules (Travel Per Diem Rate child), mileage rate,
                  Expense Claim Type mapping, auto-advance + notifications master switches
```

**Shared cost block** (identical on the five cost tables): `estimated_cost`, `cost`, `paid_by` (Company/Employee), `paid_by_traveler` (required+validated when Employee), `billable`, and the hidden `expense_claim` stamp.

**Plan a Trip fields** (v1.520.0): `leg` (Outbound / Return / During Trip) on Trip Flight and Trip Ground Transport — optional on the form, always set by the page, inferred from the date when blank; hidden `booking_group` on Trip Flight, Trip Accommodation, Trip Ground Transport and Trip Mileage. Ground transport gained a **Personal Vehicle** type (riders get a row each at no cost; the driver's miles go to a Trip Mileage row in the same `booking_group`), and a **Company Fleet** row no longer requires a Vehicle — neither `Vehicle` nor `Fleet Vehicle` held a single record on prod, so the requirement made a company truck impossible to enter. A Vehicle Log still needs one.

**Freight and time ranges** (v1.520.0): **Trip Freight** is a carrier shipment — equipment or materials sent to the job — with its tracking / PRO / BOL number, ship-from and deliver-to, a pickup window and a delivery window (from/to datetimes), who on the crew receives it (`traveler`, blank = whole crew, the same visibility rule as a booking), and the shared cost block. It is one row per shipment, not per person, and it is in `COST_TABLES`: rollups, the unclaimed report, the spend-by-category report (its own Freight column) and Expense Claims (as a misc expense — Travel Settings has no freight type) all count it. Our *own* truck's load is not freight: it is the **Hauling** note (`cargo`) on that Ground Transport row. Time ranges: Ground Transport `arrival_datetime` (a drive's end), Trip Accommodation `check_in_time` / `check_out_time`, Trip Agenda `end_time` (its `time` is now labelled Start Time). A stop's **Place** is still a Link to Travel POI; a place picked from Google on the page becomes a POI with its point (`planner.place_to_poi`), reusing one with exactly the same name.

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
| `page/plan_a_trip/` | **Plan a Trip** desk page (`/desk/plan-a-trip`, `?trip=` to continue one, `&step=` to jump) — the step-by-step entry. Saves on every step change and when you leave the page; refuses a stale save rather than merging. Each step and the list is its own history entry, so Back/Forward move a step at a time. Times are native inputs (AM/PM on a US device) | `TripPlanner` |
| `planner.py` | The page's read/write side: cards ↔ per-person rows, freight rows, optimistic lock, allowlisted fields, Google place → Travel POI | `get_plan`, `save_plan`, `get_recent_plans`, `place_to_poi` (whitelisted); `merge_bookings`, `merge_mileage`, `merge_travelers`, `merge_freight`, `merge_stops`, `get_state` |
| `completeness.py` | The trip checklist, pure Python (no site needed) | `find_gaps`, `lodging_gaps`, `travel_gaps`, `confirmation_gaps`, `cost_gaps` |
| `permissions.py` | Crew-scoped row access (hooks) | `get_permission_query_conditions`, `has_permission` |
| `tasks.py` | Daily status auto-advance | `auto_advance_trip_statuses` |
| `integrations.py` | doc_events on Expense Claim / Employee Advance / Vehicle Log: status mirroring + stamp clearing | `sync_expense_claim_status`, `sync_employee_advance_status`, `sync_vehicle_log_unlink` |
| `notifications.py` | Code-driven travel emails (+ Notification Log), gated by Travel Settings switch | `on_trip_update` dispatcher, `deliver_*` jobs, `notify_expense_claims_generated`, `send_itinerary_emails` |
| `reminders.py` | Daily pre-travel itinerary email + single-shot post-trip expense nudge (stamp-first idempotency) | `send_pre_travel_reminders`, `send_post_trip_expense_nudges` |
| `ics.py` | Dependency-free RFC 5545 builder (METHOD:PUBLISH, stable UIDs). Flights, hotel check-ins, rentals/rides/drives and freight windows, each with its confirmation or tracking number; a datetime at exactly midnight ("time not known yet") becomes an all-day event | `build_ics`, `trip_events_for_traveler`, `trip_ics_attachment` |
| `itinerary_text.py` | The travel emails' wording, pure Python: one line per itinerary item with its PNR / confirmation / tracking number (or "no … yet"), times on a 12-hour clock. Used by the itinerary email and by "Trip booked" / "You were added", which now list the recipient's own bookings | `clock`, `item_line`, `day_lines`, `booking_lines` |
| `dashboard.py` | Travel group on Opportunity/Lead/Customer dashboards (dynamic-link counts) | `get_*_dashboard_data` |
| `report/…` | Script Reports | Travel Trip Cost Summary, Travel Spend by Category, Unclaimed Travel Expenses |
| `workspace/travel_management/` | "Travel" workspace (links, calendar/new-trip/itinerary shortcuts) | — |

Read-side endpoints (calendar events, `/itinerary` page data, the trip form's Google Maps agenda map) live in [`api/travel.py`](../api/README.md); the form scripts are `public/js/travel_trip.js` + `public/js/travel/travel_trip_map.js` (the latter needs the **Google Maps API Key** set in Travel Settings; a POI whose linked Address was picked from the Places autocomplete is plotted from that stored point instead of being geocoded, which is also the only way the Leaflet `/itinerary` map — it has no geocoder — can place a POI with no Geolocation of its own), the calendar config `public/js/travel_trip_calendar.js`, and the mobile page `www/itinerary.*` + `public/js|css/travel/itinerary.*`. The page addresses its trip as `/itinerary?trip=<name>` — a trip chip tap is one browser history entry, so Back returns to the previous trip, and a reload or a login keeps it ([`www/README.md`](../www/README.md)). Nothing links to that form yet: the trip emails and calendar invites (`notifications.py`, `ics.py`) still open bare `/itinerary`, which shows the current trip.

The **company travel policy** ships as a login-gated page at `/travel_guidelines` (`www/travel_guidelines.py`/`.html`) — policy text plus "In the system" callouts tying each rule to these flows (Accommodation rows one-per-room, the nearest Home Depot as a *Hardware Store* Travel POI linked to itinerary stops, receipts on cost rows, claims within a week, Time Kiosk clock-in at scheduled departure). It is linked from the Travel workspace, the `/itinerary` footer, and the booked/traveler-added emails (`guidelines_url` in the notification context).

## Permissions

| Role | Access |
|---|---|
| System Manager / **Travel Coordinator** (role seeded by patch) | every trip, full control, reopen Closed trips |
| HR Manager | every trip (read/write/create, no delete) |
| Employee | create trips; read/WRITE trips they own **or are travelling on** (collaborative crew editing); no delete |

Row scoping is hook-based (`permission_query_conditions` + `has_permission`), tracking the travelers table live — no `frappe.share` records to orphan. Employee links inside trip children carry `ignore_user_permissions` so the site's Employee user-permission cascade doesn't block crew members saving rows for colleagues.

## hooks.py touchpoints

- `doctype_js["Travel Trip"]`, `doctype_calendar_js["Travel Trip"]`, `doctype_list_js["Travel Trip"]` (`public/js/travel/travel_trip_list.js`: *+ Add Travel Trip* opens Plan a Trip).
- Patch `reload_travel_workspace_for_plan_a_trip` forces the workspace past the import age gate (its "New Travel Trip" tile became "Plan a Trip").
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
- **A flight or drive with no time yet is stored at midnight**, because a Datetime column cannot hold a date alone. Plan a Trip therefore reads a stored `00:00:00` as "no time given" — a flight genuinely leaving at 12:00 AM shows a blank time on the page (the form shows it correctly).
- **Back and Forward are history entries the page writes itself** (`history.pushState` with the query string, from `window.location.pathname` — frappe v16's `set_route` drops the query, and routing to the page it is on is what broke v1.520.0). A step the user moves to is pushed. **Back is never held up**: a move to an earlier step, or wherever the phone's Back leads, saves quietly and moves whether or not the save went through (the edits stay on the page, marked *Not saved*). An earlier version ran Back through the same blocking save as Next, so one half-finished card — a flight with no airline yet — showed a modal on every press of Back and overwrote each entry it refused, rewriting history into copies of the step you were stuck on. A move **forward** passes the Next button's checks and save; a Forward that is refused goes back (`history.go`) to the entry of the step still showing, leaving the entry asked for in place, and shows its message only once it is back there, because frappe closes any open dialog on every route change (`router.set_history` → `hide_open_dialog`) — which is also why that save is made `silent` and the server's refusal shown afterwards. Every entry the page writes is marked in `history.state` (`tp: {draft, back, pos}`): `pos` is how the page tells Back from Forward and how far to undo; a new trip's `?new=1` entries carry its draft id, so Back onto one after the trip was saved reopens that trip — an unmarked `?new=1` (the list's *+ Add*) is the only kind that starts a blank one. An entry frappe itself pushed for the trip already on screen (the form's *Plan step by step*, a checklist link — v16 pushes the bare path) is rewritten in place to name that trip and step, or Back onto it later, or a reload, would show the list. A trip the list replaces before the server would take it (nobody on it yet, or a refused save) is kept in memory and offered on the list; `beforeunload` still warns while one is unsaved. `scripts/test_wizard_back_forward.mjs` runs the real page against a port of the v16 router (run by `tests/test_travel_planner.py`).
- **The page and the server each list a booking's shared fields** (`TP_SHARED` in `plan_a_trip.js`, `BOOKING_TABLES` in `planner.py`). Add a field to one and not the other and edits to it are dropped with no error; `tests/test_travel_planner.py` fails the build on a mismatch.
- **Removing someone from the crew on the page takes them off every booking** (the server refuses a booking for a non-traveler). Their rows are deleted on save unless they are on an Expense Claim.
