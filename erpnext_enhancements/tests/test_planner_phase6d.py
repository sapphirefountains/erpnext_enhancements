# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Project Planner Phase 6D (TASK-2026-02470): personal blocks, day notes and the Conflict center,
for both planners.

What this pins, because each of them fails quietly:

* **A block's note never leaks.** The engine reads ``Planner Block`` without its note; a booking,
  a conflict sentence and a whole engine answer carry "Unavailable" and the window only. Notes are
  attached by ``crew_availability.block_notes`` alone: a scheduler reads every note, a technician
  only their own, and the AI tool none at all.
* **Own-or-scheduler** on every block write, an existing block included; day notes are schedulers'.
* **Blocks count like the rules say.** An all-day block is a day off ("Unavailable"), a timed one
  books its hours and clashes with an overlapping firm slot, a block alone is never a conflict, and
  a pencil never conflicts.
* **next_free_day** skips days off, blocks and full days, gives a task its own hours back, and stops
  at the horizon.
* **The Conflict center** makes one engine pass with Google off, keys each conflict stably, marks
  ``own`` and offers fixes only for the calling planner's own records (no pencil for a visit, no fix
  on a rental), and an acknowledgement hides a conflict only until its fingerprint changes. A reason
  is required and the timeline comment is escaped.
* **Day notes** reach the planners' payloads and the digests by audience, and the combined digest
  still goes at most once a day.

Bench-free: installs its own ``frappe`` stub (plus ``email_style``, telephony and
``frappe_assistant_core`` stand-ins), runs the real modules, and puts ``sys.modules`` back.

