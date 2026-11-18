"""Bench-free checks that the operations-side endpoints check access before they read or write.

Each test reads the module's source with ``ast`` (no ``frappe`` import) and asserts that the
check is in the function and comes before the first read. ``ast.unparse`` drops comments and
docstrings, so prose that names a check cannot satisfy these assertions; needles are written in
``ast.unparse``'s own spelling (single quotes). Two small helpers are also executed against a
fake ``frappe`` to pin their behavior: the task tree's node cap and the sign-off request's
assignment and attempt rules.

What is pinned:

- Maintenance: ``get_dashboard_context`` needs Project read and a maintenance role;
  ``get_visit_payload`` needs Record create/write or read on what it names. The Visit Wizard's
  bootstrap calls the unchecked builders after its own record check.
- The three "Create > Maintenance Contract" mappers need source read and Contract create; the
  signing hook maps through the unchecked function.
- The Task tree needs Task read and stops at ``MAX_TREE_NODES``.
- Change Order, Budget Reallocation and Subcontractor Scorecard lists need their own doctype's
  read; the scorecard history clamps ``limit``.
- Project comments, the Projects Dashboard readers and the master-project ordering check the
  Project; the portfolio Gantt reader has the page gate and reads through ``get_list``.
- Opportunity tag sync needs write and leaves ``modified`` alone; Asset Booking availability
  needs Asset Booking read.
- A sign-off request uses only the learner's own open assignment and own attempt, for a course
  the learner can open.
- The unlink-and-delete preview and the project merge preview check read on what they name.

Run: python -m unittest erpnext_enhancements.tests.test_access_checks_ops -v
"""

import ast
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

RECORD = APP / "sapphire_maintenance/doctype/sapphire_maintenance_record/sapphire_maintenance_record.py"
CONTRACT = APP / "sapphire_maintenance/doctype/sapphire_maintenance_contract/sapphire_maintenance_contract.py"
VISIT_API = APP / "api/maintenance_visit.py"
TASK = APP / "task_enhancements/doctype/task/task.py"
CHANGE_ORDER = APP / "api/change_order.py"
BUDGET = APP / "api/project_budget.py"
SCORECARD = APP / "api/subcontractor_scorecard.py"
PROJECT_INIT = APP / "project_enhancements/__init__.py"
DASHBOARD = APP / "project_enhancements/page/project_dashboard/project_dashboard.py"
CRM_API = APP / "crm_enhancements/api.py"
ASSET_BOOKING = APP / "asset_management/doctype/asset_booking/asset_booking.py"
SIGNOFF = APP / "training/signoff.py"
DELETE_UTILS = APP / "delete_utils.py"
PROJECT_MERGE = APP / "project_merge.py"
RECORD_JS = APP / "sapphire_maintenance/doctype/sapphire_maintenance_record/sapphire_maintenance_record.js"
MAINTENANCE_PERMISSIONS = APP / "sapphire_maintenance/permissions.py"
OPPORTUNITY_JS = APP / "public/js/crm_enhancements/opportunity.js"


class _AttrDict(dict):
	"""``frappe._dict`` enough for these helpers: keys read as attributes."""

	__getattr__ = dict.get


def _tree(path):
	return ast.parse(path.read_text(encoding="utf-8"))


def _function(path, name):
	for node in ast.walk(_tree(path)):
		if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
			return node
	raise AssertionError(f"{path.name} has no function {name}")


def _code(path, name):
	"""The function's body as ``ast.unparse`` writes it, docstring and comments left out."""
	node = _function(path, name)
	body = node.body
	if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
		body = body[1:]
	return "\n".join(ast.unparse(statement) for statement in body)


def _is_whitelisted(path, name):
	for decorator in _function(path, name).decorator_list:
		target = decorator.func if isinstance(decorator, ast.Call) else decorator
		if ast.unparse(target) == "frappe.whitelist":
			return True
	return False


