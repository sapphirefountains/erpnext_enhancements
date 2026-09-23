"""Bench-free tests for the Procurement Tracker's quantity and status math.

Plain pytest functions — ``erpnext_enhancements.procurement_quantities`` imports only
the stdlib, and the app-root ``__init__`` carries nothing but ``__version__``, so this
needs no Frappe site and no stub.

Run: python -m pytest erpnext_enhancements/tests/test_procurement_quantities.py -q

The suite that matters is :func:`test_item_status_is_not_inherited_from_the_document`.
Everything else guards an edge; that one guards the defect.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.procurement_quantities import (
	NOT_ORDERED,
	NOT_RECEIVED,
	OPEN_RULES,
	ORDER_STATUSES,
	ORDERED,
	OVER_ORDERED,
	OVER_RECEIVED,
	PARTIALLY_ORDERED,
	PARTIALLY_RECEIVED,
	RECEIVE_STATUSES,
	RECEIVED,
	SETTLED_PO_STATUSES,
	dedupe_lines,
	document_is_open,
	order_status,
	plan_receipt,
	quantity_progress,
	receive_status,
	rollup_quantity_progress,
)

# ---------------------------------------------------------------------------
# The regression fence
# ---------------------------------------------------------------------------


def test_item_status_is_not_inherited_from_the_document():
	"""Three lines of one Material Request, three different statuses, one parent.

	This is the shape of MAT-MR-2026-00001 on PRJ-00566: a request ERPNext labels
	"Partially Ordered" whose lines are individually fully ordered, part ordered and
	untouched. The old feed painted the parent's single label onto all three rows, so
	the fully-ordered line read "Partially Ordered".

	Note the parent's label happens to be correct for exactly one of its three
	children. That coincidence is what made the bug survive a casual look.
	"""
	lines = [
		quantity_progress(requested=4, ordered=4, received=0),
		quantity_progress(requested=4, ordered=2, received=0),
		quantity_progress(requested=4, ordered=0, received=0),
	]

	assert [line["order_status"] for line in lines] == [ORDERED, PARTIALLY_ORDERED, NOT_ORDERED]

	rollup = rollup_quantity_progress(lines)
	assert rollup["order_status"] == PARTIALLY_ORDERED
	assert rollup["requested_qty"] == 12
	assert rollup["ordered_qty"] == 6


def test_a_fully_ordered_request_is_fully_ordered_at_both_levels():
	"""Guards the over-correction: "any partial poisons the parent" must not become
	"any parent poisons the lines"."""
	lines = [quantity_progress(4, 4, 0), quantity_progress(10, 10, 0)]
	assert {line["order_status"] for line in lines} == {ORDERED}
	assert rollup_quantity_progress(lines)["order_status"] == ORDERED


def test_an_untouched_request_shows_nothing_as_partial():
	lines = [quantity_progress(4, 0, 0), quantity_progress(10, 0, 0)]
	assert {line["order_status"] for line in lines} == {NOT_ORDERED}
	assert rollup_quantity_progress(lines)["order_status"] == NOT_ORDERED


def test_received_status_is_independent_of_order_status():
	"""Acceptance criterion 4: the same independence has to hold for receiving.

	A line can be fully ordered and not yet received; collapsing the two axes into one
	badge is what made the original confusion possible.
	"""
	line = quantity_progress(requested=4, ordered=4, received=1)
	assert line["order_status"] == ORDERED
	assert line["receive_status"] == PARTIALLY_RECEIVED


# ---------------------------------------------------------------------------
# Ordered axis
# ---------------------------------------------------------------------------


def test_nothing_ordered_is_not_ordered_not_fully_ordered():
	"""The second half of the reported bug.

	The feed substituted the requested quantity when no Purchase Order line joined
	(``ordered_qty if ordered_qty > 0 else mr_qty``), so an untouched line rendered
	``4 / 0`` under a header reading "Qty (Ord / Rec)" and looked complete.
	"""
	line = quantity_progress(requested=4, ordered=0, received=0)
	assert line["order_status"] == NOT_ORDERED
	assert line["ordered_qty"] == 0
	assert line["requested_qty"] == 4
	assert line["percent_ordered"] == 0.0


def test_over_ordering_is_visible_not_clamped():
	line = quantity_progress(requested=4, ordered=5, received=0)
	assert line["order_status"] == OVER_ORDERED
	assert line["percent_ordered"] == 125.0


def test_a_direct_purchase_order_line_has_no_request_behind_it():
	"""``requested=None`` means "nobody asked", which is not the same as "asked for 0".

	The feed printed 0 for both. A direct Purchase Order line is fully ordered by
	definition — the order is the ask — and can never be "over".
	"""
	line = quantity_progress(requested=None, ordered=7, received=0)
	assert line["requested_qty"] is None
	assert line["order_status"] == ORDERED
	assert line["percent_ordered"] == 100.0


def test_outstanding_order_quantity():
	assert quantity_progress(10, 4, 0)["outstanding_order_qty"] == 6
	# Over-ordered never reports negative outstanding.
	assert quantity_progress(4, 10, 0)["outstanding_order_qty"] == 0


# ---------------------------------------------------------------------------
# Received axis
# ---------------------------------------------------------------------------


def test_over_receipt_is_visible_not_hidden():
	line = quantity_progress(requested=4, ordered=4, received=6)
	assert line["receive_status"] == OVER_RECEIVED
	assert line["over_received_qty"] == 2
	assert line["completion_percentage"] == 150.0


def test_goods_against_nothing_ordered_is_over_receipt():
	assert receive_status(ordered=0, received=3) == OVER_RECEIVED


def test_completion_percentage_measures_against_ordered_not_requested():
	"""The old denominator fell back to the requested quantity, so a line with 4
	requested, 0 ordered and 0 received reported against 4 and looked like a receiving
	problem rather than an ordering one."""
	line = quantity_progress(requested=10, ordered=4, received=2)
	assert line["completion_percentage"] == 50.0
	assert line["outstanding_receipt_qty"] == 2


# ---------------------------------------------------------------------------
# Arithmetic edges
# ---------------------------------------------------------------------------


def test_accumulated_float_error_still_counts_as_complete():
	"""Quantities accumulate over several receipts. Without a tolerance a finished
	line reads "Partially Received" forever."""
	assert order_status(1, 0.1 + 0.2 + 0.7) == ORDERED
	assert receive_status(1, 0.1 + 0.2 + 0.7) == RECEIVED


def test_zero_and_none_never_raise():
	empty = quantity_progress(0, 0, 0)
	assert empty["completion_percentage"] == 0.0
	assert empty["order_status"] == NOT_ORDERED

	# A LEFT JOIN that matched nothing yields None, not 0.
	nulls = quantity_progress(None, None, None)
	assert nulls["ordered_qty"] == 0
	assert nulls["received_qty"] == 0
	assert nulls["order_status"] == NOT_ORDERED
	assert nulls["receive_status"] == NOT_RECEIVED


def test_draft_quantity_is_reported_but_never_counted_as_ordered():
	"""Ten Purchase Order Item rows on draft Purchase Orders are linked to Material
	Request lines on production today. They inflate the tracker while ERPNext's own
	``ordered_qty`` excludes them, so the tracker and the request form disagree."""
	line = quantity_progress(requested=4, ordered=0, received=0, draft_ordered=4)
	assert line["ordered_qty"] == 0
	assert line["draft_ordered_qty"] == 4
	assert line["order_status"] == NOT_ORDERED


# ---------------------------------------------------------------------------
# Rollup
# ---------------------------------------------------------------------------


def test_empty_document_rolls_up_to_nothing_not_a_crash():
	rollup = rollup_quantity_progress([])
	assert rollup["order_status"] == NOT_ORDERED
	assert rollup["receive_status"] == NOT_RECEIVED
	assert rollup["item_count"] == 0


def test_over_receipt_anywhere_wins_the_document_rollup():
	"""A document that has over-received is not merely "received"."""
	lines = [quantity_progress(4, 4, 4), quantity_progress(4, 4, 9)]
	assert rollup_quantity_progress(lines)["receive_status"] == OVER_RECEIVED


def test_rollup_ignores_statuses_from_another_vocabulary():
	"""ERPNext's own status strings ("To Receive and Bill", "Stopped") reach the feed
	on other doctypes. They must not crash the rollup or be mistaken for ours."""
	rows = [
		{"order_status": "To Receive and Bill", "receive_status": "Stopped"},
		{"order_status": ORDERED, "receive_status": RECEIVED},
	]
	rollup = rollup_quantity_progress(rows)
	assert rollup["order_status"] == ORDERED
	assert rollup["receive_status"] == RECEIVED


def test_a_line_split_across_two_orders_counts_once():
	"""The fan-out guard, with the numbers it was measured against.

	MAT-MR-2026-00001's ten lines arrive from the feed's query as nineteen rows,
	because the Purchase Order join matches ``supplier_quotation_item OR
	material_request_item`` and a line reachable both ways comes back twice. Summing
	the rows straight reports 720 requested against a true 362.
	"""
	line = dict(quantity_progress(4, 4, 0), mr_item="MRI-0001")
	other = dict(quantity_progress(2, 0, 0), mr_item="MRI-0002")

	# Same line twice, as the join emits it.
	rows = [line, line, other]
	assert len(dedupe_lines(rows)) == 2

	rollup = rollup_quantity_progress(dedupe_lines(rows))
	assert rollup["requested_qty"] == 6
	assert rollup["ordered_qty"] == 4
	assert rollup["item_count"] == 2

	# Without the de-duplication the same rows nearly double.
	naive = rollup_quantity_progress(rows)
	assert naive["requested_qty"] == 10


def test_rows_with_no_child_row_of_their_own_each_count_once():
	"""Documents that never joined a chain get rows built by the supplementary sweep,
	which has no child-row name to key on. They must not collapse into one."""
	rows = [quantity_progress(4, 0, 0), quantity_progress(4, 0, 0), quantity_progress(1, 0, 0)]
	assert len(dedupe_lines(rows)) == 3
	assert rollup_quantity_progress(dedupe_lines(rows))["requested_qty"] == 9


def test_direct_purchase_order_lines_dedupe_on_their_own_row():
	rows = [
		dict(quantity_progress(None, 5, 0), po_item="POI-1"),
		dict(quantity_progress(None, 5, 0), po_item="POI-1"),
	]
	assert len(dedupe_lines(rows)) == 1


def test_vocabularies_are_ordered_worst_first():
	"""The tuples are a contract: the rollup unpacks them positionally, and column
	sorting will read them as a progression. Reordering silently changes both."""
	assert ORDER_STATUSES == (NOT_ORDERED, PARTIALLY_ORDERED, ORDERED, OVER_ORDERED)
	assert RECEIVE_STATUSES == (NOT_RECEIVED, PARTIALLY_RECEIVED, RECEIVED, OVER_RECEIVED)
	assert len(set(ORDER_STATUSES) & set(RECEIVE_STATUSES)) == 0


# ---------------------------------------------------------------------------
# Receiving against an order (plan_receipt) — Receive Items on the Purchase Order
# ---------------------------------------------------------------------------


def _order():
	return [
		{"name": "row-a", "item_code": "A", "qty": 6, "received_qty": 0},
		{"name": "row-b", "item_code": "B", "qty": 6, "received_qty": 4},
		{"name": "row-c", "item_code": "C", "qty": 2, "received_qty": 2},
	]


def test_partial_delivery_puts_only_the_lines_that_arrived_on_the_receipt():
	lines, problems = plan_receipt(_order(), {"row-a": 2, "row-b": 0})
	assert problems == []
	assert lines == [{"purchase_order_item": "row-a", "qty": 2.0}]


def test_lines_keep_order_line_order_not_typed_order():
	lines, _ = plan_receipt(_order(), {"row-b": 1, "row-a": 1})
	assert [line["purchase_order_item"] for line in lines] == ["row-a", "row-b"]


def test_more_than_pending_is_refused_in_the_buyers_terms():
	lines, problems = plan_receipt(_order(), {"row-b": 3})
	assert lines == []
	assert problems == ["B: 3 is more than the 2 still to come."]


def test_the_allowance_widens_what_is_accepted_and_is_named_when_it_is_the_limit():
	order = _order()
	order[1]["allowance_pct"] = 50
	lines, problems = plan_receipt(order, {"row-b": 3})
	assert problems == []
	assert lines == [{"purchase_order_item": "row-b", "qty": 3.0}]
	_, problems = plan_receipt(order, {"row-b": 4})
	assert problems == [
		"B: 4 is more than the 2 still to come (up to 3 with the 50% over-receipt allowance)."
	]


def test_exactly_pending_is_accepted_through_float_noise():
	order = [{"name": "r", "item_code": "X", "qty": 0.3, "received_qty": 0.1}]
	lines, problems = plan_receipt(order, {"r": 0.2})
	assert problems == []
	assert lines == [{"purchase_order_item": "r", "qty": 0.2}]


def test_a_fully_received_line_cannot_take_more():
	_, problems = plan_receipt(_order(), {"row-c": 1})
	assert problems == ["C is already fully received."]


def test_negative_and_unknown_rows_are_problems_not_silently_dropped():
	lines, problems = plan_receipt(_order(), {"row-a": -1, "ghost": 1})
	assert lines == []
	assert "Row ghost is not on this order." in problems
	assert "A: a received quantity cannot be negative." in problems


def test_drop_ship_lines_are_never_received_here():
	order = [{"name": "d", "item_code": "D", "qty": 1, "received_qty": 0, "delivered_by_supplier": 1}]
	lines, problems = plan_receipt(order, {"d": 1})
	assert lines == []
	assert problems == ["D ships from the supplier straight to the customer and is never received here."]


def test_all_zero_is_not_a_problem_here_but_yields_no_lines():
	"""The endpoint refuses an empty receipt with its own sentence; the planner must not
	pre-empt it, or a table left at zero would read as two errors."""
	assert plan_receipt(_order(), {"row-a": 0}) == ([], [])


def test_nothing_is_clamped():
	"""A quantity quietly reduced to fit would be a receipt for goods that did not arrive."""
	lines, problems = plan_receipt(_order(), {"row-a": 7})
	assert lines == [] and problems


# ---------------------------------------------------------------------------
# Which documents are open — the tracker's Print → Open
# ---------------------------------------------------------------------------


def test_a_received_order_awaiting_its_bill_is_not_open():
	"""The case the rule was written around. Billing lives in QuickBooks, so 70 fully
	received orders on production sit at "To Bill" with no invoice ever coming here. A
	status-label rule would print every one of them as "open"."""
	assert document_is_open("Purchase Order", 1, "To Bill", 100) is False
	assert document_is_open("Purchase Order", 1, "To Receive and Bill", 0) is True
	assert document_is_open("Purchase Order", 1, "To Receive and Bill", 40) is True


def test_closed_and_delivered_orders_are_not_open_whatever_per_received_says():
	"""Closed is a deliberate "stop chasing this"; Delivered is a drop-ship."""
	for status in ("Closed", "Delivered"):
		assert document_is_open("Purchase Order", 1, status, 0) is False
	assert SETTLED_PO_STATUSES == ("Closed", "Delivered")


def test_an_order_on_hold_with_goods_outstanding_is_open():
	"""Paused is not finished. Same answer as the Project form's "+ Purchase Receipt"
	picker, which lists every order not Closed/Delivered with goods still to come."""
	assert document_is_open("Purchase Order", 1, "On Hold", 40) is True


def test_drafts_and_cancelled_documents_are_never_open():
	"""A printed pack of open orders must not carry an order nobody has placed."""
	for doctype in OPEN_RULES:
		assert document_is_open(doctype, 0, "Draft", 0) is False
		assert document_is_open(doctype, 2, "Cancelled", 0) is False
	# The feed's docstatus comes from frappe as an int, but a missing document reaches this
	# with None, and junk must not raise mid-feed.
	assert document_is_open("Purchase Order", None, "To Receive and Bill", 0) is False
	assert document_is_open("Purchase Order", "junk", "To Receive and Bill", 0) is False
	assert document_is_open("Purchase Order", "1", "To Receive and Bill", None) is True


def test_a_material_request_is_open_until_its_material_arrives():
	"""Ordered is still open: the job is waiting on it. Each request type's own "done"
	status, and a deliberate Stop, are not."""
	for status in ("Pending", "Partially Ordered", "Ordered", "Partially Received"):
		assert document_is_open("Material Request", 1, status) is True, status
	for status in ("Received", "Transferred", "Issued", "Manufactured", "Stopped"):
		assert document_is_open("Material Request", 1, status) is False, status


def test_a_purchase_invoice_is_open_while_money_is_owed():
	for status in ("Unpaid", "Overdue", "Partly Paid"):
		assert document_is_open("Purchase Invoice", 1, status) is True, status
	for status in ("Paid", "Return", "Debit Note Issued", "Submitted", "Internal Transfer"):
		assert document_is_open("Purchase Invoice", 1, status) is False, status


def test_doctypes_without_an_open_answer_none_not_false():
	""""Cannot be open" and "is not open" are different answers: the first hides the Open
	choice altogether, the second counts toward it. A submitted RFQ, a standing quote and a
	receipt waiting on a QuickBooks bill would each read as open under any rule written for
	them, so they get none."""
	for doctype in ("Request for Quotation", "Supplier Quotation", "Purchase Receipt", "Stock Entry"):
		assert doctype not in OPEN_RULES
		assert document_is_open(doctype, 1, "Submitted") is None
		assert document_is_open(doctype, 1, "To Bill") is None
	assert set(OPEN_RULES) == {"Material Request", "Purchase Order", "Purchase Invoice"}
	# The words the dialog shows, one per rule, never empty.
	assert all(isinstance(text, str) and text.strip() for text in OPEN_RULES.values())
