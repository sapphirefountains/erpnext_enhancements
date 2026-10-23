"""Bench-free tests for ``setup/workspace_tweaks``: ``hide_core_sidebar_items``,
``add_core_sidebar_items``, ``arrange_core_sidebar_items`` and the boot-time
``drop_unreadable_sidebar_links``.

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
		assert doctype == "Sidebar Item" and field in ("idx", "label") and update_modified is False
		# An idx write is recorded as (name, idx), the shape the tests above were written against.
		self.writes.append((name, value) if field == "idx" else (name, field, value))
		for row in self.rows:
			if row["name"] == name:
				row[field] = value


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

	def get_all(self, doctype, filters, fields=None, order_by=None, pluck=None):
		assert doctype == "Sidebar Item", doctype
		# The add and the reorder read whole rows in idx order; the relabel plucks names.
		assert (fields and order_by == "idx asc") or (pluck == "name" and not fields), order_by
		if self.db.fail:
			raise RuntimeError("Table 'tabSidebar Item' doesn't exist")
		rows = [r for r in self.db.rows if all(r.get(k) == v for k, v in filters.items())]
		if pluck:
			return [r[pluck] for r in rows]
		return [{f: r.get(f) for f in fields} for r in sorted(rows, key=lambda r: r["idx"])]

	def get_doc(self, values):
		assert values["doctype"] == "Sidebar Item"
		return _NewRow(self.db, values)

	def log_error(self, title=None, message=None):
		self.errors.append((title, message))

	@staticmethod
	def get_traceback():
		return "Traceback (most recent call last):\n  ...\nRuntimeError: boom"


def _module(fake):
	tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
	body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
	ns = {"frappe": fake}
	exec(compile(ast.Module(body=body, type_ignores=[]), str(MODULE_PATH), "exec"), ns)
	return ns


def _run_add(db):
	fake = _AddFrappe(db)
	_module(fake)["add_core_sidebar_items"]()
	return fake


def _run_migrate(db):
	"""Both entry points, in hooks.py's after_migrate order."""
	fake = _AddFrappe(db)
	ns = _module(fake)
	ns["add_core_sidebar_items"]()
	ns["arrange_core_sidebar_items"]()
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

	def test_crm_gets_the_guided_page_under_opportunity(self):
		"""Production's CRM Sidebar, read on 2026-10-13 (its first rows and a section)."""
		crm = [
			("c1", "Link", "Dashboard", "CRM", "Home"),
			("c2", "Link", "DocType", "Lead", "Lead"),
			("c3", "Link", "DocType", "Opportunity", "Opportunity"),
			("c4", "Link", "DocType", "Customer", "Customer"),
			("c5", "Section Break", "DocType", None, "Reports"),
			("c6", "Link", "Report", "Sales Analytics", "Sales Analytics"),
		]
		rows = [
			{"name": name, "parent": "CRM", "parenttype": "Sidebar", "idx": idx, "type": type_,
			 "link_type": link_type, "link_to": link_to, "label": label, "hidden": 0}
			for idx, (name, type_, link_type, link_to, label) in enumerate(crm, start=1)
		]
		db = _AddDb(rows, sidebars=("CRM",))
		_run_add(db)
		self.assertEqual(
			_order(db),
			[
				# v1.735.0: the Opportunity Kanban goes on top (see TestCrmOpensOnTheKanban).
				"Opportunity Kanban",
				"Home",
				"Lead",
				"Opportunity",
				"Guided Entry",
				"Opportunity Board",
				"Customer",
				"Reports",
				"Sales Analytics",
			],
		)
		self.assertEqual(sorted(r["idx"] for r in db.rows), list(range(1, len(db.rows) + 1)))
		_kanban, guided, board = db.inserted
		self.assertEqual((guided["link_type"], guided["link_to"], guided["child"]), ("Page", "guided-entry", 0))
		# Form kit P6: the new board sits under the guided page, at the same level.
		self.assertEqual((board["link_type"], board["link_to"], board["child"]), ("Page", "opportunity-board", 0))

	def test_icon_is_one_the_desk_can_draw(self):
		names = {line for line in ICON_NAMES.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")}
		db = _AddDb(_sidebar_rows() + _crm_rows(), sidebars=("Projects", "CRM"))
		_run_add(db)
		# The Project Planner, Crew Utilization and the Opportunity Kanban.
		self.assertEqual(len(db.inserted), 3)
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


# ------------------------------------------------- the dock usage audit's sidebars (v1.735.0)
#
# The v1.734.0 audit (90 days of Route History to 2026-10-23) recommended two sidebar changes:
# Purchase Order (916 visits) and Material Request (133) first on Buying, and CRM opening on the
# Opportunity Kanban (836) rather than its dashboard (189). The rows are production's "Buying" and
# "CRM" Sidebars, read on 2026-10-23, in idx order; no Custom Sidebar layer existed for either.
# Row names are placeholders: the read did not select them.

KANBAN = "/desk/opportunity/view/kanban/Opportunity"

_BUYING_2026_10_23 = [
	("Link", "Workspace", "Buying", "Home"),
	("Link", "Dashboard", "Buying", "Dashboard"),
	("Link", "DocType", "Supplier", "Supplier"),
	("Link", "DocType", "Material Request", "Material Request"),
	("Link", "DocType", "Request for Quotation", "Request for Quotation"),
	("Link", "DocType", "Supplier Quotation", "Supplier Quotation"),
	("Link", "DocType", "Purchase Order", "Purchase Order"),
	("Link", "DocType", "Purchase Receipt", "Purchase Receipt"),
	("Link", "DocType", "Purchase Invoice", "Purchase Invoice"),
	("Section Break", "DocType", None, "Reports"),
	("Link", "Report", "Purchase Analytics", "Purchase Analytics"),
	("Link", "Report", "Purchase Order Analysis", "Purchase Order Analysis"),
	("Link", "Report", "Purchase Register", "Purchase Register"),
	("Link", "Report", "Supplier Quotation Comparison", "Supplier Quotation Comparison"),
	("Link", "Report", "Accounts Payable", "Accounts Payable"),
	("Link", "Report", "Accounts Payable Summary", "Accounts Payable Summary"),
	("Section Break", "DocType", None, "Items"),
	("Link", "DocType", "Item", "Item"),
	("Link", "DocType", "Item Group", "Item Group"),
	("Link", "DocType", "Price List", "Price List"),
	("Section Break", "DocType", None, "Setup"),
	("Link", "DocType", "Supplier Group", "Supplier Group"),
	("Link", "DocType", "Supplier Scorecard", "Supplier Scorecard"),
	("Link", "DocType", "Buying Settings", "Settings"),
]

# Through the first two sections; the other 26 rows (Maintenance, Sales Pipeline, Campaign, Setup and
# Settings sections) only shift down with the rest.
_CRM_2026_10_23 = [
	("Link", "Dashboard", "CRM", "Home"),
	("Link", "DocType", "Lead", "Lead"),
	("Link", "DocType", "Opportunity", "Opportunity"),
	("Link", "Page", "guided-entry", "Guided Entry"),
	("Link", "Page", "opportunity-board", "Opportunity Board"),
	("Link", "DocType", "Customer", "Customer"),
	("Section Break", "DocType", None, "Reports"),
	("Link", "Report", "Sales Analytics", "Sales Analytics"),
	("Link", "Report", "Lead Details", "Lead Details"),
	("Section Break", "DocType", None, "Maintenance"),
	("Link", "DocType", "Maintenance Schedule", "Maintenance Schedule"),
]


def _rows(parent, spec, prefix):
	return [
		{
			"name": f"{prefix}{idx:02d}",
			"parent": parent,
			"parenttype": "Sidebar",
			"idx": idx,
			"type": type_,
			"link_type": link_type,
			"link_to": link_to,
			"url": None,
			"label": label,
			"hidden": 0,
		}
		for idx, (type_, link_type, link_to, label) in enumerate(spec, start=1)
	]


def _buying_rows():
	return _rows("Buying", _BUYING_2026_10_23, "buy")


def _crm_rows():
	return _rows("CRM", _CRM_2026_10_23, "crm")


def _labels(db, parent):
	return [r["label"] for r in sorted((r for r in db.rows if r["parent"] == parent), key=lambda r: r["idx"])]


def _landing(db, parent):
	"""Where frappe 16.50's desk opens a module: the first row that leads anywhere
	(``sidebar.module_landing_route`` over ``sidebar_item.get_route``)."""
	for row in sorted((r for r in db.rows if r["parent"] == parent), key=lambda r: r["idx"]):
		if row["type"] != "Link" or row.get("hidden"):
			continue
		return row["url"] if row["link_type"] == "URL" else (row["link_type"], row["link_to"])


def _contiguous(test, db, parent):
	idx = sorted(r["idx"] for r in db.rows if r["parent"] == parent)
	test.assertEqual(idx, list(range(1, len(idx) + 1)))


class TestCrmOpensOnTheKanban(unittest.TestCase):
	def _migrate(self):
		db = _AddDb(_crm_rows(), sidebars=("CRM",))
		return db, _run_migrate(db)

	def test_production_on_2026_10_23(self):
		db, fake = self._migrate()
		self.assertEqual(
			_labels(db, "CRM")[:8],
			[
				"Opportunity Kanban",
				"Dashboard",
				"Lead",
				"Opportunity",
				"Guided Entry",
				"Opportunity Board",
				"Customer",
				"Reports",
			],
		)
		self.assertEqual(_landing(db, "CRM"), KANBAN)
		_contiguous(self, db, "CRM")
		self.assertEqual(fake.errors, [])
		self.assertIn("bootinfo", fake.cache.deleted)

	def test_the_kanban_row(self):
		db, _ = self._migrate()
		(row,) = db.inserted
		self.assertEqual((row["parent"], row["parenttype"], row["parentfield"]), ("CRM", "Sidebar", "items"))
		self.assertEqual(
			(row["type"], row["link_type"], row["link_to"], row["url"]), ("Link", "URL", None, KANBAN)
		)
		self.assertEqual((row["idx"], row["child"], row["indent"], row["open_in_new_tab"]), (1, 0, 0, 0))
		# Opens in the desk: a landing outside /desk/ goes to a new tab (`sidebar.open_landing`).
		self.assertTrue(row["url"].startswith("/desk/"))
		# No key of ours that is not a Sidebar Item column (`read` is for the boot filter only).
		self.assertNotIn("read", row)
		self.assertNotIn("after", row)

	def test_the_url_is_the_one_the_dock_opens(self):
		dock = (MODULE_PATH.parent / "dock_profiles.py").read_text(encoding="utf-8")
		self.assertIn(f'OPPORTUNITIES = ("URL", "{KANBAN}")', dock)

	def test_the_dashboard_row_is_renamed_not_moved_or_lost(self):
		db, _ = self._migrate()
		row = next(r for r in db.rows if (r["link_type"], r["link_to"]) == ("Dashboard", "CRM"))
		self.assertEqual((row["label"], row["idx"]), ("Dashboard", 2))
		self.assertIn((row["name"], "label", "Dashboard"), db.writes)

	def test_second_migrate_changes_nothing(self):
		db, _ = self._migrate()
		db.writes.clear()
		db.inserted.clear()
		fake = _run_migrate(db)
		self.assertEqual((db.writes, db.inserted, fake.cache.deleted, fake.errors), ([], [], [], []))

	def test_erpnext_reimport_is_redone(self):
		db, _ = self._migrate()
		db.rows = _crm_rows()
		db.inserted.clear()
		_run_migrate(db)
		self.assertEqual([r["url"] for r in db.inserted], [KANBAN])
		self.assertEqual(_labels(db, "CRM")[:2], ["Opportunity Kanban", "Dashboard"])

	def test_a_kanban_row_already_there_is_left_alone(self):
		# A Workspace Manager moved it down and hid it in the base; it is still the same link.
		rows = _crm_rows()
		rows.append(
			dict(
				rows[0],
				name="mine01",
				idx=99,
				type="Link",
				link_type="URL",
				link_to=None,
				url=KANBAN,
				label="Pipeline",
				hidden=1,
			)
		)
		db = _AddDb(rows, sidebars=("CRM",))
		_run_migrate(db)
		self.assertEqual(db.inserted, [])
		self.assertEqual(next(r for r in db.rows if r["name"] == "mine01")["hidden"], 1)

	def test_another_url_row_is_not_the_kanban(self):
		# A URL row has no link_to, so identity on (link_type, link_to) would make every URL row the same.
		rows = _crm_rows()
		rows.append(
			dict(
				rows[0],
				name="other1",
				idx=12,
				link_type="URL",
				link_to=None,
				url="https://example.com",
				label="Elsewhere",
			)
		)
		db = _AddDb(rows, sidebars=("CRM",))
		_run_migrate(db)
		self.assertEqual([r["url"] for r in db.inserted], [KANBAN])

	def test_a_label_someone_else_set_is_kept(self):
		rows = _crm_rows()
		rows[0]["label"] = "Sales Dashboard"
		db = _AddDb(rows, sidebars=("CRM",))
		_run_migrate(db)
		self.assertEqual(_labels(db, "CRM")[1], "Sales Dashboard")
		self.assertFalse([w for w in db.writes if len(w) == 3])


class TestBuyingOrder(unittest.TestCase):
	def test_production_on_2026_10_23(self):
		db = _AddDb(_buying_rows(), sidebars=("Buying",))
		fake = _run_migrate(db)
		self.assertEqual(
			_labels(db, "Buying")[:10],
			[
				"Home",
				"Purchase Order",
				"Material Request",
				"Dashboard",
				"Supplier",
				"Request for Quotation",
				"Supplier Quotation",
				"Purchase Receipt",
				"Purchase Invoice",
				"Reports",
			],
		)
		# Buying still opens on its workspace.
		self.assertEqual(_landing(db, "Buying"), ("Workspace", "Buying"))
		# Only the six rows whose place changed, in place, without a new `modified`.
		self.assertEqual(
			sorted(db.writes),
			sorted([("buy07", 2), ("buy04", 3), ("buy02", 4), ("buy03", 5), ("buy05", 6), ("buy06", 7)]),
		)
		self.assertEqual(db.inserted, [])
		self.assertEqual(_labels(db, "Buying")[10:], [label for *_, label in _BUYING_2026_10_23[10:]])
		_contiguous(self, db, "Buying")
		self.assertEqual(fake.cache.deleted, ["bootinfo"])
		self.assertEqual(fake.errors, [])

	def test_second_migrate_changes_nothing(self):
		db = _AddDb(_buying_rows(), sidebars=("Buying",))
		_run_migrate(db)
		db.writes.clear()
		fake = _run_migrate(db)
		self.assertEqual((db.writes, fake.cache.deleted), ([], []))

	def test_erpnext_reimport_is_reordered_again(self):
		db = _AddDb(_buying_rows(), sidebars=("Buying",))
		_run_migrate(db)
		db.rows = _buying_rows()
		_run_migrate(db)
		self.assertEqual(_labels(db, "Buying")[:3], ["Home", "Purchase Order", "Material Request"])

	def test_a_missing_row_is_left_out_not_guessed(self):
		rows = [r for r in _buying_rows() if r["link_to"] != "Material Request"]
		for idx, row in enumerate(rows, start=1):
			row["idx"] = idx
		db = _AddDb(rows, sidebars=("Buying",))
		_run_migrate(db)
		self.assertEqual(_labels(db, "Buying")[:3], ["Home", "Purchase Order", "Dashboard"])
		_contiguous(self, db, "Buying")

	def test_a_missing_anchor_leaves_the_sidebar_alone(self):
		rows = [r for r in _buying_rows() if r["link_type"] != "Workspace"]
		db = _AddDb(rows, sidebars=("Buying",))
		_run_migrate(db)
		self.assertEqual(db.writes, [])

	def test_no_buying_sidebar_nothing_happens(self):
		db = _AddDb(_buying_rows(), sidebars=())
		fake = _run_migrate(db)
		self.assertEqual((db.writes, fake.errors), ([], []))

	def test_before_16_50_nothing_happens(self):
		db = _AddDb(_buying_rows() + _crm_rows(), doctypes=(), sidebars=("Buying", "CRM"))
		fake = _run_migrate(db)
		self.assertEqual((db.writes, db.inserted, fake.errors), ([], [], []))

	def test_a_failure_is_logged_not_raised(self):
		db = _AddDb(_buying_rows(), sidebars=("Buying",), fail=True)
		fake = _AddFrappe(db)
		_module(fake)["arrange_core_sidebar_items"]()
		self.assertEqual(len(fake.errors), 1)
		self.assertEqual(fake.errors[0][0], "Core sidebar items not arranged")
		self.assertIn("\n", fake.errors[0][1])


class TestWiring(unittest.TestCase):
	APP = MODULE_PATH.parent.parent

	def test_after_migrate_arranges_after_adding(self):
		hooks = (self.APP / "hooks.py").read_text(encoding="utf-8")
		block = hooks[hooks.index("\nafter_migrate = [") :]
		block = block[: block.index("\n]")]
		added = block.index('"erpnext_enhancements.setup.workspace_tweaks.add_core_sidebar_items"')
		arranged = block.index('"erpnext_enhancements.setup.workspace_tweaks.arrange_core_sidebar_items"')
		self.assertLess(added, arranged)

	def test_boot_session_filters_the_sidebars(self):
		tree = ast.parse((self.APP / "boot.py").read_text(encoding="utf-8"))
		boot = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "boot_session")
		calls = [
			n
			for n in ast.walk(boot)
			if isinstance(n, ast.Call)
			and isinstance(n.func, ast.Name)
			and n.func.id == "drop_unreadable_sidebar_links"
		]
		self.assertEqual(len(calls), 1)
		self.assertEqual([a.id for a in calls[0].args], ["bootinfo"])


