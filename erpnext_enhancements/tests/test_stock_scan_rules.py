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
