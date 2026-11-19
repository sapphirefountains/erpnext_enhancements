"""Bench-free tests for the Reorder Board (v1.730.0, ER-2026-1323810).

What is held, in the order the brief asked for it:

* **ERPNext's rule.** ``reorder_rules.is_below`` / ``suggested_qty`` against a transcription of the
  closure in ERPNext v16.50.0's (production's) ``erpnext/stock/reorder_item.py`` (``add_to_material_request``) over a grid
  of levels, quantities and projected quantities, level 0 included; the projected qty read at the
  rule's ``warehouse_group`` when it has one, and a missing Bin as 0.
* **Coverage.** An item on an open Material Request (a Draft included) or Purchase Order leaves
  Needs reorder and shows on On order at the right stage, derived from the MR <- PO <- PR links; a
  received line stays a week, then drops off.
* **Material Requests.** ``create_requests`` makes Drafts (never submits), inserted as the caller,
  refuses stale lines (no longer below, already requested, bad qty) writing nothing, and is
  idempotent on ``client_ref``; it needs the role and Material Request create.
* **Snoozes.** Each kind starts and ends (1 week, 1 month with the day clamped, until stock drops
  further, where equal is not further).
* **The digest.** It names only newly-below, non-snoozed lines, names an ended snooze again, sends
  nothing when there is nothing new, and stamps what it named.
* **Recipients.** The role comes from Inventory Scanner Settings (blank = Purchase Manager), and
  Administrator, Guest, Triton, disabled and website users are left out.
* **The patches.** auto_indent goes 1 -> 0 and nothing else; off, or a never-saved Stock Settings,
  writes nothing; neither patch raises.
* **The page.** Its logic under node (routes, quantities, the picked lines), its roles, and that every
  tab is a ``frappe.set_route`` (a history entry) while dialogs push nothing.
* **Suggested reorder levels (v1.831.0).** ``reorder_suggest_rules``: what counts as used (takes,
  deliveries, moves out to a job site or the Project Shelf, less returns from a job; never a count
  correction, a move between shop places or a release from the shelf), the scope, the basis (the
  window, the first recorded take, the first day in the shop), the thin-data gate, the arithmetic and
  its rounding, and the numbers a person may approve. ``reorder_suggestions``: the tab's read over the
  board's rules and the ledger, and Apply: the role gate, the Item's own write permission, a stale page,
  a rule that is gone, an ordinary ``Item.save()`` as the person, nothing saved when nothing changed.

Each part installs its own ``frappe`` stub. Run:
python -m unittest erpnext_enhancements.tests.test_reorder_board -v
"""

import contextlib
import datetime as dt
import importlib
import json
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
PAGE_DIR = APP / "inventory_enhancements" / "page" / "reorder_board"

from erpnext_enhancements.inventory_enhancements import reorder_rules as rules

NOW = dt.datetime(2026, 10, 22, 7, 20)
TODAY = NOW.date()


def erpnext_reference(reorder_level, reorder_qty, projected_qty):
	"""ERPNext v16.50.0's ``_reorder_item.add_to_material_request`` (production's version, read from
	the v16.50.0 tag), transcribed: ``(fires, qty)``."""
	reorder_level = float(reorder_level or 0)
	reorder_qty = float(reorder_qty or 0)
	projected_qty = float(projected_qty or 0)
	if (reorder_level or reorder_qty) and projected_qty <= reorder_level:
		deficiency = reorder_level - projected_qty
		if deficiency > reorder_qty:
			reorder_qty = deficiency
		return True, reorder_qty
	return False, 0.0


def rule(item="ITEM-A", warehouse="Room - X", group="All - X", level=5, qty=10, **extra):
	row = {
		"item_code": item,
		"warehouse": warehouse,
		"warehouse_group": group,
		"company": "Example Co",
		"reorder_level": level,
		"reorder_qty": qty,
		"item_name": f"{item} name",
		"stock_uom": "Nos",
		"purchase_uom": "Nos",
		"conversion_factor": 1.0,
		"stock_uom_whole": True,
		"purchase_uom_whole": True,
		"lead_time_days": 3,
	}
	row.update(extra)
	return row


# --- the pure rules ------------------------------------------------------------------


class TestErpnextRule(unittest.TestCase):
	def test_matches_erpnext_on_a_grid(self):
		for level in (0, 1, 5, 12.5):
			for qty in (0, 1, 10, 3.5):
				for projected in (-4, 0, 0.5, 1, 4.999, 5, 6, 12.5, 20):
					with self.subTest(level=level, qty=qty, projected=projected):
						fires, want = erpnext_reference(level, qty, projected)
						self.assertEqual(rules.is_below(level, qty, projected), fires)
						self.assertAlmostEqual(rules.suggested_qty(level, qty, projected), want)

	def test_level_zero_is_a_real_rule(self):
		# Parker: "reorder when out of stock". Level 0, qty 4: fires at 0 and below, not at 1.
		self.assertTrue(rules.is_below(0, 4, 0))
		self.assertEqual(rules.suggested_qty(0, 4, 0), 4)
		self.assertTrue(rules.is_below(0, 4, -2))
		self.assertEqual(rules.suggested_qty(0, 4, -2), 4)
		self.assertFalse(rules.is_below(0, 4, 1))
		# Both 0: no rule at all.
		self.assertFalse(rules.is_below(0, 0, 0))

	def test_top_up_for_a_deep_shortfall(self):
		# Level 10, qty 3, projected -2: the deficiency, 12, is more than the rule's 3, so 12 (16.50.0).
		self.assertEqual(rules.suggested_qty(10, 3, -2), 12)
		# A shortfall smaller than the rule's quantity buys the rule's quantity.
		self.assertEqual(rules.suggested_qty(5, 10, 1), 10)

	def test_the_tag_not_the_branch(self):
		"""Level 10, qty 3, projected 5: v16.50.0 (production) asks for 5, the deficiency; the
		version-16 branch would ask for 3 + 5 = 8. Fails if the branch's formula comes back."""
		self.assertEqual(rules.suggested_qty(10, 3, 5), 5)
		self.assertEqual(erpnext_reference(10, 3, 5), (True, 5))

	def test_projected_at_the_group_and_a_missing_bin(self):
		projected = {"ITEM-A": {"Room - X": 2.0, "All - X": 9.0}}
		self.assertEqual(rules.projected_for_rule(projected, "ITEM-A", "Room - X", "All - X"), 9.0)
		self.assertEqual(rules.projected_for_rule(projected, "ITEM-A", "Room - X", None), 2.0)
		# No Bin for the item, or none at that warehouse: 0, as _reorder_item's flt(None).
		self.assertEqual(rules.projected_for_rule(projected, "ITEM-B", "Room - X", "All - X"), 0.0)
		self.assertEqual(rules.projected_for_rule(projected, "ITEM-A", "Bin 1 - X", None), 0.0)

	def test_a_group_rule_judged_on_the_group(self):
		# 2 here, 9 across the group, level 5: ERPNext reads the group, so it is NOT below.
		projected = {"ITEM-A": {"Room - X": 2.0, "All - X": 9.0}}
		board = rules.assemble([rule(level=5)], projected, [], {}, NOW)
		self.assertFalse(board["lines"][0]["below"])
		self.assertEqual(board["needs"], [])

	def test_order_qty_and_clean_qty(self):
		self.assertEqual(rules.order_qty(10, 4, True), 3)
		self.assertAlmostEqual(rules.order_qty(10, 4, False), 2.5)
		self.assertEqual(rules.clean_qty("12", True), (12.0, None))
		self.assertIsNotNone(rules.clean_qty("0", False)[1])
		self.assertIsNotNone(rules.clean_qty("-1", False)[1])
		self.assertIsNotNone(rules.clean_qty("abc", False)[1])
		self.assertIsNotNone(rules.clean_qty(rules.MAX_QTY + 1, False)[1])
		self.assertIsNotNone(rules.clean_qty("2.5", True)[1])
		self.assertEqual(rules.clean_qty("2.5", False), (2.5, None))

	def test_schedule_date_is_the_lead_time(self):
		self.assertEqual(rules.schedule_date(TODAY, 3), TODAY + dt.timedelta(days=3))
		self.assertEqual(rules.schedule_date(TODAY, None), TODAY)


def mr_line(
	name="MRI-1", parent="MR-1", docstatus=1, qty=10, item="ITEM-A", warehouse="Room - X", created=None
):
	return {
		"name": name,
		"parent": parent,
		"item_code": item,
		"warehouse": warehouse,
		"stock_qty": qty,
		"schedule_date": TODAY,
		"docstatus": docstatus,
		"transaction_date": created or TODAY,
		"creation": dt.datetime.combine(created or TODAY, dt.time(9, 0)),
	}


def po_line(
	name="POI-1",
	parent="PO-1",
	docstatus=1,
	qty=10,
	mri="MRI-1",
	stage=None,
	item="ITEM-A",
	status="To Receive and Bill",
):
	return {
		"name": name,
		"parent": parent,
		"item_code": item,
		"warehouse": "Room - X",
		"stock_qty": qty,
		"schedule_date": TODAY + dt.timedelta(days=5),
		"material_request_item": mri,
		"docstatus": docstatus,
		"status": status if docstatus == 1 else "Draft",
		"supplier": "SUP-1",
		"supplier_name": "Example Supplier",
		"order_stage": stage,
	}


def pr_line(poi="POI-1", qty=10, docstatus=1, posted=TODAY, parent="PR-1"):
	return {
		"parent": parent,
		"purchase_order_item": poi,
		"stock_qty": qty,
		"docstatus": docstatus,
		"posting_date": posted,
	}


class TestTracking(unittest.TestCase):
	def stage(self, requests, orders=(), receipts=(), today=TODAY):
		lines = rules.track_lines(list(requests), list(orders), list(receipts), today)
		return [l["stage"] for l in lines], lines

	def test_each_stage_from_the_links(self):
		cases = [
			([mr_line(docstatus=0)], [], [], rules.STAGE_DRAFT_REQUEST),
			([mr_line()], [], [], rules.STAGE_REQUESTED),
			([mr_line()], [po_line(docstatus=0)], [], rules.STAGE_PO_DRAFT),
			([mr_line()], [po_line(qty=4)], [], rules.STAGE_PARTIALLY_ORDERED),
			([mr_line()], [po_line()], [], rules.STAGE_ORDERED),
			([mr_line()], [po_line()], [pr_line(qty=3)], rules.STAGE_PARTIALLY_RECEIVED),
			([mr_line()], [po_line()], [pr_line(qty=10)], rules.STAGE_RECEIVED),
			# A draft receipt receives nothing yet.
			([mr_line()], [po_line()], [pr_line(qty=10, docstatus=0)], rules.STAGE_ORDERED),
		]
		for requests, orders, receipts, want in cases:
			with self.subTest(want=want):
				stages, _lines = self.stage(requests, orders, receipts)
				self.assertEqual(stages, [want])

	def test_a_received_line_stays_a_week_then_drops_off(self):
		recent = [pr_line(posted=TODAY - dt.timedelta(days=rules.RECEIVED_VISIBLE_DAYS))]
		self.assertEqual(self.stage([mr_line()], [po_line()], recent)[0], [rules.STAGE_RECEIVED])
		old = [pr_line(posted=TODAY - dt.timedelta(days=rules.RECEIVED_VISIBLE_DAYS + 1))]
		self.assertEqual(self.stage([mr_line()], [po_line()], old)[0], [])

	def test_a_purchase_order_with_no_request(self):
		stages, lines = self.stage([], [po_line(mri=None, stage=rules.ORDER_STAGE_PICKUP)], [])
		self.assertEqual(stages, [rules.STAGE_ORDERED])
		self.assertIsNone(lines[0]["request"])
		self.assertEqual(lines[0]["orders"][0]["arrival"], "pickup")
		self.assertEqual(lines[0]["expected"], TODAY + dt.timedelta(days=5))

	def test_order_view_carries_supplier_and_delivery(self):
		_stages, lines = self.stage([mr_line()], [po_line(stage=rules.ORDER_STAGE_DELIVERY)], [])
		order = lines[0]["orders"][0]
		self.assertEqual(
			(order["name"], order["supplier_name"], order["arrival"]),
			("PO-1", "Example Supplier", "delivery"),
		)


