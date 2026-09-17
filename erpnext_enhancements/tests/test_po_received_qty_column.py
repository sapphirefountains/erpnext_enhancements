"""Received Qty as a column in the Purchase Order items grid (ER-2026-458194, TASK-2026-02046).

Every assertion here pins a decision where the obvious edit is the wrong one and produces
no error — the shape this repo keeps being bitten by:

* ``in_list_view`` alone puts the column **off the right edge**. A grid's column order is the
  doctype's field order, and ``received_qty`` is native field 75 of 106, behind Rate, Amount,
  Warehouse and Project. The column exists, renders, and nobody sees it without scrolling.
  The ``field_order`` Property Setter this app already carries has to move it up, beside Qty.
* ``depends_on: received_qty`` on the native field is evaluated **per row** for grid columns
  (frappe v16 ``grid_row.refresh_dependency``), so without clearing it every unreceived line
  shows an empty cell instead of 0 — which reads as "unknown", not "nothing yet".
* ``columns`` left at the default of 2 overflows the 12-unit row; 1 fits exactly, once Item
  Status leaves the default grid.
* Item Status (``custom_item_status``, ER-2026-312391) leaves the **grid**, not the form: set
  on 0 of 389 lines this year, and its "Received" option is what the new column now says
  with a number. It stays editable after submit on the row form.

Bench-free: reads the fixture JSON. unittest-style, on the same CI step as
``test_feedback_er_batch`` (the sibling ER pins).

Run: python -m unittest erpnext_enhancements.tests.test_po_received_qty_column
"""

import json
import pathlib
import unittest

APP = pathlib.Path(__file__).resolve().parents[1]
CUSTOM_FIELDS = APP / "fixtures" / "custom_field.json"
PROPERTY_SETTERS = APP / "fixtures" / "property_setter.json"

DOCTYPE = "Purchase Order Item"
FIELD = "received_qty"


def _by_name(path):
	return {row["name"]: row for row in json.loads(path.read_text(encoding="utf-8"))}


class ReceivedQtyColumn(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.props = _by_name(PROPERTY_SETTERS)
		cls.fields = _by_name(CUSTOM_FIELDS)

	def _prop(self, prop):
		row = self.props.get(f"{DOCTYPE}-{FIELD}-{prop}")
		self.assertIsNotNone(row, f"missing Property Setter {DOCTYPE}-{FIELD}-{prop}")
		self.assertEqual(row["doc_type"], DOCTYPE)
		self.assertEqual(row["field_name"], FIELD)
		self.assertEqual(row["property"], prop)
		self.assertEqual(
			row["is_system_generated"], 0, "the fixture filter drops system-generated setters"
		)
		return row

	def test_the_native_field_is_surfaced_not_shadowed(self):
		"""``received_qty`` is stock ERPNext, maintained by ``status_updater`` from receipts. A
		Custom Field with that fieldname is rejected as a duplicate — the
		``expected_delivery_date`` lesson from ER-2026-362239."""
		self.assertNotIn(f"{DOCTYPE}-{FIELD}", self.fields)
		row = self._prop("in_list_view")
		self.assertEqual(row["value"], "1")
		self.assertEqual(row["property_type"], "Check")

	def test_one_unit_wide(self):
		row = self._prop("columns")
		self.assertEqual(row["value"], "1")
		self.assertEqual(row["property_type"], "Int")

	def test_the_dependency_is_cleared_so_zero_shows_as_zero(self):
		row = self._prop("depends_on")
		self.assertEqual(row["value"], "", "a blank cell on an unreceived line reads as unknown, not 0")
		self.assertEqual(row["property_type"], "Data")

	def test_it_sits_beside_qty_in_the_field_order(self):
		"""Column order is field order. The native position is behind Rate, Amount, Warehouse
		and Project — off the right edge of the grid."""
		row = self.props.get(f"{DOCTYPE}-main-field_order")
		self.assertIsNotNone(row, "the field_order Property Setter is what places the column")
		order = json.loads(row["value"])
		self.assertEqual(order.count(FIELD), 1)
		self.assertEqual(order.index(FIELD), order.index("uom") + 1, "received_qty must directly follow uom")
		for later in ("rate", "amount", "warehouse", "project", "section_break_56"):
			self.assertLess(order.index(FIELD), order.index(later))

	def test_item_status_left_the_grid_and_only_the_grid(self):
		field = self.fields[f"{DOCTYPE}-custom_item_status"]
		self.assertEqual(field["in_list_view"], 0, "Item Status gives its grid unit to Received Qty")
		self.assertEqual(field["hidden"], 0, "it left the grid, not the form")
		self.assertEqual(field["allow_on_submit"], 1, "still editable after submit on the row form")

	def test_the_default_row_still_fits_twelve_units(self):
		"""What the grid shows before Rate, in field order, with each column's width.

		The native ``in_list_view`` fields and their widths are v16's (``item_code`` 2,
		``qty`` 1, ``uom`` 1, ``schedule_date`` 2); the rest come from the fixtures. Custom
		fields are counted whole, because both that carry ``in_list_view`` today sit before
		Rate — the Buy column first, Item Status after Expected Delivery. A 13th unit pushes
		Expected Delivery off the edge, which is precisely the state the screenshot on
		ER-2026-458194 was taken in.
		"""
		widths = {"item_code": 2, "qty": 1, "uom": 1, "schedule_date": 2}
		widths["item_name"] = 2  # in_list_view via Property Setter, default width for Data
		widths["expected_delivery_date"] = int(
			self.props[f"{DOCTYPE}-expected_delivery_date-columns"]["value"]
		)
		widths[FIELD] = int(self._prop("columns")["value"])
		order = json.loads(self.props[f"{DOCTYPE}-main-field_order"]["value"])
		native_units = sum(
			width for name, width in widths.items() if order.index(name) < order.index("rate")
		)
		custom_units = sum(
			row.get("columns") or 2
			for row in self.fields.values()
			if row["dt"] == DOCTYPE and row["in_list_view"]
		)
		total = native_units + custom_units
		self.assertLessEqual(total, 12, f"{total} units before Rate; the row is 12")


if __name__ == "__main__":
	unittest.main()
