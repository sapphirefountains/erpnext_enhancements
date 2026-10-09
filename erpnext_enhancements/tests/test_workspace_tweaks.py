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


# ---------------------------------------------------------------- add_core_sidebar_items
#
# Nik, 2026-10-09: "I'd also like the Project Planner to exist in the Projects module". The rows
# are production's "Projects" Sidebar, read on 2026-10-09 (Project already hidden by the hook above).

ICON_NAMES = Path(__file__).resolve().parent / "data" / "desk_icon_names.txt"

_PROJECTS_2026_10_09 = [
	("k1ah8md3ug", "Link", "Workspace", "Projects", "Home"),
	("k1aehnenis", "Link", "Dashboard", "Project", "Dashboard"),
	("k1ak5cmb0s", "Link", "DocType", "Project", "Project"),
	("k1a42sno6d", "Link", "DocType", "Task", "Task"),
	("k1af6nd5rj", "Link", "DocType", "Timesheet", "Timesheet"),
	("k1a6bfmp44", "Section Break", "DocType", None, "Reports"),
	("k1a21071ss", "Link", "Report", "Project Summary", "Project Summary"),
	("k1aqbb780h", "Link", "Report", "Timesheet Billing Summary", "Timesheet Billing Summary"),
	("k1avh2reab", "Link", "Report", "Daily Timesheet Summary", "Daily Timesheet Summary"),
	("k1au4sc6mb", "Link", "Report", "Delayed Tasks Summary", "Delayed Tasks Summary"),
	("k1a93sth78", "Link", "Report", "Project wise Stock Tracking", "Project wise Stock Tracking"),
	("k1a7rcm733", "Section Break", "DocType", None, "Setup"),
	("k1a2dv5cin", "Link", "DocType", "Activity Type", "Activity Type"),
	("k1a1mitr8n", "Link", "DocType", "Projects Settings", "Settings"),
]


def _sidebar_rows(drop=()):
	rows = []
	for idx, (name, type_, link_type, link_to, label) in enumerate(_PROJECTS_2026_10_09, start=1):
		if name in drop:
			continue
		rows.append(
			{
				"name": name,
				"parent": "Projects",
				"parenttype": "Sidebar",
				"idx": idx,
				"type": type_,
				"link_type": link_type,
				"link_to": link_to,
				"label": label,
				"hidden": 1 if name == "k1ak5cmb0s" else 0,
			}
		)
	return rows


class _AddDb:
	def __init__(self, rows, doctypes=("Sidebar",), sidebars=("Projects",), fail=False):
		self.rows, self.doctypes, self.sidebars, self.fail = rows, set(doctypes), set(sidebars), fail
		self.writes, self.inserted = [], []

	def exists(self, doctype, name):
		if doctype == "DocType":
			return name in self.doctypes
		if doctype == "Sidebar":
			return name in self.sidebars
		raise AssertionError(f"unexpected exists({doctype!r})")

	def set_value(self, doctype, name, field, value, update_modified=True):
		assert doctype == "Sidebar Item" and field == "idx" and update_modified is False
		self.writes.append((name, value))
		for row in self.rows:
			if row["name"] == name:
				row["idx"] = value


class _NewRow(dict):
	def __init__(self, db, values):
		super().__init__(values)
		self.db = db

	def db_insert(self):
		row = dict(self)
		row["name"] = f"new{len(self.db.inserted)}"
		self.db.inserted.append(row)
		self.db.rows.append(row)


class _AddFrappe:
	def __init__(self, db):
		self.db = db
		self.cache = _Cache()
		self.errors = []

	def get_all(self, doctype, filters, fields, order_by):
		assert doctype == "Sidebar Item" and order_by == "idx asc", (doctype, order_by)
		if self.db.fail:
			raise RuntimeError("Table 'tabSidebar Item' doesn't exist")
		rows = [r for r in self.db.rows if all(r.get(k) == v for k, v in filters.items())]
		return [{f: r.get(f) for f in fields} for r in sorted(rows, key=lambda r: r["idx"])]

	def get_doc(self, values):
		assert values["doctype"] == "Sidebar Item"
		return _NewRow(self.db, values)

	def log_error(self, title=None, message=None):
		self.errors.append((title, message))

	@staticmethod
	def get_traceback():
		return "Traceback (most recent call last):\n  ...\nRuntimeError: boom"


def _run_add(db):
	tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
	body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
	fake = _AddFrappe(db)
	ns = {"frappe": fake}
	exec(compile(ast.Module(body=body, type_ignores=[]), str(MODULE_PATH), "exec"), ns)
	ns["add_core_sidebar_items"]()
	return fake


