# Pick Routing Map — showing PO detail (spike)

**Status: recommendation, awaiting a direction. No POC built yet** — the task asked for the
read-out and the options first.

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
