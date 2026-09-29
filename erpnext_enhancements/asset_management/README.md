# `asset_management/` — asset bookings, event rentals and rental inspections

Reserves an ERPNext Asset for a time window, books fountains out for events, and records
what went out with each one and what came back.

| Path | Purpose |
|---|---|
| `doctype/asset_booking/` | The submittable booking record, and **every fountain's calendar** |
| `doctype/rental_booking/` | One event rental: customer, project, schedule, fountains, accessories, status (v1.562.0) |
| `doctype/rental_booking_fountain/`, `rental_booking_accessory/` | Its lines |
| `doctype/rental_accessory_pool/` | A counted stock of one accessory (uplights, pumps, skirting) |
| `doctype/rental_package/` (+ `_fountain`, `_accessory`) | A preset of fountain models, accessories and fees |
| `doctype/asset_out_of_service/` | A fountain that cannot go out, and until when |
| `doctype/rental_checklist_template/` | What a given fountain ships with |
| `doctype/rental_inspection/` | A pre-shipping or return checklist, submittable |
| `rental_rules.py` | Every rental judgement, **no Frappe**; tested bench-free (`tests/test_rental_rules.py`) |
| `rental_availability.py` | The reads and writes: conflicts, pool peaks, calendar sync, the form's endpoints |
| `out_of_service.py` | Automatic out-of-service from damaged return inspections and Asset Repairs |
| `rental_planner.py`, `page/rental_planner/` | The Rental Planner page: fleet timeline + the four-step new-rental flow (v1.563.0) |
| `workspace/asset_management/` | Desk workspace (with an **Event Rentals** card) |

## `Asset Booking`

Reserves an Asset from `from_datetime` to `to_datetime` with a `booking_type` of **Rental**,
**Travel** or **Maintenance**, and an optional `location` (Address). Bookings default to a
Calendar view.

The three booking types are why this is one doctype rather than three: a truck booked for
travel and the same truck booked for a maintenance visit are the same physical conflict, and
splitting them by purpose would let the two collide. Overlap validation only works because
every reservation lands in one place.

Composite bookings across the three types are created through
`api/booking.py::create_composite_booking`.

**A booking reserves exactly one `asset` and has no items and no status.** Both facts matter
downstream and both have already been assumed away once — see the next section.

## `Rental Checklist Template` — the list a booking cannot provide

ER-2026-312370 asked for a pre-shipping and return checklist. The natural place to read the
component list from would be the Asset Booking, but there is nothing there to read: a
booking holds one `asset` and no child table, and nothing else in this app records what a
fountain ships with. This doctype is that missing record.

Two levels, resolved most-specific-first by `api/booking.py::resolve_checklist_template`:

- a template naming an **Asset** — this particular fountain, with whatever it has accumulated;
- a template naming an **Asset Category** — every fountain of a type, so a newly bought unit
  is inspectable on day one rather than after somebody writes a list.

At most one *active* template at each level, enforced in `validate` rather than by a unique
index (a template may be deactivated and replaced, so the constraint is on the active subset).
"Which list did the crew use" is not a question a damage dispute should have to ask.

## `Rental Inspection`

A submittable checklist in one `direction` — **Pre-shipping** or **Return** — against one
booking. Rows carry the component, quantity expected, quantity counted, a condition
(Pass / Damaged / Missing / N/A), notes and a photo. `has_damage` and `has_shortfall` are
read-only roll-ups, so "did this rental come back short" is a query rather than a reading
exercise.

**`N/A` exists because a category-level template describes a *type* of fountain.** It
necessarily lists parts a given unit does not carry, and without N/A the crew's only
options were to delete the row or to mark a component Missing that was never in the crate
— filing a false shortfall. An N/A row is skipped by the roll-ups, exempt from the count
requirement, and dropped from the return sheet generated off that pre-shipping inspection.

**And N/A requires a note, for the opposite reason to the others.** It is the only value
that removes a row from the findings entirely, so it is exactly what somebody would reach
for to make a genuinely missing part stop being a problem. Requiring a sentence turns a
dropdown click into a written claim. `CONDITIONS_NEEDING_A_NOTE` is deliberately wider
than `ADVERSE_CONDITIONS` for that reason, and a test pins the pair.

**A return reconciles against the pre-shipping sheet, not against the template.** Its
`qty_expected` is what was actually counted *out* — if three of four panels shipped, three
coming back is complete. Reconciling against the catalogue instead would answer a different
question than the one the request asked. When no submitted pre-shipping sheet exists the
template is used and `row_source` says so on the document.