def _order(db):
	return [r["label"] for r in sorted(db.rows, key=lambda r: r["idx"])]


class TestAddCoreSidebarItems(unittest.TestCase):
	def test_production_on_2026_10_09(self):
		db = _AddDb(_sidebar_rows())
		fake = _run_add(db)
		self.assertEqual(
			_order(db),
			[
				"Home",
				"Dashboard",
				"Project",
				"Task",
				"Project Planner",
				"Timesheet",
				"Reports",
				"Project Summary",
				"Timesheet Billing Summary",
				"Daily Timesheet Summary",
				"Delayed Tasks Summary",
				"Project wise Stock Tracking",
				"Crew Utilization",
				"Setup",
				"Activity Type",
				"Settings",
			],
		)
		# idx stays a contiguous 1..n, so nothing ties with a row the desk sorts beside it.
		self.assertEqual(sorted(r["idx"] for r in db.rows), list(range(1, len(db.rows) + 1)))
		self.assertEqual(fake.cache.deleted, ["bootinfo"])
		self.assertEqual(fake.errors, [])

	def test_the_inserted_rows(self):
		db = _AddDb(_sidebar_rows())
		_run_add(db)
		planner, report = db.inserted
		self.assertEqual(
			(planner["parent"], planner["parenttype"], planner["parentfield"]), ("Projects", "Sidebar", "items")
		)
		self.assertEqual((planner["type"], planner["link_type"], planner["link_to"]), ("Link", "Page", "project-planner"))
		self.assertEqual((planner["child"], planner["open_in_new_tab"]), (0, 0))
		self.assertEqual((report["link_type"], report["link_to"], report["child"]), ("Report", "Crew Utilization", 1))
		self.assertEqual(report["open_in_new_tab"], 0)

	def test_icon_is_one_the_desk_can_draw(self):
		names = {line for line in ICON_NAMES.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")}
		db = _AddDb(_sidebar_rows())
		_run_add(db)
		for row in db.inserted:
			if row["icon"]:
				self.assertIn(row["icon"], names)

	def test_second_migrate_changes_nothing(self):
		db = _AddDb(_sidebar_rows())
		_run_add(db)
		db.writes.clear()
		db.inserted.clear()
		fake = _run_add(db)
		self.assertEqual((db.writes, db.inserted, fake.cache.deleted), ([], [], []))

	def test_a_link_already_there_is_left_alone(self):
		# A Workspace Manager hid it and renamed it; it is still the same link.
		rows = _sidebar_rows()
		rows.append(
			{
				"name": "mine01",
				"parent": "Projects",
				"parenttype": "Sidebar",
				"idx": 99,
				"type": "Link",
				"link_type": "Page",
				"link_to": "project-planner",
				"label": "Crew calendar",
				"hidden": 1,
			}
		)
		db = _AddDb(rows)
		_run_add(db)
		self.assertEqual([r["link_to"] for r in db.inserted], ["Crew Utilization"])
		self.assertEqual(next(r for r in db.rows if r["name"] == "mine01")["hidden"], 1)

	def test_erpnext_reimport_is_added_again(self):
		db = _AddDb(_sidebar_rows())
		_run_add(db)
		db.rows = _sidebar_rows()
		db.inserted.clear()
		_run_add(db)
		self.assertEqual([r["link_to"] for r in db.inserted], ["project-planner", "Crew Utilization"])

	def test_a_missing_anchor_is_skipped_not_guessed(self):
		db = _AddDb(_sidebar_rows(drop={"k1a93sth78"}))
		_run_add(db)
		self.assertEqual([r["link_to"] for r in db.inserted], ["project-planner"])

	def test_before_16_50_nothing_happens(self):
		db = _AddDb(_sidebar_rows(), doctypes=())
		fake = _run_add(db)
		self.assertEqual((db.writes, db.inserted, fake.errors), ([], [], []))

	def test_no_projects_sidebar_nothing_happens(self):
		db = _AddDb(_sidebar_rows(), sidebars=())
		fake = _run_add(db)
		self.assertEqual((db.writes, db.inserted, fake.errors), ([], [], []))

	def test_a_failure_is_logged_not_raised(self):
		db = _AddDb(_sidebar_rows(), fail=True)
		fake = _run_add(db)
		self.assertEqual(len(fake.errors), 1)
		self.assertEqual(fake.errors[0][0], "Core sidebar item not added")
		self.assertIn("\n", fake.errors[0][1])


if __name__ == "__main__":
	unittest.main()
