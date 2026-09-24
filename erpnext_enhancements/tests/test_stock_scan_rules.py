"""Bench-free tests for the Stock Scan page's rules and its QR label drawing.

``inventory_enhancements/stock_scan_rules.py`` and ``inventory_enhancements/qr_svg.py``
import no ``frappe``, and ``inventory_enhancements/__init__.py`` is empty, so this runs
with the stdlib alone. The one test that exercises the real encoder skips when
``pyqrcode`` is absent; CI installs it on this step so that test runs there.

Run: python -m unittest erpnext_enhancements.tests.test_stock_scan_rules -v

The ones that matter most:

* :meth:`TestParseScan.test_label_from_another_site_still_resolves` — a label is a URL,
  and only its path and query identify it. A label printed on the test site has to work
  on production.
* :meth:`TestQuantities.test_take_refusal_explains_put_away` — on the day this shipped
  every bin on production was empty and all stock sat in ``Stores - SF``. A technician
  holding a part the system places elsewhere is the *normal* first scan, and the refusal
  has to tell them what to do rather than quote a ledger error.
* :meth:`TestUndo.test_owner_window_is_inclusive_and_supervisor_is_unbounded`.
"""

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.inventory_enhancements import qr_svg
from erpnext_enhancements.inventory_enhancements import stock_scan_rules as rules

try:
	import pyqrcode

	HAVE_PYQRCODE = True
except ImportError:
	HAVE_PYQRCODE = False


class TestParseScan(unittest.TestCase):
	def test_blank(self):
		self.assertEqual(rules.parse_scan(""), ("", ""))
		self.assertEqual(rules.parse_scan("   "), ("", ""))
		self.assertEqual(rules.parse_scan(None), ("", ""))

	def test_warehouse_label(self):
		url = "https://erp.sapphirefountains.com/stock-scan?w=Bin%20B1-2-10%20-%20SF"
		self.assertEqual(rules.parse_scan(url), ("warehouse", "Bin B1-2-10 - SF"))

	def test_plus_decodes_as_space(self):
		url = "https://erp.sapphirefountains.com/stock-scan?w=Bin+B1-2-10+-+SF"
		self.assertEqual(rules.parse_scan(url), ("warehouse", "Bin B1-2-10 - SF"))

	def test_label_from_another_site_still_resolves(self):
		url = "https://beta.erp.sapphirefountains.com/stock-scan?w=Stores%20-%20SF"
		self.assertEqual(rules.parse_scan(url), ("warehouse", "Stores - SF"))

	def test_relative_and_trailing_slash(self):
		self.assertEqual(rules.parse_scan("/stock-scan/?w=Stores%20-%20SF"), ("warehouse", "Stores - SF"))

	def test_item_and_storage_location(self):
		self.assertEqual(rules.parse_scan("https://x.test/stock-scan?item=PDT-0008"), ("item", "PDT-0008"))
		self.assertEqual(
			rules.parse_scan("https://x.test/stock-scan?loc=MAIN-A-03-B"), ("storage_location", "MAIN-A-03-B")
		)

	def test_warehouse_wins_over_item(self):
		self.assertEqual(
			rules.parse_scan("https://x.test/stock-scan?item=PDT-1&w=Stores%20-%20SF"),
			("warehouse", "Stores - SF"),
		)

	def test_other_urls_are_text_not_guesses(self):
		url = "https://example.com/products?w=Stores"
		self.assertEqual(rules.parse_scan(url), ("text", url))
		empty = "https://x.test/stock-scan?w="
		self.assertEqual(rules.parse_scan(empty), ("text", empty))

	def test_bare_codes_are_text(self):
		self.assertEqual(rules.parse_scan(" 012345678905 "), ("text", "012345678905"))
		self.assertEqual(rules.parse_scan("Bin A1-3-1 - SF"), ("text", "Bin A1-3-1 - SF"))

	def test_login_redirect_brings_the_whole_query_back(self):
		"""The login page splits its own query on '&', so an unencoded path lost every
		parameter after the first (the labels page's size and skip, a second ?w=)."""
		from urllib.parse import parse_qs, urlsplit

		for path in (
			"/warehouse-labels?under=Row%201%20-%20SF&size=avery-5163&skip=3",
			"/warehouse-labels?w=A%20-%20SF&w=B%20-%20SF",
			"/stock-scan?w=Bin%20B1-2-10%20-%20SF",
		):
			url = rules.login_redirect(path)
			self.assertTrue(url.startswith("/login?redirect-to=/"), url)
			self.assertEqual(parse_qs(urlsplit(url).query)["redirect-to"], [path])
		self.assertEqual(rules.login_redirect(None), "/login?redirect-to=/stock-scan")

	def test_scan_url_round_trips(self):
		for name in ("Bin B1-2-10 - SF", "Bin D2-2-1 (Boneyard) - SF", "Stores - SF", "A&B/C?d=e"):
			url = rules.scan_url("https://erp.sapphirefountains.com/", name)
			self.assertTrue(url.startswith("https://erp.sapphirefountains.com/stock-scan?w="), url)
			self.assertNotIn(" ", url)
			self.assertEqual(rules.parse_scan(url), ("warehouse", name))


