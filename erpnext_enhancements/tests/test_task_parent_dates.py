"""Bench-free pytest suite for drag-to-parent on the Tasks Tree and parent dates that follow the
subtasks (v1.782.0; Nikolas, 2026-11-03: "Make any task a parent by simple drag and drop ... make
rigid start and end dates based off the child tasks so a child task is always within the range of its
parent task").

``task_enhancements/parent_dates.py`` (the rule and its hooks), ``api/scope_tasks.move_tasks`` (the
tree's save), the override controller's promotion, the one-shot patch, and the wiring in
``hooks.py``, the tree's JS, the Task form script and the stylesheet.

What fails expensively and quietly here, and so is pinned:

* the parent promoted to a group **after** ERPNext's ``validate_parent_is_group`` has already thrown
  (the ``before_save`` bug: every drop onto a leaf task refused, for as long as the tree existed);
* the parent widened **after** ERPNext's ``validate_parent_expected_end_date`` reads it, or the
  Project window widened before this hook settles the dates (hook order in ``hooks.py``);
* a hook that raises out of a Task save, or a patch that raises out of ``bench migrate`` (the deploy);
* a parent's own date wiped because an **undated** subtask was dropped under it;
* a move saved half-way (the old endpoint's "rolls back on exception" that never did), a refusal
  swallowed by the tree, ``frappe.flags.mute_messages`` left on;
* the tree saving every visible row on each drag, or a cycle (a task under its own subtask);
* SortableJS back on ``@latest`` from a CDN, or the pinned file name not matching the vendored file;
* the inline editor or the Gantt writing a parent's dates straight to the table, past the hooks;
* a dialog flow that pushes browser history (Nik: never break Back and Forward).

A plain pytest module with its own ``frappe`` stub, so CI runs it in a ``python -m pytest`` step of
its own (``python -m unittest`` cannot collect function tests).
"""

import ast
import copy
import datetime
import importlib
import re
import sys
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
TREE_JS = APP / "public/js/project_enhancements/task_tree_manager.js"
FORM_JS = APP / "task_enhancements/doctype/task/task.js"
TREE_CSS = APP / "public/css/project_enhancements/task_tree.css"
DASHBOARD_PY = APP / "project_enhancements/page/project_dashboard/project_dashboard.py"
OVERRIDE_PY = APP / "task_enhancements/doctype/task/task.py"
HOOKS_PY = APP / "hooks.py"
PATCHES_TXT = APP / "patches.txt"
CI_YML = APP.parent / ".github/workflows/ci.yml"

D = datetime.datetime


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


class Doc:
	"""A Task as ``frappe.get_doc`` hands it over: attributes, ``save`` writes back to the site."""

	def __init__(self, site, row, new=False):
		self._site = site
		self._new = new
		self._before = copy.deepcopy(row)
		self.doctype = "Task"
		for key, value in row.items():
			setattr(self, key, value)
		self.flags = types.SimpleNamespace()

	def get(self, key, default=None):
		return getattr(self, key, default)

	def set(self, key, value):
		setattr(self, key, value)

	def is_new(self):
		return self._new

	def get_doc_before_save(self):
		return types.SimpleNamespace(**self._before)

	def save(self, ignore_permissions=False):
		self._site.saved.append(self.name)
		if self._site.refuse_save and self.name in self._site.refuse_save:
			raise self._site.refuse_save[self.name]
		row = self._site.tasks.setdefault(self.name, {})
		for key in list(vars(self)):
			if key.startswith("_") or key in ("flags", "doctype"):
				continue
			row[key] = getattr(self, key)


