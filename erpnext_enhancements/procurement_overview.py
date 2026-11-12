"""What each Procurement Tracker line is waiting on, and the tracker by item (v1.806.0).

Pure functions, no ``frappe`` import, for the same reason as ``procurement_quantities``:
``project_enhancements/__init__.py`` imports ``frappe``, so nothing under it can be tested
without a bench, and this is the part worth testing. ``tests/test_procurement_overview.py``
runs it in CI.

Three things are decided here, each once, for the tracker in the browser and for the
``project_procurement_status`` assistant tool alike.

**A line's state, in the words the filter uses.** ``order_status`` and ``receive_status`` stay
exactly as ``procurement_quantities`` computes them. They answer two separate questions, and a
person scanning a job asks one: *what is this line waiting on?* :func:`line_state` answers
**To order**, **Ordered**, **Partly received**, **Received**, **Late** or **Closed** from the
same per-line quantities, never from a parent document's label:

* *To order* -- something asked for is not on a submitted order yet. A draft order is not an
  order (``procurement_quantities``), so a line that sits only on a draft is still to order.
* *Ordered* / *Partly received* / *Received* -- by what has arrived against what is on order.
* *Late* -- still waiting, and its date has passed: the order line's expected date, or the
  request line's required-by date when nothing is ordered.
* *Closed* -- still short, and nobody is chasing it: the order was closed (or delivered
  straight to the customer), or the request was stopped. On production 148 order lines sat on
  orders closed with nothing received; calling those "late" would bury the handful that are.

**"Needs action"** is every line still waiting on an order or a delivery. Closed, quote-only
and invoice-only lines never need action.

**The tracker by item.** :func:`by_item` is one row per item for the project: requested,
ordered and received across every document, the suppliers, the documents, and where the item
is now. It de-duplicates on the child row before it sums, for the reason
``procurement_quantities.dedupe_lines`` gives, and it leaves quotes and bills out of the
quantities: a quotation is not an ask and a bill is not an order.

**Where it is now** comes from ``inventory_enhancements.project_stock`` (the stock ledger's
rows that name the job). :func:`attach` puts it on every line and every item, and says plainly
when there is nothing to say: a non-stock item's location is not tracked, and that is a fact
about the item, not a blank.

Indentation is tabs (new file, as ``procurement_quantities``).
"""

from erpnext_enhancements import procurement_quantities as quantities
from erpnext_enhancements.inventory_enhancements import project_stock_rules as stock_rules

TOLERANCE = quantities.TOLERANCE

# ---------------------------------------------------------------------------
# Line states
# ---------------------------------------------------------------------------

TO_ORDER = "to_order"
ORDERED = "ordered"
PARTLY_RECEIVED = "partly_received"
RECEIVED = "received"
LATE = "late"
CLOSED = "closed"
QUOTE_ONLY = "quote_only"
INVOICE_ONLY = "invoice_only"

#: What the filter offers, in the order it offers it.
FILTER_STATES = (TO_ORDER, ORDERED, PARTLY_RECEIVED, RECEIVED, LATE)

STATE_LABELS = {
	TO_ORDER: "To order",
	ORDERED: "Ordered",
	PARTLY_RECEIVED: "Partly received",
	RECEIVED: "Received",
	LATE: "Late",
	CLOSED: "Closed",
	QUOTE_ONLY: "Quote only",
	INVOICE_ONLY: "Bill only",
}

#: Which state a line is *shown* as when it has more than one, most urgent first. The order is
#: also the sort order of the Status column: one click puts what has to be chased at the top.
STATE_RANK = (LATE, TO_ORDER, ORDERED, PARTLY_RECEIVED, RECEIVED, CLOSED, QUOTE_ONLY, INVOICE_ONLY)

#: Where-now kinds a line can be filtered by, beside the kinds of place.
NOT_RECEIVED = "not_received"
NOT_TRACKED = "not_tracked"