class TestCoverage(unittest.TestCase):
	PROJECTED = {"ITEM-A": {"All - X": 1.0}, "ITEM-B": {"All - X": 0.0}}

	def board(self, tracked, watches=None):
		rows = [rule("ITEM-A"), rule("ITEM-B")]
		return rules.assemble(rows, self.PROJECTED, tracked, watches or {}, NOW)

	def test_both_below_and_uncovered_need_reordering(self):
		b = self.board([])
		self.assertEqual([l["item_code"] for l in b["needs"]], ["ITEM-A", "ITEM-B"])
		self.assertEqual(b["needs"][0]["suggested_qty"], 10)

	def test_an_item_on_an_open_request_leaves_needs_reorder(self):
		for docstatus in (0, 1):
			with self.subTest(docstatus=docstatus):
				tracked = rules.track_lines([mr_line(docstatus=docstatus)], [], [], TODAY)
				b = self.board(tracked)
				self.assertEqual([l["item_code"] for l in b["needs"]], ["ITEM-B"])
				a = next(l for l in b["lines"] if l["item_code"] == "ITEM-A")
				self.assertTrue(a["covered"])
				self.assertEqual(a["covered_by"], ["MR-1"])

	def test_a_received_line_no_longer_covers(self):
		tracked = rules.track_lines([mr_line()], [po_line()], [pr_line()], TODAY)
		self.assertEqual([l["item_code"] for l in self.board(tracked)["needs"]], ["ITEM-A", "ITEM-B"])

	def test_a_request_for_another_warehouse_does_not_cover(self):
		tracked = rules.track_lines([mr_line(warehouse="Other - X")], [], [], TODAY)
		self.assertEqual(len(self.board(tracked)["needs"]), 2)

	def test_a_snoozed_line_moves_to_snoozed(self):
		watch = {"snooze_kind": rules.SNOOZE_WEEK, "snoozed_until": NOW + dt.timedelta(days=2)}
		b = self.board([], {rules.line_key("ITEM-A", "Room - X"): watch})
		self.assertEqual([l["item_code"] for l in b["needs"]], ["ITEM-B"])
		self.assertEqual([l["item_code"] for l in b["snoozed"]], ["ITEM-A"])


class TestClosedShort(unittest.TestCase):
	"""Review blocker: a PO closed short must stop covering. ERPNext v16.50.0 closing a PO updates
	the Bin, never the MR, which stays Ordered / Partially Received; prod had 87 of 104 Closed POs
	under 100% received, and MAT-MR-2026-00007 (Ordered, its PO Closed) already had this shape."""

	PROJECTED = {"ITEM-A": {"All - X": 1.0}}

	def needs(self, orders, receipts=()):
		tracked = rules.track_lines([mr_line()], list(orders), list(receipts), TODAY)
		board = rules.assemble([rule("ITEM-A")], self.PROJECTED, tracked, {}, NOW)
		return [l["item_code"] for l in board["needs"]], tracked

	def test_closed_after_part_arrived(self):
		needs, tracked = self.needs([po_line(status="Closed")], [pr_line(qty=4)])
		self.assertEqual(needs, ["ITEM-A"], "the item is back on Needs reorder")
		self.assertEqual(tracked, [], "and the closed-short line is off On order")

	def test_closed_with_nothing_received(self):
		needs, tracked = self.needs([po_line(status="Closed")])
		self.assertEqual(needs, ["ITEM-A"])
		self.assertEqual(tracked, [])

	def test_an_open_order_received_short_still_covers(self):
		needs, tracked = self.needs([po_line()], [pr_line(qty=4)])
		self.assertEqual(needs, [])
		self.assertEqual([t["stage"] for t in tracked], [rules.STAGE_PARTIALLY_RECEIVED])

	def test_on_hold_still_covers(self):
		# ERPNext keeps an On Hold order in the Bin's ordered qty (only Closed and Delivered drop
		# out), so the projected qty still counts it: the board must agree.
		needs, tracked = self.needs([po_line(status="On Hold")])
		self.assertEqual(needs, [])
		self.assertTrue(tracked[0]["open"])

	def test_part_ordered_on_a_closed_po_leaves_the_rest_to_come(self):
		# 10 requested, 4 on a Closed PO: the 6 never ordered are still wanted, so it covers.
		needs, tracked = self.needs([po_line(qty=4, status="Closed")])
		self.assertEqual(needs, [])
		self.assertAlmostEqual(rules.still_to_come(mr_line(), [po_line(qty=4, status="Closed")], {}), 6)

	def test_a_closed_order_with_no_request(self):
		order = po_line(mri=None, status="Closed")
		self.assertEqual(rules.order_still_to_come(order, {}), 0)
		self.assertEqual(rules.track_lines([], [order], [], TODAY), [])
		self.assertEqual(rules.order_still_to_come(po_line(mri=None, status="On Hold"), {}), 10)


class TestOldDrafts(unittest.TestCase):
	PROJECTED = {"ITEM-A": {"All - X": 1.0}}

	def board(self, created):
		tracked = rules.track_lines([mr_line(docstatus=0, created=created)], [], [], TODAY)
		return rules.assemble([rule("ITEM-A")], self.PROJECTED, tracked, {}, NOW), tracked

	def test_a_draft_older_than_the_limit_stops_covering(self):
		created = TODAY - dt.timedelta(days=rules.STALE_DRAFT_DAYS + 1)
		board, tracked = self.board(created)
		self.assertEqual([l["item_code"] for l in board["needs"]], ["ITEM-A"])
		self.assertEqual(board["needs"][0]["old_drafts"], ["MR-1"])
		self.assertEqual([t["stage"] for t in tracked], [rules.STAGE_OLD_DRAFT])
		self.assertEqual(tracked[0]["stage_label"], "Old draft request")
		self.assertFalse(tracked[0]["open"])

	def test_a_draft_within_the_limit_still_covers(self):
		board, _tracked = self.board(TODAY - dt.timedelta(days=rules.STALE_DRAFT_DAYS))
		self.assertEqual(board["needs"], [])
		self.assertEqual(rules.STALE_DRAFT_DAYS, 30)

	def test_a_submitted_request_is_never_stale(self):
		self.assertFalse(rules.is_stale_draft(mr_line(created=TODAY - dt.timedelta(days=400)), TODAY))


class TestCoverageByGroup(unittest.TestCase):
	"""ERPNext judges a group rule on every warehouse under the group, so a request for any of them
	is stock the rule is waiting for."""

	PARENTS = {
		"Room - X": "All - X",
		"Bin 1 - X": "Inventory - X",
		"Bin 2 - X": "Inventory - X",
		"Inventory - X": "All - X",
	}

	def needs(self, rule_row, warehouse):
		ancestors = rules.ancestor_map(self.PARENTS)
		tracked = rules.track_lines([mr_line(docstatus=0, warehouse=warehouse)], [], [], TODAY)
		projected = {"ITEM-A": {rule_row.get("warehouse_group") or rule_row["warehouse"]: 0.0}}
		board = rules.assemble([rule_row], projected, tracked, {}, NOW, ancestors)
		return [l["item_code"] for l in board["needs"]]

	def test_a_draft_for_a_sibling_bin_covers_a_group_rule(self):
		self.assertEqual(
			self.needs(rule("ITEM-A", warehouse="Bin 1 - X", group="Inventory - X"), "Bin 2 - X"), []
		)

	def test_a_draft_outside_the_group_does_not(self):
		self.assertEqual(
			self.needs(rule("ITEM-A", warehouse="Bin 1 - X", group="Inventory - X"), "Room - X"), ["ITEM-A"]
		)

	def test_a_rule_without_a_group_counts_its_own_warehouse_only(self):
		row = rule("ITEM-A", warehouse="Bin 1 - X", group=None)
		self.assertEqual(self.needs(row, "Bin 2 - X"), ["ITEM-A"])
		self.assertEqual(self.needs(row, "Bin 1 - X"), [])

	def test_a_bin_that_is_its_own_group(self):
		# Production has one rule whose group is its own bin: only that bin counts.
		row = rule("ITEM-A", warehouse="Bin 1 - X", group="Bin 1 - X")
		self.assertEqual(self.needs(row, "Bin 2 - X"), ["ITEM-A"])
		self.assertEqual(self.needs(row, "Bin 1 - X"), [])

	def test_ancestor_map_survives_a_loop(self):
		self.assertEqual(rules.ancestor_map({"A": "B", "B": "A"})["A"], frozenset({"B", "A"}))


class TestSnooze(unittest.TestCase):
	def test_one_week(self):
		fields, err = rules.snooze_fields(rules.SNOOZE_WEEK, 3, NOW, " not urgent ", "buyer@example.com")
		self.assertIsNone(err)
		self.assertEqual(fields["snoozed_until"], NOW + dt.timedelta(days=7))
		self.assertEqual(fields["snooze_reason"], "not urgent")
		self.assertEqual(fields["snoozed_by"], "buyer@example.com")
		self.assertEqual(rules.snooze_state(fields, 3, NOW + dt.timedelta(days=6)), "active")
		self.assertEqual(rules.snooze_state(fields, 3, NOW + dt.timedelta(days=7)), "ended")

	def test_one_month_clamps_the_day(self):
		jan31 = dt.datetime(2027, 1, 31, 9, 0)
		fields, _err = rules.snooze_fields(rules.SNOOZE_MONTH, 3, jan31)
		self.assertEqual(fields["snoozed_until"], dt.datetime(2027, 2, 28, 9, 0))
		dec = dt.datetime(2026, 12, 15)
		self.assertEqual(
			rules.snooze_fields(rules.SNOOZE_MONTH, 0, dec)[0]["snoozed_until"], dt.datetime(2027, 1, 15)
		)
		self.assertEqual(rules.snooze_state(fields, 3, dt.datetime(2027, 2, 27)), "active")
		self.assertEqual(rules.snooze_state(fields, 3, dt.datetime(2027, 2, 28, 9, 0)), "ended")

	def test_until_stock_drops_further(self):
		fields, _err = rules.snooze_fields(rules.SNOOZE_DROP, 4, NOW)
		self.assertEqual(fields["snooze_projected_qty"], 4)
		self.assertIsNone(fields["snoozed_until"])
		self.assertEqual(rules.snooze_state(fields, 4, NOW + dt.timedelta(days=90)), "active")
		self.assertEqual(rules.snooze_state(fields, 5, NOW), "active")
		self.assertEqual(rules.snooze_state(fields, 3, NOW), "ended")

	def test_an_unknown_kind_is_refused(self):
		self.assertIsNotNone(rules.snooze_fields("forever", 0, NOW)[1])

	def test_sync_plan(self):
		key_a = rules.line_key("ITEM-A", "Room - X")
		key_b = rules.line_key("ITEM-B", "Room - X")
		key_gone = rules.line_key("ITEM-C", "Room - X")
		lines = [
			{"key": key_a, "item_code": "ITEM-A", "warehouse": "Room - X", "below": True, "projected_qty": 1},
			{"key": key_b, "item_code": "ITEM-B", "warehouse": "Room - X", "below": True, "projected_qty": 1},
		]
		watches = {
			key_a: {
				"name": "w-a",
				"snooze_kind": rules.SNOOZE_DROP,
				"snooze_projected_qty": 2,
				"alerted_on": NOW,
			},
			key_gone: {"name": "w-c", "alerted_on": NOW},
		}
		inserts, updates, deletes = rules.sync_plan(lines, watches, NOW)
		self.assertEqual([i["key"] for i in inserts], [key_b])
		self.assertEqual(inserts[0]["below_since"], NOW)
		self.assertEqual(deletes, [key_gone])
		self.assertEqual(len(updates), 1)
		key, fields = updates[0]
		self.assertEqual(key, key_a)
		self.assertIsNone(fields["alerted_on"])
		self.assertIsNone(fields["snooze_kind"])
		self.assertEqual(fields["snooze_ended_on"], NOW)


