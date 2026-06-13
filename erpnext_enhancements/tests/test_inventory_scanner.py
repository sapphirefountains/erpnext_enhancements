"""Tests for the Inventory Scanner Audit API (``api.inventory_scanner``).

Covers scan resolution (location / item-by-barcode / item-by-code / unknown),
the single-open-session invariant, counted-line upsert with system-qty snapshot
and variance, the negative-count and variance-reason guards, line removal and
session cancel, the role gate, and the finalize aggregation + draft Stock
Reconciliation build.

``erpnext.stock.utils.get_stock_balance`` is patched so the count maths are
deterministic without seeding the stock ledger; the finalize happy-path patches
the reconciliation builder so the orchestration/state-transition is verified
independently of ERPNext's Stock Reconciliation submit-path validation (the
builder itself is unit-tested separately). Stock-dependent tests skip gracefully
when the site has no Company/Warehouse to anchor them.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.api import inventory_scanner as isa

NO_ROLE_USER = "isa_test_norole@example.com"


def _fake_balance(item_code, warehouse, *args, **kwargs):
	"""10 on hand, valued at 100; matches get_stock_balance's two return shapes."""
	if kwargs.get("with_valuation_rate"):
		return (10.0, 100.0)
	return 10.0


class TestInventoryScanner(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.company = frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")
		self.warehouse = self._ensure_warehouse()
		self.item = self._ensure_item()
		self._ensure_location()

	# ----- fixtures -----
	def _ensure_warehouse(self):
		if not self.company:
			return None
		existing = frappe.db.get_value("Warehouse", {"company": self.company, "is_group": 0}, "name")
		if existing:
			return existing
		return (
			frappe.get_doc(
				{"doctype": "Warehouse", "warehouse_name": "ISA Test WH", "company": self.company}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _ensure_item(self):
		if frappe.db.exists("Item", "ISA-TEST-ITEM"):
			return "ISA-TEST-ITEM"
		item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or frappe.db.get_value(
			"Item Group", {}, "name"
		)
		uom = frappe.db.get_value("UOM", {}, "name") or "Nos"
		doc = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "ISA-TEST-ITEM",
				"item_name": "ISA Test Item",
				"item_group": item_group,
				"stock_uom": uom,
				"is_stock_item": 1,
			}
		)
		doc.append("barcodes", {"barcode": "ISA-BC-1"})
		doc.insert(ignore_permissions=True)
		return doc.name

	def _ensure_location(self):
		if not self.warehouse or frappe.db.exists("Storage Location", "ISA-LOC-1"):
			return
		frappe.get_doc(
			{
				"doctype": "Storage Location",
				"location_code": "ISA-LOC-1",
				"barcode": "ISA-LOCBC-1",
				"warehouse": self.warehouse,
			}
		).insert(ignore_permissions=True)

	def _ensure_no_role_user(self):
		if not frappe.db.exists("User", NO_ROLE_USER):
			frappe.get_doc(
				{"doctype": "User", "email": NO_ROLE_USER, "first_name": "ISA", "last_name": "NoRole"}
			).insert(ignore_permissions=True)

	def _require_stock(self):
		if not self.company or not self.warehouse:
			self.skipTest("No Company/Warehouse available on this site for a stock-dependent test.")

	def _new_session(self):
		return frappe.get_doc(
			{
				"doctype": "Inventory Count Session",
				"counted_by": "Administrator",
				"company": self.company,
				"default_warehouse": self.warehouse,
				"status": "Open",
			}
		).insert(ignore_permissions=True)

	# ----- resolve_scan -----
	def test_resolve_location_by_barcode(self):
		self._require_stock()
		res = isa.resolve_scan("ISA-LOCBC-1")
		self.assertEqual(res["type"], "location")
		self.assertEqual(res["storage_location"], "ISA-LOC-1")
		self.assertEqual(res["warehouse"], self.warehouse)

	def test_resolve_item_by_barcode_and_by_code(self):
		by_barcode = isa.resolve_scan("ISA-BC-1")
		self.assertEqual(by_barcode["type"], "item")
		self.assertEqual(by_barcode["item_code"], "ISA-TEST-ITEM")

		by_code = isa.resolve_scan("ISA-TEST-ITEM")
		self.assertEqual(by_code["type"], "item")
		self.assertEqual(by_code["item_code"], "ISA-TEST-ITEM")

	def test_resolve_unknown(self):
		self.assertEqual(isa.resolve_scan("NO-SUCH-CODE-zzz")["type"], "unknown")

	def test_resolve_item_includes_system_qty_when_warehouse_given(self):
		self._require_stock()
		with patch("erpnext.stock.utils.get_stock_balance", _fake_balance):
			res = isa.resolve_scan("ISA-BC-1", warehouse=self.warehouse)
		self.assertEqual(res["system_qty"], 10.0)

	# ----- session lifecycle -----
	def test_start_session_single_open_invariant(self):
		self._require_stock()
		first = isa.start_session()
		second = isa.start_session()
		self.assertEqual(first["name"], second["name"])

	# ----- add_count -----
	def test_add_count_snapshots_and_upserts(self):
		self._require_stock()
		sess = self._new_session()
		with patch("erpnext.stock.utils.get_stock_balance", _fake_balance):
			isa.add_count(sess.name, "ISA-TEST-ITEM", 10, storage_location="ISA-LOC-1")
			payload = isa.add_count(sess.name, "ISA-TEST-ITEM", 12, storage_location="ISA-LOC-1", reason="recount")
		self.assertEqual(len(payload["lines"]), 1)  # upsert, not a duplicate row
		line = payload["lines"][0]
		self.assertEqual(line["system_qty"], 10.0)
		self.assertEqual(line["counted_qty"], 12.0)
		self.assertEqual(line["variance"], 2.0)

	def test_add_count_blocks_negative(self):
		self._require_stock()
		sess = self._new_session()
		with patch("erpnext.stock.utils.get_stock_balance", _fake_balance):
			with self.assertRaises(frappe.ValidationError):
				isa.add_count(sess.name, "ISA-TEST-ITEM", -1, storage_location="ISA-LOC-1")

	def test_add_count_requires_variance_reason(self):
		self._require_stock()
		sess = self._new_session()
		with patch("erpnext.stock.utils.get_stock_balance", _fake_balance):
			with self.assertRaises(frappe.ValidationError):
				isa.add_count(sess.name, "ISA-TEST-ITEM", 7, storage_location="ISA-LOC-1")
			payload = isa.add_count(sess.name, "ISA-TEST-ITEM", 7, storage_location="ISA-LOC-1", reason="short")
		self.assertEqual(payload["lines"][0]["variance"], -3.0)

	# ----- remove + cancel -----
	def test_remove_line(self):
		self._require_stock()
		sess = self._new_session()
		with patch("erpnext.stock.utils.get_stock_balance", _fake_balance):
			isa.add_count(sess.name, "ISA-TEST-ITEM", 10, storage_location="ISA-LOC-1")
		payload = isa.remove_line(sess.name, 1)
		self.assertEqual(len(payload["lines"]), 0)

	def test_cancel_session(self):
		self._require_stock()
		sess = self._new_session()
		res = isa.cancel_session(sess.name)
		self.assertEqual(res["status"], "Cancelled")
		self.assertEqual(frappe.db.get_value("Inventory Count Session", sess.name, "status"), "Cancelled")

	# ----- role gate -----
	def test_role_gate_blocks_unprivileged_user(self):
		self._ensure_no_role_user()
		frappe.set_user(NO_ROLE_USER)
		try:
			with self.assertRaises(frappe.PermissionError):
				isa.get_bootstrap()
		finally:
			frappe.set_user("Administrator")

	# ----- finalize: aggregation + reconciliation -----
	def test_aggregate_counts_sums_per_item_and_warehouse(self):
		doc = frappe._dict(
			lines=[
				frappe._dict(item_code="A", warehouse="WH1", counted_qty=3),
				frappe._dict(item_code="A", warehouse="WH1", counted_qty=2),
				frappe._dict(item_code="A", warehouse="WH2", counted_qty=5),
				frappe._dict(item_code="B", warehouse="WH1", counted_qty=1),
			]
		)
		agg = isa._aggregate_counts(doc)
		self.assertEqual(agg[("A", "WH1")], 5.0)
		self.assertEqual(agg[("A", "WH2")], 5.0)
		self.assertEqual(agg[("B", "WH1")], 1.0)

	def test_build_reconciliation_is_draft_with_one_row_per_key(self):
		self._require_stock()
		agg = {("ISA-TEST-ITEM", self.warehouse): 13.0}
		with patch("erpnext.stock.utils.get_stock_balance", _fake_balance):
			sr = isa._build_reconciliation(self.company, agg)
		self.assertEqual(sr.purpose, "Stock Reconciliation")
		self.assertEqual(sr.company, self.company)
		self.assertEqual(sr.docstatus, 0)  # draft — never auto-submitted
		self.assertEqual(len(sr.items), 1)
		row = sr.items[0]
		self.assertEqual(row.item_code, "ISA-TEST-ITEM")
		self.assertEqual(row.warehouse, self.warehouse)
		self.assertEqual(row.qty, 13.0)
		self.assertEqual(row.valuation_rate, 100.0)

	def test_finalize_links_draft_reconciliation_and_marks_finalized(self):
		self._require_stock()
		sess = self._new_session()
		with patch("erpnext.stock.utils.get_stock_balance", _fake_balance):
			isa.add_count(sess.name, "ISA-TEST-ITEM", 13, storage_location="ISA-LOC-1", reason="found extra")

		# Isolate from ERPNext's Stock Reconciliation validation: stub the builder
		# so finalize's orchestration + state transition is what's under test.
		fake_sr = frappe._dict(name="SR-ISA-TEST-0001")
		fake_sr.insert = lambda ignore_permissions=True: None
		with patch.object(isa, "_build_reconciliation", return_value=fake_sr):
			res = isa.finalize_session(sess.name)

		self.assertEqual(res["stock_reconciliation"], "SR-ISA-TEST-0001")
		self.assertEqual(res["rows"], 1)
		sess.reload()
		self.assertEqual(sess.status, "Finalized")
		self.assertEqual(sess.stock_reconciliation, "SR-ISA-TEST-0001")

	def test_finalize_empty_session_throws(self):
		self._require_stock()
		sess = self._new_session()
		with self.assertRaises(frappe.ValidationError):
			isa.finalize_session(sess.name)
