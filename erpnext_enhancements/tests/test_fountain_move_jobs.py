"""Bench-free pytest suite for Fountain Move jobs (``project_enhancements/fountain_move_jobs.py``).

Nik, 2026-10-21: a move job is a Service project with ``custom_is_fountain_move`` ticked; ticking it
on a new project fills the Fountain Move template, its tasks are moved on the Maintenance Planner
and are read-only on the Project Planner, and PRJ-00775 is converted by a one-shot patch.

What fails expensively and quietly here, and so is pinned:

* the type -> template default **overriding a template somebody chose** (a Build job created from
  its own template would silently get move tasks instead);
* a seed or conversion patch that **raises** (it aborts ``bench migrate``, which is the deploy) or
  that **re-seeds over Nik's edits** on a later migrate;
* the conversion **canceling a task somebody started** on PRJ-00775, or adding the move tasks twice;
* the planners' customer-job set **dropping Service**, which would take every move job off both;
* a move task becoming **movable on the Project Planner** or **not movable on the Maintenance
  Planner**, and office tasks with no hours turning into Maintenance Planner cards.

A plain pytest module with its own ``frappe`` stub, so CI runs it in a ``python -m pytest`` step of
its own (``python -m unittest`` cannot collect function tests).
"""

import ast
import datetime
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]


class Thrown(Exception):
	pass


def _getdate(value=None):
	if value is None:
		return datetime.date(2026, 10, 21)
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value)[:10])


def install_frappe_stub():
	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")

	def flt(value=0, precision=None):
		try:
			return float(value or 0)
		except (TypeError, ValueError):
			return 0.0

	utils.flt = flt
	utils.cint = lambda value=0, *a, **k: int(flt(value))
	utils.getdate = _getdate
	utils.nowdate = lambda: "2026-10-21"
	utils.get_fullname = lambda user: user
	frappe.utils = utils

	def throw(message=None, *a, **k):
		raise Thrown(message)

	frappe.throw = throw
	frappe._ = lambda message=None, *a, **k: message
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.db = types.SimpleNamespace(
		has_column=lambda *a, **k: True,
		exists=lambda *a, **k: None,
		set_value=lambda *a, **k: None,
		sql=lambda *a, **k: [],
		savepoint=lambda *a, **k: None,
		rollback=lambda *a, **k: None,
	)
	frappe.get_all = lambda *a, **k: []
	frappe.get_doc = lambda *a, **k: None
	frappe.has_permission = lambda *a, **k: True
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda *a, **k: "traceback"
	frappe.get_app_path = lambda *parts: str(APP.joinpath(*parts[1:]))
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	return frappe


@pytest.fixture()
def fm():
	frappe = install_frappe_stub()
	for name in list(sys.modules):
		if name.startswith("erpnext_enhancements.project_enhancements.fountain_move_jobs") or name.startswith(
			"erpnext_enhancements.patches.seed_fountain_move_template"
		) or name.startswith("erpnext_enhancements.patches.make_prj_00775_a_move_job"):
			del sys.modules[name]
	module = importlib.import_module("erpnext_enhancements.project_enhancements.fountain_move_jobs")
	module.frappe = frappe
	return module


class Doc(dict):
	"""A dict that answers ``get``/``set`` and attribute reads, like a Document."""

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None

	def set(self, field, value):
		self[field] = value


# ---------------------------------------------------------------------- type -> template default


def test_ticked_box_fills_a_blank_template_and_type(fm):
	assert fm.insert_defaults({fm.FLAG: 1}) == {"project_template": fm.TEMPLATE, "project_type": "Service"}


def test_the_move_template_ticks_the_box(fm):
	out = fm.insert_defaults({"project_template": fm.TEMPLATE, "project_type": "Service"})
	assert out == {fm.FLAG: 1}


def test_an_explicit_other_template_is_never_replaced(fm):
	out = fm.insert_defaults({fm.FLAG: 1, "project_template": "Build Project Template", "project_type": "Build"})
	assert out == {}