class TestDigestPick(unittest.TestCase):
	def test_only_newly_below_and_ended(self):
		lines = [{"key": k, "snooze": None} for k in ("new", "told", "ended", "nowatch")]
		lines.append({"key": "snoozed", "snooze": "active"})
		watches = {
			"new": {"alerted_on": None},
			"told": {"alerted_on": NOW},
			"ended": {"alerted_on": None, "snooze_ended_on": NOW},
			"snoozed": {"alerted_on": None, "snooze_kind": rules.SNOOZE_WEEK},
		}
		newly, ended = rules.digest_pick(lines, watches)
		self.assertEqual([l["key"] for l in newly], ["new"])
		self.assertEqual([l["key"] for l in ended], ["ended"])

	def test_nothing_new(self):
		self.assertEqual(rules.digest_pick([{"key": "a"}], {"a": {"alerted_on": NOW}}), ([], []))


class TestRecipients(unittest.TestCase):
	def test_exclusions(self):
		users = [
			{"name": "Administrator", "email": "admin@example.com", "enabled": 1, "user_type": "System User"},
			{"name": "Guest", "email": "", "enabled": 1, "user_type": "System User"},
			{
				"name": "triton@sapphirefountains.com",
				"email": "triton@sapphirefountains.com",
				"enabled": 1,
				"user_type": "System User",
			},
			{
				"name": "buyer@example.com",
				"email": "buyer@example.com",
				"enabled": 1,
				"user_type": "System User",
			},
			{
				"name": "BUYER@example.com",
				"email": "buyer@example.com",
				"enabled": 1,
				"user_type": "System User",
			},
			{
				"name": "gone@example.com",
				"email": "gone@example.com",
				"enabled": 0,
				"user_type": "System User",
			},
			{
				"name": "web@example.com",
				"email": "web@example.com",
				"enabled": 1,
				"user_type": "Website User",
			},
			{
				"name": "boss@example.com",
				"email": "boss@example.com",
				"enabled": 1,
				"user_type": "System User",
			},
		]
		self.assertEqual(rules.filter_recipients(users), ["buyer@example.com", "boss@example.com"])

	def test_client_ref(self):
		self.assertTrue(rules.valid_client_ref("0b7c9d1e-1234-4abc-9def-001122334455"))
		self.assertFalse(rules.valid_client_ref("short"))
		self.assertFalse(rules.valid_client_ref("has space in it!"))
		self.assertFalse(rules.valid_client_ref(None))


# --- the frappe-backed module, on a stub ----------------------------------------------

STATE = {}


class _Doc(dict):
	"""Enough of a frappe Document for the code under test."""

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError as e:
			raise AttributeError(name) from e

	def __setattr__(self, name, value):
		self[name] = value

	def update(self, *args, **kwargs):
		dict.update(self, *args, **kwargs)

	def append(self, table, row):
		self.setdefault(table, []).append(_Doc(row))

	def insert(self, ignore_permissions=False):
		if self.get("doctype") == "Material Request":
			STATE["mr_seq"] += 1
			self["name"] = f"MAT-MR-{STATE['mr_seq']:05d}"
			self["docstatus"] = 0
			STATE["mr_names"].add(self["name"])
		STATE["inserts"].append((self.get("doctype"), dict(self), ignore_permissions))
		return self

	def save(self, ignore_permissions=False):
		STATE["saves"].append((self.get("doctype"), dict(self), ignore_permissions))
		return self

	def set(self, key, value):
		self[key] = value

	def check_permission(self, ptype="read"):
		# The Item's own permission: STATE["perm"][("Item", ptype)] False refuses it.
		if STATE["perm"].get((self.get("doctype"), ptype), True) is False:
			raise sys.modules["frappe"].PermissionError(f"No {ptype} permission")

	def submit(self):
		STATE["submits"].append(self.get("name"))


def _install_stubs():
	frappe = types.ModuleType("frappe")

	class ValidationError(Exception):
		pass

	class PermissionError(Exception):
		pass

	class DuplicateEntryError(Exception):
		pass

	class UniqueValidationError(ValidationError):
		pass

	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError
	frappe.DuplicateEntryError = DuplicateEntryError
	frappe.UniqueValidationError = UniqueValidationError
	frappe.flags = types.SimpleNamespace(mute_messages=False)
	frappe.local = types.SimpleNamespace(site="erp.example.com")
	frappe._ = lambda s, *a, **k: s
	frappe.session = types.SimpleNamespace(user="buyer@example.com")

	def throw(msg="", exc=ValidationError, title=None):
		raise exc(msg)

	frappe.throw = throw

	def whitelist(*dargs, **dkwargs):
		def wrap(fn):
			return fn

		return wrap(dargs[0]) if dargs and callable(dargs[0]) else wrap

	frappe.whitelist = whitelist
	frappe.get_roles = lambda user=None: STATE["roles"]
	frappe.has_permission = lambda doctype=None, ptype="read", *a, **k: STATE["perm"].get(
		(doctype, ptype), True
	)
	frappe.parse_json = lambda s: json.loads(s) if isinstance(s, str) else s
	frappe.get_traceback = lambda *a, **k: "traceback"
	frappe.log_error = lambda *a, **k: STATE["logged"].append((a, k))
	frappe.new_doc = lambda doctype: _Doc(doctype=doctype)

	def get_doc(arg, name=None, **kwargs):
		if isinstance(arg, dict):
			return _Doc(arg)
		return _Doc(STATE["docs"][(arg, name)])

	frappe.get_doc = get_doc
	frappe.sendmail = lambda **kw: STATE["mail"].append(kw)
	frappe.clear_document_cache = lambda *a, **k: None
	frappe.get_all = lambda *a, **k: []
	frappe.get_meta = lambda doctype: STATE["meta"]
	frappe.delete_doc = lambda doctype, name, **kw: STATE["deletes"].append((doctype, name))

	def sql(query, values=None, as_dict=False):
		STATE["sql"].append((query, values))
		handler = STATE.get("sql_handler")
		if handler:
			return handler(query, values)
		if "get_lock" in query:
			return [[1]]
		return []

	def set_value(doctype, name, fields, value=None, update_modified=True):
		STATE["set_values"].append((doctype, name, fields))

	def set_single_value(doctype, field, value):
		STATE["singles"].append((doctype, field, value))

	def get_single_value(doctype, field):
		key = (doctype, field)
		if isinstance(STATE["single_values"].get(key), Exception):
			raise STATE["single_values"][key]
		return STATE["single_values"].get(key)

	def exists(doctype, name=None):
		if doctype == "Material Request":
			return name in STATE["mr_names"]
		if doctype == "Item":
			return ("Item", name) in STATE["docs"]
		if doctype == "DocType":
			return True
		return name in STATE["watch_names"]

	def commit():
		STATE["commits"] += 1

	frappe.db = types.SimpleNamespace(
		sql=sql,
		set_value=set_value,
		set_single_value=set_single_value,
		get_single_value=get_single_value,
		exists=exists,
		commit=commit,
		rollback=lambda: None,
		get_value=lambda *a, **k: STATE.get("get_value"),
		has_column=lambda *a, **k: True,
	)

	utils = types.ModuleType("frappe.utils")

	def cint(v):
		try:
			return int(float(v or 0))
		except (TypeError, ValueError):
			return 0

	utils.cint = cint
	utils.cstr = lambda v: "" if v is None else str(v)
	utils.now_datetime = lambda: NOW
	utils.nowdate = lambda: TODAY.isoformat()
	utils.getdate = lambda v=None: dt.date.fromisoformat(v) if isinstance(v, str) else (v or TODAY)
	utils.add_days = lambda d, n: d + dt.timedelta(days=n)
	utils.get_url = lambda path="": "https://erp.example.com" + (path or "")
	utils.get_url_to_form = lambda doctype, name: f"https://erp.example.com/desk/{doctype}/{name}"
	utils.format_date = lambda v=None: str(v)
	frappe.utils = utils

	database = types.ModuleType("frappe.database")
	database_database = types.ModuleType("frappe.database.database")

	@contextlib.contextmanager
	def savepoint(catch=Exception):
		STATE["savepoints"] += 1
		try:
			yield
		except catch:
			STATE["rolled_back_to_savepoint"] += 1

	database_database.savepoint = savepoint
	database.database = database_database
	frappe.database = database

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = object
	model.document = document
	frappe.model = model

	email_style = types.ModuleType("erpnext_enhancements.email_style")
	for fn in ("p", "h", "note"):
		setattr(email_style, fn, (lambda name: lambda text: f"<{name}>{text}</{name}>")(fn))
	email_style.kpis = lambda cards: "<kpis>" + json.dumps(cards) + "</kpis>"
	email_style.table = lambda headers, rows: "<table>" + json.dumps(rows) + "</table>"
	email_style.button = lambda url, label, tone="primary": f'<a href="{url}">{label}</a>'
	email_style.wrap = lambda body, **kw: f"<shell>{body}</shell>"

	for name, module in {
		"frappe": frappe,
		"frappe.utils": utils,
		"frappe.model": model,
		"frappe.model.document": document,
		"frappe.database": database,
		"frappe.database.database": database_database,
		"erpnext_enhancements.email_style": email_style,
	}.items():
		sys.modules[name] = module


_SAVED = {}
board = None
patch_auto = None
patch_role = None
suggestions = None
api = None


def setUpModule():
	global board, patch_auto, patch_role, suggestions, api
	for name in (
		"frappe",
		"frappe.utils",
		"frappe.model",
		"frappe.model.document",
		"frappe.database",
		"frappe.database.database",
		"erpnext_enhancements.email_style",
	):
		_SAVED[name] = sys.modules.get(name)
	_install_stubs()
	for name in (
		"erpnext_enhancements.inventory_enhancements.doctype.reorder_watch.reorder_watch",
		"erpnext_enhancements.inventory_enhancements.reorder_board",
		"erpnext_enhancements.patches.disable_auto_material_requests",
		"erpnext_enhancements.patches.backfill_reorder_alert_role",
		"erpnext_enhancements.inventory_enhancements.reorder_suggestions",
		"erpnext_enhancements.api.reorder_board",
	):
		sys.modules.pop(name, None)
	board = importlib.import_module("erpnext_enhancements.inventory_enhancements.reorder_board")
	patch_auto = importlib.import_module("erpnext_enhancements.patches.disable_auto_material_requests")
	patch_role = importlib.import_module("erpnext_enhancements.patches.backfill_reorder_alert_role")
	suggestions = importlib.import_module("erpnext_enhancements.inventory_enhancements.reorder_suggestions")
	api = importlib.import_module("erpnext_enhancements.api.reorder_board")


def tearDownModule():
	for name, module in _SAVED.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def _reset(**over):
	STATE.clear()
	STATE.update(
		{
			"roles": ["Purchase Manager"],
			"perm": {},
			"inserts": [],
			"saves": [],
			"submits": [],
			"set_values": [],
			"singles": [],
			"single_values": {},
			"sql": [],
			"logged": [],
			"mail": [],
			"deletes": [],
			"docs": {},
			"mr_seq": 0,
			"mr_names": set(),
			"watch_names": set(),
			"commits": 0,
			"meta": None,
			"savepoints": 0,
			"rolled_back_to_savepoint": 0,
		}
	)
	STATE.update(over)


