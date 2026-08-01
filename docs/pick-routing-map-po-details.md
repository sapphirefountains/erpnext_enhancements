# Pick Routing Map — showing PO detail (spike)

**Status: decided and built (v1.202.0).** Options **(b) and (c)** were both chosen. (a) was
not built — see why below, it is the one that would have created a second source of truth.

The sections below are the original spike, kept because the reasoning is still what the
implementation rests on. "What was actually built" at the end records where the delivery
departed from the option as costed, and why.

The ask: when creating the Pick Routing Map, show the PO details of what we are collecting,
as an optional formatted report or a custom HTML block.

## The finding that changes the estimate

**The item lines are already on the wire and simply are not rendered.**

`api/pickup_routing.py::get_pickup_route_data` already returns, per stop, every Purchase
Order behind it and every line on those orders:

```
stop.purchase_orders[].items[] → item_code, item_name, qty, received_qty, uom
stop.purchase_orders[]         → name, status, docstatus, date, required_by,
                                 per_received, grand_total, currency
```

So this is a **rendering** question, not a data question. No new endpoint, no second
round-trip, and — importantly for a phone in a truck — no extra network call at the moment
the driver needs it. That makes every option below cheaper than the ticket assumes, and it
changes which one wins.

## STEP 1 — what exists today

| | |
|---|---|
| Trigger | `custom_btn_pick_routing_map` on Project (Budget tab → *Material Pickup*), created by `patches/add_project_pick_routing_button.py` |
| Client | [`public/js/project_enhancements/pick_routing_map.js`](../erpnext_enhancements/public/js/project_enhancements/pick_routing_map.js), 1009 lines, IIFE |
| Server | [`api/pickup_routing.py`](../erpnext_enhancements/api/pickup_routing.py) → `get_pickup_route_data(project, scope)` |
| Surface | An **extra-large modal dialog**, two panes: ordered stop list left (`minmax(300px, 5fr)`), map right (`7fr`), `height: 62vh`. Collapses to one column under 900px. |
| Map | **Google Maps `DirectionsService` with `optimizeWaypoints: true`** — a real drive-time solve, run in the browser. Nothing geocoded server-side. |
| Which stops | POs on the project (union of header and item-row `project`), `docstatus = 1`, status not `Closed`/`Delivered`, `per_received < 100`. The *number*, not the label. |
| Stop identity | `"{supplier}::{address}"` — one supplier with two branches is two stops |

Three things about it worth respecting in any change:

- **Every re-route is a billable Directions call**, including one per checkbox click. The
  existing code is careful about this — the custom-address field re-routes on blur, not on
  keystroke, with a comment saying why. Anything added must not introduce a new one.
- **It degrades in three steps**, each still usable: optimised route → geocoded pins in PO
  order (key without the Directions API) → a plain ordered list with Google Maps deep
  links (no key at all). "Open in Google Maps" works at every step. A PO-detail feature
  that only works at step one would be a regression in the field.
- **Google's ceilings are surfaced, not hidden**: 23 waypoints per route, 9 per deep link.
  Overflow stops render in their own section rather than silently vanishing.

## STEP 2 — three options

### (a) A Frappe Query/Script Report beside the map

A report filtered to the project, opened alongside or linked from the dialog.

| | |
|---|---|
| Effort | Medium — a new report, its own permissions, and a second query that must agree with `_select_purchase_orders` or the sheet contradicts the map |
| Phone in a truck | **Poor.** Frappe's report view is a desktop grid; on a phone it is a horizontal-scroll exercise |
| Printable | Good |
| Stays in sync | Only if the report reimplements the stop-selection rule. **Two sources of truth for "still to collect" is exactly the divergence the procurement tracker just had to be fixed for** |
| Maintenance | Highest — a second query to keep aligned forever |

### (b) A Print Format pick sheet

A printable sheet the driver carries, grouped by stop in route order.

| | |
|---|---|
| Effort | Medium. Needs a Print Format on some doctype — and the natural subject is *the route*, which is not a document. It would have to hang off Project and re-derive the stops |
| Phone in a truck | N/A — it is paper, which is its point |
| Printable | Excellent |
| Stays in sync | **No, and that is inherent.** Paper is a snapshot; a PO received after printing is invisible |
| Maintenance | Medium — plus it is **blocked on the PDF generator**, which is currently broken in both backends (see [pdf-generation.md](pdf-generation.md)) |

