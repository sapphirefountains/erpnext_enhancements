# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests: the visit form matches what the maintenance agreement covers (v1.618.0).

Valley Fair Mall's agreement (2026-10-02) has a daily visit with a weekly schedule in Exhibit A:
the splash pad nozzle filters on Monday, the cartridge filters on Tuesday and Friday, and so
on. A template could only put every line on every visit. Pinned here, through the real code:

* ``visit_days``: the days a line names, written any reasonable way, and refused when a word
  is not a day; a blank or unreadable value keeps the line on every visit;
* the Section controller stores the days one way and refuses a misspelt one where it was typed;
* ``get_visit_payload`` leaves a line off a visit on another weekday, by the visit's own date;
* ``agreement_scope``: the schedule, chosen services and Exhibit A for the wizard's card, no
  rates, and nothing for an agreement that is not a maintenance one or that the user cannot read;
* section 2.4's "not included" lines still match the agreement template word for word;
* the wizard draws the card on the Safety step, escaped.

Stubs a minimal ``frappe`` in ``setUpModule``; runs the real modules.

Run: python -m unittest erpnext_enhancements.tests.test_visit_agreement_match
"""

import datetime
import html
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
RECORD_MODULE = "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record"
SECTION_MODULE = "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_section.sapphire_maintenance_section"
DAYS_MODULE = "erpnext_enhancements.sapphire_maintenance.visit_days"
SCOPE_MODULE = "erpnext_enhancements.sapphire_maintenance.agreement_scope"
STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.model",
	"frappe.model.document",
	RECORD_MODULE,
	SECTION_MODULE,
	DAYS_MODULE,
	SCOPE_MODULE,
)

record_module = None
section_module = None
visit_days = None
agreement_scope = None
STATE = {}
_saved = {}

D = datetime.date
TODAY = D(2026, 10, 13)  # a Tuesday


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class _Document:
	def __init__(self, **fields):
		self.__dict__.update(fields)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


class _Thrown(Exception):
	pass


def _getdate(value=None):
	if value is None:
		return STATE["today"]
	if isinstance(value, D):
		return value
	return D.fromisoformat(str(value)[:10])


def _item(label, days=None, sequence=0, idx=1, **extra):
	return _Dict(label=label, days=days, sequence=sequence, idx=idx, is_mandatory=0, **extra)


def _reset():
	STATE.clear()
	STATE.update(
		{
			"today": TODAY,
			"docs": {},
			"values": {},
			"exists": set(),
			"readable": set(),
			"options": [],
			"errors": [],
		}
	)


def setUpModule():
	global record_module, section_module, visit_days, agreement_scope
	for name in STUBBED:
		_saved[name] = sys.modules.pop(name, None)
	_forget_package_attrs()

	frappe = types.ModuleType("frappe")
	frappe.__dict__["_"] = lambda text, *a, **k: text
	frappe._dict = _Dict
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.get_traceback = lambda: "traceback"
	frappe.log_error = lambda *a, **k: STATE["errors"].append(k.get("title"))

	def _throw(message, *args, **kwargs):
		raise _Thrown(message)

	def _get_doc(doctype, name):
		return STATE["docs"][(doctype, name)]

	def _get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
		if doctype == "Sapphire Reading Range Override":
			return []
		if doctype == "Contract Service Option":
			assert filters["included"] == 1 and filters["parenttype"] == "Project Contract", filters
			rows = [r for r in STATE["options"] if r["parent"] == filters["parent"] and r["included"]]
			return [r[pluck] for r in rows] if pluck else rows
		raise AssertionError(doctype)

	def _get_value(doctype, name, fields=None, as_dict=False, **kwargs):
		if doctype == "Item":
			return _Dict(item_name=name, stock_uom="Nos")
		row = STATE["values"].get((doctype, name))
		if row is None:
			return None
		if as_dict:
			return _Dict({f: row.get(f) for f in fields})
		return row.get(fields)

	frappe.throw = _throw
	frappe.get_doc = _get_doc
	frappe.get_all = _get_all
	frappe.has_permission = lambda doctype, ptype="read", doc=None, **k: doc in STATE["readable"]
	frappe.db = types.SimpleNamespace(
		get_value=_get_value, exists=lambda doctype, name=None: (doctype, name) in STATE["exists"]
	)

	utils = types.ModuleType("frappe.utils")
	utils.flt = lambda v, *a: float(v or 0)
	utils.getdate = _getdate
	utils.nowdate = lambda: str(STATE["today"])
	utils.now_datetime = lambda: datetime.datetime.combine(STATE["today"], datetime.time(9))
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Document
	model.document = document
	frappe.model = model

	sys.modules.update(
		{"frappe": frappe, "frappe.utils": utils, "frappe.model": model, "frappe.model.document": document}
	)
	import importlib

	visit_days = importlib.import_module(DAYS_MODULE)
	agreement_scope = importlib.import_module(SCOPE_MODULE)
	section_module = importlib.import_module(SECTION_MODULE)
	record_module = importlib.import_module(RECORD_MODULE)


def _forget_package_attrs():
	import erpnext_enhancements.sapphire_maintenance as package
	import erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record as record_package
	import erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_section as section_package

	for owner, attr in (
		(package, "visit_days"),
		(package, "agreement_scope"),
		(record_package, "sapphire_maintenance_record"),
		(section_package, "sapphire_maintenance_section"),
	):
		if attr in vars(owner):
			delattr(owner, attr)


def tearDownModule():
	_forget_package_attrs()
	for name in STUBBED:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


class TestVisitDays(unittest.TestCase):
	def test_days_written_any_reasonable_way(self):
		for text, expected in (
			("Tue, Fri", "Tue, Fri"),
			("friday & tuesday", "Tue, Fri"),
			("Mon/Thu", "Mon, Thu"),
			("Tues and Thurs.", "Tue, Thu"),
			("wed;wed", "Wed"),
			("  ", ""),
			(None, ""),
		):
			self.assertEqual(visit_days.normalize_days(text), expected, text)

	def test_ranges_and_group_words(self):
		for text, expected in (
			("Mon-Fri", "Mon, Tue, Wed, Thu, Fri"),
			("mon - wed, sat", "Mon, Tue, Wed, Sat"),
			("Mon to Wed", "Mon, Tue, Wed"),
			("Sat-Mon", "Mon, Sat, Sun"),
			("weekends", "Sat, Sun"),
			("weekdays", "Mon, Tue, Wed, Thu, Fri"),
			# All seven is every visit, stored blank.
			("daily", ""),
			("every day", ""),
			("Mon-Sun", ""),
		):
			self.assertEqual(visit_days.normalize_days(text), expected, text)
		with self.assertRaises(ValueError):
			visit_days.parse_days("Mon-Fry")

	def test_a_day_that_is_not_a_date_keeps_the_line(self):
		"""v16's getdate returns None for "0000-00-00"; the visit must still open."""
		self.assertTrue(visit_days.scheduled_on("Tue", None))

	def test_a_word_that_is_not_a_day_is_refused_and_named(self):
		with self.assertRaises(ValueError) as caught:
			visit_days.parse_days("Mon, Fryday")
		self.assertEqual(str(caught.exception), "fryday")

	def test_blank_is_every_visit_and_a_named_day_only_that_day(self):
		monday, tuesday = D(2026, 10, 12), D(2026, 10, 13)
		self.assertTrue(visit_days.scheduled_on("", monday))
		self.assertTrue(visit_days.scheduled_on(None, tuesday))
		self.assertTrue(visit_days.scheduled_on("Tue, Fri", tuesday))
		self.assertFalse(visit_days.scheduled_on("Tue, Fri", monday))

	def test_an_unreadable_value_keeps_the_line(self):
		"""A typo saved before the check existed must not drop work from every visit."""
		self.assertTrue(visit_days.scheduled_on("Mondy", D(2026, 10, 13)))

	def test_the_visit_day_is_done_on_then_scheduled_then_today(self):
		_reset()
		self.assertEqual(visit_days.checklist_day("2026-10-12", "2026-10-09"), D(2026, 10, 12))
		self.assertEqual(visit_days.checklist_day(None, "2026-10-09"), D(2026, 10, 9))
		self.assertEqual(visit_days.checklist_day(), TODAY)