class _BoardCase(unittest.TestCase):
	"""``build_board`` replaced by ``rules.assemble`` over in-memory rules, requests and watches."""

	PROJECTED = {"ITEM-A": {"All - X": 1.0}, "ITEM-B": {"All - X": 0.0}, "ITEM-C": {"All - X": 50.0}}

	def setUp(self):
		_reset()
		self.requests = []
		self.watches = {}
		self._orig_build = board.build_board
		self._orig_watches = board.read_watches
		board.build_board = self.fake_build
		board.read_watches = lambda: self.watches

	def tearDown(self):
		board.build_board = self._orig_build
		board.read_watches = self._orig_watches

	def fake_build(self, include_rates=False, now=None):
		rows = [rule("ITEM-A"), rule("ITEM-B", purchase_uom="Box", conversion_factor=4.0), rule("ITEM-C")]
		# Every Material Request the stub has inserted is an open Draft line for its items.
		requests = list(self.requests)
		for _doctype, doc, _ip in [i for i in STATE["inserts"] if i[0] == "Material Request"]:
			for n, item in enumerate(doc["items"]):
				requests.append(
					mr_line(
						name=f"{doc['name']}-{n}",
						parent=doc["name"],
						docstatus=0,
						item=item["item_code"],
						warehouse=item["warehouse"],
					)
				)
		tracked = rules.track_lines(requests, [], [], TODAY)
		ancestors = rules.ancestor_map({"Room - X": "All - X"})
		out = rules.assemble(rows, self.PROJECTED, tracked, self.watches, now or NOW, ancestors)
		out["on_order"] = tracked
		out["watches"] = self.watches
		return out


class TestCreateRequests(_BoardCase):
	LINES = [
		{"item_code": "ITEM-A", "warehouse": "Room - X", "qty": 12},
		{"item_code": "ITEM-B", "warehouse": "Room - X", "qty": 10},
	]
	REF = "0b7c9d1e-1234-4abc-9def-001122334455"

	def mrs(self):
		return [doc for doctype, doc, _ip in STATE["inserts"] if doctype == "Material Request"]

	def test_one_draft_request_inserted_as_the_caller(self):
		out = board.create_requests(json.dumps(self.LINES), self.REF)
		self.assertTrue(out["ok"], out)
		self.assertFalse(out["replayed"])
		self.assertEqual([r["name"] for r in out["requests"]], ["MAT-MR-00001"])
		mr_inserts = [i for i in STATE["inserts"] if i[0] == "Material Request"]
		self.assertEqual(len(mr_inserts), 1)
		_doctype, doc, ignore_permissions = mr_inserts[0]
		self.assertFalse(ignore_permissions, "the request is inserted as the person")
		self.assertEqual(STATE["submits"], [], "a Draft: the buyer submits it on the form")
		self.assertEqual(doc["material_request_type"], "Purchase")
		self.assertEqual(doc["company"], "Example Co")
		items = {i["item_code"]: i for i in doc["items"]}
		self.assertEqual(items["ITEM-A"]["qty"], 12)
		self.assertEqual(items["ITEM-A"]["warehouse"], "Room - X")
		self.assertEqual(items["ITEM-A"]["schedule_date"], TODAY + dt.timedelta(days=3))
		# Stock 10 in a box of 4: 3 boxes, rounded up as ERPNext's create_material_request does.
		self.assertEqual(
			(items["ITEM-B"]["qty"], items["ITEM-B"]["uom"], items["ITEM-B"]["conversion_factor"]),
			(3, "Box", 4.0),
		)
		self.assertEqual(doc["schedule_date"], TODAY + dt.timedelta(days=3))
		# Serialised: the named lock taken and released.
		queries = [q for q, _v in STATE["sql"]]
		self.assertTrue(any("get_lock" in q for q in queries))
		self.assertTrue(any("release_lock" in q for q in queries))

	def test_a_retry_with_the_same_ref_returns_the_same_request(self):
		first = board.create_requests(self.LINES, self.REF)
		self.assertTrue(first["ok"])
		# What _remember_request wrote (watch rows were missing, so it inserted them) becomes the
		# watch state the next call reads.
		for doctype, doc, _ip in STATE["inserts"]:
			if doctype == "Reorder Watch":
				self.watches[rules.line_key(doc["item_code"], doc["warehouse"])] = dict(
					doc, name=doc["item_code"]
				)
		self.assertEqual(len(self.watches), 2)
		again = board.create_requests(self.LINES, self.REF)
		self.assertTrue(again["ok"])
		self.assertTrue(again["replayed"])
		self.assertEqual([r["name"] for r in again["requests"]], ["MAT-MR-00001"])
		self.assertEqual(len(self.mrs()), 1, "a double click or a retry never makes two")

	def test_a_new_click_on_a_requested_line_is_refused(self):
		board.create_requests(self.LINES, self.REF)
		out = board.create_requests(self.LINES, "ffffffff-1234-4abc-9def-001122334455")
		self.assertFalse(out["ok"])
		self.assertEqual(len(out["stale"]), 2)
		self.assertIn("already on MAT-MR-00001", out["stale"][0]["reason"])
		self.assertEqual(len(self.mrs()), 1)

	def test_a_line_no_longer_below_is_refused_and_nothing_is_written(self):
		out = board.create_requests(
			[
				{"item_code": "ITEM-A", "warehouse": "Room - X", "qty": 5},
				{"item_code": "ITEM-C", "warehouse": "Room - X", "qty": 5},
			],
			self.REF,
		)
		self.assertFalse(out["ok"])
		self.assertEqual([s["key"] for s in out["stale"]], [rules.line_key("ITEM-C", "Room - X")])
		self.assertEqual(self.mrs(), [])

	def test_an_unknown_rule_and_a_bad_qty_are_refused(self):
		for lines in (
			[{"item_code": "ITEM-Z", "warehouse": "Room - X", "qty": 5}],
			[{"item_code": "ITEM-A", "warehouse": "Room - X", "qty": 0}],
			[{"item_code": "ITEM-A", "warehouse": "Room - X", "qty": 2.5}],
			[{"item_code": "ITEM-A", "warehouse": "Room - X", "qty": rules.MAX_QTY + 1}],
		):
			with self.subTest(lines=lines):
				out = board.create_requests(lines, self.REF)
				self.assertFalse(out["ok"])
		self.assertEqual(self.mrs(), [])

	def test_who_may(self):
		STATE["roles"] = ["Stock User"]
		with self.assertRaises(sys.modules["frappe"].PermissionError):
			board.create_requests(self.LINES, self.REF)
		STATE["roles"] = ["Purchase User"]
		STATE["perm"][("Material Request", "create")] = False
		with self.assertRaises(sys.modules["frappe"].PermissionError):
			board.create_requests(self.LINES, self.REF)
		self.assertEqual(self.mrs(), [])

	def test_a_bad_ref_or_no_lines(self):
		with self.assertRaises(board.BoardError):
			board.create_requests(self.LINES, "x")
		with self.assertRaises(board.BoardError):
			board.create_requests([], self.REF)

	def test_the_lock_is_the_sites(self):
		name = board.lock_name()
		self.assertTrue(name.startswith(board.LOCK_NAME + ":"))
		self.assertLessEqual(len(name), 64, "MySQL refuses a named lock over 64 characters")
		frappe = sys.modules["frappe"]
		frappe.local.site = "other.example.com"
		try:
			self.assertNotEqual(board.lock_name(), name)
		finally:
			frappe.local.site = "erp.example.com"
		board.create_requests(self.LINES, self.REF)
		locks = [v for q, v in STATE["sql"] if "get_lock" in q]
		self.assertEqual(locks[0][0], name)

	def test_a_watch_row_made_meanwhile_is_written_quietly(self):
		# _ensure_watch: the row did not exist when looked for, the insert collides (the hourly sync
		# won), and the request is still remembered on that row, with no error and no message.
		frappe = sys.modules["frappe"]
		original = _Doc.insert

		def collide(doc, ignore_permissions=False):
			if doc.get("doctype") == "Reorder Watch":
				raise frappe.UniqueValidationError("duplicate")
			return original(doc, ignore_permissions)

		_Doc.insert = collide
		try:
			out = board.create_requests(self.LINES, self.REF)
		finally:
			_Doc.insert = original
		self.assertTrue(out["ok"])
		written = {
			name
			for doctype, name, fields in STATE["set_values"]
			if fields.get("last_request_ref") == self.REF
		}
		self.assertEqual(written, {"ITEM-A::Room - X", "ITEM-B::Room - X"})

	def test_a_held_lock_refuses(self):
		STATE["sql_handler"] = lambda q, v: [[0]] if "get_lock" in q else []
		with self.assertRaises(board.BoardError):
			board.create_requests(self.LINES, self.REF)
		self.assertEqual(self.mrs(), [])


class TestSnoozeEndpoints(_BoardCase):
	def test_snooze_inserts_as_the_person(self):
		out = board.snooze("ITEM-A", "Room - X", rules.SNOOZE_DROP, "seasonal")
		self.assertTrue(out["ok"])
		doctype, doc, ignore_permissions = STATE["inserts"][-1]
		self.assertEqual(doctype, "Reorder Watch")
		self.assertFalse(ignore_permissions)
		self.assertEqual(doc["snooze_projected_qty"], 1.0)
		self.assertEqual(doc["snoozed_by"], "buyer@example.com")
		self.assertEqual(doc["snooze_reason"], "seasonal")

	def test_only_purchasing_may_snooze(self):
		# Stock Manager (17 people on prod, technicians among them) may view and ask for a Draft, but a
		# snooze silences the Purchase Managers' digest, so it is Purchasing's.
		STATE["roles"] = ["Stock Manager"]
		with self.assertRaises(sys.modules["frappe"].PermissionError):
			board.snooze("ITEM-A", "Room - X", rules.SNOOZE_WEEK)
		with self.assertRaises(sys.modules["frappe"].PermissionError):
			board.unsnooze("ITEM-A", "Room - X")
		self.assertFalse(board.permissions()["can_snooze"])
		self.assertTrue(board.permissions()["can_create"])
		self.assertEqual([i for i in STATE["inserts"] if i[0] == "Reorder Watch"], [])
		for role in ("Purchase User", "System Manager"):
			with self.subTest(role=role):
				STATE["roles"] = [role]
				self.assertTrue(board.permissions()["can_snooze"])
		self.assertEqual(set(board.SNOOZE_ROLES), {"System Manager", "Purchase Manager", "Purchase User"})

	def test_a_racing_insert_lands_on_the_existing_row(self):
		# The hourly sync made the row a moment earlier: the insert collides, nothing is raised or
		# left queued for the reply, and the snooze is saved onto that row.
		frappe = sys.modules["frappe"]
		key = "ITEM-A::Room - X"
		STATE["docs"][("Reorder Watch", key)] = {"doctype": "Reorder Watch", "name": key}
		original = _Doc.insert

		def collide(doc, ignore_permissions=False):
			assert frappe.flags.mute_messages, "the collision's msgprint must be muted"
			raise frappe.DuplicateEntryError("Reorder Watch", key)

		_Doc.insert = collide
		try:
			out = board.snooze("ITEM-A", "Room - X", rules.SNOOZE_WEEK)
		finally:
			_Doc.insert = original
		self.assertTrue(out["ok"])
		self.assertEqual(STATE["rolled_back_to_savepoint"], 1)
		self.assertFalse(frappe.flags.mute_messages, "muting is restored")
		self.assertEqual(STATE["saves"][-1][1]["snooze_kind"], rules.SNOOZE_WEEK)

	def test_snooze_refuses_a_line_not_below(self):
		with self.assertRaises(board.BoardError):
			board.snooze("ITEM-C", "Room - X", rules.SNOOZE_WEEK)

	def test_unsnooze_clears_and_saves_as_the_person(self):
		STATE["get_value"] = "ITEM-A::Room - X"
		STATE["docs"][("Reorder Watch", "ITEM-A::Room - X")] = {
			"doctype": "Reorder Watch",
			"name": "ITEM-A::Room - X",
			"snooze_kind": rules.SNOOZE_WEEK,
		}
		board.unsnooze("ITEM-A", "Room - X")
		doctype, doc, ignore_permissions = STATE["saves"][-1]
		self.assertEqual(doctype, "Reorder Watch")
		self.assertIsNone(doc["snooze_kind"])
		self.assertFalse(ignore_permissions)