LOCATION_FILTERS = (
	stock_rules.KIND_SHELF,
	stock_rules.KIND_SITE,
	stock_rules.KIND_BIN,
	stock_rules.KIND_TAKEN,
	stock_rules.KIND_RETURNED,
	NOT_RECEIVED,
	NOT_TRACKED,
)

LOCATION_LABELS = {
	stock_rules.KIND_SHELF: "Project Shelf",
	stock_rules.KIND_SITE: "Job site",
	stock_rules.KIND_BIN: "Shop bins",
	stock_rules.KIND_TAKEN: "Taken to the job",
	stock_rules.KIND_RETURNED: "Returned to supplier",
	NOT_RECEIVED: "Not received yet",
	NOT_TRACKED: "Location not tracked",
}

#: Kinds of place the "Shop bins" filter covers: anywhere in the shop that is not the shelf.
_SHOP_KINDS = frozenset({stock_rules.KIND_BIN, stock_rules.KIND_VEHICLE, stock_rules.KIND_BONEYARD})

_QUOTE_DOCTYPES = frozenset({"Request for Quotation", "Supplier Quotation"})

NOT_TRACKED_TEXT = "Location not tracked (not a stock item)"
NOT_RECEIVED_TEXT = "Not received yet"
#: Nothing arrived and nothing is coming: the order was closed, or the request stopped.
NEVER_RECEIVED_TEXT = "Not received"


