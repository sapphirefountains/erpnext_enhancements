"""Bench-free tests for the Custom HTML Block seeder's placement logic.

Like test_process_documents, this avoids importing frappe: the module-level
constants and the pure ``_merge_blocks`` / ``_plan_projects_layout`` helpers are
extracted with ``ast`` and exec'd against a tiny ``frappe`` stub, so the seeder's
``import frappe`` (and its ``import os``) never run. This keeps the idempotency
contract — the rule that the cockpit is never placed twice — covered by plain
``pytest``/``unittest``. ``TestPlaceProjectsLayout`` execs the whole module the same
way (imports dropped, a fake database passed in as ``frappe``), so nothing is put in
``sys.modules`` and the suite can keep sharing its CI step.
"""

import ast
import json
import os
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent.parent / "setup" / "custom_html_blocks.py"


class _FrappeStub:
	"""Just enough of ``frappe`` for ``_merge_blocks`` to build a block id."""

	@staticmethod
	def scrub(text):
		return text.lower().replace(" ", "_").replace("-", "_")


def _load():
	"""Exec the module's constants + its pure helpers without importing frappe."""
	tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
	wanted = []
	for node in tree.body:
		if isinstance(node, ast.Assign):
			wanted.append(node)
		elif isinstance(node, ast.FunctionDef) and node.name in ("_merge_blocks", "_plan_projects_layout"):
			wanted.append(node)
	namespace = {"frappe": _FrappeStub}
	exec(compile(ast.Module(body=wanted, type_ignores=[]), str(MODULE_PATH), "exec"), namespace)
	return namespace


