# `asset_management/` — asset bookings and rental inspections

Reserves an ERPNext Asset for a time window, and records what went out with it and what
came back. Three submittable doctypes, one template doctype, and a workspace.

| Path | Purpose |
|---|---|
| `doctype/asset_booking/` | The submittable booking record |
| `doctype/rental_checklist_template/` | What a given fountain ships with |
| `doctype/rental_inspection/` | A pre-shipping or return checklist, submittable |
| `workspace/asset_management/` | Desk workspace |

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
(Pass / Damaged / Missing), notes and a photo. `has_damage` and `has_shortfall` are read-only
roll-ups, so "did this rental come back short" is a query rather than a reading exercise.

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

## Related

- `api/booking.py` — composite bookings, `generate_inspection`, template resolution
- `patches/seed_rental_asset_setup.py` — the Asset Category and Location without which no
  Asset can be saved at all, and therefore no booking made and nothing inspected
- `public/js/asset_management/asset_form.js` — the Asset form's Item quick-entry seeding
- `travel_management/` — trips that book assets
- `fleet_maintenance/` — vehicle maintenance scheduling
- `sapphire_maintenance/` — the maintenance visit model
