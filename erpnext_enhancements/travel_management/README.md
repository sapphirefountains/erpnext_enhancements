# Travel Management

A submittable **Travel Trip** doctype that groups an employee's travel logistics into four child tables and is driven by the **Travel Trip Workflow**. Once a trip reaches its settlement states, the parent controller auto-generates a draft ERPNext **Expense Claim** from the costed flight/accommodation rows.

## Data model

```
Travel Trip (parent, submittable, autoname TRIP-.YYYY.-.#####)
│   links to one Expense Claim via custom_expense_claim
├── flights         → Trip Flight            (airline → Supplier, cost)
├── accommodation   → Trip Accommodation     (hotel_lodging → Supplier, cost)
├── ground_transport→ Trip Ground Transport
└── itinerary       → Trip Agenda            (location → Travel POI)

Travel POI   reusable Point of Interest, referenced by Trip Agenda rows
```

## File map

| File | Purpose | Key functions / classes |
|---|---|---|
| `doctype/travel_trip/travel_trip.py` | Parent controller; expense-claim rollup | `TravelTrip`, `on_update`, `create_expense_claim_on_workflow_transition` (with inner `get_expense_type`) |
| `doctype/travel_poi/travel_poi.py` | Reusable Point of Interest | `TravelPOI` (pass-through) |
| `doctype/trip_flight/trip_flight.py` | Flight-segment child row | `TripFlight` (pass-through) |
| `doctype/trip_accommodation/trip_accommodation.py` | Lodging-stay child row | `TripAccommodation` (pass-through) |
| `doctype/trip_ground_transport/trip_ground_transport.py` | Ground-travel child row | `TripGroundTransport` (pass-through) |
| `doctype/trip_agenda/trip_agenda.py` | Itinerary/agenda child row | `TripAgenda` (pass-through) |

Form behavior is layered on by `public/js/travel_trip.js` (sets `transport_ref_doctype` from the transport type).

## Expense-claim rollup

`create_expense_claim_on_workflow_transition` runs when `workflow_state` is **"Expense Review"** or **"Closed"**, `custom_expense_claim` is empty, and at least one flight/accommodation exists. Only rows with `cost > 0` become expense lines ("Air Travel" / "Hotel Accommodation", falling back to "Travel"). The created claim is left as a **draft** (saved, not submitted); its name is written back via `db_set` for idempotency.

## Workflow

`Travel Trip Workflow` states: **Draft → Requested → Approved → Booking in Progress → Ready for Travel → In Progress → Expense Review → Closed** (plus "Pending Review" / "Final/Submitted"). The workflow, its states, actions ("Request Review", "Approve & Submit"), and the `Expense Claim-custom_travel_trip` Custom Field all ship as **fixtures** (see [`../hooks.py`](../hooks.py)).

## `hooks.py` touchpoints

- `doctype_js["Travel Trip"]` = `public/js/travel_trip.js`.
- `fixtures`: the Workflow / Workflow State / Workflow Action / Workflow Action Master records and the `custom_travel_trip` Custom Field.
- The Employee dashboard gains a "Travel" connections group via `dashboard_overrides.get_data` (see [Project Enhancements](../project_enhancements/README.md)).

## Gotchas

- The Expense Claim is `save()`d only, never submitted.
- The fallback expense type `"Travel"` is returned even if that Expense Claim Type doesn't exist, which could raise on save — create it during setup.
- `total_estimated_cost` and the hidden `status` field are not maintained by code.