class TestDigest(_BoardCase):
	def setUp(self):
		super().setUp()
		STATE["single_values"][("Inventory Scanner Settings", "reorder_alert_role")] = "Purchase Manager"
		self.users = [
			{
				"name": "buyer@example.com",
				"email": "buyer@example.com",
				"enabled": 1,
				"user_type": "System User",
			},
			{"name": "Administrator", "email": "admin@example.com", "enabled": 1, "user_type": "System User"},
		]
		STATE["sql_handler"] = lambda q, v: self.users if "Has Role" in q else []
		# The hourly sync's writes, reflected in the watches the digest then reads.
		self._orig_apply = board._apply_sync
		board._apply_sync = self.apply_sync

	def tearDown(self):
		board._apply_sync = self._orig_apply
		super().tearDown()

	def apply_sync(self, built, now):
		inserts, updates, deletes = rules.sync_plan(built["lines"], self.watches, now)
		for row in inserts:
			self.watches[row["key"]] = {"name": row["key"], "below_since": now, "alerted_on": None}
		for key, fields in updates:
			self.watches[key].update(fields)
		for key in deletes:
			self.watches.pop(key)
		return len(inserts), len(updates), len(deletes)

	def notifications(self):
		return [doc for doctype, doc, _ip in STATE["inserts"] if doctype == "Notification Log"]

	def test_first_digest_names_both_and_stamps_them(self):
		board.send_daily_digest()
		self.assertEqual(len(STATE["mail"]), 1)
		mail = STATE["mail"][0]
		self.assertEqual(mail["recipients"], ["buyer@example.com"])
		self.assertIn("2 item(s)", mail["subject"])
		self.assertIn("ITEM-A", mail["message"])
		self.assertIn("/desk/reorder-board", mail["message"])
		bells = self.notifications()
		self.assertEqual([b["for_user"] for b in bells], ["buyer@example.com"])
		self.assertEqual(bells[0]["link"], "/desk/reorder-board")
		self.assertEqual(bells[0]["type"], "Alert")
		stamped = [
			name
			for doctype, name, fields in STATE["set_values"]
			if doctype == "Reorder Watch" and fields.get("alerted_on") == NOW
		]
		self.assertEqual(sorted(stamped), sorted(self.watches))

	def test_nothing_new_sends_nothing(self):
		for key in (rules.line_key("ITEM-A", "Room - X"), rules.line_key("ITEM-B", "Room - X")):
			self.watches[key] = {"name": key, "alerted_on": NOW - dt.timedelta(days=1)}
		board.send_daily_digest()
		self.assertEqual(STATE["mail"], [])
		self.assertEqual(self.notifications(), [])

	def test_a_snoozed_item_is_never_repeated_and_an_ended_one_is(self):
		a = rules.line_key("ITEM-A", "Room - X")
		b = rules.line_key("ITEM-B", "Room - X")
		self.watches[a] = {
			"name": a,
			"alerted_on": None,
			"snooze_kind": rules.SNOOZE_WEEK,
			"snoozed_until": NOW + dt.timedelta(days=3),
		}
		self.watches[b] = {
			"name": b,
			"alerted_on": NOW - dt.timedelta(days=8),
			"snooze_kind": rules.SNOOZE_WEEK,
			"snoozed_until": NOW - dt.timedelta(hours=1),
		}
		board.send_daily_digest()
		self.assertEqual(len(STATE["mail"]), 1)
		message = STATE["mail"][0]["message"]
		self.assertNotIn("ITEM-A name", message)
		self.assertIn("ITEM-B name", message)
		self.assertIn("Snooze ended", message)

	def test_the_table_is_four_columns(self):
		# email_style.table: four columns or fewer fit a phone.
		self.assertEqual(len(board.DIGEST_HEADERS), 4)
		rows = board.digest_rows(
			[
				{
					"item_code": "ITEM-A",
					"item_name": "A pump",
					"warehouse": "Room - X",
					"projected_qty": 1,
					"reorder_level": 5,
					"suggested_qty": 10,
					"stock_uom": "Nos",
				}
			],
			lambda code: f"/item/{code}",
		)
		self.assertEqual(rows, [[("/item/ITEM-A", "ITEM-A · A pump"), "Room - X", "1 / 5", "10 Nos"]])
		board.send_daily_digest()
		table = re.search(r"<table>(.*?)</table>", STATE["mail"][0]["message"]).group(1)
		self.assertTrue(all(len(r) == 4 for r in json.loads(table)))

	def test_recipients_follow_the_settings_role(self):
		seen = []

		def handler(q, v):
			if "Has Role" in q:
				seen.append(v["role"])
				return self.users
			return []

		STATE["sql_handler"] = handler
		STATE["single_values"][("Inventory Scanner Settings", "reorder_alert_role")] = "Stock Manager"
		self.assertEqual(board.recipients(), ["buyer@example.com"])
		STATE["single_values"][("Inventory Scanner Settings", "reorder_alert_role")] = ""
		board.recipients()
		STATE["single_values"][("Inventory Scanner Settings", "reorder_alert_role")] = RuntimeError(
			"no column yet"
		)
		board.recipients()
		self.assertEqual(seen, ["Stock Manager", "Purchase Manager", "Purchase Manager"])

	def test_a_failure_never_raises(self):
		def boom(*a, **k):
			raise RuntimeError("db gone")

		board.build_board = boom
		board.send_daily_digest()
		board.sync_watch()
		self.assertEqual(len(STATE["logged"]), 2)


class TestPatches(unittest.TestCase):
	def setUp(self):
		_reset()

	def rows(self, rows):
		STATE["sql_handler"] = lambda q, v: rows

	def test_auto_indent_goes_off(self):
		self.rows([("auto_indent", "1"), ("reorder_email_notify", "1")])
		patch_auto.execute()
		self.assertEqual(STATE["singles"], [("Stock Settings", "auto_indent", 0)])

	def test_already_off_writes_nothing(self):
		self.rows([("auto_indent", "0")])
		patch_auto.execute()
		self.assertEqual(STATE["singles"], [])

	def test_a_never_saved_stock_settings_writes_nothing(self):
		self.rows([])
		patch_auto.execute()
		self.assertEqual(STATE["singles"], [])

	def test_never_raises(self):
		def boom(q, v):
			raise RuntimeError("no table")

		STATE["sql_handler"] = boom
		patch_auto.execute()
		patch_role.execute()
		self.assertEqual(len(STATE["logged"]), 2)

	def role_meta(self):
		field = types.SimpleNamespace(fieldname="reorder_alert_role", default="Purchase Manager")
		STATE["meta"] = types.SimpleNamespace(
			get_field=lambda name: field if name == "reorder_alert_role" else None
		)

	def test_backfill_writes_only_where_there_is_no_row(self):
		self.role_meta()
		self.rows([("default_warehouse",), ("undo_window_minutes",)])
		patch_role.execute()
		self.assertEqual(
			STATE["singles"], [("Inventory Scanner Settings", "reorder_alert_role", "Purchase Manager")]
		)

	def test_backfill_never_writes_over_a_stored_value(self):
		# A row for the field, whatever it holds (a chosen role, or blank on purpose): left alone.
		self.role_meta()
		self.rows([("default_warehouse",), ("reorder_alert_role",)])
		patch_role.execute()
		self.assertEqual(STATE["singles"], [])

	def test_backfill_leaves_a_never_saved_single_alone(self):
		# One written row would stop it loading its declared defaults (CLAUDE.md, the Singles notes).
		self.role_meta()
		self.rows([])
		patch_role.execute()
		self.assertEqual(STATE["singles"], [])

	def test_the_patches_are_registered_post_model_sync(self):
		text = (APP / "patches.txt").read_text(encoding="utf-8")
		post = text.split("[post_model_sync]", 1)[1]
		for name in ("disable_auto_material_requests", "backfill_reorder_alert_role"):
			with self.subTest(patch=name):
				self.assertIn(f"erpnext_enhancements.patches.{name}\n", post)
		# Dropped on review: Frappe 16.50's model sync imports the bumped workspace file by itself.
		self.assertNotIn("reorder_board_workspace_link", text)
		self.assertFalse((APP / "patches" / "reorder_board_workspace_link.py").exists())


# --- wiring, the page and its logic ----------------------------------------------------


# --- suggested reorder levels (v1.831.0) --------------------------------------------------

from erpnext_enhancements.inventory_enhancements import reorder_suggest_rules as srules

SHOP_BIN = "Bin A1 - X"
SHOP_BIN_2 = "Bin A2 - X"
ROOM = "Room - X"
SITE = "Job Site PRJ-90001 - X"
SHELF = "Project Shelf - X"
SITES = {SITE}
NOT_SHOP = {SITE, SHELF}
START = TODAY - dt.timedelta(days=89)


def shop(w):
	return w not in NOT_SHOP


def led(warehouse, qty, after, day, voucher_type="Stock Entry", purpose=None, voucher=None, project=None):
	return {
		"warehouse": warehouse,
		"actual_qty": qty,
		"qty_after_transaction": after,
		"posting_date": day,
		"voucher_type": voucher_type,
		"voucher_no": voucher or f"V-{warehouse}-{day}-{qty}",
		"project": project,
		"purpose": purpose,
	}


D = lambda n: TODAY - dt.timedelta(days=n)  # noqa: E731