class TestQuantities(unittest.TestCase):
	def test_check_qty(self):
		self.assertEqual(rules.check_qty("3"), (3.0, None))
		self.assertEqual(rules.check_qty(2.5), (2.5, None))
		for bad in (None, "", "abc", "nan", "inf", 0, -1, "0"):
			qty, problem = rules.check_qty(bad)
			self.assertIsNone(qty, bad)
			self.assertTrue(problem, bad)

	def test_check_qty_rejects_a_barcode_typed_into_the_quantity(self):
		qty, problem = rules.check_qty("4006381333931")
		self.assertIsNone(qty)
		self.assertIn("more than one save can move", problem)

	def test_whole_number_units(self):
		self.assertEqual(rules.check_qty(4, whole_number=True, uom="Unit"), (4.0, None))
		qty, problem = rules.check_qty(2.5, whole_number=True, uom="Unit")
		self.assertIsNone(qty)
		self.assertIn("Unit is counted in whole numbers", problem)

	def test_take_within_stock(self):
		self.assertIsNone(rules.check_take(3, 3))
		self.assertIsNone(rules.check_take(1, "5"))

	def test_take_more_than_on_hand(self):
		problem = rules.check_take(4, 3, "Bin A1-3-1")
		self.assertEqual(problem, "Only 3 on hand at Bin A1-3-1, so 4 cannot be taken.")

	def test_take_refusal_explains_put_away(self):
		problem = rules.check_take(1, 0, "Bin A1-3-1")
		self.assertIn("none on hand at Bin A1-3-1", problem)
		self.assertIn("Move here", problem)
		self.assertIsNotNone(rules.check_take(1, None))

	def test_to_order_uom(self):
		self.assertEqual(rules.to_order_uom(5, 1), (5.0, None))
		self.assertEqual(rules.to_order_uom(20, 10, whole_number=True, uom="Box"), (2.0, None))
		qty, problem = rules.to_order_uom(5, 10, whole_number=True, uom="Box")
		self.assertIsNone(qty)
		self.assertIn("Box", problem)
		self.assertEqual(rules.to_order_uom(5, 10), (0.5, None))
		self.assertEqual(rules.to_order_uom(5, 0), (5.0, None))
		self.assertEqual(rules.to_order_uom(5, None), (5.0, None))

	def test_pending_stock_qty(self):
		self.assertEqual(rules.pending_stock_qty({"qty": 10, "received_qty": 4, "conversion_factor": 1}), 6.0)
		self.assertEqual(
			rules.pending_stock_qty({"qty": 3, "received_qty": 1, "conversion_factor": 12}), 24.0
		)
		self.assertEqual(rules.pending_stock_qty({"qty": 3, "received_qty": 5}), 0.0)

	def test_plain(self):
		self.assertEqual(rules.plain(3.0), "3")
		self.assertEqual(rules.plain(2.5), "2.5")
		self.assertEqual(rules.plain("7"), "7")
		self.assertEqual(rules.plain(0.1 + 0.2), "0.3")


