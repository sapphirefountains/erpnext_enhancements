# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Planner Phase 6B (TASK-2026-02468): faster scheduling on both planners.

Nik picked four things on 2026-10-09: a right-click menu (assign to, move to the next free day,
pencil / firm, duplicate, split), drag-to-resize with double-click quick add, moving several tasks
at once, and search with keyboard shortcuts. What this pins, because each part can fail quietly:

* **Nobody is picked for anyone.** "Suggest a crew" was declined: Assign to... is a list of who is
  free that the planner clicks, a duplicate keeps the crew it copied, a quick add books only the
  person named, a split copies the crew to both halves. The page has no code path that adds a person
  without a click on a ``data-p6b-pick`` row.
* **Every write goes through the page's send().** So the reason prompt (``needs_reason``), draft
  mode and Undo apply to the new writes too; the ``planner_actions`` endpoints are reached through
  ``this.p6_send_modules``, never a side door.
* **The new endpoints keep the planner's rules.** Overbooking answers ``needs_reason`` and writes
  nothing; a new task cannot be a draft (refused, with a sentence); a rental crew task is refused;
  a split refuses a task with a timesheet or clocked time, shares hours by working days, makes the
  second half depend on the first, and is all or nothing inside a savepoint; a multi-task move is
  one conflict check and one reason; the Undo of a new task deletes it only when nothing has been
  attached since, and never with ``force``; search is customer jobs only and capped at 20.
* **The pages' pure helpers** (the day-offset math, the shortcut table, the selection set, the
  menu's provider groups, local search) run under node, the Project Planner's and the Maintenance
  Planner's copies through the same cases, and the split weights agree with the server's.

Bench-free: installs its own ``frappe`` stub, runs the real modules, puts ``sys.modules`` back.
The node parts skip when node is not on PATH.

Run: python -m unittest erpnext_enhancements.tests.test_planner_phase6b
"""

import ast
import datetime
import importlib
import json
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
ACTIONS = APP / "api/planner_actions.py"
PP_JS = APP / "project_enhancements/page/project_planner/project_planner.js"
MP_JS = APP / "sapphire_maintenance/page/maintenance_planner/maintenance_planner.js"
CI = REPO_ROOT / ".github/workflows/ci.yml"
API_README = APP / "api/README.md"
PE_README = APP / "project_enhancements/README.md"
SM_README = APP / "sapphire_maintenance/README.md"

D = datetime.date
TODAY = D(2026, 10, 9)  # a Friday
NOW = datetime.datetime(2026, 10, 9, 7, 0, 0)
MON, TUE, WED, THU, FRI, SAT, SUN = (D(2026, 10, 12) + datetime.timedelta(days=i) for i in range(7))
MODIFIED = "2026-10-08 09:00:00"
SAVED = "2026-10-09 10:00:00"

# The planner_actions methods the pages call and every argument they send. The Project Planner's own
# endpoints are test_project_planner_page.API_CONTRACT; these live in their own module.
ACTIONS_CONTRACT = {
	"duplicate_task": {"task", "date", "reason", "draft"},
	"split_task": {"task", "split_date", "modified", "reason", "draft"},
	"quick_add_task": {"project", "subject", "date", "hours", "resource", "tentative", "reason", "draft"},
	"move_many": {"moves", "reason", "draft"},
	"remove_created_task": {"task", "modified", "restore"},
	"search_planner": {"q", "start", "planner"},
}
ACTIONS_WRITES = {name for name in ACTIONS_CONTRACT if name != "search_planner"}
PHASE_MARKER = "// ====================================================================== Phase 6B"

_saved_modules = {}
_modules_before = set()
frappe = None
engine = None
api = None
mp = None
actions = None


class _Throw(Exception):
	pass


class _PermissionError(Exception):
	pass


class _Doc(dict):
	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None

	def __setattr__(self, name, value):
		self[name] = value


class _TaskDoc(_Doc):
	"""Enough of a frappe Document for the writes: permission checks, child tables, save, insert."""

	def check_permission(self, ptype):
		frappe.events.append(("check_permission", ptype, self.get("name")))
		if self.get("name") in frappe.denied:
			raise _PermissionError(f"No permission for {self.get('name')}")

	def set(self, field, value):
		self[field] = [_Doc(row) for row in value] if isinstance(value, list) else value

	def append(self, field, row):
		self.setdefault(field, []).append(_Doc(row))

	def save(self):
		if self.get("name") in frappe.failing:
			raise _Throw(f"Cannot save {self.get('name')}")
		frappe.saved.append(json.loads(json.dumps(dict(self), default=str)))
		frappe.events.append(("save", self.get("name")))
		self["modified"] = SAVED

	def insert(self):
		if frappe.fail_insert:
			raise _Throw("Insert refused")
		doctype = self.get("doctype") or "Task"
		frappe.counter += 1
		self["name"] = f"{'TASK-NEW' if doctype == 'Task' else 'DRAFT'}-{frappe.counter}"
		self["modified"] = SAVED
		self["owner"] = frappe.session.user
		frappe.inserted.append(json.loads(json.dumps(dict(self), default=str)))
		frappe.events.append(("insert", doctype, self["name"]))
		frappe.docs[(doctype, self["name"])] = self
		return self

	def add_comment(self, kind, text):
		frappe.comments.append((self.get("name"), kind, text))


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


def setUpModule():
	global frappe, engine, api, mp, actions
	_modules_before.update(sys.modules)
	for name in list(sys.modules):
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements")):
			_saved_modules[name] = sys.modules.pop(name)

	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.getdate = _getdate
	utils.add_days = lambda value, days: _getdate(value) + datetime.timedelta(days=days)
	utils.date_diff = lambda a, b: (_getdate(a) - _getdate(b)).days
	utils.formatdate = lambda value=None, *a, **k: _getdate(value).strftime("%m-%d-%Y")
	utils.nowdate = lambda: str(TODAY)
	utils.now_datetime = lambda: NOW
	utils.flt = lambda value, precision=None: float(value or 0)
	utils.cint = lambda value: int(float(value or 0))
	utils.strip_html_tags = lambda text: re.sub(r"<[^>]+>", "", str(text))
	utils.escape_html = lambda text: (
		str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
	)
	utils.get_url = lambda path="": "https://erp.example.com" + (path or "")
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	frappe.throw = _throw
	frappe.PermissionError = _PermissionError
	frappe.get_traceback = lambda: "Traceback: boom"
	frappe.parse_json = json.loads
	sys.modules.update({"frappe": frappe, "frappe.utils": utils})
	_reset()

	engine = importlib.import_module("erpnext_enhancements.project_enhancements.crew_availability")
	mp = importlib.import_module("erpnext_enhancements.api.maintenance_planner")
	api = importlib.import_module("erpnext_enhancements.api.project_planner")
	actions = importlib.import_module("erpnext_enhancements.api.planner_actions")


def tearDownModule():
	for name in set(sys.modules) - _modules_before:
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements")):
			sys.modules.pop(name, None)
	for name in list(sys.modules):
		if name == "frappe" or name.startswith("frappe."):
			sys.modules.pop(name, None)
	sys.modules.update(_saved_modules)


# ---------------------------------------------------------------------- the fake site


def _matches(row, filters):
	for key, wanted in (filters or {}).items():
		value = row.get(key)
		if isinstance(wanted, list | tuple) and len(wanted) == 2 and isinstance(wanted[0], str):
			op, operand = wanted
			if op == "in" and value not in operand:
				return False
			if op == "not in" and value in operand:
				return False
			if op == "!=" and value == operand:
				return False
			if op == "<" and not (value is not None and value < operand):
				return False
			if op == "like":
				needle = str(operand).strip("%").replace("\\%", "%").replace("\\_", "_").replace("\\\\", "\\")
				if needle.lower() not in str(value or "").lower():
					return False
			continue
		if value != wanted:
			return False
	return True


def _rows(doctype, filters=None):
	rows = frappe.tables.get(doctype, [])
	rows = rows(filters) if callable(rows) else rows
	return [_Doc(r) for r in rows if _matches(r, filters)]


def _get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
	frappe.queries.append(("get_all", doctype, filters))
	rows = _rows(doctype, filters)
	limit = kwargs.get("limit_page_length")
	if limit:
		rows = rows[:limit]
	if pluck:
		return [r.get(pluck) for r in rows]
	return rows


def _exists(doctype, name=None):
	if doctype == "DocType":
		return name in frappe.installed
	if isinstance(name, dict):
		return bool(_rows(doctype, name))
	return (doctype, name) in frappe.docs or any(r.get("name") == name for r in _rows(doctype))


def _get_value(doctype, name, field=None, *args, **kwargs):
	return frappe.values.get((doctype, name, field))


def _get_doc(doctype, name=None):
	if isinstance(doctype, dict):
		return _TaskDoc(doctype)
	if (doctype, name) not in frappe.docs:
		raise _Throw(f"{doctype} {name} not found")
	return frappe.docs[(doctype, name)]


def _sql(query, values=None, as_dict=False, **kwargs):
	frappe.queries.append(("sql", query, values))
	if "`tabProject` proj" in query:
		return [_Doc(r) for r in frappe.jobs]
	for marker in (
		"tabSapphire Maintenance Contract",
		"tabSapphire Maintenance Record",
		"FROM `tabTask` t",
		"FROM `tabProject` p",
	):
		if marker in query:
			return [_Doc(r) for r in frappe.sql_rows.get(marker, [])]
	return []


def _get_cached_doc(*args, **kwargs):
	raise KeyError(args)


def _reset():
	frappe.roles = ["Projects User"]
	frappe.get_roles = lambda user=None: list(frappe.roles)
	frappe.session = types.SimpleNamespace(user="nik@example.com")
	frappe.local = types.SimpleNamespace(message_log=[], flags=types.SimpleNamespace())
	frappe.flags = types.SimpleNamespace()
	frappe.tables = {}
	frappe.docs = {}
	frappe.sql_rows = {}
	frappe.values = {}
	frappe.queries = []
	frappe.saved, frappe.inserted, frappe.comments, frappe.deleted = [], [], [], []
	frappe.events, frappe.errors = [], []
	frappe.denied, frappe.failing = set(), set()
	frappe.fail_insert = False
	frappe.counter = 0
	frappe.perms = {"create": True, "delete": True, "write": True, "read": True}
	frappe.installed = {"Job Interval", "Planner Draft Change"}
	frappe.jobs = [
		{"name": "PRJ-1", "project_type": "Build", "planner_stream": 0},
		{"name": "PRJ-2", "project_type": "Service", "planner_stream": 0},
		{"name": "PRJ-INT", "project_type": "Internal", "planner_stream": 0},
	]
	frappe.log_error = lambda *a, **k: frappe.errors.append((a, k))
	frappe.get_all = _get_all
	frappe.get_list = _get_all
	frappe.get_doc = _get_doc
	frappe.get_cached_doc = _get_cached_doc
	frappe.has_permission = lambda doctype=None, ptype="read", doc=None, *a, **k: frappe.perms.get(
		ptype, True
	)
	frappe.delete_doc = lambda doctype, name, *a, **k: (
		frappe.deleted.append((doctype, name, dict(k))),
		frappe.events.append(("delete", name)),
	)
	frappe.get_system_settings = lambda key: "Sunday"
	frappe.db = types.SimpleNamespace(
		sql=_sql,
		exists=_exists,
		has_column=lambda doctype, column: True,
		get_value=_get_value,
		get_single_value=lambda *a, **k: None,
		set_value=lambda *a, **k: frappe.events.append(("set_value", a)),
		savepoint=lambda name: frappe.events.append(("savepoint", name)),
		rollback=lambda save_point=None: frappe.events.append(("rollback", save_point)),
	)
	for project in ("PRJ-1", "PRJ-2", "PRJ-INT"):
		frappe.docs[("Project", project)] = _Doc(name=project)
	frappe.tables["Planner Resource"] = [
		{
			"name": "RES-1",
			"resource_name": "Austin Healey",
			"user": "austin@example.com",
			"is_active": 1,
			"resource_group": "Field",
		},
		{
			"name": "RES-2",
			"resource_name": "Korben",
			"user": "korben@example.com",
			"is_active": 1,
			"resource_group": "Field",
		},
		{
			"name": "RES-3",
			"resource_name": "Retired",
			"user": None,
			"is_active": 0,
			"resource_group": "Field",
		},
		{
			"name": "RES-4",
			"resource_name": "Acme Subs",
			"user": None,
			"is_active": 1,
			"resource_group": "Subcontractor",
		},
	]


def _crew(*members):
	return [
		_Doc(resource=resource, resource_name=label, hours=hours, is_lead=lead)
		for resource, label, hours, lead in members
	]


def _task(name="TASK-1", start="2026-10-12 08:00:00", end="2026-10-15 17:00:00", **values):
	doc = _TaskDoc(
		{
			"doctype": "Task",
			"name": name,
			"subject": f"Subject {name}",
			"project": "PRJ-1",
			"status": "Open",
			"exp_start_date": start,
			"exp_end_date": end,
			"expected_time": 16,
			"custom_start_datetime": None,
			"custom_end_datetime": None,
			"custom_rental_booking": None,
			"custom_rental_task_kind": None,
			"custom_crew_size": 2,
			"custom_tentative": 0,
			"custom_outdoor": 1,
			"custom_customer_visit": 0,
			"custom_locationaddress_of_task": "ADDR-1",
			"color": "#123456",
			"description": "<p>Pump set</p>",
			"parent_task": None,
			"is_group": 0,
			"is_template": 0,
			"modified": MODIFIED,
			"owner": "nik@example.com",
			"custom_crew": _crew(("RES-1", "Austin Healey", 0, 1), ("RES-2", "Korben", 8, 0)),
			"custom_required_credentials": [_Doc(credential_type="Forklift")],
			"custom_equipment": [],
			"depends_on": [],
		}
	)
	doc.update(values)
	frappe.docs[("Task", name)] = doc
	return doc


def _fake_card(doc):
	span = engine.task_span(api._state(doc))
	return {
		"name": doc.get("name"),
		"subject": doc.get("subject"),
		"start": str(span[0]) if span else None,
		"end": str(span[1]) if span else None,
		"expected_time": float(doc.get("expected_time") or 0),
	}


class _Writes(unittest.TestCase):
	"""The engine's conflict checks replaced by answers each test sets, ``_card`` by a summary."""

	def setUp(self):
		_reset()
		self.batches = []
		self.answers = []
		self.previews = []
		self.preview_answer = {}
		self.patches = [
			mock.patch.object(engine, "preview_batch", self._batch),
			mock.patch.object(engine, "preview_conflicts", self._preview),
			mock.patch.object(engine, "_preview", lambda task, crew, *a: ({}, {})),
			mock.patch.object(api, "_card", _fake_card),
		]
		for patch in self.patches:
			patch.start()

	def tearDown(self):
		for patch in self.patches:
			patch.stop()

	def _batch(self, changes, start=None, end=None):
		self.batches.append([(dict(task), [dict(m) for m in crew]) for task, crew in changes])
		return self.answers.pop(0) if self.answers else {}

	def _preview(self, task, crew, start=None, end=None):
		self.previews.append((dict(task), list(crew)))
		return self.preview_answer if not engine.is_tentative(task) else {}

	def _inserted_tasks(self):
		return [row for row in frappe.inserted if row.get("doctype") == "Task"]


