# `asset_management/` — asset bookings

Reserves an ERPNext Asset for a time window. One submittable doctype and a workspace.

| Path | Purpose |
|---|---|
| `doctype/asset_booking/` | The submittable booking record |
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

## Related

- `travel_management/` — trips that book assets
- `fleet_maintenance/` — vehicle maintenance scheduling
- `sapphire_maintenance/` — the maintenance visit model