def _view_all_roles():
	"""``VIEW_ALL_ROLES`` read out of the source (importing the module needs a bench)."""
	for node in _tree(MAINTENANCE_PERMISSIONS).body:
		if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "VIEW_ALL_ROLES":
			return set(ast.literal_eval(node.value))
	raise AssertionError("VIEW_ALL_ROLES not found")


class _Refused(Exception):
	pass


class _FakeFrappe:
	"""Enough of ``frappe`` to run the two access helpers: a user, their roles, and a
	``has_permission`` that records what it was asked and answers from a table."""

	PermissionError = _Refused

	def __init__(self, user="tech@example.com", roles=(), allowed=()):
		self.session = types.SimpleNamespace(user=user)
		self._roles = list(roles)
		self._allowed = set(allowed)
		self.asked = []

	def get_roles(self, user=None):
		return list(self._roles)

	def has_permission(self, doctype, ptype="read", doc=None, throw=False):
		self.asked.append((doctype, ptype, doc, throw))
		ok = (doctype, ptype) in self._allowed or (doctype, ptype, doc) in self._allowed
		if not ok and throw:
			raise _Refused(f"{doctype} {ptype}")
		return ok

	def throw(self, message, exc=None, **kwargs):
		raise (exc or ValueError)(message)


def _run(path, name, fake, **extra):
	"""The function ``name`` from ``path``, compiled alone against ``fake``. An import inside
	its body is dropped and the names it would bind are passed in ``extra``."""
	node = _function(path, name)
	node.body = [statement for statement in node.body if not isinstance(statement, ast.ImportFrom)]
	module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
	namespace = {"frappe": fake, "_": lambda text: text, **extra}
	exec(compile(module, str(path), "exec"), namespace)
	return namespace[name]


class _Order(unittest.TestCase):
	def assertBefore(self, code, first, then):
		self.assertIn(first, code)
		self.assertIn(then, code)
		self.assertLess(code.index(first), code.index(then), f"{first!r} must come before {then!r}")