# ---------------------------------------------------------------------- pure helpers


class TestPureHelpers(unittest.TestCase):
	def test_working_days_and_split_weights(self):
		self.assertEqual(actions.working_days(MON, SUN), 5)
		self.assertEqual(actions.working_days(SAT, SUN), 0)
		self.assertEqual(actions.working_days(SUN, MON), 0)
		# Mon-Thu cut at Wed: two working days each.
		self.assertEqual(actions.split_weights((MON, THU), WED), (2, 2))
		# Mon-Fri cut at Thu: three and two.
		self.assertEqual(actions.split_weights((MON, FRI), THU), (3, 2))
		# Fri-Mon cut at Sat: Fri, then Sat-Mon (one working day each).
		self.assertEqual(actions.split_weights((D(2026, 10, 9), MON), D(2026, 10, 10)), (1, 1))
		# Fri-Sun cut at Sat: the second part is all weekend, so calendar days decide (1 and 2).
		self.assertEqual(actions.split_weights((FRI, SUN), SAT), (1, 2))

	def test_split_hours_always_add_up(self):
		self.assertEqual(actions.split_hours(16, 2, 2), (8.0, 8.0))
		self.assertEqual(actions.split_hours(10, 3, 2), (6.0, 4.0))
		self.assertEqual(actions.split_hours(10, 1, 2), (3.33, 6.67))
		self.assertEqual(actions.split_hours(0, 3, 2), (0.0, 0.0))
		self.assertEqual(actions.split_hours(7, 0, 0), (0.0, 0.0))
		for total, a, b in ((13.37, 3, 4), (1, 1, 2), (0.05, 1, 1)):
			first, second = actions.split_hours(total, a, b)
			self.assertAlmostEqual(first + second, total, places=2)

	def test_split_crew_shares_own_hours_and_keeps_the_rest(self):
		first, second = actions.split_crew(
			[
				{"resource": "RES-1", "hours": 0, "is_lead": 1},
				{"resource": "RES-2", "hours": 9, "is_lead": 0},
				{"resource": None},
			],
			1,
			2,
		)
		self.assertEqual(
			first,
			[
				{"resource": "RES-1", "is_lead": 1, "hours": None},
				{"resource": "RES-2", "is_lead": 0, "hours": 3.0},
			],
		)
		self.assertEqual(
			second,
			[
				{"resource": "RES-1", "is_lead": 1, "hours": None},
				{"resource": "RES-2", "is_lead": 0, "hours": 6.0},
			],
		)

	def test_like_pattern_escapes_the_wildcards(self):
		self.assertEqual(actions.like_pattern("pump"), "%pump%")
		self.assertEqual(actions.like_pattern("50%_off\\"), "%50\\%\\_off\\\\%")

	def test_merge_results_caps_people_and_projects_then_fills_with_records(self):
		people = [{"kind": "person", "label": f"P{i}"} for i in range(7)]
		projects = [{"kind": "project", "label": f"J{i}"} for i in range(2)]
		records = [{"kind": "task", "label": f"T{i}"} for i in range(30)]
		results, truncated = actions.merge_results(people, projects, records)
		self.assertEqual(len(results), 20)
		self.assertEqual([r["kind"] for r in results[:7]], ["person"] * 5 + ["project"] * 2)
		self.assertTrue(truncated)
		results, truncated = actions.merge_results(people[:2], [], records[:3])
		self.assertEqual((len(results), truncated), (5, False))

	def test_nearest_first_puts_undated_last(self):
		rows = [
			{"name": "A", "start": None},
			{"name": "B", "start": "2026-10-20"},
			{"name": "C", "start": "2026-10-08"},
		]
		self.assertEqual(
			[r["name"] for r in actions.nearest_first(rows, TODAY, lambda r: r["start"])], ["C", "B", "A"]
		)

	def test_the_module_is_tab_indented_and_every_write_is_post(self):
		source = ACTIONS.read_text(encoding="utf-8")
		self.assertNotIn("\n    def ", source)
		self.assertNotIn("\r", source)
		tree = ast.parse(source)
		functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
		for method, args in ACTIONS_CONTRACT.items():
			node = functions[method]
			params = {a.arg for a in node.args.args + node.args.kwonlyargs}
			self.assertTrue(args <= params, f"{method} lacks {sorted(args - params)}")
			decorators = [ast.unparse(d) for d in node.decorator_list]
			if method in ACTIONS_WRITES:
				self.assertIn("frappe.whitelist(methods=['POST'])", decorators, method)
			else:
				self.assertIn("frappe.whitelist()", decorators, method)
		names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
		self.assertEqual(sorted({n for n in names if names.count(n) > 1}), [])
		# Undo never force-deletes.
		self.assertNotIn("force=True", source)
		self.assertNotIn("ignore_permissions", source)


# ---------------------------------------------------------------------- duplicate


class TestDuplicate(_Writes):
	def test_a_duplicate_is_copy_weeks_copy_on_the_same_days(self):
		_task()
		result = actions.duplicate_task("TASK-1")
		(new,) = self._inserted_tasks()
		self.assertEqual(
			{
				k: new[k]
				for k in (
					"subject",
					"project",
					"status",
					"expected_time",
					"custom_crew_size",
					"custom_tentative",
					"custom_outdoor",
					"custom_customer_visit",
					"custom_locationaddress_of_task",
					"color",
				)
			},
			{
				"subject": "Subject TASK-1",
				"project": "PRJ-1",
				"status": "Open",
				"expected_time": 16.0,
				"custom_crew_size": 2,
				"custom_tentative": 0,
				"custom_outdoor": 1,
				"custom_customer_visit": 0,
				"custom_locationaddress_of_task": "ADDR-1",
				"color": "#123456",
			},
		)
		self.assertEqual(
			(new["exp_start_date"], new["exp_end_date"]), ("2026-10-12 08:00:00", "2026-10-15 17:00:00")
		)
		self.assertEqual(
			[(r["resource"], r["hours"], r["is_lead"]) for r in new["custom_crew"]],
			[("RES-1", 0.0, 1), ("RES-2", 8.0, 0)],
		)
		self.assertEqual(new["custom_required_credentials"], [{"credential_type": "Forklift"}])
		self.assertIn("Copied from TASK-1 on the Project Planner", new["description"])
		self.assertEqual(
			(result["created"], result["source"], result["name"]), (True, "TASK-1", "TASK-NEW-1")
		)
		# Create permission is checked on the new doc, before it is inserted.
		self.assertLess(
			frappe.events.index(("check_permission", "create", None)),
			frappe.events.index(("insert", "Task", "TASK-NEW-1")),
		)
		# The copy was checked as a new task, by its own name, with the crew it carries.
		((preview, crew),) = self.batches[0]
		self.assertEqual(preview["name"], "TASK-1 (copy)")
		self.assertEqual([m["resource"] for m in crew], ["RES-1", "RES-2"])

	def test_duplicate_to_a_day_keeps_the_length_and_the_times(self):
		_task()
		actions.duplicate_task("TASK-1", date="2026-10-19")
		(new,) = self._inserted_tasks()
		self.assertEqual(
			(new["exp_start_date"], new["exp_end_date"]), ("2026-10-19 08:00:00", "2026-10-22 17:00:00")
		)

	def test_an_undated_task_dropped_on_a_day_gets_enough_days_for_its_estimate(self):
		_task(
			start=None,
			end=None,
			expected_time=24,
			custom_crew=_crew(("RES-1", "Austin Healey", 0, 0)),
			custom_crew_size=0,
		)
		actions.duplicate_task("TASK-1", date=str(MON))
		(new,) = self._inserted_tasks()
		self.assertEqual((new["exp_start_date"], new["exp_end_date"]), ("2026-10-12", "2026-10-14"))
		_reset()
		_task(start=None, end=None)
		actions.duplicate_task("TASK-1")
		(new,) = self._inserted_tasks()
		self.assertEqual((new["exp_start_date"], new["exp_end_date"]), (None, None))

	def test_overbooking_asks_for_a_reason_and_creates_nothing(self):
		_task()
		self.answers = [{"Austin Healey": ["2026-10-12: Over by 2h"]}]
		result = actions.duplicate_task("TASK-1")
		self.assertEqual(
			result, {"needs_reason": True, "conflicts": {"Austin Healey": ["2026-10-12: Over by 2h"]}}
		)
		self.assertEqual(frappe.inserted, [])
		self.answers = [{"Austin Healey": ["2026-10-12: Over by 2h"]}]
		actions.duplicate_task("TASK-1", reason="Two crews that day")
		self.assertEqual(len(self._inserted_tasks()), 1)
		self.assertIn("Reason: Two crews that day", frappe.comments[0][2])

	def test_refusals(self):
		_task(custom_rental_booking="RB-1")
		with self.assertRaises(_Throw) as caught:
			actions.duplicate_task("TASK-1")
		self.assertIn("Rental Booking RB-1", str(caught.exception))
		_task(project="PRJ-INT")
		with self.assertRaises(_Throw):
			actions.duplicate_task("TASK-1")
		_task(is_group=1)
		with self.assertRaises(_Throw):
			actions.duplicate_task("TASK-1")
		_task()
		with self.assertRaises(_Throw) as caught:
			actions.duplicate_task("TASK-1", draft=1)
		self.assertIn("Draft mode", str(caught.exception))
		frappe.perms["create"] = False
		with self.assertRaises(_PermissionError):
			actions.duplicate_task("TASK-1")
		frappe.perms["create"] = True
		frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			actions.duplicate_task("TASK-1")
		self.assertEqual(frappe.inserted, [])


# ---------------------------------------------------------------------- split


