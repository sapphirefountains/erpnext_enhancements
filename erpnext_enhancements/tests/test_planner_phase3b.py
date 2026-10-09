# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Project Planner Phase 3B, "telling people": draft and publish, the combined 6 AM digest, the
48-hour change alerts, the printable crew sheet and My week.

What this pins, because each of them fails quietly:

* **A draft never touches the Task.** ``save_task(draft=1)`` (and ``add_crew``/``swap_crew``) write
  a Planner Draft Change row and nothing else: no ``doc.save()``, so no assignment sync and nobody
  told. Re-drafting merges into the one row and builds on the earlier draft. The drafting edit is
  compared with the live ``_apply`` edit on the same change, so the two cannot drift apart.
* **Publishing is all or nothing on conflicts, judged on the whole batch**, replays each draft
  through ``_apply``, skips (and reports) a task somebody else changed since it was drafted, and
  tells each affected person once, never the planner who published.
* **The digest sends at most once per person per day through a database row** claimed before
  anything goes out (the deploy FLUSHDBs redis), skips people with nothing booked, and the two
  older digests skip whoever it covers. Both new switches default **off**.
* **Change alerts coalesce** to one per person per request, only for the next 48 hours, never during
  a publish, and never raise into a save.
* **The crew sheet and My week** escape what people typed, and print in a browser window.

Bench-free: installs its own ``frappe`` stub (and a recording ``email_style``), runs the real
modules, and puts ``sys.modules`` back afterwards.

