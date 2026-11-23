"""Bench-free tests for the Estimate's rules, its seeded templates and its cost-visibility contract.

* ``estimating/rules.py``: cost codes checked against the catalog (never a pattern), phases as the
  integers Task carries, and what the Quotation says - one price per phase, allowances named,
  "by others" listed as exclusions, never a cost.
* ``estimating/data/estimate_templates.json``: every seeded line's code is in the catalog and its
  phase is one its template lists; the three job kinds Nik set for the first test are there.
* The doctype JSON: every cost, markup, margin and rate field is at permlevel 1, and only the
  2026-09-21 audience (Estimator, Finance Team, Executive Team, System Manager) can read level 1.
  That is the rule that keeps a wage from being worked out of a labor line.

Run: python -m unittest erpnext_enhancements.tests.test_estimating_rules -v
"""

import json
import os
import unittest

from erpnext_enhancements.estimating import estimate_math as em
from erpnext_enhancements.estimating import rules

APP = os.path.join(os.path.dirname(__file__), "..")
DOCTYPES = os.path.join(APP, "estimating", "doctype")
COST_AUDIENCE = {"System Manager", "Estimator", "Finance Team", "Executive Team"}


def doctype_json(name):
	folder = name.lower().replace(" ", "_")
	with open(os.path.join(DOCTYPES, folder, folder + ".json")) as fh:
		return json.load(fh)


class TestCostCodes(unittest.TestCase):
	def test_normalize(self):
		self.assertEqual(rules.normalize_code(" lab-pm "), "LAB-PM")
		self.assertEqual(rules.normalize_code("22 5216"), "22 5216")
		self.assertEqual(rules.normalize_code("22   5216 "), "22 5216")
		self.assertEqual(rules.normalize_code(None), "")

	def test_catalog_not_pattern(self):
		self.assertIsNone(rules.code_problem("22 5216"))
		self.assertIsNone(rules.code_problem("LAB-PM"))
		self.assertIsNone(rules.code_problem(""))
		# A legal shape that is not a section: an invented six-digit sibling (WI-077).
		self.assertIn("not in the cost code catalog", rules.code_problem("22 5214"))

	def test_old_codes_point_forward(self):
		self.assertIn("22 5216", rules.code_problem("22 3100"))

	def test_computed_codes_are_refused(self):
		self.assertIn("computed", rules.code_problem("FEE"))
		self.assertIn("computed", rules.code_problem("01 2116"))
		codes = {o["code"] for o in rules.code_options()}
		self.assertNotIn("FEE", codes)
		self.assertIn("LAB-DIRECT", codes)

	def test_default_cost_type(self):
		self.assertEqual(rules.default_cost_type("22 5216"), "Equipment")
		self.assertEqual(rules.default_cost_type("LAB-PM"), "Labor")
		self.assertIsNone(rules.default_cost_type("nope"))


class TestPhases(unittest.TestCase):
	def test_labels(self):
		self.assertEqual(rules.phase_label(120), "Schematic design")
		self.assertEqual(rules.phase_label(300, {300: "Underground"}), "Underground")
		self.assertEqual(rules.phase_label(350), "Phase 350")

	def test_missing_phases_add_parents(self):
		self.assertEqual(rules.missing_phases([300], [120, 300, 0, 500]), [100, 120, 500])
		self.assertEqual(rules.missing_phases([], [0, 0]), [])