### (c) An inline HTML block in the dialog — **recommended**

Each stop row in the left pane gains a disclosure showing that stop's PO lines.

| | |
|---|---|
| Effort | **Low.** The data is already in the payload; this is a template change in one file |
| Phone in a truck | **Best.** Same surface the driver already has, collapsed by default so the stop list stays scannable |
| Poor signal | **Best.** Zero additional fetch — once the dialog is open the detail is already client-side. On a bad connection the map degrades and the item list still works |
| Printable | Weak on its own — browser print of a dialog is unreliable |
| Stays in sync | **Yes, by construction.** Same payload, same stop-selection rule, one source of truth |
| Maintenance | Lowest — no new endpoint, no new permission surface, no second query |

## STEP 3 — recommendation

**(c), and treat (b) as a separate follow-up only if paper is actually wanted.**

The deciding argument is not effort, it is *agreement*. The map already decides which
suppliers have material outstanding, using a rule that took a round of production data to
get right (`per_received`, not `status`). Options (a) and (b) both re-derive that rule
somewhere else, and the moment the two disagree the driver is holding a sheet that
contradicts the screen. Option (c) renders the same payload the map is already drawing, so
they cannot diverge.

Second argument: this is used in the field. (c) is the only option that needs no network at
the moment it is read.

### Proposed content, per line

Confirmed: **no pricing.** A driver needs to know what to collect and check it at the
counter; unit rates on a sheet handed across that counter is a conversation nobody wants.

| Field | Source | Notes |
|---|---|---|
| Item code | `items[].item_code` | |
| Description | `items[].item_name` | truncated, full text on hover |
| Qty ordered | `items[].qty` | |
| Qty received | `items[].received_qty` | |
| **Still to collect** | `qty - received_qty` | the number that matters; the others are context |
| UOM | `items[].uom` | |
| PO number + status | `purchase_orders[].name`, `.status` | groups the lines |
| Required by | `purchase_orders[].required_by` | |

**Grouped by PO within a stop**, not flat: a stop can carry several orders and the counter
staff will ask which one. Lines fully received are dimmed rather than hidden — "we already
got that" is useful at a counter.

**Collapsed by default**, one disclosure per stop. The left pane's job is the route; the
detail is what you open when you arrive.

`grand_total` and `currency` are in the payload and will simply not be rendered.

## Open question before the POC

Should fully-received lines appear at all? Dimming them costs nothing and answers "did we
already collect that?" at the counter — but it makes the list longer on a phone, and there
are currently **zero partly-received POs on production**, so no live example either way.

**Resolved: dimmed, not hidden.** At a will-call counter, a line's *absence* is ambiguous
between "we already collected that" and "it was never ordered" — and only one of those is a
reason to stop arguing with the counter staff. The phone-length cost is real but smaller than
that ambiguity. Implemented as `lineModel().done`, with `QTY_TOLERANCE = 0.005` matching
`procurement_quantities.py` so the two features agree on what "fully received" means.

---

## What was actually built (v1.202.0)

Both surfaces live in
[`pick_routing_map.js`](../erpnext_enhancements/public/js/project_enhancements/pick_routing_map.js).

### (c) In-dialog disclosure — as costed

One `<details>` per stop, collapsed, grouped by PO. Two implementation notes that are not
obvious from the option description:

- **Open/closed state is held on the controller, not in the DOM.** `renderList()` rebuilds
  every row from scratch on each tick, re-route and reorder. State read back off the old
  nodes is lost, so unticking one stop silently collapsed the lines being read on another.
- **Opening a stop does not `recompute()`.** Every re-route is a billable Directions call.
  Looking at what you are collecting must not cost one — the same discipline the existing
  custom-address field already applies by re-routing on blur rather than keystroke.

### (b) Pick sheet — built as a browser print view, *not* a Frappe Print Format

This is the one real departure from the option as costed, and it was forced by two things:

1. **The optimised stop order exists only in the browser.** Google returns `waypoint_order`
   to the dialog; the server never sees it. A Print Format would have had to re-query and
   would have printed in purchase-order sequence — a sheet contradicting the screen it was
   printed from. That is precisely the divergence this spike rejected option (a) over, so
   reproducing it in (b) would have been incoherent.
