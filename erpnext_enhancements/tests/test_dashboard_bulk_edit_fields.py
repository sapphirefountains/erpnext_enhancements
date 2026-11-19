"""The Projects Dashboard's bulk edit, ``project_dashboard.update_multiple_docs`` (v1.832.0), run on a stub.

The rule: the bulk edit accepts only the fields the dashboard's own grid edits, for Project
(``EDITABLE_PROJECT_FIELDS``, shared with ``update_project_details``) and for Task
(``EDITABLE_TASK_FIELDS``: the Tasks tree's start date, end date and expected time cells). A request
that names any other field is refused as a whole, with a message, before anything is written.

Two halves. The first RUNS the endpoint over a stand-in ``frappe`` and records every write: allowed
fields are written (Project and Task), one disallowed field refuses the whole request with nothing
written (whichever doctype, and whichever record in the batch carries it), and malformed input is
refused. The second reads the JavaScript that calls the endpoint and holds the two lists to it, so
the lists and the page cannot drift apart silently.

The module is loaded from its file under a private name with ``frappe`` stubbed in ``sys.modules``
for the life of this suite only (setUpModule / tearDownModule).

Run: python -m unittest erpnext_enhancements.tests.test_dashboard_bulk_edit_fields
"""

import importlib.util
import json
import re
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
MODULE_FILE = APP / "project_enhancements" / "page" / "project_dashboard" / "project_dashboard.py"
TREE_JS = APP / "public" / "js" / "project_enhancements" / "task_tree_manager.js"
DASHBOARD_BLOCK_JS = APP / "custom_html_blocks" / "projects_dashboard.js"
CI_YML = APP.parent / ".github" / "workflows" / "ci.yml"

_STUBBED = (
	"frappe",
	"frappe.utils",
	"erpnext_enhancements.utils.spreadsheet",
	"erpnext_enhancements.task_enhancements.parent_dates",
)
_saved_modules = {}
dashboard = None
SITE = None


class Site:
	"""What the stand-in database holds and what was written to it."""

	def __init__(self):
		self.writes = []
		self.walked = []
		self.writable_projects = {"PROJ-1", "PROJ-2"}
		# task -> (project, parent_task)
		self.tasks = {"TASK-1": ("PROJ-1", None), "TASK-2": ("PROJ-1", "TASK-1"), "TASK-3": ("PROJ-9", None)}
		self.errors = []


def setUpModule():
	for name in _STUBBED:
		_saved_modules[name] = sys.modules.get(name)

	frappe = types.ModuleType("frappe")
	frappe.whitelist = lambda *a, **k: (lambda fn: fn) if not (a and callable(a[0])) else a[0]
	frappe._ = lambda text, *a, **k: text

	def set_value(doctype, name, field, value=None, *a, **k):
		SITE.writes.append((doctype, name, dict(field) if isinstance(field, dict) else {field: value}))

	def get_value(doctype, name, fields=None, *a, **k):
		if doctype == "Task" and name in SITE.tasks:
			return SITE.tasks[name]
		return None

	frappe.db = types.SimpleNamespace(set_value=set_value, get_value=get_value)
	frappe.has_permission = lambda doctype=None, ptype="read", doc=None, **k: (
		doctype == "Project" and doc in SITE.writable_projects
	)
	frappe.get_roles = lambda *a, **k: []
	frappe.get_traceback = lambda *a, **k: "Traceback (most recent call last):\n  stub"
	frappe.log_error = lambda title=None, message=None, **k: SITE.errors.append(title)

	utils = types.ModuleType("frappe.utils")
	for name in ("add_days", "cint", "flt", "getdate", "nowdate"):
		setattr(utils, name, lambda *a, **k: None)
	frappe.utils = utils

	spreadsheet = types.ModuleType("erpnext_enhancements.utils.spreadsheet")
	spreadsheet.build_payload = lambda *a, **k: {}

	# The parent-dates rule has its own suite (test_task_parent_dates.py); here it only has to
	# answer "no refusal" and record which parents were walked.
	parent_dates = types.ModuleType("erpnext_enhancements.task_enhancements.parent_dates")
	parent_dates.DATE_FIELDS = ("exp_start_date", "exp_end_date")
	parent_dates.refusal = lambda task: None
	parent_dates.sync_task_ancestors = lambda parent, *a, **k: SITE.walked.append(parent)

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["erpnext_enhancements.utils.spreadsheet"] = spreadsheet
	sys.modules["erpnext_enhancements.task_enhancements.parent_dates"] = parent_dates

	global dashboard
	spec = importlib.util.spec_from_file_location("_project_dashboard_bulk_edit_under_test", MODULE_FILE)
	dashboard = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(dashboard)