# ------------------------------------------------------------ drop_unreadable_sidebar_links


class _BootFrappe:
	def __init__(self, readable=(), fail=False):
		self.readable, self.fail = set(readable), fail
		self.errors = []

	def has_permission(self, doctype, ptype):
		assert ptype == "read"
		if self.fail:
			raise RuntimeError("boom")
		return doctype in self.readable

	def log_error(self, title=None, message=None):
		self.errors.append((title, message))

	@staticmethod
	def get_traceback():
		return "Traceback (most recent call last):\n  ...\nRuntimeError: boom"


def _boot_item(label, link_type, link_to=None, url=None, type_="Link"):
	return {"label": label, "type": type_, "link_type": link_type, "link_to": link_to, "url": url}


def _boot():
	return {
		"module_sidebars": {
			"CRM": {
				"name": "CRM",
				"items": [
					_boot_item("Opportunity Kanban", "URL", url=KANBAN),
					_boot_item("Dashboard", "Dashboard", "CRM"),
					_boot_item("Contact", "DocType", "Contact"),
					_boot_item("Elsewhere", "URL", url="https://example.com"),
				],
			},
			"Buying": {"name": "Buying", "items": [_boot_item("Home", "Workspace", "Buying")]},
		}
	}


def _filter(bootinfo, fake):
	_module(fake)["drop_unreadable_sidebar_links"](bootinfo)
	return bootinfo