class TestWhatCountsAsUsed(unittest.TestCase):
	def usage(self, rows, scope=shop, start=START):
		return srules.item_usage(rows, scope, SITES, NOT_SHOP, start, TODAY)

	def test_takes_and_deliveries_are_use(self):
		u = self.usage(
			[
				led(SHOP_BIN, 20, 20, D(60), "Purchase Receipt"),
				led(SHOP_BIN, -3, 17, D(50), purpose="Material Issue", voucher="SE-1"),
				led(SHOP_BIN, -2, 15, D(40), purpose="Material Issue", voucher="SE-2"),
				led(SHOP_BIN, -1, 14, D(30), "Delivery Note", voucher="DN-1"),
			]
		)
		self.assertEqual((u["used"], u["uses"], u["first_seen"]), (6.0, 3, D(60)))

	def test_a_count_correction_is_never_use(self):
		# ERPNext stores a count with actual_qty 0; the change is derived, and kept beside the use.
		u = self.usage(
			[
				led(SHOP_BIN, 20, 20, D(60), "Purchase Receipt"),
				led(SHOP_BIN, 0, 12, D(20), "Stock Reconciliation", voucher="SR-1"),
				led(SHOP_BIN, 0, 14, D(10), "Stock Reconciliation", voucher="SR-2"),
			]
		)
		self.assertEqual((u["used"], u["uses"]), (0.0, 0))
		self.assertEqual((u["counted_down"], u["counted_up"]), (8.0, 2.0))

	def test_a_move_out_to_a_job_site_or_the_shelf_is_use(self):
		u = self.usage(
			[
				led(SHOP_BIN, 10, 10, D(60), "Purchase Receipt"),
				led(SHOP_BIN, -4, 6, D(30), purpose="Material Transfer", voucher="SE-T1"),
				led(SITE, 4, 4, D(30), purpose="Material Transfer", voucher="SE-T1"),
				led(SHOP_BIN, -1, 5, D(20), purpose="Material Transfer", voucher="SE-T2"),
				led(SHELF, 1, 1, D(20), purpose="Material Transfer", voucher="SE-T2"),
			]
		)
		self.assertEqual((u["used"], u["uses"]), (5.0, 2))

	def test_a_take_at_the_site_is_not_the_shops_use_again(self):
		u = self.usage(
			[
				led(SHOP_BIN, 10, 10, D(60), "Purchase Receipt"),
				led(SHOP_BIN, -4, 6, D(30), purpose="Material Transfer", voucher="SE-T1"),
				led(SITE, 4, 4, D(30), purpose="Material Transfer", voucher="SE-T1"),
				led(SITE, -4, 0, D(25), purpose="Material Issue", voucher="SE-I1"),
			]
		)
		self.assertEqual((u["used"], u["uses"]), (4.0, 1))

	def test_moves_between_shop_places_are_not_use(self):
		u = self.usage(
			[
				led(SHOP_BIN, 10, 10, D(60), "Purchase Receipt"),
				led(SHOP_BIN, -4, 6, D(30), purpose="Material Transfer", voucher="SE-T1"),
				led(SHOP_BIN_2, 4, 4, D(30), purpose="Material Transfer", voucher="SE-T1"),
			]
		)
		self.assertEqual((u["used"], u["uses"]), (0.0, 0))

	def test_returns_from_a_job_come_off(self):
		u = self.usage(
			[
				led(SHOP_BIN, 10, 10, D(60), "Purchase Receipt"),
				led(SHOP_BIN, -6, 4, D(40), purpose="Material Issue", voucher="SE-1"),
				# Stock Scan's + with a job: a return from that job.
				led(SHOP_BIN, 2, 6, D(35), purpose="Material Receipt", voucher="SE-2", project="PRJ-90001"),
				# Added without a PO and without a job: not a return.
				led(SHOP_BIN, 5, 11, D(34), purpose="Material Receipt", voucher="SE-3"),
				# Moved back from the job's site, but the shop sent nothing out to it in this period:
				# that stock was never the shop's (it reached the site from the shelf), so it takes
				# nothing off.
				led(SITE, -1, 0, D(30), purpose="Material Transfer", voucher="SE-4"),
				led(SHOP_BIN, 1, 12, D(30), purpose="Material Transfer", voucher="SE-4"),
			]
		)
		self.assertEqual((u["gross_used"], u["returned"], u["used"], u["uses"]), (6.0, 2.0, 4.0, 1))

	def test_leftovers_back_from_a_site_come_off_up_to_what_was_sent(self):
		u = self.usage(
			[
				led(SHOP_BIN, 10, 10, D(60), "Purchase Receipt"),
				led(SHOP_BIN, -4, 6, D(40), purpose="Material Transfer", voucher="SE-T1"),
				led(SITE, 4, 4, D(40), purpose="Material Transfer", voucher="SE-T1"),
				led(SITE, -1, 3, D(30), purpose="Material Transfer", voucher="SE-T2"),
				led(SHOP_BIN, 1, 7, D(30), purpose="Material Transfer", voucher="SE-T2"),
			]
		)
		self.assertEqual((u["gross_used"], u["returned"], u["used"], u["uses"]), (4.0, 1.0, 3.0, 1))

	def test_a_release_from_the_shelf_comes_off_up_to_what_the_shop_set_aside(self):
		# Shop -> shelf is use (parts set aside for a job); shelf -> shop gives it back, but never more
		# than the shop set aside: the rest was bought straight onto the shelf for a job.
		u = self.usage(
			[
				led(SHOP_BIN, 10, 10, D(60), "Purchase Receipt"),
				led(SHOP_BIN, -4, 6, D(40), purpose="Material Transfer", voucher="SE-T1"),
				led(SHELF, 4, 4, D(40), purpose="Material Transfer", voucher="SE-T1"),
				led(SHELF, -2, 2, D(30), purpose="Material Transfer", voucher="SE-T2"),
				led(SHOP_BIN, 2, 8, D(30), purpose="Material Transfer", voucher="SE-T2"),
			]
		)
		self.assertEqual((u["gross_used"], u["returned"], u["used"]), (4.0, 2.0, 2.0))
		capped = self.usage(
			[
				led(SHOP_BIN, 10, 10, D(60), "Purchase Receipt"),
				led(SHOP_BIN, -2, 8, D(40), purpose="Material Transfer", voucher="SE-T1"),
				led(SHELF, 2, 2, D(40), purpose="Material Transfer", voucher="SE-T1"),
				led(SHELF, -5, 0, D(30), purpose="Material Transfer", voucher="SE-T2"),
				led(SHOP_BIN, 5, 13, D(30), purpose="Material Transfer", voucher="SE-T2"),
				led(SHOP_BIN, -3, 10, D(20), purpose="Material Issue", voucher="SE-I1"),
			]
		)
		# 2 set aside + 3 taken = 5; the release of 5 gives back only the 2 the shop set aside.
		self.assertEqual((capped["gross_used"], capped["returned"], capped["used"]), (5.0, 2.0, 3.0))

	def test_a_release_of_parts_the_shop_never_set_aside_is_not_a_return(self):
		u = self.usage(
			[
				led(SHOP_BIN, 10, 10, D(60), "Purchase Receipt"),
				led(SHOP_BIN, -6, 4, D(40), purpose="Material Issue", voucher="SE-1"),
				led(SHELF, -2, 0, D(30), purpose="Material Transfer", voucher="SE-4"),
				led(SHOP_BIN, 2, 6, D(30), purpose="Material Transfer", voucher="SE-4"),
			]
		)
		self.assertEqual((u["used"], u["returned"]), (6.0, 0.0))

	def test_use_never_goes_below_zero(self):
		u = self.usage(
			[
				led(SHOP_BIN, 3, 3, D(40), purpose="Material Receipt", voucher="SE-2", project="PRJ-90001"),
			]
		)
		self.assertEqual(u["used"], 0.0)

	def test_outside_the_window_or_the_scope_does_not_count(self):
		rows = [
			led(SHOP_BIN, 20, 20, D(200), "Purchase Receipt"),
			led(SHOP_BIN, -5, 15, D(120), purpose="Material Issue", voucher="SE-old"),
			led(SHOP_BIN, -1, 14, D(10), purpose="Material Issue", voucher="SE-new"),
			led("Elsewhere - X", 9, 9, D(10), "Purchase Receipt"),
			led("Elsewhere - X", -9, 0, D(5), purpose="Material Issue", voucher="SE-other"),
		]
		u = self.usage(rows, scope=lambda w: w == SHOP_BIN)
		self.assertEqual((u["used"], u["uses"], u["first_seen"]), (1.0, 1, D(200)))

	def test_cancelled_rows_never_arrive(self):
		# The read leaves them out; nothing here re-adds them. A sanity check on an empty ledger.
		u = self.usage([])
		self.assertEqual((u["used"], u["uses"], u["first_seen"]), (0.0, 0, None))


class TestSuggestion(unittest.TestCase):
	def test_the_basis_starts_at_the_latest_of_window_first_take_and_first_day(self):
		window = srules.window_start(TODAY, 90)
		self.assertEqual(window, TODAY - dt.timedelta(days=89))
		since = TODAY - dt.timedelta(days=40)
		self.assertEqual(srules.basis(window, since, D(200), TODAY), (since, 41))
		self.assertEqual(srules.basis(window, since, D(20), TODAY), (D(20), 21))
		self.assertEqual(srules.basis(window, None, D(200), TODAY), (window, 90))
		self.assertEqual(srules.basis(window, since, None, TODAY), (None, 0))

	def test_the_arithmetic(self):
		# 30 used in 60 days: 0.5 a day. Lead time 10: reorder at 0.5 x (10 + 7) = 8.5 -> 9; buy 15.
		s = srules.suggest(30, 6, 60, 10, whole=True)
		self.assertTrue(s["enough"])
		self.assertEqual(
			(s["level"], s["qty"], s["daily"], s["lead_days"], s["lead_assumed"]), (9.0, 15.0, 0.5, 10, False)
		)
		# A fractional unit rounds up to the hundredth.
		f = srules.suggest(10, 4, 30, 10, whole=False)
		self.assertEqual((f["level"], f["qty"]), (5.67, 10.0))

	def test_no_lead_time_is_assumed_and_said(self):
		s = srules.suggest(14, 5, 14, 0, whole=True)
		self.assertEqual((s["lead_days"], s["lead_assumed"]), (srules.DEFAULT_LEAD_DAYS, True))
		self.assertEqual(s["level"], float((srules.DEFAULT_LEAD_DAYS + srules.SAFETY_DAYS) * 1))
		self.assertEqual(s["qty"], 30.0)

	def test_buy_is_never_below_one_unit(self):
		s = srules.suggest(3, 3, 180, 0, whole=True)
		self.assertTrue(s["enough"])
		self.assertEqual(s["qty"], 1.0)
		self.assertGreaterEqual(s["level"], 1.0)

	def test_thin_history_has_no_suggestion_and_says_its_one_cause(self):
		cases = [
			# (used, uses, days) -> the one reason
			((0, 0, 0), "Not in the shop during this window."),
			((0, 0, 60), "Not used in these 60 days."),
			((0, 4, 30), "Used 4 times, but as much came back as went out: nothing used on balance."),
			((5, 2, 60), "Used 2 times in 60 days: a suggestion needs at least 3 uses."),
			((1, 1, 1), "Used 1 time in 1 day: a suggestion needs at least 3 uses."),
			((5, 3, 10), "Only 10 days of history: a suggestion needs at least 14 days."),
			((5, 3, 1), "Only 1 day of history: a suggestion needs at least 14 days."),
		]
		for (used, uses, days), reason in cases:
			with self.subTest(used=used, uses=uses, days=days):
				s = srules.suggest(used, uses, days, 7, whole=True)
				self.assertFalse(s["enough"])
				self.assertIsNone(s["level"])
				self.assertIsNone(s["qty"])
				self.assertEqual(s["reason"], reason)
		self.assertIsNone(srules.suggest(30, 6, 60, 7, whole=True)["reason"])

	def test_the_uses_and_the_days_are_the_same_period(self):
		# Takes 60 and 50 days ago are inside the 90-day window but before the first recorded take on
		# the site (40 days ago, say a test entry): averaged over 41 days they must not be counted.
		rows = [
			led(SHOP_BIN, 20, 20, D(80), "Purchase Receipt"),
			led(SHOP_BIN, -5, 15, D(60), purpose="Material Issue", voucher="SE-a"),
			led(SHOP_BIN, -5, 10, D(50), purpose="Material Issue", voucher="SE-b"),
			led(SHOP_BIN, -1, 9, D(30), purpose="Material Issue", voucher="SE-c"),
			led(SHOP_BIN, 0, 7, D(20), "Stock Reconciliation", voucher="SR-1"),
		]
		m = srules.measure(rows, shop, SITES, NOT_SHOP, srules.window_start(TODAY, 90), D(40), TODAY)
		self.assertEqual((m["basis_start"], m["basis_days"], m["first_seen"]), (D(40), 41, D(80)))
		self.assertEqual((m["used"], m["uses"]), (1.0, 1))
		# Counts are over the same days, and still derived from the whole ledger's balance.
		self.assertEqual(m["counted_down"], 2.0)
		never = srules.measure([], shop, SITES, NOT_SHOP, START, D(40), TODAY)
		self.assertEqual(
			(never["basis_start"], never["basis_days"], never["used"], never["uses"]), (None, 0, 0.0, 0)
		)

	def test_direction(self):
		self.assertEqual(srules.direction(5, 9), "higher")
		self.assertEqual(srules.direction(5, 2), "lower")
		self.assertEqual(srules.direction(5, 5.0), "same")
		self.assertIsNone(srules.direction(5, None))

	def test_the_window(self):
		self.assertEqual(srules.clean_window("60"), 60)
		for odd in (None, "", "45", "abc", 0, -30, 365):
			self.assertEqual(srules.clean_window(odd), srules.DEFAULT_WINDOW)

	def test_the_numbers_a_person_may_approve(self):
		self.assertEqual(srules.clean_rule("0", "5", True), (0.0, 5.0, None))
		self.assertEqual(srules.clean_rule("1,200", "2.5", False), (1200.0, 2.5, None))
		for level, qty in (
			("", "5"),
			("3", ""),
			("-1", "5"),
			("3", "0"),
			("3", "x"),
			("100001", "5"),
			("nan", "5"),
		):
			with self.subTest(level=level, qty=qty):
				self.assertIsNotNone(srules.clean_rule(level, qty, False)[2])
		self.assertIn("whole numbers", srules.clean_rule("2.5", "5", True)[2])

	def test_same_stamp(self):
		stamp = dt.datetime(2026, 11, 19, 10, 0, 0, 123456)
		self.assertTrue(srules.same_stamp(stamp, "2026-11-19 10:00:00.123456"))
		self.assertFalse(srules.same_stamp(stamp, "2026-11-19 10:00:00"))
		self.assertFalse(srules.same_stamp(stamp, None))
		self.assertFalse(srules.same_stamp(None, None))

	def test_apply_roles(self):
		self.assertEqual(set(srules.APPLY_ROLES), {"Purchase Manager", "Stock Manager", "System Manager"})
		self.assertNotIn("Purchase User", srules.APPLY_ROLES)
		self.assertEqual(srules.MAX_QTY, rules.MAX_QTY)