class Site:
	"""The Task table and the frappe calls the modules under test make."""

	def __init__(self, tasks):
		self.tasks = {t["name"]: dict(t) for t in tasks}
		self.writes = []
		self.saved = []
		self.logged = []
		self.savepoints = []
		self.rollbacks = []
		self.refuse_save = {}
		self._snapshots = {}
		self.fail_reads = None

	# -- reads
	def _match(self, row, filters):
		for key, cond in (filters or {}).items():
			value = row.get(key)
			if isinstance(cond, list | tuple):
				op, arg = cond
				if op == "in" and value not in arg:
					return False
				if op == "!=" and value == arg:
					return False
				if op == "is" and arg == "not set" and value:
					return False
			elif value != cond:
				return False
		return True

	def get_all(self, doctype, filters=None, fields=None, **kw):
		if self.fail_reads:
			raise self.fail_reads
		assert doctype == "Task", doctype
		rows = [row for row in self.tasks.values() if self._match(row, filters)]
		if fields:
			return [{f: row.get(f) for f in fields} for row in rows]
		return [dict(row) for row in rows]

	def get_value(self, doctype, name, fieldname=None, as_dict=False, **kw):
		if doctype != "Task":
			return None
		row = self.tasks.get(name)
		if not row:
			return None
		if isinstance(fieldname, list | tuple):
			if as_dict:
				return {f: row.get(f) for f in fieldname}
			return tuple(row.get(f) for f in fieldname)
		return row.get(fieldname)

	def exists(self, doctype, filters=None):
		if doctype == "Task" and isinstance(filters, dict):
			return any(self._match(row, filters) for row in self.tasks.values()) or None
		if doctype == "Task":
			return filters in self.tasks
		return True

	def get_doc(self, doctype, name):
		assert doctype == "Task"
		return Doc(self, dict(self.tasks[name]))

	# -- writes
	def set_value(self, doctype, name, field, value=None, update_modified=True, **kw):
		assert doctype == "Task"
		changes = field if isinstance(field, dict) else {field: value}
		self.writes.append((name, dict(changes), update_modified))
		self.tasks[name].update(changes)

	def savepoint(self, name):
		self.savepoints.append(name)
		self._snapshots[name] = copy.deepcopy(self.tasks)

	def rollback(self, save_point=None):
		self.rollbacks.append(save_point)
		if save_point in self._snapshots:
			self.tasks = copy.deepcopy(self._snapshots[save_point])


def install_frappe_stub(site):
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

	class PermissionError(Exception):
		pass

	class DocumentLockedError(ValidationError):
		pass

	class QueryDeadlockError(Exception):
		pass

	class QueryTimeoutError(Exception):
		pass

	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError
	frappe.DocumentLockedError = DocumentLockedError
	frappe.LinkExistsError = ValidationError
	frappe.QueryDeadlockError = QueryDeadlockError
	frappe.QueryTimeoutError = QueryTimeoutError
	frappe.db = types.SimpleNamespace(
		has_column=lambda *a, **k: True,
		exists=site.exists,
		get_value=site.get_value,
		set_value=site.set_value,
		savepoint=site.savepoint,
		rollback=site.rollback,
		delete=lambda *a, **k: None,
		sql=lambda *a, **k: [],
	)
	frappe.get_all = site.get_all
	frappe.get_doc = site.get_doc
	frappe.has_permission = lambda *a, **k: True
	frappe.log_error = lambda *a, **k: site.logged.append(k.get("title") or (a[0] if a else ""))
	frappe.get_traceback = lambda *a, **k: "traceback"
	frappe.get_app_path = lambda *parts: str(APP.joinpath(*parts[1:]))
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	return frappe


def _fresh(prefixes):
	"""Forget the modules under ``prefixes`` so the next import binds the stub installed now. The
	parent package's attribute goes too: ``from package import module`` hands back the attribute
	when it is there, without looking at ``sys.modules``, so a stale module would come back with
	the previous stub's exception classes."""
	for name in list(sys.modules):
		if any(name.startswith(p) for p in prefixes):
			del sys.modules[name]
			parent, _, child = name.rpartition(".")
			if parent in sys.modules and hasattr(sys.modules[parent], child):
				delattr(sys.modules[parent], child)


def T(name, parent=None, start=None, end=None, **extra):
	return dict(
		{
			"name": name,
			"subject": name.lower(),
			"project": "PRJ-1",
			"parent_task": parent,
			"exp_start_date": start,
			"exp_end_date": end,
			"is_group": 0,
		},
		**extra,
	)


#: A build: a group, its two subtasks, one of them a group with a subtask of its own, and a stranger.
TREE = [
	T("P", None, "2026-11-10", "2026-11-20", is_group=1),
	T("A", "P", "2026-11-10", "2026-11-12"),
	T("B", "P", "2026-11-14", "2026-11-20", is_group=1),
	T("B1", "B", "2026-11-14", "2026-11-18"),
	T("X", None, "2026-12-01", "2026-12-02"),
]


