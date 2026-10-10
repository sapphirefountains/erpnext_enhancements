# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Project Planner Phase 4, "tracking": customer jobs only, actuals, the labor forecast, the Crew
Utilization report, and vehicle and equipment booking.

What this pins, because each of them fails quietly:

* **Customer jobs only** (Nik, 2026-10-09). A task counts when its project's type is Design,
  Build, Service, Events, Rent or Delivery, or the project has a Design/Build/Service/Events/
  Delivery value-stream row in either column; internal projects, a blank type with no stream and
  a task with no project are out, a rental crew task is always in. Every reader that lists tasks
  (the engine, the Unscheduled tray, the Overdue tray, the project filter, copy week, actuals)
  carries the same SQL and the same Python re-check.
* **Nobody without a cost role is given money**: not a rate, not a per-person cost and not a
  total, and their pay rates are not even read. The stored budget field sits at permlevel 2,
  because ERPNext's own Project permissions give Desk User read at level 1.
* **No paid Google fan-out**: the forecast, the actuals and the report run the engine with
  ``google=False``.
* **Equipment conflicts** (two firm tasks on one vehicle, a vehicle in the shop, an asset out on
  another job's Asset Booking) come back under ``"Equipment"`` through the same needs_reason path
  as people, including for a task with no crew.

Bench-free: installs its own ``frappe`` stub, runs the real modules, and puts ``sys.modules``
back afterwards.

Run: python -m unittest erpnext_enhancements.tests.test_planner_phase4
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
TRACKING_PATH = APP / "project_enhancements/planner_tracking.py"
DOCTYPES = APP / "project_enhancements/doctype"
REPORT_DIR = APP / "project_enhancements/report/crew_utilization"
FIXTURES = APP / "fixtures"
PATCH_PATH = APP / "patches/grant_labor_forecast_visibility.py"

D = datetime.date
DT = datetime.datetime
TODAY = D(2026, 10, 8)  # a Thursday
NOW = DT(2026, 10, 8, 12, 0, 0)
MON, TUE, WED, THU, FRI, SAT, SUN = (D(2026, 10, 12) + datetime.timedelta(days=i) for i in range(7))

#: The lead's lists (2026-10-09), restated so a change to the engine's constants is deliberate.
CUSTOMER_TYPES = ("Design", "Build", "Service", "Events", "Rent", "Delivery")
CUSTOMER_STREAMS = ("Design", "Build", "Service", "Events", "Delivery")
EXCLUDED_TYPES = (
	"Internal",
	"Group Projects",
	"Overhead",
	"Other",
	"External",
	"Organizational Projects",
	"",
)

PLANNER_ROLES = {
	"System Manager",
	"Projects Manager",
	"Projects User",
	"Maintenance Supervisor",
	"Maintenance User",
}
COST_ROLES = {"System Manager", "Finance Team", "Executive Team", "Estimator", "HR Manager"}

STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.permissions",
	"frappe.model",
	"frappe.model.document",
	"erpnext_enhancements.project_enhancements",
	"erpnext_enhancements.project_enhancements.crew_availability",
	"erpnext_enhancements.project_enhancements.routing",
	"erpnext_enhancements.project_enhancements.planner_tracking",
	"erpnext_enhancements.project_enhancements.budget_rollup",
	"erpnext_enhancements.project_enhancements.report.crew_utilization.crew_utilization",
	"erpnext_enhancements.api.project_planner",
	"erpnext_enhancements.api.maintenance_planner",
	"erpnext_enhancements.patches.grant_labor_forecast_visibility",
)
_saved_modules = {}
_modules_before = set()
frappe = None
engine = None
api = None
tracking = None
rollup = None
report = None
patch = None


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


class _TaskDoc(_Doc):
	"""A Task document: child tables as lists of _Doc, save() bumps modified."""

	def check_permission(self, ptype):
		frappe.calls.append(("check_permission", ptype, self.get("name")))

	def set(self, key, value):
		self[key] = [_Doc(v) for v in value] if isinstance(value, list) else value

	def append(self, key, value):
		row = _Doc(value)
		row["idx"] = len(self.get(key) or []) + 1
		self.setdefault(key, []).append(row)
		return row

	def remove(self, row):
		for value in self.values():
			if isinstance(value, list) and row in value:
				value.remove(row)
				return

	def save(self):
		frappe.saved.append(dict(self))
		self["modified"] = "2026-10-08 10:00:01"
		return self

	def add_comment(self, kind, text):
		frappe.comments.append((self.get("name"), text))


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


def _matches(row, filters):
	for key, want in (filters or {}).items():
		value = row.get(key)
		if isinstance(want, list | tuple) and want:
			op, arg = want[0], want[1] if len(want) > 1 else None
			if op == "in" and value not in arg:
				return False
			if op == "!=" and value == arg:
				return False
			if op == ">=" and (value is None or str(value) < str(arg)):
				return False
			continue
		if value != want:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, pluck=None, as_list=False, **kwargs):
	frappe.queries.append((doctype, {"filters": filters, "fields": fields, **kwargs}))
	rows = frappe.tables.get(doctype, [])
	rows = rows(filters) if callable(rows) else [r for r in rows if _matches(r, filters)]
	if pluck:
		return [row.get(pluck) for row in rows]
	if as_list:
		return [tuple(row.get(f) for f in fields) for row in rows]
	return [_Doc(row) for row in rows]


def _sql(query, values=None, as_dict=False, **kwargs):
	frappe.queries.append(("sql", query, values))
	for marker, rows in frappe.sql_rows.items():
		if marker in query:
			return [_Doc(row) for row in rows]
	return []


def _exists(doctype, name=None):
	if doctype == "Role":
		return name in frappe.site_roles
	if doctype == "DocType":
		return name not in frappe.missing_doctypes
	if doctype == "Custom DocPerm":
		return any(_matches(row, name) for row in frappe.tables.get("Custom DocPerm", []))
	if isinstance(name, str) and doctype in frappe.tables:
		return any(row.get("name") == name for row in frappe.tables[doctype])
	return True


def _reset():
	frappe.roles = ["Projects User"]
	frappe.get_roles = lambda user=None: list(frappe.roles)
	frappe.session.user = "nik@example.com"
	frappe.local.message_log = []
	frappe.tables = {}
	frappe.queries = []
	frappe.sql_rows = {}
	frappe.errors = []
	frappe.calls = []
	frappe.saved = []
	frappe.comments = []
	frappe.cached = {}
	frappe.values = {}
	frappe.missing_doctypes = set()
	frappe.site_roles = set(COST_ROLES) | set(PLANNER_ROLES)
	frappe.custom_perm_setups = []
	frappe.cleared = []
	frappe.log_error = lambda *a, **k: frappe.errors.append((a, k))
	frappe.get_all = _get_all
	frappe.get_list = _get_all
	# A fresh load each time, as from the database.
	frappe.get_doc = lambda doctype, name=None: copy.deepcopy(frappe.docs[(doctype, name)])
	frappe.get_cached_doc = lambda doctype, name=None: (_ for _ in ()).throw(KeyError(doctype))
	frappe.get_meta = lambda doctype: types.SimpleNamespace(has_field=lambda f: True)
	frappe.has_permission = lambda *a, **k: True
	frappe.clear_cache = lambda **k: frappe.cleared.append(k)
	frappe.docs = {}
	frappe.db = types.SimpleNamespace(
		sql=_sql,
		exists=_exists,
		has_column=lambda doctype, column: True,
		get_value=lambda doctype, name, field=None, *a, **k: frappe.values.get((doctype, name, field)),
		get_single_value=lambda doctype, field, *a, **k: None,
	)


def setUpModule():
	global frappe, engine, api, tracking, rollup, report, patch
	_modules_before.update(sys.modules)
	for name in STUBBED:
		_saved_modules[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.getdate = _getdate
	utils.add_days = lambda value, days: _getdate(value) + datetime.timedelta(days=days)
	utils.date_diff = lambda a, b: (_getdate(a) - _getdate(b)).days
	utils.nowdate = lambda: str(TODAY)
	utils.formatdate = lambda value=None, *a, **k: _getdate(value).strftime("%m-%d-%Y")
	utils.now_datetime = lambda: NOW
	utils.flt = lambda value, precision=None: float(value or 0)
	utils.cint = lambda value: int(float(value or 0))
	utils.strip_html_tags = lambda text: re.sub(r"<[^>]+>", "", str(text))
	utils.escape_html = lambda text: str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe._dict = _Doc
	frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	frappe.throw = _throw
	frappe.PermissionError = _PermissionError
	frappe.DoesNotExistError = KeyError
	frappe.session = types.SimpleNamespace(user="nik@example.com")
	frappe.local = types.SimpleNamespace(message_log=[])
	frappe.flags = types.SimpleNamespace()
	frappe.get_traceback = lambda *a, **k: "traceback"

	permissions = types.ModuleType("frappe.permissions")
	permissions.setup_custom_perms = lambda doctype: frappe.custom_perm_setups.append(
		(doctype, len(frappe.tables.get("Custom DocPerm", [])))
	)
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Doc
	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.permissions": permissions,
			"frappe.model": model,
			"frappe.model.document": document,
		}
	)
	_reset()

	engine = importlib.import_module("erpnext_enhancements.project_enhancements.crew_availability")
	tracking = importlib.import_module("erpnext_enhancements.project_enhancements.planner_tracking")
	api = importlib.import_module("erpnext_enhancements.api.project_planner")
	rollup = importlib.import_module("erpnext_enhancements.project_enhancements.budget_rollup")
	report = importlib.import_module(
		"erpnext_enhancements.project_enhancements.report.crew_utilization.crew_utilization"
	)
	patch = importlib.import_module("erpnext_enhancements.patches.grant_labor_forecast_visibility")


