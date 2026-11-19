"""Bench-free tests for the Estimate controller (``estimating/doctype/estimate/estimate.py``).

Runs the real controller against a small ``frappe`` stub installed in ``setUpModule``, so it has its
own CI step. What it holds:

* a new estimate copies the default rate card, but never over rates already typed on it;
* a phase row is added for every phase a line uses, and for the phase it nests under (120 -> 100),
  in number order, with the usual label;
* a bad override names its row; ``" 5 % "`` is stored as ``"5"``; a blank stays blank (inherit);
* a cost code outside the catalog, or a computed one (FEE), is refused; a cost type follows its code
  when the line has none;
* every line, phase and total the server stores is the engine's number;
* a superseded revision refuses an edit.

Run: python -m unittest erpnext_enhancements.tests.test_estimate_controller -v
"""

import importlib
import sys
import types
import unittest


class Thrown(Exception):
	pass


class Row(dict):
	"""A child row: attribute and dict access, like a frappe Document's child."""

	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value

	def set(self, key, value):
		self[key] = value

	def as_dict(self):
		return dict(self)


class FakeDocument:
	def __init__(self, **fields):
		self.__dict__["_fields"] = {}
		self.flags = types.SimpleNamespace(from_revision=False)
		for k, v in fields.items():
			setattr(self, k, v)

	def __getattr__(self, key):
		return self.__dict__["_fields"].get(key)

	def __setattr__(self, key, value):
		if key == "flags":
			self.__dict__["flags"] = value
			return
		if key in ("lines", "phases"):
			value = [v if isinstance(v, Row) else Row(v) for v in value]
			for i, v in enumerate(value, start=1):
				v.setdefault("idx", i)
		self.__dict__["_fields"][key] = value

	def get(self, key, default=None):
		value = self.__dict__["_fields"].get(key)
		return default if value is None else value

	def set(self, key, value):
		setattr(self, key, value)

	def append(self, table, row):
		rows = self.get(table) or []
		row = Row(row)
		row["idx"] = len(rows) + 1
		rows.append(row)
		self.__dict__["_fields"][table] = rows
		return row

	def is_new(self):
		return not self.name


RATE_CARD = {
	"labor_rate": 50,
	"tax_pct": 7.25,
	"markup_material": 15,
	"markup_equipment": 15,
	"markup_labor": 0,
	"markup_subcontract": 10,
	"markup_expense": 0,
	"contingency_pct": 5,
	"insurance_pct": 7,
	"fee_pct": 12,
	"commission_pct": 10,
}
STORED_STATUS = {}


def setUpModule():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda s: s
	frappe.bold = lambda s: s

	def throw(msg, *a, **k):
		raise Thrown(msg)

	frappe.throw = throw
	frappe.session = types.SimpleNamespace(user="estimator@example.com")
	frappe.defaults = types.SimpleNamespace(get_user_default=lambda key: "Example Co")

	class DB:
		def get_value(self, doctype, filters, field=None, **kw):
			if doctype == "Estimate Rate Card":
				return "Standard"
			if doctype == "Estimate":
				return STORED_STATUS.get(filters)
			return None

		def get_single_value(self, *a):
			return "Example Co"

	frappe.db = DB()
	frappe.get_cached_doc = lambda doctype, name: Row(RATE_CARD)
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = FakeDocument
	frappe.model = model
	model.document = document
	sys.modules["frappe"] = frappe
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document
	global estimate
	estimate = importlib.import_module("erpnext_enhancements.estimating.doctype.estimate.estimate")
	importlib.reload(estimate)


def tearDownModule():
	for name in ("frappe", "frappe.model", "frappe.model.document"):
		sys.modules.pop(name, None)


def new_estimate(lines, phases=(), **fields):
	doc = estimate.Estimate(title="Example water wall", **fields)
	doc.lines = [dict(line) for line in lines]
	doc.phases = [dict(p) for p in phases]
	return doc


