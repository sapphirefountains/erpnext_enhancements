"""Bench-free tests for ``setup/workspace_tweaks.hide_core_sidebar_items``.

The module is exec'd with its ``import frappe`` dropped and a fake database passed in as
``frappe``, the way ``test_custom_html_blocks`` runs the seeder, so nothing is put in
``sys.modules``.

frappe 16.50 (production since 2026-10-06) draws the Projects sidebar from the ``Sidebar``
document "Projects" and no longer reads ``Workspace Sidebar``, so the "Project" link the hook
had removed came back. The rows below are production's, read on 2026-10-07.
"""

import ast
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent.parent / "setup" / "workspace_tweaks.py"


def _row(name, parent, idx, link_type, link_to, label, parenttype="Sidebar", hidden=0, type_="Link"):
	return {
		"name": name,
		"parent": parent,
		"parenttype": parenttype,
		"idx": idx,
		"type": type_,
		"link_type": link_type,
		"link_to": link_to,
		"label": label,
		"hidden": hidden,
	}


def _production_rows():
	return [
		_row("k1aelhome0", "Projects", 1, "Workspace", "Projects", "Home"),
		_row("k1aehnenis", "Projects", 2, "Dashboard", "Project", "Dashboard"),
		_row("k1ak5cmb0s", "Projects", 3, "DocType", "Project", "Project"),
		_row("k1aetask00", "Projects", 4, "DocType", "Task", "Task"),
		_row("k1aetimes0", "Projects", 5, "DocType", "Timesheet", "Timesheet"),
		# A site layer naming the same link is the site's own decision, never ours to change.
		_row("cslayer001", "a1b2c3", 1, "DocType", "Project", "Project", parenttype="Custom Sidebar"),
		# Another module's sidebar linking Project.
		_row("k1aeother0", "Project Enhancements (Custom)", 2, "DocType", "Project", "Project"),
	]


class _Item:
	def __init__(self, link_type, link_to, label):
		self.link_type, self.link_to, self.label = link_type, link_to, label


class _WorkspaceSidebar:
	def __init__(self, db, name, items):
		self.db, self.name, self.items = db, name, items
		self.flags = type("Flags", (), {})()

	def save(self):
		self.db.saved.append((self.name, [(i.link_type, i.link_to, i.label) for i in self.items]))


class _Cache:
	def __init__(self):
		self.deleted = []

	def delete_key(self, key):
		self.deleted.append(key)


class _Db:
	def __init__(self, rows, doctypes, workspace_sidebars=None, fail=False):
		self.rows = rows
		self.doctypes = set(doctypes)
		self.workspace_sidebars = workspace_sidebars or {}
		self.fail = fail
		self.writes = []
		self.saved = []

	def exists(self, doctype, name):
		if doctype == "DocType":
			return name in self.doctypes
		if doctype == "Workspace Sidebar":
			return name in self.workspace_sidebars
		raise AssertionError(f"unexpected exists({doctype!r})")

	def has_column(self, doctype, column):
		return doctype == "Sidebar Item" and column == "hidden" and "Sidebar" in self.doctypes

	def set_value(self, doctype, name, field, value, update_modified=True):
		self.writes.append((doctype, name, field, value, update_modified))
		for row in self.rows:
			if row["name"] == name:
				row[field] = value


class _Frappe:
	def __init__(self, db):
		self.db = db
		self.cache = _Cache()
		self.errors = []

	def get_all(self, doctype, filters, pluck):
		assert doctype == "Sidebar Item" and pluck == "name", (doctype, pluck)
		if self.db.fail:
			raise RuntimeError("Unknown column 'hidden'")
		return [r["name"] for r in self.db.rows if all(r.get(k) == v for k, v in filters.items())]

	def get_doc(self, doctype, name):
		assert doctype == "Workspace Sidebar"
		return _WorkspaceSidebar(self.db, name, self.db.workspace_sidebars[name])

	def log_error(self, title=None, message=None):
		self.errors.append((title, message))

	@staticmethod
	def get_traceback():
		return "Traceback (most recent call last):\n  ...\nRuntimeError: Unknown column 'hidden'"