class TestSectionDays(unittest.TestCase):
	def _section(self, *days):
		return section_module.SapphireMaintenanceSection(
			section_type="Cleaning Tasks",
			items=[_item(f"line {i}", d, idx=i + 1) for i, d in enumerate(days)],
		)

	def test_days_are_stored_one_way(self):
		section = self._section("friday, tue", "", None)
		section.validate()
		self.assertEqual([row.days for row in section.items], ["Tue, Fri", "", ""])

	def test_a_misspelt_day_is_refused_with_its_row(self):
		section = self._section("Mon", "Wendsday")
		with self.assertRaises(_Thrown) as caught:
			section.validate()
		self.assertIn("Row 2", str(caught.exception))
		self.assertIn("wendsday", str(caught.exception))


class TestPayloadByWeekday(unittest.TestCase):
	def setUp(self):
		_reset()
		contract = _Document(
			name="MNT-1",
			customer="Wasatch",
			project="PRJ-00183",
			visit_shape="Per Site Visit",
			default_template="Daily",
			covered_features=[_Dict(serial_no="FOUNTAIN", template=None, default_warehouse=None)],
		)
		template = _Document(sections=[_Dict(section="Clean"), _Dict(section="Readings")])
		clean = _Document(
			name="Clean",
			section_title="Clean",
			section_type="Cleaning Tasks",
			disabled=0,
			items=[
				_item("Basket filters", "", 1, 1),
				_item("Splash pad nozzle filters", "Mon", 2, 2),
				_item("Cartridge filters", "Tue, Fri", 3, 3),
				_item("Wye strainers", "Tue", 4, 4),
			],
		)
		readings = _Document(
			name="Readings",
			section_title="Readings",
			section_type="Water Chemistry",
			disabled=0,
			items=[
				_item("pH", "", 1, 1, uom="pH", min_value=7.1, max_value=7.9),
				_item("Calcium Hardness", "Fri", 2, 2),
			],
		)
		STATE["docs"].update(
			{
				("Sapphire Maintenance Contract", "MNT-1"): contract,
				("Sapphire Maintenance Template", "Daily"): template,
				("Sapphire Maintenance Section", "Clean"): clean,
				("Sapphire Maintenance Section", "Readings"): readings,
			}
		)

	def _tasks(self, visit_date=None):
		payload = record_module.get_visit_payload(maintenance_contract="MNT-1", visit_date=visit_date)
		return [row["task"] for row in payload["tasks"]], [row["reading"] for row in payload["readings"]]

	def test_tuesday_gets_tuesday_lines_and_every_day_lines(self):
		tasks, readings = self._tasks("2026-10-13")
		self.assertEqual(tasks, ["Basket filters", "Cartridge filters", "Wye strainers"])
		self.assertEqual(readings, ["pH"])

	def test_monday_and_friday(self):
		self.assertEqual(self._tasks("2026-10-12")[0], ["Basket filters", "Splash pad nozzle filters"])
		tasks, readings = self._tasks("2026-10-16")
		self.assertEqual(tasks, ["Basket filters", "Cartridge filters"])
		self.assertEqual(readings, ["pH", "Calcium Hardness"])

	def test_no_date_is_today(self):
		STATE["today"] = D(2026, 10, 12)
		self.assertEqual(self._tasks()[0], ["Basket filters", "Splash pad nozzle filters"])

	def test_the_wizard_builds_the_checklist_for_the_visit_day(self):
		"""First open instantiates the template for the day the work is done, else scheduled."""
		source = (APP / "api/maintenance_visit.py").read_text(encoding="utf-8")
		self.assertIn('visit_date=doc.get("visit_date") or doc.get("scheduled_visit_date")', source)
		desk = (
			APP / "sapphire_maintenance/doctype/sapphire_maintenance_record/sapphire_maintenance_record.js"
		).read_text(encoding="utf-8")
		self.assertIn("visit_date: frm.doc.visit_date || frm.doc.scheduled_visit_date", desk)

	def test_the_desk_form_rebuilds_what_it_built_when_the_weekday_moves(self):
		"""A paper visit keyed in project first and date second must not keep today's lines."""
		desk = (
			APP / "sapphire_maintenance/doctype/sapphire_maintenance_record/sapphire_maintenance_record.js"
		).read_text(encoding="utf-8")
		for field in ("visit_date", "scheduled_visit_date"):
			start = desk.index(f"\t{field}: function (frm) {{")
			self.assertIn('frm.trigger("rebuild_for_day")', desk[start : desk.index("\t},", start)])
		start = desk.index("\trebuild_for_day: function (frm) {")
		body = desk[start : desk.index("\n\t},", start)]
		# Only tables this form built (a weekday was recorded), and only on another weekday.
		self.assertIn("frm._ee_payload_weekday === undefined) return;", body)
		self.assertIn("ee_payload_weekday(frm) !== frm._ee_payload_weekday", body)
		self.assertIn('frm.trigger("populate_from_template")', body)
		start = desk.index("\tpopulate_from_template: function (frm) {")
		fill = desk[start : desk.index("frappe.call({", start)]
		self.assertIn("frm._ee_payload_weekday = ee_payload_weekday(frm);", fill)
		# The same fields, in the same order, as the server's checklist_day.
		self.assertIn(
			"frm.doc.visit_date || frm.doc.scheduled_visit_date || frappe.datetime.get_today()", desk
		)