class _SuggestCase(unittest.TestCase):
	"""``reorder_suggestions`` over the board stub: the board's reads replaced, the ledger by SQL."""

	RULES = [
		dict(rule("ITEM-A", warehouse=ROOM, group="All - X", level=2, qty=5), lead_time_days=0),
		dict(rule("ITEM-B", warehouse=SHOP_BIN, group=ROOM, level=4, qty=4), lead_time_days=0),
	]

	def setUp(self):
		_reset(roles=["Stock Manager"])
		self._saved = {
			"read_rules": board.read_rules,
			"read_ancestors": board.read_ancestors,
			"_item_details": board._item_details,
			"_not_shop": suggestions._not_shop,
		}
		board.read_rules = lambda: ([dict(r) for r in self.RULES], {})
		board.read_ancestors = lambda: rules.ancestor_map(
			{
				SHOP_BIN: ROOM,
				SHOP_BIN_2: ROOM,
				ROOM: "All - X",
				SITE: "Sites - X",
				"Sites - X": "All - X",
				SHELF: "All - X",
			}
		)
		board._item_details = lambda codes, include_rates: (
			{c: {"item_name": f"Name of {c}", "stock_uom": "Nos", "stock_uom_whole": True} for c in codes},
			{},
		)
		suggestions._not_shop = lambda: (set(SITES), set(NOT_SHOP))
		self.ledger = {
			"ITEM-A": [
				led(SHOP_BIN, 40, 40, D(80), "Purchase Receipt"),
				led(SHOP_BIN, -4, 36, D(30), purpose="Material Issue", voucher="SE-1"),
				led(SHOP_BIN_2, -2, 34, D(20), purpose="Material Issue", voucher="SE-2"),
				led(SHOP_BIN, -6, 28, D(10), purpose="Material Issue", voucher="SE-3"),
				led(SHOP_BIN, 0, 25, D(5), "Stock Reconciliation", voucher="SR-1"),
			],
			"ITEM-B": [
				led(SHOP_BIN, 10, 10, D(80), "Purchase Receipt"),
				led(SHOP_BIN, -1, 9, D(30), purpose="Material Issue", voucher="SE-4"),
			],
			"ITEM-C": [
				led(SHOP_BIN, 5, 5, D(80), "Purchase Receipt"),
				led(SHOP_BIN, -2, 3, D(15), purpose="Material Issue", voucher="SE-5"),
			],
		}
		self.since = D(55)
		STATE["sql_handler"] = self.sql
		STATE["docs"][("Item", "ITEM-A")] = {
			"doctype": "Item",
			"name": "ITEM-A",
			"stock_uom": "Nos",
			"modified": dt.datetime(2026, 10, 1, 9, 0, 0),
			"reorder_levels": [
				_Doc(
					warehouse="Elsewhere - X",
					material_request_type="Purchase",
					warehouse_reorder_level=1,
					warehouse_reorder_qty=1,
				),
				_Doc(
					warehouse=ROOM,
					material_request_type="Purchase",
					warehouse_reorder_level=2,
					warehouse_reorder_qty=5,
				),
			],
		}

	def tearDown(self):
		board.read_rules = self._saved["read_rules"]
		board.read_ancestors = self._saved["read_ancestors"]
		board._item_details = self._saved["_item_details"]
		suggestions._not_shop = self._saved["_not_shop"]

	def sql(self, query, values):
		q = " ".join(query.split())
		if "min(sle.posting_date)" in q:
			return [[self.since]]
		if "select distinct sle.item_code" in q:
			return [["ITEM-A"], ["ITEM-B"], ["ITEM-C"]]
		if "from `tabStock Ledger Entry` sle left join" in q and "order by sle.posting_datetime" in q:
			out = []
			for code in values["codes"]:
				for row in self.ledger.get(code, []):
					out.append(dict(row, item_code=code))
			return out
		if "from `tabItem Reorder`" in q:
			own = getattr(self, "own_rules", None)
			if own is None:
				own = {(r["item_code"], r["warehouse"]) for r in self.RULES}
			return [[c, w] for c, w in sorted(own) if c in values["codes"]]
		if "select name, modified, lead_time_days, variant_of from `tabItem`" in q:
			variants = getattr(self, "variants", {})
			return [
				{
					"name": c,
					"modified": dt.datetime(2026, 10, 1, 9, 0, 0),
					"lead_time_days": 0,
					"variant_of": variants.get(c),
				}
				for c in values["codes"]
			]
		if "from `tabUOM`" in q:
			return [{"must_be_whole_number": 1}]
		return []


class TestSuggestionsRead(_SuggestCase):
	def test_each_rule_with_its_suggestion_and_basis(self):
		data = suggestions.build(days=90, today=TODAY)
		self.assertEqual((data["window"], data["tracking_since"]), (90, self.since))
		by_code = {l["item_code"]: l for l in data["lines"]}
		a = by_code["ITEM-A"]
		# Three takes, 12 used, from the first recorded take (55 days ago): 56 days.
		self.assertEqual((a["used"], a["uses"], a["basis_days"], a["basis_start"]), (12.0, 3, 56, self.since))
		# The count found 3 fewer: shown, not counted.
		self.assertEqual(a["counted_down"], 3.0)
		s = a["suggestion"]
		self.assertTrue(s["enough"])
		# 12 / 56 a day x (7 assumed + 7) = 3 -> 3; x 30 = 6.43 -> 7.
		self.assertEqual((s["level"], s["qty"], s["lead_assumed"]), (3.0, 7.0, True))
		self.assertEqual(
			(a["reorder_level"], a["reorder_qty"], a["direction"], a["matches"]), (2, 5, "higher", False)
		)
		self.assertEqual(a["key"], "ITEM-A::" + ROOM)
		self.assertEqual(a["modified"], "2026-10-01 09:00:00")
		# One take: no suggestion, listed after the ready ones.
		b = by_code["ITEM-B"]
		self.assertFalse(b["suggestion"]["enough"])
		self.assertIsNone(b["direction"])
		self.assertEqual([l["item_code"] for l in data["lines"]], ["ITEM-A", "ITEM-B"])

	def test_the_rule_scope_decides_what_counts(self):
		# ITEM-B's rule watches its bin and its room. A take in another room is not its use.
		self.ledger["ITEM-B"].append(
			led("Other Room Bin - X", -5, 0, D(8), purpose="Material Issue", voucher="SE-9")
		)
		b = next(l for l in suggestions.build(days=90, today=TODAY)["lines"] if l["item_code"] == "ITEM-B")
		self.assertEqual((b["used"], b["uses"]), (1.0, 1))

	def test_a_variants_rule_from_its_template_is_marked_and_not_applied_here(self):
		self.own_rules = {("ITEM-B", SHOP_BIN)}
		self.variants = {"ITEM-A": "TEMPLATE-A"}
		by_code = {l["item_code"]: l for l in suggestions.build(days=90, today=TODAY)["lines"]}
		self.assertEqual(by_code["ITEM-A"]["inherited_from"], "TEMPLATE-A")
		self.assertIsNone(by_code["ITEM-B"]["inherited_from"])

	def test_used_items_without_a_rule(self):
		data = suggestions.build(days=90, today=TODAY)
		self.assertEqual([u["item_code"] for u in data["unruled"]], ["ITEM-C"])
		c = data["unruled"][0]
		self.assertNotIn("warehouse", c)
		self.assertEqual((c["used"], c["uses"]), (2.0, 1))

	def test_the_window_choice(self):
		data = suggestions.build(days="30", today=TODAY)
		a = next(l for l in data["lines"] if l["item_code"] == "ITEM-A")
		self.assertEqual((data["window"], a["basis_days"], a["uses"]), (30, 30, 2))

	def test_the_ledger_read_is_bound_and_per_item(self):
		suggestions.build(days=90, today=TODAY)
		reads = [(q, v) for q, v in STATE["sql"] if "order by sle.posting_datetime" in q]
		self.assertEqual(len(reads), 1)
		query, values = reads[0]
		self.assertIn("sle.is_cancelled = 0", query)
		self.assertIn("sle.item_code in %(codes)s", query)
		self.assertEqual(values["codes"], ("ITEM-A", "ITEM-B", "ITEM-C"))

	def test_the_page_read_says_who_may_apply(self):
		data = api.get_reorder_suggestions(days=60)
		self.assertTrue(data["can_apply"])
		self.assertEqual(data["window"], 60)
		self.assertEqual(data["window_choices"], list(srules.WINDOW_CHOICES))
		STATE["perm"][("Item", "write")] = False
		self.assertFalse(api.get_reorder_suggestions()["can_apply"])
		STATE["perm"].pop(("Item", "write"))
		STATE["roles"] = ["Purchase User"]
		self.assertFalse(api.get_reorder_suggestions()["can_apply"])

	def test_the_read_needs_a_board_role(self):
		STATE["roles"] = ["Stock User"]
		with self.assertRaises(sys.modules["frappe"].PermissionError):
			api.get_reorder_suggestions()


