"""Bench-free pytest suite for the Scope tab's task actions (v1.777.0; Nik, 2026-11-03).

``project_enhancements/scope_tasks.py`` (pure), ``api/scope_tasks.py`` (the endpoints) and the
fixtures that put the Tasks Tree first on the Project form's Scope tab.

What fails expensively and quietly here, and so is pinned:

* an import that **duplicates** a task already on the job (the one thing the skip rule exists for),
  or that **flattens** a template: a child under the root, a dependency dropped, the template's
  order lost;
* a second import of a template whose group is already there adding a **second group** instead of
  the missing children under the existing one;
* a canceled task **blocking** an import, or a dependency on a skipped task pointing at nothing;
* a mass delete that tries a parent **before** its children (ERPNext refuses it), that reports a
  refusal as a crash, or that leaves ``frappe.flags.mute_messages`` on for the rest of the request;
* the "Add task" dialog's values reaching the Task with a field the dialog never offered;
* the fixtures putting the tree **anywhere but first** on the Scope tab, or a Section Break's
  ``insert_after`` disagreeing with the ``field_order`` Property Setter (v16 places by the array,
  so the two drift apart in silence);
* a dialog flow that pushes browser history (Nik: never break Back and Forward).

A plain pytest module with its own ``frappe`` stub, so CI runs it in a ``python -m pytest`` step of
its own (``python -m unittest`` cannot collect function tests).
"""

import datetime
import importlib
import json
import re
import sys
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
FIXTURES = APP / "fixtures"
TREE_JS = APP / "public/js/project_enhancements/task_tree_manager.js"
FORM_JS = APP / "public/js/project_enhancements/project_form_script.js"


def _getdate(value=None):
	if value is None:
		return datetime.date(2026, 11, 3)
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value)[:10])


class Refusal(Exception):
	"""What the stub's ``frappe.throw`` raises."""


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
	utils.nowdate = lambda: "2026-11-03"
	utils.get_fullname = lambda user: user
	frappe.utils = utils
	frappe._ = lambda message=None, *a, **k: message
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.message_log = []

	def throw(message, exc=None, *a, **k):
		frappe.message_log.append(message)
		raise (exc or Refusal)(message)

	frappe.throw = throw
	frappe.flags = types.SimpleNamespace(mute_messages=False)

	class ValidationError(Exception):
		pass

	class LinkExistsError(ValidationError):
		pass

	class DocumentLockedError(ValidationError):
		pass

	class PermissionError(Exception):
		pass

	frappe.ValidationError = ValidationError
	frappe.LinkExistsError = LinkExistsError
	frappe.DocumentLockedError = DocumentLockedError
	frappe.PermissionError = PermissionError
	frappe.db = types.SimpleNamespace(
		has_column=lambda *a, **k: True,
		exists=lambda *a, **k: None,
		get_value=lambda *a, **k: None,
		set_value=lambda *a, **k: None,
		savepoint=lambda *a, **k: None,
		rollback=lambda *a, **k: None,
	)
	frappe.get_all = lambda *a, **k: []
	frappe.get_doc = lambda *a, **k: None
	frappe.delete_doc = lambda *a, **k: None
	frappe.has_permission = lambda *a, **k: True
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda *a, **k: "traceback"
	frappe.get_app_path = lambda *parts: str(APP.joinpath(*parts[1:]))
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	return frappe


@pytest.fixture()
def rules():
	install_frappe_stub()
	for name in list(sys.modules):
		if name.startswith("erpnext_enhancements.project_enhancements.scope_tasks"):
			del sys.modules[name]
	return importlib.import_module("erpnext_enhancements.project_enhancements.scope_tasks")


@pytest.fixture()
def api():
	frappe = install_frappe_stub()
	for name in list(sys.modules):
		if name.startswith("erpnext_enhancements.api.scope_tasks") or name.startswith(
			"erpnext_enhancements.project_enhancements.scope_tasks"
		):
			del sys.modules[name]
	module = importlib.import_module("erpnext_enhancements.api.scope_tasks")
	module.frappe = frappe
	module.rules.frappe = frappe
	return module