class TestSplit(_Writes):
	def test_a_multi_day_task_is_cut_in_two_pro_rata(self):
		_task()  # Mon 08:00 - Thu 17:00, 16h, Austin (share) + Korben (8h of his own)
		result = actions.split_task("TASK-1", str(WED), MODIFIED)
		(first,) = frappe.saved
		self.assertEqual(
			(first["exp_start_date"], first["exp_end_date"]), ("2026-10-12 08:00:00", "2026-10-13 17:00:00")
		)
		self.assertEqual(first["expected_time"], 8.0)
		self.assertEqual(
			[(r["resource"], r["hours"]) for r in first["custom_crew"]], [("RES-1", 0.0), ("RES-2", 4.0)]
		)
		(second,) = self._inserted_tasks()
		self.assertEqual(
			(second["exp_start_date"], second["exp_end_date"]), ("2026-10-14 08:00:00", "2026-10-15 17:00:00")
		)
		self.assertEqual(second["expected_time"], 8.0)
		self.assertEqual(
			[(r["resource"], r["hours"], r["is_lead"]) for r in second["custom_crew"]],
			[("RES-1", 0.0, 1), ("RES-2", 4.0, 0)],
		)
		# The second half depends on the first, and carries what a copy carries.
		self.assertEqual(second["depends_on"], [{"task": "TASK-1"}])
		self.assertEqual(second["custom_required_credentials"], [{"credential_type": "Forklift"}])
		self.assertEqual((second["custom_tentative"], second["custom_outdoor"]), (0, 1))
		self.assertIn("Split from TASK-1 on the Project Planner", second["description"])
		self.assertNotIn("Copied from", second["description"])
		# A timeline note on both.
		notes = {(name, text.split(":")[0]) for name, _kind, text in frappe.comments}
		self.assertIn(("TASK-1", "Split on the Project Planner"), notes)
		self.assertTrue(
			any(name == "TASK-NEW-1" and "Split from TASK-1" in text for name, _k, text in frappe.comments)
		)
		self.assertEqual(result["first"], {"start": "2026-10-12", "end": "2026-10-13", "expected_time": 8.0})
		self.assertEqual(
			(result["second"]["name"], result["second"]["start"], result["second"]["end"]),
			("TASK-NEW-1", "2026-10-14", "2026-10-15"),
		)
		self.assertIn(("savepoint", "planner_split"), frappe.events)
		self.assertNotIn(("rollback", "planner_split"), frappe.events)
		# The first is saved before the second exists, so ERPNext has nothing of the second's to push.
		self.assertLess(
			frappe.events.index(("save", "TASK-1")), frappe.events.index(("insert", "Task", "TASK-NEW-1"))
		)

	def test_hours_follow_working_days(self):
		_task(
			start="2026-10-12 08:00:00",
			end="2026-10-16 17:00:00",
			expected_time=10,
			custom_crew=_crew(("RES-1", "Austin Healey", 0, 1)),
		)
		actions.split_task("TASK-1", str(THU), MODIFIED)
		self.assertEqual(frappe.saved[0]["expected_time"], 6.0)
		self.assertEqual(self._inserted_tasks()[0]["expected_time"], 4.0)
		# A crew without hours of its own is not rewritten on the first half.
		self.assertEqual(
			[(r["resource"], r["hours"]) for r in frappe.saved[0]["custom_crew"]], [("RES-1", 0)]
		)

	def test_a_one_day_task_splits_in_two_halves_onto_the_day_given(self):
		_task(
			start="2026-10-12 08:00:00",
			end="2026-10-12 17:00:00",
			expected_time=6,
			custom_crew=_crew(("RES-1", "Austin Healey", 0, 1)),
		)
		actions.split_task("TASK-1", str(THU), MODIFIED)
		first, second = frappe.saved[0], self._inserted_tasks()[0]
		self.assertEqual(
			(first["exp_start_date"], first["exp_end_date"], first["expected_time"]),
			("2026-10-12 08:00:00", "2026-10-12 17:00:00", 3.0),
		)
		self.assertEqual(
			(second["exp_start_date"], second["exp_end_date"], second["expected_time"]),
			("2026-10-15 08:00:00", "2026-10-15 17:00:00", 3.0),
		)

	def test_a_one_day_task_with_no_estimate_gets_half_a_day_each(self):
		_task(
			start="2026-10-12 08:00:00",
			end="2026-10-12 17:00:00",
			expected_time=0,
			custom_crew=_crew(("RES-1", "Austin Healey", 0, 1)),
		)
		actions.split_task("TASK-1", str(TUE), MODIFIED)
		self.assertEqual(
			(frappe.saved[0]["expected_time"], self._inserted_tasks()[0]["expected_time"]), (4.0, 4.0)
		)

	def test_a_slot_moves_with_a_one_day_half_and_a_from_to_pair_never_becomes_one(self):
		_task(
			start="2026-10-12 00:00:00",
			end="2026-10-12 00:00:00",
			custom_start_datetime="2026-10-12 09:00:00",
			custom_end_datetime="2026-10-12 11:00:00",
			custom_crew=_crew(("RES-1", "Austin Healey", 0, 1)),
		)
		actions.split_task("TASK-1", str(WED), MODIFIED)
		second = self._inserted_tasks()[0]
		self.assertEqual(
			(second["custom_start_datetime"], second["custom_end_datetime"]),
			("2026-10-14 09:00:00", "2026-10-14 11:00:00"),
		)
		_reset()
		# A two-day task with a start time on Monday and an end time on Tuesday: cut at Tuesday, each
		# half is one day, and neither turns into a 08:00-17:00 slot that books its length.
		_task(
			start="2026-10-12 08:00:00",
			end="2026-10-13 17:00:00",
			custom_start_datetime="2026-10-12 08:00:00",
			custom_end_datetime="2026-10-13 17:00:00",
			custom_crew=_crew(("RES-1", "Austin Healey", 0, 1)),
		)
		actions.split_task("TASK-1", str(TUE), MODIFIED)
		first, second = frappe.saved[0], self._inserted_tasks()[0]
		self.assertEqual((first["custom_start_datetime"], first["custom_end_datetime"]), (None, None))
		self.assertEqual((second["custom_start_datetime"], second["custom_end_datetime"]), (None, None))
		self.assertEqual(
			(first["exp_end_date"], second["exp_start_date"]), ("2026-10-12 17:00:00", "2026-10-13 08:00:00")
		)

	def test_refusals_say_why(self):
		cases = [
			({"custom_rental_booking": "RB-1"}, "Rental Booking RB-1"),
			({"status": "Completed"}, "Completed"),
			({"is_template": 1}, "template"),
		]
		for values, needle in cases:
			_reset()
			_task(**values)
			with self.assertRaises(_Throw) as caught:
				actions.split_task("TASK-1", str(WED), MODIFIED)
			self.assertIn(needle, str(caught.exception))
		_reset()
		_task()
		frappe.tables["Timesheet Detail"] = [{"task": "TASK-1", "docstatus": 1}]
		with self.assertRaises(_Throw) as caught:
			actions.split_task("TASK-1", str(WED), MODIFIED)
		self.assertIn("timesheet", str(caught.exception))
		frappe.tables["Timesheet Detail"] = [
			{"task": "TASK-1", "docstatus": 2}
		]  # a canceled one does not count
		actions.split_task("TASK-1", str(WED), MODIFIED)
		_reset()
		_task()
		frappe.tables["Job Interval"] = [{"task": "TASK-1"}]
		with self.assertRaises(_Throw) as caught:
			actions.split_task("TASK-1", str(WED), MODIFIED)
		self.assertIn("clocked", str(caught.exception))
		frappe.installed.discard("Job Interval")  # a site without the kiosk
		actions.split_task("TASK-1", str(WED), MODIFIED)
		self.assertEqual(len(self._inserted_tasks()), 1)

	def test_bad_days_drafts_stale_pages_and_permissions(self):
		_task()
		for day in (str(MON), str(FRI), "2026-10-01"):
			with self.assertRaises(_Throw):
				actions.split_task("TASK-1", day, MODIFIED)
		with self.assertRaises(_Throw):
			actions.split_task("TASK-1", None, MODIFIED)
		_task(start="2026-10-12 08:00:00", end="2026-10-12 17:00:00")
		with self.assertRaises(_Throw):
			actions.split_task("TASK-1", str(MON), MODIFIED)
		with self.assertRaises(_Throw) as caught:
			actions.split_task("TASK-1", str(WED), MODIFIED, draft="1")
		self.assertIn("Draft mode", str(caught.exception))
		with self.assertRaises(_Throw):
			actions.split_task("TASK-1", str(WED), "2026-01-01 00:00:00")
		frappe.denied = {"TASK-1"}
		with self.assertRaises(_PermissionError):
			actions.split_task("TASK-1", str(WED), MODIFIED)
		frappe.denied = set()
		frappe.perms["create"] = False
		with self.assertRaises(_PermissionError):
			actions.split_task("TASK-1", str(WED), MODIFIED)
		self.assertEqual((frappe.saved, frappe.inserted), ([], []))

	def test_overbooking_asks_once_and_writes_nothing(self):
		_task()
		self.answers = [{}, {"Korben": ["2026-10-14: Over by 1h"]}]  # before, after
		result = actions.split_task("TASK-1", str(WED), MODIFIED)
		self.assertEqual(result, {"needs_reason": True, "conflicts": {"Korben": ["2026-10-14: Over by 1h"]}})
		self.assertEqual((frappe.saved, frappe.inserted), ([], []))
		# The check saw the task as it was, then both halves together.
		before, after = self.batches
		self.assertEqual([t["name"] for t, _c in before], ["TASK-1"])
		self.assertEqual([t["name"] for t, _c in after], ["TASK-1", "TASK-1 (split)"])
		_task()
		self.answers = [{}, {"Korben": ["2026-10-14: Over by 1h"]}]
		actions.split_task("TASK-1", str(WED), MODIFIED, reason="Korben stays late")
		self.assertTrue(any("Reason: Korben stays late" in text for _n, _k, text in frappe.comments))
		# A conflict the task already had does not ask again.
		_reset()
		_task()
		self.answers = [{"Korben": ["2026-10-12: Over by 1h"]}, {"Korben": ["2026-10-12: Over by 1h"]}]
		self.assertNotIn("needs_reason", actions.split_task("TASK-1", str(WED), MODIFIED))

	def test_all_or_nothing(self):
		_task()
		frappe.fail_insert = True
		with self.assertRaises(_Throw):
			actions.split_task("TASK-1", str(WED), MODIFIED)
		self.assertIn(("rollback", "planner_split"), frappe.events)


# ---------------------------------------------------------------------- quick add


class TestQuickAdd(_Writes):
	def test_a_task_for_the_person_and_day_named(self):
		result = actions.quick_add_task(
			"PRJ-1", "  Set   the pump ", str(TUE), hours="6", resource="RES-1", tentative="0"
		)
		(new,) = self._inserted_tasks()
		self.assertEqual(
			{
				k: new[k]
				for k in (
					"subject",
					"project",
					"status",
					"exp_start_date",
					"exp_end_date",
					"expected_time",
					"custom_tentative",
				)
			},
			{
				"subject": "Set the pump",
				"project": "PRJ-1",
				"status": "Open",
				"exp_start_date": "2026-10-13",
				"exp_end_date": "2026-10-13",
				"expected_time": 6.0,
				"custom_tentative": 0,
			},
		)
		self.assertEqual(
			[(r["resource"], r["user"], r["is_lead"]) for r in new["custom_crew"]],
			[("RES-1", "austin@example.com", 0)],
		)
		self.assertIn(("check_permission", "create", None), frappe.events)
		self.assertEqual((result["created"], result["name"]), (True, "TASK-NEW-1"))
		((preview, crew),) = self.batches[0]
		self.assertEqual(
			(preview["exp_start_date"], [m["resource"] for m in crew]), ("2026-10-13", ["RES-1"])
		)

	def test_more_hours_than_a_day_run_over_the_next_weekdays(self):
		actions.quick_add_task("PRJ-1", "Dig", str(FRI), hours=20, resource="RES-1", tentative=1)
		(new,) = self._inserted_tasks()
		self.assertEqual(
			(new["exp_start_date"], new["exp_end_date"], new["custom_tentative"]),
			("2026-10-16", "2026-10-20", 1),
		)

	def test_no_person_books_nobody_and_checks_nothing(self):
		actions.quick_add_task("PRJ-1", "Dig", str(TUE))
		(new,) = self._inserted_tasks()
		self.assertEqual(new.get("custom_crew"), [])
		self.assertEqual(self.batches, [])

	def test_overbooking_asks_for_a_reason(self):
		self.answers = [{"Austin Healey": ["2026-10-13: Over by 3h"]}]
		result = actions.quick_add_task("PRJ-1", "Dig", str(TUE), hours=6, resource="RES-1")
		self.assertTrue(result["needs_reason"])
		self.assertEqual(frappe.inserted, [])
		self.answers = [{"Austin Healey": ["2026-10-13: Over by 3h"]}]
		actions.quick_add_task(
			"PRJ-1", "Dig", str(TUE), hours=6, resource="RES-1", reason="Short day elsewhere"
		)
		self.assertIn("Reason: Short day elsewhere", frappe.comments[0][2])

	def test_refusals(self):
		frappe.docs[("Project", "PRJ-1")] = _Doc(name="PRJ-1")
		frappe.docs[("Project", "PRJ-INT")] = _Doc(name="PRJ-INT")
		cases = [
			dict(project="PRJ-INT", subject="Dig", date=str(TUE)),
			dict(project="PRJ-NOPE", subject="Dig", date=str(TUE)),
			dict(project="PRJ-1", subject="   ", date=str(TUE)),
			dict(project="PRJ-1", subject="x" * 141, date=str(TUE)),
			dict(project="PRJ-1", subject="Dig", date=None),
			dict(project="PRJ-1", subject="Dig", date=str(TUE), hours=-1),
			dict(project="PRJ-1", subject="Dig", date=str(TUE), hours=401),
			dict(project="PRJ-1", subject="Dig", date=str(TUE), resource="RES-3"),  # inactive
			dict(project="PRJ-1", subject="Dig", date=str(TUE), resource="RES-99"),
			dict(project="PRJ-1", subject="Dig", date=str(TUE), draft=1),
		]
		for kwargs in cases:
			with self.assertRaises(_Throw, msg=str(kwargs)):
				actions.quick_add_task(**kwargs)
		frappe.perms["create"] = False
		with self.assertRaises(_PermissionError):
			actions.quick_add_task("PRJ-1", "Dig", str(TUE))
		self.assertEqual(frappe.inserted, [])


# ---------------------------------------------------------------------- move many