class TestQuotation(unittest.TestCase):
	def setUp(self):
		self.phases = [
			{"phase_number": 100, "label": "Design and planning"},
			{"phase_number": 120, "label": "Schematic design", "scope_text": "Concept layout."},
			{"phase_number": 700, "label": "Features and finishes"},
		]
		self.lines = [
			{
				"phase_number": 120,
				"description": "Schematic design",
				"cost_type": "Labor",
				"qty": 10,
				"unit_cost": 100,
			},
			{
				"phase_number": 700,
				"description": "Basin rocks",
				"cost_type": "Material",
				"qty": 1,
				"sheet_price": 2000,
				"basis": "Allowance",
			},
			{
				"phase_number": 700,
				"description": "Nozzles",
				"cost_type": "Material",
				"qty": 4,
				"unit_cost": 50,
			},
			{
				"phase_number": 700,
				"description": "Tile",
				"cost_type": "Subcontract",
				"qty": 1,
				"unit_cost": 999,
				"by_others": 1,
			},
		]
		self.results = em.price_estimate({"fee_pct": 10}, self.phases, self.lines)["lines"]

	def test_one_row_per_phase_in_order_never_cost(self):
		rows, exclusions = rules.quotation_rows(self.phases, self.lines, self.results)
		self.assertEqual(
			[r["item_name"] for r in rows], ["120 Schematic design", "700 Features and finishes"]
		)
		self.assertEqual(rows[0]["amount"], 1100.0)
		self.assertEqual(rows[1]["amount"], 2220.0)
		self.assertIn("Part of 100 Design and planning.", rows[0]["description"])
		self.assertIn("Concept layout.", rows[0]["description"])
		self.assertIn("allowance: Basin rocks, 2,000.00", rows[1]["description"])
		self.assertEqual(exclusions, ["Tile"])
		for row in rows:
			self.assertNotIn("cost", row["description"].lower())

	def test_quotation_total_is_estimate_total(self):
		rows, _ = rules.quotation_rows(self.phases, self.lines, self.results)
		total = em.price_estimate({"fee_pct": 10}, self.phases, self.lines)["totals"]["amount"]
		self.assertEqual(em.round_money(sum(r["amount"] for r in rows)), total)

	def test_unphased_lines_are_rows_of_their_own(self):
		lines = [
			{
				"phase_number": 0,
				"description": "Fountain rental",
				"cost_type": "Equipment",
				"qty": 2,
				"uom": "ea",
				"unit_cost": 100,
			},
			{
				"phase_number": 0,
				"description": "Setup labor",
				"cost_type": "Labor",
				"qty": 6,
				"uom": "h",
				"unit_cost": 40,
			},
		]
		results = em.price_estimate({}, [], lines)["lines"]
		rows, _ = rules.quotation_rows([], lines, results)
		self.assertEqual([r["item_name"] for r in rows], ["Fountain rental", "Setup labor"])
		self.assertIn("2 ea", rows[0]["description"])

	def test_unpriced_lines(self):
		lines = self.lines + [
			{"phase_number": 700, "description": "Mystery", "cost_type": "Material", "qty": 1}
		]
		results = em.price_estimate({}, self.phases, lines)["lines"]
		self.assertEqual([l["description"] for l in rules.unpriced_lines(lines, results)], ["Mystery"])

	def test_exclusions_are_escaped(self):
		html = rules.exclusions_html(["<b>Tile</b> & grout"])
		self.assertIn("&lt;b&gt;Tile&lt;/b&gt; &amp; grout", html)
		self.assertEqual(rules.exclusions_html([]), "")


class TestSeededTemplates(unittest.TestCase):
	def setUp(self):
		with open(os.path.join(APP, "estimating", "data", "estimate_templates.json")) as fh:
			self.templates = json.load(fh)["templates"]

	def test_the_job_kinds_the_first_test_needs(self):
		kinds = {t["job_kind"]: t for t in self.templates}
		for kind in ("Design-Build", "Event", "Custom Panel"):
			self.assertIn(kind, kinds)
		design_build = [p[0] for p in kinds["Design-Build"]["phases"]]
		self.assertEqual([n for n in design_build if em.top_phase(n) == 100], [100, 110, 120, 130, 140])
		self.assertEqual(kinds["Event"]["phases"], [])
		self.assertNotIn(300, [p[0] for p in kinds["Custom Panel"]["phases"]])

	def test_every_line_is_valid(self):
		job_kinds = set(doctype_json("Estimate Template")["fields"][1]["options"].split("\n"))
		for t in self.templates:
			self.assertIn(t["job_kind"], job_kinds)
			numbers = {p[0] for p in t["phases"]}
			for number, include, description, cost_type, code, uom in t["lines"]:
				where = f"{t['template_name']}: {description}"
				self.assertIn(cost_type, em.COST_TYPES, where)
				self.assertIsNone(rules.code_problem(code), where)
				self.assertIn(include, (0, 1), where)
				if numbers:
					self.assertIn(number, numbers, where)
				else:
					self.assertEqual(number, 0, where)
				if code:
					self.assertEqual(rules.default_cost_type(code), cost_type, where)

	def test_no_prices_are_seeded(self):
		raw = open(os.path.join(APP, "estimating", "data", "estimate_templates.json")).read()
		self.assertNotIn("unit_cost", raw)
		self.assertNotIn("$", raw)