class TestOrderLines(unittest.TestCase):
	def test_oldest_promise_first_and_undated_last(self):
		lines = [
			{"purchase_order": "PO-3", "schedule_date": None, "transaction_date": "2026-09-01", "idx": 1},
			{
				"purchase_order": "PO-2",
				"schedule_date": "2026-09-20",
				"transaction_date": "2026-09-02",
				"idx": 1,
			},
			{
				"purchase_order": "PO-1",
				"schedule_date": "2026-09-10",
				"transaction_date": "2026-09-05",
				"idx": 2,
			},
			{
				"purchase_order": "PO-1",
				"schedule_date": "2026-09-10",
				"transaction_date": "2026-09-05",
				"idx": 1,
			},
		]
		ordered = sorted(lines, key=rules.order_line_sort_key)
		self.assertEqual(
			[(line["purchase_order"], line["idx"]) for line in ordered],
			[("PO-1", 1), ("PO-1", 2), ("PO-2", 1), ("PO-3", 1)],
		)


class TestUndo(unittest.TestCase):
	def test_owner_window_is_inclusive_and_supervisor_is_unbounded(self):
		self.assertIsNone(rules.undo_refusal("Posted", True, False, 30, 30))
		self.assertIn("30 minutes", rules.undo_refusal("Posted", True, False, 30.5, 30))
		self.assertIsNone(rules.undo_refusal("Posted", False, True, 10000, 30))

	def test_not_owner(self):
		self.assertIn("Only the person", rules.undo_refusal("Posted", False, False, 1, 30))

	def test_already_undone(self):
		self.assertIn("already been undone", rules.undo_refusal("Undone", True, True, 1, 30))

	def test_window_off(self):
		self.assertIn("switched off", rules.undo_refusal("Posted", True, False, 1, 0))
		self.assertIn("switched off", rules.undo_refusal("Posted", True, False, 1, None))


class TestWords(unittest.TestCase):
	def test_location_trail_drops_the_root(self):
		trail = rules.location_trail(["All Warehouses", "Inventory", "Row 2", "Bay B1", "Shelf B1-2"])
		self.assertEqual(trail, "Inventory › Row 2 › Bay B1 › Shelf B1-2")
		self.assertEqual(rules.location_trail([]), "")

	def test_remarks(self):
		self.assertEqual(
			rules.remark(rules.ACTION_TAKE, "Jane Doe", "Bin A1-3-1 - SF", 3, "Unit", project="PRJ-00123"),
			"Stock Scan: Jane Doe took 3 Unit from Bin A1-3-1 - SF for PRJ-00123.",
		)
		self.assertIn(
			"against PO-0001",
			rules.remark(rules.ACTION_RECEIVE, "J", "Bin", 1, "Unit", purchase_order="PO-0001"),
		)
		self.assertIn("flagged for review", rules.remark(rules.ACTION_ADD_WITHOUT_PO, "J", "Bin", 1, "Unit"))
		# With a job it is a return from that job, and the voucher says so.
		self.assertEqual(
			rules.remark(rules.ACTION_ADD_WITHOUT_PO, "J", "Bin", 4, "Unit", project="PRJ-00580"),
			"Stock Scan: J returned 4 Unit from PRJ-00580 to Bin without a purchase order (flagged for review).",
		)
		self.assertIn(
			"from Stores - SF to Bin",
			rules.remark(rules.ACTION_MOVE, "J", "Bin", 1, "Unit", from_warehouse="Stores - SF"),
		)

	def test_actions_match_the_doctype_options(self):
		import json

		path = (
			REPO_ROOT
			/ "erpnext_enhancements"
			/ "inventory_enhancements"
			/ "doctype"
			/ "stock_scan_log"
			/ "stock_scan_log.json"
		)
		meta = json.loads(path.read_text(encoding="utf-8"))
		options = next(f["options"] for f in meta["fields"] if f["fieldname"] == "action").split("\n")
		self.assertEqual(tuple(options), rules.ACTIONS)