class TestMoveMany(_Writes):
	def _two(self):
		_task("TASK-1", start="2026-10-12 08:00:00", end="2026-10-13 17:00:00")
		_task("TASK-2", start="2026-10-14 09:00:00", end="2026-10-14 15:00:00")

	def _moves(self, days):
		def at(text):
			return (D.fromisoformat(text) + datetime.timedelta(days=days)).isoformat()

		return json.dumps(
			[
				{"task": "TASK-1", "modified": MODIFIED, "start": at("2026-10-12"), "end": at("2026-10-13")},
				{"task": "TASK-2", "modified": MODIFIED, "start": at("2026-10-14"), "end": at("2026-10-14")},
			]
		)

	def test_one_check_for_the_lot_and_one_reason(self):
		self._two()
		self.answers = [{}, {"Austin Healey": ["2026-10-21: Over by 2h"]}]
		result = actions.move_many(self._moves(7))
		self.assertTrue(result["needs_reason"])
		self.assertEqual(
			[(m["task"], m["to"], m["to_end"]) for m in result["moves"]],
			[("TASK-1", "2026-10-19", "2026-10-20"), ("TASK-2", "2026-10-21", "2026-10-21")],
		)
		self.assertEqual(frappe.saved, [])
		self.assertEqual(
			[[t["name"] for t, _c in batch] for batch in self.batches], [["TASK-1", "TASK-2"]] * 2
		)
		self._two()
		self.answers = [{}, {"Austin Healey": ["2026-10-21: Over by 2h"]}]
		result = actions.move_many(self._moves(7), reason="Pump arrives late")
		# Furthest first when moving later, times of day kept.
		self.assertEqual([s["name"] for s in frappe.saved], ["TASK-2", "TASK-1"])
		self.assertEqual(
			(frappe.saved[0]["exp_start_date"], frappe.saved[0]["exp_end_date"]),
			("2026-10-21 09:00:00", "2026-10-21 15:00:00"),
		)
		self.assertEqual([m["modified"] for m in result["moved"]], [SAVED, SAVED])
		self.assertEqual(sum("Reason: Pump arrives late" in text for _n, _k, text in frappe.comments), 2)

	def test_moving_earlier_saves_nearest_first(self):
		self._two()
		actions.move_many(self._moves(-3))
		self.assertEqual([s["name"] for s in frappe.saved], ["TASK-1", "TASK-2"])

	def test_everything_is_loaded_and_checked_before_anything_changes(self):
		self._two()
		frappe.denied = {"TASK-2"}
		with self.assertRaises(_PermissionError):
			actions.move_many(self._moves(7))
		self.assertEqual(frappe.saved, [])
		self.assertIn(("check_permission", "write", "TASK-1"), frappe.events)
		frappe.denied = set()
		moves = json.loads(self._moves(7))
		moves[1]["modified"] = "2026-01-01 00:00:00"
		with self.assertRaises(_Throw):
			actions.move_many(json.dumps(moves))
		self.assertEqual(frappe.saved, [])

	def test_pencil_and_firm_up_several(self):
		self._two()
		result = actions.move_many(
			json.dumps(
				[
					{"task": "TASK-1", "modified": MODIFIED, "tentative": 1},
					{"task": "TASK-2", "modified": MODIFIED, "tentative": "true"},
				]
			)
		)
		self.assertEqual(
			[(s["custom_tentative"], s["exp_start_date"]) for s in frappe.saved],
			[(1, "2026-10-12 08:00:00"), (1, "2026-10-14 09:00:00")],
		)
		self.assertEqual([m["tentative"] for m in result["moved"]], [True, True])

	def test_bad_batches_are_refused(self):
		self._two()
		_task("TASK-R", custom_rental_booking="RB-1")
		bad = [
			"[]",
			json.dumps([{"task": "TASK-1", "modified": MODIFIED}]),  # changes nothing
			json.dumps([{"task": "TASK-1", "modified": MODIFIED, "tentative": 1}] * 2),  # twice
			json.dumps([{"modified": MODIFIED, "start": "2026-10-19"}]),
			json.dumps([{"task": "TASK-R", "modified": MODIFIED, "start": "2026-10-19"}]),
			json.dumps([{"task": f"T-{i}", "modified": MODIFIED, "tentative": 1} for i in range(101)]),
		]
		for moves in bad:
			with self.assertRaises(_Throw, msg=moves[:60]):
				actions.move_many(moves)
		self.assertEqual(frappe.saved, [])
		frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			actions.move_many(self._moves(7))

	def test_draft_mode_drafts_each_move_and_writes_no_task(self):
		self._two()
		result = actions.move_many(self._moves(7), draft=1)
		self.assertTrue(result["drafted"])
		self.assertEqual(frappe.saved, [])
		drafts = [row for row in frappe.inserted if row.get("doctype") == "Planner Draft Change"]
		self.assertEqual([d["task"] for d in drafts], ["TASK-1", "TASK-2"])
		self.assertEqual(json.loads(drafts[0]["payload"])["start"], "2026-10-19")
		self.assertEqual(
			[(m["task"], m["to"]) for m in result["moved"]],
			[("TASK-1", "2026-10-19"), ("TASK-2", "2026-10-21")],
		)


# ---------------------------------------------------------------------- undo of a created task


class TestRemoveCreated(_Writes):
	def _new(self, **values):
		values.setdefault("owner", "nik@example.com")
		values.setdefault("modified", SAVED)
		return _task("TASK-NEW-1", **values)

	def test_a_task_nothing_happened_to_is_deleted_without_force(self):
		self._new()
		frappe.tables["Comment"] = [
			{
				"reference_doctype": "Task",
				"reference_name": "TASK-NEW-1",
				"comment_type": "Comment",
				"owner": "nik@example.com",
				"name": "C1",
			}
		]
		result = actions.remove_created_task("TASK-NEW-1", SAVED)
		self.assertEqual(result["removed"], "TASK-NEW-1")
		((doctype, name, kwargs),) = frappe.deleted
		self.assertEqual((doctype, name), ("Task", "TASK-NEW-1"))
		self.assertFalse(kwargs.get("force"))
		self.assertFalse(kwargs.get("ignore_permissions"))

	def test_anything_attached_since_stops_it(self):
		setups = {
			"someone else's": lambda: self._new(owner="lisa@example.com"),
			"changed": lambda: self._new(modified="2026-10-09 11:00:00"),
			"timesheet": lambda: frappe.tables.update(
				{"Timesheet Detail": [{"task": "TASK-NEW-1", "docstatus": 0}]}
			),
			"clocked": lambda: frappe.tables.update({"Job Interval": [{"task": "TASK-NEW-1"}]}),
			"sub-task": lambda: frappe.tables.update(
				{"Task": [{"name": "TASK-9", "parent_task": "TASK-NEW-1"}]}
			),
			"depends": lambda: frappe.tables.update(
				{"Task Depends On": [{"parenttype": "Task", "parent": "TASK-9", "task": "TASK-NEW-1"}]}
			),
			"commented": lambda: frappe.tables.update(
				{
					"Comment": [
						{
							"reference_doctype": "Task",
							"reference_name": "TASK-NEW-1",
							"comment_type": "Comment",
							"owner": "lisa@example.com",
							"name": "C2",
						}
					]
				}
			),
			"file": lambda: frappe.tables.update(
				{"File": [{"attached_to_doctype": "Task", "attached_to_name": "TASK-NEW-1"}]}
			),
			"draft": lambda: frappe.tables.update(
				{"Planner Draft Change": [{"task": "TASK-NEW-1", "status": "Draft"}]}
			),
		}
		for what, setup in setups.items():
			_reset()
			self._new()
			setup()
			with self.assertRaises(_Throw, msg=what) as caught:
				actions.remove_created_task("TASK-NEW-1", SAVED)
			self.assertIn("was not removed", str(caught.exception), what)
			self.assertEqual(frappe.deleted, [], what)

	def test_no_delete_permission_no_undo(self):
		self._new()
		frappe.perms["delete"] = False
		with self.assertRaises(_PermissionError):
			actions.remove_created_task("TASK-NEW-1", SAVED)
		self.assertEqual(frappe.deleted, [])

	def test_a_split_undo_deletes_the_second_half_then_puts_the_first_back(self):
		self._new(depends_on=[_Doc(task="TASK-1")])
		_task(
			"TASK-1", start="2026-10-12 08:00:00", end="2026-10-13 17:00:00", expected_time=8, modified=SAVED
		)
		restore = {
			"task": "TASK-1",
			"modified": SAVED,
			"start": "2026-10-12",
			"end": "2026-10-15",
			"expected_time": 16,
			"crew": [
				{"resource": "RES-1", "hours": None, "is_lead": 1},
				{"resource": "RES-2", "hours": 8, "is_lead": 0},
			],
		}
		result = actions.remove_created_task("TASK-NEW-1", SAVED, restore=json.dumps(restore))
		self.assertLess(
			frappe.events.index(("delete", "TASK-NEW-1")), frappe.events.index(("save", "TASK-1"))
		)
		(first,) = frappe.saved
		self.assertEqual((first["exp_end_date"], first["expected_time"]), ("2026-10-15 17:00:00", 16.0))
		self.assertEqual(
			[(r["resource"], r["hours"]) for r in first["custom_crew"]], [("RES-1", 0.0), ("RES-2", 8.0)]
		)
		self.assertEqual((result["name"], result["modified"]), ("TASK-1", SAVED))
		self.assertTrue(any("was undone" in text for _n, _k, text in frappe.comments))

	def test_a_split_undo_on_a_stale_first_half_deletes_nothing(self):
		self._new()
		_task("TASK-1", modified="2026-10-09 12:00:00")
		with self.assertRaises(_Throw):
			actions.remove_created_task(
				"TASK-NEW-1", SAVED, restore=json.dumps({"task": "TASK-1", "modified": SAVED})
			)
		self.assertEqual(frappe.deleted, [])


# ---------------------------------------------------------------------- search


class TestSearch(unittest.TestCase):
	def setUp(self):
		_reset()

	def _task_row(self, name, start, project="PRJ-1", project_type="Build", **values):
		row = {
			"name": name,
			"subject": f"Pump {name}",
			"project": project,
			"status": "Open",
			"exp_start_date": start,
			"exp_end_date": start,
			"custom_start_datetime": None,
			"custom_end_datetime": None,
			"custom_rental_booking": None,
			"project_title": "Highlands Plaza",
			"project_status": "Active",
			"project_type": project_type,
			"planner_stream": 0,
		}
		row.update(values)
		return row

	def test_the_role_gates(self):
		frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			actions.search_planner("pump")
		frappe.roles = ["Projects User"]  # the Project Planner's, not the Maintenance Planner's
		with self.assertRaises(_PermissionError):
			actions.search_planner("pump", planner="maintenance")
		with self.assertRaises(_Throw):
			actions.search_planner("pump", planner="rentals")

	def test_short_text_asks_nothing(self):
		self.assertEqual(actions.search_planner(" p ")["results"], [])
		self.assertEqual([q for q in frappe.queries if q[0] == "sql"], [])

	def test_customer_jobs_only_open_work_nearest_first(self):
		frappe.sql_rows["FROM `tabTask` t"] = [
			self._task_row("TASK-FAR", "2026-12-01"),
			self._task_row("TASK-NEAR", "2026-10-12"),
			self._task_row("TASK-INT", "2026-10-10", project="PRJ-INT", project_type="Internal"),
			self._task_row("TASK-DONE", "2026-10-11", status="Completed"),
			self._task_row(
				"TASK-RENT", "2026-10-13", project=None, project_type="", custom_rental_booking="RB-1"
			),
			self._task_row("TASK-UNDATED", None),
		]
		frappe.sql_rows["FROM `tabProject` p"] = [
			{
				"name": "PRJ-1",
				"title": "Highlands Plaza",
				"status": "Active",
				"project_type": "Build",
				"planner_stream": 0,
			},
			{
				"name": "PRJ-INT",
				"title": "Internal pumps",
				"status": "Active",
				"project_type": "Internal",
				"planner_stream": 0,
			},
			{
				"name": "PRJ-7",
				"title": "Pumps by stream",
				"status": "Active",
				"project_type": "",
				"planner_stream": 1,
			},
		]
		answer = actions.search_planner("pump", start=str(TODAY))
		results = answer["results"]
		self.assertEqual(
			[(r["kind"], r.get("name")) for r in results if r["kind"] != "person"],
			[
				("project", "PRJ-1"),
				("project", "PRJ-7"),
				("task", "TASK-NEAR"),
				("task", "TASK-RENT"),
				("task", "TASK-FAR"),
				("task", "TASK-UNDATED"),
			],
		)
		near = next(r for r in results if r.get("name") == "TASK-NEAR")
		self.assertEqual(
			(near["start"], near["end"], near["project_title"]),
			("2026-10-12", "2026-10-12", "Highlands Plaza"),
		)
		self.assertTrue(next(r for r in results if r.get("name") == "TASK-RENT")["rental"])
		# The SQL asks for customer jobs (or rental crew tasks) and binds the text, escaped.
		task_sql = next(q for q in frappe.queries if q[0] == "sql" and "FROM `tabTask` t" in q[1])
		self.assertIn("planner_types", task_sql[1])
		self.assertIn("%(like)s", task_sql[1])
		self.assertEqual(task_sql[2]["like"], "%pump%")
		self.assertNotIn("pump", task_sql[1])

	def test_people_and_the_cap(self):
		frappe.tables["Planner Resource"] += [
			{
				"name": f"RES-K{i}",
				"resource_name": f"Kor{i}",
				"user": None,
				"is_active": 1,
				"resource_group": "Field",
			}
			for i in range(8)
		]
		frappe.sql_rows["FROM `tabTask` t"] = [
			self._task_row(f"TASK-{i}", "2026-10-12", subject=f"Kor task {i}") for i in range(30)
		]
		answer = actions.search_planner("kor")
		self.assertEqual(len(answer["results"]), 20)
		self.assertEqual(sum(r["kind"] == "person" for r in answer["results"]), 5)
		self.assertTrue(answer["truncated"])
		self.assertEqual(
			answer["results"][0],
			{
				"kind": "person",
				"resource": "RES-2",
				"user": "korben@example.com",
				"label": "Korben",
				"group": "Field",
			},
		)

	def test_the_maintenance_planner_searches_visits_sites_and_people_with_a_user(self):
		frappe.roles = ["Maintenance User"]
		frappe.sql_rows["tabSapphire Maintenance Record"] = [
			{
				"name": "SMR-1",
				"project": "PRJ-2",
				"customer": "Hotel",
				"visit_label": None,
				"technician": "austin@example.com",
				"docstatus": 0,
				"workflow_state": "Draft",
				"scheduled_visit_date": "2026-10-14",
				"visit_date": None,
				"project_title": "Hotel Fountain Maintenance Contract",
			},
			{
				"name": "SMR-2",
				"project": "PRJ-2",
				"customer": "Hotel",
				"visit_label": "Winterization",
				"technician": None,
				"docstatus": 1,
				"workflow_state": "Approved",
				"scheduled_visit_date": "2026-09-01",
				"visit_date": "2026-09-03",
				"project_title": "Hotel Fountain",
			},
			{
				"name": "SMR-3",
				"project": "PRJ-2",
				"customer": "Hotel",
				"visit_label": None,
				"technician": None,
				"docstatus": 0,
				"workflow_state": "Pending Review",
				"scheduled_visit_date": "2026-10-01",
				"visit_date": "2026-10-02",
				"project_title": "Hotel Fountain",
			},
		]
		frappe.sql_rows["tabSapphire Maintenance Contract"] = [{"name": "PRJ-2", "title": "Hotel Fountain"}]
		frappe.tables["Planner Resource"].append(
			{
				"name": "RES-H",
				"resource_name": "Hotel crew",
				"user": None,
				"is_active": 1,
				"resource_group": "Subcontractor",
			}
		)
		answer = actions.search_planner("hotel", start=str(TODAY), planner="maintenance")
		kinds = [(r["kind"], r.get("name") or r.get("project")) for r in answer["results"]]
		# The visits nearest the day asked about first: Oct 14 is five days away, Oct 2 seven.
		self.assertEqual(
			kinds, [("site", "PRJ-2"), ("visit", "SMR-1"), ("visit", "SMR-3"), ("visit", "SMR-2")]
		)
		visits = {r["name"]: r for r in answer["results"] if r["kind"] == "visit"}
		self.assertEqual((visits["SMR-1"]["status"], visits["SMR-1"]["date"]), ("draft", "2026-10-14"))
		self.assertEqual((visits["SMR-2"]["status"], visits["SMR-2"]["date"]), ("done", "2026-09-03"))
		self.assertEqual((visits["SMR-3"]["status"], visits["SMR-3"]["date"]), ("pending", "2026-10-02"))