@pytest.fixture()
def site():
	return Site(TREE)


@pytest.fixture()
def pd(site):
	frappe = install_frappe_stub(site)
	_fresh(
		(
			"erpnext_enhancements.task_enhancements.parent_dates",
			"erpnext_enhancements.project_enhancements.project_window",
		)
	)
	module = importlib.import_module("erpnext_enhancements.task_enhancements.parent_dates")
	module.frappe = frappe
	return module


@pytest.fixture()
def api(site):
	frappe = install_frappe_stub(site)
	_fresh(("erpnext_enhancements.api.scope_tasks", "erpnext_enhancements.project_enhancements.scope_tasks"))
	module = importlib.import_module("erpnext_enhancements.api.scope_tasks")
	module.frappe = frappe
	module.rules.frappe = frappe
	return module


# ---------------------------------------------------------------------- the rule, pure


def test_when_reads_every_form_the_desk_and_the_database_hand_over(pd):
	assert pd.when(None) is None and pd.when("") is None
	assert pd.when("2026-11-03") == D(2026, 11, 3)
	assert pd.when("2026-11-03 08:30:00") == D(2026, 11, 3, 8, 30)
	assert pd.when("2026-11-03 08:30:00.123456") == D(2026, 11, 3, 8, 30)
	assert pd.when(datetime.date(2026, 11, 3)) == D(2026, 11, 3)
	assert pd.when(D(2026, 11, 3, 1)) == D(2026, 11, 3, 1)
	assert pd.when("not a date") is None


def test_window_is_the_earliest_start_and_latest_end_ignoring_blanks(pd):
	assert pd.window([("2026-11-12", None), (None, "2026-11-20"), ("2026-11-10", "2026-11-11")]) == (
		D(2026, 11, 10),
		D(2026, 11, 20),
	)
	assert pd.window([]) == (None, None)
	assert pd.window([(None, None)]) == (None, None)


def test_a_parent_keeps_its_own_date_where_no_subtask_has_one(pd):
	"""Dropping an undated task under a dated one must not wipe the dated one's plan."""
	assert pd.derived(("2026-11-10", "2026-11-20"), (None, None)) == (D(2026, 11, 10), D(2026, 11, 20))
	assert pd.derived(("2026-11-10", "2026-11-20"), (D(2026, 11, 12), None)) == (
		D(2026, 11, 12),
		D(2026, 11, 20),
	)
	assert pd.derived((None, None), (D(2026, 11, 12), D(2026, 11, 13))) == (D(2026, 11, 12), D(2026, 11, 13))


def test_derive_all_lets_a_grandparent_see_the_parent_as_the_rule_leaves_it(pd):
	rows = [
		T("G", None, "2026-01-01", "2026-01-02", is_group=1),
		T("P", "G", "2026-01-01", "2026-01-02", is_group=1),
		T("C", "P", "2026-03-01", "2026-03-09"),
		T("L", None, "2026-05-05", "2026-05-06"),
	]
	out = pd.derive_all(rows)
	assert set(out) == {"G", "P"}, "only tasks with children"
	assert out["P"] == (D(2026, 3, 1), D(2026, 3, 9))
	assert out["G"] == (D(2026, 3, 1), D(2026, 3, 9)), "from P's derived dates, not its stored ones"


def test_derive_all_cuts_a_parent_cycle(pd):
	rows = [T("A", "B", "2026-01-01", "2026-01-02"), T("B", "A", "2026-02-01", "2026-02-02")]
	out = pd.derive_all(rows)
	assert set(out) == {"A", "B"}


# ---------------------------------------------------------------------- the writes


def test_sync_walks_up_every_ancestor_and_writes_only_what_differs(pd, site):
	site.tasks["B1"].update(exp_end_date="2026-12-24")
	changed = pd.sync_task_ancestors("B")
	assert [name for name, _ in changed] == ["B", "P"]
	assert site.tasks["B"]["exp_end_date"] == D(2026, 12, 24)
	assert site.tasks["P"]["exp_end_date"] == D(2026, 12, 24)
	assert site.tasks["P"]["exp_start_date"] == "2026-11-10", "unchanged, so not written"
	assert all(not update_modified for _, _, update_modified in site.writes)
	assert pd.sync_task_ancestors("B") == [], "idempotent"