def test_a_chosen_project_type_is_never_replaced(fm):
	assert fm.insert_defaults({fm.FLAG: "1", "project_type": "Build"}) == {"project_template": fm.TEMPLATE}


def test_unticked_without_the_template_changes_nothing(fm):
	for values in ({}, {fm.FLAG: 0}, {fm.FLAG: "0", "project_template": "Service Project Template"}):
		assert fm.insert_defaults(values) == {}


def test_no_template_fill_when_the_template_is_missing(fm):
	assert fm.insert_defaults({fm.FLAG: 1}, template_exists=False) == {"project_type": "Service"}


def test_apply_on_insert_fills_the_template(fm, monkeypatch):
	monkeypatch.setattr(fm.frappe.db, "exists", lambda doctype, name=None, *a, **k: doctype == fm.TEMPLATE_DOCTYPE)
	doc = Doc({fm.FLAG: 1, "project_template": None, "project_type": None})
	fm.apply_on_insert(doc)
	assert doc["project_template"] == fm.TEMPLATE
	assert doc["project_type"] == "Service"


def test_apply_on_insert_keeps_an_explicit_template(fm, monkeypatch):
	monkeypatch.setattr(fm.frappe.db, "exists", lambda *a, **k: True)
	doc = Doc({fm.FLAG: 1, "project_template": "Rent Project Template", "project_type": "Events"})
	fm.apply_on_insert(doc)
	assert doc["project_template"] == "Rent Project Template"
	assert doc["project_type"] == "Events"


def test_apply_on_insert_ticks_an_intake_opportunity(fm, monkeypatch):
	def exists(doctype, filters=None, *a, **k):
		if doctype == "DocType":
			return True
		if doctype == "Fountain Move Request":
			return filters == {"created_opportunity": "CRM-OPP-1"}
		return doctype == fm.TEMPLATE_DOCTYPE

	monkeypatch.setattr(fm.frappe.db, "exists", exists)
	doc = Doc({fm.FLAG: 0, "project_template": fm.TEMPLATE, "custom_opportunity": "CRM-OPP-1"})
	fm.apply_on_insert(doc)
	assert doc[fm.FLAG] == 1


def test_apply_on_insert_never_raises(fm, monkeypatch):
	def boom(*a, **k):
		raise RuntimeError("no column yet")

	logged = []
	monkeypatch.setattr(fm.frappe.db, "exists", boom)
	monkeypatch.setattr(fm.frappe, "get_traceback", boom)
	monkeypatch.setattr(fm.frappe, "log_error", lambda *a, **k: logged.append(k))
	doc = Doc({fm.FLAG: 1})
	fm.apply_on_insert(doc)  # must not raise
	# A missing column is a quiet no-op, before this app's fields exist.
	monkeypatch.setattr(fm.frappe.db, "has_column", boom)
	fm.apply_on_insert(Doc({fm.FLAG: 1}))


# ---------------------------------------------------------------------- seeding


def test_template_list_is_nik_compact_list(fm):
	assert [t["subject"] for t in fm.TEMPLATE_TASKS] == [
		"Confirm move date with customer",
		"Move day",
		"Invoice the move",
	]
	move_day = fm.TEMPLATE_TASKS[1]
	# 4 h for the whole crew (2 h each), crew of 2, Outdoor (Nik, 2026-10-21).
	assert (move_day["hours"], move_day["crew_size"], move_day["outdoor"]) == (4, 2, 1)
	assert [t["hours"] for t in fm.TEMPLATE_TASKS if t is not move_day] == [0, 0]
	assert fm.template_task_values(move_day) == {
		"subject": "Move day",
		"description": move_day["description"],
		"expected_time": 4.0,
		"custom_crew_size": 2,
		"custom_outdoor": 1,
	}