def T(name, subject, parent=None, **extra):
	return dict({"name": name, "subject": subject, "parent_task": parent}, **extra)


#: The Service Project Template as prod holds it (2026-11-03): two groups with children, one empty group.
SERVICE = [
	T("TPL-LAUNCH", "Project Launch", is_group=1),
	T("TPL-SCOPE", "Scope & Team Definition", "TPL-LAUNCH"),
	T("TPL-SCHED", "Schedule Definition", "TPL-LAUNCH"),
	T("TPL-BILL", "Billing", is_group=1),
	T("TPL-INIT", "Initial Payment", "TPL-BILL"),
	T("TPL-FINAL", "Final Payment", "TPL-BILL"),
	T("TPL-VISITS", "Service Visit(s)", is_group=1),
]
#: PRO-0206's four stages, each waiting on the one before.
DESIGN = [
	T("TPL-PD", "Predesign (PD)", start=0, duration=13),
	T("TPL-SD", "Schematic Design (SD)", start=14, duration=20),
	T("TPL-DD", "Design Development (DD)", start=35, duration=27),
	T("TPL-CD", "Construction Documents (CD)", start=63, duration=20),
]
DESIGN_DEPENDS = [
	{"parent": "TPL-SD", "task": "TPL-PD"},
	{"parent": "TPL-DD", "task": "TPL-SD"},
	{"parent": "TPL-CD", "task": "TPL-DD"},
]


def _created(plan):
	return {row["template"]: row for row in plan["create"]}


# ---------------------------------------------------------------------- plan_import


def test_an_empty_project_gets_the_whole_template_in_order(rules):
	plan = rules.plan_import(SERVICE, [], [], None)
	assert [row["template"] for row in plan["create"]] == [row["name"] for row in SERVICE]
	assert plan["skip"] == []
	made = _created(plan)
	assert made["TPL-LAUNCH"]["parent"] is None
	assert made["TPL-SCOPE"]["parent"] == rules.new_ref("TPL-LAUNCH")
	assert made["TPL-INIT"]["parent"] == rules.new_ref("TPL-BILL")
	# Positions count from 0 under each parent, in template order.
	assert [made[n]["position"] for n in ("TPL-LAUNCH", "TPL-BILL", "TPL-VISITS")] == [0, 1, 2]
	assert [made[n]["position"] for n in ("TPL-SCOPE", "TPL-SCHED")] == [0, 1]


def test_the_roots_go_under_the_picked_node(rules):
	existing = [T("TASK-1", "Phase 100", is_group=1)]
	plan = rules.plan_import(SERVICE, [], existing, "TASK-1")
	made = _created(plan)
	assert made["TPL-LAUNCH"]["parent"] == rules.existing_ref("TASK-1")
	assert made["TPL-BILL"]["parent"] == rules.existing_ref("TASK-1")
	assert made["TPL-SCOPE"]["parent"] == rules.new_ref("TPL-LAUNCH")


def test_dependencies_are_kept_between_new_tasks(rules):
	plan = rules.plan_import(DESIGN, DESIGN_DEPENDS, [], None)
	assert plan["deps"] == {
		"TPL-SD": [rules.new_ref("TPL-PD")],
		"TPL-DD": [rules.new_ref("TPL-SD")],
		"TPL-CD": [rules.new_ref("TPL-DD")],
	}


def test_a_task_already_under_the_same_parent_is_skipped_by_its_words(rules):
	existing = [T("TASK-7", "  project   LAUNCH ", is_group=1), T("TASK-8", "Billing")]
	plan = rules.plan_import(SERVICE, [], existing, None)
	skipped = {row["template"]: row["existing"] for row in plan["skip"]}
	assert skipped == {"TPL-LAUNCH": "TASK-7", "TPL-BILL": "TASK-8"}
	assert "TPL-VISITS" in _created(plan)


