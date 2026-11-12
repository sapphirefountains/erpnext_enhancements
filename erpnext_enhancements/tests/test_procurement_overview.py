"""Bench-free tests for the Procurement Tracker's states, its by-item view and "where it is now"
(v1.806.0).

Plain pytest functions: ``procurement_overview`` and ``inventory_enhancements.project_stock_rules``
import only the stdlib and each other, so this needs no Frappe site and no stub. It must run in a
``python -m pytest`` step (``python -m unittest`` collects nothing from function tests and still
reports success).

Run: python -m pytest erpnext_enhancements/tests/test_procurement_overview.py -q

What is fenced here, in the order a mistake would cost most:

* the where-now never shows more at a place than the place holds, and never lists a place for
  another job's stock;
* a line's state comes from its own quantities, a closed order is not "late", and a draft order
  is not an order;
* the by-item view de-duplicates the feed's fan-out before it sums, and leaves quotes and bills
  out of the quantities;
* a non-stock item says its location is not tracked, in words.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements import procurement_overview as overview
from erpnext_enhancements import procurement_quantities as quantities
from erpnext_enhancements.inventory_enhancements import project_stock_rules as stock

SHELF = "Project Shelf - SF"
SITE = "Job Site PRJ-90001 Water Wall - SF"
OTHER_SITE = "Job Site PRJ-90002 Lobby - SF"
BIN = "Bin A1-2-3 - SF"
BONEYARD = "Boneyard - SF"
NAMES = {SHELF: "Project Shelf", SITE: "Job Site PRJ-90001 Water Wall", BIN: "Bin A1-2-3", OTHER_SITE: "Job Site PRJ-90002 Lobby"}
TODAY = "2026-11-12"


def kind_of(warehouse):
	return stock.warehouse_kind(
		warehouse, shelf=SHELF, boneyard=BONEYARD, own_sites={SITE}, all_sites={SITE, OTHER_SITE}
	)


def move(warehouse, qty):
	return {"warehouse": warehouse, "kind": stock.LEDGER_MOVE, "qty": qty}


def issue(warehouse, qty):
	return {"warehouse": warehouse, "kind": stock.LEDGER_ISSUE, "qty": qty}


def places(ledger, on_hand):
	return stock.item_places(ledger, on_hand, own_sites={SITE}, kind_of=kind_of, names=NAMES)


def by_kind(found):
	return {p["kind"]: p["qty"] for p in found}


# ---------------------------------------------------------------------------
# Ledger rows and kinds of warehouse
# ---------------------------------------------------------------------------


def test_ledger_kind_tells_a_take_from_a_transfer_and_a_return_from_a_receipt():
	assert stock.ledger_kind("Stock Entry", "Material Issue") == stock.LEDGER_ISSUE
	assert stock.ledger_kind("Stock Entry", "Material Transfer") == stock.LEDGER_MOVE
	# Parts put back into stock with the job on the entry: they came back from the job.
	assert stock.ledger_kind("Stock Entry", "Material Receipt") == stock.LEDGER_BROUGHT_BACK
	assert stock.ledger_kind("Delivery Note") == stock.LEDGER_ISSUE
	assert stock.ledger_kind("Purchase Receipt", "", 0, 5) == stock.LEDGER_MOVE
	assert stock.ledger_kind("Purchase Receipt", "", 1, -5) == stock.LEDGER_SUPPLIER_RETURN
	# A stock-updating Purchase Invoice has no is_return joined: the sign says it.
	assert stock.ledger_kind("Purchase Invoice", "", 0, -2) == stock.LEDGER_SUPPLIER_RETURN
	assert stock.ledger_kind("Stock Reconciliation") == stock.LEDGER_MOVE


def test_warehouse_kind():
	assert kind_of(SHELF) == stock.KIND_SHELF
	assert kind_of(SITE) == stock.KIND_SITE
	assert kind_of(OTHER_SITE) == stock.KIND_OTHER_SITE
	assert kind_of(BONEYARD) == stock.KIND_BONEYARD
	assert kind_of(BIN) == stock.KIND_BIN
	# Asked with no job in mind (the Bin Manager's picker), every site is a job site.
	assert stock.warehouse_kind(OTHER_SITE, all_sites={OTHER_SITE}) == stock.KIND_SITE
	assert stock.warehouse_kind("Van 2 - SF", vehicles={"Van 2 - SF"}) == stock.KIND_VEHICLE
	# No shelf set on the site: the old Stores is just a named shop location.
	assert stock.warehouse_kind("Stores - SF") == stock.KIND_BIN


def test_looks_like_vehicle_reads_whole_words():
	assert stock.looks_like_vehicle(["Service Van - SF"])
	assert stock.looks_like_vehicle(["Bin 4", "Trucks"])
	assert not stock.looks_like_vehicle(["Vanity Shelf", "Advance Bay"])
	assert not stock.looks_like_vehicle([])


# ---------------------------------------------------------------------------
# Where one item is for one job
# ---------------------------------------------------------------------------


def test_received_onto_the_shelf_is_on_the_shelf():
	found = places([move(SHELF, 10)], {SHELF: 10})
	assert by_kind(found) == {stock.KIND_SHELF: 10}
	assert found[0]["text"] == "10 on the Project Shelf"
	assert found[0]["label"] == "Project Shelf"


def test_a_line_split_across_places_lists_each_with_its_quantity():
	ledger = [move(SHELF, 10), move(SHELF, -4), move(SITE, 4), issue(SITE, -1), move(SHELF, -2), move(BIN, 2)]
	found = places(ledger, {SHELF: 4, SITE: 3, BIN: 2})
	assert by_kind(found) == {
		stock.KIND_SHELF: 4,
		stock.KIND_SITE: 3,
		stock.KIND_BIN: 2,
		stock.KIND_TAKEN: 1,
	}
	# In the order a person would go looking: shelf, site, bins, then what has left.
	assert [p["kind"] for p in found] == [stock.KIND_SHELF, stock.KIND_SITE, stock.KIND_BIN, stock.KIND_TAKEN]
	assert stock.places_text(found) == (
		"4 on the Project Shelf · 3 at the job site · 2 in Bin A1-2-3 · 1 taken to the job"
	)


def test_never_more_at_a_place_than_the_place_holds():
	"""Ten were received onto the shelf for the job; a shelf-to-bin move with no job on it took
	six away. The ledger still says ten; the shelf holds four."""
	found = places([move(SHELF, 10)], {SHELF: 4})
	assert by_kind(found) == {stock.KIND_SHELF: 4}
	assert by_kind(stock.with_unrecorded(found, 10)) == {stock.KIND_SHELF: 4, stock.KIND_UNRECORDED: 6}


def test_everything_at_the_jobs_own_site_is_the_jobs():
	"""Sent to the site from shop stock, with no purchase for the job behind it: the ledger sum
	under the job is what was transferred, and the site's own Bin row is what is there."""
	found = places([], {SITE: 7})
	assert by_kind(found) == {stock.KIND_SITE: 7}


