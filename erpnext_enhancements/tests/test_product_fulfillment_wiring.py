"""Build, test and ship a product unit: the wiring (v1.826.0, TASK-2026-03229 / -03230).

``test_product_fulfillment_rules.py`` holds the decisions. This suite holds the pieces around them
that fail without a word:

* the schema: the step fields a phone ticks on a SUBMITTED Work Order must be allow-on-submit on
  both the table and the row, or the Desk form refuses to show a tick it did not make; the Quality
  Inspection's Built By must fetch from the serial; the warehouses must default to empty (Nikolas
  picks the Build Room shelves later) while the serial series, the warranty and the test template
  carry defaults that match the code and the seed patch;
* the seed template: its acceptance formula must accept Pass and N/A and nothing else, and the
  control voltage window must be 24 VDC within ten percent;
* the label and the certificate: rendered, a draft must say it is not signed and a failure must say
  FAILED, so neither can pass for a certificate in the box;
* the glue: ``qc_gate.delivery_note_before_submit`` refuses a Delivery Note with the gate's
  sentences, leaves a return and a switched-off gate alone, and ``build.plan_and_make`` turns the
  plan into Work Orders (one per unit), shelf picks and drafts with the missing warehouse named.

The glue runs on a ``frappe`` stub installed in ``setUpModule`` (its own process in CI), with the
database helpers of the modules under test replaced, so nothing here needs a bench.

Run: python -m unittest erpnext_enhancements.tests.test_product_fulfillment_wiring
"""

import ast
import importlib
import itertools
import json
import re
import sys
import types
import unittest
from pathlib import Path

import jinja2

APP = Path(__file__).resolve().parents[1]
PF = APP / "product_fulfillment"
FIXTURES = APP / "fixtures"

_saved = {}
_STUBBED = ("frappe", "frappe.utils", "frappe.model", "frappe.model.document")


def _load(path):
	return json.loads(Path(path).read_text(encoding="utf-8"))


def _fields(doctype_json):
	return {f["fieldname"]: f for f in _load(doctype_json)["fields"]}


def _custom_fields():
	return {r["name"]: r for r in _load(FIXTURES / "custom_field.json")}


class Thrown(Exception):
	pass


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as error:
			raise AttributeError(key) from error


def setUpModule():
	for name in _STUBBED:
		_saved[name] = sys.modules.get(name)
	frappe = types.ModuleType("frappe")
	frappe._ = lambda text, *a, **k: text
	frappe._dict = _Dict
	frappe.bold = lambda text: f"<b>{text}</b>"

	def throw(message, exc=None, title=None, **kwargs):
		raise Thrown(message)

	frappe.throw = throw
	frappe.msgprint = lambda *a, **k: frappe.messages.append(a[0] if a else k.get("msg"))
	frappe.messages = []
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda *a, **k: "Traceback"
	frappe.whitelist = lambda *a, **k: (lambda fn: fn) if not (a and callable(a[0])) else a[0]
	frappe.PermissionError = PermissionError
	frappe.DoesNotExistError = LookupError
	frappe.session = types.SimpleNamespace(user="nathan.cox@example.com")
	frappe.db = types.SimpleNamespace(savepoint=lambda *a, **k: None, rollback=lambda *a, **k: None)
	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v=0, *a, **k: int(float(v or 0))
	utils.flt = lambda v=0, *a, **k: float(v or 0)
	utils.nowdate = lambda: "2026-11-18"
	utils.now_datetime = lambda: "2026-11-18 12:00:00"
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = object
	frappe.utils = utils
	sys.modules.update(
		{"frappe": frappe, "frappe.utils": utils, "frappe.model": model, "frappe.model.document": document}
	)
	global qc_gate, build, serials, frappe_stub
	frappe_stub = frappe
	for name in (
		"erpnext_enhancements.product_fulfillment.settings",
		"erpnext_enhancements.product_fulfillment.serials",
		"erpnext_enhancements.product_fulfillment.qc_gate",
		"erpnext_enhancements.product_fulfillment.build",
	):
		sys.modules.pop(name, None)
	qc_gate = importlib.import_module("erpnext_enhancements.product_fulfillment.qc_gate")
	build = importlib.import_module("erpnext_enhancements.product_fulfillment.build")
	serials = importlib.import_module("erpnext_enhancements.product_fulfillment.serials")


def tearDownModule():
	for name in (
		"erpnext_enhancements.product_fulfillment.settings",
		"erpnext_enhancements.product_fulfillment.serials",
		"erpnext_enhancements.product_fulfillment.qc_gate",
		"erpnext_enhancements.product_fulfillment.build",
	):
		sys.modules.pop(name, None)
	for name, module in _saved.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


# ---------------------------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------------------------


