# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the visit review digest (``sapphire_maintenance/review_digest.py``).

When a visit goes to Pending Review, every answer on it is emailed to the review inbox
(Lisa checks visits before they are billed) and, as their copy, to the technician who
filled it in. Three things are pinned here:

* **when** it sends: on the save that moves the visit INTO Pending Review, never on any
  other save, and on submit only where no workflow is active;
* **what** it says: every answer, worded by the same rules the record itself applies
  (a zero reading is "not recorded", out-of-range is flagged in words, a required row
  left empty is listed);
* that it **never raises**, because a digest that fails to render must not stop a
  technician sending a visit for review.

The template is rendered under a vanilla jinja2 Environment, the same way
``test_email_design`` compiles every email template, so the content is checked in the
HTML a reader gets and not only in the context handed to it.

Stubs a minimal ``frappe`` and ``email_style`` in ``setUpModule`` (execution time, not
import time), in the style of ``test_training_compliance``.

Run: python -m unittest erpnext_enhancements.tests.test_visit_review_digest
"""

import datetime
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

digest = None
STATE = {}
SITE = "https://erp.example.test"
_SAVED_MODULES = {}
_STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.model",
	"frappe.model.workflow",
	"erpnext_enhancements.email_style",
)


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class _Visit(_Dict):
	"""A Sapphire Maintenance Record: attribute access, ``.get`` and the change test."""

	def has_value_changed(self, field):
		return field in STATE["changed"]


def _reset_state():
	STATE.clear()
	STATE.update(
		{
			"flags": {},
			"changed": {"workflow_state"},
			"workflow": "Sapphire Maintenance Workflow",
			"sent": [],
			"rendered": [],
			"errors": [],
			"raise_in": None,
			"users": {
				"austin@example.com": _Dict(full_name="Austin Healey", email="austin@example.com", enabled=1),
				"korben@example.com": _Dict(full_name="Korben Jessop", email="korben@example.com", enabled=0),
			},
			"serials": [
				_Dict(name="SN-EAST", item_name="East Fountain"),
				_Dict(name="SN-WEST", item_name="West Fountain"),
			],
		}
	)
	if "frappe" in sys.modules and hasattr(sys.modules["frappe"], "flags"):
		sys.modules["frappe"].flags.clear()


# ------------------------------------------------------------------ stubs


def _db_get_value(doctype, name, field=None, as_dict=False, **kwargs):
	if doctype == "User":
		user = STATE["users"].get(name)
		if not user:
			return None
		if isinstance(field, (list, tuple)):
			return _Dict({f: user.get(f) for f in field}) if as_dict else [user.get(f) for f in field]
		return user.get(field)
	if doctype == "Project":
		return {"PRJ-0001": "Highlands Plaza"}.get(name)
	if doctype == "Customer":
		return {"CUST-0001": "Highlands Property Co"}.get(name)
	return None


def _get_all(doctype, filters=None, fields=None, **kwargs):
	if doctype == "Serial No":
		wanted = set((filters or {}).get("name", [None, []])[1])
		return [row for row in STATE["serials"] if row.name in wanted]
	return []


def _install_stubs():
	for name in _STUBBED:
		if name in sys.modules:
			_SAVED_MODULES[name] = sys.modules[name]

	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.flags = _Dict()
	frappe.session = _Dict(user="austin@example.com")
	frappe.get_traceback = lambda: "traceback"
	frappe.__dict__["_"] = lambda s, *a, **k: s

	def _log_error(*args, **kwargs):
		STATE["errors"].append(kwargs.get("title") or (args[0] if args else ""))

	frappe.log_error = _log_error

	def _sendmail(**kwargs):
		if STATE.get("raise_in") == "sendmail":
			raise RuntimeError("smtp on fire")
		STATE["sent"].append(kwargs)

	frappe.sendmail = _sendmail
	frappe.db = types.SimpleNamespace(get_value=_db_get_value)
	frappe.get_all = _get_all

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(float(v or 0))
	utils.flt = lambda v: float(v or 0)
	utils.nowdate = lambda: "2026-10-12"
	utils.formatdate = lambda value, *a, **k: str(value)
	utils.get_url = lambda path="": f"{SITE}{path}"
	utils.get_url_to_form = lambda doctype, name: f"{SITE}/app/{doctype.lower().replace(' ', '-')}/{name}"

	def _get_datetime(value):
		if isinstance(value, datetime.datetime):
			return value
		return datetime.datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")

	utils.get_datetime = _get_datetime
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	workflow = types.ModuleType("frappe.model.workflow")
	workflow.get_workflow_name = lambda doctype: STATE["workflow"]
	model.workflow = workflow
	frappe.model = model

	email_style = types.ModuleType("erpnext_enhancements.email_style")

	def _render(template, context=None, **kwargs):
		if STATE.get("raise_in") == "render":
			raise RuntimeError("template on fire")
		STATE["rendered"].append({"template": template, "context": context, "kwargs": kwargs})
		return f"<rendered {context.get('button_label')}>"

	email_style.render = _render

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.workflow"] = workflow
	sys.modules["erpnext_enhancements.email_style"] = email_style
	import erpnext_enhancements

	erpnext_enhancements.email_style = email_style


def setUpModule():
	global digest
	_install_stubs()
	_reset_state()
	sys.modules.pop("erpnext_enhancements.sapphire_maintenance.review_digest", None)
	from erpnext_enhancements.sapphire_maintenance import review_digest

	digest = review_digest


def tearDownModule():
	sys.modules.pop("erpnext_enhancements.sapphire_maintenance.review_digest", None)
	for name in _STUBBED:
		sys.modules.pop(name, None)
	sys.modules.update(_SAVED_MODULES)
	import erpnext_enhancements

	if "erpnext_enhancements.email_style" in _SAVED_MODULES:
		erpnext_enhancements.email_style = _SAVED_MODULES["erpnext_enhancements.email_style"]
	elif hasattr(erpnext_enhancements, "email_style"):
		del erpnext_enhancements.email_style


# ------------------------------------------------------------------ fixtures


def _visit(**overrides):
	"""A single-feature visit with one of everything, ready for review."""
	fields = {
		"doctype": "Sapphire Maintenance Record",
		"name": "SMR-0001",
		"docstatus": 0,
		"workflow_state": "Pending Review",
		"project": "PRJ-0001",
		"customer": "CUST-0001",
		"serial_no": "SN-EAST",
		"technician": "austin@example.com",
		"crew": [],
		"visit_date": "2026-10-12",
		"clock_in_time": "2026-10-12 08:05:00",
		"clock_out_time": "2026-10-12 10:35:00",
		"paused_duration": 900,
		"completion_percent": 87.5,
		"safety_acknowledged": 1,
		"client_sign_off": "data:image/png;base64,AAAA",
		"visit_notes": "Pump was <b>loud</b> on arrival.\nCleared the intake.",
		"chemistry_readings": [
			_Dict(
				reading="pH",
				reading_value=7.4,
				uom="",
				min_value=7.2,
				max_value=7.8,
				section_title="Water Chemistry",
			),
			_Dict(
				reading="Free chlorine",
				reading_value=6.5,
				uom="ppm",
				min_value=1,
				max_value=3,
				out_of_range=1,
				notes="Shocked yesterday",
				photo="/private/files/chlorine strip.jpg",
				section_title="Water Chemistry",
			),
			_Dict(
				reading="Alkalinity",
				reading_value=0,
				uom="ppm",
				max_value=120,
				is_mandatory=1,
				section_title="Water Chemistry",
			),
		],
		"cleaning_tasks": [
			_Dict(task="Skim surface", is_done=1, section_title="Cleaning"),
			_Dict(task="Brush walls", is_done=0, notes="Ran out of time", section_title="Cleaning"),
		],
		"maintenance_results": [
			_Dict(question="Pump pressure", selection="Pass", answer="", section_title="Pump Inspection"),
			_Dict(
				question="Nozzles",
				selection="Replace",
				answer="Two cracked",
				other_details="Ordered",
				photo="javascript:alert(1)",
				section_title="Pump Inspection",
			),
			_Dict(question="Lights", selection="", answer="", is_mandatory=1, section_title="Lighting"),
		],
		"consumables": [
			_Dict(item="CHEM-CL", item_name="Chlorine tabs", qty=2, uom="ea"),
			_Dict(item="CHEM-PH", item_name="pH down", qty=0, uom="L"),
		],
	}
	fields.update(overrides)
	return _Visit(fields)


def _site_visit():
	"""A Per Site Visit record: no header serial, every row tagged with its feature."""
	return _visit(
		serial_no=None,
		chemistry_readings=[
			_Dict(reading="pH", reading_value=7.5, serial_no="SN-EAST", section_title="Water Chemistry"),
			_Dict(
				reading="pH",
				reading_value=9.1,
				max_value=7.8,
				out_of_range=1,
				serial_no="SN-WEST",
				section_title="Water Chemistry",
			),
		],
		cleaning_tasks=[],
		maintenance_results=[],
		consumables=[_Dict(item_name="Chlorine tabs", qty=1, uom="ea", serial_no="SN-WEST")],
	)


def _block(context, heading):
	for block in context["blocks"]:
		if block["heading"] == heading:
			return block
	raise AssertionError(f"no block {heading!r} in {[b['heading'] for b in context['blocks']]}")


# ------------------------------------------------------------------ when it sends


class TestWhenItSends(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_going_to_review_sends_the_reviewer_and_the_technician_a_copy(self):
		digest.on_visit_update(_visit())
		self.assertEqual(
			[mail["recipients"] for mail in STATE["sent"]],
			[[digest.REVIEW_INBOX], ["austin@example.com"]],
		)
		self.assertEqual(digest.REVIEW_INBOX, "service_repair@sapphirefountains.com")

	def test_both_copies_point_back_at_the_record(self):
		digest.on_visit_update(_visit())
		for mail in STATE["sent"]:
			self.assertEqual(mail["reference_doctype"], "Sapphire Maintenance Record")
			self.assertEqual(mail["reference_name"], "SMR-0001")

	def test_the_two_copies_are_worded_for_their_reader(self):
		digest.on_visit_update(_visit())
		review, copy = STATE["sent"]
		self.assertEqual(review["subject"], "Ready for review: Highlands Plaza visit, 2026-10-12")
		self.assertEqual(copy["subject"], "Your visit form: Highlands Plaza, 2026-10-12")
		labels = [r["context"]["button_label"] for r in STATE["rendered"]]
		self.assertEqual(labels, ["Review the visit", "Open the visit"])
		self.assertIn("Austin Healey sent this visit for review", STATE["rendered"][0]["context"]["intro"])

	def test_it_uses_the_service_pillar_and_the_design_system_template(self):
		digest.on_visit_update(_visit())
		rendered = STATE["rendered"][0]
		self.assertEqual(rendered["template"], digest.TEMPLATE)
		self.assertEqual(rendered["kwargs"]["pillar"], "service")

	def test_an_ordinary_save_while_pending_sends_nothing(self):
		"""A reviewer's edit in Pending Review is a save, not a new review."""
		STATE["changed"] = set()
		digest.on_visit_update(_visit())
		self.assertEqual(STATE["sent"], [])

	def test_a_draft_save_sends_nothing(self):
		"""The wizard autosaves on every page; none of those is a review."""
		digest.on_visit_update(_visit(workflow_state="Draft"))
		self.assertEqual(STATE["sent"], [])

	def test_a_resubmission_after_send_back_sends_a_fresh_copy(self):
		digest.on_visit_update(_visit())
		digest.on_visit_update(_visit(visit_notes="Fixed the answer Lisa asked about."))
		self.assertEqual(len(STATE["sent"]), 4)

	def test_quiet_during_migrate_and_import(self):
		for flag in digest.QUIET_FLAGS:
			with self.subTest(flag=flag):
				_reset_state()
				sys.modules["frappe"].flags[flag] = True
				digest.on_visit_update(_visit())
				self.assertEqual(STATE["sent"], [])

	def test_no_technician_still_sends_the_review(self):
		digest.on_visit_update(_visit(technician=None))
		self.assertEqual([mail["recipients"] for mail in STATE["sent"]], [[digest.REVIEW_INBOX]])
		self.assertIn("The technician sent this visit", STATE["rendered"][0]["context"]["intro"])

	def test_a_disabled_technician_gets_no_copy(self):
		digest.on_visit_update(_visit(technician="korben@example.com"))
		self.assertEqual([mail["recipients"] for mail in STATE["sent"]], [[digest.REVIEW_INBOX]])

	def test_on_submit_sends_only_where_no_workflow_is_active(self):
		digest.on_visit_submit(_visit(docstatus=1))
		self.assertEqual(STATE["sent"], [], "with the workflow, Approve & Submit needs no second copy")
		STATE["workflow"] = None
		digest.on_visit_submit(_visit(docstatus=1))
		self.assertEqual(len(STATE["sent"]), 2)