def test_a_take_of_shop_stock_for_the_job_is_a_take_and_not_a_place():
	found = places([issue(BIN, -2)], {BIN: 30})
	assert by_kind(found) == {stock.KIND_TAKEN: 2}


def test_a_purchase_return_is_returned_to_the_supplier():
	ledger = [move(SHELF, 5), {"warehouse": SHELF, "kind": stock.LEDGER_SUPPLIER_RETURN, "qty": -2}]
	found = places(ledger, {SHELF: 3})
	assert by_kind(found) == {stock.KIND_SHELF: 3, stock.KIND_RETURNED: 2}
	assert found[-1]["text"] == "2 returned to the supplier"


def test_parts_brought_back_from_the_job_are_at_the_bin_they_came_back_to():
	ledger = [issue(BIN, -5), move(BIN, 2)]
	found = places(ledger, {BIN: 40})
	# 5 went out and 2 came back: the bin's sum for the job is still negative, so it is not a
	# place; the take stands at what the take entries say.
	assert by_kind(found) == {stock.KIND_TAKEN: 5}


def test_parts_brought_back_by_a_material_receipt_are_counted_once_where_they_are():
	"""The review's case: ten received onto the shelf, three taken for the job, the three put back
	by a Material Receipt. It read "10 on the Project Shelf" AND "3 taken to the job": thirteen
	of ten."""
	back = lambda warehouse, qty: {"warehouse": warehouse, "kind": stock.LEDGER_BROUGHT_BACK, "qty": qty}  # noqa: E731
	ledger = [move(SHELF, 10), issue(SHELF, -3), back(SHELF, 3)]
	assert by_kind(places(ledger, {SHELF: 10})) == {stock.KIND_SHELF: 10}
	# Two of the three came back: one is still taken, and nine are on the shelf.
	ledger = [move(SHELF, 10), issue(SHELF, -3), back(SHELF, 2)]
	assert by_kind(places(ledger, {SHELF: 9})) == {stock.KIND_SHELF: 9, stock.KIND_TAKEN: 1}
	# Brought back to a different bin than they were taken from: there, and not taken.
	ledger = [issue(BIN, -3), back(SHELF, 3)]
	assert by_kind(places(ledger, {SHELF: 3, BIN: 20})) == {stock.KIND_SHELF: 3}
	# More put back than this job ever took: nothing is "taken", and never a negative.
	ledger = [issue(BIN, -1), back(SHELF, 4)]
	assert by_kind(places(ledger, {SHELF: 4})) == {stock.KIND_SHELF: 4}