class TestDropUnreadableSidebarLinks(unittest.TestCase):
	def test_without_read_on_opportunity_the_kanban_goes(self):
		boot = _filter(_boot(), _BootFrappe(readable={"Contact"}))
		labels = [i["label"] for i in boot["module_sidebars"]["CRM"]["items"]]
		self.assertEqual(labels, ["Dashboard", "Contact", "Elsewhere"])
		# CRM now opens on its dashboard again, as it did before v1.735.0.
		self.assertEqual(boot["module_sidebars"]["Buying"], _boot()["module_sidebars"]["Buying"])

	def test_with_read_nothing_changes(self):
		boot = _filter(_boot(), _BootFrappe(readable={"Opportunity"}))
		self.assertEqual(boot, _boot())

	def test_a_sidebar_left_with_nothing_to_open_goes(self):
		# frappe never builds a sidebar with no row that leads anywhere; the Kanban alone made this one.
		boot = _boot()
		boot["module_sidebars"]["CRM"]["items"] = [
			_boot_item("Opportunity Kanban", "URL", url=KANBAN),
			_boot_item("Reports", "DocType", type_="Section Break"),
		]
		_filter(boot, _BootFrappe())
		self.assertNotIn("CRM", boot["module_sidebars"])
		self.assertIn("Buying", boot["module_sidebars"])

	def test_no_crm_sidebar_is_fine(self):
		boot = {"module_sidebars": {}}
		fake = _BootFrappe()
		_filter(boot, fake)
		self.assertEqual((boot, fake.errors), ({"module_sidebars": {}}, []))
		fake = _BootFrappe()
		_filter({}, fake)
		self.assertEqual(fake.errors, [])

	def test_a_failure_is_logged_not_raised(self):
		fake = _BootFrappe(fail=True)
		boot = _filter(_boot(), fake)
		self.assertEqual(boot, _boot())
		self.assertEqual(fake.errors[0][0], "Sidebar URL rows not filtered at boot")


if __name__ == "__main__":
	unittest.main()
