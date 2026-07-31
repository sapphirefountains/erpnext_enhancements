"""Per-line quantity and status arithmetic for the Project Procurement Tracker.

One place where "how much was asked for, how much is on order, how much arrived" is
decided, because the tracker asks that question at two levels and the two levels used
to answer it differently. The item row inherited its parent Material Request's header
``status`` — ``mr.status`` selected onto a child-grain row — so every line of a
partially-ordered request read "Partially Ordered", including the lines that were
fully ordered. Meanwhile the quantity column derived ordered-ness from a *different*
number again. Two calculations, two answers, one of them wrong.

Pure functions, no ``frappe`` import: the math is unit-testable without a bench, which
is where ``tests/test_procurement_quantities.py`` exercises it in CI. Callers do the
fetching.

Lives at the app root rather than beside its caller in ``project_enhancements/`` for
two reasons. It joins the other root-level procurement helpers (``procurement_project``,
``po_approval``, ``po_segregation``) — there is no procurement module. And
``project_enhancements/__init__.py`` imports ``frappe``, so a submodule of it cannot be
imported without a bench no matter how pure it is; the app-root ``__init__`` carries
nothing but ``__version__``. Same invariant as ``water_engineering/engine``, reached a
different way.

Five decisions are baked in here, and each one is load-bearing.

**Read ERPNext's denormalized rollups; never SUM over the feed's join.** The feed's
Purchase Order join matches ``supplier_quotation_item OR material_request_item``, so
one request line split across two Purchase Orders arrives as two rows. Anything summed
across those rows double-counts. ``Material Request Item.ordered_qty`` is maintained by
ERPNext's ``status_updater``, is *per line*, and is therefore immune by construction.
It was checked against ``SUM(Purchase Order Item.stock_qty)`` over every MR-linked
submitted Purchase Order line on production: zero discrepancies. It also already
handles amendments, cancellations and returns — three cases with almost no live
examples here, which is exactly where a hand-rolled sum would be wrong and nobody
would notice.

**Stock UOM is the basis.** ``ordered_qty`` and ``received_qty`` on a Material Request
line are maintained against ``stock_qty`` (``status_updater`` uses
``target_ref_field="stock_qty"``), so ``stock_qty`` is the only denominator that makes
the ratio mean anything. Every line on this site currently has
``conversion_factor = 1``, so ``qty`` and ``stock_qty`` are indistinguishable in live
data — which is precisely why the choice has to be deliberate now rather than
discovered later by a 120 FT line reading as 12000% ordered.

**Only submitted orders count as ordered.** Ten Purchase Order Item rows against draft
Purchase Orders are linked to Material Request lines on production today, and they
inflate the tracker's ordered figure while ``Material Request Item.ordered_qty``
excludes them — so the tracker and the Material Request form it links to currently
disagree. Draft quantities are reported separately as ``draft_ordered_qty``, never
folded in.

**A line with nothing ordered is Not Ordered, not "ordered for the amount asked".**
The feed used to substitute the requested quantity when no Purchase Order line joined
(``ordered_qty if ordered_qty > 0 else mr_qty``), so an untouched line rendered as
``4 / 0`` beneath a header reading "Qty (Ord / Rec)" and looked fully ordered.

**Nothing is clamped.** Over-ordering and over-receipt get their own statuses and their
percentages exceed 100. The defect being fixed was a number quietly substituted to make
a line look complete; clamping anywhere in here would repeat it in a new place.
"""

# Ordered vocabulary, in workflow order — worst first. The order is the contract:
# rollups and (later) column sorting both read it as a progression, so appending a
# state in the wrong position silently changes both.
NOT_ORDERED = "Not Ordered"
PARTIALLY_ORDERED = "Partially Ordered"
ORDERED = "Ordered"
OVER_ORDERED = "Over Ordered"

ORDER_STATUSES = (NOT_ORDERED, PARTIALLY_ORDERED, ORDERED, OVER_ORDERED)

# Received vocabulary. Deliberately distinct strings from the ordered set: the tracker
# paints both through one colour mapper, and an "Ordered" arriving where "Received" was
# meant would silently pick up the wrong badge rather than fail.
NOT_RECEIVED = "Not Received"
PARTIALLY_RECEIVED = "Partially Received"
RECEIVED = "Received"
OVER_RECEIVED = "Over Received"

RECEIVE_STATUSES = (NOT_RECEIVED, PARTIALLY_RECEIVED, RECEIVED, OVER_RECEIVED)

# Quantities carry UOM conversions and accumulate over several receipts, so exact
# equality is unsafe — without a tolerance a finished line reads "Partially Received"
# forever. Half a hundredth is far below anything this business transacts and far above
# float noise.
TOLERANCE = 0.005


def _qty(value):
	"""Coerce a quantity to float. ``None``, ``""`` and junk all mean zero.

	Quantities arrive straight off ``frappe.db.sql``, where a ``LEFT JOIN`` that
	matched nothing yields ``None`` rather than 0.
	"""
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _requested(value):
	"""Requested quantity, preserving the difference between *none asked for* and *zero*.

	A direct Purchase Order line has no Material Request behind it, so its requested
	quantity is ``None`` — genuinely absent, not zero. The feed used to conflate the
	two and print ``0``, which reads as "somebody asked for nothing".
	"""
	if value is None or value == "":
		return None
	return _qty(value)


def order_status(requested, ordered):
	"""Status of one line against its own requested quantity.

	Never consults the parent document. That inheritance is the bug this module exists
	to end.
	"""
	ordered = _qty(ordered)
	requested = _requested(requested)

	if ordered <= TOLERANCE:
		return NOT_ORDERED
	# No request behind the line (a direct Purchase Order): the order *is* the ask, so
	# anything on order is fully ordered. Never "over".
	if requested is None:
		return ORDERED
	if ordered > requested + TOLERANCE:
		return OVER_ORDERED
	if ordered + TOLERANCE >= requested:
		return ORDERED
	return PARTIALLY_ORDERED