def test_a_place_whose_share_is_already_worked_out_is_used_as_it_stands():
	"""The Project Shelf's own function shares a short shelf out between the jobs that claim it.
	The tracker shows that number, the one the Bin Manager shows, not its own ledger sum."""
	ledger = [move(SHELF, 6), move(BIN, 2)]
	on_hand = {SHELF: 8, BIN: 2}
	assert by_kind(places(ledger, on_hand)) == {stock.KIND_SHELF: 6, stock.KIND_BIN: 2}
	shared = stock.item_places(ledger, on_hand, {SITE}, kind_of, NAMES, held={SHELF: 4})
	assert by_kind(shared) == {stock.KIND_SHELF: 4, stock.KIND_BIN: 2}
	# It says none of the shelf is this job's: no shelf place, whatever the ledger sums to.
	none = stock.item_places(ledger, on_hand, {SITE}, kind_of, NAMES, held={SHELF: 0})
	assert by_kind(none) == {stock.KIND_BIN: 2}
	# And a share with no ledger row of this job's behind it still lists.
	only = stock.item_places([], on_hand, {SITE}, kind_of, NAMES, held={SHELF: 3})
	assert by_kind(only) == {stock.KIND_SHELF: 3}


def test_stock_at_another_jobs_site_is_named_and_kept_apart():
	found = places([move(OTHER_SITE, 3)], {OTHER_SITE: 3})
	assert found[0]["kind"] == stock.KIND_OTHER_SITE
	assert found[0]["label"] == "Job Site PRJ-90002 Lobby"


def test_with_unrecorded_adds_nothing_when_all_is_accounted_for_or_unknown():
	found = places([move(SHELF, 10)], {SHELF: 10})
	assert stock.with_unrecorded(found, 10) == found
	assert stock.with_unrecorded(found, None) == found
	# More accounted for than was bought (shop stock sent to the job): no negative remainder.
	assert stock.with_unrecorded(found, 4) == found


def test_a_supplier_return_and_the_ledgers_return_rows_are_never_added_together():
	ledger = [move(SHELF, 10), {"warehouse": SHELF, "kind": stock.LEDGER_SUPPLIER_RETURN, "qty": -2}]
	kinds = lambda found: by_kind(found)  # noqa: E731
	on_hand = {SHELF: 8}
	assert kinds(stock.item_places(ledger, on_hand, {SITE}, kind_of, NAMES))[stock.KIND_RETURNED] == 2
	# The Supplier Return says the same 2 left: still 2, not 4.
	assert kinds(stock.item_places(ledger, on_hand, {SITE}, kind_of, NAMES, returned=2))[stock.KIND_RETURNED] == 2
	# It knows of more than the ledger's rows carry the job for: the larger figure.
	assert kinds(stock.item_places(ledger, on_hand, {SITE}, kind_of, NAMES, returned=5))[stock.KIND_RETURNED] == 5
	# Nothing in the ledger at all (the return's rows carried no job).
	assert kinds(stock.item_places([move(SHELF, 10)], on_hand, {SITE}, kind_of, NAMES, returned=3))[stock.KIND_RETURNED] == 3
	assert stock.KIND_RETURNED not in kinds(stock.item_places([move(SHELF, 10)], on_hand, {SITE}, kind_of, NAMES, returned=None))


