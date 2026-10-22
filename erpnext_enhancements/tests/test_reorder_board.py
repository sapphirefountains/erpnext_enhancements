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

Each part installs its own ``frappe`` stub. Run:
python -m unittest erpnext_enhancements.tests.test_reorder_board -v
"""

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


def mr_line(name="MRI-1", parent="MR-1", docstatus=1, qty=10, item="ITEM-A", warehouse="Room - X"):
	return {
		"name": name,
		"parent": parent,
		"item_code": item,
		"warehouse": warehouse,
		"stock_qty": qty,
		"schedule_date": TODAY,
		"docstatus": docstatus,
		"transaction_date": TODAY,
	}


def po_line(name="POI-1", parent="PO-1", docstatus=1, qty=10, mri="MRI-1", stage=None, item="ITEM-A"):
	return {
		"name": name,
		"parent": parent,
		"item_code": item,
		"warehouse": "Room - X",
		"stock_qty": qty,
		"schedule_date": TODAY + dt.timedelta(days=5),
		"material_request_item": mri,
		"docstatus": docstatus,
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

	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError
	frappe.DuplicateEntryError = DuplicateEntryError
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
	utils.now_datetime = lambda: NOW
	utils.nowdate = lambda: TODAY.isoformat()
	utils.getdate = lambda v=None: dt.date.fromisoformat(v) if isinstance(v, str) else (v or TODAY)
	utils.add_days = lambda d, n: d + dt.timedelta(days=n)
	utils.get_url = lambda path="": "https://erp.example.com" + (path or "")
	utils.get_url_to_form = lambda doctype, name: f"https://erp.example.com/desk/{doctype}/{name}"
	utils.format_date = lambda v=None: str(v)
	frappe.utils = utils

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
		"erpnext_enhancements.email_style": email_style,
	}.items():
		sys.modules[name] = module


_SAVED = {}
board = None
patch_auto = None
patch_role = None


def setUpModule():
	global board, patch_auto, patch_role
	for name in (
		"frappe",
		"frappe.utils",
		"frappe.model",
		"frappe.model.document",
		"erpnext_enhancements.email_style",
	):
		_SAVED[name] = sys.modules.get(name)
	_install_stubs()
	for name in (
		"erpnext_enhancements.inventory_enhancements.doctype.reorder_watch.reorder_watch",
		"erpnext_enhancements.inventory_enhancements.reorder_board",
		"erpnext_enhancements.patches.disable_auto_material_requests",
		"erpnext_enhancements.patches.backfill_reorder_alert_role",
	):
		sys.modules.pop(name, None)
	board = importlib.import_module("erpnext_enhancements.inventory_enhancements.reorder_board")
	patch_auto = importlib.import_module("erpnext_enhancements.patches.disable_auto_material_requests")
	patch_role = importlib.import_module("erpnext_enhancements.patches.backfill_reorder_alert_role")


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
		out = rules.assemble(rows, self.PROJECTED, tracked, self.watches, now or NOW)
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
		self.assertNotIn('"ITEM-A"', message)
		self.assertIn('"ITEM-B"', message)
		self.assertIn("Snooze ended", message)

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

	def test_backfill_only_where_no_row(self):
		field = types.SimpleNamespace(fieldname="reorder_alert_role", default="Purchase Manager")
		STATE["meta"] = types.SimpleNamespace(
			get_field=lambda name: field if name == "reorder_alert_role" else None
		)
		self.rows([("default_warehouse",)])
		patch_role.execute()
		self.assertEqual(
			STATE["singles"], [("Inventory Scanner Settings", "reorder_alert_role", "Purchase Manager")]
		)
		STATE["singles"].clear()
		self.rows([("reorder_alert_role",)])
		patch_role.execute()
		self.assertEqual(STATE["singles"], [], "a stored value is never overwritten")
		self.rows([])
		patch_role.execute()
		self.assertEqual(STATE["singles"], [], "a never-saved Single is left alone")

	def test_the_patches_are_registered_post_model_sync(self):
		text = (APP / "patches.txt").read_text(encoding="utf-8")
		post = text.split("[post_model_sync]", 1)[1]
		for name in (
			"disable_auto_material_requests",
			"backfill_reorder_alert_role",
			"reorder_board_workspace_link",
		):
			with self.subTest(patch=name):
				self.assertIn(f"erpnext_enhancements.patches.{name}\n", post)


# --- wiring, the page and its logic ----------------------------------------------------


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
	views: [R.view_from_route(["reorder-board"]), R.view_from_route(["reorder-board", "on-order"]), R.view_from_route(["reorder-board", "snoozed"]), R.view_from_route(["reorder-board", "junk"])],
	routes: [R.route_for("needs"), R.route_for("on-order"), R.route_for("snoozed")],
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
		self.assertEqual(out["views"], ["needs", "on-order", "snoozed", "needs"])
		self.assertEqual(
			out["routes"], [["reorder-board"], ["reorder-board", "on-order"], ["reorder-board", "snoozed"]]
		)
		self.assertEqual(
			out["qty"], [[12, False], [None, True], [None, True], [1200, False], [None, True], [None, True]]
		)
		self.assertEqual(out["order"], [3, 2.5])
		self.assertEqual(out["picked"], {"out": [["a", 10]], "errors": ["b"]})
		self.assertEqual(out["days"], ["today", "3 days", ""])
		self.assertTrue(out["ref"])


if __name__ == "__main__":
	unittest.main()