def tearDownModule():
	for name, module in _saved_modules.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def call(projects=None, tasks=None, *, raw_projects=None, raw_tasks=None):
	return dashboard.update_multiple_docs(
		raw_projects if raw_projects is not None else json.dumps(projects or {}),
		raw_tasks if raw_tasks is not None else json.dumps(tasks or {}),
	)


class BulkEditRuns(unittest.TestCase):
	def setUp(self):
		global SITE
		SITE = Site()
		dashboard.check_permission = lambda: True

	# ------------------------------------------------------------ allowed fields are written

	def test_project_fields_the_grid_edits_are_written(self):
		changes = {"status": "Completed", "custom_project_priority": "2", "custom_company_priority": "5"}
		self.assertEqual(call({"PROJ-1": changes}), {"status": "success"})
		self.assertEqual(SITE.writes, [("Project", "PROJ-1", changes)])

	def test_task_fields_the_tree_edits_are_written_and_parents_walked(self):
		changes = {"exp_start_date": "2026-12-01", "exp_end_date": "2026-12-05", "expected_time": 4}
		self.assertEqual(call(tasks={"TASK-2": changes}), {"status": "success"})
		self.assertEqual(SITE.writes, [("Task", "TASK-2", changes)])
		self.assertEqual(SITE.walked, ["TASK-1"])

	def test_a_time_only_edit_walks_no_parent(self):
		self.assertEqual(call(tasks={"TASK-2": {"expected_time": 2}}), {"status": "success"})
		self.assertEqual(SITE.walked, [])

	def test_both_doctypes_in_one_request(self):
		self.assertEqual(
			call({"PROJ-1": {"status": "Open"}}, {"TASK-1": {"expected_time": 1}}), {"status": "success"}
		)
		self.assertEqual(
			SITE.writes,
			[("Project", "PROJ-1", {"status": "Open"}), ("Task", "TASK-1", {"expected_time": 1})],
		)

	def test_an_empty_request_writes_nothing(self):
		self.assertEqual(call(), {"status": "success"})
		self.assertEqual(call(raw_projects="", raw_tasks=""), {"status": "success"})
		self.assertEqual(SITE.writes, [])

	# ------------------------------------------------- one disallowed field refuses it all

	def assertRefusedWhole(self, answer):
		self.assertEqual(answer["status"], "error")
		self.assertIn("not editable from the dashboard", answer["message"])
		self.assertEqual(SITE.writes, [])
		self.assertEqual(SITE.walked, [])

	def test_a_disallowed_project_field_refuses_the_whole_request(self):
		self.assertRefusedWhole(
			call({"PROJ-1": {"status": "Open", "owner": "someone@example.com"}}, {"TASK-1": {"expected_time": 1}})
		)

	def test_a_disallowed_task_field_refuses_the_whole_request(self):
		self.assertRefusedWhole(
			call({"PROJ-1": {"status": "Open"}}, {"TASK-1": {"expected_time": 1, "project": "PROJ-2"}})
		)

	def test_a_disallowed_field_on_a_later_record_stops_the_earlier_records_too(self):
		self.assertRefusedWhole(
			call(
				{"PROJ-1": {"status": "Open"}, "PROJ-2": {"naming_series": "X-"}},
				{"TASK-1": {"expected_time": 1}},
			)
		)
		SITE.writes.clear()
		self.assertRefusedWhole(
			call(tasks={"TASK-1": {"expected_time": 1}, "TASK-2": {"status": "Working"}})
		)

	def test_a_field_allowed_for_the_other_doctype_is_still_refused(self):
		# status is a Project field here, not a Task field; the dates are Task fields, not Project ones.
		self.assertRefusedWhole(call(tasks={"TASK-1": {"status": "Working"}}))
		self.assertRefusedWhole(call({"PROJ-1": {"exp_start_date": "2026-12-01"}}))

	def test_the_refusal_does_not_echo_the_requested_names(self):
		answer = call({"PROJ-1": {"<b>x</b>": 1}})
		self.assertNotIn("<b>", answer["message"])

	# ----------------------------------------------------------------- malformed input

	def test_malformed_input_is_refused_and_writes_nothing(self):
		for raw in ('["status"]', '"status"', "5", "null", '{"PROJ-1": "Completed"}', '{"PROJ-1": ["status"]}'):
			with self.subTest(projects=raw):
				answer = call(raw_projects=raw)
				self.assertEqual(answer["status"], "error")
				self.assertEqual(SITE.writes, [])
			with self.subTest(tasks=raw):
				answer = call(raw_tasks=raw)
				self.assertEqual(answer["status"], "error")
				self.assertEqual(SITE.writes, [])

	def test_invalid_json_is_refused_and_writes_nothing(self):
		self.assertEqual(call(raw_projects="{not json")["status"], "error")
		self.assertEqual(call(raw_tasks="{not json")["status"], "error")
		self.assertEqual(SITE.writes, [])

	# ------------------------------------------------- the rest of the endpoint is unchanged

	def test_the_page_permission_gate_still_comes_first(self):
		dashboard.check_permission = lambda: False
		answer = call({"PROJ-1": {"status": "Open"}})
		self.assertEqual(answer["status"], "error")
		self.assertIn("permission", answer["message"])
		self.assertEqual(SITE.writes, [])

	def test_the_per_record_write_check_still_applies(self):
		answer = call({"PROJ-9": {"status": "Open"}})
		self.assertEqual(answer["status"], "error")
		self.assertIn("No write permission for Project PROJ-9", answer["message"])
		answer = call(tasks={"TASK-3": {"expected_time": 1}})
		self.assertEqual(answer["status"], "error")
		self.assertIn("No write permission for parent Project of Task TASK-3", answer["message"])
		self.assertEqual(SITE.writes, [])