class TestStoreRuns(unittest.TestCase):
	""""Bought on a store run" (v1.535.0, POL-0602 §4.7-4.8): the pure half of ``store_run``."""

	def log_field(self, fieldname):
		import json

		path = REPO_ROOT / "erpnext_enhancements" / "inventory_enhancements" / "doctype" / "stock_scan_log"
		meta = json.loads((path / "stock_scan_log.json").read_text(encoding="utf-8"))
		return next(f for f in meta["fields"] if f["fieldname"] == fieldname)

	def test_store_run_is_the_last_action(self):
		"""Appended, so every stored action keeps its place in the Select."""
		self.assertEqual(rules.ACTIONS[-1], rules.ACTION_STORE_RUN)
		self.assertEqual(rules.ACTIONS[:4], ("Take", "Receive", "Add Without PO", "Move"))

	def test_the_reasons_are_the_selects_options_after_its_blank(self):
		options = self.log_field("store_run_reason")["options"].split("\n")
		self.assertEqual(options[0], "")
		self.assertEqual(tuple(options[1:]), rules.STORE_RUN_REASONS)
		self.assertEqual(
			rules.STORE_RUN_REASONS,
			("A stocked item was out", "Not something we stock", "Only for this job"),
		)
		self.assertEqual(set(rules.REASON_HINTS), set(rules.STORE_RUN_REASONS))

	def test_check_price(self):
		self.assertEqual(rules.check_price("4.97"), (4.97, None))
		self.assertEqual(rules.check_price("$1,234.50"), (1234.5, None))
		self.assertEqual(rules.check_price(0.125), (0.125, None))
		for bad in (None, "", "  ", "abc", "nan", "inf", 0, "0", -1, "-4.97"):
			with self.subTest(value=bad):
				rate, problem = rules.check_price(bad)
				self.assertIsNone(rate)
				self.assertTrue(problem)
		rate, problem = rules.check_price("4006381333931")
		self.assertIsNone(rate)
		self.assertIn("Check the price", problem)

	def test_check_receipt_total(self):
		self.assertEqual(rules.check_receipt_total("23.41"), (23.41, None))
		for bad in (None, "", "x", 0, -2):
			with self.subTest(value=bad):
				self.assertIsNone(rules.check_receipt_total(bad)[0])

	def test_purchase_date(self):
		import datetime

		today = datetime.date(2026, 9, 24)
		self.assertEqual(rules.purchase_date("today", today), today)
		self.assertEqual(rules.purchase_date("Yesterday", "2026-09-24"), datetime.date(2026, 9, 23))
		self.assertEqual(rules.purchase_date("yesterday", datetime.date(2026, 10, 1)), datetime.date(2026, 9, 30))
		for other in ("", None, "2026-09-20", "last week", "tomorrow"):
			with self.subTest(bought=other):
				self.assertIsNone(rules.purchase_date(other, today))
		self.assertIsNone(rules.purchase_date("today", None))

	def test_recorded_late_and_open(self):
		self.assertTrue(rules.recorded_late("2026-09-23", "2026-09-24"))
		self.assertFalse(rules.recorded_late("2026-09-24", "2026-09-24"))
		self.assertTrue(rules.run_is_open("2026-09-24 07:15:00", "2026-09-24"))
		self.assertFalse(rules.run_is_open("2026-09-23 18:00:00", "2026-09-24"))
		self.assertFalse(rules.run_is_open(None, "2026-09-24"))

	def test_repeat_unstocked_counts_runs(self):
		self.assertFalse(rules.repeat_unstocked(rules.REASON_NOT_STOCKED, 0))
		self.assertTrue(rules.repeat_unstocked(rules.REASON_NOT_STOCKED, 1))
		self.assertTrue(rules.repeat_unstocked(rules.REASON_NOT_STOCKED, 3))
		for reason in (rules.REASON_OUT_OF_STOCK, rules.REASON_ONLY_THIS_JOB, ""):
			with self.subTest(reason=reason):
				self.assertFalse(rules.repeat_unstocked(reason, 5))
		self.assertEqual((rules.REPEAT_WINDOW_DAYS, rules.REPEAT_THRESHOLD), (60, 2))

	def test_store_key(self):
		self.assertEqual(rules.store_key("Lowes"), rules.store_key("Lowe's"))
		self.assertEqual(rules.store_key("Home Depot"), rules.store_key("THE HOME-DEPOT")[3:])
		self.assertNotEqual(rules.store_key("Home Depot"), rules.store_key("Lowes"))
		self.assertEqual(rules.store_key(None), "")

	def test_store_picker_keeps_the_newest_usable_supplier(self):
		rows = [
			{"name": "Lowes", "supplier_name": "Lowes", "creation": "2026-02-03 07:54:24"},
			{"name": "Lowe's", "supplier_name": "Lowe's", "creation": "2026-09-18 09:20:05"},
			{"name": "Home Depot", "supplier_name": "Home Depot", "creation": "2026-06-18 12:03:19"},
			{"name": "Bolt & Nut Supply", "supplier_name": "Bolt & Nut Supply", "creation": "2026-06-18", "disabled": 1},
		]
		picked = rules.store_picker(rows)
		self.assertEqual([p["supplier"] for p in picked], ["Home Depot", "Lowe's"])
		# On hold or disabled is dropped BEFORE collapsing: the older usable record is kept.
		rows[1]["on_hold"] = 1
		self.assertEqual([p["supplier"] for p in rules.store_picker(rows)], ["Home Depot", "Lowes"])
		self.assertEqual(rules.store_picker([]), [])

	def test_quick_item_problem(self):
		groups, uoms = {"PVC Fittings", "Plumbing"}, ["Unit", "FT", "Gallon"]
		good = {"item_code": "406-020", "item_name": "ELBOW, 90, SOC, PVC, 2 IN", "item_group": "PVC Fittings", "stock_uom": "Unit"}
		self.assertIsNone(rules.quick_item_problem(good, groups, uoms))
		for change, words in (
			({"item_code": " "}, "part or model number"),
			({"item_name": ""}, "name"),
			({"item_code": "x" * 141}, "140"),
			({"item_name": "y" * 141}, "140"),
			({"item_code": "A<B>"}, "<"),
			({"item_group": "All Item Groups"}, "group"),
			({"stock_uom": "Box"}, "units"),
		):
			with self.subTest(change=change):
				problem = rules.quick_item_problem({**good, **change}, groups, uoms)
				self.assertTrue(problem)
				self.assertIn(words.lower(), problem.lower())
		self.assertTrue(rules.quick_item_problem(None, groups, uoms))

	def test_a_non_stock_item_can_be_a_store_run_line(self):
		"""660 of 1,086 Items were non-stock on 2026-09-24, and counters sell exactly those."""
		tool = {"name": "DRILL-BIT-1/4", "item_name": "DRILL BIT, 1/4 IN", "is_stock_item": 0}
		self.assertIsNotNone(rules.item_refusal(tool))
		self.assertIsNone(rules.store_run_item_refusal(tool))
		self.assertIn("disabled", rules.store_run_item_refusal({**tool, "disabled": 1}))
		self.assertIn("template", rules.store_run_item_refusal({**tool, "has_variants": 1}))
		self.assertIn("end of life", rules.store_run_item_refusal({**tool, "end_of_life": "2026-01-01"}, "2026-09-24"))
		tomb = {"name": "PVC GLUE (deleted)", "item_name": "PVC GLUE", "is_stock_item": 0}
		self.assertIn("QuickBooks deleted", rules.store_run_item_refusal(tomb))
		stocked = {"name": "406-020", "item_name": "ELBOW", "is_stock_item": 1, "has_serial_no": 1}
		self.assertIn("serial", rules.store_run_item_refusal(stocked))
		self.assertIsNone(rules.store_run_item_refusal({**stocked, "has_serial_no": 0}))

	def test_the_run_ref(self):
		self.assertTrue(rules.is_run_ref("sr-kf3z9a1-8qz0x4m2ab"))
		self.assertFalse(rules.is_run_ref("ss-kf3z9a1-8qz0x4m2ab"))
		self.assertFalse(rules.is_run_ref("sr-a b"))
		self.assertFalse(rules.is_run_ref(None))

	def test_money(self):
		self.assertEqual(rules.money(4.97), "$4.97")
		self.assertEqual(rules.money(1234.5), "$1,234.50")
		self.assertEqual(rules.money(0.497), "$0.4970")

	def test_remark_for_a_store_run(self):
		text = rules.remark(
			rules.ACTION_STORE_RUN,
			"Tina Tech",
			"Bin A1 - SF",
			3,
			"Unit",
			project="PRJ-00598",
			supplier="Home Depot",
			reason=rules.REASON_ONLY_THIS_JOB,
			run="sr-kf3z9a1-8qz0x4m2ab",
		)
		self.assertEqual(
			text,
			"Stock Scan: Tina Tech bought 3 Unit at Home Depot on a store run, into Bin A1 - SF. "
			"Reason: Only for this job. Job: PRJ-00598. Run sr-kf3z9a1-8qz0x4m2ab, flagged for review.",
		)
		plain_run = rules.remark(
			rules.ACTION_STORE_RUN, "J", "Bin", 1, "Unit", supplier="Lowe's", reason="x", run="sr-1", non_stock=True
		)
		self.assertIn("No job (safety or shop).", plain_run)
		self.assertIn("(not a stock item)", plain_run)

	def test_undo_of_a_store_run(self):
		"""Stock Manager is every technician here, so it does not bypass the window on a store run;
		Purchasing and Accounts do; nobody undoes a reviewed one."""
		store = {"store_run": True}
		self.assertIsNone(rules.undo_refusal("Posted", True, True, 5, 30, **store))
		self.assertIn("30 minutes", rules.undo_refusal("Posted", True, True, 45, 30, **store))
		self.assertIn("Purchasing", rules.undo_refusal("Posted", True, True, 45, 30, **store))
		self.assertIn("Only the person", rules.undo_refusal("Posted", False, True, 1, 30, **store))
		self.assertIsNone(rules.undo_refusal("Posted", False, False, 9999, 30, is_purchasing=True, **store))
		self.assertIn("reviewed", rules.undo_refusal("Posted", True, True, 1, 30, is_purchasing=True, reviewed=True, **store))
		self.assertIn("already been undone", rules.undo_refusal("Undone", True, True, 1, 30, **store))
		# Every other save is unchanged: a supervisor may undo any at any time.
		self.assertIsNone(rules.undo_refusal("Posted", False, True, 9999, 30, reviewed=True))