def test_return_summary_adds_up_what_has_left_and_names_each_return_once():
	lines = [
		{"supplier_return": "SRET-1", "status": "Sent Back", "item_code": "A", "qty": 1, "stock_qty": 12, "gone": True, "open": True},
		{"supplier_return": "SRET-1", "status": "Sent Back", "item_code": "A", "qty": 1, "stock_qty": 3, "gone": True, "open": True},
		{"supplier_return": "SRET-2", "status": "Requested", "item_code": "A", "qty": 2, "stock_qty": 2, "gone": False, "open": True},
		{"supplier_return": "SRET-2", "status": "Requested", "item_code": "B", "qty": 5, "stock_qty": None, "gone": False, "open": True},
		{"supplier_return": None, "item_code": "A", "stock_qty": 99, "gone": True},
		{"supplier_return": "SRET-3", "item_code": None, "stock_qty": 99, "gone": True},
	]
	out = stock.return_summary(lines)
	assert out["A"]["gone"] == 15  # the stock unit, not the purchase unit
	assert out["A"]["returns"] == [
		{"supplier_return": "SRET-1", "status": "Sent Back", "qty": 15, "gone": True, "open": True},
		{"supplier_return": "SRET-2", "status": "Requested", "qty": 2, "gone": False, "open": True},
	]
	# No stock quantity on the line: the purchase quantity is all there is.
	assert out["B"] == {"gone": 0, "returns": [{"supplier_return": "SRET-2", "status": "Requested", "qty": 5, "gone": False, "open": True}]}
	assert stock.return_summary(None) == {}


def test_capped_shelf_rows_cuts_a_job_to_what_the_shelf_holds():
	rows = [
		{"project": "P1", "item_code": "A", "item_name": "Elbow", "stock_uom": "Unit", "qty": 6},
		{"project": "P1", "item_code": "B", "qty": 2},
		{"project": "P1", "item_code": "C", "qty": -1},  # more left under the job than came in
		{"project": "P2", "item_code": "B", "qty": 4},
		{"project": "P2", "item_code": "D", "qty": 3},
	]
	untagged = [{"item_code": "B", "on_hand": 1, "tagged": 6}, {"item_code": "D", "on_hand": -2, "tagged": 3}]
	out = stock.capped_shelf_rows(rows, untagged)
	assert [(r["project"], r["item_code"], r["qty"], r["recorded"]) for r in out] == [
		("P1", "A", 6, 6),  # the item's tagged total and its on-hand agree: as recorded
		("P1", "B", 1, 2),  # the shelf holds 1: nobody can be handed more
		("P2", "B", 1, 4),
	]
	assert out[1]["item_name"] == "B"  # no name sent: the code stands in
	assert stock.capped_shelf_rows(None, None) == []
	# The Project Shelf module's later shape: qty already cut, the ledger balance beside it as tagged.
	later = stock.capped_shelf_rows(
		[{"project": "P1", "item_code": "B", "qty": 1, "tagged": 2}, {"project": "P1", "item_code": "C", "qty": 0, "tagged": -1}],
		[{"item_code": "B", "on_hand": 1, "tagged": 2}],
	)
	assert [(r["item_code"], r["qty"], r["recorded"]) for r in later] == [("B", 1, 2)]


def test_shelf_rows_is_the_same_rule_per_item():
	ledger = [
		{"item_code": "A", "warehouse": SHELF, "qty": 10},
		{"item_code": "A", "warehouse": SHELF, "qty": -4},
		{"item_code": "B", "warehouse": SHELF, "qty": 5},
		{"item_code": "C", "warehouse": BIN, "qty": 9},
		{"item_code": "D", "warehouse": SHELF, "qty": 2},
	]
	assert stock.shelf_rows(ledger, {"A": 20, "B": 1}, SHELF) == {"A": 6, "B": 1}
	assert stock.shelf_rows(ledger, {"A": 20}, None) == {}


# ---------------------------------------------------------------------------
# What a line is waiting on
# ---------------------------------------------------------------------------


def line(**values):
	row = {"source_doc_type": "Purchase Order", "po": "PO-1", "po_item": "poi-1", "po_status": "To Receive and Bill"}
	row.update(values)
	requested = row.pop("requested", None)
	row.update(
		quantities.quantity_progress(
			requested, row.pop("ordered", 0), row.pop("received", 0), row.pop("draft", 0)
		)
	)
	return row