Run: python -m unittest erpnext_enhancements.tests.test_planner_phase3b
"""

import ast
import copy
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
API_PATH = APP / "api/project_planner.py"
NOTICES_PATH = APP / "project_enhancements/planner_notices.py"
DIGEST_PATH = APP / "project_enhancements/planner_digest.py"
PAGE_JS = APP / "project_enhancements/page/project_planner/project_planner.js"
DOCTYPES = APP / "project_enhancements/doctype"
SETTINGS_JSON = DOCTYPES / "project_planner_settings/project_planner_settings.json"
SETTINGS_JS = DOCTYPES / "project_planner_settings/project_planner_settings.js"
PATCH = APP / "patches/materialize_planner_notice_settings.py"

D = datetime.date
DT = datetime.datetime
TODAY = D(2026, 10, 8)  # a Thursday
NOW = DT(2026, 10, 8, 7, 0, 0)
MON, TUE, WED, THU, FRI = (D(2026, 10, 12) + datetime.timedelta(days=i) for i in range(5))
BASE = "2026-10-08 09:00:00"

STUBBED = (
	"frappe",
	"frappe.utils",
	"erpnext_enhancements.email_style",
	"erpnext_enhancements.project_enhancements",
	"erpnext_enhancements.project_enhancements.crew_availability",
	"erpnext_enhancements.project_enhancements.routing",
	"erpnext_enhancements.project_enhancements.planner_notices",
	"erpnext_enhancements.project_enhancements.planner_digest",
	"erpnext_enhancements.api.project_planner",
	"erpnext_enhancements.api.maintenance_planner",
)
_saved_modules = {}
_modules_before = set()
frappe = None
engine = None
api = None
notices = None
digest = None
email_style = None


class _Doc(dict):
	"""Enough of a frappe Document / _dict: dict access plus attribute access."""

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None

	def __setattr__(self, name, value):
		self[name] = value


class _Throw(Exception):
	pass


class _PermissionError(Exception):
	pass


class _DoesNotExist(Exception):
	pass


def _getdate(value=None):
	if value is None:
		return TODAY
	if isinstance(value, DT):
		return value.date()
	if isinstance(value, D):
		return value
	return D.fromisoformat(str(value)[:10])


def _throw(message, exc=None, title=None):
	raise (exc or _Throw)(message)


class _Callbacks:
	def __init__(self):
		self.functions = []

	def add(self, function):
		self.functions.append(function)

	def run(self):
		functions, self.functions = self.functions, []
		for function in functions:
			function()


class _TaskDoc(_Doc):
	"""A Task: save() records a snapshot and bumps modified, as the framework would."""

	def check_permission(self, ptype):
		frappe.calls.append(("check_permission", ptype, self.get("name")))

	def set(self, field, value):
		self[field] = [_Doc(row) for row in value] if isinstance(value, list) else value

	def append(self, field, row):
		self.setdefault(field, []).append(_Doc(row))

	def save(self):
		if self.get("fail_save"):
			raise _Throw("ERPNext says the task cannot leave its project's dates")
		frappe.saved.append(json.loads(json.dumps(dict(self), default=str)))
		frappe.sequence.append(f"save:{self.get('name')}")
		self["modified"] = "2026-10-08 10:00:01"

	def add_comment(self, kind, text):
		frappe.comments.append((self.get("name"), kind, text))

	def get_doc_before_save(self):
		before = self.get("_before")
		if before == "boom":
			raise RuntimeError("no before")
		return before


class _Row(_Doc):
	"""Any other document: insert() and save() write through to ``frappe.tables``."""

	def insert(self, ignore_permissions=False):
		frappe.counter += 1
		doctype = self["doctype"]
		self.setdefault("name", self.get("log_key") or f"{doctype[:3].upper()}-{frappe.counter}")
		self.setdefault("owner", frappe.session.user)
		self["creation"] = self["modified"] = f"2026-10-08 09:{frappe.counter:02d}:00"
		if frappe.fail_insert.get(doctype):
			raise frappe.fail_insert[doctype]
		frappe.tables.setdefault(doctype, []).append(dict(self))
		frappe.sequence.append(f"insert:{doctype}")
		return self

	def save(self):
		frappe.counter += 1
		self["modified"] = f"2026-10-08 09:{frappe.counter:02d}:30"
		rows = frappe.tables.setdefault(self["doctype"], [])
		for index, row in enumerate(rows):
			if row.get("name") == self.get("name"):
				rows[index] = dict(self)
				break
		frappe.sequence.append(f"save:{self['doctype']}")
		return self


def _matches(row, filters):
	for key, want in (filters or {}).items():
		value = row.get(key)
		if isinstance(want, list | tuple) and want:
			op = want[0]
			if op == "in":
				if value not in want[1]:
					return False
			elif op == "is":
				if (want[1] == "set") != bool(value):
					return False
			continue
		if value != want:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, pluck=None, limit_page_length=None, **kwargs):
	frappe.queries.append((doctype, {"filters": filters, "fields": fields, **kwargs}))
	rows = frappe.tables.get(doctype, [])
	rows = (
		rows(filters)
		if callable(rows)
		else [r for r in rows if _matches(r, filters if isinstance(filters, dict) else None)]
	)
	if limit_page_length:
		rows = rows[:limit_page_length]
	if pluck:
		return [row[pluck] for row in rows]
	return [_Doc(row) for row in rows]


def _get_doc(doctype, name=None):
	if isinstance(doctype, dict):
		return _Row(doctype)
	if doctype == "Task":
		if ("Task", name) not in frappe.docs:
			raise _DoesNotExist(name)
		# A fresh load each time, as from the database.
		return copy.deepcopy(frappe.docs[("Task", name)])
	for row in frappe.tables.get(doctype, []):
		if row.get("name") == name:
			return _Row(dict(row, doctype=doctype))
	raise _DoesNotExist(f"{doctype} {name}")


def _get_cached_doc(doctype, name=None):
	key = (doctype, name) if name else doctype
	if key not in frappe.cached:
		raise KeyError(key)
	return frappe.cached[key]


def _key(name):
	return tuple(sorted(name.items())) if isinstance(name, dict) else name


def _sql(query, values=None, as_dict=False, **kwargs):
	frappe.queries.append(("sql", query))
	for marker, rows in frappe.sql_rows.items():
		if marker in query:
			return [_Doc(row) for row in rows]
	return []


def _exists(doctype, name=None):
	if isinstance(name, str) and doctype in frappe.tables:
		return any(row.get("name") == name for row in frappe.tables[doctype])
	return True


def _fake_email_style():
	module = types.ModuleType("erpnext_enhancements.email_style")
	module.calls = []

	def piece(tag):
		def build(*args, **kwargs):
			module.calls.append((tag, args, kwargs))
			return f"<{tag}>{json.dumps(args, default=str)}</{tag}>"

		return build

	for name in ("p", "table", "button", "links", "note", "code", "bullets"):
		setattr(module, name, piece(name))

	def wrap(body, **kwargs):
		module.calls.append(("wrap", (body,), kwargs))
		return f"<shell>{body}</shell>"

	module.wrap = wrap
	return module


def setUpModule():
	global frappe, engine, api, notices, digest, email_style
	_modules_before.update(sys.modules)
	for name in STUBBED:
		_saved_modules[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.getdate = _getdate
	utils.add_days = lambda value, days: _getdate(value) + datetime.timedelta(days=days)
	utils.date_diff = lambda a, b: (_getdate(a) - _getdate(b)).days
	utils.formatdate = lambda value=None, *a, **k: _getdate(value).strftime("%m-%d-%Y")
	utils.format_datetime = lambda value=None, *a, **k: str(value)
	utils.nowdate = lambda: str(TODAY)
	utils.now_datetime = lambda: NOW
	utils.flt = lambda value, precision=None: float(value or 0)
	utils.cint = lambda value: int(float(value or 0))
	utils.strip_html_tags = lambda text: re.sub(r"<[^>]+>", "", str(text))
	utils.escape_html = lambda text: str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
	utils.get_url = lambda path="": "https://erp.example.com" + (path or "")
	utils.get_fullname = lambda user=None: {"nik@example.com": "Nik Bradshaw"}.get(user, user)
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	frappe.throw = _throw
	frappe.PermissionError = _PermissionError
	frappe.DoesNotExistError = _DoesNotExist
	frappe.session = types.SimpleNamespace(user="nik@example.com")
	frappe.local = types.SimpleNamespace(message_log=[])
	frappe.flags = types.SimpleNamespace()
	frappe.parse_json = json.loads
	frappe.get_traceback = lambda: "traceback"
	sys.modules.update({"frappe": frappe, "frappe.utils": utils})
	email_style = _fake_email_style()
	sys.modules["erpnext_enhancements.email_style"] = email_style
	_reset()

	engine = importlib.import_module("erpnext_enhancements.project_enhancements.crew_availability")
	importlib.import_module("erpnext_enhancements.project_enhancements.routing")
	api = importlib.import_module("erpnext_enhancements.api.project_planner")
	importlib.import_module("erpnext_enhancements.api.maintenance_planner")
	notices = importlib.import_module("erpnext_enhancements.project_enhancements.planner_notices")
	digest = importlib.import_module("erpnext_enhancements.project_enhancements.planner_digest")


def tearDownModule():
	for name in set(sys.modules) - _modules_before:
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements")):
			sys.modules.pop(name, None)
	for name, module in _saved_modules.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def _reset():
	frappe.roles = ["Projects User"]
	frappe.get_roles = lambda user=None: list(frappe.roles)
	frappe.session.user = "nik@example.com"
	frappe.local = types.SimpleNamespace(message_log=[])
	frappe.flags = types.SimpleNamespace()
	frappe.tables = {"Planner Draft Change": [], "Planner Digest Log": []}
	frappe.queries = []
	frappe.sql_rows = {}
	frappe.errors = []
	frappe.docs = {}
	frappe.cached = {}
	frappe.saved, frappe.comments, frappe.calls, frappe.sequence = [], [], [], []
	frappe.sent, frappe.enqueued, frappe.db_calls = [], [], []
	frappe.counter = 0
	frappe.fail_insert = {}
	frappe.values = {}
	frappe.log_error = lambda *a, **k: frappe.errors.append((a, k))
	frappe.get_all = _get_all
	frappe.get_doc = _get_doc
	frappe.get_cached_doc = _get_cached_doc
	frappe.get_meta = lambda doctype: types.SimpleNamespace(has_field=lambda f: False, fields=[])
	frappe.has_permission = lambda *a, **k: True
	frappe.get_system_settings = lambda key: "Sunday"
	frappe.defaults = types.SimpleNamespace(get_user_default=lambda key: "Sapphire Fountains")

	def sendmail(**kwargs):
		frappe.sent.append(kwargs)
		frappe.sequence.append("sendmail")

	def enqueue(method, **kwargs):
		frappe.enqueued.append((method, kwargs))

	def commit():
		frappe.db_calls.append("commit")
		frappe.sequence.append("commit")

	frappe.sendmail = sendmail
	frappe.enqueue = enqueue
	frappe.db = types.SimpleNamespace(
		sql=_sql,
		exists=_exists,
		has_column=lambda doctype, column: True,
		get_value=lambda doctype, name, field=None, *a, **k: frappe.values.get((doctype, _key(name), field)),
		get_single_value=lambda doctype, field, *a, **k: None,
		set_value=lambda *a, **k: frappe.db_calls.append(("set_value", a, k)),
		savepoint=lambda name: frappe.db_calls.append(("savepoint", name)),
		rollback=lambda save_point=None: frappe.db_calls.append(("rollback", save_point)),
		commit=commit,
		after_commit=_Callbacks(),
		after_rollback=_Callbacks(),
	)
	if email_style is not None:
		email_style.calls.clear()


def _settings(**values):
	frappe.cached["Project Planner Settings"] = _Doc(values)


PEOPLE = [
	{"name": "RES-1", "resource_name": "Austin Healey", "user": "austin@example.com", "is_active": 1},
	{"name": "RES-2", "resource_name": "Lisa Park", "user": "lisa@example.com", "is_active": 1},
	{"name": "RES-3", "resource_name": "Nik Bradshaw", "user": "nik@example.com", "is_active": 1},
]


def _task(name="TASK-1", start="2026-10-12 08:00:00", end="2026-10-14 17:00:00", crew=("RES-1",), **values):
	doc = _TaskDoc(
		{
			"doctype": "Task",
			"name": name,
			"subject": f"Subject {name}",
			"project": "PRJ-1",
			"status": "Open",
			"exp_start_date": start,
			"exp_end_date": end,
			"expected_time": 12,
			"custom_start_datetime": None,
			"custom_end_datetime": None,
			"custom_rental_booking": None,
			"custom_rental_task_kind": None,
			"custom_crew_size": 0,
			"color": None,
			"modified": BASE,
			"custom_crew": [
				_Doc(
					resource=r,
					resource_name=next(p["resource_name"] for p in PEOPLE if p["name"] == r),
					user=next(p["user"] for p in PEOPLE if p["name"] == r),
					hours=0,
					is_lead=1 if i == 0 else 0,
				)
				for i, r in enumerate(crew)
			],
			"custom_required_credentials": [],
		}
	)
	doc.update(values)
	frappe.docs[("Task", name)] = doc
	return doc


def _draft_row(task, payload, base=BASE, owner="nik@example.com", status="Draft", name=None):
	frappe.counter += 1
	row = {
		"name": name or f"PDC-{frappe.counter}",
		"doctype": "Planner Draft Change",
		"task": task,
		"subject": f"Subject {task}",
		"payload": json.dumps(payload),
		"base_modified": base,
		"status": status,
		"owner": owner,
		"modified": f"2026-10-08 08:{frappe.counter:02d}:00",
	}
	frappe.tables["Planner Draft Change"].append(row)
	return row


class _Base(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.tables["Planner Resource"] = [dict(p) for p in PEOPLE]
		frappe.values[("Project", "PRJ-1", "project_name")] = "Highlands"
		self.conflicts = {}
		self.patches = [
			mock.patch.object(engine, "preview_conflicts", self._preview),
			mock.patch.object(engine, "_preview", lambda task, crew, *a: ({}, {})),
		]
		for patch in self.patches:
			patch.start()

	def tearDown(self):
		for patch in self.patches:
			patch.stop()

	def _preview(self, task, crew, start=None, end=None):
		span = engine.task_span(task)
		hook = self.conflicts.get("hook")
		if hook:
			return hook(task, crew, span)
		return self.conflicts.get(span[0] if span else None, {})

	def _drafts(self, status="Draft"):
		return [r for r in frappe.tables["Planner Draft Change"] if r.get("status") == status]


# ====================================================================== pure helpers


class TestPayloads(unittest.TestCase):
	def test_a_new_change_merges_over_the_old_and_keeps_the_rest(self):
		old = {"start": "2026-10-12", "end": "2026-10-14", "crew_size": 2}
		out = api.merge_payload(old, {"expected_time": 6, "crew": None, "bogus": 1})
		self.assertEqual(
			out, {"start": "2026-10-12", "end": "2026-10-14", "crew_size": 2, "expected_time": 6}
		)

	def test_a_start_only_move_stores_both_dates_from_the_span(self):
		out = api.merge_payload({}, {"start": "2026-10-19"}, (D(2026, 10, 19), D(2026, 10, 21)))
		self.assertEqual(out, {"start": "2026-10-19", "end": "2026-10-21"})

	def test_unknown_keys_and_bad_json_are_never_applied(self):
		self.assertEqual(api.load_payload("{not json"), {})
		self.assertEqual(api.load_payload("[1, 2]"), {})
		self.assertEqual(
			api.load_payload('{"start": "2026-10-12", "status": "Completed"}'), {"start": "2026-10-12"}
		)

	def test_tentative_goes_only_to_a_function_that_takes_it(self):
		payload = {"start": "2026-10-12", "tentative": 1, "crew": None}
		self.assertEqual(api.payload_kwargs(payload), {"start": "2026-10-12", "tentative": 1})
		self.assertEqual(api.payload_kwargs(payload, with_tentative=False), {"start": "2026-10-12"})

	def test_a_blank_crew_hours_is_an_even_share(self):
		crew = [
			{"resource": "RES-1", "hours": 0, "is_lead": True},
			{"resource": "RES-2", "hours": 3.456},
			{"resource": None},
		]
		self.assertEqual(
			api.crew_payload(crew),
			[
				{"resource": "RES-1", "hours": None, "is_lead": 1},
				{"resource": "RES-2", "hours": 3.46, "is_lead": 0},
			],
		)

	def test_same_moment_compares_instants_not_text(self):
		self.assertTrue(api.same_moment(DT(2026, 10, 8, 9, 0, 0, 120000), "2026-10-08 09:00:00.120000"))
		self.assertFalse(api.same_moment("2026-10-08 09:00:00", "2026-10-08 09:00:01"))


class TestBatchConflicts(unittest.TestCase):
	def _cell(self, refs, conflicts):
		return {"bookings": [{"ref": r, "kind": "task"} for r in refs], "conflicts": conflicts}

	def test_only_new_conflicts_on_days_a_draft_lands_on(self):
		before = {"RES-1": {"2026-10-12": self._cell(["T-OLD"], ["Over by 1h"])}}
		after = {
			"RES-1": {
				"2026-10-12": self._cell(["T-OLD", "T-1"], ["Over by 1h", "Over by 5h"]),
				"2026-10-13": self._cell(["T-OTHER"], ["Over by 2h"]),
			}
		}
		out = api.batch_conflicts(before, after, ["T-1"], {"RES-1": "Austin Healey"})
		self.assertEqual(out, {"Austin Healey": ["2026-10-12: Over by 5h"]})

	def test_two_drafts_that_overbook_together_are_caught(self):
		after = {"RES-1": {"2026-10-12": self._cell(["T-1", "T-2"], ["Over by 4h"])}}
		self.assertEqual(
			api.batch_conflicts({}, after, ["T-1", "T-2"], {}), {"RES-1": ["2026-10-12: Over by 4h"]}
		)

	def test_a_double_booking_between_two_other_tasks_is_not_the_drafts_doing(self):
		after = {
			"RES-1": {
				"2026-10-12": self._cell(
					["T-1", "A", "B"],
					["Double-booked 09:00–10:00 (A, B)", "Double-booked 10:00–11:00 (A, T-1)"],
				)
			}
		}
		self.assertEqual(
			api.batch_conflicts({}, after, ["T-1"], {}),
			{"RES-1": ["2026-10-12: Double-booked 10:00–11:00 (A, T-1)"]},
		)


class TestDaysAndLinks(unittest.TestCase):
	def test_week_start_follows_the_system_first_day(self):
		self.assertEqual(api.week_start(WED, "Sunday"), D(2026, 10, 11))
		self.assertEqual(api.week_start(WED, "Monday"), MON)
		self.assertEqual(api.week_start(D(2026, 10, 11), "Monday"), D(2026, 10, 5))
		self.assertEqual(api.week_start(WED, "Nonsense"), D(2026, 10, 11))

	def test_maps_link_searches_the_address_else_the_point(self):
		self.assertEqual(
			api.maps_link("85 W 300 S,\n Bountiful UT"),
			"https://www.google.com/maps/search/?api=1&query=85+W+300+S%2C+Bountiful+UT",
		)
		self.assertTrue(api.maps_link(None, 40.88, -111.88).endswith("query=40.88%2C-111.88"))
		self.assertIsNone(api.maps_link("  ", None, None))

	def test_crewmates_come_from_cells_crews_and_visits(self):
		days = {
			"RES-1": {
				"2026-10-12": {"bookings": [{"kind": "task", "ref": "T-1"}, {"kind": "drive", "ref": None}]}
			},
			"RES-2": {"2026-10-12": {"bookings": [{"kind": "task", "ref": "T-1"}]}},
		}
		crews = {"T-1": [{"resource": "RES-9", "label": "Sub Contractor"}]}
		out = api.crewmates(days, {"RES-1": "Austin", "RES-2": "Lisa"}, crews, {"SMR-1": ["Ben"]})
		self.assertEqual(out[("task", "T-1")], ["Austin", "Lisa", "Sub Contractor"])
		self.assertEqual(out[("visit", "SMR-1")], ["Ben"])
		self.assertNotIn(("drive", None), out)

	def test_day_entry_orders_stops_like_the_route_and_leaves_the_person_out(self):
		person = {"name": "RES-1", "label": "Austin Healey"}
		cell = {
			"capacity": 8,
			"booked": 5,
			"free": 3,
			"off": None,
			"conflicts": [],
			"bookings": [
				{
					"kind": "visit",
					"ref": "SMR-1",
					"label": "Lake Site",
					"project": "PRJ-2",
					"hours": 2,
					"slot": None,
				},
				{
					"kind": "task",
					"ref": "T-1",
					"label": "Pump set",
					"project": "PRJ-1",
					"hours": 3,
					"slot": ["09:00", "12:00"],
				},
			],
		}
		details = {"T-1": {"point": None, "address": "1 Main St"}}
		mates = {("task", "T-1"): ["Austin Healey", "Lisa Park"]}
		entry = api.day_entry(
			person,
			MON,
			cell,
			None,
			dict(engine.DEFAULT_SETTINGS),
			details,
			{"PRJ-1": "Highlands"},
			mates,
			{"T-1": "Bring the pump"},
		)
		self.assertEqual([i["ref"] for i in entry["items"]], ["T-1", "SMR-1"])
		first = entry["items"][0]
		self.assertEqual(
			(first["time"], first["crew"], first["notes"]), ("09:00", ["Lisa Park"], "Bring the pump")
		)
		self.assertEqual(first["project_title"], "Highlands")
		self.assertIn("query=1+Main+St", first["maps_url"])
		self.assertIsNone(entry["items"][1]["notes"])
		self.assertEqual((entry["date"], entry["free"]), ("2026-10-12", 3))


class TestWording(unittest.TestCase):
	def _snap(self, start, end=None, users=("austin@example.com",), slot=None, hours=None, expected=8):
		state = {
			"name": "T-1",
			"subject": "Highlands pump set",
			"exp_start_date": str(start),
			"exp_end_date": str(end or start),
			"expected_time": expected,
			"custom_start_datetime": f"{start} {slot[0]}:00" if slot else None,
			"custom_end_datetime": f"{start} {slot[1]}:00" if slot else None,
		}
		return notices.snapshot(state, users, hours)

	def test_when_text(self):
		self.assertEqual(notices.when_text((THU, THU)), "Thu Oct 15")
		self.assertEqual(notices.when_text((THU, FRI)), "Thu Oct 15 – Fri Oct 16")
		self.assertEqual(notices.when_text((FRI, FRI), ["08:00", "10:00"], long=True), "Friday 8:00")
		self.assertEqual(notices.when_text(None), "no date")

	def test_a_move_in_both_wordings(self):
		before, after = self._snap(THU), self._snap(FRI, slot=("08:00", "10:00"))
		short = notices.change_lines(before, after)["austin@example.com"][0]
		self.assertEqual(short["text"], "Highlands pump set moved from Thu Oct 15 to Fri Oct 16 8:00")
		self.assertEqual(short["day"], "2026-10-15")
		long = notices.change_lines(before, after, long=True)["austin@example.com"][0]
		self.assertEqual(long["text"], "Highlands pump set moved to Friday 8:00")

	def test_added_removed_hours_and_nothing(self):
		before = self._snap(
			THU, users=("austin@example.com", "lisa@example.com"), hours={"lisa@example.com": 2}
		)
		after = self._snap(THU, users=("lisa@example.com", "ben@example.com"), hours={"lisa@example.com": 4})
		lines = notices.change_lines(before, after)
		self.assertEqual(lines["austin@example.com"][0]["text"], "You're off Highlands pump set (Thu Oct 15)")
		self.assertEqual(lines["ben@example.com"][0]["text"], "You're on Highlands pump set: Thu Oct 15")
		self.assertEqual(
			lines["lisa@example.com"][0]["text"], "Highlands pump set (Thu Oct 15): your hours changed"
		)
		self.assertEqual(notices.change_lines(self._snap(THU), self._snap(THU)), {})
		self.assertEqual(
			notices.change_lines(None, self._snap(THU))["austin@example.com"][0]["day"], "2026-10-15"
		)

	def test_the_window_keeps_only_the_next_48_hours(self):
		window = notices.alert_window(NOW)
		self.assertEqual(window, (D(2026, 10, 8), D(2026, 10, 10)))
		self.assertEqual(notices.change_lines(self._snap(MON), self._snap(TUE), window), {})
		lines = notices.change_lines(self._snap(D(2026, 10, 9)), self._snap(MON), window, long=True)
		self.assertEqual(lines["austin@example.com"][0]["day"], "2026-10-09")

	def test_the_alert_headline(self):
		entries = [
			{"text": "Highlands pump set moved to Monday", "day": "2026-10-09"},
			{"text": "You're on Lake job: Thursday", "day": "2026-10-08"},
		]
		self.assertEqual(
			notices.alert_headline(entries),
			"Your Thursday changed: Highlands pump set moved to Monday; You're on Lake job: Thursday",
		)
		self.assertEqual(notices.alert_headline([]), "")

	def test_a_text_holds_ten_lines_and_a_link(self):
		text = notices.sms_text("your schedule changed:", [f"line {i}" for i in range(12)], "https://x/route")
		self.assertIn("• line 9", text)
		self.assertNotIn("• line 10", text)
		self.assertIn("…and 2 more", text)
		self.assertTrue(text.endswith("https://x/route"))

	def test_digest_lines_and_text(self):
		entry = {
			"travel": None,
			"items": [
				{
					"label": "Pump set",
					"project_title": "Highlands",
					"time": "08:30",
					"address": "1 Main St",
					"crew": ["Lisa"],
					"hours": 3,
				},
				{
					"label": "Lake Site",
					"project_title": "Lake Site",
					"time": None,
					"address": None,
					"crew": [],
					"hours": 2,
				},
			],
		}
		lines = digest.digest_lines(entry)
		self.assertEqual(
			lines[0],
			{
				"time": "8:30",
				"what": "Highlands: Pump set",
				"where": "1 Main St",
				"with": "Lisa",
				"hours": 3.0,
			},
		)
		self.assertEqual(lines[1]["what"], "Lake Site")
		text = digest.digest_text(
			TODAY, lines, "https://erp.example.com/app/project-planner/route/RES-1/2026-10-08"
		)
		self.assertTrue(text.startswith("Sapphire Fountains — your day, Thu 10/8 (2):"))
		self.assertIn("1. 8:30 Highlands: Pump set — 1 Main St (with Lisa)", text)
		self.assertIn("2. Lake Site", text)
		self.assertTrue(
			text.endswith("Route: https://erp.example.com/app/project-planner/route/RES-1/2026-10-08")
		)
		self.assertEqual(
			digest.digest_lines({"travel": "Travel: Vegas", "items": []})[0]["what"], "Travel: Vegas"
		)


# ====================================================================== drafting


class TestDrafting(_Base):
	def test_a_draft_writes_a_row_and_never_the_task(self):
		_task()
		result = api.save_task("TASK-1", BASE, start="2026-10-19", draft="1")
		self.assertEqual(frappe.saved, [])
		(row,) = self._drafts()
		self.assertEqual(
			(row["task"], row["owner"], row["base_modified"]), ("TASK-1", "nik@example.com", BASE)
		)
		self.assertEqual(json.loads(row["payload"]), {"start": "2026-10-19", "end": "2026-10-21"})
		self.assertTrue(result["drafted"])
		self.assertTrue(result["card"]["drafted"])
		self.assertEqual((result["card"]["start"], result["card"]["end"]), ("2026-10-19", "2026-10-21"))
		self.assertEqual(result["modified"], BASE)  # the task did not change
		self.assertIn(("check_permission", "write", "TASK-1"), frappe.calls)

	def test_redrafting_merges_into_the_one_row_and_builds_on_the_draft(self):
		_task()
		api.save_task("TASK-1", BASE, start="2026-10-19", draft=1)
		result = api.add_crew("TASK-1", "RES-2", BASE, draft=1)
		(row,) = self._drafts()
		payload = json.loads(row["payload"])
		self.assertEqual((payload["start"], payload["end"]), ("2026-10-19", "2026-10-21"))
		self.assertEqual([m["resource"] for m in payload["crew"]], ["RES-1", "RES-2"])
		self.assertEqual(result["card"]["start"], "2026-10-19")  # the crew change sits on the moved dates
		self.assertEqual(frappe.saved, [])
		self.assertEqual(len(frappe.tables["Planner Draft Change"]), 1)

	def test_swap_crew_drafts_too(self):
		_task()
		api.swap_crew("TASK-1", "RES-1", "RES-2", BASE, date="2026-10-20", draft=1)
		payload = json.loads(self._drafts()[0]["payload"])
		self.assertEqual([m["resource"] for m in payload["crew"]], ["RES-2"])
		self.assertEqual(payload["start"], "2026-10-20")
		self.assertEqual(frappe.saved, [])

	def test_conflicts_are_information_while_drafting(self):
		_task()
		self.conflicts[D(2026, 10, 19)] = {"Austin Healey": ["2026-10-19: Over by 2h"]}
		result = api.save_task("TASK-1", BASE, start="2026-10-19", draft=1)
		self.assertNotIn("needs_reason", result)
		self.assertEqual(result["conflicts"], {"Austin Healey": ["2026-10-19: Over by 2h"]})
		self.assertEqual(result["warnings"], ["Not published yet. Austin Healey: 2026-10-19: Over by 2h"])
		self.assertEqual(len(self._drafts()), 1)

	def test_a_draft_of_a_task_changed_since_starts_again(self):
		_task()
		_draft_row("TASK-1", {"start": "2026-10-26", "end": "2026-10-28"}, base="2026-10-01 00:00:00")
		result = api.save_task("TASK-1", BASE, expected_time=20, draft=1)
		payload = json.loads(self._drafts()[0]["payload"])
		self.assertEqual(payload, {"expected_time": 20.0})
		self.assertEqual(self._drafts()[0]["base_modified"], BASE)
		self.assertIn("someone else changed the task", result["warnings"][0])

	def test_drafting_back_to_the_stored_task_leaves_no_draft(self):
		# A drag there and back (or an Undo) must not leave a no-op change waiting to be published.
		_task()
		api.save_task("TASK-1", BASE, start="2026-10-19", draft=1)
		result = api.save_task("TASK-1", BASE, start="2026-10-12", draft=1)
		self.assertEqual(self._drafts("Draft"), [])
		self.assertEqual(len(self._drafts("Discarded")), 1)
		self.assertFalse(result["card"]["drafted"])
		# A first draft that changes nothing inserts nothing.
		api.save_task("TASK-1", BASE, expected_time=12, draft=1)
		self.assertEqual(len(frappe.tables["Planner Draft Change"]), 1)

	def test_a_racing_duplicate_row_is_folded_away(self):
		_task()
		_draft_row("TASK-1", {"start": "2026-10-19", "end": "2026-10-21"})
		older = frappe.tables["Planner Draft Change"][0]
		older["modified"] = "2026-10-08 07:00:00"
		newer = _draft_row("TASK-1", {"start": "2026-10-20", "end": "2026-10-22"})
		rows, duplicates = api._draft_rows()
		self.assertEqual(
			([r["name"] for r in rows], [r["name"] for r in duplicates]), ([newer["name"]], [older["name"]])
		)
		result = api.save_task("TASK-1", BASE, expected_time=6, draft=1)
		self.assertEqual(result["card"]["start"], "2026-10-20")  # built on the newest
		self.assertIn(
			("set_value", ("Planner Draft Change", older["name"], "status", "Discarded"), {}), frappe.db_calls
		)

	def test_drafting_still_refuses_a_stale_card(self):
		_task()
		with self.assertRaises(_Throw):
			api.save_task("TASK-1", "2026-10-01 09:00:00", start="2026-10-19", draft=1)
		self.assertEqual(self._drafts(), [])

	def test_with_draft_off_save_task_is_unchanged(self):
		_task()
		result = api.save_task("TASK-1", BASE, start="2026-10-19")
		self.assertEqual(len(frappe.saved), 1)
		self.assertEqual(self._drafts(), [])
		self.assertNotIn("drafted", result)
		self.assertEqual(result["modified"], "2026-10-08 10:00:01")

	def test_the_drafting_edit_is_the_live_edit(self):
		# _draft_edit duplicates the edit half of _apply; on the same change both must leave the
		# task in the same state, or a published draft would differ from what was drafted.
		change = dict(
			start="2026-10-19",
			expected_time=20,
			crew_size=3,
			crew=[{"resource": "RES-2", "hours": 4, "is_lead": 1}, "RES-1"],
			credentials=["Forklift", {"credential_type": "Confined Space Entry"}],
		)
		live = _task()
		api._apply(live, reason="ok", **change)
		drafted = _task()
		api._draft_edit(drafted, **change)
		for field in (
			"exp_start_date",
			"exp_end_date",
			"custom_start_datetime",
			"custom_end_datetime",
			"expected_time",
			"custom_crew_size",
			"custom_crew",
			"custom_required_credentials",
		):
			self.assertEqual(
				json.dumps(live.get(field), default=str), json.dumps(drafted.get(field), default=str), field
			)


class TestOverlay(_Base):
	def setUp(self):
		super().setUp()
		self.calls = []
		stored = [
			_Doc(_task("TASK-1", crew=("RES-1",))),
			_Doc(_task("TASK-2", start="2026-10-13 08:00:00", end="2026-10-13 17:00:00")),
		]
		_task("TASK-3", start=None, end=None)
		_task("TASK-4", start="2026-10-15 08:00:00", end="2026-10-15 17:00:00")
		_task("TASK-5", start="2026-10-16 08:00:00", end="2026-10-16 17:00:00")
		frappe.sql_rows["INNER JOIN `tabProject` p"] = [
			{k: v for k, v in frappe.docs[("Task", "TASK-3")].items() if k != "custom_crew"}
		]
		frappe.tables["Task Required Credential"] = []

		def compute(start, end, resources=None, exclude=(), extra=()):
			self.calls.append({"exclude": set(exclude or ()), "extra": [dict(e[0]) for e in extra or ()]})
			tasks = [t for t in stored if t["name"] not in set(exclude or ())] + [
				_Doc(e[0]) for e in extra or ()
			]
			crews = {t["name"]: [] for t in stored}
			crews.update({e[0]["name"]: list(e[1]) for e in extra or ()})
			return {
				"resources": [],
				"days": {},
				"tasks": tasks,
				"crews": crews,
				"todo_users": {},
				"task_hours": {},
				"settings": {"default_day_hours": 8.0, "maintenance_visit_hours": 2.0},
				"user_to_resource": {},
				"routes": {},
			}

		self.patches += [
			mock.patch.object(engine, "_compute", compute),
			mock.patch.object(engine, "read_crews", lambda names: ({}, {})),
		]
		for patch in self.patches[2:]:
			patch.start()

	def test_draft_mode_overlays_the_callers_drafts(self):
		_draft_row("TASK-1", {"start": "2026-10-14", "end": "2026-10-16", "credentials": ["Forklift"]})
		_draft_row("TASK-3", {"start": "2026-10-15"})  # undated in the database, drafted onto Thursday
		_draft_row("TASK-4", {"start": "2026-10-16"}, base="2026-09-30 00:00:00")  # changed since
		_draft_row("TASK-5", {"start": "2026-10-26", "end": "2026-10-26"})  # drafted out of the week
		_draft_row("TASK-2", {"start": "2026-10-16"}, owner="lisa@example.com")  # somebody else's
		data = api.get_planner("2026-10-11", "2026-10-17", draft="1")

		self.assertEqual(self.calls[0]["exclude"], {"TASK-1", "TASK-3", "TASK-5"})
		cards = {card["name"]: card for card in data["tasks"]}
		self.assertEqual(set(cards), {"TASK-1", "TASK-2", "TASK-3"})
		self.assertTrue(cards["TASK-1"]["drafted"])
		self.assertEqual(
			(cards["TASK-1"]["start"], cards["TASK-1"]["credentials"]), ("2026-10-14", ["Forklift"])
		)
		self.assertNotIn("drafted", cards["TASK-2"])  # Lisa's draft is not Nik's
		self.assertEqual(cards["TASK-3"]["start"], "2026-10-15")
		self.assertEqual([c["name"] for c in data["unscheduled"]], [])  # it left the tray
		self.assertEqual(data["drafts"]["count"], 4)
		self.assertEqual(data["drafts"]["tasks"], ["TASK-1", "TASK-3", "TASK-5"])
		self.assertEqual([s["task"] for s in data["drafts"]["stale"]], ["TASK-4"])
		self.assertIn("someone else", data["drafts"]["stale"][0]["reason"])

	def test_without_draft_the_read_is_what_it_was(self):
		_draft_row("TASK-1", {"start": "2026-10-14", "end": "2026-10-16"})
		data = api.get_planner("2026-10-11", "2026-10-17")
		self.assertEqual(self.calls[0], {"exclude": set(), "extra": []})
		self.assertNotIn("drafts", data)
		self.assertTrue(all("drafted" not in card for card in data["tasks"]))


# ====================================================================== publishing


class TestPublish(_Base):
	def setUp(self):
		super().setUp()
		self.before_after = {"before": {}, "after": {}}
		self.batch_calls = []

		def compute(start, end, resources=None, exclude=(), extra=()):
			which = "after" if exclude else "before"
			self.batch_calls.append((which, set(exclude or ()), [e[0]["name"] for e in extra or ()]))
			return {
				"resources": [{"name": p["name"], "label": p["resource_name"]} for p in PEOPLE],
				"days": self.before_after[which],
			}

		self.notified = []
		self.patches += [
			mock.patch.object(engine, "_compute", compute),
			mock.patch.object(
				notices,
				"send_publish_notices",
				lambda out, publisher: self.notified.append((out, publisher)) or len(out),
			),
		]
		for patch in self.patches[2:]:
			patch.start()
		_task("TASK-1", crew=("RES-1",))
		_task("TASK-2", start="2026-10-13 08:00:00", end="2026-10-13 17:00:00", crew=("RES-2", "RES-3"))
		_draft_row("TASK-1", {"start": "2026-10-19", "end": "2026-10-21"})
		_draft_row("TASK-2", {"start": "2026-10-20", "end": "2026-10-20"})

	def test_a_clean_batch_publishes_every_draft_through_apply_and_tells_each_person_once(self):
		result = api.publish_drafts()
		self.assertEqual([s["name"] for s in frappe.saved], ["TASK-1", "TASK-2"])
		self.assertEqual(
			(frappe.saved[0]["exp_start_date"], frappe.saved[0]["exp_end_date"]),
			("2026-10-19 08:00:00", "2026-10-21 17:00:00"),
		)
		self.assertEqual([p["task"] for p in result["published"]], ["TASK-1", "TASK-2"])
		self.assertEqual(self._drafts("Draft"), [])
		self.assertEqual(len(self._drafts("Published")), 2)
		self.assertEqual(frappe.comments, [])  # no reason was needed
		((sent, publisher),) = self.notified
		self.assertEqual(publisher, "nik@example.com")
		# Austin and Lisa are told; Nik is on TASK-2 too but published it himself.
		self.assertEqual(set(sent), {"austin@example.com", "lisa@example.com"})
		self.assertEqual(
			sent["austin@example.com"]["lines"][0]["text"],
			"Subject TASK-1 moved from Mon Oct 12 – Wed Oct 14 to Mon Oct 19 – Wed Oct 21",
		)
		self.assertEqual(result["notified"], 2)
		self.assertFalse(frappe.flags.planner_publishing)
		self.assertEqual(self.batch_calls[1], ("after", {"TASK-1", "TASK-2"}, ["TASK-1", "TASK-2"]))

	def test_conflicts_across_the_batch_need_one_reason_and_save_nothing_without_it(self):
		self.before_after["after"] = {
			"RES-1": {
				"2026-10-19": {"bookings": [{"ref": "TASK-1", "kind": "task"}], "conflicts": ["Over by 3h"]}
			}
		}
		result = api.publish_drafts()
		self.assertEqual(result["needs_reason"], True)
		self.assertEqual(result["conflicts"], {"Austin Healey": ["2026-10-19: Over by 3h"]})
		self.assertEqual(frappe.saved, [])
		self.assertEqual(len(self._drafts("Draft")), 2)
		self.assertEqual(self.notified, [])

		result = api.publish_drafts(reason="Customer moved the date")
		self.assertEqual(len(frappe.saved), 2)
		self.assertEqual({r["reason"] for r in self._drafts("Published")}, {"Customer moved the date"})
		self.assertEqual(result["conflicts"], {"Austin Healey": ["2026-10-19: Over by 3h"]})

	def test_a_task_changed_since_it_was_drafted_is_skipped_and_reported(self):
		frappe.docs[("Task", "TASK-2")]["modified"] = "2026-10-08 09:30:00"
		result = api.publish_drafts()
		self.assertEqual([s["name"] for s in frappe.saved], ["TASK-1"])
		self.assertEqual(result["skipped"][0]["task"], "TASK-2")
		self.assertIn("Changed by someone else", result["skipped"][0]["reason"])
		self.assertEqual([r["task"] for r in self._drafts("Draft")], ["TASK-2"])

	def test_a_task_that_only_clashes_mid_batch_is_retried_without_a_reason(self):
		# TASK-1's new days clash only while TASK-2 still holds its old place.
		def hook(task, crew, span):
			if task.get("name") == "TASK-1" and span and span[0] == D(2026, 10, 19):
				if not any(s["name"] == "TASK-2" for s in frappe.saved):
					return {"Austin Healey": ["2026-10-19: Over by 1h"]}
			return {}

		self.conflicts["hook"] = hook
		result = api.publish_drafts()
		self.assertEqual([s["name"] for s in frappe.saved], ["TASK-2", "TASK-1"])
		self.assertEqual(frappe.comments, [])
		self.assertEqual(len(result["published"]), 2)

	def test_a_refused_save_is_reported_and_the_rest_still_publish(self):
		frappe.docs[("Task", "TASK-2")]["fail_save"] = True
		result = api.publish_drafts()
		self.assertEqual([s["name"] for s in frappe.saved], ["TASK-1"])
		self.assertEqual(result["failed"][0]["task"], "TASK-2")
		self.assertIn("cannot leave", result["failed"][0]["reason"])
		self.assertIn(("rollback", "planner_publish"), frappe.db_calls)
		self.assertEqual([r["task"] for r in self._drafts("Draft")], ["TASK-2"])

	def test_discard_marks_rows_and_never_deletes(self):
		self.assertEqual(api.discard_drafts(tasks=json.dumps(["TASK-2"])), {"discarded": 1})
		self.assertEqual([r["task"] for r in self._drafts("Discarded")], ["TASK-2"])
		self.assertEqual(api.discard_drafts(), {"discarded": 1})
		self.assertEqual(len(frappe.tables["Planner Draft Change"]), 2)
		self.assertEqual(frappe.saved, [])

	def test_nothing_to_publish(self):
		frappe.tables["Planner Draft Change"] = []
		self.assertEqual(api.publish_drafts()["published"], [])

	def test_publish_and_discard_need_the_planner_gate(self):
		frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			api.publish_drafts()
		with self.assertRaises(_PermissionError):
			api.discard_drafts()


class TestPublishNotices(_Base):
	def test_bell_and_email_now_texts_after_commit(self):
		_settings(change_alerts=0)
		calls = []
		with (
			mock.patch.object(notices, "_bell", lambda *a: calls.append(("bell", a))),
			mock.patch.object(notices, "_email", lambda *a: calls.append(("email", a))),
		):
			told = notices.send_publish_notices(
				{
					"austin@example.com": {
						"lines": [
							{"text": "Pump set moved from Thu Oct 15 to Fri Oct 16", "day": "2026-10-15"}
						],
						"urgent": [],
						"task": "TASK-1",
						"day": "2026-10-15",
					},
					"nobody@example.com": {"lines": [], "urgent": []},
				},
				"nik@example.com",
			)
		self.assertEqual(told, 1)
		self.assertEqual([c[0] for c in calls], ["bell", "email"])
		self.assertIn("Nik Bradshaw published changes", calls[1][1][2])
		((method, kwargs),) = frappe.enqueued
		self.assertEqual(method, notices.TEXT_JOB)
		self.assertTrue(kwargs["enqueue_after_commit"])
		self.assertIn("Pump set moved", kwargs["texts"]["austin@example.com"])

	def test_an_urgent_change_heads_the_notice_with_the_alert(self):
		subjects = []
		with (
			mock.patch.object(notices, "_bell", lambda user, subject, *a: subjects.append(subject)),
			mock.patch.object(notices, "_email", lambda *a: None),
		):
			notices.send_publish_notices(
				{
					"austin@example.com": {
						"lines": [{"text": "x", "day": "2026-10-09"}],
						"urgent": [{"text": "Pump set moved to Monday", "day": "2026-10-09"}],
						"task": "TASK-1",
						"day": "2026-10-09",
					}
				},
				"nik@example.com",
			)
		self.assertEqual(subjects, ["Your Friday changed: Pump set moved to Monday"])

	def test_the_email_goes_through_the_shell(self):
		frappe.values[("User", "austin@example.com", "email")] = "austin@example.com"
		notices._email("austin@example.com", "Subj", "Intro", ["a line"], "https://erp.example.com/x", "Open")
		self.assertEqual(frappe.sent[0]["recipients"], ["austin@example.com"])
		self.assertTrue(frappe.sent[0]["message"].startswith("<shell>"))
		self.assertIn("wrap", [c[0] for c in email_style.calls])


# ====================================================================== change alerts


class TestChangeAlerts(_Base):
	def _changed(self, before_start="2026-10-09 08:00:00", after_start="2026-10-12 08:00:00", **values):
		before = _task("TASK-1", start=before_start, end=before_start)
		doc = _task("TASK-1", start=after_start, end=after_start, **values)
		doc["_before"] = before
		return doc

	def test_off_by_default(self):
		notices.queue_task_change(self._changed())
		self.assertIsNone(getattr(frappe.local, notices.LOCAL_KEY, None))
		self.assertEqual(frappe.db.after_commit.functions, [])

	def test_one_alert_per_person_after_commit(self):
		_settings(change_alerts=1)
		doc = self._changed()
		notices.queue_task_change(doc)
		# A second save of the same task in the same request: measured from the first "before".
		doc2 = _task("TASK-1", start="2026-10-13 08:00:00", end="2026-10-13 08:00:00")
		doc2["_before"] = doc
		notices.queue_task_change(doc2)
		self.assertEqual(frappe.enqueued, [])  # nothing before the commit
		self.assertEqual(len(frappe.db.after_commit.functions), 1)
		frappe.db.after_commit.run()
		((method, kwargs),) = frappe.enqueued
		self.assertEqual(method, notices.ALERT_JOB)
		self.assertEqual(
			kwargs["alerts"],
			{
				"austin@example.com": [
					{"task": "TASK-1", "text": "Subject TASK-1 moved to Tuesday", "day": "2026-10-09"}
				]
			},
		)

	def test_a_rollback_alerts_nobody(self):
		_settings(change_alerts=1)
		notices.queue_task_change(self._changed())
		frappe.db.after_rollback.run()
		frappe.db.after_commit.functions.clear()
		self.assertIsNone(getattr(frappe.local, notices.LOCAL_KEY, None))

	def test_nothing_outside_the_next_48_hours(self):
		_settings(change_alerts=1)
		notices.queue_task_change(self._changed("2026-10-12 08:00:00", "2026-10-13 08:00:00"))
		frappe.db.after_commit.run()
		self.assertEqual(frappe.enqueued, [])

	def test_skipped_during_a_publish_a_migrate_and_for_finished_tasks(self):
		_settings(change_alerts=1)
		frappe.flags.planner_publishing = True
		notices.queue_task_change(self._changed())
		frappe.flags = types.SimpleNamespace(in_migrate=True)
		notices.queue_task_change(self._changed())
		frappe.flags = types.SimpleNamespace()
		notices.queue_task_change(self._changed(status="Completed"))
		self.assertEqual(frappe.db.after_commit.functions, [])

	def test_never_raises_into_a_save(self):
		_settings(change_alerts=1)
		doc = self._changed()
		doc["_before"] = "boom"
		notices.queue_task_change(doc)
		self.assertEqual(len(frappe.errors), 1)
		self.assertEqual(frappe.errors[0][1]["title"], "Project Planner: change alert not queued")

	def test_the_job_texts_and_falls_back_to_email(self):
		frappe.values[("Employee", (("user_id", "austin@example.com"),), "cell_number")] = "+18015550100"
		bells, emails = [], []
		with (
			mock.patch.object(notices, "_bell", lambda *a: bells.append(a)),
			mock.patch.object(notices, "_email", lambda *a: emails.append(a)),
			mock.patch.object(notices, "route_url", lambda user, day: f"https://x/{user}/{day}"),
		):
			sent_sms = []
			with mock.patch.dict(
				sys.modules,
				{
					"erpnext_enhancements.api.telephony": types.SimpleNamespace(
						send_system_sms=lambda n, m: sent_sms.append((n, m))
					)
				},
			):
				notices.send_change_alerts(
					{
						"austin@example.com": [
							{"task": "T-1", "text": "Pump set moved to Monday", "day": "2026-10-09"}
						],
						"lisa@example.com": [
							{"task": "T-1", "text": "You're off Pump set (Friday)", "day": "2026-10-09"}
						],
					}
				)
		self.assertEqual(len(bells), 2)
		self.assertEqual(sent_sms[0][0], "+18015550100")
		self.assertIn("Your Friday changed: Pump set moved to Monday", sent_sms[0][1])
		self.assertEqual([e[0] for e in emails], ["lisa@example.com"])  # no cell number: email instead


# ====================================================================== the 6 AM digest


class TestDigest(_Base):
	def setUp(self):
		super().setUp()
		frappe.tables["User"] = [
			{"name": "austin@example.com", "enabled": 1},
			{"name": "lisa@example.com", "enabled": 1},
			{"name": "nik@example.com", "enabled": 0},
		]
		self.entries = {
			"RES-1": [
				{
					"date": str(TODAY),
					"travel": None,
					"items": [
						{
							"label": "Pump set",
							"project_title": "Highlands",
							"time": "08:00",
							"address": "1 Main St",
							"crew": [],
							"hours": 3,
						}
					],
				}
			],
			"RES-2": [{"date": str(TODAY), "travel": None, "items": []}],
		}
		people_days = lambda start, end, resources=None: (  # noqa: E731
			{
				"resources": [
					{"name": p["name"], "label": p["resource_name"]}
					for p in PEOPLE
					if p["name"] in (resources or [])
				]
			},
			{name: self.entries.get(name, []) for name in resources or []},
		)
		self.texts = []
		self.patches += [
			mock.patch.object(api, "_people_days", people_days),
			mock.patch.object(
				notices, "_text", lambda user, message: self.texts.append((user, message)) or True
			),
		]
		for patch in self.patches[2:]:
			patch.start()

	def test_both_switches_default_off(self):
		self.assertEqual(digest.covered_users(), set())
		digest.send_daily_digests()
		self.assertEqual(frappe.sent, [])
		self.assertEqual(self.texts, [])

	def test_covered_users_are_the_enabled_planner_people(self):
		_settings(combined_morning_digest=1)
		self.assertEqual(digest.covered_users(), {"austin@example.com", "lisa@example.com"})

	def test_claim_then_send_once_and_skip_an_empty_day(self):
		_settings(combined_morning_digest=1)
		digest.send_daily_digests()
		(log,) = frappe.tables["Planner Digest Log"]
		self.assertEqual(
			(log["log_key"], log["user"], log["bookings"]),
			("austin@example.com|2026-10-08", "austin@example.com", 1),
		)
		self.assertEqual([m["recipients"] for m in frappe.sent], [["austin@example.com"]])  # Lisa had nothing
		seq = frappe.sequence
		self.assertLess(seq.index("insert:Planner Digest Log"), seq.index("commit"))
		self.assertLess(seq.index("commit"), seq.index("sendmail"))
		self.assertEqual(self.texts[0][0], "austin@example.com")
		self.assertIn("route/RES-1/2026-10-08", self.texts[0][1])
		self.assertTrue(frappe.sent[0]["message"].startswith("<shell>"))
		self.assertIn(
			(
				"set_value",
				("Planner Digest Log", "austin@example.com|2026-10-08", "channels", "text, email"),
				{"update_modified": False},
			),
			frappe.db_calls,
		)

		# A second run the same morning (a catch-up, a re-run after a deploy) sends nothing.
		digest.send_daily_digests()
		self.assertEqual(len(frappe.sent), 1)
		self.assertEqual(len(self.texts), 1)

	def test_a_claim_that_fails_sends_nothing(self):
		_settings(combined_morning_digest=1)
		frappe.fail_insert["Planner Digest Log"] = RuntimeError("Duplicate entry")
		digest.send_daily_digests()
		self.assertEqual(frappe.sent, [])
		self.assertEqual(self.texts, [])
		self.assertIn(("rollback", None), frappe.db_calls)

	def test_the_preview_emails_only_the_caller_and_records_nothing(self):
		frappe.session.user = "austin@example.com"
		result = digest.send_preview("austin@example.com", TODAY)
		self.assertTrue(result["sent"])
		self.assertEqual(frappe.sent[0]["recipients"], ["austin@example.com"])
		self.assertTrue(frappe.sent[0]["subject"].startswith("Preview: "))
		self.assertEqual(self.texts, [])
		self.assertEqual(frappe.tables["Planner Digest Log"], [])
		self.assertIn("code", [c[0] for c in email_style.calls])  # the text it would send

	def test_the_preview_explains_an_empty_day_and_a_missing_person(self):
		self.assertFalse(digest.send_preview("lisa@example.com", TODAY)["sent"])
		self.assertFalse(digest.send_preview("stranger@example.com", TODAY)["sent"])
		self.assertEqual(frappe.sent, [])

	def test_the_preview_endpoint_is_for_managers(self):
		frappe.roles = ["Projects User"]
		with self.assertRaises(_PermissionError):
			api.send_digest_preview()
		frappe.roles = ["Projects Manager"]
		frappe.session.user = "austin@example.com"
		self.assertTrue(api.send_digest_preview()["sent"])


class TestOlderDigestsSkipCoveredPeople(unittest.TestCase):
	def _loop(self, path, start_marker):
		source = (APP / path).read_text(encoding="utf-8")
		return source, source[source.index(start_marker) :]

	def test_the_technician_digest_skips_before_it_stamps(self):
		source, body = self._loop("api/maintenance_dispatch.py", "covered = _combined_digest_covers()")
		loop = body[: body.index("_send_tech_digest(")]
		self.assertLess(loop.index("if person in covered:"), loop.index("_stamp_digest(stamp, today)"))
		self.assertIn(
			"from erpnext_enhancements.project_enhancements.planner_digest import covered_users", source
		)

	def test_the_rental_digest_skips_covered_people(self):
		source, body = self._loop(
			"asset_management/rental_logistics.py", "covered = _combined_digest_covers()"
		)
		loop = body[: body.index("_send_digest(user, items, today)")]
		self.assertIn("if user in covered:", loop)
		self.assertIn(
			"from erpnext_enhancements.project_enhancements.planner_digest import covered_users", source
		)

	def test_the_coverage_helpers_never_raise(self):
		for path in ("api/maintenance_dispatch.py", "asset_management/rental_logistics.py"):
			source = (APP / path).read_text(encoding="utf-8")
			helper = source[source.index("def _combined_digest_covers():") :]
			helper = helper[: helper.index("\ndef ", 10)]
			self.assertIn("except Exception:", helper, path)
			self.assertIn("return set()", helper, path)


# ====================================================================== My week and the crew sheet


class TestMyWeek(_Base):
	def test_no_planner_resource_is_a_friendly_message(self):
		frappe.session.user = "stranger@example.com"
		answer = api.get_my_week("2026-10-14")
		self.assertIsNone(answer["resource"])
		self.assertIn("not on the planner's list", answer["message"])
		self.assertEqual((answer["start"], answer["end"]), ("2026-10-11", "2026-10-17"))

	def test_the_week_of_the_signed_in_person(self):
		frappe.session.user = "austin@example.com"
		frappe.roles = ["Maintenance User"]  # technicians can open it
		seen = []

		def people_days(start, end, resources=None):
			seen.append((start, end, resources))
			return {"resources": [{"name": "RES-1", "label": "Austin Healey"}]}, {
				"RES-1": [{"date": "2026-10-11", "items": []}]
			}

		with mock.patch.object(api, "_people_days", people_days):
			answer = api.get_my_week("2026-10-14")
		self.assertEqual(seen, [(D(2026, 10, 11), D(2026, 10, 17), ["RES-1"])])
		self.assertEqual((answer["resource"], answer["label"]), ("RES-1", "Austin Healey"))
		self.assertEqual(answer["days"], [{"date": "2026-10-11", "items": []}])

	def test_it_needs_a_planner_role(self):
		frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			api.get_my_week()


class TestCrewSheet(_Base):
	def _rows(self):
		def day(i, items=(), off=None, travel=None):
			return {
				"date": str(D(2026, 10, 11) + datetime.timedelta(days=i)),
				"items": list(items),
				"off": off,
				"travel": travel,
			}

		busy = [day(i) for i in range(7)]
		busy[1] = day(
			1,
			[
				{
					"label": "<script>alert(1)</script>",
					"project_title": "Highlands & Co",
					"slot": ["08:00", "10:00"],
					"arrive": None,
					"hours": 2,
					"address": "1 Main St",
					"crew": ["Lisa Park"],
				}
			],
		)
		busy[2] = day(2, off="Time off")
		busy[3] = day(3, travel="Travel: Vegas")
		return [
			{"label": "Austin Healey", "days": busy},
			{"label": "Idle Ian", "days": [day(i) for i in range(7)]},
		]

	def test_the_document_is_landscape_escaped_and_on_the_print_chrome(self):
		html = api.crew_sheet_document(D(2026, 10, 11), self._rows(), group="Field", printed_on="Oct 8")
		self.assertIn("@page { size: letter landscape", html)
		self.assertNotIn("<script>alert", html)
		self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
		self.assertIn("Highlands &amp; Co", html)
		self.assertIn("08:00–10:00 · 2h", html)
		self.assertIn("with Lisa Park", html)
		self.assertIn("Time off", html)
		self.assertIn("Travel: Vegas", html)
		self.assertEqual(html.count("<th "), 8)  # the person column and seven days
		self.assertIn("Sun Oct 11", html)
		self.assertIn("Nothing booked all week: Idle Ian", html)
		self.assertIn("height:12px", html)  # print_style's top stripe
		self.assertIn("Week of Oct 11 – 17, 2026", html)
		self.assertTrue(html.startswith("<!doctype html>"))

	def test_the_endpoint_normalizes_to_the_week_and_honors_the_group(self):
		seen = []

		def people_days(start, end, resources=None):
			seen.append((start, end, resources))
			return {"resources": []}, {}

		frappe.tables["Planner Resource"] = [dict(p, resource_group="Field") for p in PEOPLE[:2]] + [
			dict(PEOPLE[2], resource_group="PM")
		]
		with mock.patch.object(api, "_people_days", people_days):
			html = api.crew_sheet_html("2026-10-14", group="PM")
		self.assertEqual(seen, [(D(2026, 10, 11), D(2026, 10, 17), ["RES-3"])])
		self.assertIn("Nothing is booked on anyone this week.", html)

	def test_it_needs_a_planner_role(self):
		frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			api.crew_sheet_html("2026-10-14")


# ====================================================================== wiring


def _whitelisted(path):
	out = {}
	for node in ast.parse(path.read_text(encoding="utf-8")).body:
		if not isinstance(node, ast.FunctionDef):
			continue
		for decorator in node.decorator_list:
			target = decorator.func if isinstance(decorator, ast.Call) else decorator
			if isinstance(target, ast.Attribute) and target.attr == "whitelist":
				methods = None
				if isinstance(decorator, ast.Call):
					for keyword in decorator.keywords:
						if keyword.arg == "methods":
							methods = ast.literal_eval(keyword.value)
				out[node.name] = methods
	return out


def _functions(path):
	return {
		n.name: n for n in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)
	}


class TestWiring(unittest.TestCase):
	def test_writes_are_post_and_reads_are_not(self):
		endpoints = _whitelisted(API_PATH)
		for name in ("publish_drafts", "discard_drafts", "send_digest_preview"):
			self.assertEqual(endpoints[name], ["POST"], name)
		for name in ("get_my_week", "crew_sheet_html"):
			self.assertIsNone(endpoints[name], name)
		self.assertEqual(_whitelisted(NOTICES_PATH), {})
		self.assertEqual(_whitelisted(DIGEST_PATH), {})

	def test_the_draft_argument_defaults_off(self):
		functions = _functions(API_PATH)
		for name in ("get_planner", "save_task", "add_crew", "swap_crew"):
			args = functions[name].args
			self.assertEqual(args.args[-1].arg, "draft", name)
			self.assertEqual(ast.literal_eval(args.defaults[-1]), 0, name)

	def test_hooks_register_the_digest_and_the_alert(self):
		tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
		values = {}
		for node in tree.body:
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in (
				"scheduler_events",
				"doc_events",
			):
				values[node.targets[0].id] = ast.literal_eval(node.value)
		six = values["scheduler_events"]["cron"]["0 6 * * *"]
		self.assertEqual(
			six.count("erpnext_enhancements.project_enhancements.planner_digest.send_daily_digests"), 1
		)
		self.assertLess(
			six.index("erpnext_enhancements.asset_management.rental_logistics.send_crew_digests"),
			six.index("erpnext_enhancements.project_enhancements.planner_digest.send_daily_digests"),
		)
		on_update = values["doc_events"]["Task"]["on_update"]
		self.assertIn(
			"erpnext_enhancements.project_enhancements.planner_notices.queue_task_change", on_update
		)
		self.assertEqual(on_update[-1], "erpnext_enhancements.project_enhancements.crew_sync.on_task_update")

	def test_the_settings_switches_default_off_and_get_their_rows(self):
		data = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
		fields = {f["fieldname"]: f for f in data["fields"]}
		for name in ("combined_morning_digest", "change_alerts"):
			self.assertEqual((fields[name]["fieldtype"], fields[name]["default"]), ("Check", "0"), name)
			self.assertIn(name, data["field_order"])
		self.assertEqual(data["field_order"], [f["fieldname"] for f in data["fields"]])
		patches = (APP / "patches.txt").read_text(encoding="utf-8")
		post = patches.split("[post_model_sync]", 1)[1].splitlines()
		self.assertIn("erpnext_enhancements.patches.materialize_planner_notice_settings", post)
		source = PATCH.read_text(encoding="utf-8")
		self.assertIn("materialize_defaults()", source)
		self.assertNotIn("Singles", source.split('"""', 2)[2])

	def test_the_preview_button(self):
		js = SETTINGS_JS.read_text(encoding="utf-8")
		self.assertIn("erpnext_enhancements.api.project_planner.send_digest_preview", js)
		self.assertIn('has_role(["System Manager", "Projects Manager"])', js)
		self.assertIn("frappe.utils.escape_html(result.message", js)

	def test_the_new_doctypes(self):
		draft = json.loads(
			(DOCTYPES / "planner_draft_change/planner_draft_change.json").read_text(encoding="utf-8")
		)
		log = json.loads(
			(DOCTYPES / "planner_digest_log/planner_digest_log.json").read_text(encoding="utf-8")
		)
		for data in (draft, log):
			self.assertEqual((data["module"], data["custom"]), ("Project Enhancements", 0))
			self.assertEqual(data["field_order"], [f["fieldname"] for f in data["fields"]])
			self.assertEqual(data.get("in_create"), 1)
		fields = {f["fieldname"]: f for f in draft["fields"]}
		self.assertEqual(fields["status"]["options"], "Draft\nPublished\nDiscarded")
		self.assertEqual((fields["task"]["options"], fields["task"]["reqd"]), ("Task", 1))
		roles = {p["role"]: p for p in draft["permissions"]}
		self.assertEqual(set(roles), {"System Manager", "Projects Manager", "Projects User"})
		for role in ("Projects Manager", "Projects User"):
			self.assertEqual(roles[role].get("if_owner"), 1, role)
			self.assertTrue(roles[role]["create"] and roles[role]["read"], role)
		self.assertEqual(log["autoname"], "field:log_key")
		self.assertEqual({f["fieldname"]: f.get("unique") for f in log["fields"]}["log_key"], 1)
		self.assertNotIn("Projects User", {p["role"] for p in log["permissions"]})

	def test_new_modules_log_with_keywords_and_never_use_redis_for_the_stamp(self):
		for path in (API_PATH, NOTICES_PATH, DIGEST_PATH):
			tree = ast.parse(path.read_text(encoding="utf-8"))
			for node in ast.walk(tree):
				if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "log_error":
					self.assertEqual(node.args, [], path.name)
				if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "get_all":
					for keyword in node.keywords:
						if keyword.arg == "fields":
							for value in ast.walk(keyword.value):
								if isinstance(value, ast.Constant) and isinstance(value.value, str):
									self.assertNotIn("(", value.value, path.name)
		code = DIGEST_PATH.read_text(encoding="utf-8").split('"""', 2)[2]
		self.assertNotIn("frappe.cache", code)
		self.assertNotIn("get_password", NOTICES_PATH.read_text(encoding="utf-8"))

	def test_every_email_goes_through_the_shell(self):
		for path in (NOTICES_PATH, DIGEST_PATH):
			source = path.read_text(encoding="utf-8")
			self.assertIn("email_style.wrap(", source, path.name)
			self.assertNotIn("max-width", source, path.name)

	def test_the_new_doctypes_have_their_controllers(self):
		for scrub, cls in (
			("planner_draft_change", "PlannerDraftChange"),
			("planner_digest_log", "PlannerDigestLog"),
		):
			tree = ast.parse((DOCTYPES / scrub / f"{scrub}.py").read_text(encoding="utf-8"))
			self.assertIn(cls, {n.name for n in tree.body if isinstance(n, ast.ClassDef)})
			self.assertTrue((DOCTYPES / scrub / "__init__.py").exists())