def test_seed_is_insert_only(fm, monkeypatch):
	inserted = []
	monkeypatch.setattr(fm.frappe.db, "exists", lambda *a, **k: True)
	monkeypatch.setattr(fm.frappe, "get_doc", lambda *a, **k: inserted.append(a) or None)
	assert fm.seed_template() == "exists"
	assert inserted == []


def test_seed_creates_three_tasks_then_the_template(fm, monkeypatch):
	made = []

	class Fake(Doc):
		def insert(self, ignore_permissions=False, set_name=None):
			self["name"] = set_name or f"TASK-{len(made) + 1}"
			made.append(dict(self))
			return self

	monkeypatch.setattr(
		fm.frappe.db, "exists", lambda doctype, *a, **k: doctype == "Project Type"
	)
	monkeypatch.setattr(fm.frappe, "get_doc", lambda values: Fake(values))
	assert fm.seed_template() == "created"
	assert [m["doctype"] for m in made] == ["Task", "Task", "Task", "Project Template"]
	assert all(m["is_template"] == 1 and m["status"] == "Template" for m in made[:3])
	assert made[3]["name"] == fm.TEMPLATE
	assert made[3]["project_type"] == "Service"
	assert [row["subject"] for row in made[3]["tasks"]] == [t["subject"] for t in fm.TEMPLATE_TASKS]


def _patch(fm, name):
	module = importlib.import_module(f"erpnext_enhancements.patches.{name}")
	module.frappe = fm.frappe
	module.fountain_move_jobs = fm
	return module


def test_seed_patch_never_raises_and_rolls_back(fm, monkeypatch):
	patch = _patch(fm, "seed_fountain_move_template")
	events = []
	monkeypatch.setattr(fm.frappe.db, "exists", lambda *a, **k: True)
	monkeypatch.setattr(fm.frappe.db, "rollback", lambda **k: events.append(("rollback", k)))
	monkeypatch.setattr(fm.frappe, "log_error", lambda **k: events.append(("log", k["title"])))

	def boom():
		raise RuntimeError("ERPNext refused the template")

	monkeypatch.setattr(fm, "seed_template", boom)
	patch.execute()  # must not raise
	assert ("rollback", {"save_point": patch.SAVEPOINT}) in events
	assert ("log", fm.LOG_TITLE) in events


def test_prj_patch_is_a_no_op_without_the_project(fm, monkeypatch):
	patch = _patch(fm, "make_prj_00775_a_move_job")
	called = []
	monkeypatch.setattr(fm.frappe.db, "exists", lambda *a, **k: False)
	monkeypatch.setattr(fm, "make_move_job", lambda *a, **k: called.append(a))
	patch.execute()
	assert called == []


def test_prj_patch_never_raises(fm, monkeypatch):
	patch = _patch(fm, "make_prj_00775_a_move_job")
	logged = []
	monkeypatch.setattr(fm.frappe.db, "exists", lambda *a, **k: True)
	monkeypatch.setattr(fm.frappe, "log_error", lambda **k: logged.append(k["title"]))

	def boom(*a, **k):
		raise RuntimeError("database went away")

	monkeypatch.setattr(fm, "make_move_job", boom)
	patch.execute()  # must not raise
	assert logged == [fm.LOG_TITLE]


# ---------------------------------------------------------------------- PRJ-00775 conversion