def test_a_skipped_groups_children_are_matched_under_the_existing_group(rules):
	"""A second import adds only what is missing under the group that is already there."""
	existing = [
		T("TASK-7", "Project Launch", is_group=1),
		T("TASK-9", "Scope & Team Definition", "TASK-7"),
		T("TASK-10", "Schedule Definition", None),  # same subject, wrong parent: not a match
	]
	plan = rules.plan_import(SERVICE, [], existing, None)
	made = _created(plan)
	skipped = {row["template"]: row["existing"] for row in plan["skip"]}
	assert skipped == {"TPL-LAUNCH": "TASK-7", "TPL-SCOPE": "TASK-9"}
	assert made["TPL-SCHED"]["parent"] == rules.existing_ref("TASK-7")
	assert made["TPL-SCHED"]["position"] == 0


def test_the_same_subject_under_another_parent_does_not_match(rules):
	existing = [T("TASK-7", "Project Launch", is_group=1), T("TASK-11", "Billing", "TASK-7")]
	plan = rules.plan_import(SERVICE, [], existing, None)
	assert "TPL-BILL" in _created(plan)


def test_a_canceled_task_never_blocks(rules):
	for status in ("Canceled", "Cancelled"):
		existing = [T("TASK-7", "Project Launch", is_group=1, status=status)]
		plan = rules.plan_import(SERVICE, [], existing, None)
		assert "TPL-LAUNCH" in _created(plan), status


def test_a_dependency_on_a_skipped_task_points_at_the_existing_task(rules):
	existing = [T("TASK-20", "Predesign (PD)", status="Completed")]
	plan = rules.plan_import(DESIGN, DESIGN_DEPENDS, existing, None)
	assert plan["skip"] == [{"template": "TPL-PD", "subject": "Predesign (PD)", "existing": "TASK-20"}]
	assert plan["deps"]["TPL-SD"] == [rules.existing_ref("TASK-20")]
	assert "TPL-PD" not in plan["deps"]


def test_a_self_dependency_and_one_outside_the_template_are_dropped(rules):
	depends = [{"parent": "TPL-SD", "task": "TPL-SD"}, {"parent": "TPL-SD", "task": "TASK-ELSEWHERE"}]
	plan = rules.plan_import(DESIGN, depends, [], None)
	assert plan["deps"] == {}


def test_a_template_parent_outside_the_template_makes_a_root(rules):
	rows = [T("TPL-A", "Alpha", "TASK-NOT-IN-TEMPLATE"), T("TPL-B", "Beta", "TPL-A")]
	plan = rules.plan_import(rows, [], [], "TASK-ROOT")
	made = _created(plan)
	assert made["TPL-A"]["parent"] == rules.existing_ref("TASK-ROOT")
	assert made["TPL-B"]["parent"] == rules.new_ref("TPL-A")


def test_template_order_puts_a_parent_before_its_child_whatever_the_rows_say(rules):
	rows = [T("TPL-B", "Beta", "TPL-A"), T("TPL-A", "Alpha", is_group=1), T("TPL-C", "Gamma")]
	assert [row["name"] for row in rules.template_order(rows)] == ["TPL-A", "TPL-B", "TPL-C"]


def test_a_parent_cycle_loses_nothing(rules):
	rows = [T("TPL-A", "Alpha", "TPL-B"), T("TPL-B", "Beta", "TPL-A")]
	assert sorted(row["name"] for row in rules.template_order(rows)) == ["TPL-A", "TPL-B"]