class TestPage(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		code = PAGE_JS.read_text(encoding="utf-8")
		cls.code = code
		cls.block = code[
			code.index("// ====================================================================== Phase 3B") :
		]
		bare = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
		cls.bare = "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in bare.splitlines())

	def test_the_class_hooks_are_one_line_each(self):
		for line in (
			"this.init_phase3b();",
			"const mine = this.my_week_target(route);",
			"this.show_my_week(target.anchor);",
			"this.draft_args())",
			"args = this.with_draft(method, args);",
			"this.render_phase3b();",
			"Object.assign(ProjectPlanner.prototype, PP3B_METHODS);",
		):
			self.assertIn(line, self.code, line)

	def test_draft_mode_sends_draft_on_every_write_and_the_read(self):
		self.assertIn('draft_methods: ["save_task", "add_crew", "swap_crew"]', self.block)
		self.assertIn("{ draft: 1 }", self.block)
		# Remembered through the page's guarded prefs, never a new localStorage call.
		self.assertIn("this.load_pref(PP3B.pref", self.block)
		self.assertIn("this.save_pref(PP3B.pref", self.block)
		self.assertNotIn("localStorage", self.block)

	def test_the_draft_bar_and_card_marks(self):
		for text in (
			"unpublished change(s)",
			"Publish",
			"Discard",
			"pp-drafted",
			"pp-draft-chip",
			"Draft mode",
		):
			self.assertIn(text, self.block, text)
		self.assertIn("${PP.api}.publish_drafts", self.block)
		self.assertIn("${PP.api}.discard_drafts", self.block)
		self.assertIn("this.ask_reason(result.conflicts", self.block)

	def test_my_week_is_a_route(self):
		self.assertIn('my_week: "my-week"', self.block)
		self.assertIn("frappe.set_route(PP.route, PP3B.my_week, ymd)", self.block)
		self.assertIn("${PP.api}.get_my_week", self.block)
		self.assertIn("this.go_route(data.resource, entry.date)", self.block)
		self.assertRegex(self.block, r"step = \(sign\) => this\.go_my_week\(")
		for forbidden in ("pushState", "replaceState", "window.location", "location.href", "history."):
			self.assertNotIn(forbidden, self.bare)

	def test_my_week_links_only_to_google_maps_without_an_opener(self):
		self.assertIn(r"/^https:\/\/www\.google\.com\/maps\//.test(stop.maps_url)", self.block)
		self.assertIn('rel="noopener noreferrer"', self.block)
		for field in ("stop.address", "stop.label", "stop.notes", "when", "maps", "facts"):
			self.assertIn(f"pp_esc({field}", self.block, field)

	def test_the_crew_sheet_prints_in_a_browser_window(self):
		block = self.block[self.block.index("print_crew_sheet() {") :]
		self.assertLess(block.index('window.open("", "_blank")'), block.index("frappe.call("))
		self.assertIn("win.print()", block)
		self.assertIn("${PP.api}.crew_sheet_html", block)
		for forbidden in ("download_pdf", "print_format", "/api/method/frappe.utils.print"):
			self.assertNotIn(forbidden, self.block)

	def test_styles_are_namespaced(self):
		style = self.code[
			self.code.index("const PP3B_STYLE = `") : self.code.index(
				"`;", self.code.index("const PP3B_STYLE = `")
			)
		]
		classes = set(re.findall(r"\.([a-zA-Z][\w-]*)", style))
		self.assertTrue(classes)
		self.assertEqual({c for c in classes if not c.startswith("pp-")}, set())
		self.assertIn("id='pp-style-3b'", self.block)

	def test_no_record_field_is_interpolated_raw(self):
		raw = [
			line.strip()
			for line in self.block.splitlines()
			if "<" in line and re.search(r"\$\{(?:stop|entry|data|result|card|item)\.\w+", line)
		]
		self.assertEqual(raw, [])


if __name__ == "__main__":
	unittest.main()