class TestAgreementScope(unittest.TestCase):
	NAME = "SF-MAINT-2026-0010"

	def setUp(self):
		_reset()
		STATE["exists"].add(("Project Contract", self.NAME))
		STATE["readable"].add(self.NAME)
		STATE["values"][("Project Contract", self.NAME)] = {
			"template_key": "maintenance",
			"visit_frequency": "Custom",
			"custom_visit_schedule": "Daily, 7 days per week",
			"preferred_days": "",
			"preferred_time": "Before 10:00am, daily",
			"startup_month": "May 1st Startup.",
			"winterization_month": "September 30th.",
			"custom_plan_details": "EQUIPMENT ROOM\nDaily: chemical check.",
		}
		STATE["options"] = [
			{"parent": self.NAME, "option_label": "Standard Maintenance Plan", "included": 0},
			{"parent": self.NAME, "option_label": "Winterization (Fall)", "included": 1},
		]

	def test_what_the_card_gets(self):
		scope = agreement_scope.agreement_scope(self.NAME)
		self.assertEqual(scope["schedule"], "Daily, 7 days per week")
		self.assertEqual(scope["time_window"], "Before 10:00am, daily")
		self.assertEqual(scope["startup"], "May 1st Startup.")
		self.assertEqual(scope["services"], ["Winterization (Fall)"])
		self.assertEqual(scope["custom_plan"], "EQUIPMENT ROOM\nDaily: chemical check.")
		self.assertEqual(scope["not_included"], list(agreement_scope.NOT_INCLUDED))

	def test_no_rates_reach_the_card(self):
		for field in agreement_scope._FIELDS:
			self.assertNotIn("rate", field)
			self.assertNotIn("fee", field)
			self.assertNotIn("price", field)

	def test_a_plain_frequency_is_the_schedule(self):
		STATE["values"][("Project Contract", self.NAME)]["visit_frequency"] = "Bi-Weekly"
		self.assertEqual(agreement_scope.agreement_scope(self.NAME)["schedule"], "Bi-Weekly")

	def test_nothing_for_another_kind_of_agreement_or_a_reader_without_access(self):
		self.assertEqual(agreement_scope.agreement_scope(None), {})
		STATE["readable"].clear()
		self.assertEqual(agreement_scope.agreement_scope(self.NAME), {})
		STATE["readable"].add(self.NAME)
		STATE["values"][("Project Contract", self.NAME)]["template_key"] = "rental"
		self.assertEqual(agreement_scope.agreement_scope(self.NAME), {})

	def test_a_failure_leaves_the_card_out_and_logs(self):
		def _boom(*a, **k):
			raise RuntimeError("db gone")

		sys.modules["frappe"].has_permission = _boom
		try:
			self.assertEqual(agreement_scope.agreement_scope(self.NAME), {})
		finally:
			sys.modules["frappe"].has_permission = (
				lambda doctype, ptype="read", doc=None, **k: doc in STATE["readable"]
			)
		self.assertEqual(STATE["errors"], ["Visit wizard: agreement scope failed"])

	def test_not_included_matches_the_agreement_word_for_word(self):
		"""Section 2.4 of the agreement the client signs is what the technician is shown."""
		template = (APP / "templates/contracts/maintenance_services_agreement.html").read_text(
			encoding="utf-8"
		)
		text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", template)))
		start = text.index("Services NOT Included")
		section = text[start : text.index("3. SERVICE SCHEDULE", start)]
		for line in agreement_scope.NOT_INCLUDED:
			self.assertIn(line, section)
		self.assertIn("chemicals are billed in addition to the base rate", text)


class TestWizardCard(unittest.TestCase):
	def setUp(self):
		self.source = (APP / "sapphire_maintenance/page/visit_wizard/visit_wizard.js").read_text(
			encoding="utf-8"
		)

	def _method(self, name):
		start = self.source.index(f"\t{name}() {{")
		return self.source[start : self.source.index("\n\t}\n", start)]

	def test_the_safety_step_draws_the_card(self):
		self.assertIn("this.render_agreement();", self._method("render_safety"))

	def test_every_value_is_escaped(self):
		body = self._method("render_agreement")
		self.assertIn("frappe.utils.escape_html", body)
		# Every interpolation goes through esc(), vz_plain_html() or __(): no raw scope value.
		for match in re.finditer(r"\$\{([^}]*)\}", body):
			expression = match.group(1)
			if expression in ("body",):
				continue
			if expression.startswith("left_out"):
				# The list: each line through esc() in the nested template literal.
				self.assertIn("${esc(text)", expression)
				continue
			self.assertRegex(expression, r"^(esc\(|vz_plain_html\(|__\()", expression)


if __name__ == "__main__":
	unittest.main()