def test_next_position_follows_the_siblings_under_that_parent(rules):
	existing = [
		T("TASK-1", "One", custom_subtask_order=3),
		T("TASK-2", "Two", custom_subtask_order="7"),
		T("TASK-3", "Three", "TASK-1", custom_subtask_order=40),
		T("TASK-4", "Four", custom_subtask_order=None),
	]
	assert rules.next_position(existing, None) == 8
	assert rules.next_position(existing, "TASK-1") == 41
	assert rules.next_position(existing, "TASK-2") == 1
	assert rules.next_position([], None) == 1


# ---------------------------------------------------------------------- preview_rows


def test_preview_rows_show_levels_days_hours_and_waits(rules):
	rows = rules.preview_rows(DESIGN, DESIGN_DEPENDS)
	assert [row["level"] for row in rows] == [0, 0, 0, 0]
	assert rows[1]["waits"] == ["Predesign (PD)"]
	assert (rows[3]["start"], rows[3]["duration"]) == (63, 20)
	nested = rules.preview_rows(SERVICE, [])
	assert [(row["subject"], row["level"]) for row in nested][:3] == [
		("Project Launch", 0),
		("Scope & Team Definition", 1),
		("Schedule Definition", 1),
	]
	assert nested[0]["is_group"] == 1 and nested[1]["is_group"] == 0


# ---------------------------------------------------------------------- delete_order


def test_delete_order_takes_the_subtree_deepest_first(rules):
	tasks = [
		T("A", "A"),
		T("A1", "A1", "A"),
		T("A1x", "A1x", "A1"),
		T("A2", "A2", "A"),
		T("B", "B"),
		T("B1", "B1", "B"),
	]
	order = rules.delete_order(tasks, ["A", "B1"])
	assert sorted(order) == ["A", "A1", "A1x", "A2", "B1"]
	assert "B" not in order
	# Every task comes before its parent, so ERPNext's "child exists" refusal never fires.
	assert order.index("A1x") < order.index("A1") < order.index("A")
	assert order.index("A2") < order.index("A")


def test_delete_order_lists_a_task_once_and_drops_strangers(rules):
	tasks = [T("A", "A"), T("A1", "A1", "A")]
	assert rules.delete_order(tasks, ["A1", "A", "NOT-HERE", "A1"]) == ["A1", "A"]
	assert rules.delete_order(tasks, []) == []


# ---------------------------------------------------------------------- the endpoints


def test_clean_add_values_keeps_the_dialogs_fields_and_nothing_else(api):
	out = api.clean_add_values(
		{
			"subject": "  Pour   the basin ",
			"parent_task": "TASK-1",
			"is_group": "1",
			"priority": "High",
			"exp_start_date": "2026-11-10",
			"exp_end_date": "",
			"expected_time": "4.5",
			"description": "<p>Notes</p>",
			"assign_to": "pat@example.com",
		}
	)
	assert out == {
		"subject": "Pour the basin",
		"parent_task": "TASK-1",
		"is_group": 1,
		"priority": "High",
		"exp_start_date": "2026-11-10",
		"expected_time": 4.5,
		"description": "<p>Notes</p>",
	}
	with pytest.raises(ValueError, match="cannot be set here: status"):
		api.clean_add_values({"subject": "x", "status": "Completed"})
	with pytest.raises(ValueError, match="subject"):
		api.clean_add_values({"subject": "   "})
	with pytest.raises(ValueError, match="Priority"):
		api.clean_add_values({"subject": "x", "priority": "Now"})
	with pytest.raises(ValueError, match="negative"):
		api.clean_add_values({"subject": "x", "expected_time": -1})