class TestQrSvg(unittest.TestCase):
	def test_matrix_to_svg_draws_runs_as_one_path(self):
		matrix = [
			[1, 1, 0, 1],
			[0, 0, 0, 0],
			[1, 1, 1, 1],
			[0, 1, 0, 0],
		]
		svg = qr_svg.matrix_to_svg(matrix, quiet_zone=4, title="Bin <A&B>")
		self.assertTrue(svg.startswith("<svg"))
		self.assertIn('viewBox="0 0 12 12"', svg)
		self.assertIn('shape-rendering="crispEdges"', svg)
		self.assertIn("<title>Bin &lt;A&amp;B&gt;</title>", svg)
		self.assertEqual(svg.count("<path"), 1)
		path = re.search(r' d="([^"]*)"', svg).group(1)
		self.assertEqual(
			path,
			"M4,4h2v1h-2z" "M7,4h1v1h-1z" "M4,6h4v1h-4z" "M5,7h1v1h-1z",
		)

	def test_no_fixed_size(self):
		svg = qr_svg.matrix_to_svg([[1]])
		self.assertNotRegex(svg, r"<svg[^>]*\swidth=")
		self.assertNotRegex(svg, r"<svg[^>]*\sheight=")

	@unittest.skipUnless(HAVE_PYQRCODE, "pyqrcode not installed (frappe v16 ships it; CI installs it)")
	def test_real_label_url_encodes_small_enough_for_a_one_inch_label(self):
		url = rules.scan_url("https://erp.sapphirefountains.com", "Bin D2-2-1 (Boneyard) - SF")
		matrix = qr_svg.qr_matrix(url)
		size = len(matrix)
		# Version 5 is 37 modules; anything past Version 6 (41) prints too small at 1 inch.
		self.assertLessEqual(size, 41, f"{len(url)}-char URL encoded at {size} modules")
		self.assertTrue(all(len(row) == size for row in matrix))
		# The three finder patterns: a dark 7-module ring at each of three corners.
		for top, left in ((0, 0), (0, size - 7), (size - 7, 0)):
			self.assertEqual(matrix[top][left : left + 7], [1] * 7)
			self.assertEqual(matrix[top + 6][left : left + 7], [1] * 7)
		svg = qr_svg.qr_svg(url, title="Bin D2-2-1")
		self.assertIn(f'viewBox="0 0 {size + 8} {size + 8}"', svg)