def test_states_come_from_the_lines_own_quantities():
	assert overview.line_state(line(ordered=4, received=0))["state"] == overview.ORDERED
	assert overview.line_state(line(ordered=4, received=1))["state"] == overview.PARTLY_RECEIVED
	assert overview.line_state(line(ordered=4, received=4))["state"] == overview.RECEIVED
	assert overview.line_state(line(ordered=4, received=5))["state"] == overview.RECEIVED
	to_order = overview.line_state(line(mr="MR-1", mr_item="mri-1", po=None, po_item=None, requested=4))
	assert to_order["states"] == [overview.TO_ORDER]
	assert to_order["to_order_qty"] == 4
	assert to_order["needs_action"] is True


def test_a_half_ordered_half_arrived_line_is_both():
	state = overview.line_state(line(mr="MR-1", mr_item="mri-1", requested=10, ordered=6, received=2))
	assert state["states"] == [overview.TO_ORDER, overview.PARTLY_RECEIVED]
	assert state["state"] == overview.TO_ORDER
	assert state["waiting_qty"] == 4
	assert state["to_order_qty"] == 4


def test_a_draft_order_is_not_an_order():
	state = overview.line_state(line(ordered=0, draft=6))
	assert state["states"] == [overview.TO_ORDER]
	assert state["to_order_qty"] == 6


def test_a_draft_order_that_never_joined_a_chain_is_not_ordered_either():
	"""What the feed's ``_minimal_item_row`` now emits for a draft Purchase Order found only by
	the sweep: its quantity as draft, nothing ordered. (That function is held to it in
	``test_project_stock``; this is what the tracker then makes of the row.)"""
	draft = {"source_doc_type": "Purchase Order", "po": "PO-DRAFT", "po_status": "Draft", "item_code": "A"}
	draft.update(quantities.quantity_progress(None, 0, 0, 5))
	assert overview.line_state(draft, TODAY)["states"] == [overview.TO_ORDER]
	item = overview.by_item([draft], TODAY)[0]
	assert (item["ordered_qty"], item["draft_ordered_qty"], item["states"]) == (0, 5, [overview.TO_ORDER])


def test_late_needs_a_date_in_the_past_and_something_still_to_come():
	waiting = line(ordered=4, received=1, expected_date="2026-11-01")
	assert overview.line_state(waiting, TODAY)["states"] == [overview.LATE, overview.PARTLY_RECEIVED]
	assert overview.line_state(waiting, TODAY)["is_late"] is True
	# Due today is not late.
	assert overview.line_state(line(ordered=4, expected_date=TODAY), TODAY)["is_late"] is False
	# Received in full: its date no longer matters.
	assert overview.line_state(line(ordered=4, received=4, expected_date="2026-01-01"), TODAY)["is_late"] is False
	# Nothing knows today's date: nothing is late.
	assert overview.line_state(waiting)["is_late"] is False


def test_a_closed_order_is_closed_and_never_late():
	"""148 order lines on production sat on orders closed with nothing received."""
	closed = line(ordered=4, received=0, po_status="Closed", expected_date="2026-01-01")
	state = overview.line_state(closed, TODAY)
	assert state["states"] == [overview.CLOSED]
	assert state["needs_action"] is False
	assert state["is_late"] is False
	# What did arrive on it still says so.
	part = overview.line_state(line(ordered=4, received=1, po_status="Closed"), TODAY)
	assert part["states"] == [overview.CLOSED]
	# Fully received and closed is just received.
	done = overview.line_state(line(ordered=4, received=4, po_status="Closed"), TODAY)
	assert done["states"] == [overview.RECEIVED]


def test_a_request_line_is_closed_only_when_its_closed_order_covers_the_whole_shortfall():
	row = line(
		mr="MR-1", mr_item="mri-1", requested=10, ordered=10, received=4,
		po_status="Closed", po_line_qty=4, po_line_received_qty=4,
	)
	# This order delivered all of its own 4; the 6 missing sit on another order.
	assert overview.line_state(row, TODAY)["states"] == [overview.PARTLY_RECEIVED]
	row.update({"po_line_qty": 6, "po_line_received_qty": 0})
	assert overview.line_state(row, TODAY)["states"] == [overview.CLOSED]


