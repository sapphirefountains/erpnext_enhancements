# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Project Planner's data model: five DocTypes, five Task Custom Fields, crew sync, seed patch.

What each section pins, and why it would fail silently otherwise:

* **Crew sync mirrors the crew into assignments, and only the crew.** The sidebar "Assigned to"
  and the planner's crew table are two views of the same ToDos. If removal were not limited to
  users who were on the crew *before this save*, saving a crew edit would cancel the assignments
  somebody made from the sidebar. If addition ignored an existing open ToDo, every save of a
  crewed task would raise an "Already in the following Users ToDo list" toast. It must also never
  raise: a Task has to stay saveable whatever an assignee's account looks like.
* **`validate_crew` keeps the table sane before it is stored**: one row per person, one lead,
  hours rounded and never negative.
* **Planner Resource refuses what would split a person's bookings** across two active resources.
* **The seed patch's employee -> group rule** is a pure function, pinned here.
* **The fixture and the doctype JSON are what `bench migrate` will read.** A Custom Field whose
  `insert_after` points at nothing falls to the bottom of the form; a doctype JSON in the wrong
  module is placed incorrectly (and `test_doctype_modules` would fail the build, but this fails
  first, with the doctype's name).

Bench-free: it installs its own ``frappe`` stub, loads the modules straight from their files (the
`project_enhancements` package `__init__` imports half the app), and restores ``sys.modules``.

Run: python -m unittest erpnext_enhancements.tests.test_planner_doctypes
"""

import ast
import datetime
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
DOCTYPES = APP / "project_enhancements" / "doctype"
FIXTURE = APP / "fixtures" / "custom_field.json"

STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.model",
	"frappe.model.document",
	"frappe.desk",
	"frappe.desk.form",
	"frappe.desk.form.assign_to",
)
_saved_modules = {}
frappe = None
crew_sync = None
resource_module = None
settings_module = None
seed = None
assign_calls = []


class _Throw(Exception):
	pass


class _Row(dict):
	"""A child row: attribute and dict access, like a frappe child Document."""

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None

	def __setattr__(self, name, value):
		self[name] = value


class _Document:
	"""Just enough of frappe's Document for the controllers under test."""

	def __init__(self, **values):
		self.__dict__.update(values)
		self.doctype = values.get("doctype")

	def get(self, key, default=None):
		value = self.__dict__.get(key)
		return default if value is None else value

	def remove(self, row):
		for rows in self.__dict__.values():
			if isinstance(rows, list) and row in rows:
				rows.remove(row)
				for index, other in enumerate(rows, 1):
					other["idx"] = index

	def get_doc_before_save(self):
		return self.__dict__.get("_before")


def _load(name, path):
	spec = importlib.util.spec_from_file_location(name, path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def setUpModule():
	global frappe, crew_sync, resource_module, settings_module, seed
	for name in STUBBED:
		_saved_modules[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.getdate = lambda value=None: (
		value if isinstance(value, datetime.date) else datetime.date.fromisoformat(str(value)[:10])
	)
	utils.flt = lambda value, precision=None: float(value or 0)
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe.throw = lambda message, exc=None, title=None: (_ for _ in ()).throw((exc or _Throw)(message))
	frappe.get_traceback = lambda: "traceback"
	frappe.flags = types.SimpleNamespace()
	frappe.logged = []
	frappe.log_error = lambda title=None, message=None, **k: frappe.logged.append(title)

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Document
	model.document = document

	assign_to = types.ModuleType("frappe.desk.form.assign_to")
	assign_to.add = lambda args: _assign("add", args["name"], args["assign_to"][0])
	assign_to.remove = lambda doctype, name, user: _assign("remove", name, user)
	desk = types.ModuleType("frappe.desk")
	form = types.ModuleType("frappe.desk.form")
	desk.form, form.assign_to = form, assign_to

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.model": model,
			"frappe.model.document": document,
			"frappe.desk": desk,
			"frappe.desk.form": form,
			"frappe.desk.form.assign_to": assign_to,
		}
	)
	_reset()
	crew_sync = _load("_pd_crew_sync", APP / "project_enhancements" / "crew_sync.py")
	resource_module = _load(
		"_pd_planner_resource", DOCTYPES / "planner_resource" / "planner_resource.py"
	)
	settings_module = _load(
		"_pd_planner_settings", DOCTYPES / "project_planner_settings" / "project_planner_settings.py"
	)
	seed = _load("_pd_seed_planner_resources", APP / "patches" / "seed_planner_resources.py")


def tearDownModule():
	for name, module in _saved_modules.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def _assign(kind, name, user):
	assign_calls.append((kind, name, user))


def _reset():
	frappe.logged.clear()
	assign_calls.clear()
	frappe.flags = types.SimpleNamespace()
	frappe.open_todos = []
	frappe.get_all = lambda doctype, **kw: _get_all(doctype, **kw)
	frappe.db = types.SimpleNamespace(
		get_value=lambda *a, **k: None,
		table_exists=lambda doctype: True,
		commit=lambda: None,
		rollback=lambda: None,
	)


def _get_all(doctype, **kw):
	if doctype == "ToDo":
		return [_Row(allocated_to=user) for user in frappe.open_todos]
	return []


def _crew(*users, **extra):
	return [
		_Row(resource=f"RES-{i}", user=user, idx=i, hours=None, is_lead=0, **extra)
		for i, user in enumerate(users, 1)
	]


def _task(now, before=None, status="Open", **extra):
	doc = _Document(name="TASK-1", subject="Install", status=status, custom_crew=_crew(*now), **extra)
	if before is not None:
		doc._before = _Document(custom_crew=_crew(*before))
	return doc


class TestCrewSync(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_adds_only_users_newly_on_the_crew(self):
		crew_sync.on_task_update(_task(["a@x", "b@x"], before=["a@x"]))
		self.assertEqual(assign_calls, [("add", "TASK-1", "b@x")])

	def test_a_new_task_assigns_the_whole_crew_and_removes_nobody(self):
		crew_sync.on_task_update(_task(["a@x", "b@x"]))
		self.assertEqual(assign_calls, [("add", "TASK-1", "a@x"), ("add", "TASK-1", "b@x")])

	def test_does_not_duplicate_an_existing_open_assignment(self):
		frappe.open_todos = ["b@x"]
		crew_sync.on_task_update(_task(["a@x", "b@x"], before=["a@x"]))
		self.assertEqual(assign_calls, [])

	def test_removes_only_users_who_were_on_the_crew_before_the_save(self):
		# s@x was assigned from the sidebar and is not (and never was) on the crew.
		frappe.open_todos = ["a@x", "b@x", "s@x"]
		crew_sync.on_task_update(_task(["a@x"], before=["a@x", "b@x"]))
		self.assertEqual(assign_calls, [("remove", "TASK-1", "b@x")])

	def test_never_touches_a_non_crew_assignee(self):
		frappe.open_todos = ["s@x"]
		crew_sync.on_task_update(_task([], before=["a@x"]))
		self.assertEqual(assign_calls, [])
		crew_sync.on_task_update(_task(["a@x"], before=["a@x"]))
		self.assertEqual(assign_calls, [])

	def test_an_unchanged_crew_does_nothing(self):
		frappe.open_todos = ["a@x"]
		crew_sync.on_task_update(_task(["a@x"], before=["a@x"]))
		self.assertEqual(assign_calls, [])

	def test_rows_without_a_user_are_skipped(self):
		crew_sync.on_task_update(_task([None, "a@x", ""], before=[None]))
		self.assertEqual(assign_calls, [("add", "TASK-1", "a@x")])

	def test_a_removed_userless_row_removes_nobody(self):
		frappe.open_todos = ["s@x"]
		crew_sync.on_task_update(_task(["a@x"], before=["a@x", None]))
		self.assertEqual(assign_calls, [])

	def test_finished_tasks_are_skipped(self):
		for status in ("Completed", "Canceled", "Cancelled", "Invoiced", "Template"):
			with self.subTest(status=status):
				assign_calls.clear()
				crew_sync.on_task_update(_task(["a@x"], status=status))
				self.assertEqual(assign_calls, [])

	def test_a_task_without_the_custom_field_is_a_no_op(self):
		# doc_events fire during ERPNext's own test bootstrap, before the custom field exists.
		doc = _Document(name="TASK-1", status="Open")
		crew_sync.on_task_update(doc)
		self.assertEqual(assign_calls, [])
		self.assertEqual(frappe.logged, [])

	def test_import_and_migrate_flags_skip(self):
		for flag in ("in_import", "in_patch", "in_install", "in_migrate"):
			with self.subTest(flag=flag):
				frappe.flags = types.SimpleNamespace(**{flag: True})
				crew_sync.on_task_update(_task(["a@x"]))
				self.assertEqual(assign_calls, [])

	def test_never_raises_when_an_assignment_fails(self):
		def boom(args):
			raise RuntimeError("user is disabled")

		original = sys.modules["frappe.desk.form.assign_to"].add
		sys.modules["frappe.desk.form.assign_to"].add = boom
		try:
			crew_sync.on_task_update(_task(["a@x", "b@x"]))
		finally:
			sys.modules["frappe.desk.form.assign_to"].add = original
		self.assertEqual(frappe.logged, ["Task crew assignment failed"] * 2)

	def test_never_raises_when_the_lookup_fails(self):
		def boom(*a, **k):
			raise RuntimeError("db gone")

		frappe.get_all = boom
		crew_sync.on_task_update(_task(["a@x"]))
		self.assertEqual(frappe.logged, ["Task crew sync failed"])

	def test_a_pencilled_task_assigns_nobody_but_still_removes(self):
		# P3.3: tentative work is nobody's to do yet, and an assignment notifies the person.
		crew_sync.on_task_update(_task(["a@x", "b@x"], custom_tentative=1))
		self.assertEqual(assign_calls, [])
		frappe.open_todos = ["a@x"]
		crew_sync.on_task_update(_task(["b@x"], before=["a@x"], custom_tentative=1))
		self.assertEqual(assign_calls, [("remove", "TASK-1", "a@x")])

	def test_firming_up_assigns_the_whole_crew(self):
		frappe.open_todos = ["a@x"]  # an assignment that already exists is left alone
		doc = _task(["a@x", "b@x", "c@x"], before=["a@x", "b@x"], custom_tentative=0)
		doc._before.custom_tentative = 1
		crew_sync.on_task_update(doc)
		self.assertEqual(assign_calls, [("add", "TASK-1", "b@x"), ("add", "TASK-1", "c@x")])

	def test_turning_a_firm_task_into_a_pencil_removes_nothing(self):
		frappe.open_todos = ["a@x", "b@x"]
		crew_sync.on_task_update(_task(["a@x", "b@x"], before=["a@x", "b@x"], custom_tentative="1"))
		self.assertEqual(assign_calls, [])

	def test_one_failed_removal_does_not_stop_the_next(self):
		frappe.open_todos = ["a@x", "b@x"]
		remover = sys.modules["frappe.desk.form.assign_to"].remove

		def flaky(doctype, name, user):
			if user == "a@x":
				raise RuntimeError("nope")
			_assign("remove", name, user)

		sys.modules["frappe.desk.form.assign_to"].remove = flaky
		try:
			crew_sync.on_task_update(_task([], before=["a@x", "b@x"]))
		finally:
			sys.modules["frappe.desk.form.assign_to"].remove = remover
		self.assertEqual(assign_calls, [("remove", "TASK-1", "b@x")])
		self.assertEqual(frappe.logged, ["Task crew unassignment failed"])


class TestValidateCrew(unittest.TestCase):
	def setUp(self):
		_reset()

	def _doc(self, rows):
		return _Document(custom_crew=[_Row(r, idx=i) for i, r in enumerate(rows, 1)])

	def test_a_duplicate_resource_keeps_its_first_row(self):
		doc = self._doc(
			[
				{"resource": "RES-1", "hours": 2},
				{"resource": "RES-2", "hours": 3},
				{"resource": "RES-1", "hours": 9},
			]
		)
		crew_sync.validate_crew(doc)
		self.assertEqual([(r.resource, r.hours, r.idx) for r in doc.custom_crew], [("RES-1", 2.0, 1), ("RES-2", 3.0, 2)])

	def test_only_the_first_ticked_lead_survives(self):
		doc = self._doc(
			[
				{"resource": "RES-1", "is_lead": 0},
				{"resource": "RES-2", "is_lead": 1},
				{"resource": "RES-3", "is_lead": 1},
			]
		)
		crew_sync.validate_crew(doc)
		self.assertEqual([r.is_lead for r in doc.custom_crew], [0, 1, 0])

	def test_hours_round_to_two_places(self):
		doc = self._doc([{"resource": "RES-1", "hours": 1.23456}, {"resource": "RES-2", "hours": 2.005}])
		crew_sync.validate_crew(doc)
		self.assertEqual(doc.custom_crew[0].hours, 1.23)
		self.assertEqual(doc.custom_crew[1].hours, round(2.005, 2))

	def test_blank_hours_stay_blank_and_zero_stays_zero(self):
		doc = self._doc([{"resource": "RES-1", "hours": None}, {"resource": "RES-2", "hours": 0}])
		crew_sync.validate_crew(doc)
		self.assertIsNone(doc.custom_crew[0].hours)
		self.assertEqual(doc.custom_crew[1].hours, 0.0)

	def test_negative_hours_are_an_error(self):
		doc = self._doc([{"resource": "RES-1", "hours": -1}])
		with self.assertRaises(_Throw):
			crew_sync.validate_crew(doc)

	def test_no_crew_or_no_field_is_a_no_op(self):
		crew_sync.validate_crew(_Document())
		crew_sync.validate_crew(_Document(custom_crew=[]))


class TestPlannerResource(unittest.TestCase):
	def setUp(self):
		_reset()

	def _resource(self, **values):
		base = {
			"doctype": "Planner Resource",
			"name": "RES-00002",
			"resource_name": "Austin",
			"resource_type": "Employee",
			"employee": "HR-EMP-1",
			"user": "austin@x",
			"is_active": 1,
			"work_patterns": [],
		}
		base.update(values)
		return resource_module.PlannerResource(**base)

	def test_a_valid_employee_resource_passes(self):
		self._resource().validate()

	def test_an_employee_resource_needs_an_employee(self):
		with self.assertRaises(_Throw):
			self._resource(employee=None).validate()

	def test_a_second_active_resource_for_one_employee_names_the_first(self):
		seen = {}

		def get_value(doctype, filters, fieldname):
			seen.update(filters)
			return "RES-00001"

		frappe.db.get_value = get_value
		with self.assertRaises(_Throw) as caught:
			self._resource().validate()
		self.assertIn("RES-00001", str(caught.exception))
		self.assertEqual(seen["is_active"], 1)
		self.assertEqual(seen["name"], ("!=", "RES-00002"))

	def test_an_inactive_resource_may_share_an_employee(self):
		frappe.db.get_value = lambda *a, **k: self.fail("should not look for a duplicate")
		self._resource(is_active=0).validate()

	def test_a_subcontractor_loses_its_employee_and_user(self):
		doc = self._resource(resource_type="Subcontractor", supplier="SUP-1")
		doc.validate()
		self.assertIsNone(doc.employee)
		self.assertIsNone(doc.user)

	def test_pattern_hours_must_be_between_zero_and_twenty_four(self):
		for bad in (-1, 24.5):
			with self.subTest(hours=bad):
				doc = self._resource(work_patterns=[_Row(idx=1, tuesday=bad)])
				with self.assertRaises(_Throw):
					doc.validate()
		self._resource(work_patterns=[_Row(idx=1, monday=0, tuesday=24, wednesday=None)]).validate()

	def test_a_range_cannot_end_before_it_starts(self):
		row = _Row(idx=1, effective_from="2026-06-10", effective_to="2026-06-01")
		with self.assertRaises(_Throw):
			self._resource(work_patterns=[row]).validate()
		ok = _Row(idx=1, effective_from="2026-06-01", effective_to="2026-06-01")
		self._resource(work_patterns=[ok]).validate()
		self._resource(work_patterns=[_Row(idx=1, effective_from="2026-06-01", effective_to=None)]).validate()


class TestPlannerSettings(unittest.TestCase):
	def _settings(self, **values):
		base = {"default_day_hours": 8, "maintenance_visit_hours": 2, "rental_setup_hours": 3}
		base.update(values)
		doc = settings_module.ProjectPlannerSettings(**base)
		doc.meta = types.SimpleNamespace(get_label=lambda field: field)
		return doc

	def test_defaults_pass(self):
		self._settings().validate()

	def test_a_zero_visit_length_is_a_choice_not_an_error(self):
		self._settings(maintenance_visit_hours=0).validate()

	def test_negative_or_over_a_day_is_refused(self):
		for field, value in (("maintenance_visit_hours", -1), ("rental_setup_hours", 25)):
			with self.subTest(field=field), self.assertRaises(_Throw):
				self._settings(**{field: value}).validate()

	def test_a_zero_full_day_is_refused(self):
		with self.assertRaises(_Throw):
			self._settings(default_day_hours=0).validate()


class TestSeedPatch(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_selection_rule(self):
		cases = [
			("Junior Technician", "Service", ("Field", "Maintenance")),
			("Senior Technician", None, ("Field", "Maintenance")),
			("technician", "", ("Field", "Maintenance")),
			("Project Manager", "Projects", ("PM", "Projects")),
			("Project Manager ", None, ("PM", "Projects")),
			("Associate Project Manager", None, None),
			("Illustrator", "Design - SF", ("Design", "Projects")),
			(None, "design", ("Design", "Projects")),
			# Checked in order: a technician in a Design department is still Field.
			("Master Technician", "Design - SF", ("Field", "Maintenance")),
			("Accountant", "Accounting - SF", None),
			(None, None, None),
		]
		for designation, department, expected in cases:
			with self.subTest(designation=designation, department=department):
				self.assertEqual(seed.select_resource_group(designation, department), expected)

	def _run(self, employees, existing=()):
		inserted = []

		class FakeDoc:
			def insert(self, ignore_permissions=False):
				if getattr(self, "employee", None) == "EMP-BAD":
					raise RuntimeError("bad row")
				inserted.append(dict(vars(self)))

		def get_all(doctype, **kw):
			if doctype == "Planner Resource":
				return [_Row(employee=e) for e in existing]
			self.assertEqual(kw["filters"], {"status": "Active"})
			return [_Row(e) for e in employees]

		frappe.get_all = get_all
		frappe.new_doc = lambda doctype: FakeDoc()
		seed.execute()
		return inserted

	def _emp(self, name, designation=None, department=None, user_id=None):
		return {
			"name": name,
			"employee_name": f"Name {name}",
			"designation": designation,
			"department": department,
			"user_id": user_id,
		}

	def test_creates_a_resource_per_eligible_employee_and_skips_the_rest(self):
		inserted = self._run(
			[
				self._emp("EMP-1", "Junior Technician", user_id="t@x"),
				self._emp("EMP-2", "Project Manager"),
				self._emp("EMP-3", "Illustrator", "Design - SF"),
				self._emp("EMP-4", "Accountant", "Accounting - SF"),
			]
		)
		self.assertEqual(
			[(d["employee"], d["resource_group"], d["home_team"]) for d in inserted],
			[("EMP-1", "Field", "Maintenance"), ("EMP-2", "PM", "Projects"), ("EMP-3", "Design", "Projects")],
		)
		self.assertEqual(inserted[0]["resource_name"], "Name EMP-1")
		self.assertEqual(inserted[0]["user"], "t@x")
		self.assertEqual(inserted[0]["resource_type"], "Employee")
		self.assertNotIn("work_patterns", inserted[0])

	def test_an_employee_who_already_has_a_resource_is_skipped(self):
		inserted = self._run([self._emp("EMP-1", "Technician")], existing=["EMP-1"])
		self.assertEqual(inserted, [])

	def test_one_failed_insert_does_not_stop_the_rest(self):
		inserted = self._run([self._emp("EMP-BAD", "Technician"), self._emp("EMP-2", "Technician")])
		self.assertEqual([d["employee"] for d in inserted], ["EMP-2"])
		self.assertEqual(frappe.logged, ["Planner resource seed"])

	def test_returns_quietly_when_the_table_is_missing(self):
		frappe.db.table_exists = lambda doctype: False
		frappe.get_all = lambda *a, **k: self.fail("must not query")
		seed.execute()

	def test_never_raises(self):
		def boom(*a, **k):
			raise RuntimeError("db gone")

		frappe.get_all = boom
		seed.execute()
		self.assertEqual(frappe.logged, ["Planner resource seed"])


NEW_DOCTYPES = {
	"Planner Resource": "planner_resource",
	"Planner Resource Work Pattern": "planner_resource_work_pattern",
	"Task Crew Member": "task_crew_member",
	"Task Required Credential": "task_required_credential",
	"Project Planner Settings": "project_planner_settings",
}


def _doctype_json(name):
	scrub = NEW_DOCTYPES[name]
	return json.loads((DOCTYPES / scrub / f"{scrub}.json").read_text(encoding="utf-8"))


class TestDoctypeDefinitions(unittest.TestCase):
	def test_every_doctype_is_in_the_project_enhancements_module(self):
		for name, scrub in NEW_DOCTYPES.items():
			with self.subTest(doctype=name):
				data = _doctype_json(name)
				self.assertEqual(data["module"], "Project Enhancements")
				self.assertEqual(data["name"], name)
				self.assertEqual(data["doctype"], "DocType")
				self.assertEqual(data["custom"], 0)
				self.assertEqual(data["creation"], "2026-10-08 12:00:00.000000")
				self.assertTrue((DOCTYPES / scrub / "__init__.py").exists())
				self.assertTrue((DOCTYPES / scrub / f"{scrub}.py").exists())

	def test_field_order_lists_exactly_the_declared_fields(self):
		for name in NEW_DOCTYPES:
			with self.subTest(doctype=name):
				data = _doctype_json(name)
				self.assertEqual(data["field_order"], [f["fieldname"] for f in data["fields"]])

	def test_child_and_single_flags(self):
		for name in ("Planner Resource Work Pattern", "Task Crew Member", "Task Required Credential"):
			self.assertEqual(_doctype_json(name)["istable"], 1, name)
		self.assertEqual(_doctype_json("Project Planner Settings")["issingle"], 1)
		self.assertFalse(_doctype_json("Planner Resource").get("istable"))

	def test_planner_resource_shape(self):
		data = _doctype_json("Planner Resource")
		self.assertEqual(data["autoname"], "format:RES-{#####}")
		self.assertEqual(data["title_field"], "resource_name")
		fields = {f["fieldname"]: f for f in data["fields"]}
		for name in ("resource_name", "resource_type", "resource_group"):
			self.assertEqual(fields[name]["reqd"], 1, name)
		self.assertEqual(fields["user"]["fetch_from"], "employee.user_id")
		self.assertEqual(fields["work_patterns"]["options"], "Planner Resource Work Pattern")
		self.assertEqual(fields["resource_group"]["options"], "Field\nPM\nDesign\nSubcontractor")
		roles = {p["role"]: p for p in data["permissions"]}
		self.assertEqual(
			set(roles),
			{"System Manager", "Projects Manager", "Projects User", "Maintenance Supervisor", "Maintenance User"},
		)
		for manager in ("System Manager", "Projects Manager"):
			self.assertTrue(all(roles[manager][k] for k in ("create", "read", "write", "delete", "report", "export")))
		for reader in ("Projects User", "Maintenance Supervisor", "Maintenance User"):
			self.assertEqual({k for k in roles[reader] if k != "role"}, {"read", "report"})

	def test_work_pattern_defaults_are_a_monday_to_friday_week(self):
		fields = {f["fieldname"]: f for f in _doctype_json("Planner Resource Work Pattern")["fields"]}
		week = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
		self.assertEqual([fields[d]["default"] for d in week], ["8", "8", "8", "8", "8", "0", "0"])

	def test_task_crew_member_fetches_from_the_resource(self):
		fields = {f["fieldname"]: f for f in _doctype_json("Task Crew Member")["fields"]}
		self.assertEqual(fields["resource_name"]["fetch_from"], "resource.resource_name")
		self.assertEqual(fields["user"]["fetch_from"], "resource.user")
		self.assertEqual(fields["resource"]["options"], "Planner Resource")
		self.assertEqual(fields["resource"]["reqd"], 1)

	def test_required_credential_links_credential_type(self):
		(field,) = _doctype_json("Task Required Credential")["fields"]
		self.assertEqual((field["fieldname"], field["fieldtype"], field["options"]), ("credential_type", "Link", "Credential Type"))

	def test_settings_defaults(self):
		fields = {f["fieldname"]: f.get("default") for f in _doctype_json("Project Planner Settings")["fields"]}
		self.assertEqual(
			{k: fields[k] for k in fields if k.endswith("_hours")},
			{
				"default_day_hours": "8",
				"maintenance_visit_hours": "2",
				"rental_delivery_hours": "2",
				"rental_setup_hours": "3",
				"rental_takedown_hours": "2",
				"rental_cleaning_hours": "2",
			},
		)

	def test_controller_class_names_match_frappes_derivation(self):
		for name, scrub in NEW_DOCTYPES.items():
			with self.subTest(doctype=name):
				tree = ast.parse((DOCTYPES / scrub / f"{scrub}.py").read_text(encoding="utf-8"))
				classes = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
				self.assertIn(name.replace(" ", "").replace("-", ""), classes)


class TestTaskCustomFields(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
		cls.by_name = {r["name"]: r for r in cls.rows}
		cls.template = cls.by_name["Task-custom_rental_task_kind"]

	CHAIN = [
		("custom_crew_section", "Section Break", "is_milestone"),
		("custom_crew_size", "Int", "custom_crew_section"),
		("custom_required_credentials", "Table MultiSelect", "custom_crew_size"),
		("custom_crew_column", "Column Break", "custom_required_credentials"),
		("custom_crew", "Table", "custom_crew_column"),
	]

	def test_every_field_is_present_with_its_insert_after_chain_intact(self):
		for fieldname, fieldtype, after in self.CHAIN:
			with self.subTest(fieldname=fieldname):
				row = self.by_name[f"Task-{fieldname}"]
				self.assertEqual(row["dt"], "Task")
				self.assertEqual(row["fieldname"], fieldname)
				self.assertEqual(row["fieldtype"], fieldtype)
				self.assertEqual(row["insert_after"], after)
				self.assertEqual(row["module"], "ERPNext Enhancements")

	def test_each_insert_after_target_is_a_field_the_form_will_have(self):
		# is_milestone is ERPNext's own; the rest must be in this fixture.
		for _fieldname, _fieldtype, after in self.CHAIN[1:]:
			self.assertIn(f"Task-{after}", self.by_name)

	def test_records_carry_the_full_key_set(self):
		for fieldname, _fieldtype, _after in self.CHAIN:
			with self.subTest(fieldname=fieldname):
				self.assertEqual(set(self.by_name[f"Task-{fieldname}"]), set(self.template))

	def test_options_and_attributes(self):
		get = lambda name: self.by_name[f"Task-{name}"]  # noqa: E731
		self.assertEqual(get("custom_crew")["options"], "Task Crew Member")
		self.assertEqual(get("custom_required_credentials")["options"], "Task Required Credential")
		self.assertEqual(get("custom_crew_size")["non_negative"], 1)
		self.assertEqual(get("custom_crew_section")["label"], "Crew")
		self.assertEqual(get("custom_crew_size")["label"], "Crew needed")
		self.assertEqual(get("custom_required_credentials")["label"], "Qualifications needed")
		self.assertEqual(get("custom_crew")["label"], "Crew")
		for fieldname, _t, _a in self.CHAIN:
			self.assertEqual(get(fieldname)["no_copy"], 0, fieldname)

	def test_names_are_unique(self):
		names = [r["name"] for r in self.rows]
		self.assertEqual(len(names), len(set(names)))

	def test_the_tentative_check(self):
		# P3.3: the pencil flag the planner, the engine and crew_sync all read.
		row = self.by_name["Task-custom_tentative"]
		self.assertEqual(
			{k: row[k] for k in ("dt", "fieldname", "fieldtype", "label", "insert_after", "module", "default")},
			{
				"dt": "Task",
				"fieldname": "custom_tentative",
				"fieldtype": "Check",
				"label": "Tentative (pencil)",
				"insert_after": "custom_crew_size",
				"module": "ERPNext Enhancements",
				"default": "0",
			},
		)
		self.assertEqual(set(row), set(self.template))
		self.assertEqual(row["no_copy"], 0)


class TestRegistration(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
		for node in tree.body:
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "doc_events":
				cls.task_events = ast.literal_eval(node.value)["Task"]

	def test_task_validate_is_a_list_with_the_existing_hook_first(self):
		validate = self.task_events["validate"]
		self.assertIsInstance(validate, list)
		self.assertEqual(validate[0], "erpnext_enhancements.training.compliance.warn_uncertified_assignee")
		self.assertIn("erpnext_enhancements.project_enhancements.crew_sync.validate_crew", validate)

	def test_crew_sync_is_on_update(self):
		on_update = self.task_events["on_update"]
		self.assertEqual(on_update[-1], "erpnext_enhancements.project_enhancements.crew_sync.on_task_update")
		self.assertIn("erpnext_enhancements.tasks.generate_next_task", on_update)

	def test_seed_patch_is_registered_after_model_sync(self):
		text = (APP / "patches.txt").read_text(encoding="utf-8")
		post = text.split("[post_model_sync]", 1)[1]
		self.assertIn("erpnext_enhancements.patches.seed_planner_resources", post.splitlines())
		self.assertIn("seed_planner_resources", (APP / "patches" / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
	unittest.main()