def _qty(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _date(value):
	"""A date as ``YYYY-MM-DD`` text, or ``""``. ISO dates compare correctly as text."""
	return str(value)[:10] if value else ""


def _chainless(row, doctypes):
	"""Whether the row is a document of ``doctypes`` that never joined a request or an order."""
	return (
		row.get("source_doc_type") in doctypes
		and not row.get("mr")
		and not row.get("po")
		and not row.get("mr_item")
		and not row.get("po_item")
	)


def line_state(row, today=None):
	"""What one line is waiting on: ``{"states", "state", "state_label", "needs_action",
	"is_late", "waiting_qty", "to_order_qty"}``.

	``states`` is every state the line is in (a half-ordered, half-arrived line is both *To
	order* and *Partly received*); ``state`` is the one it is shown as (:data:`STATE_RANK`).
	``today`` is ``YYYY-MM-DD``; without it nothing is late.
	"""
	if _chainless(row, _QUOTE_DOCTYPES):
		return _state([QUOTE_ONLY], False, 0.0, 0.0)
	if _chainless(row, {"Purchase Invoice"}):
		return _state([INVOICE_ONLY], False, 0.0, 0.0)

	ordered = _qty(row.get("ordered_qty"))
	received = _qty(row.get("received_qty"))
	draft = _qty(row.get("draft_ordered_qty"))
	requested = row.get("requested_qty")
	to_order = max(_qty(requested) - ordered, 0.0) if requested is not None else 0.0
	if ordered <= TOLERANCE and draft > TOLERANCE:
		# Only on a draft order: nothing has been placed with anybody yet.
		to_order = max(to_order, draft)
	waiting = max(ordered - received, 0.0)

	states = []
	if to_order > TOLERANCE:
		states.append(TO_ORDER)
	if ordered > TOLERANCE:
		if received <= TOLERANCE:
			states.append(ORDERED)
		elif waiting > TOLERANCE:
			states.append(PARTLY_RECEIVED)
		else:
			states.append(RECEIVED)
	elif received > TOLERANCE:
		# Goods with no order behind them. They are here, which is what the label says.
		states.append(RECEIVED)

	stopped = row.get("mr_status") == "Stopped"
	if stopped and TO_ORDER in states:
		# A stopped request asks for nothing more.
		states = [s for s in states if s != TO_ORDER]
		to_order = 0.0
		if not states:
			states = [CLOSED]
	if waiting > TOLERANCE and _order_is_settled(row, waiting):
		states = [s for s in states if s not in (ORDERED, PARTLY_RECEIVED)] + [CLOSED]
		waiting = 0.0

	open_wait = any(s in states for s in (TO_ORDER, ORDERED, PARTLY_RECEIVED))
	expected = _date(row.get("expected_date"))
	late = bool(open_wait and _own_share_waits(row) and today and expected and expected < _date(today))
	if late:
		states.append(LATE)
	return _state(states or [CLOSED], open_wait, waiting, to_order)


def _own_share_waits(row):
	"""Whether the date on this row is the date of something still to come.

	A request line split across two orders reaches the feed once per order, each row carrying
	the *request's* totals and its own order's date. The order that delivered everything it
	held is not late because the other one is: its date is judged against its own shortfall.
	Every other row (a direct order line, a request line with no order yet) is judged whole.
	"""
	if not row.get("mr_item") or not row.get("po_item") or row.get("po_line_qty") is None:
		return True
	return _qty(row.get("po_line_qty")) - _qty(row.get("po_line_received_qty")) > TOLERANCE


def _order_is_settled(row, waiting):
	"""Whether what the line still waits for sits on an order nobody is chasing.

	A direct order line is judged by its own order. A request line carries the *request's*
	totals and only one of its orders (the feed's fan-out), so it is closed only when this
	order is settled and this order's own shortfall covers everything the request still lacks.
	"""
	if row.get("po_status") not in quantities.SETTLED_PO_STATUSES:
		return False
	if not row.get("mr_item"):
		return True
	short = _qty(row.get("po_line_qty")) - _qty(row.get("po_line_received_qty"))
	return short + TOLERANCE >= waiting


def _state(states, needs_action, waiting, to_order):
	ordered = sorted(set(states), key=STATE_RANK.index)
	return {
		"states": ordered,
		"state": ordered[0],
		"state_label": STATE_LABELS[ordered[0]],
		"needs_action": bool(needs_action),
		"is_late": LATE in ordered,
		"waiting_qty": round(waiting, 6),
		"to_order_qty": round(to_order, 6),
	}


# ---------------------------------------------------------------------------
# By item
# ---------------------------------------------------------------------------

_CHAIN_DOCTYPES = (
	("Material Request", "mr"),
	("Request for Quotation", "rfq"),
	("Supplier Quotation", "sq"),
	("Purchase Order", "po"),
	("Purchase Receipt", "pr"),
	("Purchase Invoice", "pi"),
)


def stock_quantities(row):
	"""A line's requested / ordered / received / draft quantities in the item's stock unit.

	A request line is already in the stock unit (``procurement_quantities``). A direct order
	line is in the unit it was written in, so it is multiplied by the line's conversion factor
	(1 on every line on production today, which is exactly why this is written down).
	"""
	factor = 1.0
	if not row.get("mr_item"):
		factor = _qty(row.get("conversion_factor")) or 1.0
	requested = row.get("requested_qty")
	return {
		"requested_qty": None if requested is None else _qty(requested) * factor,
		"ordered_qty": _qty(row.get("ordered_qty")) * factor,
		"received_qty": _qty(row.get("received_qty")) * factor,
		"draft_ordered_qty": _qty(row.get("draft_ordered_qty")) * factor,
	}


def _counts_toward_quantities(row):
	return not _chainless(row, _QUOTE_DOCTYPES | {"Purchase Invoice"})


def by_item(rows, today=None):
	"""One row per item for the project, in item-code order.

	Each is ``{"item_code", "item_name", "stock_uom", "line_count", "requested_qty",
	"ordered_qty", "received_qty", "draft_ordered_qty", "order_status", "receive_status",
	"states", "state", "state_label", "needs_action", "is_late", "waiting_qty",
	"to_order_qty", "expected_date", "suppliers", "received_at", "documents"}``.

	``requested_qty`` is ``None`` when no line of the item has a request behind it, the same
	"nobody asked" that a direct order line carries. ``documents`` is ``{doctype: [names]}``
	over every row of the item, quotes and bills included: they are part of its paper trail even
	though they are left out of its quantities.
	"""
	groups = {}
	for row in rows or ():
		groups.setdefault(row.get("item_code") or "", []).append(row)

	out = []
	for item_code, item_rows in groups.items():
		counted = [r for r in item_rows if _counts_toward_quantities(r)]
		lines = quantities.dedupe_lines(counted)
		requested = None
		ordered = received = draft = waiting = to_order = 0.0
		states, expected = set(), ""
		# Quantities from each line ONCE (the fan-out repeats a request line per order)...
		for line in lines:
			qty = stock_quantities(line)
			if qty["requested_qty"] is not None:
				requested = (requested or 0.0) + qty["requested_qty"]
			ordered += qty["ordered_qty"]
			received += qty["received_qty"]
			draft += qty["draft_ordered_qty"]
			state = line_state(line, today)
			waiting += state["waiting_qty"]
			to_order += state["to_order_qty"]
		# ...and states and dates from every row: each order of a split line has its own date,
		# and the one that is late must not depend on which copy the de-duplication kept.
		for row in counted:
			state = line_state(row, today)
			states.update(state["states"])
			if state["needs_action"] and _own_share_waits(row):
				date = _date(row.get("expected_date"))
				if date and (not expected or date < expected):
					expected = date
		if not lines:
			# Only quotes or bills name this item. It still lists, as what it is.
			states.update(line_state(item_rows[0], today)["states"])
		rollup = quantities.rollup_quantity_progress(lines)
		ranked = sorted(states or {CLOSED}, key=STATE_RANK.index)
		documents = {}
		for doctype, field in _CHAIN_DOCTYPES:
			names = sorted({r.get(field) for r in item_rows if r.get(field)})
			if names:
				documents[doctype] = names
		first = item_rows[0]
		out.append(
			{
				"item_code": item_code,
				"item_name": next((r.get("item_name") for r in item_rows if r.get("item_name")), "") or item_code,
				"stock_uom": next((r.get("stock_uom") for r in item_rows if r.get("stock_uom")), None),
				"row_id": "item#" + (item_code or first.get("row_id") or ""),
				"line_count": len(lines),
				"requested_qty": requested,
				"ordered_qty": ordered,
				"received_qty": received,
				"draft_ordered_qty": draft,
				"order_status": rollup["order_status"],
				"receive_status": rollup["receive_status"],
				"states": ranked,
				"state": ranked[0],
				"state_label": STATE_LABELS[ranked[0]],
				"needs_action": any(s in ranked for s in (TO_ORDER, ORDERED, PARTLY_RECEIVED)),
				"is_late": LATE in ranked,
				"waiting_qty": round(waiting, 6),
				"to_order_qty": round(to_order, 6),
				"expected_date": expected or None,
				"suppliers": sorted({r.get("supplier") for r in item_rows if r.get("supplier")}),
				"received_at": sorted({r.get("warehouse") for r in item_rows if r.get("warehouse")}),
				"documents": documents,
			}
		)
	out.sort(key=lambda r: stock_rules.natural_key(r["item_code"] or r["item_name"] or ""))
	return out


# ---------------------------------------------------------------------------
# Where it is now
# ---------------------------------------------------------------------------


def where_now(found, received=None, waiting=False):
	"""The where-now block for one item: ``{"location_tracked", "where_now", "where_now_text",
	"location_kinds"}``.

	``found`` is the item's entry from ``project_stock.places_for`` (``None`` when ERPNext does
	not know the item, or the lookup failed). ``received`` is what the purchasing documents say
	arrived, in the stock unit; ``waiting`` whether anything is still to come.

	``location_kinds`` is what the location filter matches on: the kinds of place, with every
	shop location other than the shelf folded into "shop bins", plus *not received yet* and
	*not tracked*. ``supplier_returns`` is each Supplier Return that names the item for the job
	(``project_stock_rules.return_summary``), which the tracker lists under the places.
	"""
	kinds = []
	if found is None:
		# Nothing is known about the item's stock: say nothing rather than guess.
		text = NOT_RECEIVED_TEXT if waiting and _qty(received) <= TOLERANCE else ""
		if waiting:
			kinds.append(NOT_RECEIVED)
		return {
			"location_tracked": None,
			"where_now": [],
			"where_now_text": text,
			"location_kinds": kinds,
			"supplier_returns": [],
		}
	# The Supplier Returns that name the item for this job, each with its status: shown under the
	# places, for a non-stock item too.
	returns = list(found.get("supplier_returns") or ())
	if not found.get("is_stock_item"):
		kinds.append(NOT_TRACKED)
		if any(r.get("gone") for r in returns):
			# Its place is not tracked, but that it went back is on record.
			kinds.append(stock_rules.KIND_RETURNED)
		if waiting:
			kinds.append(NOT_RECEIVED)
		return {
			"location_tracked": False,
			"where_now": [],
			"where_now_text": NOT_TRACKED_TEXT,
			"location_kinds": kinds,
			"supplier_returns": returns,
		}
	places = stock_rules.with_unrecorded(found.get("places"), received)
	for place in places:
		kind = stock_rules.KIND_BIN if place["kind"] in _SHOP_KINDS else place["kind"]
		if kind not in kinds:
			kinds.append(kind)
	if waiting:
		kinds.append(NOT_RECEIVED)
	text = stock_rules.places_text(places)
	if not text:
		text = NOT_RECEIVED_TEXT if waiting else NEVER_RECEIVED_TEXT
	return {
		"location_tracked": True,
		"where_now": places,
		"where_now_text": text,
		"location_kinds": kinds,
		"supplier_returns": returns,
	}


def attach(rows, found_items=None, today=None):
	"""Put the state and the where-now on every line, and return the by-item rows with theirs.

	``rows`` are the feed's item rows (mutated in place: the same dict sits under every document
	its chain touches, which is what makes one pass enough). ``found_items`` is
	``project_stock.places_for(...)["items"]``, or ``None`` when the lookup failed: the states
	are still attached, and the where-now says nothing.

	The where-now is **per item for the project**, not per line: ERPNext keeps stock by item,
	so two lines of one item share one answer, and ``where_now_lines`` on each row says how many
	lines that answer is spread over.
	"""
	rows = list(rows or ())
	items = by_item(rows, today)
	blocks = {}
	for item in items:
		found = None if found_items is None else found_items.get(item["item_code"])
		block = where_now(found, item["received_qty"], item["needs_action"])
		item.update(block)
		blocks[item["item_code"]] = (block, item["line_count"])
	seen = set()
	for row in rows:
		if id(row) in seen:
			continue
		seen.add(id(row))
		state = line_state(row, today)
		row.update(
			{
				"line_states": state["states"],
				"line_state": state["state"],
				"line_state_label": state["state_label"],
				"needs_action": state["needs_action"],
				"is_late": state["is_late"],
				"waiting_qty": state["waiting_qty"],
				"to_order_qty": state["to_order_qty"],
				# The old Warehouse column, under the name that says what it is.
				"received_at": row.get("warehouse"),
			}
		)
		block, line_count = blocks.get(row.get("item_code") or "", (where_now(None), 0))
		kinds = [k for k in block["location_kinds"] if k != NOT_RECEIVED]
		if state["needs_action"]:
			kinds.append(NOT_RECEIVED)
		row.update(
			{
				"location_tracked": block["location_tracked"],
				"where_now": block["where_now"],
				"where_now_text": block["where_now_text"],
				"where_now_lines": line_count,
				"location_kinds": kinds,
				"supplier_returns": block["supplier_returns"],
			}
		)
	return items


def filter_options(rows, items):
	"""What the tracker's filters can offer for this project: the suppliers on its lines, and
	the states and locations that at least one line is in. A filter that matches nothing is not
	offered."""
	suppliers = sorted({r.get("supplier") for r in rows or () if r.get("supplier")})
	states = [s for s in FILTER_STATES if any(s in (r.get("line_states") or ()) for r in rows or ())]
	kinds = set()
	for source in (rows or (), items or ()):
		for row in source:
			kinds.update(row.get("location_kinds") or ())
	return {
		"suppliers": suppliers,
		"states": [{"value": s, "label": STATE_LABELS[s]} for s in states],
		"locations": [{"value": k, "label": LOCATION_LABELS[k]} for k in LOCATION_FILTERS if k in kinds],
	}
