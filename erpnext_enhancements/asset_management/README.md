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
| `rental_holds.py`, `rental_sales.py`, `doctype/rental_settings/` | Hold expiry, the Rental Agreement, deposit/balance invoices, Submit & Send (v1.564.0) |
| `rental_logistics.py`, `rental_deposit.py`, `rental_reminders.py`, `doctype/rental_booking_crew/` | Crew tasks + 6am digest + checklists, deposit release, customer reminders (v1.566.0) |
| `rental_portal.py`, `rental_requests.py`, `../www/rentals.*`, `../www/rent*a*fountain.*`, `../portal_login.py` | Customer portal, public request form, email-link sign-in and its staff guard (v1.565.0) |
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

### Holds, the agreement and the invoices (v1.564.0)

Settings live in **Rental Settings** (`doctype/rental_settings/`, a new Single, so every default
applies on existing sites; read it with `get_cached_doc`).

- **Holds** (`rental_holds.py`, run daily):
  - A new hold lasts `hold_days` (7).
  - `hold_reminder_days` (2) before it lapses, whoever placed it gets an assigned ToDo, once
    (`hold_reminder_sent_on`, cleared when the hold is renewed).
  - A hold past its date is set to Expired, which frees its fountains, and a comment says so.
  - The date filters carry `is set`, so a hold with no date is never expired.
  - It is a sweep rather than a job per hold, because the deploy's Redis flush kills queued jobs.
  - Optional customer notice, `email_customer_on_hold`, off by default: it lists what is held and
    until when.
- **The Rental Agreement** is the existing Project Contract, `template_key = "rental"`.
  - **Create > Rental Agreement** on a booking (`rental_sales.make_rental_agreement`) fills it from
    the booking: dates, one equipment row per fountain and accessory, the rental fee, fees and the
    security deposit.
  - It is sent through the existing e-sign flow.
  - `patches/add_rental_esign_signature_block` gives the live template its `sig()` block; without
    one, Send for Signature refuses.
- **Signing confirms the booking** (`on_rental_agreement_signed`, on both signing paths).
  - The hook runs inside the signer's transaction, usually a Guest's, so it **never raises**.
  - If the booking can't be confirmed, because its fountains were taken while the agreement was
    out, the hook rolls back to a savepoint and logs the failure. It also comments on the booking
    and sends its owner a Notification.
  - The signature stands.
  - An Expired hold is renewed on the way to Confirmed.
- **Invoices are drafts** (Nik, 2026-09-29). Nothing posts until someone presses **Submit & Send**.
  - The **deposit** invoice, `deposit_percent` (50) of the rental total, is drafted when the booking
    becomes Confirmed.
  - The **balance** invoice is drafted by the daily sweep `balance_days_before_delivery` (14) days
    before delivery, due on the delivery date.
  - The **security deposit** is its own line on the balance invoice, posted to
    `security_deposit_account`. Settings refuses anything but a Liability account, because the
    deposit is owed back.
  - Tax is `taxes_and_charges` from Rental Settings **or nothing**. Any party or company default
    that `set_missing_values` picked up is cleared, because Utah's rental tax (OD-2) is still open.
  - Each invoice carries `custom_rental_booking` and `custom_rental_invoice_kind`, so drafting is
    idempotent and the booking's Billing links find them.
- **Submit & Send** (`submit_and_send`, POST) posts the invoice with the caller's own submit
  permission.
  - It first refreshes the draft's amount from the booking.
  - It then opens a hosted Stripe Checkout (`create_payment`, which expires any older open link)
    and emails the pay link in the design system (`pillar="rent"`).
  - Customers on autopay get no link. Submitting already charges their saved card, and an open
    link would make that charge refuse itself.

### The customer portal and the public request form (v1.565.0)

- **`/rentals`** (`www/rentals.py`, `rental_portal.py`, in the portal menu as **My Rentals**).
  - A signed-in customer sees every rental of the customers their Contact is linked to. The lookup is
    the same as `/pay`'s `get_portal_customers`.
  - Each rental shows its schedule, what is booked, whether the agreement is signed, and its invoices.
    Paying goes to the existing `/pay`, which holds every Stripe safety rule.
  - Each rental has a **site details** form (on-site contact and phone, surface, access, power, water,
    notes) that the delivery crew reads on the booking.
  - Each rental has an **ask for a change** box, which leaves a comment and a ToDo for the booking's
    owner.
  - Writes set only `SITE_PREP_FIELDS` with `db.set_value`, so a customer can never move dates,
    lines, status or money.
  - "Not yours" and "does not exist" give the same answer.