class Site:
	"""One project of tasks; records every delete, savepoint and rollback."""

	def __init__(self, api, tasks, refuse=None):
		self.api = api
		self.tasks = {row["name"]: row for row in tasks}
		self.refuse = refuse or {}
		self.deleted, self.savepoints, self.rollbacks, self.logged = [], [], [], []
		frappe = api.frappe
		frappe.db.exists = lambda doctype, name=None, *a, **k: name if doctype == "Project" else None
		frappe.db.savepoint = lambda name: self.savepoints.append(name)
		frappe.db.rollback = lambda save_point=None, *a, **k: self.rollbacks.append(save_point)
		frappe.get_all = self.get_all
		frappe.delete_doc = self.delete_doc
		frappe.log_error = lambda *a, **k: self.logged.append(k.get("title"))

	def get_all(self, doctype, filters=None, fields=None, **k):
		if doctype == "Task":
			return [dict(row) for row in self.tasks.values() if row.get("project") == filters.get("project")]
		return []

	def delete_doc(self, doctype, name, *a, **k):
		assert self.api.frappe.flags.mute_messages is True, "deletes run with messages muted"
		if name in self.refuse:
			self.api.frappe.throw(self.refuse[name], self.api.frappe.LinkExistsError)
		if any(row.get("parent_task") == name for row in self.tasks.values()):
			self.api.frappe.throw(
				"Child Task exists for this Task. You can not delete this Task.",
				self.api.frappe.ValidationError,
			)
		del self.tasks[name]
		self.deleted.append(name)


def _project_tasks():
	return [
		T("A", "Phase 100", project="PRJ-1", is_group=1),
		T("A1", "Dig", "A", project="PRJ-1"),
		T("A2", "Pour", "A", project="PRJ-1"),
		T("B", "Commission", project="PRJ-1"),
		T("Z", "Other job", project="PRJ-2"),
	]


def test_delete_tasks_goes_deepest_first_and_reports_a_refusal_with_its_reason(api):
	site = Site(api, _project_tasks(), refuse={"A2": "Cannot delete: a Timesheet is linked."})
	out = api.delete_tasks("PRJ-1", json.dumps(["A", "B"]))
	assert site.deleted == ["A1", "B"]
	assert [row["name"] for row in out["kept"]] == ["A2", "A"]
	assert out["kept"][0]["reason"] == "Cannot delete: a Timesheet is linked."
	assert "Child Task exists" in out["kept"][1]["reason"]
	assert out["deleted"] == ["A1", "B"]
	# Each refusal rolled back its own savepoint, the request kept going, and nothing was logged.
	assert site.rollbacks == ["scope_delete_task", "scope_delete_task"]
	assert site.logged == []
	assert api.frappe.flags.mute_messages is False


def test_delete_tasks_refuses_a_task_of_another_project_before_touching_anything(api):
	site = Site(api, _project_tasks())
	with pytest.raises(Refusal, match="Not on PRJ-1: Z"):
		api.delete_tasks("PRJ-1", ["B", "Z"])
	assert site.deleted == []


def test_delete_preview_counts_the_subtasks_under_a_pick(api):
	Site(api, _project_tasks())
	out = api.delete_preview("PRJ-1", ["A"])
	assert (out["picked"], out["under"], out["total"]) == (1, 2, 3)
	assert out["subjects"] == ["Phase 100"]


def test_import_refuses_a_disabled_template_and_writes_nothing_when_all_is_there(api):
	frappe = api.frappe
	frappe.db.exists = lambda doctype, name=None, *a, **k: name
	frappe.db.get_value = lambda doctype, name, field, *a, **k: 1 if field == "disabled" else "PRJ-1"
	with pytest.raises(Refusal, match="disabled"):
		api.import_template("PRJ-1", "Old template")

	frappe.db.get_value = lambda doctype, name, field, *a, **k: 0 if field == "disabled" else "PRJ-1"
	reads = {
		"Project Template Task": [{"parent": "Design", "task": "TPL-PD", "idx": 1}],
		"Task": None,
		"Task Depends On": [],
	}

	def get_all(doctype, filters=None, fields=None, **k):
		if doctype == "Task" and filters.get("is_template"):
			return [T("TPL-PD", "Predesign (PD)", is_template=1)]
		if doctype == "Task":
			return [T("TASK-20", "predesign (pd)", project="PRJ-1", status="Open")]
		return reads.get(doctype, [])

	frappe.get_all = get_all
	touched = []
	frappe.get_doc = lambda *a, **k: touched.append(a)
	out = api.import_template("PRJ-1", "Design")
	assert out["count"] == 0 and out["created"] == []
	assert out["skipped"] == [{"subject": "Predesign (PD)", "existing": "TASK-20"}]
	assert touched == []