def test_an_order_that_delivered_its_share_is_not_late_because_another_is():
	"""A request line split across two orders: each row carries the request's totals and its
	own order's date."""
	shared = dict(mr="MR-1", mr_item="mri-1", requested=10, ordered=10, received=4, expected_date="2026-11-01")
	delivered = line(**dict(shared, po_item="poi-1", po_line_qty=4, po_line_received_qty=4))
	short = line(**dict(shared, po_item="poi-2", po_line_qty=6, po_line_received_qty=0))
	assert overview.line_state(delivered, TODAY)["states"] == [overview.PARTLY_RECEIVED]
	assert overview.line_state(short, TODAY)["states"] == [overview.LATE, overview.PARTLY_RECEIVED]


def test_a_stopped_request_asks_for_nothing_more():
	stopped = line(mr="MR-1", mr_item="mri-1", po=None, po_item=None, mr_status="Stopped", requested=5)
	state = overview.line_state(stopped, TODAY)
	assert state["states"] == [overview.CLOSED]
	assert state["needs_action"] is False


def test_quotes_and_bills_are_not_asks_or_orders():
	quote = {"source_doc_type": "Supplier Quotation", "sq": "SQ-1"}
	quote.update(quantities.quantity_progress(5, 0, 0))
	assert overview.line_state(quote, TODAY)["states"] == [overview.QUOTE_ONLY]
	assert overview.line_state(quote, TODAY)["needs_action"] is False
	bill = {"source_doc_type": "Purchase Invoice", "pi": "PI-1"}
	bill.update(quantities.quantity_progress(None, 5, 0))
	assert overview.line_state(bill, TODAY)["states"] == [overview.INVOICE_ONLY]
	# A receipt with no order behind it (a store run) is received.
	receipt = {"source_doc_type": "Purchase Receipt", "pr": "PR-1"}
	receipt.update(quantities.quantity_progress(None, 2, 2))
	assert overview.line_state(receipt, TODAY)["states"] == [overview.RECEIVED]


def test_state_vocabularies_agree_with_each_other():
	assert set(overview.STATE_RANK) == set(overview.STATE_LABELS)
	assert set(overview.FILTER_STATES) <= set(overview.STATE_RANK)
	assert set(overview.LOCATION_FILTERS) == set(overview.LOCATION_LABELS)


# ---------------------------------------------------------------------------
# The tracker by item
# ---------------------------------------------------------------------------


def feed_rows():
	"""One request line split across two orders (the feed's fan-out: the same request line
	twice), a direct order line of another item, and a quote and a bill of the first item."""
	shared = dict(
		source_doc_type="Material Request", item_code="417-080", item_name="FITTING, ELBOW", stock_uom="Unit",
		mr="MR-1", mr_item="mri-1", requested=10, ordered=10, received=4, supplier=None,
	)
	first = line(**dict(shared, po="PO-1", po_item="poi-1", supplier="Acme Supply", po_line_qty=4, po_line_received_qty=4, row_id="mri-1#0", pr="PR-1", warehouse=SHELF))
	second = line(**dict(shared, po="PO-2", po_item="poi-2", supplier="Bolt Co", po_line_qty=6, po_line_received_qty=0, row_id="mri-1#1", expected_date="2026-11-01"))
	direct = line(item_code="2622-010", item_name="PUMP", stock_uom="Unit", po="PO-3", po_item="poi-3", ordered=2, received=2, supplier="Acme Supply", row_id="poi-3#2", conversion_factor=1)
	quote = {"source_doc_type": "Supplier Quotation", "item_code": "417-080", "item_name": "FITTING, ELBOW", "sq": "SQ-9", "row_id": "Supplier Quotation#x", "supplier": "Cheap Co"}
	quote.update(quantities.quantity_progress(10, 0, 0))
	bill = {"source_doc_type": "Purchase Invoice", "item_code": "417-080", "pi": "PI-9", "row_id": "Purchase Invoice#y"}
	bill.update(quantities.quantity_progress(None, 10, 0))
	return [first, second, direct, quote, bill]