# ---------------------------------------------------------------------- the pages


def _strip(code):
	code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
	return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in code.splitlines())


def _block(code):
	# Phase 6B's own block: to the next phase banner, so blocks appended after it are not read.
	start = code.index(PHASE_MARKER)
	end = code.find(
		"\n// ======================================================================",
		start + len(PHASE_MARKER),
	)
	return code[start:] if end < 0 else code[start:end]


def _method(code, name):
	# A class method ends with a tab and a brace; one in a methods object adds a comma.
	match = re.search(rf"^\t{name}\(.*?\) \{{\n(.*?)^\t\}},?\n", code, flags=re.S | re.M)
	assert match, f"no method {name}"
	return match.group(1)


class TestPages(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.pp = PP_JS.read_text(encoding="utf-8")
		cls.mp = MP_JS.read_text(encoding="utf-8")
		cls.pp_block = _block(cls.pp)
		cls.mp_block = _block(cls.mp)

	def test_the_one_line_hooks(self):
		for code in (self.pp, self.mp):
			self.assertIn("this.init_phase6a();\n\t\tthis.init_phase6b();", code)
			self.assertIn("this.render_phase6a();\n\t\tthis.render_phase6b();", code)
			self.assertIn("\tdrop(source, target) {\n\t\tif (this.p6b_drop(source, target)) return;", code)
			self.assertIn("if (this.p6b_undo(snap)) return;", _method(code, "undo"))
			self.assertIn("this.p6b_lift(drag);", _method(code, "lift"))
			self.assertIn("].concat(this.p6b_legend_sections()),", code)
		# The Project Planner's send() routes the planner_actions writes; nothing else in it changed.
		send = _method(self.pp, "send")
		self.assertIn("${(this.p6_send_modules || {})[method] || PP.api}.${method}", send)
		self.assertIn("args = this.with_draft(method, args);", send)
		self.assertIn('if (el.closest(".p6b-grip")) return null;', _method(self.pp, "drag_source"))
		self.assertIn("Object.assign(ProjectPlanner.prototype, PP6B_METHODS);", self.pp_block)
		self.assertIn("Object.assign(MaintenancePlanner.prototype, MP6B_METHODS);", self.mp_block)

	def test_the_writes_go_through_send(self):
		block = self.pp_block
		self.assertIn("p6b_send(method, args, opts) {\n\t\treturn this.send(method, args, opts);", block)
		listed = set(
			re.findall(
				r'"(\w+)"',
				block[block.index("action_methods: [") : block.index("]", block.index("action_methods: ["))],
			)
		)
		self.assertEqual(listed, ACTIONS_WRITES)
		self.assertIn("this.p6_send_modules[method] = PP6B.actions", block)
		self.assertIn('actions: "erpnext_enhancements.api.planner_actions"', block)
		for method in ACTIONS_WRITES:
			self.assertRegex(block, rf'p6b_(?:send|create)\(\s*"{method}"', method)
		# Never a direct call to a planner_actions write.
		self.assertNotRegex(
			_strip(block), r"frappe\.call\(\{\s*method:\s*`\$\{PP6B\.actions\}\.(?!search_planner)"
		)
		# Resize is the dialog's save (both dates through commit, so send, the reason prompt, Undo).
		resize = _method(block, "p6b_resize_end")
		self.assertIn(
			'this.commit(card, "save_task", { task: card.name, start: resize.new_start, end: resize.new_end }, message)',
			resize,
		)
		# Quick add, duplicate and split create through p6b_create, which pushes an Undo that deletes.
		self.assertIn('this.p6b_create("quick_add_task", args', block)
		create = _method(block, "p6b_create")
		self.assertIn("this.p6b_send(method, args, {})", create)
		self.assertIn('p6b: "created"', create)
		self.assertIn("this.push_undo(snapshot, message)", create)
		undo = _method(block, "p6b_undo")
		self.assertIn('this.p6b_send("remove_created_task", args, { message })', undo)
		self.assertIn("auto_reason: __(PP.undo_reason)", undo)
		# Multi-select: one move_many with the snapshot, so send pushes ONE Undo entry for all of them.
		many = _method(block, "p6b_commit_many")
		self.assertIn(
			'this.p6b_send("move_many", { moves: JSON.stringify(moves), draft: this.draft_on ? 1 : 0 }, { snapshot, message })',
			many,
		)
		# The Maintenance Planner moves visits only with its own move_visit/add_crew, through send.
		self.assertEqual(set(re.findall(r'this\.send\(\s*"(\w+)"', self.mp_block)), {"move_visit"})
		self.assertIn("this.add_to_crew(fresh, user)", self.mp_block)
		self.assertIn("this.apply(fresh, { technician: user })", self.mp_block)
		self.assertIn("this.apply(card, { date: answer.date })", self.mp_block)

	def test_nobody_is_picked_automatically(self):
		for block, attr, fn in (
			(self.pp_block, "data-p6b-pick", "p6b_assign_to("),
			(self.mp_block, "data-mp-p6b-pick", None),
		):
			bare = _strip(block)
			# Who is free is listed; the person comes only from the row the planner clicked.
			self.assertIn(f'closest("[{attr}]")', bare)
			self.assertNotRegex(bare, r"\.free\[\d\]")
			self.assertNotRegex(bare, r"not_free\[\d\]")
			self.assertNotIn("suggest_dates", bare)
			if fn:
				self.assertEqual(bare.count(fn), 2, "defined once, called once: from the click")
		click = _method(self.pp_block, "p6b_assign_click")
		self.assertIn('const resource = pick.getAttribute("data-p6b-pick");', click)
		assign = _method(self.pp_block, "p6b_assign_to")
		self.assertIn('"add_crew"', assign)
		self.assertIn('"swap_crew"', assign)
		# The add_crew and swap_crew writes exist nowhere else in the block.
		self.assertEqual(_strip(self.pp_block).count('"add_crew"'), 1)
		self.assertEqual(_strip(self.pp_block).count('"swap_crew"'), 1)
		mp_click = _method(self.mp_block, "p6b_assign_click")
		self.assertIn('pick.getAttribute("data-mp-p6b-pick")', mp_click)
		self.assertEqual(_strip(self.mp_block).count("this.add_to_crew("), 1)

	def test_the_menu_is_the_kits_and_takes_the_providers(self):
		for block, pure in ((self.pp_block, "PP6B_PURE"), (self.mp_block, "MP6B_PURE")):
			self.assertIn("this.p6_menu_providers = this.p6_menu_providers || [];", block)
			opening = _method(block, "p6b_open_for")
			self.assertIn(
				f"const all = {pure}.with_providers(items, this.p6_menu_providers, target);", opening
			)
			# A target with none of the page's own items still gets the other phases' (6C, 6D-UI).
			self.assertIn("if (!all.length) return false;", opening)
			self.assertNotIn("if (!items.length)", opening)
			self.assertIn("this.p6b_open_menu(target, all, anchor);", opening)
			self.assertIn("kit.menu({ anchor, items: all, title: this.p6b_menu_title(target), owner:", block)
			self.assertIn('root.addEventListener("contextmenu", (e) => this.p6b_contextmenu(e));', block)
			# The long press: half a second, only when the finger did not move, and the click it ends
			# in is swallowed so the menu's first item does not run.
			self.assertIn("long_press_ms: 500", block)
			self.assertIn("press.moved", block)
			self.assertIn("this.p6b_swallow_click();", block)
			self.assertIn("p6_selection = this.p6_selection || new Set();", block)
			self.assertIn('$(document).trigger("p6-selection-changed", [this]);', block)
			self.assertIn("this.p6_legend_providers = this.p6_legend_providers || [];", block)
		items = _method(self.pp_block, "p6b_card_items")
		for label in (
			"Edit",
			"Assign to…",
			"Firm up",
			"Pencil",
			"Duplicate",
			"Duplicate to…",
			"Split across days…",
			"Split in two…",
			"Project at a glance",
		):
			self.assertIn(f'__("{label}")', items)
		self.assertIn('label: __("Move to next free day")', self.pp_block)
		self.assertIn('label: __("Add task here…")', self.pp_block)
		self.assertIn('label: __("See their week")', self.pp_block)
		mp_items = _method(self.mp_block, "p6b_card_items")
		for label in ("Edit", "Assign to…", "Site at a glance"):
			self.assertIn(f'__("{label}")', mp_items)
		# Visits have no duplicate, split or pencil, and project work keeps the browser's menu.
		for absent in ("Duplicate", "Split", "Pencil", "Add visit"):
			self.assertNotIn(absent, _strip(self.mp_block).replace("Duplicate, Split", ""))
		self.assertIn('if (card.kind === "booking") return [];', mp_items)

	def test_next_free_day_uses_the_6d_endpoint_and_saves_through_the_normal_path(self):
		self.assertIn('conflicts: "erpnext_enhancements.api.planner_conflicts"', self.pp_block)
		self.assertIn("method: `${PP6B.conflicts}.get_next_free_day`", self.pp_block)
		self.assertIn(
			'next_free_day: "erpnext_enhancements.api.planner_conflicts.get_next_free_day"', self.mp_block
		)
		move = _method(self.pp_block, "p6b_move_to")
		self.assertIn('this.commit(card, "save_task", { task: card.name, start: date }', move)
		item = _method(self.pp_block, "p6b_next_free_item")
		self.assertIn('__("No crew")', item)
		self.assertIn("disabled: true", item)
		self.assertIn('__("Nothing free in {0} days"', self.pp_block)

	def test_draft_mode_refuses_a_new_task_before_asking_the_server(self):
		for name in ("p6b_duplicate", "p6b_duplicate_to", "p6b_split", "p6b_quick_add"):
			self.assertIn("this.p6b_refuse_in_draft()", _method(self.pp_block, name), name)
		self.assertIn("if (!this.draft_on) return false;", _method(self.pp_block, "p6b_refuse_in_draft"))

	def test_resize_is_pointer_events_and_never_a_move_or_a_rental(self):
		grips = _method(self.pp_block, "p6b_add_grips")
		self.assertIn("!card.movable", grips)
		self.assertIn('this.view !== "week" && this.view !== "crew"', grips)
		self.assertIn("card.slot", grips)
		start = _method(self.pp_block, "p6b_resize_start")
		self.assertIn("e.stopPropagation();", start)
		self.assertIn(
			'root.addEventListener("pointerdown", (e) => this.p6b_pointer_down(e), true);', self.pp_block
		)
		move = _method(self.pp_block, "p6b_resize_move")
		self.assertIn("resize.new_end = ymd < resize.start ? resize.start : ymd;", move)
		self.assertIn("resize.new_start = ymd > resize.end ? resize.end : ymd;", move)
		self.assertIn("this.edge_scroll(e.clientX, e.clientY);", move)
		for code in (self.pp_block, self.mp_block):
			for forbidden in (
				"draggable",
				"dragstart",
				"dragover",
				"dataTransfer",
				'addEventListener("drop"',
			):
				self.assertNotIn(forbidden, code)
		# The Maintenance Planner has no multi-day visits, so no grips.
		self.assertNotIn("grip", self.mp_block)

	def test_multi_select_moves_by_calendar_days_and_one_row_change_only(self):
		drop = _method(self.pp_block, "p6b_drop")
		self.assertIn("this.p6_selection.size < 2", drop)
		self.assertIn("target.resource !== source.from_resource", drop)
		self.assertIn("PP6B_PURE.ymd_diff(from, target.date)", drop)
		mp_drop = _method(self.mp_block, "p6b_drop")
		self.assertIn('(target.user || "") !== row_user', mp_drop)
		self.assertIn("MP6B_PURE.ymd_diff(source.card.date, target.date)", mp_drop)
		self.assertIn("calendar days", self.pp_block)
		self.assertIn("calendar days", self.mp_block)
		# The Maintenance Planner: one confirmation, one reason for the lot, a partial failure listed.
		run = _method(self.mp_block, "p6b_run_moves")
		self.assertIn("this.p6b_one_reason(", run)
		self.assertIn("missed.push(plan)", run)
		self.assertIn("frappe.msgprint(", run)
		self.assertIn("frappe.confirm(", _method(self.mp_block, "p6b_move_selection"))
		one = _method(self.mp_block, "p6b_one_reason")
		self.assertIn("delete this.ask_reason;", one)
		self.assertIn("MaintenancePlanner.prototype.ask_reason", one)

	def test_shortcuts_skip_inputs_and_leave_frappes_alone(self):
		for block in (self.pp_block, self.mp_block):
			key = _method(block, "p6b_key")
			self.assertIn(
				"closest(\"input, textarea, select, [contenteditable]:not([contenteditable='false'])\")", key
			)
			self.assertIn("isContentEditable", key)
			self.assertIn("window.cur_dialog && window.cur_dialog.display", key)
			self.assertIn('target.closest(".pk-menu")', key)
			bind = _method(block, "p6b_bind_keys")
			self.assertIn('document.addEventListener("keydown", handler, true);', bind)
			self.assertIn('document.removeEventListener("keydown", handler, true);', bind)
			# Only frappe's own show/hide of the page, not a dropdown's bubbling up from inside it.
			self.assertIn('wrapper.on("show", (e) => e.target === wrapper[0] && on())', bind)
			self.assertIn('.on("hide", (e) => e.target === wrapper[0] && off())', bind)
			self.assertNotIn("frappe.ui.keys.add_shortcut", _strip(block))
		self.assertIn("const view = PP.views[Number(action.slice(4)) - 1];", self.pp_block)
		self.assertIn("const view = MP.views[Number(action.slice(4)) - 1];", self.mp_block)

	def test_search_uses_the_loaded_data_first_and_a_real_route(self):
		self.assertIn(
			"PP6B_PURE.search_local(search.q, this.p6b_local_entries(), PP6B.search_limit)", self.pp_block
		)
		self.assertIn(
			"method: `${PP6B.actions}.search_planner`, args: { q, start: this.anchor }", self.pp_block
		)
		self.assertIn(
			'frappe.call({ method: MP6B.search, args: { q, start: this.anchor, planner: "maintenance" } })',
			self.mp_block,
		)
		jump = _method(self.pp_block, "p6b_jump_to_task")
		self.assertIn("this.go(", jump)
		self.assertNotIn("set_route(PP.route", jump)
		self.assertIn("this.go(", _method(self.mp_block, "p6b_jump_to_visit"))
		for block in (self.pp_block, self.mp_block):
			self.assertIn('"aria-autocomplete": "list"', block)
			self.assertIn('role="option"', block)
			self.assertIn('e.key === "ArrowDown" || e.key === "ArrowUp"', block)
			self.assertIn('e.key === "Enter"', block)

	def test_house_rules(self):
		for block, esc, prefix in ((self.pp_block, "pp_esc", "pp"), (self.mp_block, "mp_esc", "mp")):
			bare = _strip(block)
			for forbidden in (
				"localStorage",
				"pushState",
				"replaceState",
				"window.location",
				"location.href",
				"history.",
				'type: "GET"',
				"frappe.xcall",
				"innerHTML",
			):
				self.assertNotIn(forbidden, bare, forbidden)
			raw = [
				line.strip()
				for line in block.splitlines()
				if "<" in line
				and re.search(
					r"\$\{(?:card|item|data|visit|member|person|entry|row|answer|result)\.\w+", line
				)
			]
			self.assertEqual(raw, [])
			self.assertIn(f"{esc}(item.label)", block)
			for british in ("colour", "cancelled", "labour", "centre", "behaviour"):
				self.assertNotIn(british, block.lower(), british)
			style = block[block.index("_STYLE = `") : block.index("`;", block.index("_STYLE = `"))]
			classes = set(re.findall(r"\.([a-zA-Z][\w-]*)", style))
			self.assertEqual(
				{c for c in classes if not c.startswith(f"{prefix}-") and not c.startswith("p6b-")},
				set(),
				classes,
			)
			if prefix == "mp":
				self.assertEqual(
					{c for c in classes if c.startswith("p6b-")},
					set(),
					"the Maintenance Planner's own prefix",
				)
		self.assertEqual(self.mp.count("localStorage"), 2)
		self.assertEqual(self.pp.count("window.localStorage"), 2)

	def test_no_method_is_defined_twice(self):
		for code in (self.pp, self.mp):
			names = re.findall(r"^\t(?:async\s+)?([A-Za-z_]\w*)\s*\([^)]*\)\s*\{\s*$", code, re.M)
			names = [n for n in names if n not in {"if", "for", "while", "switch", "catch", "function"}]
			self.assertEqual(sorted({n for n in names if names.count(n) > 1}), [])
		for block, table in (
			(self.pp_block, "const PP6B_METHODS = {"),
			(self.mp_block, "const MP6B_METHODS = {"),
		):
			methods = re.findall(r"^\t([A-Za-z_]\w*)\s*\([^)]*\)\s*\{\s*$", block[block.index(table) :], re.M)
			own = [m for m in methods if m not in {"init_phase6b", "render_phase6b"}]
			self.assertTrue(
				own and all(m.startswith("p6b_") for m in own), [m for m in own if not m.startswith("p6b_")]
			)

	def test_the_page_contract_names_who_is_free(self):
		from erpnext_enhancements.tests import test_project_planner_page as page

		self.assertEqual(page.API_CONTRACT["who_is_free"], {"start", "hours"})
		self.assertIn("method: `${PP.api}.who_is_free`, args: { start: day, hours }", self.pp_block)
		self.assertIn('who_is_free: "erpnext_enhancements.api.project_planner.who_is_free"', self.mp_block)
		# The Maintenance Planner's own endpoint list is untouched: what it calls of other modules is
		# named in its own block.
		methods = self.mp[
			self.mp.index("MP.methods = {") : self.mp.index("};", self.mp.index("MP.methods = {"))
		]
		self.assertEqual(
			set(re.findall(r"(\w+): `\$\{MP\.api\}\.(\w+)`", methods)),
			{(n, n) for n in ("get_planner", "move_visit", "move_projected", "add_crew")},
		)
		calls = set(re.findall(r"planner_actions\.(\w+)", _strip(self.pp_block) + _strip(self.mp_block)))
		self.assertTrue(calls <= set(ACTIONS_CONTRACT), calls)


# ====================================================================== the pure helpers under node


def _pure(code, name):
	start = code.index(f"const {name} = {{")
	return code[start : code.index("\n};\n", start) + 3]


PURE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(0, "utf8");
const ctx = {};
vm.createContext(ctx);
vm.runInContext(source + "\nthis.PP = PP6B_PURE; this.MP = MP6B_PURE;", ctx);
const out = {};
const shared = (P) => {
	const r = {};
	r.add = [P.ymd_add("2026-10-30", 3), P.ymd_add("2026-11-01", 1), P.ymd_add("2026-03-07", 2), P.ymd_add("2026-01-01", -1), P.ymd_add("2026-10-12", 0)];
	r.diff = [P.ymd_diff("2026-10-12", "2026-10-19"), P.ymd_diff("2026-10-19", "2026-10-12"), P.ymd_diff("2026-10-31", "2026-11-02"), P.ymd_diff("2026-03-07", "2026-03-09")];
	const set = new Set(["A"]);
	r.toggle = [P.toggle(set, "B"), P.toggle(set, "A"), P.toggle(set, "B"), [...set]];
	const keys = [
		{ key: "t" }, { key: "T" }, { key: "T", shift: true }, { key: "t", editable: true }, { key: "t", dialog: true },
		{ key: "ArrowLeft" }, { key: "ArrowRight" }, { key: "ArrowLeft", shift: true }, { key: "1" }, { key: "3" }, { key: "4" },
		{ key: "z", ctrl: true }, { key: "Z", meta: true }, { key: "z", ctrl: true, shift: true }, { key: "z", ctrl: true, editable: true },
		{ key: "s", ctrl: true }, { key: "k", ctrl: true }, { key: "g", ctrl: true }, { key: "?", shift: true }, { key: "/" },
		{ key: "Escape" }, { key: "Escape", editable: true }, { key: "t", alt: true }, { key: "x" },
	];
	r.keys = keys.map((info) => P.shortcut(info));
	r.menu = P.with_providers(
		[{ label: "Edit" }],
		[
			(t) => [{ label: `Block ${t.kind}` }],
			() => { throw new Error("broken provider"); },
			() => [],
			() => [{ label: "A" }, null, { label: "B" }],
		],
		{ kind: "cell" }
	).map((item) => (item.divider ? "--" : item.label));
	r.menu_alone = P.with_providers([], [() => [{ label: "Only" }]], {}).map((item) => (item.divider ? "--" : item.label));
	r.search = P.search_local("pump hot", [
		{ kind: "task", label: "Fix the pump", keys: ["Hotel"] },
		{ kind: "visit", label: "Hotel pump", keys: [] },
		{ kind: "person", label: "Pumphrey", keys: ["Hotel crew"] },
		{ kind: "task", label: "Pump house", keys: ["Hotel"] },
		{ kind: "task", label: "Unrelated", keys: [] },
	]).map((e) => e.label);
	r.search_empty = P.search_local("  ", [{ kind: "task", label: "x" }]);
	r.search_limit = P.search_local("a", Array.from({ length: 30 }, (v, i) => ({ kind: "task", label: `a${i}` })), 7).length;
	return r;
};
out.pp = shared(ctx.PP);
out.mp = shared(ctx.MP);
const P = ctx.PP;
out.working = [P.working_days("2026-10-12", "2026-10-18"), P.working_days("2026-10-17", "2026-10-18"), P.working_days("2026-10-18", "2026-10-12")];
out.weights = [
	P.split_weights("2026-10-12", "2026-10-15", "2026-10-14"),
	P.split_weights("2026-10-12", "2026-10-16", "2026-10-15"),
	P.split_weights("2026-10-09", "2026-10-12", "2026-10-10"),
	P.split_weights("2026-10-16", "2026-10-18", "2026-10-17"),
];
out.hours = [P.split_hours(16, 2, 2), P.split_hours(10, 3, 2), P.split_hours(10, 1, 2), P.split_hours(0, 1, 1), P.split_hours(5, 0, 0)];
out.moves = P.offset_moves(
	[
		{ name: "T1", start: "2026-10-12", end: "2026-10-13", modified: "m1" },
		{ name: "T2", start: "2026-10-16", end: null, modified: "m2" },
		{ name: "T3", start: null },
		{ name: "T4", start: "2026-10-30", end: "2026-10-29" },
	],
	7,
	(name) => `fresh-${name}`
);
out.moves_back = P.offset_moves([{ name: "T1", start: "2026-10-12", end: "2026-10-13", modified: "m1" }], -3);
out.need = [
	P.need_hours({ slot: ["09:00", "11:30"] }, "add", 8),
	P.need_hours({ expected_time: 0 }, "add", 8),
	P.need_hours({ expected_time: 0 }, "add", 10),
	P.need_hours({ expected_time: 24, crew: [{}], start: "2026-10-12", end: "2026-10-14" }, "add", 8),
	P.need_hours({ expected_time: 24, crew: [{}], start: "2026-10-12", end: "2026-10-14" }, "replace", 8),
	P.need_hours({ expected_time: 1, crew: [{}, {}, {}], start: "2026-10-12", end: "2026-10-16" }, "add", 8),
	P.need_hours({ expected_time: 200, crew: [], start: "2026-10-12", end: "2026-10-12" }, "add", 8),
];
process.stdout.write(JSON.stringify(out));
"""


class TestPureUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		if not node:
			raise unittest.SkipTest("node is not on PATH")
		cls.node = node
		source = (
			_pure(PP_JS.read_text(encoding="utf-8"), "PP6B_PURE")
			+ "\n"
			+ _pure(MP_JS.read_text(encoding="utf-8"), "MP6B_PURE")
		)
		result = subprocess.run(
			[node, "-e", PURE_HARNESS],
			input=source,
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
		if result.returncode != 0:
			raise AssertionError(result.stderr)
		cls.out = json.loads(result.stdout)

	def test_the_pages_parse(self):
		for path in (PP_JS, MP_JS):
			result = subprocess.run(
				[self.node, "--check", str(path)], capture_output=True, text=True, timeout=60
			)
			self.assertEqual(result.returncode, 0, f"{path.name}: {result.stderr}")

	def test_both_pages_share_the_same_helpers(self):
		self.assertEqual(self.out["pp"], self.out["mp"])

	def test_calendar_days(self):
		out = self.out["pp"]
		# Daylight saving (Nov 1 and Mar 8, 2026 in the US) never shifts a day.
		self.assertEqual(out["add"], ["2026-11-02", "2026-11-02", "2026-03-09", "2025-12-31", "2026-10-12"])
		self.assertEqual(out["diff"], [7, -7, 2, 2])

	def test_the_selection_set(self):
		self.assertEqual(self.out["pp"]["toggle"], [True, False, False, []])

	def test_the_shortcut_table(self):
		self.assertEqual(
			self.out["pp"]["keys"],
			[
				"today",
				"today",
				None,
				None,
				None,
				"prev",
				"next",
				None,
				"view1",
				"view3",
				None,
				"undo",
				"undo",
				None,
				None,
				None,
				None,
				None,
				"legend",
				"search",
				"escape",
				None,
				None,
				None,
			],
		)

	def test_menu_groups_and_a_broken_provider(self):
		self.assertEqual(self.out["pp"]["menu"], ["Edit", "--", "Block cell", "--", "A", "B"])
		self.assertEqual(self.out["pp"]["menu_alone"], ["Only"])

	def test_local_search(self):
		out = self.out["pp"]
		# Every word must match; a label starting with the first word first, then people, then the rest.
		self.assertEqual(out["search"], ["Pumphrey", "Pump house", "Fix the pump", "Hotel pump"])
		self.assertEqual(out["search_empty"], [])
		self.assertEqual(out["search_limit"], 7)

	def test_offset_moves_keep_lengths(self):
		self.assertEqual(
			self.out["moves"],
			[
				{"task": "T1", "modified": "fresh-T1", "start": "2026-10-19", "end": "2026-10-20"},
				{"task": "T2", "modified": "fresh-T2", "start": "2026-10-23", "end": "2026-10-23"},
				{"task": "T4", "modified": "fresh-T4", "start": "2026-11-06", "end": "2026-11-06"},
			],
		)
		self.assertEqual(
			self.out["moves_back"],
			[{"task": "T1", "modified": "m1", "start": "2026-10-09", "end": "2026-10-10"}],
		)

	def test_the_split_preview_agrees_with_the_server(self):
		self.assertEqual(self.out["working"], [5, 0, 0])
		cases = [
			((MON, THU), WED),
			((MON, FRI), THU),
			((D(2026, 10, 9), MON), D(2026, 10, 10)),
			((FRI, SUN), SAT),
		]
		self.assertEqual(self.out["weights"], [list(actions.split_weights(span, day)) for span, day in cases])
		self.assertEqual(
			self.out["hours"],
			[
				list(actions.split_hours(*args))
				for args in ((16, 2, 2), (10, 3, 2), (10, 1, 2), (0, 1, 1), (5, 0, 0))
			],
		)

	def test_who_is_free_hours(self):
		self.assertEqual(self.out["need"], [2.5, 8, 10, 4, 8, 0.25, 24])


# ====================================================================== the page methods under node


def _phase_source(code, first, last):
	"""The Phase 6B block's constants and methods object, without the Object.assign onto the class."""
	return code[code.index(first) : code.index(last)]


METHODS_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const [pp_source, mp_source] = JSON.parse(fs.readFileSync(0, "utf8"));
const esc = (v) => String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const tr = (text, args) => (args ? String(text).replace(/\{(\d+)\}/g, (m, i) => String(args[i])) : String(text));
const calls = [];
const alerts = [];
const ctx = {
	console,
	Promise,
	setTimeout: (fn) => fn(),
	__: tr,
	pp_esc: esc,
	mp_esc: esc,
	pp_when: (ymd) => `when(${ymd})`,
	mp_when: (ymd) => `when(${ymd})`,
	pp_hours: (v) => String(Math.round((Number(v) || 0) * 100) / 100),
	mp_hours: (v) => String(Math.round((Number(v) || 0) * 100) / 100),
	PP: { route: "project-planner", api: "erpnext_enhancements.api.project_planner", views: ["week", "month", "crew"], heatmap_view: "heatmap", drag_px: 6, undo_reason: "Undo on the Project Planner" },
	PP6A: { owner: "pp" },
	MP: { route: "maintenance-planner", views: ["month", "week", "crew"], drag_px: 6, undo_reason: "Undo on the Maintenance Planner" },
	MP6A: { owner: "mp" },
	frappe: {
		call: (opts) => { calls.push(opts); return Promise.resolve({ message: null }); },
		show_alert: (opts) => alerts.push(opts.message),
		msgprint: (m) => alerts.push(typeof m === "string" ? m : m.message),
		confirm: (m, fn) => fn(),
		get_route: () => ["project-planner", "week", "2026-10-12"],
		datetime: { get_today: () => "2026-10-09" },
	},
	$: () => ({ trigger: () => null }),
	document: { getElementById: () => null },
	window: {},
	MaintenancePlanner: function MaintenancePlanner() {},
};
ctx.MaintenancePlanner.prototype.ask_reason = () => Promise.resolve("A reason");
vm.createContext(ctx);
vm.runInContext(pp_source + "\n" + mp_source + "\nthis.PPM = PP6B_METHODS; this.MPM = MP6B_METHODS;", ctx);
const out = {};
const flush = () => new Promise((resolve) => setImmediate(resolve));

(async () => {
	// ------------------------------------------------------------ the Project Planner
	const sent = [];
	const committed = [];
	const pushed = [];
	const labels = { "RES-1": "Austin <Healey>", "RES-2": "Korben", "RES-9": "Jesse" };
	const pp = Object.assign(Object.create(ctx.PPM), {
		view: "week",
		anchor: "2026-10-12",
		draft_on: false,
		data: { can_edit: true, settings: { default_day_hours: 8 }, start: "2026-10-11", end: "2026-10-17", projects: [{ name: "PRJ-1", title: "Plaza" }], resources: [] },
		modified: { "TASK-1": "m-fresh" },
		p6_selection: new Set(),
		p6_menu_providers: [],
		p6_legend_providers: [() => [{ title: "Color by", items: [] }]],
		p6b: { next_free: {}, search: { q: "" } },
		by_resource: { "RES-1": { user: "austin@x.com" } },
		resource_label: (resource, fallback) => labels[resource] || fallback || resource,
		crew_has: (card, resource) => (card.crew || []).some((m) => m.resource === resource),
		crew_rows: (card) => (card.crew || []).map((m) => ({ resource: m.resource, hours: m.hours || null, is_lead: m.is_lead ? 1 : 0 })),
		commit: (card, method, args, message) => { committed.push([method, args, message]); return Promise.resolve({}); },
		send: (method, args, opts) => { sent.push([method, args, Object.keys(opts || {}).sort()]); return Promise.resolve({ name: "TASK-NEW-1", modified: "m-new", card: { start: "2026-10-14" }, second: { name: "TASK-NEW-2", modified: "m-2", card: { start: "2026-10-15" } } }); },
		push_undo: (snap, message) => { pushed.push([snap, message]); return true; },
		today: () => "2026-10-09",
		load: () => null,
		render: () => null,
		open_card: () => null,
		firm_up: () => null,
		p6a_open_project: () => null,
		p6a_open_person: () => null,
		p6a_open_day: () => null,
	});
	const card = { name: "TASK-1", subject: "Dig", project: "PRJ-1", start: "2026-10-12", end: "2026-10-14", expected_time: 12, movable: true, crew: [{ resource: "RES-1", is_lead: 1 }, { resource: "RES-2" }] };
	pp.by_task = { "TASK-1": card };
	const labels_of = (items) => items.map((i) => (i.divider ? "--" : `${i.label}${i.disabled ? " [off]" : ""}${i.hint ? ` (${i.hint})` : ""}`));
	out.pp_items = labels_of(pp.p6b_card_items({ kind: "card", card, ymd: "2026-10-13", resource: null }));
	pp.draft_on = true;
	out.pp_items_draft = labels_of(pp.p6b_card_items({ kind: "card", card, ymd: "2026-10-13" }));
	pp.draft_on = false;
	out.pp_items_rental = labels_of(pp.p6b_card_items({ kind: "card", card: Object.assign({}, card, { movable: false, rental_kind: "Delivery", project: null }), ymd: "2026-10-13" }));
	out.pp_items_one_day = labels_of(pp.p6b_card_items({ kind: "card", card: Object.assign({}, card, { end: "2026-10-12", tentative: 1, crew: [{ resource: "RES-1" }, { resource: "RES-2" }] }), ymd: "2026-10-12" }));
	out.pp_cell = labels_of(pp.p6b_cell_items({ kind: "cell", resource: "RES-1", ymd: "2026-10-13" }));
	out.lead = [
		pp.p6b_lead(card, null),
		pp.p6b_lead(card, "RES-2"),
		pp.p6b_lead(card, "RES-9"),
		pp.p6b_lead({ crew: [{ resource: "RES-2" }] }, null),
		pp.p6b_lead({ crew: [{ resource: "RES-1" }, { resource: "RES-2" }] }, null),
		pp.p6b_lead({ crew: [] }, null),
	];
	out.free_state = [pp.p6b_free_state(null), pp.p6b_free_state({ date: "2026-10-15" }), pp.p6b_free_state({ date: null, horizon: 30 })];
	out.next_free_calls = calls.filter((c) => /get_next_free_day/.test(c.method)).map((c) => [c.method, c.args]);

	// Assign to: the list, never a pick.
	const state = { mode: "add", data: { hours: 4, days: [{ date: "2026-10-13", free: [{ resource: "RES-9", label: "Jesse <J>", group: "Field", free_hours: 6 }, { resource: "RES-1", label: "Austin", free_hours: 8 }], not_free: [{ resource: "RES-4", label: "Acme", reason: "Time off" }] }] } };
	const html = pp.p6b_assign_html(card, "2026-10-13", state);
	out.assign = {
		picks: [...html.matchAll(/data-p6b-pick="([^"]+)"/g)].map((m) => m[1]),
		escaped: html.includes("Jesse &lt;J&gt;") && !html.includes("Jesse <J>"),
		modes: [...html.matchAll(/name="p6b-mode" value="([^"]+)"/g)].map((m) => m[1]),
		reason: html.includes("Time off"),
	};
	pp.p6b_assign_to(card, "RES-9", "add");
	pp.p6b_assign_to(card, "RES-9", "RES-2");
	pp.p6b_assign_to(card, "RES-1", "add");

	// Next free day, pencil, a resize-like move.
	pp.p6b_move_to(card, "2026-10-15");
	pp.p6b_pencil(card);

	// Several at once.
	pp.p6_selection = new Set(["TASK-1", "TASK-2"]);
	pp.by_task["TASK-2"] = { name: "TASK-2", subject: "Set", start: "2026-10-15", end: null, movable: true, modified: "m2", tentative: 1 };
	out.drop_row = pp.p6b_drop({ kind: "card", card, from_date: "2026-10-13", from_resource: "RES-1" }, { date: "2026-10-14", resource: "RES-2" });
	out.drop = pp.p6b_drop({ kind: "card", card, from_date: "2026-10-13", from_resource: "RES-1" }, { date: "2026-10-20", resource: "RES-1" });
	out.drop_single = pp.p6b_drop({ kind: "card", card: { name: "TASK-9" } }, { date: "2026-10-20" });
	pp.p6b_pencil_many(1);
	pp.draft_on = true;
	pp.p6b_pencil_many(0);
	pp.draft_on = false;
	await flush();

	// Undo of each kind.
	pp.p6b_undo({ p6b: "many", what: "dates", subject: "2 tasks", items: [{ task: "TASK-1", start: "2026-10-12", end: "2026-10-14" }] });
	pp.p6b_undo({ p6b: "many", what: "pencil", subject: "2 tasks", items: [{ task: "TASK-2", tentative: 1 }] });
	pp.p6b_undo({ p6b: "created", subject: "Dig", task: "TASK-NEW-1", modified: "m-new" });
	pp.p6b_undo({ p6b: "split", subject: "Dig", task: "TASK-NEW-2", modified: "m-2", restore: { task: "TASK-1", modified: "m-old", start: "2026-10-12", end: "2026-10-14", expected_time: 12, crew: [] } });
	out.undo_other = pp.p6b_undo({ task: "TASK-1", start: "2026-10-12" });

	// New tasks: refused in draft mode, otherwise through send with an Undo that deletes.
	pp.draft_on = true;
	out.dup_in_draft = pp.p6b_duplicate(card, null);
	pp.draft_on = false;
	await pp.p6b_duplicate(card, "2026-10-19");
	await pp.p6b_split_save(card, "2026-10-13");
	await pp.p6b_quick_add_save({ hide: () => null }, { project: "PRJ-1", subject: " Fix ", date: "2026-10-13", hours: "3", resource: "RES-1", tentative: 1 });
	out.quick_add_blank = pp.p6b_quick_add_save({ hide: () => null }, { project: "PRJ-1", subject: "  ", date: "2026-10-13" });
	out.sent = sent;
	out.committed = committed;
	out.pushed = pushed.map(([snap, message]) => [snap, message]);
	out.legend = pp.p6b_legend_sections().map((s) => s.title);
	out.search_entries = pp.p6b_local_entries().map((e) => [e.kind, e.label]);
	out.from_server = [pp.p6b_from_server({ kind: "task", name: "TASK-7", label: "Far", project_title: "Plaza", start: "2026-12-01", end: "2026-12-02" }), pp.p6b_from_server({ kind: "visit" })];
	out.keys = ["today", "prev", "view3", "search"].map((action) => { pp.view = "route"; return pp.p6b_run_key(action); });

	// ------------------------------------------------------------ the Maintenance Planner
	const msent = [];
	const applied = [];
	const mp = Object.assign(Object.create(ctx.MPM), {
		view: "month",
		anchor: "2026-10-12",
		data: { can_move_visits: true, start: "2026-09-27", end: "2026-11-07", technicians: [{ user: "austin@x.com", name: "Austin", enabled: 1, resource: "RES-1" }, { user: "jesse@x.com", name: "Jesse", enabled: 1, resource: "RES-9" }] },
		modified: {},
		p6_selection: new Set(),
		p6_menu_providers: [(target) => [{ label: `Block ${target.kind}` }]],
		p6b: { next_free: {}, search: { q: "" } },
		tech_by_user: { "austin@x.com": { resource: "RES-1" }, "nobody@x.com": {} },
		tech_name: (user) => ({ "austin@x.com": "Austin", "jesse@x.com": "Jesse <J>" })[user] || user,
		site_of: (c) => c.site || c.name,
		visit_people: (c) => [c.technician].concat((c.crew || []).map((m) => m.user)).filter(Boolean),
		apply: (c, change) => { applied.push(["apply", c.name, change]); return Promise.resolve(); },
		add_to_crew: (c, user) => { applied.push(["add_crew", c.name, user]); return Promise.resolve(); },
		send: (method, args, opts) => { msent.push([method, args, Object.keys(opts || {}).sort()]); return Promise.resolve(args.record === "SMR-BAD" ? null : { name: args.record, modified: "x" }); },
		push_undo: (snap, message) => { pushed.push([snap, message]); return true; },
		today: () => "2026-10-09",
		load: () => null,
		render: () => null,
		open_card: () => null,
		p6a_open_site: () => null,
		p6a_open_person: () => null,
		p6a_open_day: () => null,
		range_days: () => [],
	});
	const visit = { kind: "visit", key: "SMR-1", name: "SMR-1", site: "Hotel", project: "PRJ-2", date: "2026-10-14", technician: "austin@x.com", movable: true, status: "draft", hours: 2, crew: [] };
	out.mp_items = labels_of(mp.p6b_card_items({ kind: "card", card: visit }));
	out.mp_items_projected = labels_of(mp.p6b_card_items({ kind: "card", card: Object.assign({}, visit, { kind: "projected", movable: true }) }));
	out.mp_items_nores = labels_of(mp.p6b_card_items({ kind: "card", card: Object.assign({}, visit, { technician: "nobody@x.com" }) }));
	out.mp_items_booking = mp.p6b_card_items({ kind: "card", card: { kind: "booking" } });
	out.mp_menu = mp.p6b_menu_items({ kind: "cell", ymd: "2026-10-14" }).map((i) => i.label);
	const mstate = { mode: "add", data: { hours: 2, days: [{ date: "2026-10-14", free: [{ resource: "RES-9", user: "jesse@x.com", label: "Jesse", free_hours: 6 }, { resource: "RES-1", user: "austin@x.com", free_hours: 4 }, { resource: "RES-4", user: null, label: "Acme" }, { resource: "RES-7", user: "lisa@x.com", label: "Lisa" }], not_free: [] }] } };
	const mhtml = mp.p6b_assign_html(visit, mstate);
	out.mp_assign = { picks: [...mhtml.matchAll(/data-mp-p6b-pick="([^"]+)"/g)].map((m) => m[1]), escaped: mhtml.includes("Jesse &lt;J&gt;") };
	// Several visits: one confirmation, then move_visit each; a failure is listed and the rest stay.
	mp.by_key = { "SMR-1": visit, "SMR-BAD": Object.assign({}, visit, { key: "SMR-BAD", name: "SMR-BAD", site: "Lodge", date: "2026-10-15" }), "SMR-P": Object.assign({}, visit, { key: "SMR-P", kind: "projected" }) };
	mp.p6_selection = new Set(["SMR-1", "SMR-BAD", "SMR-P"]);
	out.mp_selected = mp.p6b_selected_cards().map((c) => c.key);
	out.mp_drop_row = mp.p6b_drop({ kind: "card", card: visit, from_user: "austin@x.com" }, { date: "2026-10-16", row: true, user: "jesse@x.com" });
	out.mp_drop_past = (() => { alerts.length = 0; mp.p6b_move_selection(-10); return alerts.slice(); })();
	await mp.p6b_run_moves([{ card: visit, date: "2026-10-16" }, { card: mp.by_key["SMR-BAD"], date: "2026-10-17" }]);
	out.mp_sent = msent.slice();
	out.mp_alerts = alerts.slice();
	out.mp_restored = typeof Object.getOwnPropertyDescriptor(mp, "ask_reason");
	msent.length = 0;
	mp.p6b_undo({ p6b: "many", site: "2 visits", items: [{ record: "SMR-1", date: "2026-10-14" }, { record: "SMR-2", date: "2026-10-01" }] });
	await flush();
	await flush();
	out.mp_undo_sent = msent.slice();
	out.mp_undo_other = mp.p6b_undo({ kind: "visit", record: "SMR-1" });
	out.mp_legend = mp.p6b_legend_sections().map((s) => s.title);
	out.mp_pushed = pushed.filter(([snap]) => snap.site).map(([snap, message]) => [snap, message]);
	process.stdout.write(JSON.stringify(out));
})().catch((e) => {
	process.stderr.write(String(e && e.stack ? e.stack : e));
	process.exit(1);
});
"""


class TestMethodsUnderNode(unittest.TestCase):
	"""The Phase 6B methods themselves, with the page and frappe stubbed: what each menu offers, the
	list Assign to... draws, and exactly what each write sends through send()."""

	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		if not node:
			raise unittest.SkipTest("node is not on PATH")
		pp_code = PP_JS.read_text(encoding="utf-8")
		mp_code = MP_JS.read_text(encoding="utf-8")
		sources = [
			_phase_source(
				pp_code, "const PP6B = {", "Object.assign(ProjectPlanner.prototype, PP6B_METHODS);"
			),
			_phase_source(
				mp_code, "const MP6B = {", "Object.assign(MaintenancePlanner.prototype, MP6B_METHODS);"
			),
		]
		result = subprocess.run(
			[node, "-e", METHODS_HARNESS],
			input=json.dumps(sources),
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
		if result.returncode != 0:
			raise AssertionError(result.stderr)
		cls.out = json.loads(result.stdout)

	def test_the_task_menu(self):
		out = self.out
		self.assertEqual(
			out["pp_items"],
			[
				"Edit",
				"Assign to…",
				"Move to next free day (Looking…)",
				"Pencil (Make it tentative)",
				"Duplicate (Same days)",
				"Duplicate to…",
				"Split across days…",
				"--",
				"Project at a glance",
				"Open project",
			],
		)
		self.assertIn("Duplicate [off] (Not in Draft mode)", out["pp_items_draft"])
		self.assertIn("Split across days… [off] (Not in Draft mode)", out["pp_items_draft"])
		self.assertIn("Assign to…", out["pp_items_draft"])  # drafting an existing task is fine
		rental = out["pp_items_rental"]
		self.assertEqual(rental[0], "Edit")
		self.assertTrue(all("[off]" in item for item in rental[1:]), rental)
		self.assertIn("Assign to… [off] (Follows its Rental Booking)", rental)
		one_day = out["pp_items_one_day"]
		self.assertIn("Firm up", one_day)
		self.assertIn("Split in two…", one_day)
		self.assertIn("Move to next free day [off] (Mark a lead first)", one_day)
		self.assertEqual(out["pp_cell"], ["Add task here… (Double-click)", "Everyone's day"])

	def test_next_free_day_is_about_the_right_person(self):
		self.assertEqual(self.out["lead"], ["RES-1", "RES-2", "RES-1", "RES-2", None, None])
		self.assertEqual(
			self.out["free_state"],
			[
				{"hint": "Could not check", "disabled": False},
				{"hint": "when(2026-10-15)", "disabled": False},
				{"hint": "Nothing free in 30 days", "disabled": True},
			],
		)
		((method, args),) = self.out["next_free_calls"]
		self.assertEqual(method, "erpnext_enhancements.api.planner_conflicts.get_next_free_day")
		self.assertEqual(args, {"resource": "RES-1", "task": "TASK-1", "after": "2026-10-12"})

	def test_assign_lists_and_the_planner_picks(self):
		assign = self.out["assign"]
		# People on the crew are not offered again; someone not free is, with the reason.
		self.assertEqual(assign["picks"], ["RES-9", "RES-4"])
		self.assertTrue(assign["escaped"])
		self.assertEqual(assign["modes"], ["add", "RES-1", "RES-2"])
		self.assertTrue(assign["reason"])
		committed = self.out["committed"]
		self.assertEqual(committed[0][:2], ["add_crew", {"task": "TASK-1", "resource": "RES-9"}])
		self.assertEqual(
			committed[1][:2],
			["swap_crew", {"task": "TASK-1", "from_resource": "RES-2", "to_resource": "RES-9"}],
		)
		# Someone already on it is not added twice; the next commits are the move and the pencil.
		self.assertEqual(committed[2][:2], ["save_task", {"task": "TASK-1", "start": "2026-10-15"}])
		self.assertEqual(committed[3][:2], ["save_task", {"task": "TASK-1", "tentative": 1}])
		self.assertEqual(len(committed), 4)

	def test_several_at_once(self):
		out = self.out
		self.assertTrue(out["drop_row"])  # refused, and handled here
		self.assertTrue(out["drop"])
		self.assertFalse(out["drop_single"])  # not in the selection: an ordinary drop
		many = [entry for entry in out["sent"] if entry[0] == "move_many"]
		moved, pencilled, firmed = many[0], many[1], many[2]
		self.assertEqual(
			json.loads(moved[1]["moves"]),
			[
				{"task": "TASK-1", "modified": "m-fresh", "start": "2026-10-19", "end": "2026-10-21"},
				{"task": "TASK-2", "modified": "m2", "start": "2026-10-22", "end": "2026-10-22"},
			],
		)
		self.assertEqual((moved[1]["draft"], moved[2]), (0, ["message", "snapshot"]))
		self.assertEqual(
			json.loads(pencilled[1]["moves"]), [{"task": "TASK-1", "modified": "m-fresh", "tentative": 1}]
		)
		self.assertEqual(
			json.loads(firmed[1]["moves"]), [{"task": "TASK-2", "modified": "m2", "tentative": 0}]
		)
		self.assertEqual(firmed[1]["draft"], 1)

	def test_undo_of_each_kind(self):
		sent = self.out["sent"]
		undos = [entry for entry in sent if entry[2] in (["auto_reason", "message"], ["message"])]
		dates, pencil, created, split = undos[:4]
		self.assertEqual(
			json.loads(dates[1]["moves"]),
			[{"task": "TASK-1", "modified": "m-fresh", "start": "2026-10-12", "end": "2026-10-14"}],
		)
		self.assertEqual(
			json.loads(pencil[1]["moves"]), [{"task": "TASK-2", "modified": "m2", "tentative": 1}]
		)
		self.assertEqual(created[:2], ["remove_created_task", {"task": "TASK-NEW-1", "modified": "m-new"}])
		self.assertEqual(split[0], "remove_created_task")
		self.assertEqual(
			json.loads(split[1]["restore"])["modified"], "m-fresh"
		)  # the newest one the page saw
		self.assertFalse(self.out["undo_other"])

	def test_new_tasks_go_through_send_with_an_undo_that_deletes(self):
		out = self.out
		self.assertIsNone(out["dup_in_draft"])
		creates = [
			entry for entry in out["sent"] if entry[0] in ("duplicate_task", "split_task", "quick_add_task")
		]
		self.assertEqual(
			[(name, args) for name, args, _opts in creates],
			[
				("duplicate_task", {"task": "TASK-1", "date": "2026-10-19"}),
				("split_task", {"task": "TASK-1", "split_date": "2026-10-13", "modified": "m-fresh"}),
				(
					"quick_add_task",
					{
						"project": "PRJ-1",
						"subject": "Fix",
						"date": "2026-10-13",
						"tentative": 1,
						"hours": 3,
						"resource": "RES-1",
					},
				),
			],
		)
		self.assertIsNone(out["quick_add_blank"])
		snaps = [snap for snap, _message in out["pushed"] if snap.get("p6b") in ("created", "split")]
		self.assertEqual(
			[(s["p6b"], s["task"]) for s in snaps],
			[("created", "TASK-NEW-1"), ("split", "TASK-NEW-2"), ("created", "TASK-NEW-1")],
		)
		self.assertEqual(
			snaps[1]["restore"],
			{
				"task": "TASK-1",
				"start": "2026-10-12",
				"end": "2026-10-14",
				"expected_time": 12,
				"crew": [
					{"resource": "RES-1", "hours": None, "is_lead": 1},
					{"resource": "RES-2", "hours": None, "is_lead": 0},
				],
				"modified": "m-new",
			},
		)

	def test_legend_search_and_keys(self):
		out = self.out
		self.assertEqual(out["legend"], ["Keyboard", "Faster scheduling", "Color by"])
		self.assertEqual(out["mp_legend"], ["Keyboard", "Faster scheduling"])
		self.assertIn(["task", "Dig"], out["search_entries"])
		self.assertIn(["project", "Plaza"], out["search_entries"])
		self.assertEqual(out["from_server"][0]["sub"], "Plaza · when(2026-12-01) – when(2026-12-02)")
		self.assertIsNone(out["from_server"][1])
		self.assertEqual(out["keys"], [False, False, False, False])  # the route view keeps its own buttons

	def test_the_visit_menu_and_assign(self):
		out = self.out
		self.assertEqual(
			out["mp_items"],
			[
				"Edit",
				"Assign to…",
				"Move to next free day (Looking…)",
				"--",
				"Site at a glance",
				"Austin's week",
			],
		)
		projected = out["mp_items_projected"]
		self.assertIn("Assign to… [off] (Follows the site's Maintenance Profile)", projected)
		self.assertIn("Move to next free day [off] (Follows the site's Maintenance Profile)", projected)
		self.assertIn("Move to next free day [off] (Not a Planner Resource)", out["mp_items_nores"])
		self.assertEqual(out["mp_items_booking"], [])
		self.assertEqual(out["mp_menu"], ["Everyone's day"])
		# Technicians only (a person with no user, or not on this planner, is not offered), not the lead.
		self.assertEqual(out["mp_assign"]["picks"], ["jesse@x.com"])
		self.assertTrue(out["mp_assign"]["escaped"])

	def test_several_visits(self):
		out = self.out
		self.assertEqual(out["mp_selected"], ["SMR-1", "SMR-BAD"])  # a projected visit is never in it
		self.assertTrue(out["mp_drop_row"])
		self.assertEqual(out["mp_drop_past"], ["Visits can only be moved to today or a later day."])
		self.assertEqual(
			[(m, a["record"], a["date"]) for m, a, _o in out["mp_sent"]],
			[("move_visit", "SMR-1", "2026-10-16"), ("move_visit", "SMR-BAD", "2026-10-17")],
		)
		self.assertIn(
			"<li>Lodge</li>", out["mp_alerts"][-1]
		)  # the one that failed is named; the rest stay moved
		self.assertEqual(out["mp_restored"], "undefined")  # the batch's ask_reason is gone again
		((snap, message),) = out["mp_pushed"]
		self.assertEqual(
			snap,
			{
				"p6b": "many",
				"site": "1 visits",
				"items": [{"record": "SMR-1", "site": "Hotel", "date": "2026-10-14"}],
			},
		)
		self.assertEqual(message, "1 visits moved")
		# Undo moves back only to days that have not passed, with the Undo reason.
		self.assertEqual(
			[(m, a["record"], a["date"], o) for m, a, o in out["mp_undo_sent"]],
			[("move_visit", "SMR-1", "2026-10-14", ["auto_reason", "noun"])],
		)
		self.assertFalse(out["mp_undo_other"])


# ====================================================================== docs and wiring


class TestDocs(unittest.TestCase):
	def test_the_readmes_and_ci(self):
		api_readme = API_README.read_text(encoding="utf-8")
		self.assertIn("planner_actions.py", api_readme)
		for method in ACTIONS_CONTRACT:
			self.assertIn(method, api_readme)
		self.assertIn("planner_actions", api_readme.split("**Tab-indented**")[1].split("Every other file")[0])
		pe = PE_README.read_text(encoding="utf-8")
		self.assertIn("### Faster scheduling (Phase 6B", pe)
		for needle in (
			"p6_menu_providers",
			"p6_selection",
			"p6_send_modules",
			"p6_legend_providers",
			"remove_created_task",
		):
			self.assertIn(needle, pe)
		sm = SM_README.read_text(encoding="utf-8")
		self.assertIn("Faster scheduling (Phase 6B", sm)
		self.assertIn("Add visit here", sm)
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_planner_phase6b -v", ci)
		self.assertLess(
			ci.index("erpnext_enhancements.tests.test_planner_phase6d -v"),
			ci.index("erpnext_enhancements.tests.test_planner_phase6b -v"),
		)


if __name__ == "__main__":
	unittest.main()