**Draft is cheap, submit is strict.** The completeness gates (every row answered, every
counted, a note on anything not Pass) are `before_submit`, not `validate`: a crew member
fills the sheet over several minutes and saves as they go, and refusing a half-filled draft
repeats the exact mistake that produced ER-2026-420503. What must not happen is a *signed*
sheet that is silently incomplete — that reads as "everything came back fine", and it is the
strongest document in the room the day a customer disputes a damage charge. For the same
reason `generate_inspection` throws rather than returning an empty checklist.

Findings are reported as a timeline comment on the Asset Booking, which is where anyone
looking into a rental starts. Deliberately not an NCR: Quality's NCR path carries its own
triage and paging and is about product non-conformance, and a scuffed rental panel in that
queue is noise in someone else's process.

**Enforcement is on the inspection, not the booking,** because the booking has no lifecycle
event to gate. It is submitted when the booking is *made*, days before anything ships, and
the Asset's `custom_rental_status` is derived from the clock by `update_asset_status` rather
than set by anyone.

## Event rentals (v1.562.0)

The first of five planned PRs. The guided Rental page and fleet timeline follow; then the sales
side (e-signed agreement, Stripe deposit and balance, hold expiry, public request form, customer
portal); then crew logistics and inspections; then KPIs, AI tools and calendar feeds. Nik's design
calls, 2026-09-29: fountains are booked individually and accessories from pools; a quote places a
**tentative hold** that becomes **firm** when the agreement is signed; the blocked window comes from
the event schedule plus each fountain's own buffers.

### Every fountain's calendar is Asset Booking

A Rental Booking keeps no second calendar. Each fountain on it becomes up to three **Asset
Booking legs** (`rental_rules.leg_windows`):

| Leg | Asset Booking type | Window |
|---|---|---|
| Prep | Maintenance | `custom_rental_prep_hours` before delivery (only if non-zero) |
| Rental | Rental | delivery → take-down |
| Turnaround | Maintenance | take-down → `custom_rental_turnaround_hours` later (default 24) |

That keeps the one-place rule above: Asset Booking already refuses an overlap for *any* booking
type, so a rental cannot land on a Travel or Maintenance booking made by hand; the hourly status
job reads Rented/Maintenance correctly; and the inspection buttons, which sit on a submitted Rental
booking, work on the Rental leg unchanged.

- **A tentative hold is a draft leg; firm is submitted.** Drafts block (`docstatus < 2`), so a hold
  is a real hold; submitting is what the inspection buttons look for. The calendar draws held legs
  lighter and labels them "held".
- **Legs are moved, not re-created** (`rental_rules.plan_leg_changes`), so a submitted leg keeps its
  name and every inspection filed against it. That needed `allow_on_submit` on Asset Booking's
  dates, location, customer and project, and a `before_update_after_submit` that re-runs the
  overlap check.
- **A leg is changed through its booking only.** `AssetBooking.guard_rental_leg` refuses a hand
  move, cancel or delete unless `frappe.flags.rental_booking_sync` names the owning booking —
  otherwise the booking would still believe it holds a fountain its calendar has let go.
- **One rental never collides with itself.** Legs touch end to start and the overlap test is
  strict; and while the booking moves them one at a time (a lengthened Rental leg briefly overlaps
  the Turnaround leg it is about to move), `check_overlap` ignores the leg's own rental.

### Availability, and the last-fountain race

`RentalBooking.check_availability` refuses a save with **every** problem listed: a fountain taken
by another booking (any type) or out of service, or an accessory pool without enough units at the
busiest moment of the window. Before reading anything it row-locks the Assets and pools
(`rental_availability.lock_rows`, sorted names, so two bookings cannot deadlock), and
`check_overlap` locks the Asset too, so two people saving the last fountain at once are checked one
after the other.

**A pool is checked on its peak, not a sum** (`rental_rules.peak_usage`). Friday's and Sunday's
bookings both touch a Friday–Sunday request but never coexist; adding them up would refuse a
booking that fits, and nobody would notice the kit sitting idle.

**Not every save re-checks** (`availability_needs_checking`): only a new booking, a changed
schedule or lines, a renewed hold, and Confirm. A fountain can go out of service *after* a booking
took it, and re-checking every save would then refuse "Mark Returned" on that booking. The outage
flags the booking instead.

### Status

`Tentative → Confirmed → Out → Returned → Closed`, plus `Expired` (a lapsed hold, renewable back
to Tentative) and `Canceled`. Transitions are `rental_rules.TRANSITIONS`; the form draws its
buttons from them through `__onload.next_statuses`, so there is one list. Expired and Canceled
release the legs (drafts deleted, submitted ones canceled); Closed keeps them as history. A
Closed, Expired or Canceled booking's schedule and lines are frozen. The hold date defaults to
seven days, but **nothing expires holds yet**; that job comes with the sales PR.