def receive_status(ordered, received):
	"""Status of one line's receipts against what is actually on order for it.

	Measured against *ordered*, not *requested*: you cannot receive what was never
	ordered, and a line requested-but-unordered is an ordering problem, not a
	receiving one.
	"""
	ordered = _qty(ordered)
	received = _qty(received)

	if received <= TOLERANCE:
		return NOT_RECEIVED
	# Goods against nothing on order. Rare, but it is over-receipt rather than
	# completion, and saying so is the point of having the state.
	if ordered <= TOLERANCE:
		return OVER_RECEIVED
	if received > ordered + TOLERANCE:
		return OVER_RECEIVED
	if received + TOLERANCE >= ordered:
		return RECEIVED
	return PARTIALLY_RECEIVED


def _percent(part, whole):
	"""``part`` as a percentage of ``whole``. Zero whole means zero, not a crash.

	Uncapped on purpose: an over-receipt reads 120%, not 100%.
	"""
	whole = _qty(whole)
	if whole <= TOLERANCE:
		return 0.0
	return round((_qty(part) / whole) * 100, 2)


def quantity_progress(requested, ordered, received, draft_ordered=0):
	"""The full per-line picture, as the dict the feed merges onto an item row.

	``requested`` may be ``None`` for a direct Purchase Order line. ``draft_ordered``
	is quantity sitting on unsubmitted Purchase Orders — reported, never counted.

	``ordered_qty`` / ``received_qty`` / ``completion_percentage`` keep the names the
	feed already emitted, because ``assistant_tools/project_procurement_status.py``
	sums the first two and renaming them would break that tool in silence. Their
	*meaning* does change: ``ordered_qty`` is now genuinely ordered quantity rather
	than a requested-quantity substitute.
	"""
	requested_qty = _requested(requested)
	ordered_qty = _qty(ordered)
	received_qty = _qty(received)
	draft_qty = _qty(draft_ordered)

	base = requested_qty if requested_qty is not None else ordered_qty

	return {
		"requested_qty": requested_qty,
		"ordered_qty": ordered_qty,
		"received_qty": received_qty,
		"draft_ordered_qty": draft_qty,
		"outstanding_order_qty": max(0.0, (requested_qty or 0.0) - ordered_qty),
		"outstanding_receipt_qty": max(0.0, ordered_qty - received_qty),
		"over_received_qty": max(0.0, received_qty - ordered_qty),
		"order_status": order_status(requested_qty, ordered_qty),
		"receive_status": receive_status(ordered_qty, received_qty),
		"percent_ordered": _percent(ordered_qty, base),
		# Kept for backward compatibility: the feed's "Status" column and the MCP tool
		# both read it. Now measured against what was ordered rather than against a
		# denominator that fell back to the requested quantity.
		"completion_percentage": _percent(received_qty, ordered_qty),
	}


def rollup_quantity_progress(rows):
	"""Aggregate per-line progress dicts into one document-level answer.

	The direction is the whole point: a document is derived from its lines, never a
	line from its document. Deriving a line from its document is the defect.

	Status is decided by the *set* of line statuses, not by a quantity-weighted
	percentage. ``MAT-MR-2026-00001`` is 98.9% ordered by quantity with one line
	untouched; weighted, it rounds to "basically done", which is how the untouched
	line stayed invisible. All-rows also makes the document badge agree with its own
	line badges by construction.

	Callers must de-duplicate first — see the module docstring on the ``OR``-join
	fan-out. This function trusts the rows it is given.
	"""
	rows = list(rows or [])
	if not rows:
		return {
			"requested_qty": 0.0,
			"ordered_qty": 0.0,
			"received_qty": 0.0,
			"draft_ordered_qty": 0.0,
			"order_status": NOT_ORDERED,
			"receive_status": NOT_RECEIVED,
			"percent_ordered": 0.0,
			"completion_percentage": 0.0,
			"item_count": 0,
		}

	requested_total = sum(_qty(r.get("requested_qty")) for r in rows)
	ordered_total = sum(_qty(r.get("ordered_qty")) for r in rows)
	received_total = sum(_qty(r.get("received_qty")) for r in rows)
	draft_total = sum(_qty(r.get("draft_ordered_qty")) for r in rows)

	return {
		"requested_qty": requested_total,
		"ordered_qty": ordered_total,
		"received_qty": received_total,
		"draft_ordered_qty": draft_total,
		"order_status": _rollup_status([r.get("order_status") for r in rows], ORDER_STATUSES),
		"receive_status": _rollup_status([r.get("receive_status") for r in rows], RECEIVE_STATUSES),
		"percent_ordered": _percent(ordered_total, requested_total or ordered_total),
		"completion_percentage": _percent(received_total, ordered_total),
		"item_count": len(rows),
	}


def _rollup_status(statuses, vocabulary):
	"""Collapse a set of line statuses into one document status.

	``vocabulary`` is the workflow-ordered 4-tuple (none, partial, all, over). All the
	same means that; anything mixed is partial; an "over" anywhere wins outright,
	because a document that has over-received is not merely "received". Unrecognised
	values are ignored rather than crashing the feed — ERPNext's own status strings
	reach this code path on other doctypes.
	"""
	none_label, partial_label, all_label, over_label = vocabulary
	known = [s for s in statuses if s in vocabulary]
	if not known:
		return none_label

	if over_label in known:
		return over_label

	distinct = set(known)
	if distinct == {none_label}:
		return none_label
	if distinct == {all_label}:
		return all_label
	return partial_label