class TestSchema(unittest.TestCase):
	def test_the_settings_warehouses_default_to_empty(self):
		fields = _fields(PF / "doctype" / "product_build_settings" / "product_build_settings.json")
		for name in ("build_warehouse", "wip_warehouse", "finished_warehouse"):
			self.assertNotIn("default", fields[name], name)
			self.assertEqual(fields[name]["options"], "Warehouse")

	def test_the_settings_defaults_match_the_code_and_the_seed(self):
		from erpnext_enhancements.product_fulfillment import rules

		fields = _fields(PF / "doctype" / "product_build_settings" / "product_build_settings.json")
		self.assertEqual(fields["serial_no_series"]["default"], rules.DEFAULT_SERIAL_SERIES)
		self.assertEqual(int(fields["warranty_days"]["default"]), rules.DEFAULT_WARRANTY_DAYS)
		self.assertEqual(fields["require_test"]["default"], "1")
		self.assertEqual(fields["make_work_orders"]["default"], "1")
		patch = (APP / "patches" / "seed_estop_pre_shipment_test.py").read_text(encoding="utf-8")
		template = re.search(r'^TEMPLATE = "([^"]+)"', patch, re.M).group(1)
		self.assertEqual(fields["test_template"]["default"], template)
		settings_py = (PF / "settings.py").read_text(encoding="utf-8")
		self.assertIn(
			f'"test_template": "{template}"', settings_py, "the not-installed fallback names it too"
		)

	def test_only_a_system_manager_can_switch_the_test_off(self):
		# Manager review of #1470: either builder holds Manufacturing Manager, which writes these settings.
		data = _load(PF / "doctype" / "product_build_settings" / "product_build_settings.json")
		fields = {f["fieldname"]: f for f in data["fields"]}
		for name in ("require_test", "test_template"):
			self.assertEqual(fields[name].get("permlevel"), 1, name)
		level1 = [p for p in data["permissions"] if p.get("permlevel") == 1]
		self.assertEqual([p["role"] for p in level1 if p.get("write")], ["System Manager"])
		self.assertTrue(all(p.get("read") for p in level1))
		self.assertEqual(data.get("track_changes"), 1, "who changed a switch is on the record")
		other = [
			f
			for f in data["fields"]
			if f.get("permlevel") and f["fieldname"] not in ("require_test", "test_template")
		]
		self.assertEqual(other, [])

	def test_the_get_endpoints_take_get_only_and_their_callers_ask_with_get(self):
		# The repo's whitelist ratchet (security batch #1478) wants methods= on every endpoint.
		for path, func in (("checklist.py", "get_build"), ("qc_gate.py", "delivery_gate_status")):
			tree = ast.parse((PF / path).read_text(encoding="utf-8"))
			node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == func)
			methods = [
				kw.value
				for d in node.decorator_list
				if isinstance(d, ast.Call)
				for kw in d.keywords
				if kw.arg == "methods"
			]
			self.assertEqual([ast.literal_eval(m) for m in methods], [["GET"]], func)
		page = (PF / "page" / "build_checklist" / "build_checklist.js").read_text(encoding="utf-8")
		self.assertIn('this.call("checklist.get_build", { work_order: asked }, "GET")', page)
		form = (APP / "public" / "js" / "product_fulfillment" / "delivery_note.js").read_text(
			encoding="utf-8"
		)
		self.assertIn('type: "GET"', form)

	def test_ticks_on_a_submitted_work_order_are_allowed_on_submit(self):
		step = _fields(PF / "doctype" / "work_order_build_step" / "work_order_build_step.json")
		for name in ("done", "done_by", "done_at"):
			self.assertEqual(step[name].get("allow_on_submit"), 1, name)
		fields = _custom_fields()
		table = fields["Work Order-custom_build_steps"]
		self.assertEqual(
			(table["fieldtype"], table["options"], table["allow_on_submit"]),
			("Table", "Work Order Build Step", 1),
		)
		self.assertEqual(fields["Work Order-custom_built_by"]["allow_on_submit"], 1)
		self.assertEqual(
			fields["Work Order-custom_built_by"]["no_copy"], 1, "a copied Work Order is a new unit"
		)

	def test_the_tests_built_by_comes_from_the_serial(self):
		fields = _custom_fields()
		self.assertEqual(
			fields["Quality Inspection-custom_built_by"]["fetch_from"], "item_serial_no.custom_built_by"
		)
		self.assertEqual(fields["Serial No-custom_built_by"]["read_only"], 1, "set by the build, not typed")

	def test_the_new_fields_chain_in_order_on_the_work_order(self):
		fields = _custom_fields()
		chain = [
			"batch_size",
			"custom_build_tab",
			"custom_built_by",
			"custom_build_configuration",
			"custom_build_steps",
		]
		for before, after in itertools.pairwise(chain):
			self.assertEqual(fields[f"Work Order-{after}"]["insert_after"], before)

	def test_the_child_table_and_settings_live_in_the_module(self):
		modules = (APP / "modules.txt").read_text(encoding="utf-8").splitlines()
		self.assertIn("Product Fulfillment", modules)
		for name in ("product_build_settings", "work_order_build_step"):
			self.assertEqual(_load(PF / "doctype" / name / f"{name}.json")["module"], "Product Fulfillment")

	def test_the_build_checklist_page_matches_its_folder(self):
		page = _load(PF / "page" / "build_checklist" / "build_checklist.json")
		self.assertEqual(
			(page["name"], page["page_name"], page["module"]),
			("build-checklist", "build-checklist", "Product Fulfillment"),
		)
		js = (PF / "page" / "build_checklist" / "build_checklist.js").read_text(encoding="utf-8")
		self.assertIn('frappe.pages["build-checklist"]', js)
		for method in (
			"checklist.get_build",
			"checklist.tick",
			"checklist.claim_build",
			"qc_gate.start_test",
		):
			self.assertIn(f'"{method}"', js)

	def test_the_page_uses_only_defined_tokens(self):
		css = (PF / "page" / "build_checklist" / "build_checklist.css").read_text(encoding="utf-8")
		tokens = (APP / "public" / "css" / "sf_tokens.bundle.css").read_text(encoding="utf-8")
		for token in set(re.findall(r"var\((--sf-[a-z0-9-]+)", css)):
			self.assertIn(token + ":", tokens, token)
		bare = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
		for selector in re.findall(r"^([^\s{}][^{}]*)\{", bare, re.M):
			self.assertTrue(selector.strip().startswith(".pfb-"), selector)