### Project, packages, pools

- **The booking owns the event schedule.** It is filled from the Project's Events fields when the
  booking is new, then mirrored back onto them on every save (`push_schedule_to_project`, without
  touching `modified`). While a live booking exists the Project shows those four fields read-only
  with a banner naming it (`public/js/asset_management/project_rental.js`). One live booking per
  Project.
- **A package names fountain models, not fountains.** Applying it (`plan_package`) picks free
  fountains of each model for the dates and reports a shortfall rather than failing.
- **Fleet membership is `Asset.custom_rentable`** ("Available for Event Rental"), with
  `custom_rental_prep_hours` and `custom_rental_turnaround_hours`. All three are `allow_on_submit`
  because the fleet Assets are submitted. `patches/seed_rental_fleet_flags` ticks it for the Rental
  Fountain Fleet category. The fountain link on a booking searches through `rentable_asset_query`,
  because the people who book rentals are not necessarily allowed to search the Asset register,
  and v16 validates a link through that same search.

### The Rental Planner (v1.563.0)

A desk page at `/desk/rental-planner` (`page/rental_planner/`, server side in `rental_planner.py`).
Open it from the workspace, the Rental Booking list, or a booking's View menu.

- **The board is the fleet timeline.** It has one row per rentable fountain and one per accessory
  pool, across two weeks, a month or a quarter.
  - Confirmed rentals are solid blue. Tentative holds are hatched and dashed. Prep and cleaning are
    thin grey bars. Anything booked by hand on Asset Booking is amber, and out-of-service is red.
  - Clicking a bar opens its record. Clicking an empty day on a fountain starts a new rental with
    that fountain and date filled in.
  - Pool rows show units out per day against what can be booked, shaded as they fill.
  - The feed is `rental_planner.get_timeline`, capped at 100 days.
- **It is drawn here, not on the Gantt widget.** DHTMLX Gantt Standard draws one bar per row. A
  fleet timeline needs several bookings on each fountain's row, and split tasks and the resource
  view are PRO-only.
- **New Rental is four steps:**
  1. When and where.
  2. Fountains and accessories. Live availability comes from `get_availability`, and a package can
     be applied with `plan_package`.
  3. Customer and project. There is an in-place New Customer quick entry, and an Events Project can
     be created.
  4. Review and fees, then **Place Hold** (Tentative) or **Book as Confirmed**.
- **`create_rental` creates the Project and the booking in one transaction.** If the booking is
  refused, for example because someone took the fountain in the meantime, the Project rolls back
  with it. It accepts only the fields in `BOOKING_FIELDS`, so a request cannot set status, stamps
  or totals. A new Project is typed as Events by both `project_type` and the `custom_value_stream`
  row.
- **Every screen is a route** (`new/dates`, `new/fountains`, `new/customer`, `new/review`), so Back
  and Forward step through the flow.
  - The flow's own Back button steps back through history when the previous step is what is behind
    it, so Next, Back, Next leaves no stack of entries to replay.
  - An address for a step whose earlier steps are unfinished is corrected in place, not pushed.
  - The draft is kept in memory and in `sessionStorage`, so a reload or leaving the page loses
    nothing.
  - Once booked, the draft is cleared, so Back from the confirmation cannot book the same fountains
    twice.
  - `scripts/test_rental_planner_history.js` runs the real script against a model of the v16 router,
    from `tests/test_rental_planner.py`.

### Out of service

A separate record rather than an Asset Booking, because being broken is a fact, not a
reservation: it has to be recordable *over* rentals already booked, which an overlap-checked
booking cannot be. It blocks until returned; while still out, through Expected Back if given; with
no date, every future date. Creating one comments on every live rental it lands on.
`out_of_service.py` opens one automatically from a **Return** inspection that found damage and from
a **Pending ERPNext Asset Repair** (Completed or Cancelled puts it back; the repair hook runs in a
savepoint and never stops a repair saving). `custom_rental_status` gains **Out of Service**.

## Related

- `api/booking.py` — composite bookings, `generate_inspection`, template resolution
- `patches/seed_rental_asset_setup.py` — the Asset Category and Location without which no
  Asset can be saved at all, and therefore no booking made and nothing inspected
- `public/js/asset_management/asset_form.js` — the Asset form's Item quick-entry seeding
- `travel_management/` — trips that book assets
- `fleet_maintenance/` — vehicle maintenance scheduling
- `sapphire_maintenance/` — the maintenance visit model