# ---------------------------------------------------------------------- the Scope tab's layout


def _custom_fields():
	return {
		row["name"]: row for row in json.loads((FIXTURES / "custom_field.json").read_text(encoding="utf-8"))
	}


def _field_order():
	setters = json.loads((FIXTURES / "property_setter.json").read_text(encoding="utf-8"))
	return json.loads(next(row for row in setters if row["name"] == "Project-main-field_order")["value"])


def test_the_tasks_tree_is_the_first_thing_on_the_scope_tab():
	order = _field_order()
	at = order.index("custom_scope")
	assert order[at + 1 : at + 3] == ["custom_tasks_section", "custom_tasks_html"]
	assert len(order) == len(set(order)), "field_order has a duplicate"
	# The rest of the tab follows the tree, up to the Schedule tab.
	tail = order[at + 3 : order.index("custom_schedule")]
	assert tail[0] == "custom_section_break_w4acr"
	for name in ("custom_design_scope", "custom_build_scope", "custom_service_scope", "custom_rent_scope"):
		assert name in tail, name


def test_insert_after_agrees_with_the_field_order():
	"""v16 places by the array; a Section Break's insert_after that disagrees is a trap for the next edit."""
	fields = _custom_fields()
	assert fields["Project-custom_tasks_section"]["insert_after"] == "custom_scope"
	assert fields["Project-custom_tasks_html"]["insert_after"] == "custom_tasks_section"
	assert fields["Project-custom_section_break_w4acr"]["insert_after"] == "custom_tasks_html"
	assert fields["Project-custom_schedule"]["insert_after"] == "custom_rent_deliverables"
	order = _field_order()
	for name in (
		"custom_tasks_section",
		"custom_tasks_html",
		"custom_section_break_w4acr",
		"custom_schedule",
	):
		after = fields["Project-" + name]["insert_after"]
		assert order.index(after) == order.index(name) - 1, name


def test_the_scope_sections_collapse_only_when_empty():
	fields = _custom_fields()
	for stream in ("design", "build", "service", "rent"):
		section = fields[f"Project-custom_{stream}_scope"]
		assert section["collapsible"] == 1, stream
		rule = section["collapsible_depends_on"]
		assert rule.startswith("eval:"), stream
		assert (
			f"custom_{stream}_customer_requests" in rule and f"custom_{stream}_deliverables" in rule
		), stream
		assert section["hidden"] == 0, stream


# ---------------------------------------------------------------------- the dialogs never route away


def test_the_tree_flows_push_no_history_and_route_nowhere():
	text = TREE_JS.read_text(encoding="utf-8")
	for method in (
		"openTaskDialog",
		"openTemplateDialog",
		"deleteSelected",
		"add_task",
		"import_template",
		"delete_tasks",
	):
		assert method in text, method
	assert "pushState" not in text
	assert "frappe.set_route" not in text
	assert "frappe.new_doc" not in text
	for endpoint in ("get_template_catalog", "import_template", "add_task", "delete_preview", "delete_tasks"):
		assert f"erpnext_enhancements.api.scope_tasks.{endpoint}" in text, endpoint


def test_the_raw_asset_path_is_only_a_fallback():
	"""The bundle's class wins; the year-cached raw path loads only when the bundle lacks it."""
	text = FORM_JS.read_text(encoding="utf-8")
	assert re.search(r"erpnext_enhancements\.TaskTreeManager\s*\?\s*Promise\.resolve\(\)", text)
	assert text.count("frappe.require(") == 1