class TestItNeverBlocksTheReview(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_a_render_failure_is_logged_not_raised(self):
		STATE["raise_in"] = "render"
		self.assertIsNone(digest.on_visit_update(_visit()))
		self.assertEqual(STATE["sent"], [])
		self.assertTrue(STATE["errors"])

	def test_a_send_failure_is_logged_not_raised(self):
		STATE["raise_in"] = "sendmail"
		self.assertIsNone(digest.on_visit_update(_visit()))
		self.assertEqual(len(STATE["errors"]), 2, "each copy is tried on its own")

	def test_a_failure_building_the_digest_is_logged_not_raised(self):
		broken = _visit(chemistry_readings=[object()])
		self.assertIsNone(digest.on_visit_update(broken))
		self.assertTrue(STATE["errors"])

	def test_even_a_broken_error_log_does_not_raise(self):
		frappe = sys.modules["frappe"]
		original = frappe.log_error

		def _broken(*args, **kwargs):
			raise RuntimeError("error log is down too")

		frappe.log_error = _broken
		STATE["raise_in"] = "render"
		try:
			self.assertIsNone(digest.on_visit_update(_visit()))
		finally:
			frappe.log_error = original


# ------------------------------------------------------------------ what it says


class TestWhatItSays(unittest.TestCase):
	def setUp(self):
		_reset_state()
		self.context = digest.build_context(_visit())

	def test_readings_carry_value_unit_and_target(self):
		rows = _block(self.context, "Water Chemistry")["rows"]
		self.assertEqual(rows[0], ["pH", "7.4", "7.2–7.8", ""])
		self.assertEqual(rows[1], ["Free chlorine", "6.5 ppm (OUT OF RANGE)", "1–3 ppm", "Shocked yesterday"])

	def test_a_zero_reading_is_not_recorded(self):
		"""Blank and zero are the same on this form, by the record's own rule."""
		rows = _block(self.context, "Water Chemistry")["rows"]
		self.assertEqual(rows[2], ["Alkalinity", "Not recorded", "≤ 120 ppm", ""])

	def test_cleaning_tasks_say_done_or_not(self):
		rows = _block(self.context, "Cleaning")["rows"]
		self.assertEqual(rows, [["Skim surface", "Done", ""], ["Brush walls", "Not done", "Ran out of time"]])

	def test_inspection_answers_keep_the_selection_and_the_words(self):
		rows = _block(self.context, "Pump Inspection")["rows"]
		self.assertEqual(rows[0], ["Pump pressure", "Pass", ""])
		self.assertEqual(rows[1], ["Nozzles", "Replace: Two cracked", "Ordered"])
		self.assertEqual(_block(self.context, "Lighting")["rows"], [["Lights", "Not answered", ""]])

	def test_the_form_sections_become_the_headings_in_order(self):
		self.assertEqual(
			[block["heading"] for block in self.context["blocks"]],
			["Water Chemistry", "Cleaning", "Pump Inspection", "Lighting", "Chemicals and parts used"],
		)

	def test_only_chemicals_actually_used_are_listed(self):
		block = _block(self.context, "Chemicals and parts used")
		self.assertEqual(block["rows"], [["Chlorine tabs", "2 ea"]])

	def test_nothing_used_says_so(self):
		context = digest.build_context(_visit(consumables=[_Dict(item_name="pH down", qty=0)]))
		block = _block(context, "Chemicals and parts used")
		self.assertEqual((block["rows"], block["empty"]), ([], "None used."))

	def test_the_warnings_name_what_needs_looking_at(self):
		self.assertEqual(
			self.context["warnings"],
			[
				"Warning: 1 reading out of range: Free chlorine.",
				"Warning: 2 required items not answered yet: Lights, Alkalinity.",
			],
		)

	def test_the_summary(self):
		summary = dict(self.context["summary"])
		self.assertEqual(summary["Site"], "Highlands Plaza")
		self.assertEqual(summary["Account"], "Highlands Property Co")
		self.assertEqual(summary["Water feature"], "East Fountain")
		self.assertEqual(summary["Technician"], "Austin Healey")
		self.assertEqual(summary["Visit type"], "Regular visit")
		self.assertEqual(
			summary["On site"], "8:05 AM – 10:35 AM (2 h 15 min)", "paused time is not time on site"
		)
		self.assertEqual(summary["Completed"], "87%")
		self.assertEqual(summary["Safety and PPE acknowledged"], "Yes")
		self.assertEqual(summary["Client sign-off"], "Signed")
		self.assertNotIn("Crew", summary)

	def test_the_crew_is_listed_when_there_is_one(self):
		context = digest.build_context(
			_visit(crew=[_Dict(user="korben@example.com", full_name="Korben Jessop")])
		)
		self.assertEqual(dict(context["summary"])["Crew"], "Korben Jessop")

	def test_photos_are_absolute_links_and_unsafe_ones_are_dropped(self):
		chemistry = _block(self.context, "Water Chemistry")["photos"]
		self.assertEqual(chemistry, [(f"{SITE}/private/files/chlorine%20strip.jpg", "Photo: Free chlorine")])
		self.assertEqual(_block(self.context, "Pump Inspection")["photos"], [])

	def test_a_site_visit_names_each_feature(self):
		context = digest.build_context(_site_visit())
		self.assertEqual(
			[block["heading"] for block in context["blocks"]],
			[
				"East Fountain — Water Chemistry",
				"West Fountain — Water Chemistry",
				"Chemicals and parts used",
			],
		)
		self.assertEqual(context["warnings"], ["Warning: 1 reading out of range: pH (West Fountain)."])
		self.assertEqual(
			_block(context, "Chemicals and parts used")["rows"], [["Chlorine tabs", "1 ea", "West Fountain"]]
		)


# ------------------------------------------------------------------ the HTML


class TestTheTemplateRendersIt(unittest.TestCase):
	"""The content, checked in the HTML a reader gets rather than in the context alone."""

	@classmethod
	def setUpClass(cls):
		from jinja2 import Environment, FileSystemLoader

		cls.env = Environment(loader=FileSystemLoader(str(REPO_ROOT)), autoescape=False)

	def setUp(self):
		_reset_state()

	def _html(self, audience):
		context = digest.build_context(_visit())
		context.update(digest._audience_context(context, audience))
		return self.env.get_template(digest.TEMPLATE).render(**context)

	def test_every_answer_reaches_the_page(self):
		html = self._html("review")
		for text in (
			"Free chlorine",
			"6.5 ppm (OUT OF RANGE)",
			"Not recorded",
			"Brush walls",
			"Replace: Two cracked",
			"Not answered",
			"Chlorine tabs",
			"Highlands Plaza",
			"Warning: 2 required items not answered yet",
			"Review the visit",
			f"{SITE}/private/files/chlorine%20strip.jpg",
		):
			with self.subTest(text=text):
				self.assertIn(text, html)

	def test_free_text_is_escaped(self):
		"""frappe's Jinja env has no autoescape; the macros must do it, exactly once."""
		html = self._html("review")
		self.assertIn("Pump was &lt;b&gt;loud&lt;/b&gt; on arrival.", html)
		self.assertNotIn("<b>loud</b>", html)
		self.assertNotIn("&amp;lt;", html)

	def test_the_technicians_copy(self):
		html = self._html("technician")
		self.assertIn("This is your copy of the visit form", html)
		self.assertIn("Open the visit", html)
		self.assertIn("tell the office before the visit is approved", html)
		self.assertNotIn("Review the visit", html)

	def test_it_is_in_the_email_inventory(self):
		inventory = (REPO_ROOT / "docs" / "email-design-system.md").read_text(encoding="utf-8")
		self.assertTrue(
			"sapphire_maintenance/visit_review_digest.html" in inventory,
			"add the digest to the Inventory table in docs/email-design-system.md",
		)


if __name__ == "__main__":
	unittest.main()