def tearDownModule():
	for name in set(sys.modules) - _modules_before:
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements")):
			sys.modules.pop(name, None)
	for name, module in _saved_modules.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def _task(name="TASK-1", start=MON, end=None, **values):
	row = {
		"name": name,
		"subject": f"Subject {name}",
		"project": "PRJ-1",
		"project_type": "Build",
		"planner_stream": 0,
		"status": "Open",
		"exp_start_date": start,
		"exp_end_date": end,
		"expected_time": 0,
		"custom_start_datetime": None,
		"custom_end_datetime": None,
		"custom_rental_booking": None,
		"custom_rental_task_kind": None,
		"custom_crew_size": 0,
		"custom_tentative": 0,
		"color": None,
		"modified": "2026-10-08 09:00:00",
	}
	row.update(values)
	return _Doc(row)


def _json(path):
	return json.loads(path.read_text(encoding="utf-8"))


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


def _all_keys(value):
	"""Every dict key anywhere inside ``value``."""
	if isinstance(value, dict):
		out = set(value)
		for item in value.values():
			out |= _all_keys(item)
		return out
	if isinstance(value, list | tuple):
		out = set()
		for item in value:
			out |= _all_keys(item)
		return out
	return set()


# ====================================================================== customer jobs only


class TestCustomerJobsOnly(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_the_lists_are_niks(self):
		self.assertEqual(engine.PLANNER_PROJECT_TYPES, CUSTOMER_TYPES)
		self.assertEqual(engine.PLANNER_VALUE_STREAMS, CUSTOMER_STREAMS)
		self.assertNotIn("Products", engine.PLANNER_VALUE_STREAMS)
		self.assertEqual(
			engine.PLANNER_SQL_VALUES, {"planner_types": CUSTOMER_TYPES, "planner_streams": CUSTOMER_STREAMS}
		)

	def test_every_customer_type_is_in(self):
		for kind in CUSTOMER_TYPES:
			with self.subTest(kind=kind):
				self.assertTrue(engine.is_planner_job(_task(project_type=kind)))

	def test_every_excluded_type_is_out(self):
		for kind in EXCLUDED_TYPES:
			with self.subTest(kind=kind):
				self.assertFalse(engine.is_planner_job(_task(project_type=kind)))
				self.assertFalse(engine.is_planner_job(_task(project_type=kind or None)))

	def test_a_blank_type_with_a_qualifying_stream_is_in(self):
		self.assertTrue(engine.is_planner_job(_task(project_type="", planner_stream=1)))
		self.assertTrue(engine.is_planner_job(_task(project_type="Internal", planner_stream="1")))
		self.assertFalse(engine.is_planner_job(_task(project_type="", planner_stream=0)))

	def test_no_project_is_out_and_a_rental_crew_task_is_always_in(self):
		self.assertFalse(engine.is_planner_job(_task(project=None, project_type=None)))
		self.assertTrue(
			engine.is_planner_job(_task(project=None, project_type=None, custom_rental_booking="RB-1"))
		)
		self.assertTrue(engine.is_planner_job(_task(project_type="Internal", custom_rental_booking="RB-1")))

	def test_the_stream_sql_reads_both_columns(self):
		type_expr, stream = engine.planner_job_sql("p")
		self.assertEqual(type_expr, "IFNULL(p.project_type, '')")
		self.assertIn("FROM `tabValue Stream` vs", stream)
		self.assertIn("vs.parenttype = 'Project'", stream)
		self.assertIn("vs.parent = p.name", stream)
		self.assertIn("vs.`value_stream` IN %(planner_streams)s", stream)
		# The older column qualifies too.
		self.assertIn("vs.`value_streams` IN %(planner_streams)s", stream)

	def test_a_missing_stream_column_is_left_out_and_none_reads_as_no_stream(self):
		frappe.db.has_column = lambda doctype, column: column != "value_streams"
		self.assertNotIn("value_streams", engine.planner_job_sql("p")[1])
		frappe.db.has_column = lambda doctype, column: doctype != "Value Stream"
		self.assertEqual(engine.planner_job_sql("p")[1], "0")

	def test_read_tasks_filters_in_sql_and_again_in_python(self):
		frappe.sql_rows["FROM `tabTask` t"] = [
			_task("BUILD", project_type="Build", project_status="Active"),
			_task("STREAM", project_type="", planner_stream=1, project_status="Active"),
			# The SQL would not return these; Python has the last word anyway.
			_task("INTERNAL", project_type="Internal", project_status="Active"),
			_task("GROUP", project="PRJ-00580", project_type="Group Projects", project_status="Active"),
			_task("STAGE", project_type="", project_status="Active"),
			_task("NO-PROJECT", project=None, project_type=None, project_status=None),
			_task(
				"RENTAL", project=None, project_type=None, project_status=None, custom_rental_booking="RB-1"
			),
		]
		names = [row["name"] for row in engine.read_tasks(MON, FRI)]
		self.assertEqual(names, ["BUILD", "STREAM", "RENTAL"])
		_marker, query, values = next(q for q in frappe.queries if q[0] == "sql")
		self.assertIn("IFNULL(p.project_type, '') IN %(planner_types)s", query)
		self.assertIn("IFNULL(t.`custom_rental_booking`, '') <> ''", query)
		self.assertIn("AS planner_stream", query)
		self.assertEqual(values["planner_types"], CUSTOMER_TYPES)
		self.assertEqual(values["planner_streams"], CUSTOMER_STREAMS)

	def test_the_trays_and_the_project_filter_carry_the_same_rule(self):
		frappe.sql_rows["INNER JOIN `tabProject` p"] = [
			_task("U-BUILD", start=None, project_title="Vegas"),
			_task("U-INTERNAL", start=None, project="PRJ-00739", project_type="Internal"),
		]
		self.assertEqual([r["name"] for r in api._unscheduled()], ["U-BUILD"])
		query = frappe.queries[-1][1]
		self.assertIn("IN %(planner_types)s", query)
		self.assertIn("tabValue Stream", query)

		frappe.queries.clear()
		frappe.sql_rows = {
			"< %(today)s": [
				_task("O-BUILD", start=D(2026, 10, 1), project_title="Vegas"),
				_task("O-INTERNAL", start=D(2026, 10, 1), project="PRJ-00755", project_type="Group Projects"),
			]
		}
		data = api.get_overdue()
		self.assertEqual(data["total"], 1)
		self.assertEqual(data["projects"][0]["tasks"][0]["name"], "O-BUILD")
		self.assertIn("IN %(planner_types)s", next(q[1] for q in frappe.queries if q[0] == "sql"))

		frappe.queries.clear()
		api._projects({"PRJ-1"})
		_marker, query, values = frappe.queries[-1]
		self.assertIn("(p.status = %(active)s AND (p.name IS NOT NULL AND", query)
		self.assertEqual(values["planner_types"], CUSTOMER_TYPES)

	def test_planner_projects_answers_by_type_or_stream(self):
		frappe.sql_rows["FROM `tabProject` proj"] = [
			{"name": "PRJ-BUILD", "project_type": "Build", "planner_stream": 0},
			{"name": "PRJ-STREAM", "project_type": "", "planner_stream": 1},
			{"name": "PRJ-00739", "project_type": "Internal", "planner_stream": 0},
			{"name": "PRJ-STAGE", "project_type": "", "planner_stream": 0},
		]
		got = engine.planner_projects(["PRJ-BUILD", "PRJ-STREAM", "PRJ-00739", "PRJ-STAGE"])
		self.assertEqual(got, {"PRJ-BUILD", "PRJ-STREAM"})
		self.assertEqual(engine.planner_projects([]), set())

	def test_copy_week_skips_a_task_on_an_internal_project(self):
		frappe.sql_rows["FROM `tabProject` proj"] = [{"name": "PRJ-00739", "project_type": "Internal"}]
		frappe.docs[("Task", "T-INT")] = _TaskDoc(
			_task("T-INT", start="2026-10-12 08:00:00", project="PRJ-00739", project_type=None)
		)
		with mock.patch.object(engine, "preview_batch", lambda changes: {}):
			result = api.copy_week("2026-10-12", "2026-10-19", json.dumps(["T-INT"]))
		self.assertEqual(result["copies"], [])
		self.assertEqual(
			result["skipped"], [{"task": "T-INT", "reason": "Its project is not a customer job."}]
		)


# ====================================================================== actuals (P4.1)


class TestActuals(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_interval_hours_net_of_pauses(self):
		done = {
			"start_time": "2026-10-08 06:00:00",
			"end_time": "2026-10-08 10:00:00",
			"status": "Completed",
			"total_paused_seconds": 1800,
		}
		self.assertEqual(tracking.interval_hours(done, NOW), 3.5)
		running = {"start_time": "2026-10-08 08:00:00", "status": "Open", "total_paused_seconds": 0}
		self.assertEqual(tracking.interval_hours(running, NOW), 4.0)  # to now
		paused = {
			"start_time": "2026-10-08 08:00:00",
			"status": "Paused",
			"last_pause_time": "2026-10-08 09:30:00",
			"total_paused_seconds": 600,
		}
		self.assertAlmostEqual(tracking.interval_hours(paused, NOW), round((5400 - 600) / 3600, 4))
		self.assertEqual(
			tracking.interval_hours({"start_time": "2026-10-08 06:00:00", "status": "Completed"}, NOW), 0.0
		)
		self.assertEqual(tracking.interval_hours({"status": "Open"}, NOW), 0.0)
		backwards = {
			"start_time": "2026-10-08 10:00:00",
			"end_time": "2026-10-08 09:00:00",
			"status": "Completed",
		}
		self.assertEqual(tracking.interval_hours(backwards, NOW), 0.0)

	def test_over_plan_is_more_than_ten_percent_over_on_an_open_task(self):
		self.assertFalse(tracking.is_over_plan(10, 11, "Open"))  # exactly 10% over is not over
		self.assertTrue(tracking.is_over_plan(10, 11.2, "Open"))
		self.assertFalse(tracking.is_over_plan(10, 30, "Completed"))
		self.assertFalse(tracking.is_over_plan(0, 5, "Open"))  # nothing planned: no verdict

	def test_planned_is_the_estimate_else_the_engines_allocation(self):
		self.assertEqual(tracking.planned_total({"expected_time": 12}, {"RES-1": 3}), 12.0)
		self.assertEqual(tracking.planned_total({"expected_time": 0}, {"RES-1": 8, "RES-2": 8}), 16.0)

	def test_actuals_by_task_and_the_summary(self):
		intervals = [
			{
				"task": "T-1",
				"employee": "EMP-1",
				"start_time": "2026-10-07 07:00:00",
				"end_time": "2026-10-07 15:00:00",
				"status": "Completed",
			},
			{"task": "T-1", "employee": "EMP-1", "start_time": "2026-10-08 07:00:00", "status": "Open"},
			{
				"task": "T-1",
				"employee": "EMP-9",
				"start_time": "2026-10-08 08:00:00",
				"end_time": "2026-10-08 10:00:00",
				"status": "Completed",
			},
			{"task": None, "employee": "EMP-1", "start_time": "2026-10-08 08:00:00", "status": "Open"},
		]
		actual = tracking.actuals_by_task(intervals, NOW, {"EMP-1": "RES-1"})
		self.assertEqual(actual, {"T-1": {"RES-1": 13.0, "employee:EMP-9": 2.0}})
		summary = tracking.actuals_summary(
			{"name": "T-1", "expected_time": 12, "status": "Open"},
			{"RES-1": 6, "RES-2": 6},
			actual["T-1"],
			{"RES-1": "Austin", "RES-2": "Lisa", "employee:EMP-9": "Pat Doe"},
			[{"resource": "RES-1", "hours": None, "label": "Austin"}, {"resource": "RES-2", "hours": 4}],
		)
		self.assertEqual(summary["planned"], 12.0)
		self.assertEqual(summary["actual"], 15.0)
		self.assertTrue(summary["over_plan"])
		self.assertEqual(
			summary["by_person"],
			[
				{"resource": "RES-1", "label": "Austin", "planned": 6.0, "actual": 13.0},
				{"resource": "RES-2", "label": "Lisa", "planned": 4.0, "actual": 0.0},
				{"resource": None, "label": "Pat Doe", "planned": 0.0, "actual": 2.0},
			],
		)

	def test_a_card_carries_actuals_equipment_and_its_conflicts(self):
		task = _task("T-1", start=MON, end=TUE, expected_time=10)
		crew = [{"resource": "RES-1", "label": "Austin", "hours": None, "is_lead": True}]
		card = api.build_card(
			task,
			crew,
			{"RES-1": 10},
			[],
			TODAY,
			True,
			equipment=[{"type": "Vehicle", "name": "Truck 2", "label": "Truck 2"}],
			equipment_conflicts=["2026-10-12: Truck 2 is also on T-2 (Dig)"],
			actuals={"RES-1": 9.5, "employee:EMP-9": 2},
		)
		self.assertEqual(card["actual_hours"], 11.5)
		self.assertEqual(card["planned_hours"], 10.0)
		self.assertTrue(card["over_plan"])
		self.assertEqual(card["crew"][0]["actual"], 9.5)
		self.assertEqual(card["equipment"], [{"type": "Vehicle", "name": "Truck 2", "label": "Truck 2"}])
		# A list of sentences on the card (what the page reads); needs_reason keys them by "Equipment".
		self.assertEqual(card["conflicts"], ["2026-10-12: Truck 2 is also on T-2 (Dig)"])
		plain = api.build_card(task, crew, {"RES-1": 10}, [], TODAY, True)
		self.assertEqual(
			(plain["actual_hours"], plain["over_plan"], plain["equipment"], plain["conflicts"]),
			(0.0, False, [], []),
		)

	def test_get_actuals_end_to_end_customer_jobs_only_and_without_google(self):
		frappe.roles = ["Projects User"]
		frappe.tables["Task"] = [
			dict(_task("T-1", start=MON, end=TUE, expected_time=0), is_group=0, is_template=0),
			dict(_task("T-INT", start=MON, project="PRJ-00739"), is_group=0, is_template=0),
		]
		frappe.sql_rows["FROM `tabProject` proj"] = [
			{"name": "PRJ-1", "project_type": "Build"},
			{"name": "PRJ-00739", "project_type": "Internal"},
		]
		frappe.tables["Planner Resource"] = [
			{
				"name": "RES-1",
				"resource_name": "Austin",
				"user": "austin@example.com",
				"employee": "EMP-1",
				"is_active": 1,
			},
		]
		frappe.tables["Task Crew Member"] = [
			{"parent": "T-1", "parenttype": "Task", "resource": "RES-1", "hours": 0}
		]
		frappe.tables["Job Interval"] = [
			{
				"name": "JI-1",
				"task": "T-1",
				"employee": "EMP-1",
				"project": "PRJ-1",
				"start_time": "2026-10-07 07:00:00",
				"end_time": "2026-10-07 17:00:00",
				"status": "Completed",
			},
		]
		seen = []

		def preview(changes, start=None, end=None, google=True):
			seen.append(google)
			return {}, {task.get("name"): {"RES-1": 16.0} for task, _crew in changes}

		with mock.patch.object(engine, "_preview_many", preview):
			result = api.get_actuals(project="PRJ-1")
		self.assertEqual(seen, [False])
		self.assertEqual(list(result), ["T-1"])
		self.assertEqual(result["T-1"]["planned"], 16.0)
		self.assertEqual(result["T-1"]["actual"], 10.0)
		self.assertFalse(result["T-1"]["over_plan"])
		self.assertEqual(
			result["T-1"]["by_person"],
			[{"resource": "RES-1", "label": "Austin", "planned": 16.0, "actual": 10.0}],
		)

	def test_get_actuals_needs_the_planner_gate_and_a_target(self):
		frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			api.get_actuals(tasks=json.dumps(["T-1"]))
		frappe.roles = ["Projects User"]
		with self.assertRaises(_Throw):
			api.get_actuals()


# ====================================================================== rates and the forecast (P4.2)


RATES = {
	"EMP-1": [
		{
			"parent": "EMP-1",
			"effective_from": "2026-01-01",
			"pay_type": "Hourly",
			"hourly_rate": 30,
			"burden_pct": 0,
		},
		{
			"parent": "EMP-1",
			"effective_from": "2026-10-09",
			"pay_type": "Hourly",
			"hourly_rate": 40,
			"burden_pct": 0,
		},
	],
}


class TestRates(unittest.TestCase):
	def test_the_row_in_force_on_the_day(self):
		rows = RATES["EMP-1"]
		self.assertEqual(tracking.rate_on(rows, D(2026, 10, 8)), {"rate": 30.0, "burdened": False})
		self.assertEqual(tracking.rate_on(rows, D(2026, 10, 9)), {"rate": 40.0, "burdened": False})
		self.assertIsNone(tracking.rate_on(rows, D(2025, 12, 31)))  # before any row
		self.assertIsNone(tracking.rate_on([], TODAY))

	def test_a_burden_only_when_one_is_set(self):
		rows = [{"effective_from": "2026-01-01", "pay_type": "Hourly", "hourly_rate": 20, "burden_pct": 25}]
		self.assertEqual(tracking.rate_on(rows, TODAY), {"rate": 25.0, "burdened": True})
		rows[0]["burden_pct"] = None
		self.assertEqual(tracking.rate_on(rows, TODAY), {"rate": 20.0, "burdened": False})

	def test_salaried_uses_the_hourly_equivalent_else_salary_over_2080(self):
		rows = [
			{
				"effective_from": "2026-01-01",
				"pay_type": "Salaried",
				"hourly_equivalent": 50,
				"annual_salary": 999,
			}
		]
		self.assertEqual(tracking.rate_on(rows, TODAY)["rate"], 50.0)
		rows = [{"effective_from": "2026-01-01", "pay_type": "Salaried", "annual_salary": 104000}]
		self.assertEqual(tracking.rate_on(rows, TODAY)["rate"], 50.0)
		self.assertIsNone(tracking.rate_on([{"effective_from": "2026-01-01", "pay_type": "Hourly"}], TODAY))

	def test_today_counts_once(self):
		self.assertEqual(tracking.remaining_today(8, 3), 5.0)
		self.assertEqual(tracking.remaining_today(2, 3), 0.0)
		person = tracking.forecast_person({TODAY: 8, D(2026, 10, 9): 8, D(2026, 10, 7): 8}, {TODAY: 3}, TODAY)
		self.assertEqual(person, {"booked": 13.0, "actual": 3.0})  # no rate_of: no money at all


def _forecast_site():
	"""PRJ-1 (Build): Austin (rates, the change on 10-09), Lisa (no rate), a subcontractor, and Pat
	(clocked, not a Planner Resource, no rate)."""
	frappe.tables["Task"] = [dict(_task("T-1", start=TODAY, end=D(2026, 10, 9)), is_group=0, is_template=0)]
	frappe.tables["Project"] = [{"name": "PRJ-1"}]
	frappe.tables["Planner Resource"] = [
		{
			"name": "RES-1",
			"resource_name": "Austin Healey",
			"employee": "EMP-1",
			"resource_type": "Employee",
			"is_active": 1,
		},
		{
			"name": "RES-SUB",
			"resource_name": "Sub Co",
			"employee": None,
			"resource_type": "Subcontractor",
			"is_active": 1,
		},
	]
	frappe.tables["Employee"] = [{"name": "EMP-3", "employee_name": "Pat Doe"}]
	frappe.tables["Job Interval"] = [
		{
			"name": "JI-1",
			"employee": "EMP-1",
			"project": "PRJ-1",
			"task": "T-1",
			"status": "Completed",
			"start_time": "2026-10-08 06:00:00",
			"end_time": "2026-10-08 09:00:00",
			"total_paused_seconds": 0,
		},
		{
			"name": "JI-2",
			"employee": "EMP-1",
			"project": "PRJ-1",
			"task": "T-0",
			"status": "Completed",
			"start_time": "2026-10-01 07:00:00",
			"end_time": "2026-10-01 15:00:00",
			"total_paused_seconds": 0,
		},
		{
			"name": "JI-3",
			"employee": "EMP-3",
			"project": "PRJ-1",
			"task": "T-0",
			"status": "Completed",
			"start_time": "2026-10-02 07:00:00",
			"end_time": "2026-10-02 09:00:00",
			"total_paused_seconds": 0,
		},
		{
			"name": "JI-4",
			"employee": "EMP-1",
			"project": "PRJ-2",
			"task": "T-X",
			"status": "Completed",
			"start_time": "2026-10-02 07:00:00",
			"end_time": "2026-10-02 09:00:00",
			"total_paused_seconds": 0,
		},
	]
	frappe.tables["Employee Pay Rate"] = [dict(row, parenttype="Employee") for row in RATES["EMP-1"]]

	def cell(*bookings):
		return {"bookings": list(bookings)}

	days = {
		"RES-1": {
			"2026-10-08": cell({"kind": "task", "project": "PRJ-1", "hours": 8, "ref": "T-1"}),
			"2026-10-09": cell(
				{"kind": "task", "project": "PRJ-1", "hours": 8, "ref": "T-1"},
				{"kind": "visit", "project": "PRJ-9", "hours": 2, "ref": "SMR-1"},
				{"kind": "drive", "project": None, "hours": 1},
			),
			"2026-10-12": cell(
				{"kind": "task", "project": "PRJ-1", "hours": 4, "ref": "T-P", "tentative": True}
			),
		},
		"RES-SUB": {"2026-10-09": cell({"kind": "task", "project": "PRJ-1", "hours": 6, "ref": "T-1"})},
	}
	calls = []

	def compute(start, end, resources=None, exclude=(), extra=(), google=True, equipment=False):
		calls.append({"start": start, "end": end, "google": google})
		return {
			"resources": [
				{"name": "RES-1", "label": "Austin Healey", "employee": "EMP-1", "type": "Employee"},
				{"name": "RES-SUB", "label": "Sub Co", "employee": None, "type": "Subcontractor"},
			],
			"days": days,
		}

	return compute, calls


class TestLaborForecast(unittest.TestCase):
	def setUp(self):
		_reset()
		self.compute, self.calls = _forecast_site()

	def _forecast(self):
		with mock.patch.object(engine, "_compute", self.compute):
			return api.get_labor_forecast("PRJ-1")

	def test_a_cost_role_gets_the_money_at_base_rate(self):
		frappe.roles = ["Finance Team"]
		result = self._forecast()
		self.assertTrue(result["can_see_cost"])
		self.assertEqual(self.calls, [{"start": TODAY, "end": D(2026, 10, 9), "google": False}])
		self.assertEqual(result["through"], "2026-10-09")
		# Austin: today 8 booked less 3 clocked = 5, plus 8 tomorrow; Sub Co 6. Pencils and other
		# projects' visits and driving are not this project's labor.
		self.assertEqual(result["booked_hours"], 19.0)
		self.assertEqual(result["actual_hours"], 13.0)  # Austin 3 + 8, Pat 2; PRJ-2 is not counted
		# 5h at $30 today, 8h at $40 from the 9th, 11h clocked at $30.
		self.assertEqual(result["booked_cost"], 470.0)
		self.assertEqual(result["actual_cost"], 330.0)
		self.assertEqual(result["forecast_cost"], 800.0)
		self.assertFalse(result["burdened"])
		self.assertEqual(result["rate_label"], "base rate")
		self.assertEqual(result["no_rate"], ["Pat Doe", "Sub Co"])
		people = {row["label"]: row for row in result["by_person"]}
		self.assertEqual(
			people["Austin Healey"],
			{
				"label": "Austin Healey",
				"booked": 13.0,
				"actual": 11.0,
				"rate": 30.0,
				"cost": 800.0,
				"burdened": False,
				"no_rate": False,
			},
		)
		self.assertEqual(people["Sub Co"]["cost"], None)
		self.assertTrue(people["Sub Co"]["no_rate"])

	def test_anyone_else_gets_hours_and_no_money_anywhere(self):
		frappe.roles = ["Projects User", "Maintenance User"]
		result = self._forecast()
		self.assertFalse(result["can_see_cost"])
		self.assertIsNone(result["burdened"])
		self.assertEqual((result["booked_hours"], result["actual_hours"]), (19.0, 13.0))
		money = {"rate", "cost", "booked_cost", "actual_cost", "forecast_cost", "rate_label", "no_rate"}
		self.assertFalse(_all_keys(result) & money, _all_keys(result) & money)
		for row in result["by_person"]:
			self.assertEqual(set(row), {"label", "booked", "actual"})
		# The wage table is not even read for them.
		self.assertNotIn("Employee Pay Rate", [q[0] for q in frappe.queries])

	def test_a_one_person_project_gives_a_non_cost_role_no_total(self):
		frappe.roles = ["Projects Manager"]
		frappe.tables["Job Interval"] = frappe.tables["Job Interval"][:2]
		result = self._forecast()
		self.assertNotIn("forecast_cost", result)
		self.assertFalse(_all_keys(result) & {"cost", "booked_cost", "actual_cost", "forecast_cost"})

	def test_a_cost_role_the_site_does_not_have_grants_nothing(self):
		frappe.site_roles.discard("Estimator")
		frappe.roles = ["Estimator", "Projects User"]
		self.assertFalse(self._forecast()["can_see_cost"])
		frappe.site_roles.add("Estimator")
		self.assertTrue(self._forecast()["can_see_cost"])

	def test_the_burdened_label_only_when_every_rate_is_burdened(self):
		frappe.roles = ["HR Manager"]
		for row in frappe.tables["Employee Pay Rate"]:
			row["burden_pct"] = 20
		result = self._forecast()
		self.assertTrue(result["burdened"])
		self.assertEqual(result["rate_label"], "burdened rate")

	def test_the_gate(self):
		frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			self._forecast()
		frappe.roles = ["Finance Team"]  # a cost role without a planner role may still ask
		self.assertTrue(self._forecast()["can_see_cost"])
		frappe.session.user = "Administrator"
		frappe.roles = []
		self.assertTrue(self._forecast()["can_see_cost"])

	def test_the_cost_roles_are_the_specs(self):
		self.assertEqual(set(tracking.COST_ROLES), COST_ROLES)
		self.assertEqual(api.FORECAST_ROLES, PLANNER_ROLES | COST_ROLES)


class TestBudgetRollup(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_only_the_labor_line_gets_a_forecast_computed_with_cost(self):
		doc = _ProjectDoc([_Doc(category="Materials"), _Doc(category="Labor")])
		calls = []
		with mock.patch.object(
			tracking,
			"labor_forecast",
			lambda project, with_cost=False: calls.append((project, with_cost)) or {"forecast_cost": 812.5},
		):
			rollup.refresh_labor_forecast(doc)
		self.assertEqual(calls, [("PRJ-1", True)])
		self.assertEqual(doc.custom_budget_lines[1].labor_forecast, 812.5)
		self.assertNotIn("labor_forecast", doc.custom_budget_lines[0])
		# A forecast is not spend: actual and coverage are not touched.
		self.assertNotIn("actual_amount", doc.custom_budget_lines[1])

	def test_no_labor_line_no_work_and_a_failure_is_logged_not_raised(self):
		with mock.patch.object(tracking, "labor_forecast", lambda *a, **k: self.fail("computed")):
			rollup.refresh_labor_forecast(_ProjectDoc([_Doc(category="Materials")]))
		doc = _ProjectDoc([_Doc(category="Labor", labor_forecast=5)])
		with mock.patch.object(tracking, "labor_forecast", lambda *a, **k: 1 / 0):
			rollup.refresh_labor_forecast(doc)
		self.assertEqual(doc.custom_budget_lines[0].labor_forecast, 5)
		self.assertEqual(frappe.errors[0][1]["title"], "Labor forecast rollup failed")

	def test_refresh_calls_it_last(self):
		source = (APP / "project_enhancements/budget_rollup.py").read_text(encoding="utf-8")
		body = source.split("def refresh(", 1)[1].split("\ndef ", 1)[0]
		self.assertTrue(body.rstrip().endswith("refresh_labor_forecast(doc)"))


class _ProjectDoc(_Doc):
	def __init__(self, lines):
		super().__init__(name="PRJ-1", custom_budget_lines=lines)


class TestForecastVisibility(unittest.TestCase):
	"""The stored figure is a wage when one person is on the job."""

	def setUp(self):
		_reset()
		self.line = _json(DOCTYPES / "project_budget_line/project_budget_line.json")
		self.field = next(f for f in self.line["fields"] if f["fieldname"] == "labor_forecast")

	def test_the_field_is_computed_currency_at_permlevel_2(self):
		self.assertEqual(self.field["fieldtype"], "Currency")
		self.assertEqual(self.field["read_only"], 1)
		self.assertIn("labor_forecast", self.line["field_order"])
		# Not 1: ERPNext v16's own Project permissions give Desk User (every desk user) read at
		# permlevel 1, and a child field answers to its parent's permissions.
		self.assertEqual(self.field["permlevel"], 2)
		self.assertIn("base rate", self.field["description"])

	def test_the_json_is_newer_than_its_site_row(self):
		self.assertGreater(self.line["modified"], "2026-09-15 09:05:00.000000")

	def test_the_patch_grants_exactly_the_cost_roles_read_only(self):
		self.assertEqual(set(patch.ROLES), COST_ROLES)
		self.assertEqual(patch.PERMLEVEL, 2)
		self.assertEqual(patch.PARENT, "Project")
		inserted = []
		frappe.get_doc = lambda values: types.SimpleNamespace(
			insert=lambda ignore_permissions=False: (
				inserted.append(values),
				frappe.tables.setdefault("Custom DocPerm", []).append(values),
			)
		)
		frappe.site_roles.discard("Estimator")
		self.assertEqual(patch.grant_labor_forecast_visibility(), 4)
		# setup_custom_perms first, before any row exists, so Project keeps its standard rows.
		self.assertEqual(frappe.custom_perm_setups, [("Project", 0)])
		self.assertEqual({row["role"] for row in inserted}, COST_ROLES - {"Estimator"})
		for row in inserted:
			self.assertEqual(
				(row["permlevel"], row["read"], row["write"], row["parent"]), (2, 1, 0, "Project")
			)
		self.assertEqual(frappe.cleared, [{"doctype": "Project"}])
		# Safe twice.
		self.assertEqual(patch.grant_labor_forecast_visibility(), 0)

	def test_the_patch_never_raises(self):
		frappe.db.exists = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone"))
		patch.execute()
		self.assertEqual(frappe.errors[0][1]["title"], "grant_labor_forecast_visibility")

	def test_the_patch_is_registered_after_model_sync(self):
		lines = [line.strip() for line in (APP / "patches.txt").read_text(encoding="utf-8").splitlines()]
		entry = "erpnext_enhancements.patches.grant_labor_forecast_visibility"
		self.assertEqual(lines.count(entry), 1)
		self.assertGreater(lines.index(entry), lines.index("[post_model_sync]"))

	def test_no_custom_docperm_fixture_restates_project(self):
		parents = {row["parent"] for row in _json(FIXTURES / "custom_docperm.json")}
		self.assertNotIn("Project", parents)


class TestNoGoogle(unittest.TestCase):
	"""The heavy reads never ask Google for a drive time."""

	def setUp(self):
		_reset()

	def test_the_engine_passes_google_false_to_the_router(self):
		seen = []

		def plan_routes(day_bookings, settings, **options):
			seen.append(options)
			return {}

		person = {
			"name": "RES-1",
			"resource_name": "Austin",
			"user": "a@x",
			"employee": "EMP-1",
			"resource_type": "Employee",
			"resource_group": "Field",
		}
		with (
			mock.patch.object(engine, "get_settings", lambda: dict(engine.DEFAULT_SETTINGS)),
			mock.patch.object(engine, "_read_resources", lambda names=None: ([_Doc(person)], {})),
			mock.patch.object(engine, "read_tasks", lambda start, end: [_task("T-1", start=MON)]),
			mock.patch.object(engine, "read_crews", lambda names: ({"T-1": [{"resource": "RES-1"}]}, {})),
			mock.patch.object(engine, "_read_holidays", lambda *a: {}),
			mock.patch.object(engine, "_read_time_off", lambda *a: {}),
			mock.patch.object(engine, "_read_restrictions", lambda *a: {}),
			mock.patch.object(engine, "_read_visits", lambda *a: []),
			mock.patch.object(engine, "_read_travel", lambda *a: []),
			mock.patch.object(engine.routing, "plan_routes", plan_routes),
		):
			engine._compute(MON, FRI, google=False)
		self.assertEqual(seen, [{"google": False}])

	def test_the_report_and_the_forecast_compute_with_google_off(self):
		calls = []

		def compute(start, end, resources=None, exclude=(), extra=(), google=True, equipment=False):
			calls.append(google)
			return {"resources": [], "days": {}}

		frappe.tables["Project"] = [{"name": "PRJ-1"}]
		with mock.patch.object(engine, "_compute", compute):
			report.execute({"from_date": "2026-10-01", "to_date": "2026-10-31"})
			tracking.labor_forecast("PRJ-1", with_cost=True)
		self.assertEqual(calls, [False, False])


# ====================================================================== the report (P4.3)


def _cell(capacity, booked, *bookings):
	return {"capacity": capacity, "booked": booked, "bookings": list(bookings)}


class TestCrewUtilization(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_weeks_follow_the_sites_first_weekday_and_months_are_clipped(self):
		sunday = api.WEEKDAY_NUMBERS["Sunday"]
		weeks = report.periods_for(D(2026, 10, 1), D(2026, 10, 14), "Week", sunday)
		self.assertEqual(
			[(str(p["start"]), str(p["end"])) for p in weeks],
			[
				("2026-10-01", "2026-10-03"),
				("2026-10-04", "2026-10-10"),
				("2026-10-11", "2026-10-14"),
			],
		)
		self.assertEqual(weeks[0]["key"], "2026-09-27")
		months = report.periods_for(D(2026, 10, 20), D(2026, 11, 5), "Month", sunday)
		self.assertEqual(
			[(p["label"], str(p["start"]), str(p["end"])) for p in months],
			[
				("Oct 2026", "2026-10-20", "2026-10-31"),
				("Nov 2026", "2026-11-01", "2026-11-05"),
			],
		)

	def test_aggregate_splits_booked_hours_and_leaves_pencils_out(self):
		people = [{"name": "RES-1", "label": "Austin", "group": "Field"}, {"name": "RES-2", "label": "Lisa"}]
		days = {
			"RES-1": {
				"2026-10-12": _cell(
					8,
					9,
					{"kind": "task", "hours": 5},
					{"kind": "visit", "hours": 2},
					{"kind": "drive", "hours": 2},
					{"kind": "task", "hours": 3, "tentative": True},
				),
				"2026-10-13": _cell(8, 8, {"kind": "rental", "hours": 3}, {"kind": "travel", "hours": 5}),
			},
			"RES-2": {"2026-10-12": _cell(0, 0)},
		}
		actual = {"RES-1": {MON: 6.0, TUE: 7.0}}
		other = {"RES-1": {MON: 1.5}}
		periods = [{"key": "w", "label": "Week of Oct 11", "start": MON, "end": TUE}]
		rows = report.aggregate(people, days, actual, other, periods)
		austin, lisa = rows
		self.assertEqual(
			{
				k: austin[k]
				for k in (
					"capacity",
					"booked",
					"project",
					"maintenance",
					"rental",
					"travel",
					"drive",
					"actual",
					"other_clocked",
					"utilization",
					"actual_pct",
				)
			},
			{
				"capacity": 16.0,
				"booked": 17.0,
				"project": 5.0,
				"maintenance": 2.0,
				"rental": 3.0,
				"travel": 5.0,
				"drive": 2.0,
				"actual": 13.0,
				"other_clocked": 1.5,
				"utilization": 106.2,
				"actual_pct": 81.2,
			},
		)
		# No capacity: a percentage of nothing is blank, not 0%.
		self.assertIsNone(lisa["utilization"])
		self.assertIsNone(lisa["actual_pct"])
		chart = report.chart(rows)
		self.assertEqual(chart["type"], "bar")
		self.assertEqual(chart["barOptions"], {"stacked": 1})
		self.assertEqual(chart["data"]["labels"], ["Austin", "Lisa"])
		self.assertEqual(
			[d["name"] for d in chart["data"]["datasets"]],
			["Project", "Maintenance", "Rental", "Travel", "Drive"],
		)

	def test_clocked_time_on_customer_jobs_is_actual_and_the_rest_is_other(self):
		intervals = [
			{
				"employee": "EMP-1",
				"project": "PRJ-1",
				"start_time": "2026-10-12 07:00:00",
				"end_time": "2026-10-12 11:00:00",
				"status": "Completed",
			},
			{
				"employee": "EMP-1",
				"project": "PRJ-00580",
				"start_time": "2026-10-12 12:00:00",
				"end_time": "2026-10-12 14:00:00",
				"status": "Completed",
			},
			{
				"employee": "EMP-1",
				"project": None,
				"task": "T-R",
				"start_time": "2026-10-13 07:00:00",
				"end_time": "2026-10-13 08:00:00",
				"status": "Completed",
			},
			{
				"employee": "EMP-1",
				"project": "PRJ-1",
				"start_time": "2026-11-12 07:00:00",
				"end_time": "2026-11-12 08:00:00",
				"status": "Completed",
			},
			{
				"employee": "EMP-7",
				"project": "PRJ-1",
				"start_time": "2026-10-12 07:00:00",
				"end_time": "2026-10-12 08:00:00",
				"status": "Completed",
			},
		]
		actual, other = report.clocked(
			intervals, MON, SUN, {"EMP-1": "RES-1"}, {"RES-1"}, {"PRJ-1"}, {"T-R"}, NOW
		)
		self.assertEqual({k: dict(v) for k, v in actual.items()}, {"RES-1": {MON: 4.0, TUE: 1.0}})
		self.assertEqual({k: dict(v) for k, v in other.items()}, {"RES-1": {MON: 2.0}})

	def test_execute_end_to_end(self):
		frappe.roles = ["Maintenance User"]
		frappe.tables["Job Interval"] = []

		def compute(start, end, resources=None, exclude=(), extra=(), google=True, equipment=False):
			self.assertFalse(google)
			self.assertEqual((start, end), (D(2026, 10, 1), D(2026, 10, 31)))  # this month by default
			return {
				"resources": [{"name": "RES-1", "label": "Austin", "group": "Field", "employee": "EMP-1"}],
				"days": {"RES-1": {"2026-10-12": _cell(8, 4, {"kind": "task", "hours": 4})}},
			}

		with mock.patch.object(engine, "_compute", compute):
			columns, rows, message, chart = report.execute({"granularity": "Month"})
		self.assertIsNone(message)
		self.assertEqual([r["period"] for r in rows], ["Oct 2026"])
		self.assertEqual((rows[0]["capacity"], rows[0]["project"], rows[0]["utilization"]), (8.0, 4.0, 50.0))
		self.assertIn("utilization", [c["fieldname"] for c in columns])
		self.assertEqual(chart["data"]["datasets"][0]["values"], [4.0])

	def test_execute_refuses_a_long_range_and_the_gate(self):
		with self.assertRaises(_Throw):
			report.execute({"from_date": "2026-01-01", "to_date": "2026-12-31"})
		frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			report.execute({})

	def test_the_report_record(self):
		data = _json(REPORT_DIR / "crew_utilization.json")
		self.assertEqual(
			(data["name"], data["report_name"], data["report_type"], data["module"]),
			("Crew Utilization", "Crew Utilization", "Script Report", "Project Enhancements"),
		)
		self.assertEqual({r["role"] for r in data["roles"]}, PLANNER_ROLES)
		self.assertEqual(data["is_standard"], "Yes")
		js = (REPORT_DIR / "crew_utilization.js").read_text(encoding="utf-8")
		self.assertIn('frappe.query_reports["Crew Utilization"]', js)
		for name in ("from_date", "to_date", "granularity", "group", "resource"):
			self.assertIn(f'fieldname: "{name}"', js)


# ====================================================================== equipment (P4.4)


VEHICLE = {"equipment_type": "Vehicle", "vehicle": "Truck 2"}
FOUNTAIN = {"equipment_type": "Asset", "asset": "ACC-ASS-1"}


class TestEquipmentPure(unittest.TestCase):
	def test_refs_and_normalizing(self):
		self.assertEqual(engine.equipment_ref(VEHICLE), ("Vehicle", "Truck 2"))
		self.assertEqual(engine.equipment_ref(FOUNTAIN), ("Asset", "ACC-ASS-1"))
		self.assertEqual(engine.equipment_ref({"vehicle": "Truck 2"}), ("Vehicle", "Truck 2"))
		self.assertEqual(engine.equipment_ref({"type": "Asset", "name": "A-1"}), ("Asset", "A-1"))
		self.assertIsNone(engine.equipment_ref({"equipment_type": "Vehicle", "asset": "A-1"}))
		self.assertIsNone(engine.equipment_ref({"equipment_type": "Boat", "vehicle": "X"}))
		self.assertEqual(
			engine.task_equipment([VEHICLE, dict(VEHICLE), FOUNTAIN], {("Asset", "ACC-ASS-1"): "Fountain A"}),
			[
				{"type": "Vehicle", "name": "Truck 2", "label": "Truck 2"},
				{"type": "Asset", "name": "ACC-ASS-1", "label": "Fountain A"},
			],
		)

	def test_two_firm_tasks_on_one_vehicle_and_a_pencil_that_is_not(self):
		tasks = [
			_task("T-1", start=MON, end=WED),
			_task("T-2", start=TUE, subject="Dig"),
			_task("T-P", start=MON, custom_tentative=1),
		]
		truck = [{"type": "Vehicle", "name": "Truck 2", "label": "Truck 2"}]
		got = engine.equipment_conflicts(tasks, {"T-1": truck, "T-2": truck, "T-P": truck}, MON, FRI)
		self.assertEqual(
			got,
			{
				"T-1": ["2026-10-13: Truck 2 is also on T-2 (Dig)"],
				"T-2": ["2026-10-13: Truck 2 is also on T-1 (Subject T-1)"],
			},
		)
		# Outside the range nothing is judged.
		self.assertEqual(engine.equipment_conflicts(tasks, {"T-1": truck, "T-2": truck}, WED, FRI), {})

	def test_a_vehicle_in_the_shop_and_an_asset_out_on_another_job(self):
		tasks = [_task("T-1", start=MON, end=TUE, project="PRJ-1")]
		equipment = {
			"T-1": [
				{"type": "Vehicle", "name": "Truck 2", "label": "Truck 2"},
				{"type": "Asset", "name": "ACC-ASS-1", "label": "Fountain A"},
			]
		}
		bookings = [
			{
				"name": "AB-1",
				"asset": "ACC-ASS-1",
				"from_datetime": "2026-10-13 09:00:00",
				"to_datetime": "2026-10-20 17:00:00",
				"booking_type": "Rental",
				"project": "PRJ-9",
			},
			# Booked for this very job: not a clash.
			{
				"name": "AB-2",
				"asset": "ACC-ASS-1",
				"from_datetime": "2026-10-12 09:00:00",
				"to_datetime": None,
				"booking_type": "Rental",
				"project": "PRJ-1",
			},
		]
		got = engine.equipment_conflicts(tasks, equipment, MON, FRI, {"Truck 2": "In Shop"}, bookings)
		self.assertEqual(
			got,
			{
				"T-1": [
					"Truck 2 is In Shop",
					"2026-10-13: Fountain A is booked on Asset Booking AB-1 (Rental)",
				]
			},
		)
		self.assertEqual(
			engine.equipment_conflicts(tasks, equipment, MON, FRI, {"Truck 2": "Active"}, []), {}
		)

	def test_the_board_and_its_day_states(self):
		tasks = [
			_task("T-1", start=MON),
			_task("T-2", start=MON),
			_task("T-P", start=TUE, custom_tentative=1),
		]
		truck = [{"type": "Vehicle", "name": "Truck 2", "label": "Truck 2"}]
		board = engine.equipment_board(tasks, {"T-1": truck, "T-2": truck, "T-P": truck}, MON, FRI)
		monday = board[("Vehicle", "Truck 2")][MON]
		self.assertEqual([t["task"] for t in monday["tasks"]], ["T-1", "T-2"])
		self.assertEqual(api.equipment_day_state(monday, "Active"), "double_booked")
		self.assertEqual(api.equipment_day_state(board[("Vehicle", "Truck 2")][TUE], "Active"), "in_use")
		self.assertEqual(
			api.equipment_day_state(board[("Vehicle", "Truck 2")][TUE], "In Shop"), "unavailable"
		)


class _EquipmentSite:
	"""T-OTHER (firm, Monday) already has Truck 2; nobody is on the planner."""

	def __enter__(self):
		frappe.tables["Task Equipment"] = [
			{
				"parent": "T-OTHER",
				"parenttype": "Task",
				"idx": 1,
				"equipment_type": "Vehicle",
				"vehicle": "Truck 2",
			},
		]
		frappe.tables["Fleet Vehicle"] = [
			{"name": "Truck 2", "vehicle_name": "Truck 2", "status": "Active"},
			{"name": "Van 1", "vehicle_name": "Van 1", "status": "In Shop"},
			{"name": "Old 3", "vehicle_name": "Old 3", "status": "Retired"},
		]
		frappe.tables["Asset"] = []
		frappe.tables["Asset Booking"] = []
		self.patches = [
			mock.patch.object(engine, "get_settings", lambda: dict(engine.DEFAULT_SETTINGS)),
			mock.patch.object(engine, "_read_resources", lambda names=None: ([], {})),
			mock.patch.object(
				engine, "read_tasks", lambda start, end: [_task("T-OTHER", start=MON, subject="Dig")]
			),
			mock.patch.object(engine, "read_crews", lambda names: ({}, {})),
		]
		for p in self.patches:
			p.start()
		return self

	def __exit__(self, *exc):
		for p in self.patches:
			p.stop()


class TestEquipmentConflictsThroughThePreview(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_crewless_task_with_the_same_truck_needs_a_reason(self):
		with _EquipmentSite():
			got = engine.preview_conflicts(dict(_task("T-NEW", start=MON), equipment=[VEHICLE]), [])
			self.assertEqual(got, {"Equipment": ["2026-10-12: Truck 2 is also on T-OTHER (Dig)"]})
			# The same task without the truck, or on another day, has nothing to say.
			self.assertEqual(engine.preview_conflicts(dict(_task("T-NEW", start=MON), equipment=[]), []), {})
			self.assertEqual(
				engine.preview_conflicts(dict(_task("T-NEW", start=TUE), equipment=[VEHICLE]), []), {}
			)
			# A pencil never needs a reason.
			pencil = dict(_task("T-NEW", start=MON, custom_tentative=1), equipment=[VEHICLE])
			self.assertEqual(engine.preview_conflicts(pencil, []), {})
			# A vehicle in the shop.
			self.assertEqual(
				engine.preview_conflicts(
					dict(_task("T-NEW", start=TUE), equipment=[{"vehicle": "Van 1"}]), []
				),
				{"Equipment": ["Van 1 is In Shop"]},
			)

	def test_save_task_asks_for_a_reason_then_saves_the_rows(self):
		frappe.roles = ["Projects Manager"]
		frappe.docs[("Task", "T-NEW")] = _TaskDoc(
			dict(
				_task("T-NEW", start="2026-10-12 08:00:00"),
				doctype="Task",
				custom_crew=[],
				custom_equipment=[],
			)
		)
		frappe.tables["Planner Resource"] = []
		frappe.values[("Project", "PRJ-1", "project_name")] = "Vegas"
		with _EquipmentSite():
			first = api.save_task("T-NEW", "2026-10-08 09:00:00", equipment=json.dumps([VEHICLE]))
			self.assertEqual(
				first,
				{
					"needs_reason": True,
					"conflicts": {"Equipment": ["2026-10-12: Truck 2 is also on T-OTHER (Dig)"]},
				},
			)
			self.assertEqual(frappe.saved, [])
			done = api.save_task(
				"T-NEW", "2026-10-08 09:00:00", equipment=json.dumps([VEHICLE]), reason="Two trips"
			)
		self.assertEqual(len(frappe.saved), 1)
		self.assertEqual(
			[
				(r["equipment_type"], r["vehicle"], r["asset"], r["label"])
				for r in frappe.saved[0]["custom_equipment"]
			],
			[("Vehicle", "Truck 2", None, "Truck 2")],
		)
		self.assertIn("Two trips", frappe.comments[0][1])
		self.assertEqual(
			done["card"]["equipment"], [{"type": "Vehicle", "name": "Truck 2", "label": "Truck 2"}]
		)
		self.assertEqual(done["conflicts"], {"Equipment": ["2026-10-12: Truck 2 is also on T-OTHER (Dig)"]})

	def test_save_task_refuses_equipment_that_does_not_exist(self):
		frappe.docs[("Task", "T-NEW")] = _TaskDoc(
			dict(
				_task("T-NEW", start="2026-10-12 08:00:00"),
				doctype="Task",
				custom_crew=[],
				custom_equipment=[],
			)
		)
		with _EquipmentSite(), self.assertRaises(_Throw):
			api.save_task("T-NEW", "2026-10-08 09:00:00", equipment=json.dumps([{"vehicle": "Nope"}]))
		with _EquipmentSite(), self.assertRaises(_Throw):
			api.save_task("T-NEW", "2026-10-08 09:00:00", equipment=json.dumps([{"equipment_type": "Asset"}]))

	def test_get_equipment_lists_the_fleet_and_each_vehicles_days(self):
		frappe.roles = ["Maintenance User"]
		with _EquipmentSite():
			result = api.get_equipment("2026-10-11", "2026-10-17")
		self.assertEqual(
			[(e["name"], e["status"]) for e in result["equipment"]],
			[("Truck 2", "Active"), ("Van 1", "In Shop")],
		)
		truck = result["equipment"][0]
		self.assertEqual(list(truck["days"]), ["2026-10-12"])
		self.assertEqual(truck["days"]["2026-10-12"]["state"], "in_use")
		self.assertEqual(truck["days"]["2026-10-12"]["tasks"][0]["task"], "T-OTHER")
		self.assertEqual(result["equipment"][1]["days"], {})
		self.assertEqual(result["conflicts"], {})

	def test_the_draft_payload_carries_equipment(self):
		self.assertIn("equipment", api.DRAFT_KEYS)
		self.assertEqual(
			api.equipment_payload([_Doc(VEHICLE), _Doc(FOUNTAIN)]),
			[
				{"equipment_type": "Vehicle", "vehicle": "Truck 2"},
				{"equipment_type": "Asset", "asset": "ACC-ASS-1"},
			],
		)


class TestValidateEquipment(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.tables["Fleet Vehicle"] = [{"name": "Truck 2", "vehicle_name": "Truck 2", "status": "Active"}]
		frappe.tables["Asset"] = [{"name": "ACC-ASS-1", "asset_name": "Fountain A"}]

	def test_dedupes_clears_the_other_link_and_fills_the_label(self):
		doc = _TaskDoc(
			name="T-1",
			custom_equipment=[
				_Doc(idx=1, equipment_type="Vehicle", vehicle="Truck 2", asset="ACC-ASS-1"),
				_Doc(idx=2, equipment_type="Asset", vehicle=None, asset="ACC-ASS-1"),
				_Doc(idx=3, equipment_type="Vehicle", vehicle="Truck 2", asset=None),
			],
		)
		tracking.validate_equipment(doc)
		self.assertEqual(
			[(r.equipment_type, r.vehicle, r.asset, r.label) for r in doc.custom_equipment],
			[("Vehicle", "Truck 2", None, "Truck 2"), ("Asset", None, "ACC-ASS-1", "Fountain A")],
		)

	def test_a_row_naming_nothing_is_an_error_and_no_table_is_fine(self):
		with self.assertRaises(_Throw):
			tracking.validate_equipment(
				_TaskDoc(name="T-1", custom_equipment=[_Doc(idx=1, equipment_type="Asset")])
			)
		tracking.validate_equipment(types.SimpleNamespace(name="T-1"))  # before the field exists


class TestEquipmentDefinitions(unittest.TestCase):
	def test_the_child_doctype(self):
		data = _json(DOCTYPES / "task_equipment/task_equipment.json")
		self.assertEqual(
			(data["name"], data["module"], data["istable"], data["custom"]),
			("Task Equipment", "Project Enhancements", 1, 0),
		)
		self.assertEqual(data["field_order"], [f["fieldname"] for f in data["fields"]])
		fields = {f["fieldname"]: f for f in data["fields"]}
		self.assertEqual(
			(fields["equipment_type"]["fieldtype"], fields["equipment_type"]["options"]),
			("Select", "Vehicle\nAsset"),
		)
		self.assertEqual(fields["vehicle"]["options"], "Fleet Vehicle")
		self.assertEqual(fields["vehicle"]["depends_on"], "eval:doc.equipment_type=='Vehicle'")
		self.assertEqual(fields["asset"]["options"], "Asset")
		self.assertEqual(fields["asset"]["depends_on"], "eval:doc.equipment_type=='Asset'")
		self.assertEqual((fields["label"]["fieldtype"], fields["label"]["read_only"]), ("Data", 1))
		self.assertEqual(data["permissions"], [])
		tree = ast.parse((DOCTYPES / "task_equipment/task_equipment.py").read_text(encoding="utf-8"))
		self.assertIn("TaskEquipment", {n.name for n in tree.body if isinstance(n, ast.ClassDef)})
		self.assertTrue((DOCTYPES / "task_equipment/__init__.py").exists())

	def test_the_task_fields_follow_the_crew_section(self):
		rows = {r["name"]: r for r in _json(FIXTURES / "custom_field.json")}
		template = set(rows["Task-custom_crew"])
		section, table = rows["Task-custom_equipment_section"], rows["Task-custom_equipment"]
		self.assertEqual(
			(section["fieldtype"], section["label"], section["insert_after"]),
			("Section Break", "Equipment", "custom_crew"),
		)
		self.assertEqual(
			(table["fieldtype"], table["options"], table["insert_after"]),
			("Table", "Task Equipment", "custom_equipment_section"),
		)
		for row in (section, table):
			self.assertEqual(set(row), template)
			self.assertEqual(
				(row["dt"], row["module"], row["is_system_generated"]), ("Task", "ERPNext Enhancements", 0)
			)

	def test_the_task_validate_hook(self):
		tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
		doc_events = next(
			ast.literal_eval(n.value)
			for n in tree.body
			if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "doc_events"
		)
		self.assertIn(
			"erpnext_enhancements.project_enhancements.planner_tracking.validate_equipment",
			doc_events["Task"]["validate"],
		)


class TestNightlyLaborForecastRefresh(unittest.TestCase):
	"""``planner_tracking.refresh_labor_forecasts``: the figure stored on the Labor line stays fresh
	without anybody saving the Project."""

	JOB = "erpnext_enhancements.project_enhancements.planner_tracking.refresh_labor_forecasts"

	def setUp(self):
		_reset()
		self.lines = []  # the "database": {line, project, status, category, parentfield, labor_forecast}
		self.writes = []
		self.commits = []
		self.rollbacks = []
		self.queries = []
		self.table_exists = True

		def sql(query, values=None, as_dict=False, **kwargs):
			# Honors the bound parameters the way the database would, so a wrong status list or
			# category in the query is a failing test and not a silent pass.
			self.queries.append((query, values))
			rows = [
				_Doc(line=r["line"], project=r["project"], labor_forecast=r.get("labor_forecast"))
				for r in self.lines
				if r["category"] == values["category"]
				and r.get("parentfield", "custom_budget_lines") == values["field"]
				and (r.get("status") or "") not in values["closed"]
			]
			return rows

		self.patches = [
			mock.patch.object(frappe.db, "sql", sql),
			mock.patch.object(frappe.db, "table_exists", lambda dt: self.table_exists, create=True),
			mock.patch.object(
				frappe.db,
				"set_value",
				lambda *a, **k: self.writes.append((a, k)),
				create=True,
			),
			mock.patch.object(frappe.db, "commit", lambda: self.commits.append(1), create=True),
			mock.patch.object(frappe.db, "rollback", lambda: self.rollbacks.append(1), create=True),
			mock.patch.object(frappe, "logger", lambda *a, **k: mock.Mock(), create=True),
		]
		for patcher in self.patches:
			patcher.start()
			self.addCleanup(patcher.stop)

		def no_save(*a, **k):
			raise AssertionError("the nightly job must never load or save a Project")

		frappe.get_doc = no_save

	def _line(self, project, category="Labor", status="Open", stored=0, name=None):
		self.lines.append(
			{
				"line": name or f"{project}-{category}",
				"project": project,
				"category": category,
				"status": status,
				"labor_forecast": stored,
			}
		)

	def _run(self, costs):
		"""Run the job with ``labor_forecast`` answering ``costs[project]`` (an exception raises)."""

		def forecast(project, with_cost=False, now=None):
			self.assertTrue(with_cost)
			value = costs[project]
			if isinstance(value, Exception):
				raise value
			return {"forecast_cost": value}

		with mock.patch.object(tracking, "labor_forecast", forecast):
			return tracking.refresh_labor_forecasts()

	def test_it_writes_only_the_values_that_changed_and_never_bumps_modified(self):
		self._line("PRJ-1", stored=100)
		self._line("PRJ-2", stored=250.5)
		self._line("PRJ-3", stored=None)
		summary = self._run({"PRJ-1": 100.0, "PRJ-2": 300.25, "PRJ-3": 0})
		self.assertEqual(summary, {"checked": 3, "updated": 1, "failed": 0, "skipped": 0})
		self.assertEqual(
			self.writes,
			[(("Project Budget Line", "PRJ-2-Labor", "labor_forecast", 300.25), {"update_modified": False})],
		)
		self.assertEqual(len(self.commits), 3)  # committed after each project, changed or not

	def test_it_never_saves_a_project_and_every_write_is_a_child_row_without_modified(self):
		self._line("PRJ-1", stored=1)
		self._run({"PRJ-1": 2})  # get_doc is a tripwire in setUp
		for args, kwargs in self.writes:
			self.assertEqual(args[0], "Project Budget Line")
			self.assertIs(kwargs.get("update_modified"), False)
		source = TRACKING_PATH.read_text(encoding="utf-8")
		body = source.split("def refresh_labor_forecasts(", 1)[1].split("\n# ----", 1)[0]
		for forbidden in (".save(", "get_doc(", "doc.save", '"Project",'):
			self.assertNotIn(forbidden, body)
		self.assertIn("update_modified=False", body)

	def test_one_failing_project_is_logged_and_the_rest_continue(self):
		for project in ("PRJ-1", "PRJ-2", "PRJ-3"):
			self._line(project, stored=0)
		summary = self._run({"PRJ-1": 10, "PRJ-2": RuntimeError("engine blew up"), "PRJ-3": 30})
		self.assertEqual(summary, {"checked": 3, "updated": 2, "failed": 1, "skipped": 0})
		self.assertEqual([w[0][1] for w in self.writes], ["PRJ-1-Labor", "PRJ-3-Labor"])
		self.assertEqual(len(frappe.errors), 1)
		args, kwargs = frappe.errors[0]
		self.assertEqual(args, ())  # keyword arguments only
		self.assertEqual(kwargs["title"], tracking.REFRESH_TITLE)
		self.assertIn("PRJ-2", kwargs["message"])
		self.assertEqual(len(self.rollbacks), 1)
		self.assertEqual(len(self.commits), 2)

	def test_closed_projects_are_skipped(self):
		for status in engine.CLOSED_PROJECT_STATUSES:
			self._line(f"PRJ-{status}", status=status)
		self._line("PRJ-OPEN", status="Open")
		self._line("PRJ-BLANK", status=None)
		summary = self._run({"PRJ-OPEN": 5, "PRJ-BLANK": 6})
		self.assertEqual(summary["checked"], 2)
		self.assertEqual(sorted(w[0][1] for w in self.writes), ["PRJ-BLANK-Labor", "PRJ-OPEN-Labor"])
		query, values = self.queries[0]
		self.assertEqual(set(values["closed"]), set(engine.CLOSED_PROJECT_STATUSES))

	def test_lines_that_are_not_labor_are_untouched(self):
		from erpnext_enhancements.quality import budgets

		self._line("PRJ-1", category="Materials", stored=7)
		self._line("PRJ-2", category="Labor", stored=7)
		summary = self._run({"PRJ-2": 9})  # PRJ-1 is not in the map: asking for it would KeyError
		self.assertEqual(summary["checked"], 1)
		self.assertEqual([w[0][1] for w in self.writes], ["PRJ-2-Labor"])
		self.assertEqual(self.queries[0][1]["category"], budgets.CATEGORY_LABOR)
		self.assertEqual(self.queries[0][1]["field"], budgets.LINES_FIELD)

	def test_a_second_labor_line_on_one_project_is_not_computed_twice(self):
		self._line("PRJ-1", stored=1, name="ROW-A")
		self._line("PRJ-1", stored=1, name="ROW-B")
		summary = self._run({"PRJ-1": 2})
		self.assertEqual(summary["checked"], 1)
		self.assertEqual([w[0][1] for w in self.writes], ["ROW-A"])

	def test_the_run_is_capped(self):
		for index in range(5):
			self._line(f"PRJ-{index}")
		costs = {f"PRJ-{index}": index + 1 for index in range(5)}
		with mock.patch.object(tracking, "REFRESH_MAX_PROJECTS", 2):
			summary = self._run(costs)
		self.assertEqual(summary, {"checked": 2, "updated": 2, "failed": 0, "skipped": 3})

	def test_it_does_nothing_without_the_table_or_the_column(self):
		self._line("PRJ-1")
		self.table_exists = False
		self.assertEqual(self._run({"PRJ-1": 1})["checked"], 0)
		self.assertEqual((self.queries, self.writes), ([], []))
		self.table_exists = True

		def raising(doctype, column):
			raise RuntimeError("Table 'tabProject Budget Line' doesn't exist")

		with mock.patch.object(frappe.db, "has_column", raising):
			self.assertEqual(self._run({"PRJ-1": 1})["checked"], 0)
		with mock.patch.object(frappe.db, "has_column", lambda doctype, column: False):
			self.assertEqual(self._run({"PRJ-1": 1})["checked"], 0)
		self.assertEqual((self.queries, self.writes), ([], []))

	def test_the_engine_is_called_with_google_off(self):
		calls = []

		def compute(start, end, resources=None, exclude=(), extra=(), google=True, equipment=False):
			calls.append(google)
			return {"resources": [], "days": {}}

		self._line("PRJ-1", stored=5)
		self._line("PRJ-2", stored=0)
		frappe.tables["Project"] = [{"name": "PRJ-1"}, {"name": "PRJ-2"}]
		with mock.patch.object(engine, "_compute", compute):
			summary = tracking.refresh_labor_forecasts()  # the real labor_forecast
		self.assertEqual(calls, [False, False])
		self.assertEqual(summary["failed"], 0)
		# No bookings and no clocked time: the forecast is 0, so only the line holding 5 changes.
		self.assertEqual([w[0][1:] for w in self.writes], [("PRJ-1-Labor", "labor_forecast", 0.0)])

	def test_it_is_registered_in_hooks_on_the_long_queue(self):
		tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
		events = next(
			node.value
			for node in tree.body
			if isinstance(node, ast.Assign)
			and any(getattr(t, "id", None) == "scheduler_events" for t in node.targets)
		)
		registered = [
			key.value
			for key, value in zip(events.keys, events.values, strict=True)
			if self.JOB in {c.value for c in ast.walk(value) if isinstance(c, ast.Constant)}
		]
		self.assertEqual(registered, ["daily_long"])
		module, _, function = self.JOB.rpartition(".")
		self.assertTrue(callable(getattr(importlib.import_module(module), function)))


class TestWiring(unittest.TestCase):
	def test_the_phase4_endpoints_are_reads(self):
		endpoints = _whitelisted(API_PATH)
		for name in ("get_actuals", "get_labor_forecast", "get_equipment"):
			self.assertIn(name, endpoints)
			self.assertIsNone(endpoints[name], name)
		self.assertEqual(endpoints["save_task"], ["POST"])
		self.assertEqual(_whitelisted(TRACKING_PATH), {})  # the tracking module is not an endpoint

	def test_save_task_takes_equipment_and_keeps_draft_last(self):
		fn = next(
			n
			for n in ast.parse(API_PATH.read_text(encoding="utf-8")).body
			if isinstance(n, ast.FunctionDef) and n.name == "save_task"
		)
		names = [a.arg for a in fn.args.args]
		self.assertIn("equipment", names)
		self.assertEqual(names[-1], "draft")


if __name__ == "__main__":
	unittest.main()