def test_sync_counts_a_subtask_about_to_move_in_and_leaves_an_excluded_one_out(pd, site):
	# X is not under P in the database yet: the override stands in for the save in flight.
	changed = pd.sync_task_ancestors("P", override={"X": ("2026-12-01", "2026-12-02")})
	assert dict(changed)["P"] == {"exp_end_date": D(2026, 12, 2)}
	# Without B (and so B1), P's end is A's.
	site.tasks["P"].update(exp_end_date="2026-11-20")
	changed = pd.sync_task_ancestors("P", exclude={"B"})
	assert dict(changed)["P"] == {"exp_end_date": D(2026, 11, 12)}


def test_before_validate_widens_the_parent_for_the_dates_this_save_carries(pd, site):
	"""The drag that used to fail: a child whose end is later than the parent's stored end. By the time
	ERPNext's validate_parent_expected_end_date reads the parent, the parent must reach the child."""
	doc = Doc(site, dict(site.tasks["A"]))
	doc.exp_end_date = "2026-12-31"
	pd.before_task_validate(doc)
	assert site.tasks["P"]["exp_end_date"] == D(2026, 12, 31)


def test_before_validate_re_derives_a_parents_own_dates_from_its_subtasks(pd, site):
	"""A Desk form posts the read-only fields back; the hook makes them the subtasks' again."""
	doc = Doc(site, dict(site.tasks["B"]))
	doc.exp_start_date = "2026-01-01"
	doc.exp_end_date = "2026-12-31"
	pd.before_task_validate(doc)
	assert (doc.exp_start_date, doc.exp_end_date) == (D(2026, 11, 14), D(2026, 11, 18))
	# ... and P follows what B now carries: its end shrinks to B's (A ends earlier).
	assert site.tasks["P"]["exp_end_date"] == D(2026, 11, 18)


def test_before_validate_on_a_new_task_only_widens_the_parent(pd, site):
	doc = Doc(site, T("NEW", "P", "2026-11-01", "2026-11-02"), new=True)
	pd.before_task_validate(doc)
	assert site.tasks["P"]["exp_start_date"] == D(2026, 11, 1)
	assert (doc.exp_start_date, doc.exp_end_date) == ("2026-11-01", "2026-11-02")


def test_on_update_recomputes_the_new_parent_and_the_old_one_after_a_move(pd, site):
	# B1 moves from B to P: the database already holds the move when on_update runs.
	before = dict(site.tasks["B1"])
	site.tasks["B1"]["parent_task"] = "P"
	doc = Doc(site, dict(site.tasks["B1"]))
	doc._before = before
	pd.on_task_update(doc)
	assert site.tasks["B"]["exp_start_date"] == "2026-11-14", "B keeps its own: no subtask left"
	assert site.tasks["B"]["exp_end_date"] == "2026-11-20"
	assert site.tasks["P"]["exp_end_date"] == "2026-11-20"


def test_after_delete_recomputes_the_parent_from_the_subtasks_left(pd, site):
	gone = Doc(site, dict(site.tasks.pop("B1")))
	site.tasks["B"].update(exp_start_date="2026-11-14", exp_end_date="2026-11-18")
	pd.after_task_delete(gone)
	# B has no subtask left, so it keeps what it had.
	assert site.tasks["B"]["exp_start_date"] == "2026-11-14"
	# P follows A and B: its end is B's now.
	assert site.tasks["P"]["exp_start_date"] == "2026-11-10"
	assert site.tasks["P"]["exp_end_date"] == D(2026, 11, 18)


def test_the_hooks_never_raise_and_log_instead(pd, site):
	site.fail_reads = RuntimeError("boom")
	doc = Doc(site, dict(site.tasks["A"]))
	assert pd.before_task_validate(doc) is None
	assert pd.on_task_update(doc) is None
	assert pd.after_task_delete(doc) is None
	assert site.logged == [pd.LOG_TITLE] * 3