class FakeSite:
	"""Just enough of the database for make_move_job: one project, two templates, some tasks."""

	def __init__(self, fm, tasks):
		self.fm = fm
		self.project = Doc(name="PRJ-00775", project_type="Build", project_template="Service Project Template")
		self.project[fm.FLAG] = 0
		self.tasks = {t["name"]: Doc(t) for t in tasks}
		self.templates = {
			fm.TEMPLATE: ["TT-MOVE-1", "TT-MOVE-2", "TT-MOVE-3"],
			"Service Project Template": ["TT-SVC-1", "TT-SVC-2", "TT-SVC-3"],
		}
		self.timesheets = set()
		self.writes = []

	def install(self, monkeypatch):
		fm = self.fm
		monkeypatch.setattr(fm.frappe, "get_doc", self.get_doc)
		monkeypatch.setattr(fm.frappe, "get_all", self.get_all)
		monkeypatch.setattr(fm.frappe.db, "set_value", self.set_value)
		monkeypatch.setattr(
			fm.frappe.db,
			"exists",
			lambda doctype, filters=None, *a, **k: doctype == "Timesheet Detail" and filters["task"] in self.timesheets,
		)

	def get_doc(self, doctype, name=None):
		if isinstance(doctype, dict):
			site = self

			class NewTask(Doc):
				def insert(self, ignore_permissions=False):
					self["name"] = f"TASK-NEW-{len(site.tasks) + 1}"
					site.tasks[self["name"]] = Doc(self)
					return self

			return NewTask(doctype)
		if doctype == "Project":
			return Doc(self.project)
		if doctype == self.fm.TEMPLATE_DOCTYPE:
			return Doc(tasks=[Doc(task=t) for t in self.templates[name]])
		if doctype == "Task":
			return Doc(name=name, subject=f"Subject of {name}", description="", is_group=0)
		raise AssertionError(doctype)

	def get_all(self, doctype, filters=None, fields=None, pluck=None, order_by=None, **k):
		if doctype == "Project Template Task":
			return list(self.templates[filters["parent"]])
		assert doctype == "Task"
		wanted = set(filters["template_task"][1])
		rows = [t for t in self.tasks.values() if t["project"] == filters["project"] and t["template_task"] in wanted]
		return [r[pluck] for r in rows] if pluck else rows

	def set_value(self, doctype, name, field, value=None):
		self.writes.append((doctype, name, field, value))
		if doctype == "Project":
			self.project.update(field)
		else:
			self.tasks[name][field] = value


def _task(name, template_task, status="Open", assign=None, hours=0):
	return {
		"name": name,
		"project": "PRJ-00775",
		"template_task": template_task,
		"status": status,
		"_assign": json.dumps(assign or []),
		"actual_time": hours,
	}


def test_conversion_cancels_only_untouched_tasks_and_adds_the_move_tasks(fm, monkeypatch):
	site = FakeSite(
		fm,
		[
			_task("T1", "TT-SVC-1"),
			_task("T2", "TT-SVC-2", assign=["austin@example.com"]),
			_task("T3", "TT-SVC-3", hours=1.5),
			_task("T4", "TT-SVC-1", status="Working"),
			_task("T5", "TT-SVC-2"),
		],
	)
	site.timesheets.add("T5")
	site.install(monkeypatch)

	report = fm.make_move_job("PRJ-00775", cancel_from_template="Service Project Template")

	assert report["fields"] == {fm.FLAG: 1, "project_template": fm.TEMPLATE, "project_type": "Service"}
	assert report["canceled"] == ["T1"]
	assert sorted(report["kept"]) == ["T2", "T3", "T4", "T5"]
	assert site.tasks["T1"]["status"] == fm.CANCELED
	assert [site.tasks[n]["template_task"] for n in report["added"]] == ["TT-MOVE-1", "TT-MOVE-2", "TT-MOVE-3"]
	# Added undated: the Move day goes to the Maintenance Planner's Unscheduled tray.
	assert all("exp_start_date" not in site.tasks[n] for n in report["added"])


def test_conversion_is_safe_twice(fm, monkeypatch):
	site = FakeSite(fm, [_task("T1", "TT-SVC-1")])
	site.install(monkeypatch)
	fm.make_move_job("PRJ-00775", cancel_from_template="Service Project Template")
	count = len(site.tasks)
	writes = len(site.writes)

	again = fm.make_move_job("PRJ-00775", cancel_from_template="Service Project Template")

	assert again["fields"] == {} and again["added"] == [] and again["canceled"] == []
	assert again["kept"] == ["T1"]  # already Canceled: nothing to do
	assert len(site.tasks) == count and len(site.writes) == writes


