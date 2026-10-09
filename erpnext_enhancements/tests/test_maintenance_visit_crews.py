# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Multi-person maintenance visits (P1.8): crews, their hours, their ToDos, their digests.

"Some maintenance jobs take multiple people, and some even take the entire day of multiple
people" (Nik, 2026-10-09). A visit record now carries a ``crew`` table beside ``technician`` (who
stays the person filling it in), plus ``planned_hours`` and ``full_day``; a site's Maintenance
Profile carries the default crew, length and full-day flag every drafted visit starts from.

What each section pins, and how it would fail otherwise:

* **The crew mirror only touches crew ToDos.** Removal is limited to people who were on the crew
  before this save and are not now the technician, or saving a crew edit would cancel a sidebar
  assignment, and a Visit Wizard claim (which swaps a helper and the technician) would strip one
  of them of their ToDo. It never raises: a visit must stay saveable.
* **Drafting copies the profile's crew, length and full-day flag** and assigns everyone.
* **The 6 AM digest goes to every crew member, at most once per person per day.** The SMS is
  billed. The stamps are persisted per (visit, person) because a deploy FLUSHDBs redis.
* **Any crew member may fill the visit in.** The kiosk lists their visits, and a claim moves the
  displaced technician into the crew rather than off the visit.
* **The engine books every person** with the right hours: own row hours, a full day (their
  capacity, or the Settings' full day), planned hours, the Settings default, or the clocked length
  for the technician. Projected visits carry the profile's crew.
* **The planner** shows crews on cards, checks every affected person's day on a move, swaps only
  the dragged helper, and refuses to add a helper to a finished, started or projected visit.

Bench-free: it installs its own ``frappe`` stub, runs the real modules, and restores ``sys.modules``.

Run: python -m unittest erpnext_enhancements.tests.test_maintenance_visit_crews
"""

import datetime
import importlib
import json
import re
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
DOCTYPES = APP / "sapphire_maintenance" / "doctype"

D = datetime.date
TODAY = D(2026, 10, 12)  # a Monday
RECORD = "Sapphire Maintenance Record"
CREW = "Sapphire Visit Crew Member"
PROFILE = "Sapphire Maintenance Profile"
RECORD_MODULE = "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record"
CONTRACT_MODULE = "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract"
SETTINGS_MODULE = "erpnext_enhancements.workforce.doctype.time_kiosk_settings.time_kiosk_settings"
STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.desk",
	"frappe.desk.form",
	"frappe.desk.form.assign_to",
	"erpnext_enhancements.email_style",
	"erpnext_enhancements.api.telephony",
	RECORD_MODULE,
	CONTRACT_MODULE,
	"erpnext_enhancements.workforce",
	"erpnext_enhancements.workforce.tracking_health",
	"erpnext_enhancements.workforce.doctype",
	"erpnext_enhancements.workforce.doctype.time_kiosk_settings",
	SETTINGS_MODULE,
)
_saved_modules = {}
_modules_before = set()
frappe = None
visit_crew = dispatch = tasks = visit = kiosk = engine = planner = None

AUSTIN, KORBEN, JESSE, DANIEL, LISA = (
	"austin@example.com",
	"korben@example.com",
	"jesse@example.com",
	"daniel@example.com",
	"lisa@example.com",
)


class _Throw(Exception):
	pass


class _PermissionError(Exception):
	pass


class _Doc(dict):
	"""Enough of a frappe Document: attribute access, child tables, save and its hooks."""

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None

	def __setattr__(self, name, value):
		self[name] = value

	def set(self, key, value):
		self[key] = value

	def append(self, table, row=None):
		row = row if isinstance(row, _Doc) else _Doc(row or {})
		self[table] = list(self.get(table) or []) + [row]
		return row

	def remove(self, row):
		for key, value in self.items():
			if isinstance(value, list) and any(item is row for item in value):
				self[key] = [item for item in value if item is not row]
				return

	def check_permission(self, ptype=None):
		pass

	def add_comment(self, kind, text):
		frappe.comments.append((self.get("name"), kind, text))

	def get_doc_before_save(self):
		return self.get("_before")

	def save(self):
		frappe.saved.append(json.loads(json.dumps(self, default=str)))
		self["modified"] = "2026-10-12 10:00:01"

	def insert(self, ignore_permissions=False):
		self["name"] = self.get("name") or f"SMR-NEW-{len(frappe.inserted) + 1}"
		frappe.inserted.append(self)
		return self


class _Dict(_Doc):
	"""``frappe._dict``, which is what ``get_all`` rows are: a missing key reads None."""

	def __getattr__(self, name):
		return self.get(name)


def _getdate(value=None):
	if value is None:
		return TODAY
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, D):
		return value
	return D.fromisoformat(str(value)[:10])


def _throw(message, exc=None, title=None):
	raise (exc or _Throw)(message)


def _match(row, filters):
	for field, wanted in (filters or {}).items():
		value = row.get(field)
		if isinstance(wanted, (list, tuple)):
			op, arg = wanted[0], wanted[1] if len(wanted) > 1 else None
			if op == "in" and value not in (arg or []):
				return False
			if op == "is" and (arg == "set") != bool(value):
				return False
		elif value != wanted:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, pluck=None, or_filters=None, as_list=False, **kwargs):
	frappe.queries.append(
		(doctype, {"filters": filters, "or_filters": or_filters, "fields": fields, **kwargs})
	)
	source = frappe.tables.get(doctype, [])
	rows = source(filters=filters, or_filters=or_filters) if callable(source) else source
	rows = [_Dict(r) for r in rows if callable(source) or _match(r, filters)]
	if pluck:
		return [row.get(pluck) for row in rows]
	if as_list:
		return [tuple(row.get(f) for f in fields) for row in rows]
	return rows


def _get_value(doctype, name, field=None, *args, as_dict=False, **kwargs):
	rows = frappe.tables.get(doctype, [])
	rows = rows() if callable(rows) else rows
	if isinstance(name, dict):
		row = next((r for r in rows if _match(r, name)), None)
	else:
		row = next((r for r in rows if r.get("name") == name), None)
	if row is None:
		return None
	if isinstance(field, (list, tuple)):
		return _Dict({f: row.get(f) for f in field}) if as_dict else tuple(row.get(f) for f in field)
	return row.get(field)


def _exists(doctype, name):
	rows = frappe.tables.get(doctype, [])
	rows = rows() if callable(rows) else rows
	if isinstance(name, dict):
		return any(_match(r, name) for r in rows)
	return any(r.get("name") == name for r in rows)


def _sql(query, values=None, as_dict=False, **kwargs):
	frappe.queries.append(("sql", query, values))
	return [_Dict(row) for row in frappe.sql_handler(query, values or {})]


def setUpModule():
	global frappe, visit_crew, dispatch, tasks, visit, kiosk, engine, planner
	_modules_before.update(sys.modules)
	for name in STUBBED:
		_saved_modules[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.getdate = _getdate
	utils.nowdate = lambda: str(TODAY)
	utils.add_days = lambda value, days: _getdate(value) + datetime.timedelta(days=days)
	utils.add_months = lambda value, months: _getdate(value)
	utils.add_years = lambda value, years: _getdate(value)
	utils.get_weekday = lambda value=None: _getdate(value).strftime("%A")
	utils.date_diff = lambda a, b: (_getdate(a) - _getdate(b)).days
	utils.formatdate = lambda value=None, *a, **k: _getdate(value).strftime("%m-%d-%Y")
	utils.cint = lambda value: int(float(value or 0))
	utils.flt = lambda value, precision=None: float(value or 0)
	utils.now_datetime = lambda: datetime.datetime(2026, 10, 12, 6, 0)
	utils.get_datetime = lambda value=None: value
	utils.strip_html_tags = lambda text: re.sub(r"<[^>]+>", "", str(text))
	utils.escape_html = lambda text: str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe._dict = _Dict
	frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	frappe.throw = _throw
	frappe.PermissionError = _PermissionError
	frappe.parse_json = lambda value: json.loads(value) if isinstance(value, str) else value
	frappe.get_traceback = lambda: "traceback"
	frappe.session = types.SimpleNamespace(user=AUSTIN)
	frappe.local = types.SimpleNamespace(message_log=[])

	assign_to = types.ModuleType("frappe.desk.form.assign_to")
	assign_to.add = lambda args: _assigned("add", args["name"], args["assign_to"][0])
	assign_to.remove = lambda doctype, name, user: _assigned("remove", name, user)
	desk = types.ModuleType("frappe.desk")
	form = types.ModuleType("frappe.desk.form")
	desk.form, form.assign_to = form, assign_to

	email_style = types.ModuleType("erpnext_enhancements.email_style")
	email_style.p = lambda text: f"<p>{text}</p>"
	email_style.table = lambda headers, rows: json.dumps({"headers": headers, "rows": rows})
	email_style.wrap = lambda html, **kwargs: html
	telephony = types.ModuleType("erpnext_enhancements.api.telephony")
	telephony.send_system_sms = lambda number, message: frappe.sms.append((number, message))

	record_module = types.ModuleType(RECORD_MODULE)
	record_module.get_dashboard_context = lambda *a, **k: {}
	record_module.get_visit_payload = lambda *a, **k: {}
	record_module.resolve_template = lambda *a, **k: None
	contract_module = types.ModuleType(CONTRACT_MODULE)
	contract_module.iter_seasonal_visits = lambda contract: iter(())

	workforce = types.ModuleType("erpnext_enhancements.workforce")
	for sub in ("client_time", "costing", "photo_gate", "sites", "tracking_health"):
		setattr(workforce, sub, types.ModuleType(f"erpnext_enhancements.workforce.{sub}"))
	workforce.tracking_health.haversine_m = lambda *a: 0
	settings = types.ModuleType(SETTINGS_MODULE)
	settings.get_settings = lambda: {}

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.desk": desk,
			"frappe.desk.form": form,
			"frappe.desk.form.assign_to": assign_to,
			"erpnext_enhancements.email_style": email_style,
			"erpnext_enhancements.api.telephony": telephony,
			RECORD_MODULE: record_module,
			CONTRACT_MODULE: contract_module,
			"erpnext_enhancements.workforce": workforce,
			"erpnext_enhancements.workforce.tracking_health": workforce.tracking_health,
			"erpnext_enhancements.workforce.doctype": types.ModuleType("doctype"),
			"erpnext_enhancements.workforce.doctype.time_kiosk_settings": types.ModuleType("tks"),
			SETTINGS_MODULE: settings,
		}
	)
	_reset()

	visit_crew = importlib.import_module("erpnext_enhancements.sapphire_maintenance.visit_crew")
	dispatch = importlib.import_module("erpnext_enhancements.api.maintenance_dispatch")
	tasks = importlib.import_module("erpnext_enhancements.tasks")
	visit = importlib.import_module("erpnext_enhancements.api.maintenance_visit")
	kiosk = importlib.import_module("erpnext_enhancements.api.time_kiosk")
	engine = importlib.import_module("erpnext_enhancements.project_enhancements.crew_availability")
	planner = importlib.import_module("erpnext_enhancements.api.maintenance_planner")


def tearDownModule():
	for name in set(sys.modules) - _modules_before:
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements")):
			sys.modules.pop(name, None)
	for name, module in _saved_modules.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def _users():
	return [
		{"name": AUSTIN, "full_name": "Austin Healey", "enabled": 1, "email": AUSTIN},
		{"name": KORBEN, "full_name": "Korben Dallas", "enabled": 1, "email": KORBEN},
		{"name": JESSE, "full_name": "Jesse Pinkman", "enabled": 1, "email": JESSE},
		{"name": DANIEL, "full_name": "Daniel Gone", "enabled": 0, "email": DANIEL},
		{"name": LISA, "full_name": "Lisa Office", "enabled": 1, "email": LISA},
	]


def _reset():
	frappe.flags = types.SimpleNamespace()
	frappe.session.user = AUSTIN
	frappe.local.message_log = []
	frappe.roles = ["Maintenance User"]
	frappe.get_roles = lambda user=None: list(frappe.roles)
	frappe.tables = {"User": _users(), "ToDo": []}
	frappe.queries = []
	frappe.writes = []
	frappe.assigned = []
	frappe.errors = []
	frappe.comments = []
	frappe.saved = []
	frappe.inserted = []
	frappe.sms = []
	frappe.mails = []
	frappe.docs = {}
	frappe.singles = {}
	frappe.sql_handler = lambda query, values: []
	frappe.log_error = lambda *a, **k: frappe.errors.append((a, k))
	frappe.get_all = _get_all
	frappe.get_list = _get_all
	frappe.get_doc = lambda doctype, name=None: frappe.docs[(doctype, name)]
	frappe.new_doc = lambda doctype: _Doc(doctype=doctype)
	frappe.has_permission = lambda *a, **k: True
	frappe.sendmail = lambda **kwargs: frappe.mails.append(kwargs)
	frappe.enqueue = lambda *a, **k: None
	frappe.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *a, **k: None)
	frappe.db = types.SimpleNamespace(
		sql=_sql,
		exists=_exists,
		get_value=_get_value,
		set_value=lambda dt, name, field, value=None, update_modified=True: frappe.writes.append(
			(dt, name, field, value)
		),
		get_single_value=lambda doctype, field, *a, **k: frappe.singles.get((doctype, field)),
		has_column=lambda doctype, column: True,
	)


def _assigned(kind, name, user):
	frappe.assigned.append((kind, name, user))
	if kind == "add":
		frappe.tables["ToDo"].append(
			{"reference_type": RECORD, "reference_name": name, "allocated_to": user, "status": "Open"}
		)


def _todo(name, user):
	frappe.tables["ToDo"].append(
		{"reference_type": RECORD, "reference_name": name, "allocated_to": user, "status": "Open"}
	)


def _record(name="SMR-1", technician=AUSTIN, crew=(), before=None, **values):
	doc = _Doc(
		name=name,
		doctype=RECORD,
		docstatus=0,
		workflow_state="Draft",
		project="PRJ-1",
		technician=technician,
		scheduled_visit_date=TODAY,
		visit_date=None,
		planned_hours=0,
		full_day=0,
		dispatch_digest_sent_on=None,
		modified="2026-10-12 09:00:00",
	)
	doc["crew"] = [_Doc(row if isinstance(row, dict) else {"user": row, "hours": 0}) for row in crew]
	doc.update(values)
	if before is not None:
		doc["_before"] = before
	frappe.docs[(RECORD, name)] = doc
	return doc


# ---------------------------------------------------------------------- data model


class TestDocTypes(unittest.TestCase):
	def _json(self, folder):
		return json.loads((DOCTYPES / folder / f"{folder}.json").read_text(encoding="utf-8"))

	def test_the_child_doctype(self):
		doc = self._json("sapphire_visit_crew_member")
		self.assertEqual(doc["name"], CREW)
		self.assertEqual(doc["module"], "Sapphire Maintenance")
		self.assertEqual(doc["istable"], 1)
		fields = {f["fieldname"]: f for f in doc["fields"]}
		self.assertEqual(fields["user"]["options"], "User")
		self.assertEqual(fields["user"]["reqd"], 1)
		self.assertEqual(fields["full_name"]["fetch_from"], "user.full_name")
		self.assertEqual(fields["hours"]["fieldtype"], "Float")
		self.assertEqual(fields["hours"]["description"], "This person's hours. Blank: the visit's length.")
		# The per-person digest stamp: hidden, never copied onto a duplicated visit.
		self.assertEqual((fields["digest_sent_on"]["hidden"], fields["digest_sent_on"]["no_copy"]), (1, 1))
		source = (DOCTYPES / "sapphire_visit_crew_member" / "sapphire_visit_crew_member.py").read_text(
			"utf-8"
		)
		self.assertIn("class SapphireVisitCrewMember(Document)", source)

	def test_the_record_carries_crew_hours_and_full_day_beside_the_technician(self):
		doc = self._json("sapphire_maintenance_record")
		order = doc["field_order"]
		at = order.index("technician")
		self.assertEqual(order[at + 1 : at + 4], ["crew", "planned_hours", "full_day"])
		fields = {f["fieldname"]: f for f in doc["fields"]}
		self.assertEqual(fields["crew"]["options"], CREW)
		self.assertEqual(fields["planned_hours"]["label"], "Planned hours")
		self.assertEqual(fields["full_day"]["label"], "Full day")
		self.assertEqual(doc["modified"], "2026-10-09 14:00:00.000000")

	def test_the_profile_carries_the_site_default(self):
		doc = self._json("sapphire_maintenance_profile")
		fields = {f["fieldname"]: f for f in doc["fields"]}
		self.assertEqual(fields["visit_crew_section"]["label"], "Visit crew and length")
		self.assertEqual(fields["default_crew"]["options"], CREW)
		self.assertEqual(fields["visit_hours"]["fieldtype"], "Float")
		self.assertEqual(fields["visit_full_day"]["fieldtype"], "Check")
		self.assertEqual(doc["modified"], "2026-10-09 14:00:00.000000")
		order = doc["field_order"]
		self.assertLess(order.index("visit_crew_section"), order.index("default_crew"))

	def test_the_mirror_is_a_registered_on_update_hook(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		at = hooks.index('"Sapphire Maintenance Record": {')
		block = hooks[at : hooks.index('"Project Contract": {', at)]
		self.assertIn('"on_update": [', block)
		self.assertIn("erpnext_enhancements.sapphire_maintenance.visit_crew.on_record_update", block)


class TestValidateCrew(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_the_technician_and_repeats_are_dropped_and_hours_rounded(self):
		doc = _record(crew=[AUSTIN, {"user": KORBEN, "hours": 3.333}, {"user": KORBEN, "hours": 1}, JESSE])
		visit_crew.validate_crew(doc)
		self.assertEqual([(r.user, r.hours) for r in doc.crew], [(KORBEN, 3.33), (JESSE, 0)])

	def test_negative_hours_are_refused(self):
		with self.assertRaises(_Throw):
			visit_crew.validate_crew(_record(crew=[{"user": KORBEN, "hours": -1}]))
		with self.assertRaises(_Throw):
			visit_crew.validate_crew(_record(planned_hours=-2))

	def test_the_profile_drops_its_default_technician(self):
		profile = _Doc(default_technician=AUSTIN, default_crew=[_Doc(user=AUSTIN), _Doc(user=KORBEN)])
		visit_crew.validate_crew(
			profile, table="default_crew", lead_field="default_technician", hours_field="visit_hours"
		)
		self.assertEqual([r.user for r in profile.default_crew], [KORBEN])

	def test_it_reads_safely_before_the_field_exists(self):
		visit_crew.validate_crew(_Doc(technician=AUSTIN))
		self.assertEqual(visit_crew.crew_users(_Doc(technician=AUSTIN)), [])


# ---------------------------------------------------------------------- assignment mirror


class TestCrewMirror(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_new_visit_assigns_its_whole_crew(self):
		doc = _record(crew=[KORBEN, JESSE])
		visit_crew.on_record_update(doc)
		self.assertEqual(frappe.assigned, [("add", "SMR-1", JESSE), ("add", "SMR-1", KORBEN)])

	def test_only_people_new_to_the_crew_are_assigned(self):
		_todo("SMR-1", KORBEN)
		doc = _record(crew=[KORBEN, JESSE], before=_record(crew=[KORBEN]))
		visit_crew.on_record_update(doc)
		self.assertEqual(frappe.assigned, [("add", "SMR-1", JESSE)])

	def test_someone_already_holding_a_todo_is_not_assigned_twice(self):
		_todo("SMR-1", JESSE)
		visit_crew.on_record_update(_record(crew=[JESSE]))
		self.assertEqual(frappe.assigned, [])

	def test_only_people_who_left_the_crew_are_unassigned(self):
		_todo("SMR-1", KORBEN)
		_todo("SMR-1", LISA)  # assigned from the sidebar, never on the crew
		doc = _record(crew=[], before=_record(crew=[KORBEN]))
		visit_crew.on_record_update(doc)
		self.assertEqual(frappe.assigned, [("remove", "SMR-1", KORBEN)])

	def test_a_sidebar_assignee_is_never_touched(self):
		_todo("SMR-1", LISA)
		doc = _record(crew=[KORBEN], before=_record(crew=[KORBEN]))
		visit_crew.on_record_update(doc)
		self.assertEqual(frappe.assigned, [])

	def test_a_claim_swap_keeps_both_todos(self):
		# Korben (crew) took the form; Austin moved into the crew. Neither loses their ToDo.
		_todo("SMR-1", AUSTIN)
		_todo("SMR-1", KORBEN)
		doc = _record(technician=KORBEN, crew=[AUSTIN], before=_record(technician=AUSTIN, crew=[KORBEN]))
		visit_crew.on_record_update(doc)
		self.assertEqual(frappe.assigned, [])

	def test_finished_visits_and_migrates_are_skipped(self):
		visit_crew.on_record_update(_record(crew=[KORBEN], docstatus=1))
		frappe.flags.in_migrate = True
		visit_crew.on_record_update(_record(crew=[KORBEN]))
		self.assertEqual(frappe.assigned, [])

	def test_it_never_raises(self):
		def boom(args):
			raise RuntimeError("disabled user")

		with mock.patch.object(sys.modules["frappe.desk.form.assign_to"], "add", boom):
			visit_crew.on_record_update(_record(crew=[KORBEN]))
		self.assertEqual(frappe.errors[0][1]["title"], "Maintenance visit crew assignment failed")
		frappe.get_all = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down"))
		visit_crew.on_record_update(_record(crew=[KORBEN]))
		self.assertEqual(frappe.errors[-1][1]["title"], "Maintenance visit crew sync failed")

	def test_set_crew_keeps_a_staying_persons_row_and_stamp(self):
		doc = _record(crew=[{"user": KORBEN, "hours": 0, "digest_sent_on": TODAY}])
		korben_row = doc.crew[0]
		self.assertTrue(visit_crew.set_crew(doc, [{"user": KORBEN, "hours": 4}, {"user": JESSE}]))
		self.assertIs(doc.crew[0], korben_row)
		self.assertEqual((doc.crew[0].hours, doc.crew[0].digest_sent_on), (4, TODAY))
		self.assertEqual(doc.crew[1].user, JESSE)
		self.assertFalse(visit_crew.set_crew(doc, [{"user": KORBEN, "hours": 4}, {"user": JESSE}]))


# ---------------------------------------------------------------------- profile defaults + drafting


def _profile_tables(
	crew=((KORBEN, 0), (AUSTIN, 0), (DANIEL, 0), (JESSE, 2.5), (KORBEN, 1)), hours=4, full_day=1
):
	frappe.tables[PROFILE] = [
		{
			"name": "PROF-1",
			"project": "PRJ-1",
			"default_technician": AUSTIN,
			"visit_hours": hours,
			"visit_full_day": full_day,
		}
	]
	frappe.tables[CREW] = [
		{"parent": "PROF-1", "parenttype": PROFILE, "user": user, "hours": h, "idx": i}
		for i, (user, h) in enumerate(crew, 1)
	]


class TestDefaultCrew(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_enabled_users_only_never_the_technician_and_no_repeats(self):
		_profile_tables()
		self.assertEqual(
			dispatch.default_crew_for("PRJ-1"),
			{
				"crew": [KORBEN, JESSE],
				"rows": [{"user": KORBEN, "hours": None}, {"user": JESSE, "hours": 2.5}],
				"hours": 4.0,
				"full_day": True,
			},
		)

	def test_a_site_without_a_profile_has_no_crew(self):
		self.assertEqual(
			dispatch.default_crew_for("PRJ-9"), {"crew": [], "rows": [], "hours": None, "full_day": False}
		)

	def test_blank_hours_mean_the_default(self):
		_profile_tables(crew=(), hours=0, full_day=0)
		self.assertEqual(dispatch.default_crew_for("PRJ-1")["hours"], None)
		self.assertFalse(dispatch.default_crew_for("PRJ-1")["full_day"])

	def test_a_failure_is_logged_and_answers_nothing(self):
		frappe.get_all = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no column"))
		self.assertEqual(dispatch.default_crews_for(["PRJ-1"]), {})
		self.assertEqual(frappe.errors[0][1]["title"], "Maintenance default crew lookup failed")

	def test_an_assignment_is_skipped_for_someone_who_holds_one(self):
		_todo("SMR-1", KORBEN)
		dispatch.assign_to_technician("SMR-1", KORBEN)
		dispatch.assign_to_technician("SMR-1", JESSE)
		self.assertEqual(frappe.assigned, [("add", "SMR-1", JESSE)])


class TestDrafting(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_drafted_visit_copies_the_site_default_and_assigns_everyone(self):
		_profile_tables()
		contract = _Doc(name="CON-1", customer="Highlands", project="PRJ-1", project_contract=None)
		record = tasks._draft_maintenance_record(contract, scheduled_date=TODAY, exact_date=True)
		self.assertEqual(record.technician, AUSTIN)
		self.assertEqual([(r.user, r.hours) for r in record.crew], [(KORBEN, 0), (JESSE, 2.5)])
		self.assertEqual((record.planned_hours, record.full_day), (4.0, 1))
		self.assertEqual(
			frappe.assigned,
			[("add", record.name, AUSTIN), ("add", record.name, KORBEN), ("add", record.name, JESSE)],
		)

	def test_a_site_with_no_crew_drafts_as_before(self):
		frappe.tables[PROFILE] = [{"name": "PROF-1", "project": "PRJ-1", "default_technician": AUSTIN}]
		contract = _Doc(name="CON-1", customer="Highlands", project="PRJ-1", project_contract=None)
		record = tasks._draft_maintenance_record(contract, scheduled_date=TODAY, exact_date=True)
		self.assertEqual(record.get("crew") or [], [])
		self.assertNotIn("planned_hours", record)
		self.assertEqual(frappe.assigned, [("add", record.name, AUSTIN)])


# ---------------------------------------------------------------------- digest


class TestDigest(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.singles[("ERPNext Enhancements Settings", "maintenance_dispatch_digests")] = 1
		frappe.tables["Employee"] = [
			{"user_id": AUSTIN, "cell_number": "+1801000001"},
			{"user_id": KORBEN, "cell_number": "+1801000002"},
			{"user_id": DANIEL, "cell_number": "+1801000004"},
		]
		frappe.tables["Project"] = [
			{"name": "PRJ-1", "project_name": "Highlands"},
			{"name": "PRJ-2", "project_name": "Red Butte"},
		]
		frappe.tables[RECORD] = [
			{
				"name": "SMR-1",
				"technician": AUSTIN,
				"project": "PRJ-1",
				"customer": "Highlands HOA",
				"scheduled_visit_date": TODAY,
				"docstatus": 0,
				"dispatch_digest_sent_on": None,
			},
			{
				"name": "SMR-2",
				"technician": None,
				"project": "PRJ-2",
				"customer": "Red Butte",
				"scheduled_visit_date": TODAY,
				"docstatus": 0,
				"dispatch_digest_sent_on": None,
			},
		]
		frappe.tables[CREW] = [
			{
				"name": "row-k1",
				"parent": "SMR-1",
				"parenttype": RECORD,
				"user": KORBEN,
				"digest_sent_on": None,
				"idx": 1,
			},
			{
				"name": "row-d1",
				"parent": "SMR-1",
				"parenttype": RECORD,
				"user": DANIEL,
				"digest_sent_on": None,
				"idx": 2,
			},
			{
				"name": "row-k2",
				"parent": "SMR-2",
				"parenttype": RECORD,
				"user": KORBEN,
				"digest_sent_on": None,
				"idx": 1,
			},
		]

	def _apply_writes(self):
		for doctype, name, field, value in frappe.writes:
			for row in frappe.tables.get(doctype, []):
				if row.get("name") == name and field != "modified":
					row[field] = value

	def test_every_crew_member_gets_one_digest_with_who_they_are_with(self):
		dispatch.send_morning_digests()
		texts = dict(frappe.sms)
		# Austin (lead) and Korben (on two visits) get one text each; Daniel is disabled.
		self.assertEqual(set(texts), {"+1801000001", "+1801000002"})
		self.assertIn("1 maintenance visit(s)", texts["+1801000001"])
		self.assertIn("with Korben Dallas, Daniel Gone", texts["+1801000001"])
		self.assertIn("2 maintenance visit(s)", texts["+1801000002"])
		self.assertIn("with Austin Healey", texts["+1801000002"])
		self.assertIn("— crew", texts["+1801000002"])  # SMR-2 has no technician yet
		self.assertEqual(sorted(m["recipients"][0] for m in frappe.mails), [AUSTIN, KORBEN])

	def test_each_send_is_stamped_on_a_persisted_row_before_it_goes(self):
		dispatch.send_morning_digests()
		stamps = {(dt, name, field) for dt, name, field, value in frappe.writes if value == TODAY}
		self.assertIn((RECORD, "SMR-1", "dispatch_digest_sent_on"), stamps)
		self.assertIn((CREW, "row-k1", "digest_sent_on"), stamps)
		self.assertIn((CREW, "row-k2", "digest_sent_on"), stamps)
		self.assertNotIn((CREW, "row-d1", "digest_sent_on"), stamps)
		# A helper's stamp touches the record too, so a stale desk form cannot save a blank back.
		self.assertIn((RECORD, "SMR-2", "modified"), {(dt, n, f) for dt, n, f, v in frappe.writes})

	def test_a_second_run_the_same_day_texts_nobody(self):
		dispatch.send_morning_digests()
		self._apply_writes()
		frappe.sms, frappe.mails = [], []
		dispatch.send_morning_digests()
		self.assertEqual((frappe.sms, frappe.mails), ([], []))

	def test_a_helper_added_after_the_run_is_texted_by_the_next_and_nobody_else_is(self):
		dispatch.send_morning_digests()
		self._apply_writes()
		frappe.sms = []
		frappe.tables[CREW].append(
			{
				"name": "row-j1",
				"parent": "SMR-1",
				"parenttype": RECORD,
				"user": JESSE,
				"digest_sent_on": None,
				"idx": 3,
			}
		)
		frappe.tables["Employee"].append({"user_id": JESSE, "cell_number": "+1801000003"})
		dispatch.send_morning_digests()
		self.assertEqual([number for number, _text in frappe.sms], ["+1801000003"])

	def test_the_stamp_is_a_date_so_a_moved_visit_comes_back(self):
		frappe.tables[RECORD][0]["dispatch_digest_sent_on"] = TODAY - datetime.timedelta(days=3)
		frappe.tables[CREW][0]["digest_sent_on"] = TODAY - datetime.timedelta(days=3)
		dispatch.send_morning_digests()
		self.assertIn("+1801000001", dict(frappe.sms))

	def test_it_is_gated(self):
		frappe.singles = {}
		dispatch.send_morning_digests()
		self.assertEqual(frappe.sms, [])


# ---------------------------------------------------------------------- kiosk + claim


class TestKiosk(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.session.user = KORBEN
		frappe.tables[RECORD] = lambda filters=None, or_filters=None: [
			{"name": "SMR-1", "project": "PRJ-1", "serial_no": None, "visit_label": None}
		]
		frappe.tables["Project"] = [{"name": "PRJ-1", "project_name": "Highlands"}]

		def sql(query, values):
			if "tabSapphire Visit Crew Member" in query and values.get("user") == KORBEN:
				return [{"name": "SMR-1", "modified": "2026-10-12 09:00:00"}]
			return []

		frappe.sql_handler = sql

	def test_todays_visits_include_the_ones_i_am_crew_on(self):
		visits = kiosk.get_my_visits_today()
		self.assertEqual([v["name"] for v in visits], ["SMR-1"])
		_doctype, query = next(q for q in frappe.queries if q[0] == RECORD)
		self.assertIn(["name", "in", ["SMR-1"]], query["or_filters"])
		self.assertIn(["technician", "=", KORBEN], query["or_filters"])
		sql = next(q for q in frappe.queries if q[0] == "sql")
		self.assertEqual(sql[2]["docstatus"], 0)
		self.assertIn("LIMIT %(limit)s", sql[1])

	def test_a_crew_lookup_failure_leaves_the_list_standing(self):
		frappe.sql_handler = lambda query, values: (_ for _ in ()).throw(RuntimeError("no table"))
		visits = kiosk.get_my_visits_today()
		self.assertEqual([v["name"] for v in visits], ["SMR-1"])
		self.assertEqual(frappe.errors[0][1]["title"], "Maintenance visit crew lookup failed")


class TestClaim(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.session.user = KORBEN
		self.clocked = set()
		patcher = mock.patch.object(
			visit, "_clocked_into_project", lambda user, project: user in self.clocked
		)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_a_crew_member_takes_the_form_and_the_technician_joins_the_crew(self):
		doc = _record(
			crew=[{"user": KORBEN, "hours": 3, "digest_sent_on": TODAY}, JESSE],
			dispatch_digest_sent_on=TODAY - datetime.timedelta(days=1),
		)
		self.clocked = {KORBEN}
		self.assertEqual(visit._claim_visit(doc), "")  # "" -> no ToDo to move: both hold one
		self.assertEqual(doc.technician, KORBEN)
		self.assertEqual([r.user for r in doc.crew], [JESSE, AUSTIN])
		austin = doc.crew[1]
		# Austin joins with the visit's length, and the digest stamps swap with the places.
		self.assertEqual((austin.hours, austin.digest_sent_on), (0, TODAY - datetime.timedelta(days=1)))
		self.assertEqual(doc.dispatch_digest_sent_on, TODAY)

	def test_a_clocked_in_technician_is_never_displaced(self):
		doc = _record(crew=[KORBEN])
		self.clocked = {KORBEN, AUSTIN}
		self.assertIsNone(visit._claim_visit(doc))
		self.assertEqual(doc.technician, AUSTIN)

	def test_a_crew_member_peeking_before_clocking_in_does_not_claim(self):
		doc = _record(crew=[KORBEN])
		self.assertIsNone(visit._claim_visit(doc))
		self.assertEqual([r.user for r in doc.crew], [KORBEN])

	def test_a_substitute_from_outside_the_crew_still_replaces_the_technician(self):
		doc = _record(crew=[JESSE])
		self.clocked = {KORBEN}
		self.assertEqual(visit._claim_visit(doc), AUSTIN)  # truthy: the caller moves the ToDo
		self.assertEqual([r.user for r in doc.crew], [JESSE])

	def test_an_unassigned_visit_is_taken_by_the_crew_member(self):
		doc = _record(technician=None, crew=[KORBEN, JESSE])
		self.assertEqual(visit._claim_visit(doc), "")
		self.assertEqual(doc.technician, KORBEN)
		self.assertEqual([r.user for r in doc.crew], [JESSE])


# ---------------------------------------------------------------------- engine


def _settings(**overrides):
	out = dict(engine.DEFAULT_SETTINGS)
	out.update(overrides)
	return out


class TestEngineHours(unittest.TestCase):
	def test_the_rule_in_order(self):
		self.assertEqual(engine.visit_person_hours(3, True, 5, 2), (3.0, False))  # own row hours
		self.assertEqual(engine.visit_person_hours(0, True, 5, 2), (None, True))  # full day
		self.assertEqual(engine.visit_person_hours(None, False, 5, 2), (5.0, False))  # planned
		self.assertEqual(engine.visit_person_hours(0, False, 0, 2), (2.0, True))  # Settings default

	def test_a_full_day_is_the_capacity_or_the_settings_day(self):
		self.assertEqual(engine.full_day_hours(6, 8), 6.0)
		self.assertEqual(engine.full_day_hours(0, 8), 8.0)

	def test_the_planner_agrees_with_the_engine(self):
		for args in ((3, True, 5), (0, True, 5), (None, False, 5), (0, False, 0)):
			hours, _est = engine.visit_person_hours(*args, 2)
			expected = engine.full_day_hours(6, 8) if hours is None else hours
			self.assertEqual(planner.person_visit_hours(*args, 2, 6, 8), expected, args)


class TestEngineVisits(unittest.TestCase):
	def setUp(self):
		_reset()
		self.records = []
		self.crews = []

		def sql(query, values):
			if "plan_date BETWEEN" in query:
				return self.records
			if "FROM `tabSapphire Visit Crew Member`" in query:
				return [r for r in self.crews if r["parent"] in values["names"]]
			return []

		frappe.sql_handler = sql
		self.saved = (planner._projections, planner._decorate)
		planner._projections = lambda start, end, today: []
		planner._decorate = lambda cards: None
		self.addCleanup(lambda: setattr(planner, "_projections", self.saved[0]))
		self.addCleanup(lambda: setattr(planner, "_decorate", self.saved[1]))

	def _row(self, name, technician=AUSTIN, **values):
		row = {
			"name": name,
			"project": "PRJ-1",
			"visit_label": None,
			"technician": technician,
			"clock_in_time": None,
			"clock_out_time": None,
			"planned_hours": 0,
			"full_day": 0,
			"project_title": "Highlands Maintenance Contract",
			"plan_date": TODAY,
		}
		row.update(values)
		return row

	def _hours(self, users):
		got = engine._read_visits(users, TODAY, TODAY + datetime.timedelta(days=4), _settings())
		return {(v["ref"], v["user"]): (v["hours"], v["estimated"]) for v in got}

	def test_every_person_is_booked_with_their_hours(self):
		self.records = [
			self._row("SMR-1", planned_hours=3),
			self._row("SMR-2", full_day=1),
			self._row("SMR-3"),
		]
		self.crews = [
			{"parent": "SMR-1", "user": KORBEN, "hours": 0},
			{"parent": "SMR-1", "user": JESSE, "hours": 1.5},
			{"parent": "SMR-2", "user": KORBEN, "hours": 0},
			{"parent": "SMR-3", "user": KORBEN, "hours": 0},
		]
		self.assertEqual(
			self._hours([AUSTIN, KORBEN, JESSE]),
			{
				("SMR-1", AUSTIN): (3.0, False),
				("SMR-1", KORBEN): (3.0, False),
				("SMR-1", JESSE): (1.5, False),
				("SMR-2", AUSTIN): (None, True),  # full day: _compute turns it into capacity
				("SMR-2", KORBEN): (None, True),
				("SMR-3", AUSTIN): (2.0, True),
				("SMR-3", KORBEN): (2.0, True),
			},
		)

	def test_a_clocked_visit_books_the_clocked_length_for_the_technician_only(self):
		self.records = [
			self._row("SMR-1", clock_in_time="2026-10-12 08:00:00", clock_out_time="2026-10-12 12:30:00")
		]
		self.crews = [{"parent": "SMR-1", "user": KORBEN, "hours": 0}]
		got = engine._read_visits([AUSTIN, KORBEN], TODAY, TODAY, _settings())
		by_user = {v["user"]: v for v in got}
		self.assertEqual((by_user[AUSTIN]["hours"], by_user[AUSTIN]["slot"]), (4.5, ["08:00", "12:30"]))
		self.assertEqual((by_user[KORBEN]["hours"], by_user[KORBEN]["slot"]), (2.0, None))

	def test_a_helper_is_booked_even_when_the_technician_is_not_asked_about(self):
		self.records = [self._row("SMR-1")]
		self.crews = [{"parent": "SMR-1", "user": KORBEN, "hours": 4}]
		self.assertEqual(self._hours([KORBEN]), {("SMR-1", KORBEN): (4.0, False)})
		query = next(q for q in frappe.queries if q[0] == "sql" and "plan_date BETWEEN" in q[1])
		self.assertIn("tabSapphire Visit Crew Member", query[1])  # the crew counts in the WHERE

	def test_projected_visits_carry_the_profile_crew(self):
		self.records = []

		def projections(start, end, today):
			return [
				{
					"kind": "projected",
					"key": "C1||2026-10-14",
					"contract": "C1",
					"project": "PRJ-1",
					"date": "2026-10-14",
				}
			]

		def decorate(cards):
			for card in cards:
				card.update(
					site="Highlands",
					technician=AUSTIN,
					crew=[
						{"user": KORBEN, "name": "Korben", "hours": None},
						{"user": JESSE, "name": "J", "hours": 1},
					],
					planned_hours=None,
					full_day=True,
				)

		planner._projections, planner._decorate = projections, decorate
		self.assertEqual(
			self._hours([AUSTIN, KORBEN, JESSE]),
			{("C1", AUSTIN): (None, True), ("C1", KORBEN): (None, True), ("C1", JESSE): (1.0, False)},
		)

	def test_compute_turns_a_full_day_into_each_persons_capacity(self):
		people = [
			_Doc(name="RES-A", user=AUSTIN, employee="EMP-A", resource_name="Austin", resource_group="Field"),
			_Doc(name="RES-K", user=KORBEN, employee="EMP-K", resource_name="Korben", resource_group="Field"),
		]
		visits = [
			{
				"user": AUSTIN,
				"date": TODAY,
				"ref": "SMR-1",
				"label": "Highlands",
				"project": "PRJ-1",
				"hours": None,
				"slot": None,
				"estimated": True,
				"full_day": True,
			},
			{
				"user": KORBEN,
				"date": TODAY,
				"ref": "SMR-1",
				"label": "Highlands",
				"project": "PRJ-1",
				"hours": None,
				"slot": None,
				"estimated": True,
				"full_day": True,
			},
		]
		patterns = {"RES-A": [], "RES-K": []}
		with mock.patch.multiple(
			engine,
			get_settings=lambda: _settings(default_day_hours=8),
			_read_resources=lambda names=None: (people, patterns),
			read_tasks=lambda start, end: [],
			read_crews=lambda names: ({}, {}),
			_read_holidays=lambda p, s, e: {},
			_read_time_off=lambda p, s, e: {"RES-K": {TODAY: "Half day off"}},
			_read_restrictions=lambda p, s, e: {},
			_read_visits=lambda users, start, end, settings: [dict(v) for v in visits],
			_read_travel=lambda employees, start, end: [],
			_apply_routes=lambda *a, **k: {},
			pattern_hours=lambda patterns, day, default=8.0: 8.0,
			day_capacity=lambda base, holiday=None, time_off=None: (base / 2, "Half day off")
			if time_off
			else (base, None),
		):
			data = engine.availability(TODAY, TODAY)
		cells = {name: per_day[str(TODAY)] for name, per_day in data["days"].items()}
		self.assertEqual(cells["RES-A"]["booked"], 8.0)
		self.assertEqual(cells["RES-K"]["booked"], 4.0)  # half a day off: half a day's visit


# ---------------------------------------------------------------------- planner


class TestPlannerCards(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_cards_carry_the_crew_and_the_leads_effective_hours(self):
		cards = [
			planner._visit_card(
				_Doc(
					name="SMR-1",
					docstatus=0,
					workflow_state="Draft",
					plan_date=TODAY,
					project="PRJ-1",
					maintenance_contract="C1",
					customer="H",
					serial_no=None,
					visit_label=None,
					technician=AUSTIN,
					visit_date=None,
					completion_percent=0,
					has_out_of_range_readings=0,
					modified="m",
					planned_hours=0,
					full_day=1,
					clock_in_time=None,
					clock_out_time=None,
				),
				TODAY,
				True,
			),
			planner._visit_card(
				_Doc(
					name="SMR-2",
					docstatus=0,
					workflow_state="Draft",
					plan_date=TODAY,
					project="PRJ-1",
					maintenance_contract="C1",
					customer="H",
					serial_no=None,
					visit_label=None,
					technician=AUSTIN,
					visit_date=None,
					completion_percent=0,
					has_out_of_range_readings=0,
					modified="m",
					planned_hours=3,
					full_day=0,
					clock_in_time=None,
					clock_out_time=None,
				),
				TODAY,
				True,
			),
		]
		frappe.sql_handler = lambda query, values: (
			[
				{
					"name": "r1",
					"parent": "SMR-1",
					"user": KORBEN,
					"full_name": "Korben Dallas",
					"hours": 0,
					"idx": 1,
				},
				{
					"name": "r2",
					"parent": "SMR-1",
					"user": JESSE,
					"full_name": "Jesse Pinkman",
					"hours": 2,
					"idx": 2,
				},
			]
			if "tabSapphire Visit Crew Member" in query
			else []
		)
		planner._attach_crews(cards)
		with mock.patch.object(planner, "_hour_settings", lambda: (2.0, 8.0)):
			planner._set_lead_hours(cards, {AUSTIN: {str(TODAY): {"capacity": 6.0}}})
		self.assertEqual(
			cards[0]["crew"],
			[
				{"user": KORBEN, "name": "Korben Dallas", "hours": None},
				{"user": JESSE, "name": "Jesse Pinkman", "hours": 2.0},
			],
		)
		self.assertEqual((cards[0]["full_day"], cards[0]["hours"]), (True, 6.0))
		self.assertEqual((cards[1]["crew"], cards[1]["planned_hours"], cards[1]["hours"]), ([], 3.0, 3.0))

	def test_projected_cards_carry_the_profile_default(self):
		_profile_tables()
		frappe.tables["Project"] = [{"name": "PRJ-1", "project_name": "Highlands Maintenance Contract"}]
		cards = [{"kind": "projected", "project": "PRJ-1", "contract": "C1", "date": str(TODAY)}]
		planner._decorate(cards)
		self.assertEqual(cards[0]["technician"], AUSTIN)
		self.assertEqual(
			cards[0]["crew"],
			[
				{"user": KORBEN, "name": "Korben Dallas", "hours": None},
				{"user": JESSE, "name": "Jesse Pinkman", "hours": 2.5},
			],
		)
		self.assertEqual((cards[0]["planned_hours"], cards[0]["full_day"]), (4.0, True))

	def test_crew_members_get_a_row_in_the_crew_view(self):
		frappe.tables["Has Role"] = []
		frappe.tables["Employee"] = []
		people = planner._technicians([{"technician": AUSTIN, "crew": [{"user": KORBEN}]}])
		self.assertEqual({p["user"] for p in people}, {AUSTIN, KORBEN})


class _Conflicts:
	"""``visit_conflicts`` answered from a table: ``{user: hours that would be over}``."""

	def __init__(self, over=None):
		self.over = over or {}
		self.calls = []

	def __call__(self, user, day, ref, label=None, hours=None, full_day=False):
		self.calls.append((user, hours, full_day))
		return {user: [f"{day}: Over"]} if user in self.over else {}


class TestMoveVisit(unittest.TestCase):
	def setUp(self):
		_reset()
		self.conflicts = _Conflicts()
		patcher = mock.patch.object(planner, "visit_conflicts", self.conflicts)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_a_date_move_checks_every_person_on_the_visit(self):
		_record(crew=[KORBEN, {"user": JESSE, "hours": 1}], planned_hours=3)
		planner.move_visit("SMR-1", date="2026-10-14")
		self.assertEqual(
			self.conflicts.calls, [(AUSTIN, 3.0, False), (KORBEN, 3.0, False), (JESSE, 1.0, False)]
		)

	def test_a_full_day_visit_checks_each_persons_whole_day(self):
		_record(crew=[KORBEN], full_day=1)
		planner.move_visit("SMR-1", date="2026-10-14")
		self.assertEqual(self.conflicts.calls, [(AUSTIN, None, True), (KORBEN, None, True)])

	def test_adding_to_the_crew_checks_only_the_new_person_and_asks_for_a_reason(self):
		_record(crew=[KORBEN])
		self.conflicts.over = {JESSE}
		result = planner.move_visit("SMR-1", crew=json.dumps([KORBEN, {"user": JESSE, "hours": 2}]))
		self.assertEqual(self.conflicts.calls, [(JESSE, 2.0, False)])
		self.assertEqual(result, {"needs_reason": True, "conflicts": {JESSE: ["2026-10-12: Over"]}})
		self.assertEqual(frappe.saved, [])
		_record(crew=[KORBEN])  # the page asks again with the reason; each request loads afresh
		result = planner.move_visit(
			"SMR-1", crew=json.dumps([KORBEN, {"user": JESSE, "hours": 2}]), reason="Big job"
		)
		self.assertEqual(
			result["crew"],
			[{"user": KORBEN, "name": KORBEN, "hours": None}, {"user": JESSE, "name": JESSE, "hours": 2.0}],
		)
		self.assertIn("Reason: Big job", frappe.comments[0][2])

	def test_changing_the_length_checks_everyone(self):
		_record(crew=[KORBEN])
		result = planner.move_visit("SMR-1", planned_hours="5", full_day=0)
		self.assertEqual(self.conflicts.calls, [(AUSTIN, 5.0, False), (KORBEN, 5.0, False)])
		self.assertEqual((result["planned_hours"], result["full_day"]), (5.0, False))
		result = planner.move_visit("SMR-1", full_day="true")
		self.assertTrue(result["full_day"])

	def test_an_unchanged_crew_saves_nothing(self):
		_record(crew=[KORBEN])
		planner.move_visit("SMR-1", crew=[KORBEN], planned_hours="", full_day=0)
		self.assertEqual((frappe.saved, self.conflicts.calls), ([], []))

	def test_dragging_a_helper_swaps_that_helper_only(self):
		_record(crew=[{"user": KORBEN, "hours": 3}])
		result = planner.move_visit("SMR-1", technician=JESSE, from_user=KORBEN)
		self.assertEqual(result["technician"], AUSTIN)
		self.assertEqual(result["crew"], [{"user": JESSE, "name": JESSE, "hours": 3.0}])
		self.assertEqual(self.conflicts.calls, [(JESSE, 3.0, False)])
		# Assignments follow through the on_update mirror; the technician's ToDo is not moved.
		self.assertEqual(frappe.assigned, [])

	def test_dragging_the_technician_hands_the_visit_over(self):
		_record(crew=[KORBEN])
		result = planner.move_visit("SMR-1", technician=JESSE, from_user=AUSTIN)
		self.assertEqual((result["technician"], [c["user"] for c in result["crew"]]), (JESSE, [KORBEN]))
		self.assertEqual(frappe.assigned, [("remove", "SMR-1", AUSTIN), ("add", "SMR-1", JESSE)])

	def test_a_technician_handed_over_who_stays_as_crew_keeps_their_todo(self):
		_record(crew=[KORBEN])
		_todo("SMR-1", AUSTIN)
		_todo("SMR-1", KORBEN)
		planner.move_visit("SMR-1", technician=KORBEN, crew=[AUSTIN])
		self.assertEqual(frappe.assigned, [])

	def test_a_helper_who_left_meanwhile_is_refused(self):
		_record(crew=[KORBEN])
		with self.assertRaises(_Throw):
			planner.move_visit("SMR-1", technician=JESSE, from_user=LISA)

	def test_an_inactive_crew_member_is_refused(self):
		_record()
		with self.assertRaises(_Throw):
			planner.move_visit("SMR-1", crew=[DANIEL])


class TestAddCrew(unittest.TestCase):
	def setUp(self):
		_reset()
		self.conflicts = _Conflicts()
		patcher = mock.patch.object(planner, "visit_conflicts", self.conflicts)
		patcher.start()
		self.addCleanup(patcher.stop)
		frappe.tables[RECORD] = [{"name": "SMR-1"}]
		frappe.tables["Sapphire Maintenance Contract"] = [{"name": "C1"}]

	def test_a_helper_is_added(self):
		_record(crew=[KORBEN])
		result = planner.add_crew("SMR-1", JESSE, "2026-10-12 09:00:00")
		self.assertEqual([c["user"] for c in result["crew"]], [KORBEN, JESSE])
		self.assertEqual(self.conflicts.calls, [(JESSE, None, False)])
		self.assertEqual(len(frappe.saved), 1)

	def test_an_overbooking_asks_for_a_reason(self):
		_record()
		self.conflicts.over = {JESSE}
		self.assertTrue(planner.add_crew("SMR-1", JESSE)["needs_reason"])
		self.assertEqual(frappe.saved, [])
		_record()
		planner.add_crew("SMR-1", JESSE, reason="Heavy pump")
		self.assertEqual(len(frappe.saved), 1)

	def test_someone_already_on_the_visit_changes_nothing(self):
		_record(crew=[KORBEN])
		planner.add_crew("SMR-1", KORBEN)
		planner.add_crew("SMR-1", AUSTIN)
		self.assertEqual(frappe.saved, [])

	def test_refusals(self):
		cases = [
			("C1|SN-1|2026-10-14", {}, "default crew on its Maintenance Profile"),
			("C1", {}, "default crew on its Maintenance Profile"),
			("SMR-1", {"docstatus": 1, "workflow_state": "Final/Submitted"}, "already finished"),
			("SMR-1", {"workflow_state": "Pending Review"}, "already finished"),
			("SMR-1", {"visit_date": TODAY}, "was started"),
		]
		for record, values, message in cases:
			with self.subTest(record=record, values=values):
				_record(**values)
				with self.assertRaises(_Throw) as caught:
					planner.add_crew(record, JESSE)
				self.assertIn(message, str(caught.exception))
		_record()
		with self.assertRaises(_Throw):
			planner.add_crew("SMR-1", DANIEL)  # disabled
		with self.assertRaises(_Throw):
			planner.add_crew("SMR-1", JESSE, modified="2026-10-01 00:00:00")  # stale
		self.assertEqual(frappe.saved, [])

	def test_it_is_post_only(self):
		source = (APP / "api" / "maintenance_planner.py").read_text(encoding="utf-8")
		self.assertIn('@frappe.whitelist(methods=["POST"])\ndef add_crew(', source)


if __name__ == "__main__":
	unittest.main()