class ListsMatchThePage(unittest.TestCase):
	"""The lists and the JavaScript that sends to them cannot drift apart silently."""

	@classmethod
	def setUpClass(cls):
		cls.tree = TREE_JS.read_text(encoding="utf-8")
		cls.block = DASHBOARD_BLOCK_JS.read_text(encoding="utf-8")

	def test_the_task_list_is_exactly_the_cells_the_tree_edits(self):
		# Date cells: renderDateCell(task, "<field>", ...) draws .editable-date; the dialog sends
		# that cell's data-field.
		date_fields = set(re.findall(r'renderDateCell\(task,\s*"(\w+)"', self.tree))
		# Time cell: a literal .editable-time cell with its own data-field.
		time_fields = set(re.findall(r'editable-time[^`]*?data-field="(\w+)"', self.tree))
		self.assertTrue(date_fields and time_fields, "the tree draws editable date and time cells")
		edited = date_fields | time_fields
		self.assertEqual(
			edited,
			set(dashboard.EDITABLE_TASK_FIELDS),
			"EDITABLE_TASK_FIELDS must list exactly the Task fields the tree's inline editor sends",
		)

	def test_the_tree_queues_a_change_only_from_the_two_editors(self):
		# Every call that queues a field for update_multiple_docs passes the cell's data-field
		# (read back from a cell the previous test covers), never a literal of its own.
		calls = re.findall(r"updatePendingChange\(([^)]*)\)", self.tree)
		callers = [c for c in calls if c.strip() != "taskId, field, value"]
		self.assertEqual(len(callers), 2, callers)
		for args in callers:
			self.assertTrue(args.startswith("taskId, field,"), args)
		cells = re.findall(r'\.data\("field"\)', self.tree)
		self.assertGreaterEqual(len(cells), 2)

	def test_the_tree_is_the_only_caller_and_sends_no_project_changes(self):
		needle = "update_multiple_docs"
		hits = []
		for pattern in ("*.js", "*.html", "*.json", "*.py"):
			for path in APP.rglob(pattern):
				if "tests" in path.parts or "node_modules" in path.parts or path == MODULE_FILE:
					continue
				if needle in path.read_text(encoding="utf-8", errors="ignore"):
					hits.append(path)
		js_hits = [p for p in hits if p.suffix in (".js", ".html", ".json")]
		self.assertEqual(js_hits, [TREE_JS], "another page now calls the bulk edit; add its fields to the lists")
		call_site = self.tree.split(needle)[1][:600]
		self.assertIn('project_updates: "{}"', call_site)

	def test_the_project_list_is_the_fields_the_dashboard_edits(self):
		priority_cells = set(re.findall(r"build_editable_priority_cell\(p\.name,\s*'(\w+)'", self.block))
		inline_selects = set(re.findall(r'project-inline-edit" data-field="(\w+)"', self.block))
		self.assertTrue(priority_cells and inline_selects)
		self.assertEqual(
			priority_cells | inline_selects,
			set(dashboard.EDITABLE_PROJECT_FIELDS),
			"EDITABLE_PROJECT_FIELDS must list exactly the Project fields the dashboard edits",
		)

	def test_the_lists_are_named_constants_the_endpoint_reads(self):
		source = MODULE_FILE.read_text(encoding="utf-8")
		body = source.split("def update_multiple_docs(")[1].split("\n@frappe.whitelist(")[0]
		self.assertIn("EDITABLE_PROJECT_FIELDS", body)
		self.assertIn("EDITABLE_TASK_FIELDS", body)
		self.assertIn('@frappe.whitelist(methods=["POST"])\ndef update_multiple_docs(', source)

	def test_the_suite_is_wired_into_ci(self):
		ci = CI_YML.read_text(encoding="utf-8")
		self.assertIn("erpnext_enhancements.tests.test_dashboard_bulk_edit_fields", ci)


if __name__ == "__main__":
	unittest.main()