class TestApplySuggestion(_SuggestCase):
	STAMP = "2026-10-01 09:00:00"

	def apply(self, **kw):
		args = dict(item_code="ITEM-A", warehouse=ROOM, modified=self.STAMP, level="3", qty="7")
		args.update(kw)
		return api.apply_reorder_suggestion(**args)

	def test_saves_the_rule_as_the_person(self):
		r = self.apply()
		self.assertTrue(r["ok"])
		self.assertTrue(r["saved"])
		self.assertEqual((r["reorder_level"], r["reorder_qty"]), (3.0, 7.0))
		self.assertEqual(len(STATE["saves"]), 1)
		doctype, doc, ignore_permissions = STATE["saves"][0]
		self.assertEqual(doctype, "Item")
		self.assertFalse(ignore_permissions)
		rows = {row["warehouse"]: row for row in doc["reorder_levels"]}
		self.assertEqual(
			(rows[ROOM]["warehouse_reorder_level"], rows[ROOM]["warehouse_reorder_qty"]), (3.0, 7.0)
		)
		# Only the rule asked for.
		self.assertEqual(rows["Elsewhere - X"]["warehouse_reorder_level"], 1)

	def test_the_same_numbers_save_nothing(self):
		r = self.apply(level="2", qty="5")
		self.assertTrue(r["ok"])
		self.assertFalse(r["saved"])
		self.assertEqual(STATE["saves"], [])

	def test_a_stale_page_writes_nothing(self):
		r = self.apply(modified="2026-09-30 09:00:00")
		self.assertFalse(r["ok"])
		self.assertIn("changed by someone else", r["message"])
		self.assertEqual(STATE["saves"], [])

	def test_a_variant_is_told_where_its_rule_is(self):
		STATE["docs"][("Item", "VAR-A")] = {
			"doctype": "Item",
			"name": "VAR-A",
			"stock_uom": "Nos",
			"variant_of": "TEMPLATE-A",
			"modified": dt.datetime(2026, 10, 1, 9, 0, 0),
			"reorder_levels": [],
		}
		r = self.apply(item_code="VAR-A")
		self.assertFalse(r["ok"])
		self.assertEqual(
			r["message"],
			"VAR-A takes its reorder rule from its template, TEMPLATE-A. Change the rule on TEMPLATE-A; it "
			"applies to every variant of it.",
		)
		self.assertEqual(STATE["saves"], [])

	def test_a_rule_that_is_gone(self):
		r = self.apply(warehouse=SHOP_BIN)
		self.assertFalse(r["ok"])
		self.assertIn("no reorder rule", r["message"])
		self.assertEqual(STATE["saves"], [])

	def test_bad_numbers(self):
		for level, qty in (("-1", "7"), ("3", "0"), ("3", ""), ("2.5", "7")):
			with self.subTest(level=level, qty=qty):
				r = self.apply(level=level, qty=qty)
				self.assertFalse(r["ok"])
		self.assertEqual(STATE["saves"], [])

	def test_an_unknown_item(self):
		r = self.apply(item_code="NOPE")
		self.assertFalse(r["ok"])
		self.assertEqual(STATE["saves"], [])

	def test_only_the_apply_roles(self):
		for roles in (["Purchase User"], ["Stock User"], []):
			with self.subTest(roles=roles):
				STATE["roles"] = roles
				with self.assertRaises(sys.modules["frappe"].PermissionError):
					self.apply()
		for role in srules.APPLY_ROLES:
			with self.subTest(role=role):
				_reset(roles=[role], docs=dict(STATE["docs"]), sql_handler=self.sql)
				self.assertTrue(self.apply()["ok"])

	def test_the_items_own_write_permission(self):
		STATE["perm"][("Item", "write")] = False
		with self.assertRaises(sys.modules["frappe"].PermissionError):
			self.apply()
		self.assertEqual(STATE["saves"], [])

	def test_post_only(self):
		src = (APP / "api/reorder_board.py").read_text(encoding="utf-8")
		self.assertIn('@frappe.whitelist(methods=["POST"])\ndef apply_reorder_suggestion(', src)
		self.assertIn('@frappe.whitelist(methods=["GET", "POST"])\ndef get_reorder_suggestions(', src)

	def test_no_sql_writes(self):
		src = (APP / "inventory_enhancements/reorder_suggestions.py").read_text(encoding="utf-8").lower()
		for verb in ("insert into", "update `", "delete from", "ignore_permissions"):
			self.assertNotIn(verb, src)


def _strip_js_comments(src):
	return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", src, flags=re.S))


class TestWiring(unittest.TestCase):
	def test_page_roles_are_the_board_roles(self):
		page = json.loads((PAGE_DIR / "reorder_board.json").read_text(encoding="utf-8"))
		self.assertEqual(page["name"], "reorder-board")
		self.assertEqual(page["module"], "Inventory Enhancements")
		self.assertEqual({r["role"] for r in page["roles"]}, set(board.BOARD_ROLES))
		self.assertNotIn("Stock User", {r["role"] for r in page["roles"]})

	def test_no_workspace_shares_the_slug(self):
		for ws in APP.glob("*/workspace/*/*.json"):
			name = json.loads(ws.read_text(encoding="utf-8")).get("name") or ""
			self.assertNotEqual(name.lower().replace(" ", "-"), "reorder-board", ws)

	def test_the_workspace_links_the_page(self):
		ws = json.loads(
			(
				APP / "inventory_enhancements/workspace/inventory_enhancements/inventory_enhancements.json"
			).read_text(encoding="utf-8")
		)
		links = [l for l in ws["links"] if l.get("link_to") == "reorder-board"]
		self.assertEqual(len(links), 1)
		self.assertEqual(links[0]["link_type"], "Page")
		self.assertGreaterEqual(ws["modified"], "2026-10-22 18:00:00")

	def test_scheduled(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn(
			'"20 7 * * *": ["erpnext_enhancements.inventory_enhancements.reorder_board.send_daily_digest"]',
			hooks,
		)
		self.assertEqual(hooks.count('"20 7 * * *"'), 1, "a repeated cron key silently replaces the other")
		self.assertIn('"erpnext_enhancements.inventory_enhancements.reorder_board.sync_watch",', hooks)

	def test_no_sql_function_strings_in_get_all(self):
		for path in (APP / "inventory_enhancements/reorder_board.py", APP / "api/reorder_board.py"):
			src = path.read_text(encoding="utf-8")
			self.assertNotRegex(src, r'fields=\[[^\]]*"\w+\(', path.name)

	def test_no_sql_writes(self):
		src = (APP / "inventory_enhancements/reorder_board.py").read_text(encoding="utf-8").lower()
		for verb in ("insert into", "update `", "delete from"):
			self.assertNotIn(verb, src)
		self.assertNotIn(".submit(", src)

	def test_the_watch_controller_name(self):
		src = (APP / "inventory_enhancements/doctype/reorder_watch/reorder_watch.py").read_text(
			encoding="utf-8"
		)
		self.assertIn("class ReorderWatch(Document):", src)
		meta = json.loads(
			(APP / "inventory_enhancements/doctype/reorder_watch/reorder_watch.json").read_text(
				encoding="utf-8"
			)
		)
		types_by_field = {f["fieldname"]: f["fieldtype"] for f in meta["fields"]}
		self.assertEqual(
			types_by_field["last_request"], "Data", "a Link would stop a buyer deleting the Draft"
		)

	def test_watch_name(self):
		from erpnext_enhancements.inventory_enhancements.doctype.reorder_watch.reorder_watch import watch_name

		self.assertEqual(watch_name("A", "W"), "A::W")
		long = watch_name("A" * 100, "W" * 100)
		self.assertTrue(long.startswith("RW-") and len(long) <= 140)
		self.assertEqual(long, watch_name("A" * 100, "W" * 100))

	def test_tabs_are_history_entries_and_dialogs_are_not(self):
		js = _strip_js_comments((PAGE_DIR / "reorder_board.js").read_text(encoding="utf-8"))
		self.assertIn("frappe.set_route(...ROB.route_for(view))", js)
		self.assertNotIn("pushState", js)
		self.assertNotIn("replaceState", js)
		self.assertNotIn("location.search", js)
		self.assertIn('frappe.pages["reorder-board"].on_page_show', js)
		# Every selector in the page's stylesheet is namespaced.
		css = re.sub(
			r"/\*.*?\*/", "", (PAGE_DIR / "reorder_board.css").read_text(encoding="utf-8"), flags=re.S
		)
		for selector in re.findall(r"([^{}]+)\{", css):
			for part in selector.split(","):
				with self.subTest(selector=part.strip()):
					self.assertTrue(part.strip().startswith(".rob-"), part)


NODE_CHECK = r"""
const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[process.argv.length - 1], "utf8");
const frappe = { pages: { "reorder-board": {} } };
const ctx = { frappe, __: (s, args) => (args || []).reduce((t, a, i) => t.replace(`{${i}}`, a), s), window: {}, console };
vm.createContext(ctx);
vm.runInContext(src, ctx);
const R = frappe.pages["reorder-board"].logic;
const out = {
	views: [R.view_from_route(["reorder-board"]), R.view_from_route(["reorder-board", "on-order"]), R.view_from_route(["reorder-board", "snoozed"]), R.view_from_route(["reorder-board", "junk"]), R.view_from_route(["reorder-board", "levels"])],
	routes: [R.route_for("needs"), R.route_for("on-order"), R.route_for("snoozed"), R.route_for("levels")],
	basis: [R.basis_text({ used: 12, uses: 3, basis_days: 40, stock_uom: "Nos" }), R.basis_text({ used: 1, uses: 1, basis_days: 1 }), R.basis_text({ basis_days: 0 })],
	lead: [R.lead_text({ lead_days: 7, lead_assumed: true }), R.lead_text({ lead_days: 1, lead_assumed: false })],
	counts: [R.counts_text({ counted_down: 3 }), R.counts_text({ counted_down: 2, counted_up: 1 }), R.counts_text({})],
	split: (() => {
		const r = R.split_levels([{ key: "a", suggestion: { enough: true } }, { key: "b", suggestion: { enough: false } }, { key: "c" }]);
		return [r.ready.map((l) => l.key), r.thin.map((l) => l.key)];
	})(),
	direction: [R.direction_label("higher"), R.direction_label("lower"), R.direction_label("same"), R.direction_label(null)],
	qty: [R.clean_qty("12", 100000, true), R.clean_qty("2.5", 100000, true), R.clean_qty("0", 100000, false), R.clean_qty("1,200", 100000, false), R.clean_qty("100001", 100000, false), R.clean_qty("", 100000, false)].map((r) => [r.qty, !!r.error]),
	order: [R.order_qty(10, 4, true), R.order_qty(10, 4, false)],
	picked: (() => {
		const lines = [{ key: "a", suggested_qty: 10, stock_uom_whole: true }, { key: "b", suggested_qty: 5, stock_uom_whole: true }, { key: "c", suggested_qty: 3 }];
		const r = R.picked(lines, new Set(["a", "b"]), new Map([["b", "2.5"]]), 100000);
		return { out: r.out.map((x) => [x.line.key, x.qty]), errors: r.errors.map((x) => x.line.key) };
	})(),
	days: [R.days_below("2026-10-22 06:00:00", new Date("2026-10-22T08:00:00")), R.days_below("2026-10-19 06:00:00", new Date("2026-10-22T08:00:00")), R.days_below(null, new Date())],
	ref: R.new_ref().length >= 8,
};
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestPageLogicUnderNode(unittest.TestCase):
	def test_the_page_logic(self):
		result = subprocess.run(
			["node", "-e", NODE_CHECK, str(PAGE_DIR / "reorder_board.js")],
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
		self.assertEqual(result.returncode, 0, result.stderr)
		out = json.loads(result.stdout)
		self.assertEqual(out["views"], ["needs", "on-order", "snoozed", "needs", "levels"])
		self.assertEqual(
			out["routes"],
			[
				["reorder-board"],
				["reorder-board", "on-order"],
				["reorder-board", "snoozed"],
				["reorder-board", "levels"],
			],
		)
		self.assertEqual(
			out["basis"],
			["Used 12 Nos in 40 days (3 uses)", "Used 1 in 1 day (1 use)", ""],
		)
		self.assertEqual(out["lead"], ["lead time 7 days (assumed: none set on the item)", "lead time 1 day"])
		self.assertEqual(
			out["counts"],
			[
				"counts found 3 fewer (not counted as use)",
				"counts found 2 fewer, counts found 1 more (not counted as use)",
				"",
			],
		)
		self.assertEqual(out["split"], [["a"], ["b", "c"]])
		self.assertEqual(out["direction"], ["raise", "lower", "same", ""])
		self.assertEqual(
			out["qty"], [[12, False], [None, True], [None, True], [1200, False], [None, True], [None, True]]
		)
		self.assertEqual(out["order"], [3, 2.5])
		self.assertEqual(out["picked"], {"out": [["a", 10]], "errors": ["b"]})
		self.assertEqual(out["days"], ["today", "3 days", ""])
		self.assertTrue(out["ref"])


if __name__ == "__main__":
	unittest.main()