def _run(db):
	tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
	body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
	fake = _Frappe(db)
	ns = {"frappe": fake}
	exec(compile(ast.Module(body=body, type_ignores=[]), str(MODULE_PATH), "exec"), ns)
	ns["hide_core_sidebar_items"]()
	return fake


class TestSidebar1650(unittest.TestCase):
	def test_production_on_2026_10_07(self):
		db = _Db(_production_rows(), {"Sidebar", "Workspace Sidebar"})
		fake = _run(db)
		# Only ERPNext's own "Project" DocType row, flagged in place without a new modified.
		self.assertEqual(db.writes, [("Sidebar Item", "k1ak5cmb0s", "hidden", 1, False)])
		self.assertEqual(fake.cache.deleted, ["bootinfo"])
		self.assertEqual(fake.errors, [])

	def test_nothing_else_is_touched(self):
		db = _Db(_production_rows(), {"Sidebar", "Workspace Sidebar"})
		_run(db)
		hidden = {r["name"] for r in db.rows if r["hidden"]}
		# Not the Dashboard link (also link_to Project), not the site's layer, not another
		# module's sidebar, and no row is removed.
		self.assertEqual(hidden, {"k1ak5cmb0s"})
		self.assertEqual(len(db.rows), len(_production_rows()))

	def test_the_whole_identity_must_match(self):
		# Not on production: a link that shares the label and the type but opens something
		# else is a different item.
		rows = _production_rows() + [
			_row("synthetic1", "Projects", 6, "DocType", "Project Update", "Project"),
			_row("synthetic2", "Projects", 7, "Report", "Project", "Project"),
			_row("synthetic3", "Projects", 8, "DocType", "Project", "All Projects"),
		]
		db = _Db(rows, {"Sidebar", "Workspace Sidebar"})
		_run(db)
		self.assertEqual([w[1] for w in db.writes], ["k1ak5cmb0s"])

	def test_second_migrate_changes_nothing(self):
		db = _Db(_production_rows(), {"Sidebar", "Workspace Sidebar"})
		_run(db)
		db.writes.clear()
		fake = _run(db)
		self.assertEqual(db.writes, [])
		self.assertEqual(fake.cache.deleted, [])

	def test_erpnext_reimport_is_flagged_again(self):
		# An ERPNext release with a newer sidebar file re-imports the row unflagged, before
		# after_migrate runs.
		db = _Db(_production_rows(), {"Sidebar", "Workspace Sidebar"})
		_run(db)
		db.rows = _production_rows()
		db.writes.clear()
		_run(db)
		self.assertEqual(db.writes, [("Sidebar Item", "k1ak5cmb0s", "hidden", 1, False)])

	def test_before_16_50_the_sidebar_step_does_nothing(self):
		db = _Db(_production_rows(), {"Workspace Sidebar"})
		fake = _run(db)
		self.assertEqual(db.writes, [])
		self.assertEqual(fake.errors, [])

	def test_a_failure_is_logged_not_raised(self):
		db = _Db(_production_rows(), {"Sidebar", "Workspace Sidebar"}, fail=True)
		fake = _run(db)
		self.assertEqual(len(fake.errors), 1)
		title, message = fake.errors[0]
		self.assertEqual(title, "Core sidebar item not hidden")
		self.assertIn("\n", message)


class TestWorkspaceSidebarBefore1650(unittest.TestCase):
	def test_old_sidebar_still_drops_the_row(self):
		items = [
			_Item("Workspace", "Projects", "Home"),
			_Item("Dashboard", "Project", "Dashboard"),
			_Item("DocType", "Project", "Project"),
			_Item("DocType", "Task", "Task"),
		]
		db = _Db([], {"Workspace Sidebar"}, workspace_sidebars={"Projects": items})
		_run(db)
		self.assertEqual(
			db.saved,
			[
				(
					"Projects",
					[
						("Workspace", "Projects", "Home"),
						("Dashboard", "Project", "Dashboard"),
						("DocType", "Task", "Task"),
					],
				)
			],
		)

	def test_old_sidebar_already_clean_is_not_saved(self):
		items = [_Item("Workspace", "Projects", "Home"), _Item("DocType", "Task", "Task")]
		db = _Db([], {"Workspace Sidebar"}, workspace_sidebars={"Projects": items})
		_run(db)
		self.assertEqual(db.saved, [])


if __name__ == "__main__":
	unittest.main()