def test_a_failure_of_the_database_itself_is_raised(pd, site):
	site.fail_reads = pd.frappe.QueryDeadlockError("deadlock")
	with pytest.raises(pd.frappe.QueryDeadlockError):
		pd.before_task_validate(Doc(site, dict(site.tasks["A"])))


def test_a_doc_that_is_not_a_task_is_left_alone(pd, site):
	other = types.SimpleNamespace(doctype="Project", name="PRJ-1", parent_task="P")
	assert pd.before_task_validate(other) is None
	assert site.writes == []


def test_refusal_names_a_task_with_subtasks_and_passes_a_leaf(pd):
	assert "follow its subtasks" in pd.refusal("P")
	assert pd.refusal("A") is None
	assert pd.refusal(None) is None


# ---------------------------------------------------------------------- sync_all and the patch


def test_sync_all_is_one_bottom_up_pass_and_idempotent(pd, site):
	site.tasks["B1"].update(exp_end_date="2026-12-24")
	assert pd.sync_all() == (2, 2)
	assert site.tasks["P"]["exp_end_date"] == D(2026, 12, 24)
	assert pd.sync_all() == (2, 0)


def test_the_patch_cannot_raise(pd, site):
	_fresh(("erpnext_enhancements.patches.sync_parent_task_dates",))
	patch = importlib.import_module("erpnext_enhancements.patches.sync_parent_task_dates")
	patch.frappe = pd.frappe
	site.fail_reads = RuntimeError("the table is locked")
	patch.execute()
	assert site.logged == [patch.LOG_TITLE]
	site.fail_reads = None
	site.tasks["B1"].update(exp_end_date="2026-12-24")
	patch.execute()
	assert site.tasks["P"]["exp_end_date"] == D(2026, 12, 24)


def test_the_patch_is_listed_once(pd):
	lines = [line.strip() for line in PATCHES_TXT.read_text(encoding="utf-8").splitlines()]
	assert lines.count("erpnext_enhancements.patches.sync_parent_task_dates") == 1


# ---------------------------------------------------------------------- move_tasks


def _rows(*items):
	return [{"name": n, "parent_task": p, "custom_subtask_order": o} for n, p, o in items]


def test_move_saves_only_the_rows_whose_parent_or_position_changed(api, site):
	for name, order in (("P", 1), ("A", 1), ("B", 2), ("B1", 1), ("X", 2)):
		site.tasks[name]["custom_subtask_order"] = order
	# The tree sends every visible row; only X (now under P, third) differs.
	out = api.move_tasks("PRJ-1", _rows(("P", None, 1), ("A", "P", 1), ("B", "P", 2), ("X", "P", 3)))
	assert out == {"moved": ["X"], "converted": [], "refused": None}
	assert site.saved == ["X"]
	assert site.tasks["X"]["parent_task"] == "P" and site.tasks["X"]["custom_subtask_order"] == 3


def test_a_leaf_dropped_on_becomes_a_group_before_the_child_is_saved(api, site):
	out = api.move_tasks("PRJ-1", _rows(("X", "A", None)))
	assert out["converted"] == ["A"] and out["moved"] == ["X"]
	assert site.saved == ["A", "X"], "the parent first, so ERPNext's validate_parent_is_group passes"
	assert site.tasks["A"]["is_group"] == 1


def test_a_missing_position_lands_after_the_parents_last_subtask(api, site):
	site.tasks["A"]["custom_subtask_order"] = 4
	site.tasks["B"]["custom_subtask_order"] = 9
	api.move_tasks("PRJ-1", _rows(("X", "P", None)))
	assert site.tasks["X"]["custom_subtask_order"] == 10


def test_a_task_cannot_go_under_itself_or_its_own_subtask(api):
	with pytest.raises(Refusal, match="own subtasks"):
		api.move_tasks("PRJ-1", _rows(("P", "B1", None)))
	with pytest.raises(Refusal, match="own parent"):
		api.move_tasks("PRJ-1", _rows(("P", "P", None)))