class TestCostVisibility(unittest.TestCase):
	COST_WORDS = (
		"cost",
		"markup",
		"margin",
		"commission",
		"contingency",
		"insurance",
		"fee",
		"tax",
		"labor_rate",
		"override",
	)

	def test_cost_fields_are_permlevel_1(self):
		for name in ("Estimate", "Estimate Line", "Estimate Phase"):
			for field in doctype_json(name)["fields"]:
				fn = field["fieldname"]
				if field["fieldtype"] in ("Section Break", "Column Break", "HTML", "Table"):
					continue
				if fn in ("missing_cost_count", "needs_cost", "line_count", "cost_type", "cost_code"):
					continue  # counts, flags and classification, not money
				if any(w in fn for w in self.COST_WORDS):
					self.assertEqual(field.get("permlevel", 0), 1, f"{name}.{fn}")

	def test_price_fields_stay_at_level_0(self):
		fields = {f["fieldname"]: f for f in doctype_json("Estimate Line")["fields"]}
		for fn in ("amount", "unit_price", "qty", "description", "sheet_price"):
			self.assertEqual(fields[fn].get("permlevel", 0), 0, fn)

	def test_only_the_cost_audience_reads_level_1(self):
		perms = doctype_json("Estimate")["permissions"]
		level1 = {p["role"] for p in perms if p.get("permlevel") == 1 and p.get("read")}
		self.assertEqual(level1, COST_AUDIENCE)
		level0 = {p["role"] for p in perms if not p.get("permlevel") and p.get("read")}
		self.assertTrue(level0 <= COST_AUDIENCE, level0 - COST_AUDIENCE)

	def test_rate_cards_and_templates_only_for_the_cost_audience(self):
		for name in ("Estimate Rate Card", "Estimate Template"):
			roles = {p["role"] for p in doctype_json(name)["permissions"] if p.get("read")}
			self.assertEqual(roles, COST_AUDIENCE, name)

	def test_controller_writes_only_real_fields(self):
		src = open(os.path.join(DOCTYPES, "estimate", "estimate.py")).read()
		line_fields = {f["fieldname"] for f in doctype_json("Estimate Line")["fields"]}
		block = src.split("LINE_RESULTS = (")[1].split(")")[0]
		for fn in [x.strip().strip('",') for x in block.split("\n") if x.strip().strip('",')]:
			self.assertIn(fn, line_fields, fn)
		for fn in ("needs_cost",):
			self.assertIn(fn, line_fields)
		est_fields = {f["fieldname"] for f in doctype_json("Estimate")["fields"]}
		block = src.split("RATE_FIELDS = (")[1].split(")")[0]
		for fn in [x.strip().strip('",') for x in block.split("\n") if x.strip().strip('",')]:
			self.assertIn(fn, est_fields, fn)
		card_fields = {f["fieldname"] for f in doctype_json("Estimate Rate Card")["fields"]}
		for fn in [x.strip().strip('",') for x in block.split("\n") if x.strip().strip('",')]:
			self.assertIn(fn, card_fields, fn)


class TestBillingDocuments(unittest.TestCase):
	"""The schedule of values and the pay application leave the building (milestone 2)."""

	BILLING = ("Schedule of Values", "Schedule of Values Line", "Pay Application", "Pay Application Line", "Retainage Step")

	def test_no_cost_and_no_markup_at_all(self):
		# Absent, not hidden (TASK-2026-02117): a field that exists is one export away from a GC.
		for name in self.BILLING:
			for field in doctype_json(name)["fields"]:
				fn = field["fieldname"]
				if fn == "cost_type":
					continue  # a classification of the work, carries no money
				for word in ("cost", "markup", "margin", "commission", "contingency", "insurance", "fee", "labor_rate"):
					self.assertNotIn(word, fn, f"{name}.{fn}")
				if fn in (
					"certified_less",
					"amount_certified",
					"work_retainage_override",
					"stored_retainage_override",
					"retainage_release",  # B9: releasing retainage is accounting's
				):
					self.assertEqual(field.get("permlevel"), 1, fn)  # accounting's to write
				else:
					self.assertEqual(field.get("permlevel", 0), 0, f"{name}.{fn}")

	def test_only_accounting_certifies(self):
		perms = doctype_json("Pay Application")["permissions"]
		submit = {p["role"] for p in perms if p.get("submit")}
		self.assertEqual(submit, {"System Manager", "Finance Team", "Executive Team", "Accounts Manager"})
		writers = {p["role"] for p in perms if p.get("write") and not p.get("permlevel")}
		self.assertTrue({"Projects Manager", "Project Manager"} <= writers)
		certifiers = {p["role"] for p in perms if p.get("write") and p.get("permlevel") == 1}
		self.assertEqual(certifiers, submit)

	def test_controllers_write_only_real_fields(self):
		src = open(os.path.join(DOCTYPES, "pay_application", "pay_application.py")).read()
		parent = {f["fieldname"] for f in doctype_json("Pay Application")["fields"]}
		child = {f["fieldname"] for f in doctype_json("Pay Application Line")["fields"]}
		for const, fields in (("LINE_RESULTS", child), ("LINE_SYNC", child), ("TOTALS", parent)):
			block = src.split(f"{const} = (")[1].split(")")[0]
			for fn in [x.strip().strip('",') for x in block.replace(",", "\n").split("\n") if x.strip().strip('", ')]:
				self.assertIn(fn.strip('" '), fields, f"{const}: {fn}")

	def test_the_engine_totals_are_all_stored(self):
		from erpnext_enhancements.estimating import pay_app_math as pm

		priced = pm.price_application({"contract_sum": 1}, {}, [{"line_key": "a", "scheduled_value": 1}], {})
		parent = {f["fieldname"] for f in doctype_json("Pay Application")["fields"]}
		child = {f["fieldname"] for f in doctype_json("Pay Application Line")["fields"]}
		self.assertTrue(set(priced["totals"]) <= parent, set(priced["totals"]) - parent)
		self.assertTrue(set(priced["lines"][0]) <= child, set(priced["lines"][0]) - child)


if __name__ == "__main__":
	unittest.main()