Run: python -m unittest erpnext_enhancements.tests.test_planner_phase6d
"""

import ast
import contextlib
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
DOCTYPES = APP / "project_enhancements" / "doctype"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
HOOKS = APP / "hooks.py"
PP_PATH = APP / "api" / "project_planner.py"
MP_PATH = APP / "api" / "maintenance_planner.py"

D = datetime.date
TODAY = D(2026, 10, 9)  # a Friday
NOW = datetime.datetime(2026, 10, 9, 6, 0, 0)
MON, TUE, WED, THU, FRI = (D(2026, 10, 12) + datetime.timedelta(days=i) for i in range(5))
SAT, SUN = D(2026, 10, 17), D(2026, 10, 18)

AUSTIN, KORBEN, LISA = "austin@example.com", "korben@example.com", "lisa@example.com"
PM = "pm@example.com"
NOTE = "Dentist appointment"
OTHER_NOTE = "School pickup"

_saved_modules = {}
_modules_before = set()
frappe = None
engine = None
routing = None
pp = None
mp = None
blocks = None
conflicts = None
digest = None
report = None
dispatch = None
rental = None
tool_module = None
gate = None
block_controller = None
note_controller = None
email_style = None
telephony = None


class _Throw(Exception):
	pass


class _PermissionError(Exception):
	pass


class _Doc(dict):
	"""Enough of a frappe Document (and of frappe._dict): dict and attribute access, a missing
	field reads None, insert/save/delete and comments."""

	def __getattr__(self, name):
		if name.startswith("__"):
			raise AttributeError(name)
		return self.get(name)

	def __setattr__(self, name, value):
		self[name] = value

	def set(self, key, value):
		self[key] = value

	def is_new(self):
		return bool(self.get("__islocal"))

	def check_permission(self, ptype):
		frappe.calls.append(("check_permission", ptype, self.get("doctype"), self.get("name")))
		if self.get("name") in frappe.denied:
			raise _PermissionError(ptype)

	def add_comment(self, kind, text):
		frappe.comments.append((self.get("doctype"), self.get("name"), kind, text))

	def _run_validate(self):
		validate = getattr(type(self), "validate", None)
		if validate:
			validate(self)

	def insert(self, ignore_permissions=False, **kwargs):
		self._run_validate()
		if not self.get("name"):
			frappe.counter += 1
			self["name"] = (
				self.get("log_key") or f"{_PREFIX.get(self.get('doctype'), 'DOC')}-{frappe.counter:05d}"
			)
		self["__islocal"] = 0
		self["modified"] = f"2026-10-09 06:00:{frappe.counter % 60:02d}"
		frappe.docs[(self.get("doctype"), self["name"])] = self
		frappe.inserted.append(self)
		return self

	def save(self, ignore_permissions=False, **kwargs):
		if self.is_new():
			return self.insert(ignore_permissions=ignore_permissions)
		self._run_validate()
		frappe.counter += 1
		self["modified"] = f"2026-10-09 07:00:{frappe.counter % 60:02d}"
		frappe.docs[(self.get("doctype"), self["name"])] = self
		frappe.saved.append(self)
		return self


_PREFIX = {
	"Planner Block": "PBLK-2026",
	"Planner Day Note": "PDN-2026",
	"Planner Conflict Ack": "PCA-2026",
}

BLOCK_FIELDS = ("resource", "resource_name", "user", "date", "all_day", "from_time", "to_time", "note")
NOTE_FIELDS = ("date", "note", "audience", "project", "project_title")


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


def _fake_email_style():
	module = types.ModuleType("erpnext_enhancements.email_style")
	module.p = lambda text: f"<p>{text}</p>"
	module.table = lambda headers, rows: "<table>" + "".join(f"<tr>{r}</tr>" for r in rows) + "</table>"
	module.bullets = lambda items: "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"
	module.button = lambda url, label, tone="primary": f"<a href='{url}'>{label}</a>"
	module.links = lambda items: "".join(f"<a href='{u}'>{label}</a>" for u, label in items)
	module.note = lambda text: f"<small>{text}</small>"
	module.code = lambda text: f"<code>{text}</code>"
	module.wrap = lambda body, **kwargs: f"<shell>{body}</shell>"
	return module


def _fake_telephony():
	module = types.ModuleType("erpnext_enhancements.api.telephony")
	module.sent = []
	module.send_system_sms = lambda number, message: module.sent.append((number, message))
	return module


def _fake_fac():
	base_tool = types.ModuleType("frappe_assistant_core.core.base_tool")

	class BaseTool:
		def __init__(self):
			self.name = ""
			self.description = ""
			self.inputSchema = {}
			self.requires_permission = None
			self.category = "Custom"
			self.source_app = "frappe_assistant_core"

	base_tool.BaseTool = BaseTool
	fac = types.ModuleType("frappe_assistant_core")
	fac_core = types.ModuleType("frappe_assistant_core.core")
	fac.core, fac_core.base_tool = fac_core, base_tool
	return {
		"frappe_assistant_core": fac,
		"frappe_assistant_core.core": fac_core,
		"frappe_assistant_core.core.base_tool": base_tool,
	}


def setUpModule():
	global frappe, engine, routing, pp, mp, blocks, conflicts, digest, report, dispatch, rental
	global tool_module, gate, block_controller, note_controller, email_style, telephony
	_modules_before.update(sys.modules)
	for name in list(sys.modules):
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements", "frappe_assistant_core")):
			_saved_modules[name] = sys.modules.pop(name)

	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.getdate = _getdate
	utils.get_datetime = lambda value=None: value
	utils.add_days = lambda value, days: _getdate(value) + datetime.timedelta(days=days)
	utils.date_diff = lambda a, b: (_getdate(a) - _getdate(b)).days
	utils.formatdate = lambda value=None, *a, **k: _getdate(value).strftime("%m-%d-%Y")
	utils.format_datetime = lambda value=None, *a, **k: str(value)
	utils.nowdate = lambda: str(TODAY)
	utils.now_datetime = lambda: NOW
	utils.flt = lambda value, precision=None: float(value or 0)
	utils.cint = lambda value: int(float(value or 0))
	utils.strip_html_tags = lambda text: re.sub(r"<[^>]+>", "", str(text))
	utils.escape_html = lambda text: (
		str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
	)
	utils.get_url = lambda path="": "https://erp.example.com" + (path or "")
	utils.get_fullname = lambda user=None: user
	utils.validate_email_address = lambda value, throw=False: value if "@" in str(value or "") else ""
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe._dict = _Doc
	frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	frappe.throw = _throw
	frappe.PermissionError = _PermissionError
	frappe.get_traceback = lambda *a, **k: "Traceback: boom"
	frappe.parse_json = lambda value: json.loads(value) if isinstance(value, str) else value
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Doc
	model.document = document
	frappe.model = model
	sys.modules.update(
		{"frappe": frappe, "frappe.utils": utils, "frappe.model": model, "frappe.model.document": document}
	)
	email_style = _fake_email_style()
	sys.modules["erpnext_enhancements.email_style"] = email_style
	telephony = _fake_telephony()
	sys.modules["erpnext_enhancements.api.telephony"] = telephony
	sys.modules.update(_fake_fac())
	_reset()

	engine = importlib.import_module("erpnext_enhancements.project_enhancements.crew_availability")
	routing = importlib.import_module("erpnext_enhancements.project_enhancements.routing")
	mp = importlib.import_module("erpnext_enhancements.api.maintenance_planner")
	pp = importlib.import_module("erpnext_enhancements.api.project_planner")
	blocks = importlib.import_module("erpnext_enhancements.api.planner_blocks")
	conflicts = importlib.import_module("erpnext_enhancements.api.planner_conflicts")
	digest = importlib.import_module("erpnext_enhancements.project_enhancements.planner_digest")
	report = importlib.import_module(
		"erpnext_enhancements.project_enhancements.report.crew_utilization.crew_utilization"
	)
	dispatch = importlib.import_module("erpnext_enhancements.api.maintenance_dispatch")
	rental = importlib.import_module("erpnext_enhancements.asset_management.rental_logistics")
	gate = importlib.import_module("erpnext_enhancements.assistant_tools._gate")
	tool_module = importlib.import_module("erpnext_enhancements.assistant_tools.crew_conflicts")
	block_controller = importlib.import_module(
		"erpnext_enhancements.project_enhancements.doctype.planner_block.planner_block"
	)
	note_controller = importlib.import_module(
		"erpnext_enhancements.project_enhancements.doctype.planner_day_note.planner_day_note"
	)


def tearDownModule():
	for name in set(sys.modules) - _modules_before:
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements", "frappe_assistant_core")):
			sys.modules.pop(name, None)
	for name in list(sys.modules):
		if name == "frappe" or name.startswith(("frappe.", "frappe_assistant_core")):
			sys.modules.pop(name, None)
	sys.modules.update(_saved_modules)


# ---------------------------------------------------------------------- the fake site


def _matches(row, filters):
	for key, wanted in (filters or {}).items():
		value = row.get(key)
		if isinstance(wanted, list | tuple) and len(wanted) == 2 and isinstance(wanted[0], str):
			op, operand = wanted
			if op == "in":
				if value not in operand:
					return False
			elif op == "between":
				low, high = (_getdate(v) for v in operand)
				if value is None or not (low <= _getdate(value) <= high):
					return False
			elif op == "is":
				if (operand == "set") != bool(value):
					return False
			elif op == "!=":
				if value == operand:
					return False
			continue
		if value != wanted:
			return False
	return True


def _rows(doctype):
	rows = [doc for (dt, _name), doc in frappe.docs.items() if dt == doctype]
	return rows + [_Doc(r) for r in frappe.tables.get(doctype, [])]


def _get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, **kwargs):
	frappe.queries.append((doctype, filters, list(fields) if fields else fields))
	rows = [r for r in _rows(doctype) if _matches(r, filters)]
	if order_by:
		field, _space, direction = str(order_by).split(",")[0].strip().partition(" ")
		rows.sort(key=lambda r: str(r.get(field) or ""), reverse=direction.strip().lower() == "desc")
	limit = kwargs.get("limit_page_length")
	if limit:
		rows = rows[:limit]
	if pluck:
		return [r.get(pluck) for r in rows]
	if fields:
		return [_Doc({f: r.get(f) for f in fields}) for r in rows]
	return [_Doc(r) for r in rows]


def _find(doctype, name):
	if isinstance(name, dict):
		found = [r for r in _rows(doctype) if _matches(r, name)]
		return found[0] if found else None
	doc = frappe.docs.get((doctype, name))
	if doc is not None:
		return doc
	return next((_Doc(r) for r in frappe.tables.get(doctype, []) if r.get("name") == name), None)


def _get_value(doctype, name, field=None, *args, as_dict=False, **kwargs):
	row = _find(doctype, name)
	if row is None:
		return None
	if isinstance(field, list | tuple):
		out = _Doc({f: row.get(f) for f in field})
		return out if as_dict else tuple(out.values())
	return row.get(field)


def _controller(doctype):
	if doctype == "Planner Block" and block_controller is not None:
		return block_controller.PlannerBlock
	if doctype == "Planner Day Note" and note_controller is not None:
		return note_controller.PlannerDayNote
	return _Doc


def _new_doc(doctype):
	fields = {"Planner Block": BLOCK_FIELDS, "Planner Day Note": NOTE_FIELDS}.get(doctype, ())
	doc = _controller(doctype)({f: None for f in fields})
	doc.update({"doctype": doctype, "__islocal": 1, "flags": types.SimpleNamespace(), "name": None})
	return doc


def _get_doc(doctype, name=None):
	if isinstance(doctype, dict):
		doc = _controller(doctype.get("doctype"))(doctype)
		doc.update({"__islocal": 1, "flags": types.SimpleNamespace()})
		return doc
	if (doctype, name) in frappe.docs:
		return frappe.docs[(doctype, name)]
	row = next((r for r in frappe.tables.get(doctype, []) if r.get("name") == name), None)
	if row is None:
		raise _Throw(f"{doctype} {name} not found")
	# Promoted from the table to a document, once: later reads see the document, not both.
	frappe.tables[doctype] = [r for r in frappe.tables[doctype] if r is not row]
	doc = _controller(doctype)(row)
	doc.setdefault("doctype", doctype)
	doc.setdefault("flags", types.SimpleNamespace())
	frappe.docs[(doctype, name)] = doc
	return doc


def _delete_doc(doctype, name, ignore_permissions=False, **kwargs):
	frappe.deleted.append((doctype, name, ignore_permissions))
	frappe.docs.pop((doctype, name), None)
	frappe.tables[doctype] = [r for r in frappe.tables.get(doctype, []) if r.get("name") != name]


def _reset():
	frappe.user_roles = {
		PM: ["Projects Manager"],
		AUSTIN: ["Maintenance User"],
		KORBEN: ["Maintenance User"],
		LISA: ["Projects User"],
		"tech-only@example.com": ["Maintenance User"],
		"stranger@example.com": ["Accounts User"],
	}
	frappe.get_roles = lambda user=None: list(frappe.user_roles.get(user or frappe.session.user, []))
	frappe.session = types.SimpleNamespace(user=PM)
	frappe.local = types.SimpleNamespace(message_log=[], flags=types.SimpleNamespace(commit=False))
	frappe.flags = types.SimpleNamespace(mute_messages=False)
	frappe.docs, frappe.tables = {}, {}
	frappe.calls, frappe.comments, frappe.errors, frappe.queries = [], [], [], []
	frappe.inserted, frappe.saved, frappe.deleted, frappe.sent, frappe.db_calls = [], [], [], [], []
	frappe.denied = set()
	frappe.missing_tables = set()
	frappe.counter = 0
	frappe.log_error = lambda *a, **k: frappe.errors.append((a, k))
	frappe.get_all = _get_all
	frappe.get_list = _get_all
	frappe.get_doc = _get_doc
	frappe.new_doc = _new_doc
	frappe.delete_doc = _delete_doc
	frappe.get_cached_doc = lambda doctype, name=None: _Doc({})
	frappe.has_permission = lambda *a, **k: True
	frappe.msgprint = lambda message, *a, **k: frappe.local.message_log.append(
		json.dumps({"message": message})
	)
	frappe.sendmail = lambda **kwargs: frappe.sent.append(kwargs)
	frappe.enqueue = lambda *a, **k: None
	frappe.get_system_settings = lambda key: "Sunday"
	frappe.defaults = types.SimpleNamespace(
		get_user_default=lambda key: None, get_global_default=lambda key: None
	)
	frappe.db = types.SimpleNamespace(
		has_column=lambda doctype, column: True,
		table_exists=lambda doctype: doctype not in frappe.missing_tables,
		get_value=_get_value,
		set_value=lambda doctype, name, field, value=None, **k: frappe.db_calls.append(
			("set_value", doctype, name, field, value)
		),
		exists=lambda doctype, name=None: _find(doctype, name) is not None if doctype != "DocType" else True,
		sql=lambda *a, **k: [],
		commit=lambda: frappe.db_calls.append("commit"),
		rollback=lambda *a, **k: frappe.db_calls.append("rollback"),
		get_single_value=lambda *a, **k: None,
	)
	if telephony is not None:
		telephony.sent.clear()
	_people_table()


PEOPLE = [
	{
		"name": "RES-1",
		"resource_name": "Austin Healey",
		"user": AUSTIN,
		"employee": "EMP-1",
		"resource_group": "Field",
		"is_active": 1,
	},
	{
		"name": "RES-2",
		"resource_name": "Korben Fox",
		"user": KORBEN,
		"employee": "EMP-2",
		"resource_group": "Field",
		"is_active": 1,
	},
	{
		"name": "RES-3",
		"resource_name": "Lisa Park",
		"user": LISA,
		"employee": "EMP-3",
		"resource_group": "PM",
		"is_active": 1,
	},
	{
		"name": "RES-9",
		"resource_name": "Gone Person",
		"user": "gone@example.com",
		"employee": "EMP-9",
		"resource_group": "Field",
		"is_active": 0,
	},
]


def _people_table():
	frappe.tables["Planner Resource"] = [dict(p) for p in PEOPLE]
	frappe.tables["User"] = [
		{"name": AUSTIN, "full_name": "Austin Healey", "enabled": 1},
		{"name": KORBEN, "full_name": "Korben Fox", "enabled": 1},
		{"name": LISA, "full_name": "Lisa Park", "enabled": 1},
		{"name": PM, "full_name": "Pat Manager", "enabled": 1},
	]


def _block(name, resource, day, note=None, all_day=1, start=None, end=None, user=None):
	person = next(p for p in PEOPLE if p["name"] == resource)
	row = {
		"name": name,
		"doctype": "Planner Block",
		"resource": resource,
		"resource_name": person["resource_name"],
		"user": user if user is not None else person["user"],
		"date": day,
		"all_day": all_day,
		"from_time": start,
		"to_time": end,
		"note": note,
		"modified": "2026-10-08 10:00:00",
	}
	frappe.tables.setdefault("Planner Block", []).append(row)
	return row


def _day_note(name, day, note, audience=None, project=None, project_title=None):
	row = {
		"name": name,
		"date": day,
		"note": note,
		"audience": audience,
		"project": project,
		"project_title": project_title,
		"modified": "2026-10-08 10:00:00",
	}
	frappe.tables.setdefault("Planner Day Note", []).append(row)
	return row


def _task(name, day, hours=0, end=None, slot=None, tentative=0, subject=None, rental=None, **values):
	row = _Doc(
		{
			"name": name,
			"subject": subject or f"Job {name}",
			"project": "PRJ-1",
			"project_title": "Riverwalk",
			"status": "Open",
			"exp_start_date": str(day),
			"exp_end_date": str(end or day),
			"expected_time": hours,
			"custom_start_datetime": f"{day} {slot[0]}:00" if slot else None,
			"custom_end_datetime": f"{day} {slot[1]}:00" if slot else None,
			"custom_tentative": tentative,
			"custom_rental_booking": rental,
			"custom_rental_task_kind": "Setup" if rental else None,
			"custom_crew_size": 0,
			"project_type": "Build",
			"planner_stream": 0,
			"modified": "2026-10-01 09:00:00",
		}
	)
	row.update(values)
	return row


@contextlib.contextmanager
def _engine(tasks=(), crews=None, visits=(), travel=(), equipment=None, people=None):
	"""Run the real ``_compute`` over fake readers. Blocks come from ``frappe.tables`` through the
	real ``_read_blocks``; ``crews`` is ``{task: [resource, ...]}``."""
	people = people if people is not None else [p for p in PEOPLE if p["is_active"]]
	crews = crews or {}
	route_calls = []

	def read_resources(names=None):
		chosen = [p for p in people if names is None or p["name"] in names]
		return [_Doc(p) for p in chosen], {}

	def read_tasks(start, end):
		return [
			_Doc(t)
			for t in tasks
			if engine._range_overlaps(*(engine.task_span(t) or (None, None)), start, end)
		]

	def read_crews(names):
		return {n: [{"resource": r, "hours": 0} for r in crews.get(n, [])] for n in names}, {}

	def read_visits(users, start, end, settings):
		return [dict(v) for v in visits if v["user"] in users and start <= v["date"] <= end]

	def read_travel(employees, start, end):
		return [dict(t) for t in travel if t["employee"] in employees and start <= t["date"] <= end]

	def plan_routes(day_bookings, settings=None, **kwargs):
		route_calls.append((dict(day_bookings), kwargs))
		return {}

	equipment = equipment or {}
	with contextlib.ExitStack() as stack:
		for name, value in (
			("get_settings", lambda: dict(engine.DEFAULT_SETTINGS)),
			("_read_resources", read_resources),
			("read_tasks", read_tasks),
			("read_crews", read_crews),
			("_read_holidays", lambda *a: {}),
			("_read_time_off", lambda *a: {}),
			("_read_restrictions", lambda *a: {}),
			("_read_visits", read_visits),
			("_read_travel", read_travel),
			("read_equipment", lambda names: {n: list(equipment[n]) for n in names if n in equipment}),
			("read_equipment_details", lambda refs: ({ref: ref[1] for ref in refs}, {})),
			("read_asset_bookings", lambda *a: []),
		):
			stack.enter_context(mock.patch.object(engine, name, value))
		stack.enter_context(mock.patch.object(engine.routing, "plan_routes", plan_routes))
		yield route_calls


def _cell(data, resource, day):
	return data["days"][resource][str(day)]


# ====================================================================== pure block math


class TestBlockMath(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_the_window_reads_as_people_say_it(self):
		self.assertEqual(engine.block_window(["14:00", "16:00"]), "2–4 pm")
		self.assertEqual(engine.block_window(["09:30", "11:00"]), "9:30–11 am")
		self.assertEqual(engine.block_window(["11:00", "13:30"]), "11 am–1:30 pm")
		self.assertEqual(engine.block_window(["12:00", "13:00"]), "12–1 pm")
		self.assertIsNone(engine.block_window(None))

	def test_time_values_from_every_source(self):
		for value, minutes in (
			(datetime.timedelta(hours=14), 840),
			(datetime.time(9, 30), 570),
			("14:00:00", 840),
			("7:05", 425),
			("", None),
			(None, None),
			("soon", None),
		):
			self.assertEqual(engine.time_minutes(value), minutes, value)

	def test_an_all_day_block_takes_the_day_and_books_nothing(self):
		rows = [
			{"name": "B1", "all_day": 1},
			{"name": "B2", "all_day": 0, "from_time": "14:00", "to_time": "16:00"},
		]
		got = engine.block_bookings(rows, 8)
		self.assertEqual(
			[(b["ref"], b["hours"], b["all_day"], b["slot"]) for b in got], [("B1", 0.0, True, None)]
		)
		self.assertEqual(engine.blocked_capacity(8, None), (0.0, "Unavailable"))
		self.assertEqual(engine.blocked_capacity(4, "Half day off"), (0.0, "Unavailable"))
		self.assertEqual(engine.blocked_capacity(0, "Holiday: Columbus Day"), (0.0, "Holiday: Columbus Day"))

	def test_timed_blocks_count_their_hours_once_and_never_past_the_day(self):
		rows = [
			{"name": "B1", "all_day": 0, "from_time": "13:00", "to_time": "16:00"},
			{"name": "B2", "all_day": 0, "from_time": "15:00", "to_time": "17:00"},
		]
		self.assertEqual([b["hours"] for b in engine.block_bookings(rows, 8)], [3.0, 1.0])
		self.assertEqual([b["hours"] for b in engine.block_bookings(rows, 2.5)], [2.5, 0.0])
		self.assertEqual(engine.block_bookings(rows, 8)[0]["window"], "1–4 pm")
		# A timed row with no usable window is ignored rather than guessed at.
		self.assertEqual(
			engine.block_bookings(
				[{"name": "B3", "all_day": 0, "from_time": "16:00", "to_time": "15:00"}], 8
			),
			[],
		)

	def test_the_booking_is_unavailable_and_never_carries_a_note(self):
		booking = engine.block_bookings(
			[{"name": "B1", "all_day": 0, "from_time": "14:00", "to_time": "16:00", "note": NOTE}], 8
		)[0]
		self.assertEqual(booking["kind"], "block")
		self.assertEqual(booking["label"], "Unavailable")
		self.assertEqual(booking["block"], "B1")
		self.assertNotIn(NOTE, json.dumps(booking))
		self.assertNotIn("note", booking)

	def test_conflicts_with_blocks(self):
		block = engine.block_bookings(
			[{"name": "B1", "all_day": 0, "from_time": "14:00", "to_time": "16:00"}], 8
		)[0]
		task = {"kind": "task", "ref": "T-1", "hours": 2, "slot": ["13:00", "15:00"]}
		self.assertEqual(engine.day_conflicts(8, None, [block, task]), ["Unavailable 2–4 pm (T-1)"])
		# A block alone, or two blocks overlapping, is never a conflict.
		other = dict(block, ref="B2", block="B2")
		self.assertEqual(engine.day_conflicts(8, None, [block]), [])
		self.assertEqual(engine.day_conflicts(8, None, [block, other]), [])
		# A pencil never conflicts, block or no block.
		self.assertEqual(engine.day_conflicts(8, None, [block, dict(task, tentative=True)]), [])
		# The block's hours count towards "Over by".
		self.assertEqual(
			engine.day_conflicts(8, None, [block, {"kind": "task", "ref": "T-2", "hours": 7}]), ["Over by 1h"]
		)
		# An all-day block's day: any firm booking is "Booked on a day off (Unavailable)".
		whole = engine.block_bookings([{"name": "B9", "all_day": 1}], 0)[0]
		self.assertEqual(
			engine.day_conflicts(0, "Unavailable", [whole, {"kind": "visit", "ref": "SMR-1", "hours": 2}]),
			["Booked on a day off (Unavailable)"],
		)
		self.assertEqual(engine.day_conflicts(0, "Unavailable", [whole]), [])

	def test_details_say_what_each_sentence_is_about(self):
		block = engine.block_bookings(
			[{"name": "B1", "all_day": 0, "from_time": "14:00", "to_time": "16:00"}], 8
		)[0]
		task = {"kind": "task", "ref": "T-1", "hours": 2, "slot": ["13:00", "15:00"]}
		late = {"kind": "task", "ref": "T-2", "hours": 6, "slot": ["14:30", "20:30"]}
		details = engine.day_conflict_details(8, None, [block, task, late])
		self.assertEqual([d["type"] for d in details], ["overbooked", "blocked", "overlap", "blocked"])
		self.assertEqual([d["message"] for d in details], engine.day_conflicts(8, None, [block, task, late]))
		self.assertEqual([b["ref"] for b in details[0]["bookings"]], ["T-1", "T-2"])
		self.assertEqual(details[0]["block"]["ref"], "B1")
		self.assertEqual([b["ref"] for b in details[1]["bookings"]], ["T-1"])
		whole = engine.block_bookings([{"name": "B9", "all_day": 1}], 0)[0]
		(off,) = engine.day_conflict_details(
			0, "Unavailable", [whole, {"kind": "task", "ref": "T-3", "hours": 4}]
		)
		self.assertEqual((off["type"], off["block"]["ref"]), ("blocked", "B9"))
		(holiday,) = engine.day_conflict_details(
			0, "Holiday: Labor Day", [whole, {"kind": "task", "ref": "T-3", "hours": 4}]
		)
		self.assertEqual((holiday["type"], holiday["block"]), ("day_off", None))

	def test_block_reason(self):
		block = engine.block_bookings(
			[{"name": "B1", "all_day": 0, "from_time": "14:00", "to_time": "16:00"}], 8
		)[0]
		self.assertEqual(engine.block_reason({"bookings": [block]}), "Unavailable 2–4 pm")
		self.assertIsNone(engine.block_reason({"bookings": [{"kind": "task"}]}))


# ====================================================================== the engine


class TestEngineBlocks(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_an_all_day_block_zeroes_the_day_and_a_booking_on_it_conflicts(self):
		_block("PBLK-1", "RES-1", TUE, note=NOTE)
		tasks = [_task("T-1", TUE, hours=4)]
		with _engine(tasks, {"T-1": ["RES-1"]}):
			data = engine._compute(MON, FRI, google=False)
		cell = _cell(data, "RES-1", TUE)
		self.assertEqual((cell["capacity"], cell["free"], cell["off"]), (0.0, 0.0, "Unavailable"))
		self.assertEqual(cell["conflicts"], ["Booked on a day off (Unavailable)"])
		blocks_on = [b for b in cell["bookings"] if b["kind"] == "block"]
		self.assertEqual(
			[(b["ref"], b["label"], b["all_day"]) for b in blocks_on], [("PBLK-1", "Unavailable", True)]
		)
		self.assertEqual(data["blocks"]["PBLK-1"]["resource"], "RES-1")
		# Nothing anywhere in the answer carries the note.
		self.assertNotIn(NOTE, json.dumps(data, default=str))

	def test_the_engine_never_reads_the_note(self):
		_block("PBLK-1", "RES-1", TUE, note=NOTE)
		with _engine():
			engine._compute(MON, FRI, google=False)
		reads = [fields for doctype, _f, fields in frappe.queries if doctype == "Planner Block"]
		self.assertTrue(reads)
		for fields in reads:
			self.assertNotIn("note", fields)

	def test_a_timed_block_counts_its_hours_and_clashes_with_an_overlapping_slot(self):
		_block("PBLK-2", "RES-1", WED, note=OTHER_NOTE, all_day=0, start="14:00:00", end="16:00:00")
		tasks = [
			_task("T-4", WED, slot=("13:00", "15:00")),
			_task("T-P", WED, slot=("14:00", "15:00"), tentative=1),
		]
		with _engine(tasks, {"T-4": ["RES-1"], "T-P": ["RES-1"]}):
			data = engine._compute(MON, FRI, google=False)
		cell = _cell(data, "RES-1", WED)
		self.assertEqual((cell["capacity"], cell["booked"], cell["free"]), (8.0, 4.0, 4.0))
		self.assertEqual(cell["conflicts"], ["Unavailable 2–4 pm (T-4)"])
		self.assertNotIn(OTHER_NOTE, json.dumps(data, default=str))

	def test_a_multi_day_task_spreads_round_an_all_day_block_like_time_off(self):
		_block("PBLK-1", "RES-1", TUE)
		tasks = [_task("T-9", MON, hours=12, end=WED)]
		with _engine(tasks, {"T-9": ["RES-1"]}):
			data = engine._compute(MON, FRI, google=False)
		self.assertEqual(data["task_hours"]["T-9"]["RES-1"], 12.0)
		self.assertEqual(_cell(data, "RES-1", TUE)["conflicts"], [])
		self.assertEqual(_cell(data, "RES-1", MON)["booked"], 6.0)

	def test_a_block_is_not_a_stop_and_books_no_driving(self):
		_block("PBLK-2", "RES-1", WED, all_day=0, start="14:00", end="16:00")
		self.assertNotIn("block", routing.STOP_KINDS)
		self.assertEqual(routing.plan_routes({("RES-1", WED): [{"kind": "block", "ref": "PBLK-2"}]}), {})

	def test_excluded_blocks_and_a_missing_table_read_as_none(self):
		_block("PBLK-1", "RES-1", TUE)
		with _engine():
			data = engine._compute(MON, FRI, google=False, exclude_blocks={"PBLK-1"})
		self.assertEqual(_cell(data, "RES-1", TUE)["capacity"], 8.0)
		frappe.missing_tables.add("Planner Block")
		with _engine():
			data = engine._compute(MON, FRI, google=False)
		self.assertEqual(_cell(data, "RES-1", TUE)["capacity"], 8.0)
		self.assertEqual(frappe.errors, [])

	def test_a_save_onto_a_blocked_day_asks_for_a_reason_but_not_for_someone_elses_clash(self):
		_block("PBLK-2", "RES-1", WED, all_day=0, start="14:00", end="16:00")
		tasks = [_task("T-4", WED, slot=("13:00", "15:00")), _task("T-5", MON, hours=2)]
		with _engine(tasks, {"T-4": ["RES-1"], "T-5": ["RES-1"]}):
			moved = engine.preview_conflicts(
				dict(tasks[1], exp_start_date=str(WED), exp_end_date=str(WED)), [{"resource": "RES-1"}]
			)
			onto = engine.preview_conflicts(
				dict(tasks[1], exp_start_date=str(TUE), exp_end_date=str(TUE)), [{"resource": "RES-1"}]
			)
		# T-5 has no slot: the block's clash with T-4 is not T-5's doing.
		self.assertEqual(moved, {})
		self.assertEqual(onto, {})
		_block("PBLK-1", "RES-1", TUE)
		with _engine(tasks, {"T-4": ["RES-1"], "T-5": ["RES-1"]}):
			onto = engine.preview_conflicts(
				dict(tasks[1], exp_start_date=str(TUE), exp_end_date=str(TUE)), [{"resource": "RES-1"}]
			)
		self.assertEqual(onto, {"Austin Healey": ["2026-10-13: Booked on a day off (Unavailable)"]})

	def test_who_is_free_says_unavailable(self):
		block = engine.block_bookings(
			[{"name": "B1", "all_day": 0, "from_time": "08:00", "to_time": "15:00"}], 8
		)[0]
		person = {"name": "RES-1", "label": "Austin Healey", "group": "Field", "user": AUSTIN}
		ok, entry = pp.free_entry(person, {"capacity": 8, "free": 1, "booked": 7, "bookings": [block]}, 2)
		self.assertFalse(ok)
		self.assertEqual(entry["reason"], "Unavailable 8 am–3 pm; only 1h free")
		self.assertEqual(entry["user"], AUSTIN)
		ok, entry = pp.free_entry(
			person, {"capacity": 0, "free": 0, "booked": 0, "off": "Unavailable", "bookings": []}, 1
		)
		self.assertEqual(entry["reason"], "Unavailable")

	def test_the_maintenance_planner_cell_shows_blocks_without_notes(self):
		block = engine.block_bookings(
			[{"name": "B1", "all_day": 0, "from_time": "14:00", "to_time": "16:00"}], 8
		)[0]
		cell = mp._booking_cell(
			{"capacity": 8, "booked": 2, "free": 6, "bookings": [block, {"kind": "task", "ref": "T-1"}]}
		)
		self.assertEqual([i["ref"] for i in cell["items"]], ["T-1"])
		self.assertEqual(
			[(b["ref"], b["label"], b["window"]) for b in cell["blocks"]], [("B1", "Unavailable", "2–4 pm")]
		)

	def test_crew_utilization_counts_a_timed_block_as_absence(self):
		block = {"kind": "block", "hours": 2}
		days = {
			"RES-1": {
				str(MON): {"capacity": 8, "booked": 7, "bookings": [block, {"kind": "task", "hours": 5}]}
			}
		}
		periods = [{"key": "w", "label": "Week", "start": MON, "end": MON}]
		(row,) = report.aggregate([{"name": "RES-1", "label": "Austin"}], days, {}, {}, periods)
		self.assertEqual((row["capacity"], row["booked"], row["project"]), (6.0, 5.0, 5.0))


# ====================================================================== next free day


class TestNextFreeDay(unittest.TestCase):
	def days(self):
		def cell(capacity, *bookings):
			return {"capacity": capacity, "bookings": list(bookings)}

		return {
			"RES-1": {
				str(MON): cell(8, {"kind": "task", "ref": "T-1", "hours": 8}),
				str(TUE): cell(0, {"kind": "block", "ref": "B1", "hours": 0}),  # all-day block
				str(WED): cell(8, {"kind": "block", "ref": "B2", "hours": 6}),  # 2h left
				str(THU): cell(
					8, {"kind": "task", "ref": "T-1", "hours": 4}, {"kind": "task", "ref": "T-2", "hours": 3}
				),
				str(FRI): cell(8, {"kind": "travel", "ref": "TRIP-1", "hours": 8}),
				str(SAT): cell(0),
				str(SUN): cell(0),
				str(MON + datetime.timedelta(days=7)): cell(8),
			}
		}

	def test_skips_days_off_blocks_and_full_days(self):
		self.assertEqual(engine.next_free_day(self.days(), "RES-1", 4, MON), MON + datetime.timedelta(days=7))
		self.assertEqual(engine.next_free_day(self.days(), "RES-1", 2, MON), WED)
		self.assertEqual(engine.next_free_day(self.days(), "RES-1", 1, WED), THU)

	def test_the_task_gets_its_own_hours_back(self):
		self.assertEqual(engine.next_free_day(self.days(), "RES-1", 5, WED, exclude_task="T-1"), THU)
		self.assertEqual(engine.next_free_day(self.days(), "RES-1", 5, WED), MON + datetime.timedelta(days=7))

	def test_the_horizon_and_a_whole_day(self):
		self.assertIsNone(engine.next_free_day(self.days(), "RES-1", 4, MON, horizon=6))
		# No hours: the whole day must be free.
		self.assertEqual(
			engine.next_free_day(self.days(), "RES-1", None, MON), MON + datetime.timedelta(days=7)
		)
		self.assertIsNone(engine.next_free_day(self.days(), "RES-2", 1, MON))
		# Pencils are not in anyone's way.
		days = {
			"RES-1": {
				str(TUE): {
					"capacity": 8,
					"bookings": [{"kind": "task", "ref": "T-9", "hours": 8, "tentative": True}],
				}
			}
		}
		self.assertEqual(engine.next_free_day(days, "RES-1", 8, MON), TUE)

	def test_the_endpoint_makes_one_google_free_pass_without_the_task(self):
		_reset()
		frappe.session.user = AUSTIN
		calls = []
		real = engine._compute

		def spy(*args, **kwargs):
			calls.append((args, kwargs))
			return real(*args, **kwargs)

		_block("PBLK-1", "RES-1", MON)
		with (
			_engine([_task("T-1", TUE, hours=8)], {"T-1": ["RES-1"]}),
			mock.patch.object(engine, "_compute", spy),
		):
			got = conflicts.get_next_free_day("RES-1", hours=8, after=str(TODAY), task="T-1")
			busy = conflicts.get_next_free_day("RES-1", hours=8, after=str(TODAY))
		self.assertEqual(len(calls), 2)
		self.assertIs(calls[0][1]["google"], False)
		self.assertEqual(calls[0][1]["exclude"], {"T-1"})
		self.assertEqual(got["date"], str(TUE))  # Monday is blocked; Tuesday's task is the one moving
		self.assertEqual(busy["date"], str(WED))
		frappe.session.user = "stranger@example.com"
		with self.assertRaises(_PermissionError):
			conflicts.get_next_free_day("RES-1", hours=2)


# ====================================================================== block notes (privacy)


class TestBlockNotes(unittest.TestCase):
	def setUp(self):
		_reset()
		_block("PBLK-1", "RES-1", TUE, note=NOTE)
		_block("PBLK-2", "RES-2", TUE, note=OTHER_NOTE)
		_block("PBLK-3", "RES-2", WED, note="   ")

	def test_a_scheduler_reads_every_note(self):
		self.assertEqual(
			engine.block_notes(["PBLK-1", "PBLK-2", "PBLK-3"], PM), {"PBLK-1": NOTE, "PBLK-2": OTHER_NOTE}
		)
		self.assertEqual(engine.block_notes(["PBLK-2"], "Administrator"), {"PBLK-2": OTHER_NOTE})

	def test_a_technician_reads_only_their_own(self):
		self.assertEqual(engine.block_notes(["PBLK-1", "PBLK-2"], AUSTIN), {"PBLK-1": NOTE})
		frappe.session.user = KORBEN
		self.assertEqual(engine.block_notes(["PBLK-1", "PBLK-2"]), {"PBLK-2": OTHER_NOTE})
		# Owned through the Planner Resource's user, not a stale copy on the block.
		frappe.tables["Planner Block"][0]["user"] = KORBEN
		self.assertEqual(engine.block_notes(["PBLK-1"], KORBEN), {})
		self.assertEqual(engine.block_notes(["PBLK-1"], AUSTIN), {"PBLK-1": NOTE})
		self.assertEqual(engine.block_notes(["PBLK-1"], "stranger@example.com"), {})

	def test_get_blocks_shows_unavailable_and_only_the_notes_you_may_read(self):
		frappe.session.user = AUSTIN
		answer = blocks.get_blocks(str(MON), str(FRI))
		self.assertFalse(answer["can_schedule"])
		by_name = {b["name"]: b for b in answer["blocks"]}
		self.assertEqual(by_name["PBLK-1"]["note"], NOTE)
		self.assertIsNone(by_name["PBLK-2"]["note"])
		self.assertEqual({b["label"] for b in answer["blocks"]}, {"Unavailable"})
		self.assertEqual((by_name["PBLK-1"]["own"], by_name["PBLK-1"]["can_edit"]), (True, True))
		self.assertEqual((by_name["PBLK-2"]["own"], by_name["PBLK-2"]["can_edit"]), (False, False))
		self.assertNotIn(OTHER_NOTE, json.dumps(answer))
		frappe.session.user = LISA
		answer = blocks.get_blocks(str(MON), str(FRI), user=KORBEN)
		self.assertTrue(answer["can_schedule"])
		self.assertEqual(
			{b["name"]: b["note"] for b in answer["blocks"]}, {"PBLK-2": OTHER_NOTE, "PBLK-3": None}
		)

	def test_reads_are_gated_and_bounded(self):
		frappe.session.user = "stranger@example.com"
		with self.assertRaises(_PermissionError):
			blocks.get_blocks(str(MON), str(FRI))
		with self.assertRaises(_PermissionError):
			blocks.get_day_notes(str(MON), str(FRI))
		frappe.session.user = PM
		with self.assertRaises(_Throw):
			blocks.get_blocks(str(MON), "2027-03-01")


# ====================================================================== own-or-scheduler writes


class TestBlockWrites(unittest.TestCase):
	def setUp(self):
		_reset()
		_block("PBLK-2", "RES-2", TUE, note=OTHER_NOTE)

	def save(self, **kwargs):
		with _engine():
			return blocks.save_block(**kwargs)

	def test_a_technician_blocks_their_own_time(self):
		frappe.session.user = AUSTIN
		answer = self.save(date=str(WED), from_time="14:00", to_time="16:00", note="  Dentist  ")
		block = answer["block"]
		self.assertEqual((block["resource"], block["user"], block["all_day"]), ("RES-1", AUSTIN, False))
		self.assertEqual(
			(block["slot"], block["window"], block["hours"]), (["14:00", "16:00"], "2–4 pm", 2.0)
		)
		self.assertEqual(block["note"], "Dentist")
		self.assertEqual(answer["conflicts"], [])
		self.assertIsNone(answer["message"])
		stored = frappe.docs[("Planner Block", block["name"])]
		self.assertEqual(
			(stored["from_time"], stored["to_time"], stored["all_day"]), ("14:00:00", "16:00:00", 0)
		)
		self.assertNotIn("<", block["name"])
		self.assertNotIn(">", block["name"])

	def test_a_technician_may_not_touch_anyone_elses_block(self):
		frappe.session.user = AUSTIN
		with self.assertRaises(_PermissionError):
			self.save(date=str(WED), resource="RES-2")
		with self.assertRaises(_PermissionError):
			self.save(date=str(WED), user=KORBEN)
		with self.assertRaises(_PermissionError):
			self.save(date=str(WED), name="PBLK-2", note="mine now")
		with self.assertRaises(_PermissionError):
			blocks.delete_block("PBLK-2")
		self.assertEqual(_find("Planner Block", "PBLK-2")["note"], OTHER_NOTE)
		self.assertEqual(frappe.deleted, [])

	def test_a_scheduler_blocks_anyone_and_edits_and_deletes(self):
		frappe.session.user = LISA
		answer = self.save(date=str(WED), resource="RES-2")
		self.assertTrue(answer["block"]["all_day"])
		self.assertTrue(answer["block"]["can_edit"])
		edited = self.save(date=str(THU), name="PBLK-2", all_day=0, from_time="08:00", to_time="09:30")
		self.assertEqual((edited["block"]["date"], edited["block"]["window"]), (str(THU), "8–9:30 am"))
		self.assertEqual(edited["block"]["note"], OTHER_NOTE)  # note left out: kept
		cleared = self.save(date=str(THU), name="PBLK-2", note="")
		self.assertIsNone(cleared["block"]["note"])
		self.assertTrue(cleared["block"]["all_day"])  # no times given: all day
		self.assertEqual(blocks.delete_block("PBLK-2"), {"name": "PBLK-2", "deleted": True})
		self.assertEqual(frappe.deleted, [("Planner Block", "PBLK-2", True)])

	def test_a_technician_edits_and_deletes_their_own(self):
		_block("PBLK-7", "RES-1", WED, note="old")
		frappe.session.user = AUSTIN
		answer = self.save(date=str(WED), name="PBLK-7", note="new")
		self.assertEqual(answer["block"]["note"], "new")
		self.assertTrue(blocks.delete_block("PBLK-7")["deleted"])

	def test_validation(self):
		frappe.session.user = PM
		with self.assertRaises(_Throw):
			self.save(date=str(WED), resource="RES-9")  # inactive
		with self.assertRaises(_Throw):
			self.save(date=str(WED), resource="RES-1", all_day=0, from_time="16:00", to_time="14:00")
		with self.assertRaises(_Throw):
			self.save(date=str(WED), resource="RES-1", all_day=0, from_time="16:00")
		frappe.session.user = "tech-only@example.com"
		with self.assertRaises(_Throw):
			self.save(date=str(WED))  # not on the planner at all
		frappe.session.user = "stranger@example.com"
		with self.assertRaises(_PermissionError):
			self.save(date=str(WED))

	def test_a_past_day_saves_with_a_warning(self):
		frappe.session.user = AUSTIN
		answer = self.save(date=str(TODAY - datetime.timedelta(days=3)))
		self.assertEqual(answer["warnings"], ["This block is on a day that has already passed."])
		self.assertEqual(frappe.local.message_log, [])

	def test_a_block_over_booked_work_is_saved_and_says_what_it_overlaps(self):
		frappe.session.user = AUSTIN
		tasks = [_task("T-4", WED, slot=("13:00", "15:00"), subject="Dig at Riverwalk")]
		with _engine(tasks, {"T-4": ["RES-1"]}):
			answer = blocks.save_block(date=str(WED), from_time="14:00", to_time="16:00", note=NOTE)
		self.assertEqual(
			answer["conflicts"],
			[
				{
					"date": str(WED),
					"label": "Unavailable 2–4 pm (T-4)",
					"items": [
						{"doctype": "Task", "name": "T-4", "title": "Dig at Riverwalk", "planner": "project"}
					],
				}
			],
		)
		self.assertEqual(
			answer["message"], "This overlaps Dig at Riverwalk; your PM will see it in the Conflict center."
		)
		self.assertTrue(frappe.docs[("Planner Block", answer["block"]["name"])])
		self.assertEqual(frappe.comments, [])  # deliberately no timeline comment
		self.assertNotIn(NOTE, json.dumps(answer["conflicts"]))


class TestDayNotes(unittest.TestCase):
	def setUp(self):
		_reset()
		_day_note("PDN-1", MON, "Shop meeting 7 am")
		_day_note("PDN-2", MON, "PM budget review", audience="PM")
		_day_note(
			"PDN-3", TUE, "City inspection", audience="Field", project="PRJ-1", project_title="Riverwalk"
		)

	def test_audience(self):
		entries = [blocks.note_entry(r) for r in frappe.tables["Planner Day Note"]]
		self.assertEqual([e["name"] for e in blocks.notes_for_audience(entries, "Field")], ["PDN-1", "PDN-3"])
		self.assertEqual([e["name"] for e in blocks.notes_for_audience(entries, "PM")], ["PDN-1", "PDN-2"])
		self.assertEqual([e["name"] for e in blocks.notes_for_audience(entries, None)], ["PDN-1"])

	def test_everyone_reads_and_mine_filters_by_group(self):
		frappe.session.user = AUSTIN
		answer = blocks.get_day_notes(str(MON), str(FRI))
		self.assertEqual(
			{d: [n["name"] for n in notes] for d, notes in answer["notes"].items()},
			{str(MON): ["PDN-1", "PDN-2"], str(TUE): ["PDN-3"]},
		)
		self.assertFalse(answer["notes"][str(MON)][0]["can_edit"])
		mine = blocks.get_day_notes(str(MON), str(FRI), mine=1)
		self.assertEqual([n["name"] for notes in mine["notes"].values() for n in notes], ["PDN-1", "PDN-3"])

	def test_only_schedulers_write(self):
		frappe.session.user = AUSTIN
		with self.assertRaises(_PermissionError):
			blocks.save_day_note(str(WED), "Mine")
		with self.assertRaises(_PermissionError):
			blocks.delete_day_note("PDN-1")
		frappe.session.user = "Administrator"
		frappe.user_roles["Administrator"] = []
		saved = blocks.save_day_note(str(WED), "  Truck to the shop  ", audience="Field", project="PRJ-1")
		self.assertEqual(
			(saved["note"], saved["audience"], saved["project"]), ("Truck to the shop", "Field", "PRJ-1")
		)
		with self.assertRaises(_Throw):
			blocks.save_day_note(str(WED), "x", audience="Everyone")
		with self.assertRaises(_Throw):
			blocks.save_day_note(str(WED), "   ")
		edited = blocks.save_day_note(str(WED), "Truck to the yard", name=saved["name"])
		self.assertEqual(
			(edited["name"], edited["note"], edited["audience"]), (saved["name"], "Truck to the yard", None)
		)
		self.assertEqual(blocks.delete_day_note(saved["name"])["deleted"], True)


# ====================================================================== payloads


class TestPayloads(unittest.TestCase):
	def setUp(self):
		_reset()
		_day_note("PDN-1", MON, "Shop meeting 7 am")
		_block("PBLK-1", "RES-1", TUE, note=NOTE)
		_block("PBLK-2", "RES-2", TUE, note=OTHER_NOTE)

	def engine_days(self):
		with _engine():
			return engine._compute(MON, FRI, google=False)

	def test_project_planner_get_planner_carries_notes_and_can_schedule(self):
		data = self.engine_days()
		data.update(equipment={}, equipment_conflicts={})
		with contextlib.ExitStack() as stack:
			for target, name, value in (
				(engine, "_compute", lambda *a, **k: data),
				(engine, "read_crews", lambda names: ({}, {})),
				(engine, "read_equipment", lambda names: {}),
				(pp, "_unscheduled", lambda: []),
				(pp, "_credentials", lambda names: {}),
				(pp, "_dependencies", lambda names: {}),
				(pp, "_task_rows", lambda names: {}),
				(pp, "_held_credentials", lambda people, types_: {}),
				(pp, "_projects", lambda names: []),
				(pp, "_phase5_cards", lambda cards, today: None),
				(pp.planner_tracking, "task_actuals", lambda names: ({}, {})),
			):
				stack.enter_context(mock.patch.object(target, name, value))
			frappe.session.user = AUSTIN
			tech = pp.get_planner(str(MON), str(FRI))
			frappe.session.user = PM
			boss = pp.get_planner(str(MON), str(FRI))
		self.assertEqual(tech["day_notes"][str(MON)][0]["note"], "Shop meeting 7 am")
		self.assertEqual(tech["block_notes"], {"PBLK-1": NOTE})
		self.assertFalse(tech["can_schedule"])
		self.assertNotIn(OTHER_NOTE, json.dumps(tech, default=str))
		self.assertEqual(boss["block_notes"], {"PBLK-1": NOTE, "PBLK-2": OTHER_NOTE})
		self.assertTrue(boss["can_schedule"])
		self.assertEqual(frappe.errors, [])

	def test_maintenance_planner_get_planner_carries_them_too(self):
		data = self.engine_days()
		techs = [
			{"user": AUSTIN, "name": "Austin Healey", "enabled": True},
			{"user": KORBEN, "name": "Korben Fox", "enabled": True},
		]
		with contextlib.ExitStack() as stack:
			for name, value in (
				("_records_between", lambda start, end: []),
				("_unscheduled_drafts", lambda: []),
				("_projections", lambda start, end, today: []),
				("_decorate", lambda cards: None),
				("_technicians", lambda cards: techs),
			):
				stack.enter_context(mock.patch.object(mp, name, value))
			stack.enter_context(
				mock.patch.object(
					engine,
					"availability",
					lambda *a, **k: {key: data[key] for key in ("resources", "days", "user_to_resource")},
				)
			)
			frappe.session.user = KORBEN
			answer = mp.get_planner(str(MON), str(FRI))
		self.assertEqual(answer["block_notes"], {"PBLK-2": OTHER_NOTE})
		self.assertEqual(answer["day_notes"][str(MON)][0]["name"], "PDN-1")
		self.assertFalse(answer["can_schedule"])
		self.assertEqual([b["ref"] for b in answer["bookings"][AUSTIN][str(TUE)]["blocks"]], ["PBLK-1"])
		self.assertEqual(answer["bookings"][AUSTIN][str(TUE)]["off"], "Unavailable")
		self.assertNotIn(NOTE, json.dumps(answer, default=str))
		self.assertEqual(frappe.errors, [])

	def test_my_week_has_my_blocks_with_my_notes_and_my_groups_day_notes(self):
		_day_note("PDN-2", MON, "PM budget review", audience="PM")
		frappe.session.user = AUSTIN
		extras = blocks.my_week_extras("RES-1", MON, SUN)
		self.assertEqual([(b["name"], b["note"]) for b in extras["blocks"]], [("PBLK-1", NOTE)])
		self.assertEqual([n["name"] for n in extras["day_notes"][str(MON)]], ["PDN-1"])

	def test_the_hooks_are_one_line_each(self):
		source = PP_PATH.read_text(encoding="utf-8")
		self.assertIn('result.update(_phase6d().payload_extras(start, end, data["days"]))', source)
		self.assertIn("answer.update(_phase6d().my_week_extras(resource, first, last))", source)
		self.assertIn('**_planner_extras(start, end, view["bookings"])', MP_PATH.read_text(encoding="utf-8"))

	def test_a_failure_never_blanks_a_planner(self):
		with mock.patch.object(engine, "block_notes", side_effect=RuntimeError("boom")):
			got = blocks.payload_extras(MON, FRI, {})
		self.assertEqual(got["block_notes"], {})
		self.assertEqual(len(frappe.errors), 1)
		self.assertEqual(frappe.errors[0][0], ())  # keyword arguments only


# ====================================================================== the Conflict center


class ConflictSite:
	"""A week with one of everything wrong in it."""

	TASKS = [
		_task("T-1", MON, hours=8, subject="Pump set"),
		_task("T-2", MON, hours=4, subject="Lights"),
		_task("T-3", TUE, hours=4, subject="Dig at Riverwalk"),
		_task("T-4", WED, slot=("13:00", "15:00"), subject="Inspection"),
		_task("T-6", TUE, hours=2, tentative=1, subject="Maybe"),
		_task("T-7", MON, hours=6, subject="Tile"),
		_task("T-R", MON, rental="RB-1", project=None, subject="Setup: Smith wedding"),
		_task("T-8", MON, hours=1, subject="Haul A"),
		_task("T-9", MON, hours=1, subject="Haul B"),
		_task("T-10", THU, hours=4, subject="Forklift job"),
		_task("T-11", FRI, slot=("09:00", "11:00"), subject="Meet A"),
		_task("T-12", FRI, slot=("10:00", "12:00"), subject="Meet B"),
	]
	CREWS = {
		"T-1": ["RES-1"],
		"T-2": ["RES-1"],
		"T-3": ["RES-1"],
		"T-4": ["RES-1"],
		"T-6": ["RES-1"],
		"T-7": ["RES-2"],
		"T-R": ["RES-2"],
		"T-10": ["RES-3"],
		"T-11": ["RES-3"],
		"T-12": ["RES-3"],
	}
	VISITS = [
		{
			"user": KORBEN,
			"date": MON,
			"ref": "SMR-1",
			"key": "SMR-1",
			"label": "Highlands",
			"project": "PRJ-2",
			"hours": 2.0,
			"slot": None,
			"estimated": True,
			"full_day": False,
		}
	]
	EQUIPMENT = {
		"T-8": [{"type": "Vehicle", "name": "Truck 2", "label": "Truck 2"}],
		"T-9": [{"type": "Vehicle", "name": "Truck 2", "label": "Truck 2"}],
	}

	@classmethod
	def install(cls):
		_block("PBLK-1", "RES-1", TUE, note=NOTE)
		_block("PBLK-2", "RES-1", WED, note=OTHER_NOTE, all_day=0, start="14:00:00", end="16:00:00")
		frappe.tables["Sapphire Maintenance Record"] = [
			{
				"name": "SMR-1",
				"modified": "2026-10-02 08:00:00",
				"docstatus": 0,
				"workflow_state": "Draft",
				"visit_date": None,
			}
		]
		for task in cls.TASKS:
			frappe.docs[("Task", task["name"])] = _Doc(dict(task, doctype="Task"))

	@classmethod
	@contextlib.contextmanager
	def running(cls, tasks=None):
		with (
			_engine(tasks or cls.TASKS, cls.CREWS, visits=cls.VISITS, equipment=cls.EQUIPMENT),
			mock.patch.object(
				pp, "_credentials", lambda names: {"T-10": ["Forklift"]} if "T-10" in names else {}
			),
			mock.patch.object(pp, "_held_credentials", lambda people, types_: {}),
		):
			yield


class TestConflictCenter(unittest.TestCase):
	def setUp(self):
		_reset()
		ConflictSite.install()

	def load(self, planner="project", user=PM, tasks=None):
		frappe.session.user = user
		with ConflictSite.running(tasks):
			return conflicts.get_conflicts(str(MON), str(FRI), planner=planner)

	def find(self, rows, kind, date, resource=None):
		found = [
			r
			for r in rows
			if r["kind"] == kind
			and r["date"] == str(date)
			and (resource is None or r["resource"] == resource)
		]
		self.assertEqual(
			len(found),
			1,
			f"{kind} {date} {resource}: {[(r['kind'], r['date'], r['resource']) for r in rows]}",
		)
		return found[0]

	def test_one_engine_pass_with_google_off(self):
		calls = []
		real = engine._compute

		def spy(*args, **kwargs):
			calls.append((args, kwargs))
			return real(*args, **kwargs)

		frappe.session.user = PM
		with ConflictSite.running(), mock.patch.object(engine, "_compute", spy):
			conflicts.get_conflicts(str(MON), str(FRI))
		self.assertEqual(len(calls), 1)
		self.assertIs(calls[0][1]["google"], False)
		self.assertIs(calls[0][1]["equipment"], True)
		self.assertEqual((calls[0][0][0], calls[0][0][1]), (MON, FRI + datetime.timedelta(days=30)))

	def test_everything_wrong_sorted_by_date_with_stable_keys(self):
		rows = self.load()
		self.assertEqual(
			sorted({r["kind"] for r in rows}),
			["blocked", "equipment", "overbooked", "overlap", "qualification"],
		)
		self.assertEqual([r["date"] for r in rows], sorted(r["date"] for r in rows))
		again = self.load()
		self.assertEqual([r["key"] for r in rows], [r["key"] for r in again])
		for row in rows:
			kind, date, _anchor, digest_ = row["key"].split("|")
			self.assertEqual((kind, date), (row["kind"], row["date"]))
			self.assertEqual(len(digest_), 16)
			self.assertLessEqual(len(row["key"]), 140)  # a Data field
		over = self.find(rows, "overbooked", MON, "RES-1")
		self.assertEqual(over["message"], "Austin Healey: Over by 4h")
		self.assertEqual(sorted(i["name"] for i in over["items"]), ["T-1", "T-2"])
		day_off = self.find(rows, "blocked", TUE, "RES-1")
		self.assertEqual(day_off["sentence"], "Booked on a day off (Unavailable)")
		self.assertEqual([i["name"] for i in day_off["items"]], ["T-3"])  # the pencil T-6 never conflicts
		timed = self.find(rows, "blocked", WED, "RES-1")
		self.assertEqual(timed["sentence"], "Unavailable 2–4 pm (T-4)")
		self.assertEqual(timed["block"]["window"], "2–4 pm")
		overlap = self.find(rows, "overlap", FRI, "RES-3")
		self.assertEqual(sorted(i["name"] for i in overlap["items"]), ["T-11", "T-12"])
		truck = self.find(rows, "equipment", MON)
		self.assertEqual(truck["equipment"], {"type": "Vehicle", "name": "Truck 2", "label": "Truck 2"})
		self.assertEqual(truck["message"], "Truck 2 is on T-8 (Haul A) and T-9 (Haul B)")
		self.assertEqual(truck["key"].split("|")[2], "Vehicle:Truck 2")
		skill = self.find(rows, "qualification", THU)
		self.assertEqual(
			(skill["credentials"], skill["message"]),
			(["Forklift"], "Forklift job: No one on the crew holds: Forklift"),
		)
		self.assertEqual(frappe.errors, [])  # nothing failed quietly on the way

	def test_a_different_set_of_records_is_a_different_key(self):
		rows = self.load()
		key = self.find(rows, "overbooked", MON, "RES-1")["key"]
		tasks = [*ConflictSite.TASKS, _task("T-13", MON, hours=1)]
		crews = dict(ConflictSite.CREWS, **{"T-13": ["RES-1"]})
		frappe.session.user = PM
		with (
			_engine(tasks, crews, visits=ConflictSite.VISITS, equipment=ConflictSite.EQUIPMENT),
			mock.patch.object(pp, "_credentials", lambda names: {}),
		):
			later = conflicts.get_conflicts(str(MON), str(FRI))
		self.assertNotEqual(self.find(later, "overbooked", MON, "RES-1")["key"], key)

	def test_project_planner_owns_tasks_and_gets_their_fixes(self):
		rows = self.load("project")
		over = self.find(rows, "overbooked", MON, "RES-1")
		self.assertTrue(all(i["own"] and i["planner"] == "project" for i in over["items"]))
		fixes = [(f["type"], f.get("name"), f.get("date")) for f in over["fixes"]]
		self.assertIn(("next_free_day", "T-1", str(THU)), fixes)  # Tue blocked, Wed only 4h left
		self.assertIn(("next_free_day", "T-2", str(WED)), fixes)
		self.assertIn(("pencil", "T-1", None), fixes)
		self.assertEqual(over["fixes"][-1]["type"], "keep")
		move = next(f for f in over["fixes"] if f["type"] == "next_free_day" and f["name"] == "T-1")
		self.assertEqual(move["method"], "erpnext_enhancements.api.project_planner.save_task")
		self.assertEqual(move["args"], {"task": "T-1", "modified": "2026-10-01 09:00:00", "start": str(THU)})
		pick = next(f for f in over["fixes"] if f["type"] == "pick_free" and f["name"] == "T-1")
		self.assertEqual(pick["method"], "erpnext_enhancements.api.project_planner.swap_crew")
		self.assertEqual(
			pick["args"], {"task": "T-1", "modified": "2026-10-01 09:00:00", "from_resource": "RES-1"}
		)
		self.assertEqual(pick["who_is_free"]["args"], {"start": str(MON), "hours": 8.0})
		self.assertEqual(pick["pick"], {"arg": "to_resource", "field": "resource"})
		pencil = next(f for f in over["fixes"] if f["type"] == "pencil" and f["name"] == "T-2")
		self.assertEqual(pencil["args"], {"task": "T-2", "modified": "2026-10-01 09:00:00", "tentative": 1})
		keep = over["fixes"][-1]
		self.assertEqual(keep["method"], "erpnext_enhancements.api.planner_conflicts.acknowledge_conflict")
		self.assertEqual(keep["args"]["key"], over["key"])
		self.assertEqual(keep["args"]["fingerprint"], over["fingerprint"])
		skill = self.find(rows, "qualification", THU)
		(add,) = [f for f in skill["fixes"] if f["type"] == "pick_free"]
		self.assertEqual(
			(add["method"].rsplit(".", 1)[1], add["credentials"], add["resource"]),
			("add_crew", ["Forklift"], None),
		)
		self.assertFalse([f for f in skill["fixes"] if f["type"] == "pencil"])

	def test_rental_and_visit_items_are_not_the_project_planners(self):
		rows = self.load("project")
		mixed = self.find(rows, "overbooked", MON, "RES-2")
		owners = {i["name"]: (i["planner"], i["own"]) for i in mixed["items"]}
		self.assertEqual(
			owners, {"T-7": ("project", True), "T-R": ("rental", False), "SMR-1": ("maintenance", False)}
		)
		named = {f.get("name") for f in mixed["fixes"]}
		self.assertEqual(named, {"T-7", None})

	def test_the_maintenance_planner_owns_visits_with_no_pencil(self):
		rows = self.load("maintenance", user=KORBEN)
		mixed = self.find(rows, "overbooked", MON, "RES-2")
		owners = {i["name"]: i["own"] for i in mixed["items"]}
		self.assertEqual(owners, {"T-7": False, "T-R": False, "SMR-1": True})
		types_ = sorted({(f["type"], f.get("name")) for f in mixed["fixes"]})
		self.assertEqual(types_, [("keep", None), ("next_free_day", "SMR-1"), ("pick_free", "SMR-1")])
		move = next(f for f in mixed["fixes"] if f["type"] == "next_free_day")
		self.assertEqual(move["method"], "erpnext_enhancements.api.maintenance_planner.move_visit")
		self.assertEqual(
			move["args"], {"record": "SMR-1", "modified": "2026-10-02 08:00:00", "date": str(TUE)}
		)
		pick = next(f for f in mixed["fixes"] if f["type"] == "pick_free")
		self.assertEqual(pick["args"]["from_user"], KORBEN)
		self.assertEqual(pick["pick"], {"arg": "technician", "field": "user"})
		over = self.find(rows, "overbooked", MON, "RES-1")
		self.assertFalse(over["own"])
		self.assertEqual([f["type"] for f in over["fixes"]], ["keep"])

	def test_a_started_or_finished_visit_offers_no_move(self):
		frappe.tables["Sapphire Maintenance Record"][0].update(visit_date=str(MON))
		rows = self.load("maintenance", user=KORBEN)
		mixed = self.find(rows, "overbooked", MON, "RES-2")
		self.assertEqual(sorted(f["type"] for f in mixed["fixes"]), ["keep", "pick_free"])
		frappe.tables["Sapphire Maintenance Record"][0].update(docstatus=1)
		rows = self.load("maintenance", user=KORBEN)
		self.assertEqual([f["type"] for f in self.find(rows, "overbooked", MON, "RES-2")["fixes"]], ["keep"])

	def test_block_notes_only_for_who_may_read_them_and_never_in_the_words(self):
		boss = self.load(user=PM)
		self.assertEqual(self.find(boss, "blocked", TUE, "RES-1")["block"]["note"], NOTE)
		tech = self.load("maintenance", user=KORBEN)
		self.assertIsNone(self.find(tech, "blocked", TUE, "RES-1")["block"]["note"])
		self.assertNotIn(NOTE, json.dumps(tech, default=str))
		self.assertNotIn(OTHER_NOTE, json.dumps(tech, default=str))
		own = self.load("maintenance", user=AUSTIN)
		self.assertEqual(self.find(own, "blocked", WED, "RES-1")["block"]["note"], OTHER_NOTE)
		for row in boss:
			self.assertNotIn(NOTE, row["message"])
			self.assertNotIn(NOTE, row["sentence"])

	def test_gates_and_bounds(self):
		frappe.session.user = PM
		with self.assertRaises(_Throw):
			conflicts.get_conflicts(str(MON), str(MON + datetime.timedelta(days=60)))
		with self.assertRaises(_Throw):
			conflicts.get_conflicts(str(MON), str(FRI), planner="rental")
		frappe.session.user = LISA  # Projects User: the Project Planner only
		with self.assertRaises(_PermissionError):
			conflicts.get_conflicts(str(MON), str(FRI), planner="maintenance")
		frappe.session.user = "stranger@example.com"
		with self.assertRaises(_PermissionError):
			conflicts.get_conflicts(str(MON), str(FRI))

	def keep(self, row, reason="Crew agreed", user=PM, planner="project", **kwargs):
		frappe.session.user = user
		with ConflictSite.running():
			return conflicts.acknowledge_conflict(
				row["key"],
				reason,
				items=json.dumps([{"doctype": i["doctype"], "name": i["name"]} for i in row["items"]]),
				planner=planner,
				**kwargs,
			)

	def test_keeping_needs_a_reason_and_comments_on_owned_items_escaped(self):
		over = self.find(self.load(), "overbooked", MON, "RES-1")
		with self.assertRaises(_Throw):
			self.keep(over, reason="   ")
		self.assertEqual(frappe.comments, [])
		answer = self.keep(over, reason="<b>crew</b> agreed")
		self.assertEqual(answer["acknowledged"]["reason"], "<b>crew</b> agreed")
		self.assertEqual(answer["acknowledged"]["by_name"], "Pat Manager")
		self.assertEqual(sorted(c["name"] for c in answer["commented"]), ["T-1", "T-2"])
		self.assertEqual(len(frappe.comments), 2)
		(doctype, _name, kind, text) = frappe.comments[0]
		self.assertEqual((doctype, kind), ("Task", "Comment"))
		self.assertEqual(
			text,
			"Kept a conflict on Mon Oct 12: Austin Healey: Over by 4h. Reason: &lt;b&gt;crew&lt;/b&gt; agreed",
		)
		self.assertIn(("check_permission", "write", "Task", "T-1"), frappe.calls)
		(ack,) = [d for d in frappe.inserted if d.get("doctype") == "Planner Conflict Ack"]
		self.assertEqual(
			(ack["conflict_key"], ack["fingerprint"], ack["planner"]),
			(over["key"], over["fingerprint"], "project"),
		)

	def test_a_kept_conflict_hides_until_its_fingerprint_changes(self):
		over = self.find(self.load(), "overbooked", MON, "RES-1")
		self.assertIsNone(over["acknowledged"])
		self.keep(over)
		kept = self.find(self.load(), "overbooked", MON, "RES-1")
		self.assertEqual(kept["acknowledged"]["reason"], "Crew agreed")
		self.assertEqual(kept["key"], over["key"])
		changed = [
			dict(t, modified="2026-10-09 05:00:00") if t["name"] == "T-2" else t for t in ConflictSite.TASKS
		]
		back = self.find(self.load(tasks=changed), "overbooked", MON, "RES-1")
		self.assertEqual(back["key"], over["key"])
		self.assertIsNone(back["acknowledged"])

	def test_a_stale_keep_writes_nothing(self):
		over = self.find(self.load(), "overbooked", MON, "RES-1")
		with self.assertRaises(_Throw):
			self.keep(dict(over, items=over["items"][:1]))
		with self.assertRaises(_Throw):
			self.keep(over, fingerprint="0" * 40)
		with self.assertRaises(_Throw):
			self.keep(dict(over, key="overbooked|2026-10-12|RES-1|0000000000000000"))
		with self.assertRaises(_Throw):
			self.keep(dict(over, key="not-a-key"))
		self.assertEqual(frappe.comments, [])
		self.assertFalse([d for d in frappe.inserted if d.get("doctype") == "Planner Conflict Ack"])

	def test_each_planner_keeps_only_through_its_own_records(self):
		over = self.find(self.load("maintenance", user=KORBEN), "overbooked", MON, "RES-1")
		with self.assertRaises(_PermissionError):
			self.keep(over, user=KORBEN, planner="maintenance")  # nothing of the maintenance planner's in it
		frappe.user_roles[KORBEN] = ["Maintenance User", "Maintenance Supervisor"]
		answer = self.keep(over, user=KORBEN, planner="maintenance")
		self.assertEqual(answer["commented"], [])
		self.assertEqual(frappe.comments, [])
		mixed = self.find(self.load("maintenance", user=KORBEN), "overbooked", MON, "RES-2")
		answer = self.keep(mixed, user=KORBEN, planner="maintenance")
		self.assertEqual(answer["commented"], [{"doctype": "Sapphire Maintenance Record", "name": "SMR-1"}])
		self.assertEqual([c[:2] for c in frappe.comments], [("Sapphire Maintenance Record", "SMR-1")])

	def test_a_keep_needs_write_on_the_records(self):
		over = self.find(self.load(), "overbooked", MON, "RES-1")
		frappe.denied.add("T-2")
		with self.assertRaises(_PermissionError):
			self.keep(over)
		self.assertEqual(frappe.comments, [])


# ====================================================================== digests


class TestDigests(unittest.TestCase):
	def setUp(self):
		_reset()
		_day_note("PDN-1", MON, "Shop meeting 7 am")
		_day_note("PDN-2", MON, "PM budget review", audience="PM")
		_day_note("PDN-3", MON, "Inspection", audience="Field", project="PRJ-1", project_title="Riverwalk")
		_block("PBLK-1", "RES-1", MON, note=NOTE, all_day=0, start="14:00", end="16:00")
		_block("PBLK-2", "RES-2", MON, note=OTHER_NOTE)

	def test_digest_lines_put_notes_first_and_notes_and_blocks_never_send_alone(self):
		note = {"text": "Note: Shop meeting 7 am"}
		block = {"text": "Unavailable 2–4 pm: Dentist"}
		work = {"items": [{"label": "Pump set", "time": "09:00", "hours": 3}]}
		self.assertEqual(
			[line["what"] for line in digest.digest_lines(dict(work, day_notes=[note], blocks=[block]))],
			["Note: Shop meeting 7 am", "Unavailable 2–4 pm: Dentist", "Pump set"],
		)
		# Phase 6E (Nik, 2026-10-09): a note rides along with a message the person gets anyway; it
		# never causes one (tests/test_planner_phase6e.py pins the sends).
		self.assertEqual(digest.digest_lines({"day_notes": [note]}), [])
		self.assertEqual(digest.digest_lines({"day_notes": [note], "blocks": [block]}), [])
		self.assertEqual(digest.digest_lines({"blocks": [block]}), [])
		self.assertEqual([line["what"] for line in digest.digest_lines(work)], ["Pump set"])

	def test_digest_extras_by_audience_with_each_persons_own_notes(self):
		frappe.session.user = "Administrator"  # the scheduler
		extras = blocks.digest_extras(MON, ["RES-1", "RES-2", "RES-3"])
		self.assertEqual([n["name"] for n in extras["RES-1"]["day_notes"]], ["PDN-1", "PDN-3"])
		self.assertEqual([n["name"] for n in extras["RES-3"]["day_notes"]], ["PDN-1", "PDN-2"])
		self.assertEqual([b["text"] for b in extras["RES-1"]["blocks"]], ["Unavailable 2–4 pm: " + NOTE])
		self.assertEqual(
			[b["text"] for b in extras["RES-2"]["blocks"]], ["Unavailable all day: " + OTHER_NOTE]
		)
		self.assertEqual(extras["RES-3"]["blocks"], [])
		self.assertEqual(extras["RES-1"]["day_notes"][1]["text"], "Note (Riverwalk): Inspection")
		self.assertNotIn(OTHER_NOTE, json.dumps(extras["RES-1"]))

	def test_the_combined_digest_carries_the_note_to_people_with_a_booking_and_is_still_once_a_day(self):
		frappe.session.user = "Administrator"
		frappe.tables["Planner Day Note"] = [
			r for r in frappe.tables["Planner Day Note"] if r["name"] == "PDN-1"
		]
		frappe.tables["Planner Block"] = []
		person = {"name": "RES-3", "resource_name": "Lisa Park", "user": LISA}
		sends = []

		def people_days(start, end, resources=None):
			data = {"resources": [{"name": "RES-3", "label": "Lisa Park"}]}
			return data, {
				"RES-3": [{"date": str(MON), "items": [{"label": "Pump set", "time": "09:00", "hours": 3}], "off": None}]
			}

		with (
			mock.patch.object(digest.notices, "setting", lambda field: 1),
			mock.patch.object(digest, "_people", lambda: [person]),
			mock.patch.object(pp, "_people_days", people_days),
			mock.patch.object(
				digest,
				"_send",
				lambda user, resource, label, day, lines: sends.append(
					(user, [line["what"] for line in lines])
				)
				or ["email"],
			),
			mock.patch.object(digest, "nowdate", lambda: str(MON)),
		):
			digest.send_daily_digests()
			digest.send_daily_digests()
		self.assertEqual(sends, [(LISA, ["Note: Shop meeting 7 am", "Pump set"])])
		self.assertTrue(frappe.docs.get(("Planner Digest Log", f"{LISA}|{MON}")))

	def test_the_maintenance_digest_carries_notes_and_own_blocks(self):
		frappe.tables["Project"] = [{"name": "PRJ-2", "project_name": "Highlands"}]
		frappe.tables["Employee"] = [{"name": "EMP-1", "user_id": AUSTIN, "cell_number": "555-0100"}]
		visit = _Doc(
			{"project": "PRJ-2", "customer": "HOA", "serial_no": None, "visit_label": None, "with_text": ""}
		)
		dispatch._send_tech_digest(AUSTIN, [visit], MON)
		(number, text) = telephony.sent[0]
		self.assertIn("Note: Shop meeting 7 am", text)
		self.assertIn("Note (Riverwalk): Inspection", text)
		self.assertIn("Unavailable 2–4 pm: " + NOTE, text)
		self.assertNotIn("PM budget review", text)
		self.assertIn("<li>Note: Shop meeting 7 am</li>", frappe.sent[0]["message"])

	def test_the_rental_digest_carries_them_for_someone_off_the_planner(self):
		frappe.tables["Employee"] = [
			{"name": "EMP-8", "user_id": "crew@example.com", "cell_number": "555-0199"}
		]
		task = _Doc(
			{"subject": "Setup: Smith", "custom_rental_task_kind": "Setup", "custom_rental_booking": "RB-1"}
		)
		rental._send_digest("crew@example.com", [task], MON)
		(number, text) = telephony.sent[0]
		self.assertIn("Note: Shop meeting 7 am", text)
		self.assertNotIn("Inspection", text)  # a Field note, and they have no group
		self.assertNotIn(NOTE, text)

	def test_a_broken_note_reader_never_stops_a_digest(self):
		with mock.patch.object(blocks, "digest_extras", side_effect=RuntimeError("boom")):
			self.assertEqual(blocks.digest_note_lines(AUSTIN, MON), [])
		with mock.patch.object(blocks, "digest_note_lines", side_effect=RuntimeError("boom")):
			self.assertEqual(dispatch._planner_notes(AUSTIN, MON), [])
			self.assertEqual(rental._planner_notes(AUSTIN, MON), [])


# ====================================================================== doctypes, AI tool, wiring


def _doctype_json(folder):
	return json.loads((DOCTYPES / folder / f"{folder}.json").read_text(encoding="utf-8"))


class TestDoctypes(unittest.TestCase):
	NEW = {
		"Planner Block": "planner_block",
		"Planner Day Note": "planner_day_note",
		"Planner Conflict Ack": "planner_conflict_ack",
	}

	def test_shape(self):
		for name, folder in self.NEW.items():
			with self.subTest(doctype=name):
				data = _doctype_json(folder)
				self.assertEqual(
					(data["name"], data["module"], data["custom"]), (name, "Project Enhancements", 0)
				)
				self.assertEqual(data["field_order"], [f["fieldname"] for f in data["fields"]])
				self.assertNotRegex(data["autoname"], r"[<>]")
				self.assertTrue(data["autoname"].endswith(".#####"))
				self.assertTrue((DOCTYPES / folder / "__init__.py").exists())
				tree = ast.parse((DOCTYPES / folder / f"{folder}.py").read_text(encoding="utf-8"))
				self.assertIn(
					name.replace(" ", ""), {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
				)

	def test_block_fields_and_permissions(self):
		data = _doctype_json("planner_block")
		fields = {f["fieldname"]: f for f in data["fields"]}
		self.assertEqual((fields["resource"]["options"], fields["resource"]["reqd"]), ("Planner Resource", 1))
		self.assertEqual((fields["date"]["fieldtype"], fields["date"]["reqd"]), ("Date", 1))
		self.assertEqual((fields["all_day"]["fieldtype"], fields["all_day"]["default"]), ("Check", "1"))
		self.assertEqual((fields["from_time"]["fieldtype"], fields["to_time"]["fieldtype"]), ("Time", "Time"))
		self.assertEqual((fields["user"]["read_only"], fields["user"]["fetch_from"]), (1, "resource.user"))
		self.assertEqual(fields["note"]["fieldtype"], "Small Text")
		roles = {p["role"]: p for p in data["permissions"]}
		self.assertEqual(
			set(roles), {"System Manager", "Projects Manager", "Projects User", "Maintenance Supervisor"}
		)
		self.assertTrue(all(roles["System Manager"].get(k) for k in ("create", "write", "delete", "read")))
		for role in ("Projects Manager", "Projects User", "Maintenance Supervisor"):
			self.assertFalse(
				roles[role].get("write") or roles[role].get("create") or roles[role].get("delete"), role
			)
		self.assertEqual(set(roles) - {"System Manager"}, set(engine.SCHEDULER_ROLES) - {"System Manager"})

	def test_day_note_and_ack(self):
		note = _doctype_json("planner_day_note")
		fields = {f["fieldname"]: f for f in note["fields"]}
		self.assertEqual(fields["audience"]["options"], "\nField\nPM\nDesign\nSubcontractor")
		self.assertEqual((fields["note"]["reqd"], fields["project"]["options"]), (1, "Project"))
		roles = {p["role"]: p for p in note["permissions"]}
		self.assertEqual(set(roles), set(engine.SCHEDULER_ROLES))
		for role in engine.SCHEDULER_ROLES:
			self.assertTrue(all(roles[role].get(k) for k in ("create", "write", "delete", "read")), role)
		ack = _doctype_json("planner_conflict_ack")
		self.assertEqual(ack["in_create"], 1)
		self.assertEqual(
			[
				f["fieldname"]
				for f in ack["fields"]
				if f["fieldtype"] not in ("Column Break", "Section Break")
			],
			[
				"conflict_key",
				"kind",
				"conflict_date",
				"planner",
				"acknowledged_by",
				"acknowledged_on",
				"fingerprint",
				"message",
				"reason",
			],
		)
		self.assertTrue(
			all(
				f.get("read_only")
				for f in ack["fields"]
				if f["fieldtype"] not in ("Column Break", "Section Break")
			)
		)

	def test_the_day_note_controller(self):
		_reset()
		doc = _new_doc("Planner Day Note")
		doc.update(date=str(MON), note="  hi  ", audience="")
		doc.validate()
		self.assertEqual((doc.note, doc.audience), ("hi", None))
		doc.note = "  "
		with self.assertRaises(_Throw):
			doc.validate()


class TestToolAndWiring(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_roles(self):
		self.assertEqual(
			set(engine.SCHEDULER_ROLES),
			{"System Manager", "Projects Manager", "Projects User", "Maintenance Supervisor"},
		)
		self.assertIs(blocks.SCHEDULER_ROLES, engine.SCHEDULER_ROLES)
		self.assertEqual(set(blocks.ACCESS_ROLES), pp.PLANNER_ROLES | mp.PLANNER_ROLES)

	def test_the_tool_is_read_only_and_never_shows_a_note(self):
		tool = tool_module.CrewConflicts()
		self.assertEqual((tool.name, tool.requires_permission), ("crew_conflicts", "Planner Resource"))
		self.assertIn("crew_conflicts", gate.EXPLICIT_READONLY)
		self.assertNotIn("crew_conflicts", gate.APP_MUTATING)
		self.assertIs(tool.annotations["readOnlyHint"], True)
		for prop in tool.inputSchema["properties"].values():
			self.assertTrue(prop["description"])
		row = {
			"date": str(TUE),
			"kind": "blocked",
			"resource_label": "Austin Healey",
			"message": "Austin Healey: Booked on a day off (Unavailable)",
			"items": [{"doctype": "Task", "name": "T-3", "title": "Dig"}],
			"block": {"name": "PBLK-1", "note": NOTE},
			"fixes": [{"type": "keep"}],
			"acknowledged": None,
		}
		kept = dict(
			row,
			kind="overbooked",
			acknowledged={"reason": "fine", "by": PM, "by_name": "Pat Manager", "on": "x"},
		)
		with mock.patch.object(conflicts, "get_conflicts", lambda start, end: [row, kept]) as fake:
			answer = tool.execute({"start": str(MON), "end": str(FRI)})
			everything = tool.execute({"start": str(MON), "include_kept": True, "kind": "overbooked"})
		self.assertTrue(answer["success"])
		self.assertEqual((answer["count"], answer["kept_hidden"]), (1, 1))
		self.assertNotIn(NOTE, json.dumps(answer))
		self.assertNotIn("fixes", json.dumps(answer))
		self.assertEqual(everything["conflicts"][0]["kept"]["by"], "Pat Manager")
		self.assertFalse(tool.execute({"start": str(MON), "end": "2027-01-30"})["success"])
		self.assertFalse(tool.execute({"kind": "nonsense"})["success"])
		self.assertEqual(fake.__name__, "<lambda>")

	def test_hooks_and_ci(self):
		hooks = HOOKS.read_text(encoding="utf-8")
		self.assertIn('"erpnext_enhancements.assistant_tools.crew_conflicts.CrewConflicts"', hooks)
		self.assertIn(
			"python -m unittest erpnext_enhancements.tests.test_planner_phase6d",
			CI.read_text(encoding="utf-8"),
		)

	def test_new_files_are_lf_only(self):
		paths = [
			APP / "api" / "planner_blocks.py",
			APP / "api" / "planner_conflicts.py",
			APP / "assistant_tools" / "crew_conflicts.py",
			Path(__file__),
			*(
				DOCTYPES / folder / f"{folder}{ext}"
				for folder in TestDoctypes.NEW.values()
				for ext in (".py", ".json")
			),
		]
		for path in paths:
			self.assertNotIn(b"\r", path.read_bytes(), path.name)