def test_erpnexts_refusal_rolls_the_whole_move_back_and_comes_back_as_a_reason(api, site):
	site.refuse_save["B"] = api.frappe.ValidationError(
		"Cannot add an open task under completed Parent Task X."
	)
	out = api.move_tasks("PRJ-1", _rows(("A", "X", None), ("B", "X", None)))
	assert out["moved"] == [] and out["converted"] == []
	assert out["refused"] == {
		"name": "B",
		"subject": "b",
		"reason": "Cannot add an open task under completed Parent Task X.",
	}
	assert site.rollbacks == ["scope_move_tasks"]
	assert site.tasks["A"]["parent_task"] == "P", "A's move was rolled back with B's"
	assert site.tasks["X"]["is_group"] == 0, "and so was the promotion"
	assert api.frappe.flags.mute_messages is False
	assert site.logged == [], "an expected refusal is not an error"


def test_an_unexpected_failure_is_logged_and_reported_as_such(api, site):
	site.refuse_save["X"] = RuntimeError("disk full")
	out = api.move_tasks("PRJ-1", _rows(("X", "P", None)))
	assert out["refused"]["reason"] == "An error was logged."
	assert site.logged == [api.LOG_TITLE]
	assert site.rollbacks == ["scope_move_tasks"]


def test_a_task_off_the_project_is_refused_before_anything_is_saved(api, site):
	with pytest.raises(Refusal, match="Not on"):
		api.move_tasks("PRJ-1", _rows(("NOPE", "P", None)))
	assert site.saved == []


def test_the_old_endpoint_delegates_and_keeps_its_contract(api):
	source = DASHBOARD_PY.read_text(encoding="utf-8")
	body = source.split("def update_task_structure(")[1].split("\n@frappe.whitelist()")[0]
	assert "scope_tasks.move_tasks(" in body
	assert "task_doc.save(ignore_permissions=True)" not in body
	assert '"status": "error"' in body and '"status": "success"' in body


# ---------------------------------------------------------------------- wiring


def _task_doc_events():
	tree = ast.parse(HOOKS_PY.read_text(encoding="utf-8"))
	for node in ast.walk(tree):
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "doc_events" for t in node.targets):
			for key, value in zip(node.value.keys, node.value.values, strict=True):
				if isinstance(key, ast.Constant) and key.value == "Task":
					return ast.literal_eval(value)
	raise AssertionError("doc_events['Task'] not found in hooks.py")


def test_the_hooks_are_wired_in_the_order_the_validation_needs():
	events = _task_doc_events()
	before = events["before_validate"]
	assert isinstance(before, list)
	assert before.index(
		"erpnext_enhancements.task_enhancements.parent_dates.before_task_validate"
	) < before.index(
		"erpnext_enhancements.project_enhancements.project_window.before_task_validate"
	), "the parent is settled before the Project window is widened for it"
	assert "erpnext_enhancements.task_enhancements.parent_dates.on_task_update" in events["on_update"]
	assert events["after_delete"] == "erpnext_enhancements.task_enhancements.parent_dates.after_task_delete"


def test_the_override_promotes_the_parent_in_before_validate_not_before_save(site):
	"""frappe runs before_validate, validate, before_save; ERPNext's validate throws
	ParentIsGroupError, so a promotion in before_save never got its turn."""
	frappe = install_frappe_stub(site)
	erpnext = types.ModuleType("erpnext")
	projects = types.ModuleType("erpnext.projects")
	doctype = types.ModuleType("erpnext.projects.doctype")
	task_pkg = types.ModuleType("erpnext.projects.doctype.task")
	task_mod = types.ModuleType("erpnext.projects.doctype.task.task")

	class BaseTask:
		def validate(self):
			pass

		def validate_status(self):
			pass

		def on_update(self):
			pass

		def update_status(self):
			pass

	task_mod.Task = BaseTask
	for name, mod in (
		("erpnext", erpnext),
		("erpnext.projects", projects),
		("erpnext.projects.doctype", doctype),
		("erpnext.projects.doctype.task", task_pkg),
		("erpnext.projects.doctype.task.task", task_mod),
	):
		sys.modules[name] = mod
	_fresh(("erpnext_enhancements.task_enhancements.doctype",))
	module = importlib.import_module("erpnext_enhancements.task_enhancements.doctype.task.task")
	module.frappe = frappe
	assert "before_validate" in module.Task.__dict__
	assert "before_save" not in module.Task.__dict__
	child = module.Task.__new__(module.Task)
	child.parent_task = "A"
	module.Task.before_validate(child)
	assert site.tasks["A"]["is_group"] == 1 and site.saved == ["A"]