def test_by_item_dedupes_the_fan_out_and_leaves_quotes_and_bills_out_of_the_quantities():
	items = {i["item_code"]: i for i in overview.by_item(feed_rows(), TODAY)}
	fitting = items["417-080"]
	# Not 20 / 20 / 8 (the fan-out summed) and not 30 requested (the quote added in).
	assert (fitting["requested_qty"], fitting["ordered_qty"], fitting["received_qty"]) == (10, 10, 4)
	assert fitting["line_count"] == 1
	# Late because the order that is still short is late, whichever copy of the split line the
	# de-duplication kept; and due on that order's date.
	assert fitting["states"] == [overview.LATE, overview.PARTLY_RECEIVED]
	assert fitting["needs_action"] is True
	assert fitting["expected_date"] == "2026-11-01"
	# The paper trail still has every document, the quote and the bill included.
	assert fitting["documents"] == {
		"Material Request": ["MR-1"],
		"Supplier Quotation": ["SQ-9"],
		"Purchase Order": ["PO-1", "PO-2"],
		"Purchase Receipt": ["PR-1"],
		"Purchase Invoice": ["PI-9"],
	}
	assert fitting["suppliers"] == ["Acme Supply", "Bolt Co", "Cheap Co"]
	assert fitting["received_at"] == [SHELF]
	pump = items["2622-010"]
	assert pump["requested_qty"] is None  # nobody asked: a direct order
	assert (pump["ordered_qty"], pump["received_qty"]) == (2, 2)
	assert pump["states"] == [overview.RECEIVED]
	assert pump["needs_action"] is False


def test_by_item_is_in_item_code_order_numbers_as_numbers():
	rows = [
		line(item_code=code, po_item=f"poi-{code}", ordered=1)
		for code in ("417-100", "2622-010", "417-080")
	]
	assert [i["item_code"] for i in overview.by_item(rows)] == ["417-080", "417-100", "2622-010"]


def test_a_direct_order_line_is_converted_to_the_stock_unit():
	row = line(item_code="PIPE", po_item="poi-9", ordered=3, received=1, conversion_factor=20)
	item = overview.by_item([row])[0]
	assert (item["ordered_qty"], item["received_qty"]) == (60, 20)
	# A request line already is in the stock unit, whatever factor the row carries.
	request = line(item_code="PIPE", mr="MR-2", mr_item="mri-2", requested=60, ordered=60, received=20, conversion_factor=20)
	assert overview.by_item([request])[0]["ordered_qty"] == 60


def test_an_item_only_quoted_still_lists_as_what_it_is():
	quote = {"source_doc_type": "Supplier Quotation", "item_code": "NEW-1", "sq": "SQ-1"}
	quote.update(quantities.quantity_progress(5, 0, 0))
	item = overview.by_item([quote], TODAY)[0]
	assert item["states"] == [overview.QUOTE_ONLY]
	assert (item["requested_qty"], item["ordered_qty"], item["line_count"]) == (None, 0, 0)
	assert item["needs_action"] is False


# ---------------------------------------------------------------------------
# Where it is now, attached
# ---------------------------------------------------------------------------


def found(code_places):
	return {
		code: {"is_stock_item": stock_item, "places": item_places, "stock_uom": "Unit"}
		for code, (stock_item, item_places) in code_places.items()
	}


def test_attach_puts_state_and_where_now_on_every_line_and_item():
	rows = feed_rows()
	on_shelf = places([move(SHELF, 4)], {SHELF: 4})
	items = overview.attach(rows, found({"417-080": (True, on_shelf), "2622-010": (False, [])}), TODAY)
	fitting = next(i for i in items if i["item_code"] == "417-080")
	assert fitting["where_now_text"] == "4 on the Project Shelf"
	assert fitting["location_tracked"] is True
	assert fitting["location_kinds"] == [stock.KIND_SHELF, overview.NOT_RECEIVED]
	first, second, direct, quote, bill = rows
	assert first["where_now_text"] == "4 on the Project Shelf"
	assert first["received_at"] == SHELF  # where it was received is still said
	assert first["line_state"] == overview.PARTLY_RECEIVED
	assert second["is_late"] is True and second["line_state"] == overview.LATE
	# A non-stock item says so, in words, and is filterable as such.
	assert direct["where_now_text"] == overview.NOT_TRACKED_TEXT
	assert direct["location_tracked"] is False
	assert direct["location_kinds"] == [overview.NOT_TRACKED]
	assert direct["where_now"] == []
	# The quote and the bill carry the item's where-now and their own state.
	assert quote["line_state"] == overview.QUOTE_ONLY
	assert bill["line_state"] == overview.INVOICE_ONLY
	assert quote["needs_action"] is False