class TestMaintenanceBriefingAndPayload(_Order):
	def test_the_briefing_checks_before_it_builds(self):
		code = _code(RECORD, "get_dashboard_context")
		self.assertBefore(code, "_check_dashboard_access(project)", "build_dashboard_context(")
		check = _code(RECORD, "_check_dashboard_access")
		self.assertIn("frappe.has_permission('Project', 'read', doc=project, throw=True)", check)
		self.assertIn("VIEW_ALL_ROLES", check)
		self.assertIn("frappe.PermissionError", check)

	def test_the_payload_checks_before_it_builds(self):
		code = _code(RECORD, "get_visit_payload")
		self.assertBefore(code, "_check_visit_payload_access(", "build_visit_payload(")
		check = _code(RECORD, "_check_visit_payload_access")
		self.assertIn("frappe.has_permission('Sapphire Maintenance Record', 'create')", check)
		self.assertIn("frappe.has_permission(doctype, 'read', doc=name, throw=True)", check)

	def test_the_builders_are_not_endpoints(self):
		for name in ("build_dashboard_context", "build_visit_payload"):
			with self.subTest(name=name):
				self.assertFalse(_is_whitelisted(RECORD, name))
		for name in ("get_dashboard_context", "get_visit_payload"):
			with self.subTest(name=name):
				self.assertTrue(_is_whitelisted(RECORD, name))

	def test_the_wizard_bootstrap_uses_the_builders_after_its_record_check(self):
		code = _code(VISIT_API, "get_visit_bootstrap")
		self.assertBefore(code, "_get_record(record)", "build_visit_payload(")
		self.assertBefore(code, "_get_record(record)", "build_dashboard_context(")
		self.assertNotIn("get_dashboard_context(", code)
		self.assertNotIn("get_visit_payload(", code)

	def test_the_desk_form_asks_only_when_it_may(self):
		js = RECORD_JS.read_text(encoding="utf-8")
		self.assertIn("if (!frm.doc.project || !ee_sees_briefing()) return;", js)
		self.assertIn("if (frm.doc.docstatus !== 0 || !ee_fills_visits(frm)) return;", js)
		self.assertIn(
			'return frappe.user.has_role(EE_BRIEFING_ROLES) && frappe.model.can_read("Project");', js
		)

	def test_the_forms_role_list_is_the_servers(self):
		import json
		import re

		js = RECORD_JS.read_text(encoding="utf-8")
		match = re.search(r"const EE_BRIEFING_ROLES = (\[[^\]]*\]);", js)
		self.assertIsNotNone(match, "EE_BRIEFING_ROLES not found")
		self.assertEqual(set(json.loads(match.group(1))), _view_all_roles())

	def test_the_role_is_checked_before_the_project_is_looked_up(self):
		check = _code(RECORD, "_check_dashboard_access")
		self.assertBefore(
			check, "VIEW_ALL_ROLES", "frappe.has_permission('Project', 'read', doc=project, throw=True)"
		)

	def _briefing_check(self, fake):
		return _run(RECORD, "_check_dashboard_access", fake, VIEW_ALL_ROLES=_view_all_roles())

	def test_briefing_access_needs_a_project(self):
		fake = _FakeFrappe(roles=["Maintenance User"])
		with self.assertRaises(ValueError):
			self._briefing_check(fake)("")
		self.assertEqual(fake.asked, [])

	def test_briefing_access_without_the_role_never_reaches_the_project(self):
		fake = _FakeFrappe(roles=["Finance Team"], allowed=[("Project", "read", "PRJ-1")])
		with self.assertRaises(_Refused):
			self._briefing_check(fake)("PRJ-1")
		self.assertEqual(fake.asked, [])

	def test_briefing_access_with_the_role_still_needs_project_read(self):
		fake = _FakeFrappe(roles=["Maintenance User"])
		with self.assertRaises(_Refused):
			self._briefing_check(fake)("PRJ-1")
		self.assertEqual(fake.asked, [("Project", "read", "PRJ-1", True)])
		fake = _FakeFrappe(roles=["Maintenance User"], allowed=[("Project", "read", "PRJ-1")])
		self.assertIsNone(self._briefing_check(fake)("PRJ-1"))

	def test_briefing_access_for_administrator_skips_the_role_and_keeps_the_read(self):
		fake = _FakeFrappe(user="Administrator", roles=[], allowed=[("Project", "read", "PRJ-1")])
		self.assertIsNone(self._briefing_check(fake)("PRJ-1"))
		self.assertEqual(fake.asked, [("Project", "read", "PRJ-1", True)])

	def _payload_check(self, fake):
		return _run(RECORD, "_check_visit_payload_access", fake)

	def test_payload_access_for_someone_who_fills_visits_in(self):
		for right in ("create", "write"):
			with self.subTest(right=right):
				fake = _FakeFrappe(allowed=[("Sapphire Maintenance Record", right)])
				self.assertIsNone(self._payload_check(fake)("PRJ-1", "MNT-CON-1", None))
				self.assertTrue(all(row[0] == "Sapphire Maintenance Record" for row in fake.asked))

	def test_payload_access_with_nothing_named_is_refused(self):
		fake = _FakeFrappe()
		with self.assertRaises(_Refused):
			self._payload_check(fake)(None, None, None)

	def test_payload_access_otherwise_needs_read_on_each_record_named(self):
		fake = _FakeFrappe(
			allowed=[("Sapphire Maintenance Contract", "read", "MNT-CON-1"), ("Project", "read", "PRJ-1")]
		)
		self.assertIsNone(self._payload_check(fake)("PRJ-1", "MNT-CON-1", None))
		self.assertIn(("Sapphire Maintenance Contract", "read", "MNT-CON-1", True), fake.asked)
		self.assertIn(("Project", "read", "PRJ-1", True), fake.asked)
		with self.assertRaises(_Refused):
			self._payload_check(fake)("PRJ-1", "MNT-CON-1", "SC-9")