def test_direct_date_writers_ask_the_rule_and_walk_the_parents():
	source = DASHBOARD_PY.read_text(encoding="utf-8")
	for fn in ("update_task_date", "update_multiple_docs", "update_task_dates_from_gantt"):
		body = source.split(f"def {fn}(")[1].split("\n@frappe.whitelist()")[0]
		assert "parent_dates.refusal(" in body, fn
		assert "parent_dates.sync_task_ancestors(" in body, fn


def test_sortablejs_is_pinned_and_vendored():
	js = TREE_JS.read_text(encoding="utf-8")
	assert "@latest" not in js and "cdn.jsdelivr.net" not in js
	match = re.search(r'SORTABLE_ASSET =\s*"(/assets/erpnext_enhancements/js/[^"]+)"', js)
	assert match, "the pinned asset path"
	asset = APP / "public" / match.group(1).split("/assets/erpnext_enhancements/")[1]
	assert asset.is_file(), asset
	version = re.search(r"sortable-(\d+\.\d+\.\d+)\.min\.js$", asset.name).group(1)
	head = asset.read_text(encoding="utf-8")[:400]
	assert f"Sortable {version}" in head, "the file name carries the version the file says"
	assert (asset.parent / f"sortable-{version}.LICENSE").is_file()
	assert "(t=t||self).Sortable=e()" in head, "a UMD build that sets window.Sortable"


def test_the_tree_shows_the_drop_target_offers_move_under_and_surfaces_a_refusal():
	js = TREE_JS.read_text(encoding="utf-8")
	css = TREE_CSS.read_text(encoding="utf-8")
	# The nest band is the middle half, named on the row.
	assert "relY > h * 0.25 && relY < h * 0.75" in js
	assert '"Make subtask of {0}"' in js and 'attr("data-drop-label"' in js
	assert "content: attr(data-drop-label)" in css
	# The reorder edge is drawn where SortableJS will insert.
	assert 'evt.willInsertAfter ? "drop-after" : "drop-before"' in js
	assert ".task-grid-row.drop-before" in css and ".task-grid-row.drop-after" in css
	# A hint while dragging.
	assert 'find(".task-drag-hint").show()' in js and 'find(".task-drag-hint").hide()' in js
	# Move under... per row, a dialog, no route change.
	assert 'class="btn btn-xs btn-default move-task-btn' in js
	assert 'this.wrapper.on("click", ".move-task-btn"' in js
	move = js.split("openMoveDialog(taskId, subject) {")[1].split("openTemplateDialog() {")[0]
	assert "new frappe.ui.Dialog" in move and "set_route" not in move and "pushState" not in js
	# The save goes through move_tasks; a refusal is a dialog and the tree is re-fetched either way.
	save = js.split("saveStructure(rows,")[1].split("\n};")[0]
	assert '"erpnext_enhancements.api.scope_tasks.move_tasks"' in save
	assert "out.refused" in save and "frappe.msgprint(" in save
	assert "call.then(settle, settle)" in save and "me.fetchData()" in save
	assert "project_dashboard.update_task_structure" not in js
	# A parent's dates are not inline-editable; the editor binds to the link the cell no longer has.
	assert 'class="task-grid-cell derived-date' in js
	assert 'this.wrapper.on("click", ".editable-date a"' in js


def test_the_task_form_locks_a_parents_dates():
	js = FORM_JS.read_text(encoding="utf-8")
	assert 'EE_PARENT_DATE_FIELDS = ["exp_start_date", "exp_end_date"]' in js
	assert 'frappe.db.count("Task", { filters: { parent_task: frm.doc.name } })' in js
	assert 'set_df_property(field, "read_only"' in js


def test_this_suite_runs_in_ci():
	assert "erpnext_enhancements/tests/test_task_parent_dates.py" in CI_YML.read_text(encoding="utf-8")