class TestCustomHtmlBlockConstants(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.ns = _load()

	def test_kpi_cockpit_is_a_seeded_block(self):
		names = [name for name, _prefix in self.ns["BLOCKS"]]
		self.assertEqual(self.ns["KPI_COCKPIT"], "KPI Cockpit")
		self.assertIn(self.ns["KPI_COCKPIT"], names)

	def test_department_dashboards_cover_the_kpi_departments(self):
		expected = {
			"Finance",
			"Sales",
			"Operations",
			"Design",
			"Production",
			"Service",
			"Marketing",
			"Product",
			"HR",
			"Executive",
		}
		got = {ws.replace(" Dashboard", "") for ws in self.ns["KPI_DEPARTMENT_DASHBOARDS"]}
		self.assertEqual(got, expected)
		for ws in self.ns["KPI_DEPARTMENT_DASHBOARDS"]:
			self.assertTrue(ws.endswith(" Dashboard"), ws)

	def test_kpi_cockpit_not_in_home_blocks(self):
		# KPI Cockpit reaches Home via the dashboard loop, not HOME_BLOCKS.
		self.assertNotIn(self.ns["KPI_COCKPIT"], self.ns["HOME_BLOCKS"])


class TestMergeBlocks(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.merge = staticmethod(_load()["_merge_blocks"])

	def test_appends_when_absent(self):
		blocks, changed = self.merge([], ["KPI Cockpit"])
		self.assertTrue(changed)
		self.assertEqual(len(blocks), 1)
		self.assertEqual(blocks[0]["type"], "custom_block")
		self.assertEqual(blocks[0]["data"]["custom_block_name"], "KPI Cockpit")
		self.assertEqual(blocks[0]["id"], "ee_chb_kpi_cockpit")

	def test_idempotent_when_already_present(self):
		existing = [{"type": "custom_block", "data": {"custom_block_name": "KPI Cockpit", "col": 12}}]
		blocks, changed = self.merge(existing, ["KPI Cockpit"])
		self.assertFalse(changed)
		self.assertEqual(len(blocks), 1)

	def test_preserves_existing_blocks(self):
		existing = [{"type": "header", "data": {"text": "Hi", "col": 12}}]
		blocks, changed = self.merge(existing, ["KPI Cockpit"])
		self.assertTrue(changed)
		self.assertEqual(len(blocks), 2)
		self.assertEqual(blocks[0]["type"], "header")
		self.assertEqual(blocks[1]["data"]["custom_block_name"], "KPI Cockpit")

	def test_handles_non_list_content(self):
		blocks, changed = self.merge(None, ["KPI Cockpit"])
		self.assertTrue(changed)
		self.assertEqual(len(blocks), 1)


# ERPNext v16.50.0's erpnext/projects/workspace/projects/projects.json "content", byte for
# byte. Production's Projects workspace held exactly this string on 2026-10-07, after the
# 16.50.0 upgrade re-imported the file over the layout built on the site.
ERPNEXT_16_50_PROJECTS_CONTENT = (
	'[{"id": "a26c8edf16", "type": "onboarding", "data": {"onboarding_name": "Projects Onboarding",'
	' "col": 12}}, {"id": "7da431fdc9", "type": "chart", "data": {"chart_name": "Project Summary",'
	' "col": 12}}, {"id": "e8d82ebeb8", "type": "number_card", "data": {"number_card_name":'
	' "Open Projects", "col": 4}}, {"id": "69ce81d0bd", "type": "number_card", "data":'
	' {"number_card_name": "Non Completed Tasks", "col": 4}}, {"id": "c7432e7d92", "type":'
	' "number_card", "data": {"number_card_name": "Timesheet Working Hours", "col": 4}},'
	' {"id": "prjcard000", "type": "card", "data": {"card_name": "Project Reports", "col": 4}}]'
)

# Production's Projects workspace content from its last edit before the upgrade
# (Version 4rr8ouhqvr, 2026-06-12): the layout this release restores.
PRE_UPGRADE_LAYOUT = ["Projects Dashboard", "Home Dashboard Tasks", "Morning Briefing", "Task Dashboard"]


def _names(blocks):
	return [b["data"]["custom_block_name"] for b in blocks if b.get("type") == "custom_block"]


class TestProjectsLayout(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.ns = _load()
		cls.plan = staticmethod(cls.ns["_plan_projects_layout"])

	def stock(self):
		return json.loads(ERPNEXT_16_50_PROJECTS_CONTENT)

	def all_available(self):
		return set(self.ns["PROJECTS_LAYOUT"])

	def test_layout_is_the_pre_upgrade_page(self):
		self.assertEqual(list(self.ns["PROJECTS_LAYOUT"]), PRE_UPGRADE_LAYOUT)
		self.assertEqual(self.ns["PROJECTS_WORKSPACE"], "Projects")

	def test_layout_blocks_are_seeded_except_the_site_block(self):
		# "Home Dashboard Tasks" was made on the site and has no source here; every
		# other block the layout names must be one the seeder creates, or the page
		# would depend on a record that a fresh site never gets.
		seeded = {name for name, _prefix in self.ns["BLOCKS"]}
		missing = set(self.ns["PROJECTS_LAYOUT"]) - seeded
		self.assertEqual(missing, {"Home Dashboard Tasks"})

	def test_stock_file_path_is_erpnexts_projects_workspace(self):
		self.assertEqual(
			"/".join(self.ns["PROJECTS_STOCK_FILE"]), "projects/workspace/projects/projects.json"
		)

	def test_production_page_after_the_upgrade_is_restored(self):
		blocks, changed = self.plan(self.stock(), self.stock(), self.all_available())
		self.assertTrue(changed)
		self.assertEqual(_names(blocks), PRE_UPGRADE_LAYOUT)
		# Only our blocks: ERPNext's onboarding, chart and cards are not on the old page.
		self.assertTrue(all(b["type"] == "custom_block" for b in blocks))
		self.assertTrue(all(b["data"]["col"] == 12 for b in blocks))
		ids = [b["id"] for b in blocks]
		self.assertEqual(len(ids), len(set(ids)))

	def test_restore_is_idempotent(self):
		blocks, _ = self.plan(self.stock(), self.stock(), self.all_available())
		again, changed = self.plan(blocks, self.stock(), self.all_available())
		self.assertFalse(changed)
		self.assertEqual(again, blocks)

	def test_site_only_block_is_skipped_where_it_does_not_exist(self):
		available = self.all_available() - {"Home Dashboard Tasks"}
		blocks, changed = self.plan(self.stock(), self.stock(), available)
		self.assertTrue(changed)
		self.assertEqual(_names(blocks), ["Projects Dashboard", "Morning Briefing", "Task Dashboard"])

	def test_nothing_happens_without_the_projects_dashboard(self):
		# Never swap ERPNext's page for one that lacks the block the page is for.
		available = self.all_available() - {"Projects Dashboard"}
		blocks, changed = self.plan(self.stock(), self.stock(), available)
		self.assertFalse(changed)
		self.assertEqual(blocks, self.stock())

	def test_a_layout_someone_chose_is_kept(self):
		# Removing Morning Briefing on the site must survive the next migrate.
		chosen = [
			{
				"id": "x1",
				"type": "custom_block",
				"data": {"custom_block_name": "Projects Dashboard", "col": 12},
			},
			{"id": "x2", "type": "chart", "data": {"chart_name": "Project Summary", "col": 12}},
		]
		blocks, changed = self.plan(list(chosen), self.stock(), self.all_available())
		self.assertFalse(changed)
		self.assertEqual(blocks, chosen)

	def test_projects_dashboard_goes_back_on_top_of_a_custom_layout(self):
		chosen = [{"id": "x2", "type": "chart", "data": {"chart_name": "Project Summary", "col": 12}}]
		blocks, changed = self.plan(list(chosen), self.stock(), self.all_available())
		self.assertTrue(changed)
		self.assertEqual(_names(blocks), ["Projects Dashboard"])
		self.assertEqual(blocks[0]["id"], "ee_chb_projects_dashboard")
		self.assertEqual(blocks[1:], chosen)

	def test_stock_page_with_one_widget_moved_is_a_custom_layout(self):
		# Equality is the whole rule: ERPNext's widgets in another order were arranged
		# by a person, so they stay, and only the Projects Dashboard is added.
		moved = self.stock()
		moved.reverse()
		blocks, changed = self.plan(list(moved), self.stock(), self.all_available())
		self.assertTrue(changed)
		self.assertEqual(_names(blocks), ["Projects Dashboard"])
		self.assertEqual(blocks[1:], moved)

	def test_unreadable_stock_file_only_adds_the_dashboard(self):
		blocks, changed = self.plan(self.stock(), None, self.all_available())
		self.assertTrue(changed)
		self.assertEqual(_names(blocks), ["Projects Dashboard"])
		self.assertEqual(blocks[1:], self.stock())

	def test_empty_page_gets_the_dashboard(self):
		blocks, changed = self.plan(None, self.stock(), self.all_available())
		self.assertTrue(changed)
		self.assertEqual(_names(blocks), ["Projects Dashboard"])


class _Row:
	def __init__(self, **kw):
		self.__dict__.update(kw)


class _FakeDb:
	"""The Workspace, its Workspace Custom Block rows and the Custom HTML Blocks."""

	def __init__(self, content, rows, blocks):
		self.content = content
		self.rows = list(rows)
		self.blocks = set(blocks)
		self.writes = []

	def exists(self, doctype, name):
		if doctype == "Workspace":
			return name == "Projects" and self.content is not None
		if doctype == "Custom HTML Block":
			return name in self.blocks
		raise AssertionError(f"unexpected exists({doctype!r})")

	def get_value(self, doctype, name, field):
		assert (doctype, name, field) == ("Workspace", "Projects", "content"), (doctype, name, field)
		return self.content

	def set_value(self, doctype, name, field, value):
		assert (doctype, name, field) == ("Workspace", "Projects", "content"), (doctype, name, field)
		self.writes.append(value)
		self.content = value


class _FakeFrappe:
	def __init__(self, db, erpnext_root):
		self.db = db
		self.erpnext_root = erpnext_root
		self.errors = []

	@staticmethod
	def scrub(text):
		return text.lower().replace(" ", "_").replace("-", "_")

	def get_app_path(self, app, *parts):
		if app != "erpnext" or self.erpnext_root is None:
			raise ImportError(app)
		return os.path.join(self.erpnext_root, *parts)

	def get_all(self, doctype, filters, fields):
		assert doctype == "Workspace Custom Block"
		return [_Row(custom_block_name=r) for r in self.db.rows if filters["parent"] == "Projects"]

	def get_doc(self, values):
		db = self.db

		class _Doc:
			def insert(self, ignore_permissions=False):
				assert values["parent"] == "Projects" and values["parentfield"] == "custom_blocks"
				db.rows.append(values["custom_block_name"])

		return _Doc()


class TestPlaceProjectsLayout(unittest.TestCase):
	"""``_place_projects_layout`` against a fake database and a real ERPNext file on disk."""

	def setUp(self):
		tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
		body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
		self.code = compile(ast.Module(body=body, type_ignores=[]), str(MODULE_PATH), "exec")
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)
		path = os.path.join(self.tmp.name, "projects", "workspace", "projects")
		os.makedirs(path)
		with open(os.path.join(path, "projects.json"), "w", encoding="utf-8") as f:
			json.dump({"name": "Projects", "content": ERPNEXT_16_50_PROJECTS_CONTENT}, f)

	def run_place(self, db, erpnext_root="default"):
		fake = _FakeFrappe(db, self.tmp.name if erpnext_root == "default" else erpnext_root)
		ns = {"frappe": fake, "json": json, "os": os}
		exec(self.code, ns)
		return ns["_place_projects_layout"]()

	def test_production_on_2026_10_07(self):
		# Prod as found: the stock content, and the one row Nikolas added that day.
		db = _FakeDb(ERPNEXT_16_50_PROJECTS_CONTENT, ["Projects Dashboard"], PRE_UPGRADE_LAYOUT)
		self.assertTrue(self.run_place(db))
		self.assertEqual(len(db.writes), 1)
		self.assertEqual(_names(json.loads(db.content)), PRE_UPGRADE_LAYOUT)
		# A row for every block on the page, none twice: the desk draws a content block
		# only when the workspace also has its row.
		self.assertEqual(sorted(db.rows), sorted(PRE_UPGRADE_LAYOUT))
		# The second migrate changes nothing.
		self.assertFalse(self.run_place(db))
		self.assertEqual(len(db.writes), 1)

	def test_without_erpnext_file_only_the_dashboard_is_added(self):
		db = _FakeDb(ERPNEXT_16_50_PROJECTS_CONTENT, [], PRE_UPGRADE_LAYOUT)
		self.assertTrue(self.run_place(db, erpnext_root=None))
		blocks = json.loads(db.content)
		self.assertEqual(_names(blocks), ["Projects Dashboard"])
		self.assertEqual(blocks[1:], json.loads(ERPNEXT_16_50_PROJECTS_CONTENT))
		self.assertEqual(db.rows, ["Projects Dashboard"])

	def test_no_projects_workspace(self):
		db = _FakeDb(None, [], PRE_UPGRADE_LAYOUT)
		self.assertFalse(self.run_place(db))
		self.assertEqual(db.writes, [])

	def test_unparseable_content_gets_the_dashboard(self):
		db = _FakeDb("{not json", [], PRE_UPGRADE_LAYOUT)
		self.assertTrue(self.run_place(db))
		self.assertEqual(_names(json.loads(db.content)), ["Projects Dashboard"])

	def test_a_failure_cannot_stop_the_migrate(self):
		# The page is ERPNext's and can change shape under us; an exception escaping
		# an after_migrate hook skips every hook registered after this one.
		tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
		sync = next(
			n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "sync_custom_html_blocks"
		)
		guarded = set()
		for node in ast.walk(sync):
			if not isinstance(node, ast.Try):
				continue
			if not any(isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in node.handlers):
				continue
			for stmt in node.body:
				for call in ast.walk(stmt):
					if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
						guarded.add(call.func.id)
		self.assertIn("_place_projects_layout", guarded)


if __name__ == "__main__":
	unittest.main()