def test_attach_lists_the_jobs_supplier_returns_under_the_places():
	sent = {"supplier_return": "SRET-1", "status": "Sent Back", "qty": 2, "gone": True, "open": True}
	asked = {"supplier_return": "SRET-2", "status": "Requested", "qty": 1, "gone": False, "open": True}
	stocked = line(item_code="A", po_item="poi-a", ordered=10, received=10)
	service = line(item_code="LABOR", po_item="poi-l", ordered=1, received=1)
	items_found = found({"A": (True, places([move(SHELF, 8)], {SHELF: 8})), "LABOR": (False, [])})
	items_found["A"]["supplier_returns"] = [sent, asked]
	items_found["LABOR"]["supplier_returns"] = [sent]
	items = overview.attach([stocked, service], items_found, TODAY)
	assert stocked["supplier_returns"] == [sent, asked]
	assert items[0]["supplier_returns"] == [sent, asked]
	# A non-stock item's place is not tracked, but that it went back is on record and filterable.
	assert service["where_now_text"] == overview.NOT_TRACKED_TEXT
	assert service["location_kinds"] == [overview.NOT_TRACKED, stock.KIND_RETURNED]
	# One only being arranged is not "returned" yet.
	items_found["LABOR"]["supplier_returns"] = [asked]
	overview.attach([service], items_found, TODAY)
	assert service["location_kinds"] == [overview.NOT_TRACKED]


def test_attach_names_what_was_received_and_is_nowhere():
	row = line(item_code="A", po_item="poi-a", ordered=10, received=10)
	items = overview.attach([row], found({"A": (True, places([move(SHELF, 10)], {SHELF: 4}))}), TODAY)
	assert items[0]["where_now_text"] == "4 on the Project Shelf · 6 with no place recorded"
	assert stock.KIND_UNRECORDED in [p["kind"] for p in row["where_now"]]


def test_attach_says_not_received_yet_for_a_stock_item_with_nothing_here():
	row = line(item_code="A", po_item="poi-a", ordered=10, received=0)
	overview.attach([row], found({"A": (True, [])}), TODAY)
	assert row["where_now_text"] == overview.NOT_RECEIVED_TEXT
	assert row["location_kinds"] == [overview.NOT_RECEIVED]
	# On a closed order nothing is coming, so "yet" would be a promise.
	closed = line(item_code="B", po_item="poi-b", ordered=10, received=0, po_status="Closed")
	overview.attach([closed], found({"B": (True, [])}), TODAY)
	assert closed["where_now_text"] == overview.NEVER_RECEIVED_TEXT
	assert closed["location_kinds"] == []


def test_attach_survives_a_failed_lookup():
	"""The where-now is a second opinion. Without it every line still has its state, and the
	where-now says nothing rather than "nowhere"."""
	row = line(item_code="A", po_item="poi-a", ordered=10, received=10)
	items = overview.attach([row], None, TODAY)
	assert row["line_state"] == overview.RECEIVED
	assert row["location_tracked"] is None
	assert row["where_now"] == [] and row["where_now_text"] == ""
	assert row["supplier_returns"] == []
	assert items[0]["where_now_text"] == ""


def test_attach_touches_a_shared_row_once_and_counts_the_lines_an_answer_covers():
	a = line(item_code="A", po_item="poi-1", ordered=2, received=2)
	b = line(item_code="A", po_item="poi-2", ordered=3, received=3)
	overview.attach([a, a, b], found({"A": (True, places([move(SHELF, 5)], {SHELF: 5}))}), TODAY)
	assert a["where_now_lines"] == 2 and b["where_now_lines"] == 2
	assert a["where_now"] is b["where_now"]


def test_filter_options_offer_only_what_matches_something():
	rows = feed_rows()
	items = overview.attach(rows, found({"417-080": (True, places([move(SHELF, 4)], {SHELF: 4})), "2622-010": (False, [])}), TODAY)
	options = overview.filter_options(rows, items)
	assert options["suppliers"] == ["Acme Supply", "Bolt Co", "Cheap Co"]
	assert [s["value"] for s in options["states"]] == [overview.PARTLY_RECEIVED, overview.RECEIVED, overview.LATE]
	assert [s["label"] for s in options["states"]] == ["Partly received", "Received", "Late"]
	assert [k["value"] for k in options["locations"]] == [stock.KIND_SHELF, overview.NOT_RECEIVED, overview.NOT_TRACKED]
	assert options["locations"][0]["label"] == "Project Shelf"