# ---------------------------------------------------------------------------------------------
# The seed template
# ---------------------------------------------------------------------------------------------


class TestTheSeedTemplate(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		source = (APP / "patches" / "seed_estop_pre_shipment_test.py").read_text(encoding="utf-8")
		tree = ast.parse(source)
		cls.values = {}
		for node in tree.body:
			if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
				if node.targets[0].id in ("CHECKS", "PASS_OR_NA", "TEMPLATE"):
					cls.values[node.targets[0].id] = ast.literal_eval(node.value)

	def test_pass_or_na_accepts_exactly_those(self):
		formula = self.values["PASS_OR_NA"]
		for reading, accepted in (("Pass", True), ("N/A", True), ("Fail", False), ("", False), (None, False)):
			self.assertEqual(
				bool(eval(formula, {"__builtins__": {}}, {"reading_value": reading})), accepted, reading
			)

	def test_the_control_voltage_is_24_vdc_within_ten_percent(self):
		ranges = [c for c in self.values["CHECKS"] if c[2] == "range"]
		self.assertEqual(len(ranges), 1)
		low, high = ranges[0][3]
		self.assertAlmostEqual(low, 24 * 0.9)
		self.assertAlmostEqual(high, 24 * 1.1)

	def test_checks_are_unique_and_named_for_the_product(self):
		names = [c[0] for c in self.values["CHECKS"]]
		self.assertEqual(len(names), len(set(names)))
		self.assertTrue(all(n.startswith("E-Stop - ") for n in names))
		self.assertTrue(all(len(n) <= 140 for n in names))

	def test_the_tidied_readings_are_what_the_formula_accepts(self):
		for typed in ("pass", " PASS ", "ok", "na", "n/a", "Not applicable"):
			self.assertIn(qc_gate._tidy_reading(typed), ("Pass", "N/A"), typed)
		self.assertEqual(qc_gate._tidy_reading("failed"), "Fail")
		self.assertEqual(qc_gate._tidy_reading("24.1"), "24.1")


# ---------------------------------------------------------------------------------------------
# The label and the certificate
# ---------------------------------------------------------------------------------------------


class _FakeFrappe:
	"""The handful of things the two print formats call, the names print Jinja exposes at v16.56.0
	(``frappe.get_fullname`` and ``frappe.db.get_value``; dates go through the ``ps_date`` global)."""

	def __init__(self):
		self.db = types.SimpleNamespace(
			get_value=lambda doctype, name, field: {"item_name": "STILLWATER E-Stop"}.get(field)
		)

	@staticmethod
	def get_fullname(user):
		return {"logan@example.com": "Logan Penrod", "nathan@example.com": "Nathan Cox"}.get(user, user)


class _Doc(_Dict):
	def get(self, key, default=None):
		return dict.get(self, key, default)


def _render(template, doc):
	source = (PF / "setup_print_formats.py").read_text(encoding="utf-8")
	tree = ast.parse(source)
	node = next(n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == template)
	value = node.value
	if isinstance(value, ast.Call):  # """...""".strip()
		value = value.func.value
	html = ast.literal_eval(value)
	env = jinja2.Environment(autoescape=False)
	# ps_date is the Jinja global every print format reaches dates through (print_style.ps_date).
	return env.from_string(html.strip()).render(
		doc=doc, frappe=_FakeFrappe(), ps_date=lambda value: f"date:{value}"
	)


def _certificate_doc(docstatus=1, status="Accepted"):
	reading = _Doc(
		specification="E-Stop - Buzzer sounds while an e-stop is latched",
		numeric=0,
		value="Pass",
		formula_based_criteria=0,
		reading_value="Pass",
		status="Accepted",
		reading_1=None,
		min_value=0,
		max_value=0,
	)
	volts = _Doc(
		specification="E-Stop - Control voltage at the terminals (VDC)",
		numeric=1,
		value=None,
		formula_based_criteria=0,
		reading_value=None,
		reading_1="24.2",
		status="Accepted",
		min_value=21.6,
		max_value=26.4,
	)
	return _Doc(
		name="MAT-QA-2026-00001",
		docstatus=docstatus,
		status=status,
		item_code="PDT-0040-1-1-1-2-0",
		item_serial_no="SF-26-00001",
		custom_built_by="logan@example.com",
		inspected_by="nathan@example.com",
		report_date="2026-11-18",
		quality_inspection_template="Pre-Shipment Functional Test - E-Stop",
		readings=[reading, volts],
		remarks=None,
	)


class TestThePrintFormats(unittest.TestCase):
	def test_the_label_carries_the_serial_and_the_part_number(self):
		html = _render(
			"LABEL_HTML",
			_Doc(name="SF-26-00001", item_code="PDT-0040-1-1-1-2-0", item_name="STILLWATER E-Stop"),
		)
		self.assertIn("SF-26-00001", html)
		self.assertIn("PDT-0040-1-1-1-2-0", html)

	def test_a_passed_certificate_names_both_people_and_the_warranty(self):
		html = _render("CERTIFICATE_HTML", _certificate_doc())
		for text in (
			"Result: PASSED",
			"Logan Penrod",
			"Nathan Cox",
			"SF-26-00001",
			"21.6 to 26.4",
			"24.2",
			"One year, parts and labor",
		):
			self.assertIn(text, html)
		self.assertNotIn("NOT SIGNED", html)

	def test_a_draft_says_it_is_not_signed(self):
		html = _render("CERTIFICATE_HTML", _certificate_doc(docstatus=0))
		self.assertIn("DRAFT - NOT SIGNED", html)
		self.assertNotIn("Result: PASSED", html)

	def test_a_failure_says_failed(self):
		html = _render("CERTIFICATE_HTML", _certificate_doc(status="Rejected"))
		self.assertIn("Result: FAILED", html)
		self.assertNotIn("Result: PASSED", html)


# ---------------------------------------------------------------------------------------------
# The glue, on a stub
# ---------------------------------------------------------------------------------------------

CONFIGURED = {
	"is_stock_item": 1,
	"has_serial_no": 1,
	"source_configuration": "PCFG-2026-00001",
	"default_bom": "BOM-1",
}
LOGAN = "logan.penrod@example.com"
NATHAN = "nathan.cox@example.com"


def _qi(status, by, day="2026-11-18"):
	return _Dict(
		name=f"QI-{status}-{by}",
		status=status,
		inspected_by=by,
		docstatus=1,
		report_date=day,
		creation=day + " 09:00:00",
		quality_inspection_template="Pre-Shipment Functional Test - E-Stop",
		item_code="PDT-0040-1-1-1-2-0",
	)


class _Row(_Dict):
	def get(self, key, default=None):
		return dict.get(self, key, default)


def _dn(rows, is_return=0):
	return _Row(name="DN-1", is_return=is_return, items=[_Row(r) for r in rows])


class TestTheGateOnAStub(unittest.TestCase):
	def setUp(self):
		self.settings = _Dict(require_test=1, test_template="Pre-Shipment Functional Test - E-Stop")
		self.facts = {
			"PDT-0040-1-1-1-2-0": dict(CONFIGURED),
			"SRV-105": {"is_stock_item": 0, "source_configuration": None},
		}
		self.tests = {}
		self.builders = {}
		qc_gate.get_settings = lambda: self.settings
		qc_gate.item_facts = lambda codes: {c: self.facts[c] for c in codes if c in self.facts}
		qc_gate.tests_by_serial = lambda serials: {s: self.tests.get(s, []) for s in serials}
		qc_gate.builders_of = lambda serials: {s: self.builders.get(s) for s in serials}
		serials.serials_of_row = lambda row: [s for s in (row.get("serial_no") or "").split("\n") if s]

	def line(self, serial_no="SF-26-00001", qty=1, idx=1, item="PDT-0040-1-1-1-2-0"):
		return {"idx": idx, "item_code": item, "stock_qty": qty, "qty": qty, "serial_no": serial_no}

	def test_a_passed_unit_ships(self):
		self.tests["SF-26-00001"] = [_qi("Accepted", NATHAN)]
		self.builders["SF-26-00001"] = LOGAN
		qc_gate.delivery_note_before_submit(
			_dn([self.line(), self.line(idx=2, item="SRV-105", serial_no="")])
		)

	def test_an_untested_unit_is_refused_with_the_reason(self):
		self.builders["SF-26-00001"] = LOGAN
		with self.assertRaises(Thrown) as caught:
			qc_gate.delivery_note_before_submit(_dn([self.line()]))
		self.assertIn(
			"Row 1: unit SF-26-00001 has no submitted Pre-Shipment Functional Test", str(caught.exception)
		)

	def test_a_unit_tested_by_its_builder_is_refused(self):
		self.tests["SF-26-00001"] = [_qi("Accepted", LOGAN)]
		self.builders["SF-26-00001"] = LOGAN
		with self.assertRaises(Thrown) as caught:
			qc_gate.delivery_note_before_submit(_dn([self.line()]))
		self.assertIn("tested by the person who built it", str(caught.exception))

	def test_no_serial_picked_is_refused(self):
		with self.assertRaises(Thrown) as caught:
			qc_gate.delivery_note_before_submit(_dn([self.line(serial_no="")]))
		self.assertIn("pick the serial number of each unit", str(caught.exception))

	def test_a_configured_item_without_serial_numbers_is_refused(self):
		self.facts["PDT-0040-1-1-1-2-0"]["has_serial_no"] = 0
		with self.assertRaises(Thrown) as caught:
			qc_gate.delivery_note_before_submit(_dn([self.line(serial_no="")]))
		self.assertIn("has no serial numbers", str(caught.exception))

	def test_a_unit_inside_a_product_bundle_is_checked_too(self):
		self.builders["SF-26-00009"] = LOGAN
		doc = _dn([self.line(item="SRV-105", serial_no="")])
		doc["packed_items"] = [_Row(idx=1, item_code="PDT-0040-1-1-1-2-0", qty=1, serial_no="SF-26-00009")]
		with self.assertRaises(Thrown) as caught:
			qc_gate.delivery_note_before_submit(doc)
		self.assertIn(
			"Packed item 1: unit SF-26-00009 has no submitted Pre-Shipment Functional Test",
			str(caught.exception),
		)

	def test_a_return_and_a_switched_off_gate_pass(self):
		qc_gate.delivery_note_before_submit(_dn([self.line()], is_return=1))
		self.settings.require_test = 0
		qc_gate.delivery_note_before_submit(_dn([self.line()]))

	def test_a_note_with_no_configured_product_passes(self):
		qc_gate.delivery_note_before_submit(_dn([self.line(item="SRV-105", serial_no="")]))


class TestThePlanOnAStub(unittest.TestCase):
	def setUp(self):
		self.made = []
		self.on_hand = 0
		self.claims = []
		self.planned = []
		self.facts = {"PDT-0040-1-1-1-2-0": dict(CONFIGURED)}
		build.item_facts = lambda codes: {c: self.facts[c] for c in codes if c in self.facts}
		build.manufacturing_defaults = lambda: {}
		build._line_work_orders = lambda so_item: list(self.planned)
		build._on_hand = lambda item, warehouse: self.on_hand
		build._claims = lambda item, warehouse, order: list(self.claims)

		def make(doc, row, bom, warehouses, ignore_permissions):
			wo = _Dict(name=f"WO-{len(self.made) + 1}", docstatus=1 if warehouses["can_submit"] else 0)
			self.made.append((row.item_code, bom, warehouses["fg"]))
			return wo

		build._make_work_order = make

	def order(self, qty=1, warehouse="Finished Goods - SF"):
		row = _Row(
			name="SOI-1",
			item_code="PDT-0040-1-1-1-2-0",
			stock_qty=qty,
			delivered_qty=0,
			conversion_factor=1,
			warehouse=warehouse,
		)
		return _Row(name="SO-1", company="Sapphire", items=[row])

	def test_nothing_on_the_shelf_makes_one_work_order_per_unit(self):
		result = build.plan_and_make(self.order(qty=2), _Dict(wip_warehouse="WIP - SF"))
		self.assertEqual(len(self.made), 2)
		self.assertEqual(result["submitted"], ["WO-1", "WO-2"])
		self.assertEqual(self.made[0], ("PDT-0040-1-1-1-2-0", "BOM-1", "Finished Goods - SF"))

	def test_a_built_ahead_unit_ships_from_the_shelf(self):
		self.on_hand = 2
		result = build.plan_and_make(self.order(qty=1), _Dict(wip_warehouse="WIP - SF"))
		self.assertEqual(self.made, [])
		self.assertEqual(result["from_stock"], [{"item_code": "PDT-0040-1-1-1-2-0", "qty": 1}])

	def test_a_unit_promised_to_another_order_is_not_on_the_shelf(self):
		self.on_hand = 1
		self.claims = [1]
		build.plan_and_make(self.order(qty=1), _Dict(wip_warehouse="WIP - SF"))
		self.assertEqual(len(self.made), 1)

	def test_planning_again_builds_nothing_twice(self):
		self.planned = [{"status": "Not Started", "docstatus": 1, "qty": 1}]
		build.plan_and_make(self.order(qty=1), _Dict(wip_warehouse="WIP - SF"))
		self.assertEqual(self.made, [])

	def test_without_a_wip_warehouse_the_work_orders_are_drafts_and_say_why(self):
		result = build.plan_and_make(self.order(qty=1), _Dict())
		self.assertEqual(result["drafts"], ["WO-1"])
		self.assertEqual(result["missing"], ["work in progress"])

	def test_no_default_bom_builds_nothing_and_says_so(self):
		self.facts["PDT-0040-1-1-1-2-0"]["default_bom"] = None
		result = build.plan_and_make(self.order(qty=1), _Dict(wip_warehouse="WIP - SF"))
		self.assertEqual((self.made, result["missing_bom"]), ([], ["PDT-0040-1-1-1-2-0"]))

	def test_two_lines_of_one_order_share_the_shelf_in_turn(self):
		# Review of #1470: both lines used to take the same free unit.
		self.on_hand = 1
		row_a = _Row(
			name="SOI-1",
			item_code="PDT-0040-1-1-1-2-0",
			stock_qty=1,
			delivered_qty=0,
			conversion_factor=1,
			warehouse="FG",
		)
		row_b = _Row(
			name="SOI-2",
			item_code="PDT-0040-1-1-1-2-0",
			stock_qty=1,
			delivered_qty=0,
			conversion_factor=1,
			warehouse="FG",
		)
		result = build.plan_and_make(
			_Row(name="SO-1", company="Sapphire", items=[row_a, row_b]), _Dict(wip_warehouse="WIP - SF")
		)
		self.assertEqual(result["from_stock"], [{"item_code": "PDT-0040-1-1-1-2-0", "qty": 1}])
		self.assertEqual(len(self.made), 1)

	def test_an_item_without_serial_numbers_is_not_built(self):
		self.facts["PDT-0040-1-1-1-2-0"]["has_serial_no"] = 0
		build._ensure_serial_numbers = lambda item: False
		try:
			result = build.plan_and_make(self.order(qty=1), _Dict(wip_warehouse="WIP - SF"))
		finally:
			del build._ensure_serial_numbers
		self.assertEqual((self.made, result["no_serial"]), ([], ["PDT-0040-1-1-1-2-0"]))

	def test_an_item_whose_serial_numbers_can_still_be_turned_on_is_built(self):
		self.facts["PDT-0040-1-1-1-2-0"]["has_serial_no"] = 0
		build._ensure_serial_numbers = lambda item: True
		try:
			build.plan_and_make(self.order(qty=1), _Dict(wip_warehouse="WIP - SF"))
		finally:
			del build._ensure_serial_numbers
		self.assertEqual(len(self.made), 1)

	def test_a_draft_left_without_warehouses_is_started_once_they_are_set(self):
		# Manager review of #1470: ERPNext's set_default_warehouse reads only the Company's defaults, so a
		# draft never picks up Product Build Settings by itself. Planning again fills and starts it.
		self.planned = [_Dict(name="WO-7", status="Draft", docstatus=0, qty=1)]
		started = []
		build._start_draft = (
			lambda name, warehouses, ignore: started.append((name, warehouses["wip"])) or True
		)
		try:
			result = build.plan_and_make(self.order(qty=1), _Dict(wip_warehouse="WIP - SF"))
		finally:
			del build._start_draft
		self.assertEqual(started, [("WO-7", "WIP - SF")])
		self.assertEqual((self.made, result["submitted"]), ([], ["WO-7"]))

	def test_a_draft_is_left_alone_while_the_warehouses_are_missing(self):
		self.planned = [_Dict(name="WO-7", status="Draft", docstatus=0, qty=1)]
		build._start_draft = lambda *a: self.fail("started without warehouses")
		try:
			result = build.plan_and_make(self.order(qty=1), _Dict())
		finally:
			del build._start_draft
		self.assertEqual(result["submitted"], [])

	def test_the_draft_message_names_the_real_fix(self):
		source = (PF / "build.py").read_text(encoding="utf-8")
		self.assertIn("press Create > Product Work Orders on this order", source)

	def test_a_line_that_is_not_configured_is_left_to_erpnext(self):
		self.facts["PDT-0040-1-1-1-2-0"]["source_configuration"] = None
		result = build.plan_and_make(self.order(qty=1), _Dict(wip_warehouse="WIP - SF"))
		self.assertEqual((self.made, result["created"]), ([], []))


class TestTheBuilderStampOnAStub(unittest.TestCase):
	"""Review of #1470: a series-made serial is written to the Stock Entry row with db.set_value while
	the ledger posts, so the document the on_submit hook is handed has no serial on it."""

	def setUp(self):
		self.set_values = []
		self.db_rows = [_Dict(serial_no=None, serial_and_batch_bundle="SABB-1")]
		frappe_stub.db.set_value = lambda doctype, name, field, value=None, **k: self.set_values.append(
			(doctype, name, field if isinstance(field, dict) else {field: value})
		)
		frappe_stub.db.get_value = (
			lambda doctype, name, field, *a, **k: "logan.penrod@example.com"
			if field == "custom_built_by"
			else None
		)
		frappe_stub.get_all = lambda doctype, filters=None, fields=None, **k: list(self.db_rows)
		self.saved = (serials.has_column, serials.serials_of_row, serials._warn_unfinished_checklist)
		serials.has_column = lambda doctype, column: True
		serials.serials_of_row = (
			lambda row: ["SF-26-00001"] if row.get("serial_and_batch_bundle") == "SABB-1" else []
		)
		serials._warn_unfinished_checklist = lambda wo: None

	def tearDown(self):
		serials.has_column, serials.serials_of_row, serials._warn_unfinished_checklist = self.saved

	def test_the_serial_is_read_from_the_database_rows(self):
		doc = _Row(
			name="STE-1",
			purpose="Manufacture",
			work_order="WO-1",
			items=[_Row(is_finished_item=1, serial_no=None, serial_and_batch_bundle=None)],
		)
		serials.stamp_builders(doc)
		stamped = [v for v in self.set_values if v[0] == "Serial No"]
		self.assertEqual(
			stamped,
			[
				(
					"Serial No",
					"SF-26-00001",
					{"custom_built_by": "logan.penrod@example.com", "work_order": "WO-1"},
				)
			],
		)

	def test_only_a_manufacture_entry_against_a_work_order(self):
		serials.stamp_builders(_Row(name="STE-2", purpose="Material Receipt", work_order=None, items=[]))
		self.assertEqual(self.set_values, [])


class TestTheBuilderGuardOnAStub(unittest.TestCase):
	"""Manager review of #1470: Frappe does not enforce read-only on the server, so frappe.client.set_value
	could rewrite Built By. Any change through a document save is refused, but by a System Manager."""

	def setUp(self):
		self.stored = {"WO-1": "logan.penrod@example.com"}
		frappe_stub.db.get_value = lambda doctype, name, field, *a, **k: self.stored.get(name)
		frappe_stub.flags = _Dict()
		frappe_stub.get_roles = lambda *a, **k: ["Manufacturing Manager", "Stock Manager"]
		self.saved = serials.has_column
		serials.has_column = lambda doctype, column: True

	def tearDown(self):
		serials.has_column = self.saved

	def doc(self, name, value, new=False):
		row = _Row(doctype="Work Order", name=name, custom_built_by=value)
		row.is_new = lambda: new
		return row

	def test_a_change_is_refused(self):
		with self.assertRaises(Thrown):
			serials.guard_builder(self.doc("WO-1", "nathan.cox@example.com"))

	def test_clearing_it_is_refused_too(self):
		with self.assertRaises(Thrown):
			serials.guard_builder(self.doc("WO-1", None))

	def test_a_new_record_cannot_arrive_with_one(self):
		with self.assertRaises(Thrown):
			serials.guard_builder(self.doc("WO-NEW", "nathan.cox@example.com", new=True))

	def test_an_unchanged_value_saves(self):
		serials.guard_builder(self.doc("WO-1", "logan.penrod@example.com"))
		serials.guard_builder(self.doc("WO-2", None, new=True))

	def test_a_system_manager_may_correct_it(self):
		frappe_stub.get_roles = lambda *a, **k: ["System Manager"]
		serials.guard_builder(self.doc("WO-1", "nathan.cox@example.com"))

	def test_the_hooks_cover_draft_and_submitted_work_orders_and_serials(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		guard = '"erpnext_enhancements.product_fulfillment.serials.guard_builder"'
		self.assertIn(f'"validate": {guard}', hooks)
		self.assertIn(f'"before_update_after_submit": {guard}', hooks)
		self.assertEqual(
			hooks.count(guard), 3, "Work Order validate and update-after-submit, Serial No validate"
		)


class TestTheSerialLockOnAStub(unittest.TestCase):
	"""Manager review of #1470: ERPNext refuses to change Has Serial No once any submitted Delivery
	Note, Purchase Receipt, Stock Entry or Stock Reconciliation names the Item, not only once the stock
	ledger has a row. Ask ERPNext's own check."""

	def test_erpnexts_own_check_decides(self):
		asked = []
		item = _Row(name="PDT-0040-1-1-1-2-0")
		item._get_linked_submitted_documents = lambda fields: asked.append(fields) or {
			"doctype": "Delivery Note"
		}
		self.assertTrue(serials._serials_locked(item))
		self.assertEqual(asked, [["has_serial_no"]])
		item._get_linked_submitted_documents = lambda fields: None
		self.assertFalse(serials._serials_locked(item))


class TestTheMaterialIssueWarningOnAStub(unittest.TestCase):
	def setUp(self):
		frappe_stub.messages.clear()
		self.saved = (qc_gate.get_settings, qc_gate.item_facts, qc_gate.unit_states, serials.serials_of_row)
		qc_gate.get_settings = lambda: _Dict(require_test=1, test_template=None)
		qc_gate.item_facts = lambda codes: {"PDT-0040-1-1-1-2-0": dict(CONFIGURED)}
		qc_gate.unit_states = lambda serials_, template=None: {"SF-26-00001": {"state": "untested"}}
		serials.serials_of_row = lambda row: ["SF-26-00001"]
		frappe_stub.get_all = lambda *a, **k: [
			_Dict(idx=1, item_code="PDT-0040-1-1-1-2-0", serial_no="SF-26-00001")
		]

	def tearDown(self):
		qc_gate.get_settings, qc_gate.item_facts, qc_gate.unit_states, serials.serials_of_row = self.saved

	def test_an_untested_unit_issued_is_warned_about_not_refused(self):
		qc_gate.warn_untested_issue(_Row(name="STE-9", purpose="Material Issue"))
		self.assertTrue(any("SF-26-00001" in str(m) for m in frappe_stub.messages))

	def test_another_purpose_says_nothing(self):
		qc_gate.warn_untested_issue(_Row(name="STE-9", purpose="Material Transfer"))
		self.assertEqual(frappe_stub.messages, [])


class TestTheInvoiceGateOnAStub(unittest.TestCase):
	def setUp(self):
		self.calls = []
		self.saved = qc_gate.delivery_note_before_submit
		qc_gate.delivery_note_before_submit = lambda doc, method=None: self.calls.append(doc.name)

	def tearDown(self):
		qc_gate.delivery_note_before_submit = self.saved

	def test_an_invoice_that_ships_stock_is_gated(self):
		qc_gate.sales_invoice_before_submit(_Row(name="SINV-1", update_stock=1))
		self.assertEqual(self.calls, ["SINV-1"])

	def test_an_invoice_that_only_bills_is_not(self):
		qc_gate.sales_invoice_before_submit(_Row(name="SINV-2", update_stock=0))
		self.assertEqual(self.calls, [])


class TestClaimingABuildOnAStub(unittest.TestCase):
	"""Review of #1470: the builder is what keeps the builder from testing the unit, so one person
	cannot quietly replace another's name on it."""

	def setUp(self):
		self.checklist = importlib.import_module("erpnext_enhancements.product_fulfillment.checklist")
		self.writes = []
		self.doc = _Row(name="WO-1", custom_built_by="logan.penrod@example.com")
		self.doc.add_comment = lambda *a, **k: None
		self.saved = {
			n: getattr(self.checklist, n)
			for n in ("_load", "_can_edit", "_manufactured", "get_build", "_full_name")
		}
		self.checklist._load = lambda name: self.doc
		self.checklist._can_edit = lambda doc: True
		self.checklist._manufactured = lambda doc: False
		self.checklist.get_build = lambda name: "build"
		self.checklist._full_name = lambda user: user
		frappe_stub.db.set_value = lambda *a, **k: self.writes.append(a)
		frappe_stub.session.user = "nathan.cox@example.com"

	def tearDown(self):
		for name, fn in self.saved.items():
			setattr(self.checklist, name, fn)

	def test_someone_else_cannot_take_it_over(self):
		frappe_stub.get_roles = lambda *a, **k: ["Manufacturing User"]
		with self.assertRaises(Thrown):
			self.checklist.claim_build("WO-1")
		self.assertEqual(self.writes, [])

	def test_a_manufacturing_manager_cannot_either(self):
		# Manager review of #1470: both builders hold Manufacturing Manager.
		frappe_stub.get_roles = lambda *a, **k: ["Manufacturing Manager", "Stock Manager", "Item Manager"]
		with self.assertRaises(Thrown):
			self.checklist.claim_build("WO-1")
		self.assertEqual(self.writes, [])

	def test_a_system_manager_can_correct_it(self):
		frappe_stub.get_roles = lambda *a, **k: ["System Manager"]
		self.assertEqual(self.checklist.claim_build("WO-1"), "build")
		self.assertEqual(self.writes[0][2:4], ("custom_built_by", "nathan.cox@example.com"))

	def test_an_unclaimed_unit_is_anyones(self):
		self.doc["custom_built_by"] = None
		frappe_stub.get_roles = lambda *a, **k: ["Manufacturing User"]
		self.assertEqual(self.checklist.claim_build("WO-1"), "build")


if __name__ == "__main__":
	unittest.main()