class TestParseVectors(unittest.TestCase):
	"""``parse_scan`` against the shared vectors in ``tests/data/stock_scan_parse_vectors.json``.

	``parse_scan`` decides every scan: the page sends the raw text to ``resolve`` and acts only on
	the server's answer. The page keeps a JS twin, ``parseScan`` (``public/js/stock_scan/logic.js``),
	for one job — naming the label in the error it shows when the server could not be reached
	("Couldn't open Bin B1-2-10 - SF: …"). ``scripts/test_stock_scan_client.mjs`` runs that twin
	over this same file, so the two stay identical and the name in that sentence is the one the
	server would have looked up (the ``call_routing_vectors.json`` pattern).
	"""

	VECTORS = REPO_ROOT / "erpnext_enhancements" / "tests" / "data" / "stock_scan_parse_vectors.json"

	def vectors(self):
		import json

		return json.loads(self.VECTORS.read_text(encoding="utf-8"))

	def test_the_vectors_are_substantial_and_well_formed(self):
		"""Anti-vacuity: the loop below passes over an empty list."""
		vectors = self.vectors()
		self.assertGreaterEqual(len(vectors), 15)
		for case in vectors:
			with self.subTest(raw=case.get("raw")):
				self.assertTrue({"raw", "kind", "value"} <= set(case), case)
				self.assertIn(case["kind"], {"", "text", "warehouse", "item", "storage_location"})

	def test_every_kind_is_covered(self):
		kinds = {case["kind"] for case in self.vectors()}
		self.assertEqual(kinds, {"", "text"} | {kind for _key, kind in rules.QUERY_KINDS})

	def test_parse_scan_matches_every_vector(self):
		for case in self.vectors():
			with self.subTest(raw=case["raw"]):
				self.assertEqual(rules.parse_scan(case["raw"]), (case["kind"], case["value"]))

	def test_every_label_the_print_page_writes_parses_back(self):
		"""The label URL is ``scan_url``'s; the vectors' own label cases are written that way too."""
		labels = [
			case
			for case in self.vectors()
			if case["kind"] == "warehouse" and str(case["raw"]).startswith("https://erp.")
		]
		self.assertGreaterEqual(len(labels), 3, "the vectors lost their printed-label cases")
		for case in labels:
			with self.subTest(raw=case["raw"]):
				self.assertEqual(
					rules.parse_scan(rules.scan_url("https://erp.sapphirefountains.com", case["value"])),
					("warehouse", case["value"]),
				)


if __name__ == "__main__":
	unittest.main()