- **Accounts.** Sign-up stays off.
  - Signing a Rental Agreement creates a portal account for the signer's confirmed email, in its own
    savepoint inside the signing hook. The account is a Website User with only the Customer role,
    no welcome email, and a Contact linked to the Customer. The signer then gets an email saying
    where the rental lives.
  - **Create > Invite to Portal** on a booking does the same for any of the customer's contacts.
  - A staff account is never touched or linked (`portal_login.ensure_portal_user`).
- **Sign-in is an emailed link** (`portal_login.py`). `patches/enable_login_with_email_link` turns on
  frappe's setting, which is site-wide. frappe's `login_via_key` signs in *any* account with no
  password, 2FA or Google, so the setting ships with two guards:
  1. an override of `send_login_link` that mints no link for anything but an enabled Website User.
     It answers as frappe does for an unknown email, so staff addresses are not revealed. It is
     **sealed** in `before_request`, because `frappe.www.login.frappe.www.login.send_login_link` is
     a second dotted name for frappe's original;
  2. an `on_login` guard that refuses a sign-in through `login_via_key` for any other account.
     That covers aliased paths and the legacy `?cmd=` route.

  Staff stay Google-only.
- **`/rent-a-fountain`** (`www/rent_a_fountain.py`, `rental_requests.py`) is off until
  `Rental Settings.public_request_form` is ticked.
  - The public form becomes an Events **Lead**, with rental dates and ZIP, through the same triage as
    a website enquiry.
  - It shows no availability (Nik's call).
  - It is protected by Turnstile (the site's existing keys, with its own `rental-request` action), a
    honeypot, a per-IP rate limit and a field allowlist.
  - The marketing site is WordPress on another host, so it links here.

### Operations: crew, checklists, the deposit, reminders (v1.566.0)

- **Crew, per booking** (`crew` table, pre-filled with Rental Settings' `default_crew_lead`).
  - A firm booking gets **Delivery**, **Setup** (only when a setup time is set), **Take-down** and
    **Cleaning** Tasks, created by `rental_logistics.sync_tasks`.
  - They are keyed by `Task.custom_rental_booking` and `custom_rental_task_kind`, dated from the
    schedule and assigned to every crew member with a ToDo. The ToDo is inserted directly, because
    `assign_to.add` checks the *caller's* rights.
  - They are kept in step on every save: dates follow the schedule, a crew member taken off loses
    their ToDos, and a canceled booking cancels its open tasks. A Completed task is never touched.
  - **The Project's expected dates are widened (never narrowed) first.** ERPNext refuses a Task
    outside them (`Task.validate_parent_project_dates`), and cleaning always falls after take-down.
  - This runs in a savepoint from `on_update`, so it can never refuse a booking save.
- **6am** (`cron`, after the maintenance digest):
  - `generate_due_inspections` creates pre-shipping checklists for every fountain delivering today or
    tomorrow;
  - then `send_crew_digests` sends each crew member their rental jobs by email and text, at most once
    a day (`Task.custom_rental_digest_sent_on`).
- **Return checklists** are created on the save that marks a booking Returned. Checklists go through
  `api.booking.make_inspection`, the in-process half of `generate_inspection` with no whitelist, so
  it can insert as the system. It is assigned to the crew.
- **The security deposit** (`rental_deposit.py`). Once every fountain has a submitted Return
  checklist:
  - a clean return drafts the release;
  - findings instead go to the booking's owner, who judges them and uses **Release Deposit…**.
  - The release is a **credit note** for the deposit line, plus a **damage-charge invoice** for any
    deduction. Submitting the damage charge is the approval.
  - Then **Refund Deposit** refunds the remainder on the Stripe payment that paid the balance
    invoice. Only System Manager or Accounts Manager can press it, and it is capped at what that
    payment took. The existing refund webhook drafts the reversing Payment Entry.
  - **Mark Refunded by Hand** records a refund made by check.
  - `prepare_release` is the HTTP door and always checks write permission. `draft_release` is
    in-process only: a whitelisted function must never take a `check_permission` argument, because
    JSON `false` would switch it off.
- **Customer reminders** (`rental_reminders.py`, daily) are **each off until turned on** (Nik):
  - a week before delivery;
  - site details, 10 days out, only if blank;
  - the delivery time, the day before;
  - a thank-you with `review_url`, the day after.
  - Each is sent once per booking (a date stamp).
- `patches/backfill_rental_settings_defaults` fills the two ticked-by-default switches on an
  already-saved Rental Settings.

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
