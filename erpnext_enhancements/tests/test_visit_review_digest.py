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
INBOX = "service_repair@sapphirefountains.com"
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
			# ERPNext Enhancements Settings -> Visit Review Emails, as the seed patch leaves it.
			"review_emails": [INBOX],
			# Earlier approved visits to the site, for "Compared with last visit". None by
			# default: every visit is the site's first, as on prod until 2026-10-12.
			"history": [],
			"history_readings": [],
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
	if doctype == "Sapphire Chemistry Reading":
		wanted = set(filters["parent"][1])
		return [row for row in STATE["history_readings"] if row.parent in wanted]
	return []


def _db_sql(query, params, as_dict=False):
	"""visit_compare's earlier approved visits to the same site (its EARLIER_SQL); the day
	filter and order are pinned in test_visit_compare."""
	if STATE.get("raise_in") == "history":
		raise RuntimeError("history on fire")
	return [
		row
		for row in STATE["history"]
		if row.project == params["project"] and row.docstatus == 1 and row.name != params["name"]
	]


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
	frappe.db = types.SimpleNamespace(get_value=_db_get_value, sql=_db_sql)
	frappe.get_all = _get_all

	def _get_cached_doc(doctype, *args, **kwargs):
		if STATE.get("raise_in") == "settings":
			raise RuntimeError("settings unreadable")
		assert doctype == "ERPNext Enhancements Settings", doctype
		return _Dict(visit_review_emails=[_Dict(email=email) for email in STATE["review_emails"]])

	frappe.get_cached_doc = _get_cached_doc

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(float(v or 0))
	utils.flt = lambda v: float(v or 0)
	utils.nowdate = lambda: "2026-10-12"
	utils.getdate = lambda v=None: datetime.date.fromisoformat(str(v or "2026-10-12")[:10])
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


def _forget_real_modules():
	"""Drop the digest and the comparison helper it imports, from ``sys.modules`` and from the
	package: ``from package import name`` takes a stale package attribute over a fresh import,
	and a module imported under another suite's stub would read that suite's ``frappe``."""
	import erpnext_enhancements.sapphire_maintenance as package

	for name in ("review_digest", "visit_compare"):
		sys.modules.pop(f"erpnext_enhancements.sapphire_maintenance.{name}", None)
		if name in vars(package):
			delattr(package, name)


def setUpModule():
	global digest
	_install_stubs()
	_reset_state()
	_forget_real_modules()
	from erpnext_enhancements.sapphire_maintenance import review_digest

	digest = review_digest


def tearDownModule():
	_forget_real_modules()
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
			[[INBOX], ["austin@example.com"]],
		)

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
		self.assertEqual([mail["recipients"] for mail in STATE["sent"]], [[INBOX]])
		self.assertIn("The technician sent this visit", STATE["rendered"][0]["context"]["intro"])

	def test_a_disabled_technician_gets_no_copy(self):
		digest.on_visit_update(_visit(technician="korben@example.com"))
		self.assertEqual([mail["recipients"] for mail in STATE["sent"]], [[INBOX]])

	def test_on_submit_sends_only_where_no_workflow_is_active(self):
		digest.on_visit_submit(_visit(docstatus=1))
		self.assertEqual(STATE["sent"], [], "with the workflow, Approve & Submit needs no second copy")
		STATE["workflow"] = None
		digest.on_visit_submit(_visit(docstatus=1))
		self.assertEqual(len(STATE["sent"]), 2)


class TestWhoReviewsIsASetting(unittest.TestCase):
	"""ERPNext Enhancements Settings → Visit Review Emails (Nik: "service_repair should be a
	setting we can change which emails it goes to")."""

	def setUp(self):
		_reset_state()

	def test_every_listed_address_gets_the_review_once(self):
		STATE["review_emails"] = ["lisa@example.com", "james@example.com", " Lisa@Example.com "]
		digest.on_visit_update(_visit())
		self.assertEqual(STATE["sent"][0]["recipients"], ["lisa@example.com", "james@example.com"])

	def test_an_empty_list_sends_the_review_to_nobody_but_the_technician_still_gets_a_copy(self):
		STATE["review_emails"] = []
		digest.on_visit_update(_visit())
		self.assertEqual([mail["recipients"] for mail in STATE["sent"]], [["austin@example.com"]])

	def test_a_technician_on_the_list_is_not_sent_two_copies(self):
		STATE["review_emails"] = [INBOX, "Austin@example.com"]
		digest.on_visit_update(_visit())
		self.assertEqual([mail["recipients"] for mail in STATE["sent"]], [[INBOX, "Austin@example.com"]])

	def test_an_unreadable_setting_is_logged_and_the_technician_still_gets_a_copy(self):
		STATE["raise_in"] = "settings"
		digest.on_visit_update(_visit())
		self.assertEqual([mail["recipients"] for mail in STATE["sent"]], [["austin@example.com"]])
		self.assertTrue(STATE["errors"])

	def test_the_setting_is_declared_on_the_settings_doctype(self):
		import json

		path = (
			REPO_ROOT
			/ "erpnext_enhancements/enhancements_core/doctype/erpnext_enhancements_settings"
			/ "erpnext_enhancements_settings.json"
		)
		meta = json.loads(path.read_text(encoding="utf-8"))
		field = {f["fieldname"]: f for f in meta["fields"]}[digest.RECIPIENTS_FIELD]
		self.assertEqual((field["fieldtype"], field["options"]), ("Table", "Project Reminder Email"))
		self.assertIn(digest.RECIPIENTS_FIELD, meta["field_order"])