class TestEstimateController(unittest.TestCase):
	def test_new_estimate_copies_the_default_rate_card(self):
		doc = new_estimate([])
		doc.before_insert()
		self.assertEqual(doc.rate_card, "Standard")
		self.assertEqual(doc.fee_pct, 12)
		self.assertEqual(doc.labor_rate, 50)

	def test_typed_rates_are_kept(self):
		doc = new_estimate([], fee_pct=20)
		doc.before_insert()
		self.assertEqual(doc.fee_pct, 20)
		self.assertIsNone(doc.markup_material)

	def test_phases_are_added_with_parents_in_order(self):
		doc = new_estimate(
			[
				{
					"phase_number": 500,
					"description": "Pumps",
					"cost_type": "Equipment",
					"cost_code": "22 5216",
					"qty": 1,
					"unit_cost": 100,
				},
				{
					"phase_number": 120,
					"description": "Schematic design",
					"cost_type": "Labor",
					"qty": 10,
					"unit_cost": 90,
				},
			],
			phases=[{"phase_number": 500, "label": "Equipment vault"}],
		)
		doc.before_insert()
		doc.validate()
		self.assertEqual([p.phase_number for p in doc.phases], [100, 120, 500])
		self.assertEqual(
			[p.label for p in doc.phases], ["Design and planning", "Schematic design", "Equipment vault"]
		)
		self.assertEqual(doc.phases[0].amount, doc.phases[1].amount)
		self.assertEqual(doc.company, "Example Co")

	def test_pricing_is_the_engines(self):
		from erpnext_enhancements.estimating import estimate_math as em

		lines = [
			{
				"phase_number": 300,
				"description": "Rough piping",
				"cost_type": "Material",
				"cost_code": "22 5213.13",
				"qty": 120,
				"unit_cost": 5.91,
			},
			{
				"phase_number": 300,
				"description": "Rough-in labor",
				"cost_type": "Labor",
				"cost_code": "LAB-DIRECT",
				"qty": 40,
				"unit_cost": 50,
				"fee_override": " 20 % ",
			},
			{
				"phase_number": 700,
				"description": "Tile",
				"cost_type": "Subcontract",
				"qty": 1,
				"unit_cost": 999,
				"by_others": 1,
			},
			{
				"phase_number": 700,
				"description": "Basin rocks",
				"cost_type": "Material",
				"qty": 1,
				"sheet_price": 2000,
				"basis": "Allowance",
			},
		]
		doc = new_estimate(lines)
		doc.before_insert()
		doc.validate()
		self.assertEqual(doc.lines[1].fee_override, "20")
		self.assertEqual(doc.lines[0].amount, 1084.65)
		self.assertEqual(doc.lines[1].fee_pct, 20)
		self.assertEqual(doc.lines[2].amount, 0)
		self.assertEqual(doc.lines[3].needs_cost, 1)
		expect = em.price_estimate(
			RATE_CARD,
			[{"phase_number": 300}, {"phase_number": 700}],
			[dict(l, fee_override="20" if i == 1 else "") for i, l in enumerate(lines)],
		)
		self.assertEqual(doc.total_amount, expect["totals"]["amount"])
		self.assertEqual(doc.total_cost, expect["totals"]["cost_amount"])
		self.assertEqual(doc.missing_cost_count, 1)
		self.assertEqual(doc.missing_code_count, 1)  # the allowance line; by-others is not counted
		self.assertEqual(doc.allowance_count, 1)
		self.assertEqual(doc.prepared_by, "estimator@example.com")

	def test_bad_override_names_its_row(self):
		doc = new_estimate(
			[
				{
					"phase_number": 300,
					"description": "Pipe",
					"cost_type": "Material",
					"qty": 1,
					"unit_cost": 1,
					"markup_override": "lots",
				}
			]
		)
		with self.assertRaises(Thrown) as ctx:
			doc.validate()
		self.assertIn("Line 1", str(ctx.exception))

	def test_codes_are_checked(self):
		for code, words in (("22 5214", "not in the cost code catalog"), ("FEE", "computed")):
			doc = new_estimate(
				[
					{
						"phase_number": 300,
						"description": "Thing",
						"cost_type": "Material",
						"cost_code": code,
						"qty": 1,
					}
				]
			)
			with self.assertRaises(Thrown) as ctx:
				doc.validate()
			self.assertIn(words, str(ctx.exception))

	def test_cost_type_follows_the_code_when_blank(self):
		doc = new_estimate(
			[
				{
					"phase_number": 500,
					"description": "Pump",
					"cost_type": "",
					"cost_code": " 22 5216 ",
					"qty": 1,
				}
			]
		)
		doc.validate()
		self.assertEqual(doc.lines[0].cost_code, "22 5216")
		self.assertEqual(doc.lines[0].cost_type, "Equipment")

	def test_negative_numbers_are_refused(self):
		doc = new_estimate(
			[
				{
					"phase_number": 300,
					"description": "Credit",
					"cost_type": "Material",
					"qty": 1,
					"unit_cost": -5,
				}
			]
		)
		with self.assertRaises(Thrown):
			doc.validate()

	def test_superseded_revision_is_read_only(self):
		doc = new_estimate([], name="EST-26-0001")
		STORED_STATUS["EST-26-0001"] = "Superseded"
		try:
			with self.assertRaises(Thrown):
				doc.validate()
		finally:
			STORED_STATUS.clear()

	def test_event_with_no_phases_gets_none(self):
		doc = new_estimate(
			[
				{
					"phase_number": 0,
					"description": "Fountain rental",
					"cost_type": "Equipment",
					"qty": 2,
					"unit_cost": 100,
				}
			]
		)
		doc.before_insert()
		doc.validate()
		self.assertEqual(doc.phases, [])
		self.assertGreater(doc.total_amount, 200)


if __name__ == "__main__":
	unittest.main()