# ---------------------------------------------------------------------- planners


def _engine_tuple(name):
	tree = ast.parse((APP / "project_enhancements" / "crew_availability.py").read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
			return ast.literal_eval(node.value)
	raise AssertionError(name)


def test_move_jobs_are_customer_jobs_on_both_planners(fm):
	"""A move job is Service, and Service is in the planners' customer-job set everywhere it is written."""
	assert fm.PROJECT_TYPE in _engine_tuple("PLANNER_PROJECT_TYPES")
	link_js = (APP / "public" / "js" / "project_enhancements" / "project_planner_link.js").read_text(encoding="utf-8")
	assert '"Service"' in link_js.split("const planned")[1].split("\n")[0]


def test_only_leaf_tasks_with_hours_get_a_card(fm):
	assert fm.cardable({"expected_time": 4})
	assert not fm.cardable({"expected_time": 0})
	assert not fm.cardable({"expected_time": None})
	assert not fm.cardable({"expected_time": 4, "is_group": 1})


def test_move_card_has_a_visit_shape(fm):
	row = {
		"name": "TASK-1",
		"subject": "Move day",
		"project": "PRJ-00775",
		"project_title": "Michelle Ainge - 4 Pots Moved",
		"customer": "Michelle Ainge",
		"status": "Open",
		"expected_time": 4,
		"custom_crew_size": 2,
		"modified": "2026-10-21 09:00:00",
	}
	crew = [{"user": "lead@example.com", "name": "Lead"}, {"user": "two@example.com", "name": "Two"}]
	day = datetime.date(2026, 10, 23)
	card = fm.move_card(row, crew, "2026-10-21", True, day, day)
	assert card["kind"] == "move" and card["key"] == "move|TASK-1"
	assert card["date"] == "2026-10-23" and card["end"] is None
	assert card["technician"] == "lead@example.com"
	assert card["crew"] == [{"user": "two@example.com", "name": "Two"}]
	assert card["movable"] is True and card["short"] == 0 and not card["overdue"]
	assert card["site"] == "Michelle Ainge - 4 Pots Moved"

	alone = fm.move_card(row, [], "2026-10-21", False)
	assert alone["date"] is None and alone["technician"] is None and alone["short"] == 2
	assert alone["movable"] is False

	past = fm.move_card(row, [], "2026-10-21", True, datetime.date(2026, 10, 20), datetime.date(2026, 10, 20))
	assert past["overdue"]


def test_crew_users_puts_the_lead_first_and_skips_people_without_a_user(fm):
	crew = [
		{"resource": "R-A", "hours": None, "is_lead": False, "label": "Ann"},
		{"resource": "R-VAN", "hours": None, "is_lead": False, "label": "Van"},
		{"resource": "R-B", "hours": 3, "is_lead": True, "label": None},
	]
	resources = {"R-A": {"user": "ann@x"}, "R-VAN": {"user": None}, "R-B": {"user": "bob@x", "resource_name": "Bob"}}
	assert fm.crew_users(crew, resources) == [
		{"user": "bob@x", "name": "Bob", "hours": 3},
		{"user": "ann@x", "name": "Ann", "hours": None},
	]


def test_crew_entries_keep_hours_lead_first_and_report_missing(fm):
	current = [{"resource": "R-A", "hours": 2, "is_lead": True}]
	entries, missing = fm.crew_entries(
		["bob@x", "ann@x", "ghost@x", "bob@x"], current, {"ann@x": "R-A", "bob@x": "R-B"}
	)
	assert entries == [
		{"resource": "R-B", "hours": 0, "is_lead": 1},
		{"resource": "R-A", "hours": 2, "is_lead": 0},
	]
	assert missing == ["ghost@x"]


def test_project_planner_cards_of_move_jobs_are_read_only(fm, monkeypatch):
	monkeypatch.setattr(fm.frappe, "get_all", lambda *a, **k: ["PRJ-MOVE"])
	cards = [{"project": "PRJ-MOVE", "movable": True}, {"project": "PRJ-BUILD", "movable": True}, {"movable": True}]
	fm.mark_read_only(cards)
	assert cards[0] == {"project": "PRJ-MOVE", "movable": False, "fountain_move": True}
	assert cards[1] == {"project": "PRJ-BUILD", "movable": True}


def test_project_planner_refuses_a_move_task_edit(fm, monkeypatch):
	monkeypatch.setattr(fm.frappe, "get_all", lambda *a, filters=None, **k: [filters["name"][1][0]])
	with pytest.raises(Thrown):
		fm.refuse_on_project_planner(Doc(name="TASK-1", subject="Move day", project="PRJ-MOVE"))
	monkeypatch.setattr(fm.frappe, "get_all", lambda *a, **k: [])
	fm.refuse_on_project_planner(Doc(name="TASK-2", subject="Pour", project="PRJ-BUILD"))


def test_move_projects_is_empty_before_the_field_exists(fm, monkeypatch):
	monkeypatch.setattr(fm.frappe.db, "has_column", lambda *a, **k: False)
	monkeypatch.setattr(fm.frappe, "get_all", lambda *a, **k: pytest.fail("queried a missing column"))
	assert fm.move_projects(["PRJ-1"]) == set()


# ---------------------------------------------------------------------- wiring


def _source(*parts):
	return (APP.joinpath(*parts)).read_text(encoding="utf-8")


def test_project_before_insert_runs_the_default():
	hooks = _source("hooks.py")
	block = hooks.split('"Project": {', 1)[1].split('"after_insert"', 1)[0]
	assert "erpnext_enhancements.project_enhancements.fountain_move_jobs.apply_on_insert" in block


def test_patches_seed_before_converting():
	lines = [line.strip() for line in _source("patches.txt").splitlines()]
	seed = lines.index("erpnext_enhancements.patches.seed_fountain_move_template")
	convert = lines.index("erpnext_enhancements.patches.make_prj_00775_a_move_job")
	assert seed < convert


def test_the_flag_is_a_check_defaulting_to_zero():
	rows = json.loads(_source("fixtures", "custom_field.json"))
	field = next(r for r in rows if r["name"] == "Project-custom_is_fountain_move")
	assert (field["dt"], field["fieldname"], field["fieldtype"], field["default"]) == (
		"Project",
		"custom_is_fountain_move",
		"Check",
		"0",
	)


def test_project_planner_load_refuses_move_tasks_unless_allowed():
	tree = ast.parse(_source("api", "project_planner.py"))
	load = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_load")
	assert [a.arg for a in load.args.args] == ["task", "modified", "allow_move_job"]
	assert "fountain_move_jobs.refuse_on_project_planner(doc)" in ast.unparse(load)
	assert "fountain_move_jobs.mark_read_only(tasks + unscheduled)" in _source("api", "project_planner.py")


def test_maintenance_planner_endpoint_and_page_agree():
	api = _source("api", "maintenance_planner.py")
	tree = ast.parse(api)
	endpoint = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "move_fountain_move")
	decorators = [ast.unparse(d) for d in endpoint.decorator_list]
	assert decorators == ["frappe.whitelist(methods=['POST'])"]
	assert "_require_planner()" in ast.unparse(endpoint)
	js = _source("sapphire_maintenance", "page", "maintenance_planner", "maintenance_planner.js")
	assert "move_fountain_move: `${MP.api}.move_fountain_move`" in js
	assert "Object.assign(MaintenancePlanner.prototype, MPFM_METHODS);" in js
	# Draft mode holds back visits only, so it refuses a move-job write instead of losing it.
	assert "move_fountain_move:\n\t\t\t\"Draft mode" in js