class TestTheSeedPatch(unittest.TestCase):
	"""A Table field has no default, so the list starts empty on every existing site. The patch
	seeds service_repair@ once, and must never be able to abort the migrate that is the deploy."""

	PATCH = REPO_ROOT / "erpnext_enhancements/patches/seed_visit_review_emails.py"

	def _code(self):
		"""The patch's executable source: docstrings and comments stripped, quotes normalised."""
		import ast

		tree = ast.parse(self.PATCH.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			body = getattr(node, "body", None)
			if (
				isinstance(node, (ast.Module, ast.FunctionDef))
				and body
				and isinstance(body[0], ast.Expr)
				and isinstance(body[0].value, ast.Constant)
			):
				node.body = body[1:] or [ast.Pass()]
		return ast.unparse(tree).replace("'", '"')

	def test_it_seeds_the_service_inbox(self):
		self.assertIn(f'DEFAULT = "{INBOX}"', self._code())

	def test_it_writes_only_into_an_empty_table(self):
		code = self._code()
		self.assertIn("frappe.db.count(CHILD", code)
		self.assertLess(code.index("frappe.db.count(CHILD"), code.index("db_insert()"))

	def test_it_never_saves_the_single(self):
		"""Saving runs the settings' validate over 189 fields this patch does not touch."""
		code = self._code()
		for call in (".save(", ".insert(", "get_single(", '"Singles"'):
			self.assertNotIn(call, code)

	def test_a_failure_is_logged_not_raised(self):
		code = self._code()
		self.assertIn("except Exception:", code)
		self.assertIn("frappe.log_error(", code)

	def test_it_is_registered_after_model_sync(self):
		text = (REPO_ROOT / "erpnext_enhancements/patches.txt").read_text(encoding="utf-8")
		at = text.index("erpnext_enhancements.patches.seed_visit_review_emails")
		self.assertIn("[post_model_sync]", text[:at])


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

	def test_the_fountain_as_we_left_it_is_a_photo_link_after_the_notes(self):
		"""v1.597.0: the technician's last photo, the one the customer's report prints."""
		self.assertIsNone(self.context["fountain_photo"])
		context = digest.build_context(_visit(fountain_photo="/private/files/east fountain.jpg"))
		self.assertEqual(
			context["fountain_photo"],
			(f"{SITE}/private/files/east%20fountain.jpg", "Photo: the fountain as we left it"),
		)
		self.assertEqual(context["photo_count"], self.context["photo_count"] + 1)
		self.assertIsNone(
			digest.build_context(_visit(fountain_photo="javascript:alert(1)"))["fountain_photo"]
		)

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

	def test_the_fountain_photo_follows_the_notes(self):
		context = digest.build_context(_visit(fountain_photo="/private/files/east.jpg"))
		context.update(digest._audience_context(context, "review"))
		html = self.env.get_template(digest.TEMPLATE).render(**context)
		self.assertIn(f"{SITE}/private/files/east.jpg", html)
		self.assertLess(html.index("Cleared the intake."), html.index("Photo: the fountain as we left it"))
		self.assertNotIn("as we left it", self._html("review"))

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

	def test_the_sent_back_email(self):
		STATE["users"]["lisa@example.com"] = _Dict(
			full_name="Lisa Symanski", email="lisa@example.com", enabled=1
		)
		context = digest.build_context(_visit())
		context.update(sent_back_note="Re-test the <pH>.", reviewer_name="Lisa Symanski")
		context.update(digest._audience_context(context, "sent_back"))
		html = self.env.get_template(digest.TEMPLATE).render(**context)
		self.assertIn("Note from Lisa Symanski: Re-test the &lt;pH&gt;.", html)
		self.assertIn("Fix the visit", html)
		self.assertIn(f"{SITE}/desk/visit-wizard/SMR-0001", html)
		self.assertNotIn("Review the visit", html)

	def test_it_is_in_the_email_inventory(self):
		inventory = (REPO_ROOT / "docs" / "email-design-system.md").read_text(encoding="utf-8")
		self.assertTrue(
			"sapphire_maintenance/visit_review_digest.html" in inventory,
			"add the digest to the Inventory table in docs/email-design-system.md",
		)


# ------------------------------------------------------------------ v1.595.0


def _history(name="SMR-PREV", day="2026-10-05", readings=(("pH", 7.6), ("Free chlorine", 2.5))):
	"""An approved earlier visit to the same fountain, with its readings."""
	STATE["history"].append(
		_Dict(
			name=name,
			project="PRJ-0001",
			docstatus=1,
			serial_no="SN-EAST",
			visit_date=day,
			scheduled_visit_date=None,
			creation=f"{day} 09:00:00",
		)
	)
	for reading, value in readings:
		STATE["history_readings"].append(
			_Dict(parent=name, reading=reading, reading_value=value, uom="", serial_no=None)
		)


class TestComparedWithLastVisit(unittest.TestCase):
	"""A3: each reading beside its value at the site's previous approved visit, in the Value
	cell so the table keeps the email system's four columns."""

	def setUp(self):
		_reset_state()

	def test_a_first_visit_says_so(self):
		context = digest.build_context(_visit())
		self.assertEqual(dict(context["summary"])["Compared with"], "First recorded visit")
		self.assertTrue(context["first_visit"])
		self.assertEqual(_block(context, "Water Chemistry")["rows"][0][1], "7.4")

	def test_each_value_carries_last_visits(self):
		_history()
		context = digest.build_context(_visit())
		rows = _block(context, "Water Chemistry")["rows"]
		self.assertEqual(rows[0], ["pH", "7.4 (was 7.6)", "7.2–7.8", ""])
		self.assertEqual(rows[1][1], "6.5 ppm (OUT OF RANGE, was 2.5)")
		self.assertEqual(rows[2][1], "Not recorded", "never measured before either")
		self.assertEqual(len(rows[0]), 4)
		self.assertEqual(dict(context["summary"])["Compared with"], "The approved visit on 2026-10-05")
		self.assertFalse(context["first_visit"])
		labels = [label for label, _value in context["summary"]]
		self.assertEqual(labels[labels.index("Visit date") + 1], "Compared with")

	def test_earlier_visits_that_never_took_these_readings(self):
		_history(readings=(("TDS", 900),))
		summary = dict(digest.build_context(_visit())["summary"])
		self.assertEqual(summary["Compared with"], "No earlier readings of these")

	def test_a_failed_lookup_is_logged_and_the_digest_still_goes_out(self):
		STATE["raise_in"] = "history"
		context = digest.build_context(_visit())
		self.assertNotIn("Compared with", dict(context["summary"]))
		self.assertEqual(_block(context, "Water Chemistry")["rows"][0][1], "7.4")
		self.assertEqual(STATE["errors"], ["Visit review: last visit not compared"])

	def test_the_review_page_reads_the_same_findings(self):
		context = digest.build_context(_visit())
		self.assertEqual(context["out_of_range"], ["Free chlorine"])
		self.assertEqual(context["unanswered"], ["Lights", "Alkalinity"])
		self.assertEqual(context["photo_count"], 1, "the unsafe javascript: photo is dropped")


class TestTheReviewersButton(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_the_reviewer_opens_the_visit_review_page_and_the_technician_the_form(self):
		digest.on_visit_update(_visit())
		review, copy = STATE["rendered"]
		self.assertEqual(review["context"]["url"], f"{SITE}/desk/visit-review/SMR-0001")
		self.assertEqual(copy["context"]["url"], f"{SITE}/app/sapphire-maintenance-record/SMR-0001")


class TestSentBack(unittest.TestCase):
	"""A visit sent back from the Visit Review page: the technician gets the note and their answers."""

	def setUp(self):
		_reset_state()
		STATE["users"]["lisa@example.com"] = _Dict(
			full_name="Lisa Symanski", email="lisa@example.com", enabled=1
		)

	def test_the_technician_gets_the_note_and_their_answers(self):
		sent = digest.send_sent_back(_visit(), "  Re-test the pH.  ", "lisa@example.com")
		self.assertEqual(sent, ["austin@example.com"])
		(mail,) = STATE["sent"]
		self.assertEqual(mail["recipients"], ["austin@example.com"])
		self.assertEqual(mail["subject"], "Sent back to you: Highlands Plaza visit, 2026-10-12")
		self.assertEqual(mail["reference_name"], "SMR-0001")
		context = STATE["rendered"][0]["context"]
		self.assertEqual(context["warnings"][0], "Note from Lisa Symanski: Re-test the pH.")
		self.assertIn("Warning: 1 reading out of range", context["warnings"][1])
		self.assertEqual(
			(context["button_label"], context["url"]), ("Fix the visit", f"{SITE}/desk/visit-wizard/SMR-0001")
		)
		self.assertIn("Lisa Symanski sent this visit back to you", context["intro"])

	def test_it_does_not_go_to_the_review_list(self):
		digest.send_sent_back(_visit(), "Re-test the pH.", "lisa@example.com")
		self.assertNotIn(INBOX, [r for mail in STATE["sent"] for r in mail["recipients"]])

	def test_no_usable_technician_sends_nothing(self):
		self.assertEqual(digest.send_sent_back(_visit(technician=None), "x"), [])
		self.assertEqual(digest.send_sent_back(_visit(technician="korben@example.com"), "x"), [])
		self.assertEqual(STATE["sent"], [])

	def test_it_never_raises(self):
		STATE["raise_in"] = "sendmail"
		self.assertEqual(digest.send_sent_back(_visit(), "x", "lisa@example.com"), [])
		self.assertEqual(STATE["errors"], ["Visit review digest not sent"])


if __name__ == "__main__":
	unittest.main()