class TestMaintenanceContractMappers(_Order):
	def test_each_mapper_checks_first(self):
		for name, doctype in (
			("make_contract_from_sales_order", "Sales Order"),
			("make_contract_from_project_contract", "Project Contract"),
			("make_contract_from_project", "Project"),
		):
			with self.subTest(name=name):
				code = _code(CONTRACT, name)
				self.assertTrue(code.startswith(f"_check_can_map('{doctype}', source_name)"), code[:80])

	def test_the_check_reads_source_and_create(self):
		check = _code(CONTRACT, "_check_can_map")
		self.assertIn("frappe.has_permission(source_doctype, 'read', doc=source_name, throw=True)", check)
		self.assertIn("frappe.has_permission('Sapphire Maintenance Contract', 'create', throw=True)", check)

	def test_the_signing_hook_maps_without_the_endpoint_check(self):
		code = _code(CONTRACT, "autocreate_maintenance_contract_on_signed")
		self.assertIn("map_contract_from_project_contract(doc.name)", code)
		self.assertNotIn("make_contract_from_project_contract(", code)
		self.assertFalse(_is_whitelisted(CONTRACT, "map_contract_from_project_contract"))


class TestTaskTree(_Order):
	def test_read_is_checked_outside_the_try(self):
		node = _function(TASK, "get_child_tasks_html")
		statements = [ast.unparse(s) for s in node.body]
		check = next(i for i, s in enumerate(statements) if "task.check_permission('read')" in s)
		first_try = next(i for i, s in enumerate(node.body) if isinstance(s, ast.Try))
		self.assertLess(check, first_try)

	def test_the_walk_stops_at_the_cap(self):
		"""Executed: a fake tree of 3 x 3 x 3 x ... is cut at MAX_TREE_NODES rows."""
		fn = _function(TASK, "_get_all_descendants")
		module = ast.Module(body=[fn], type_ignores=[])
		queries = []

		def get_all(doctype, filters=None, fields=None, limit=None):
			queries.append(limit)
			parent = filters["parent_task"]
			return [{"name": f"{parent}.{i}", "parent_task": parent} for i in range(3)][: limit or None]

		fake = types.SimpleNamespace(get_all=get_all, _dict=_AttrDict)
		namespace = {"frappe": fake, "MAX_TREE_NODES": 10, "MAX_TREE_DEPTH": 50}
		exec(compile(module, str(TASK), "exec"), namespace)
		rows = namespace["_get_all_descendants"]("Task", "T")
		self.assertEqual(len(rows), 10)
		self.assertTrue(all(limit is not None and limit <= 10 for limit in queries))

	def test_the_walk_stops_at_the_depth_limit(self):
		fn = _function(TASK, "_get_all_descendants")
		module = ast.Module(body=[fn], type_ignores=[])

		def get_all(doctype, filters=None, fields=None, limit=None):
			return [{"name": filters["parent_task"] + "x", "parent_task": filters["parent_task"]}]

		namespace = {
			"frappe": types.SimpleNamespace(get_all=get_all, _dict=_AttrDict),
			"MAX_TREE_NODES": 1000,
			"MAX_TREE_DEPTH": 5,
		}
		exec(compile(module, str(TASK), "exec"), namespace)
		self.assertEqual(len(namespace["_get_all_descendants"]("Task", "T")), 5)


class TestChildRecordLists(_Order):
	def test_each_list_needs_its_own_doctype_read(self):
		for path, name, check in (
			(CHANGE_ORDER, "get_project_change_orders", "frappe.has_permission('Change Order', 'read', throw=True)"),
			(BUDGET, "get_project_reallocations", "frappe.has_permission(DOCTYPE, 'read', throw=True)"),
			(SCORECARD, "get_supplier_scorecards", "frappe.has_permission(DOCTYPE, 'read', throw=True)"),
		):
			with self.subTest(name=name):
				self.assertBefore(_code(path, name), check, "frappe.get_all(")

	def test_the_scorecard_history_clamps_limit(self):
		code = _code(SCORECARD, "get_supplier_scorecards")
		self.assertIn("limit = min(max(cint(limit) or 12, 1), MAX_HISTORY_ROWS)", code)
		self.assertIn("limit_page_length=limit", code)