2. **Both server-side PDF backends are broken on this host** (see
   [pdf-generation.md](pdf-generation.md)). A Print Format would have shipped unusable.

What the option promised — "a printable sheet the driver carries, grouped by stop in route
order" — is delivered. The mechanism differs. The stated cost of (b) still applies and is
written into the code: paper is a snapshot, so a PO received after printing is invisible.

The sheet adds a tick box per line, which the costing did not mention and which is the point
of a pick sheet: it is checked off at the counter.

### One renderer for the numbers, two for the layout

`lineModel()` computes ordered / received / still-to-collect / done **once**; the disclosure
and the sheet both consume it. The layouts differ deliberately — a 300px-wide dialog pane
gets a compact list, paper gets a table — but the arithmetic does not fork, so the sheet in a
driver's hand cannot disagree with the screen.

## Routing engine (v1.204.0)

The map can use either engine. **Travel Settings → "Use Routes API (beta)"**, off by default,
picks which is tried first; the legacy one always remains as an automatic fallback.

| Rung | Engine | Notes |
|---|---|---|
| 1 | `routes.Route.computeRoutes` | Only when the setting is on. Needs **Routes API** enabled on the Cloud project *and* on the key |
| 2 | `DirectionsService` | Deprecated 2026-02-25, not scheduled for removal, ≥12 months' notice promised |
| 3 | Geocoded pins in purchase-order order | No optimisation |
| 4 | Plain list + Google Maps deep links | Works with no key at all |

**Turn the setting on only after the console change**, in this order: enable Routes API on the
project → add it to the key's API restriction list → tick the setting. Doing it the other way
round means every route falls to rung 2 until Google is updated, which works but silently
costs a legacy call each time.

The two engines return different field names for the same numbers, and the differences do not
throw — they yield `undefined`. The three that matter are commented at the point of use in
`pick_routing_map.js`; the worst is `durationMillis` (Routes) against `duration.value`
(legacy, **seconds**), which is a 1000x error if the arithmetic is copied across.

Verified against a live key on 2026-08-01, so these no longer need checking:

| Question | Answer |
|---|---|
| `optimizedIntermediateWaypointIndices` semantics | a real `Array`, zero-based — `[1, 0]` for a two-stop run |
| …for a run with nothing to optimise | **`[-1]`** — a sentinel meaning "not reordered", not an error. A one-stop run returns this, and it is treated as submission order |
| Is `'viewport'` a legal field-mask string? | **yes**, and it returns a real `LatLngBounds` |
| `leg.startLocation.lat` — property or method? | **property** (a number) |
| `route.legs` length | **one more than the stop count** (origin→A, A→B, B→finish) — same as the legacy engine |
| `leg.localizedValues` | logs as an **obfuscated object**; `.distance` cannot be assumed to be a string, so it is type-checked and falls back to formatting `distanceMeters` |

Still unverified: the rejection shape of `computeRoutes`. It has a fallback, so the map works
either way.

**The optimised order is validated, not trusted.** Every index must be a whole number inside
the submitted stops, no duplicates, covering each stop exactly once, with the array length
matching. Anything else degrades to purchase-order sequence with a message rather than being
patched up — an order that is merely *incomplete* would drop a supplier from a driver's run
while still looking like a valid optimised route, which is worse than not optimising at all.

## Production notes found while building

- **The Directions API is not enabled on the Maps key.** Live console on PRJ-00567:
  `Directions Service: This API key is not authorized to use this service or API` →
  `MapsRequestError: DIRECTIONS_ROUTE: REQUEST_DENIED`. The map is therefore permanently in
  degradation step two — geocoded pins in PO order, no drive-time optimisation. This is a
  Google Cloud Console API-restrictions change, not a code fix. Until it is done, the pick
  sheet correctly prints "Stops are in purchase-order sequence, not drive-time order."
- **Most stops have no resolvable address.** PRJ-00567: 2 of 5 routable. PRJ-00566: 3 of 6.
  That is why the line detail is rendered on no-address stops too — those are exactly the
  ones somebody has to ring, and the first question back is what they are collecting.
- **Multi-PO stops are the norm, not the edge case.** PRJ-00566's Harrington stop carries
  **6 POs and 42 lines**. Grouping by PO is load-bearing, not decoration.
- **Still no partly-received PO exists on production**, so the dimmed "already collected"
  state has been exercised only against constructed data, not live records.