class TestProjectReaders(_Order):
	def test_comments_check_the_project(self):
		for name in ("get_project_comments", "add_project_comment"):
			with self.subTest(name=name):
				code = _code(PROJECT_INIT, name)
				self.assertIn("frappe.has_permission('Project', 'read', doc=project_name, throw=True)", code)
		self.assertBefore(
			_code(PROJECT_INIT, "get_project_comments"),
			"frappe.has_permission('Project', 'read'",
			"frappe.get_all(",
		)
		self.assertBefore(
			_code(PROJECT_INIT, "add_project_comment"),
			"frappe.has_permission('Project', 'read'",
			"frappe.new_doc('Comment')",
		)

	def test_the_dashboard_readers_check_the_project_first(self):
		for name, arg in (
			("get_project_tasks", "project"),
			("get_resource_allocation_data", "project_name"),
			("get_project_health_metrics", "project_name"),
			("get_gantt_tasks_for_project", "project_name"),
		):
			with self.subTest(name=name):
				code = _code(DASHBOARD, name)
				self.assertBefore(code, f"_require_project_read({arg})", "frappe.get_all(")

	def test_task_children_resolve_the_project(self):
		code = _code(DASHBOARD, "get_task_children")
		self.assertBefore(code, "frappe.db.get_value('Task', parent_task, 'project')", "_require_project_read(project)")
		self.assertIn("frappe.has_permission('Task', 'read', doc=parent_task, throw=True)", code)
		self.assertBefore(code, "_require_project_read(project)", "frappe.get_all(")

	def test_the_portfolio_gantt_has_the_page_gate_and_follows_read_permission(self):
		code = _code(DASHBOARD, "get_all_projects_for_gantt")
		self.assertTrue(code.startswith("if not check_permission():"), code[:60])
		self.assertNotIn("frappe.get_all(", code)
		self.assertEqual(code.count("frappe.get_list("), 2)

	def test_master_project_ordering_writes_only_its_own_projects(self):
		code = _code(DASHBOARD, "update_master_project_structure")
		self.assertIn("frappe.db.get_value('Project', p_name, 'custom_master_project') != master_project", code)
		self.assertIn("frappe.has_permission('Project', ptype='write', doc=p_name)", code)
		self.assertBefore(code, "custom_master_project') != master_project", "frappe.db.set_value('Project'")


class TestCrmAndAssets(_Order):
	def test_tag_sync_needs_write_and_keeps_modified(self):
		code = _code(CRM_API, "sync_opportunity_tags_for_existing")
		self.assertBefore(code, "doc.check_permission('write')", "frappe.db.set_value(")
		self.assertIn("update_modified=False", code)
		js = OPPORTUNITY_JS.read_text(encoding="utf-8")
		self.assertIn('frm.has_perm("write")', js)

	def test_availability_needs_asset_booking_read(self):
		code = _code(ASSET_BOOKING, "check_availability")
		self.assertTrue(code.startswith("frappe.has_permission('Asset Booking', 'read', throw=True)"), code[:80])


class TestSignoffRequest(_Order):
	def test_the_request_checks_course_and_attempt(self):
		code = _code(SIGNOFF, "request_signoff")
		self.assertIn("course not in _visible_course_names(caller)", code)
		self.assertIn("_is_learners_attempt(attempt, course, learner)", code)
		self.assertBefore(code, "_visible_course_names(caller)", "frappe.get_doc(")
		self.assertBefore(code, "_is_learners_attempt(", "frappe.get_doc(")

	def test_the_assignment_is_vetted_before_it_is_parked(self):
		code = _code(SIGNOFF, "_mark_assignment_awaiting")
		self.assertTrue(code.startswith("name = _learners_open_assignment(assignment, course, learner) or"), code[:80])

	def _helpers(self, rows):
		tree = _tree(SIGNOFF)
		wanted = {"_learners_open_assignment", "_is_learners_attempt"}
		body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]

		def get_value(doctype, name, fields, as_dict=False):
			row = rows.get((doctype, name))
			return _AttrDict(row) if row else None

		namespace = {
			"frappe": types.SimpleNamespace(db=types.SimpleNamespace(get_value=get_value)),
			"CLOSED_ASSIGNMENT_STATUSES": ("Completed", "Cancelled", "Waived"),
		}
		exec(compile(ast.Module(body=body, type_ignores=[]), str(SIGNOFF), "exec"), namespace)
		return namespace

	def test_only_the_learners_open_assignment_for_the_course_is_used(self):
		ns = self._helpers(
			{
				("Training Assignment", "MINE"): {"user": "a@x", "course": "C1", "status": "In Progress"},
				("Training Assignment", "OTHERS"): {"user": "b@x", "course": "C1", "status": "In Progress"},
				("Training Assignment", "OTHER-COURSE"): {"user": "a@x", "course": "C2", "status": "In Progress"},
				("Training Assignment", "DONE"): {"user": "a@x", "course": "C1", "status": "Completed"},
			}
		)
		pick = ns["_learners_open_assignment"]
		self.assertEqual(pick("MINE", "C1", "a@x"), "MINE")
		for name in ("OTHERS", "OTHER-COURSE", "DONE", "MISSING", None, ["MINE"]):
			with self.subTest(name=name):
				self.assertIsNone(pick(name, "C1", "a@x"))

	def test_only_the_learners_attempt_at_the_course_is_accepted(self):
		ns = self._helpers(
			{
				("Training Attempt", "A1"): {"user": "a@x", "course": "C1"},
				("Training Attempt", "A2"): {"user": "b@x", "course": "C1"},
			}
		)
		check = ns["_is_learners_attempt"]
		self.assertTrue(check("A1", "C1", "a@x"))
		self.assertFalse(check("A1", "C2", "a@x"))
		self.assertFalse(check("A2", "C1", "a@x"))
		self.assertFalse(check("MISSING", "C1", "a@x"))
		self.assertFalse(check({"name": "A1"}, "C1", "a@x"))


class TestDeleteAndMergePreviews(_Order):
	def test_blocking_links_need_read_on_the_target(self):
		code = _code(DELETE_UTILS, "get_blocking_links")
		self.assertBefore(code, "_resolve_doctype(doctype)", "doc.check_permission('read')")
		self.assertBefore(code, "doc.check_permission('read')", "_blocking_links(doctype, name)")
		self.assertFalse(_is_whitelisted(DELETE_UTILS, "_blocking_links"))

	def test_unlink_and_delete_checks_delete_then_uses_the_unchecked_list(self):
		code = _code(DELETE_UTILS, "unlink_and_delete")
		self.assertBefore(code, "frappe.has_permission(doctype, 'delete', name)", "_blocking_links(doctype, name)")

	def test_the_merge_preview_checks_project_read_before_it_looks_a_name_up(self):
		code = _code(PROJECT_MERGE, "get_merge_stats")
		self.assertBefore(code, "frappe.has_permission('Project', 'read', throw=True)", "frappe.db.exists(")

	def test_the_merge_preview_needs_read_on_both_projects(self):
		code = _code(PROJECT_MERGE, "get_merge_stats")
		for project in ("source_project", "target_project"):
			with self.subTest(project=project):
				self.assertBefore(
					code,
					f"frappe.has_permission('Project', 'read', doc={project}, throw=True)",
					"get_impacted_documents(",
				)


if __name__ == "__main__":
	unittest.main()
