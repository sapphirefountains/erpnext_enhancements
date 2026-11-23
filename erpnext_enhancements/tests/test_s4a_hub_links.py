"""Custom pages S4a (v1.681.0, TASK-2026-02806): hubs and dashboards link both ways.

* **Hub -> dashboard** is a URL shortcut on the hub, ``/desk/<dashboard>``. A URL shortcut is
  never permission-filtered, which is safe here only because nobody who could open a hub was
  refused its dashboard on prod on 2026-10-14 (the pairs below were all measured).
* **Dashboard -> hub** cannot be a URL shortcut: a dashboard is gated on ERPNext's broad roles
  and a hub on a team role, so 8 of the 14 people who could open the Design Dashboard could not
  open the Design Hub. It is the ``Dashboard Hub Links`` block, which draws a hub only when
  ``frappe.workspaces`` (the hubs this session may open) has it. Its map is held to the same
  pairs as the hubs' shortcuts here, so the two directions cannot drift apart.
* Operations links Stock Scan, the Product Dashboard has content, the Morning Briefing
  workspace shows the briefing, the kiosk is linked at ``/kiosk`` rather than through the
  ``time-kiosk`` redirect page, the GA4 and Day Board pages admit the roles their hubs gate
  on, and the site-made "Home Dashboard Tasks" block has a source and an endpoint.
* The patch is registered, every file it force-imports exists with a bumped stamp, and it never
  raises, even when every frappe call it makes does.

Bench-free: the JSON and JS are read as text, and the patch and the endpoint run against a
``frappe`` stub installed for this module only.

Run: python -m unittest erpnext_enhancements.tests.test_s4a_hub_links
"""

import ast
import importlib.util
import json
import re
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
PATCH = APP / "patches" / "custom_pages_s4a_hub_links.py"
SEEDER = APP / "setup" / "custom_html_blocks.py"
HUB_LINKS_JS = APP / "custom_html_blocks" / "dashboard_hub_links.js"
TASK_DASHBOARD = APP / "api" / "task_dashboard.py"
BOARD_API = APP / "api" / "maintenance_board.py"
BUMPED_ON_OR_AFTER = "2026-10-14 12:00:00"

#: Workspaces S4a did not edit that a later release bumped, with the patch that re-imports them
#: and the stamp it set: (patch module, modified on or after).
LATER_RESYNCS = {
	"Marketing Hub": ("marketing_insights_hub_links", "2026-10-29 20:00:00"),
}
HUB_LINKS = "Dashboard Hub Links"

#: hub -> the dashboards it must link. The dashboards' block leads back along the same pairs.
HUB_DASHBOARDS = {
	"Sales Hub": {"Sales Dashboard"},
	"Design Hub": {"Design Dashboard", "Product Dashboard"},
	"Marketing Hub": {"Marketing Dashboard"},
	"Operations Hub": {"Operations Dashboard", "Product Dashboard"},
	"Production Hub": {"Production Dashboard", "Service Dashboard"},
	"Support Hub": {"Service Dashboard"},
	"HR Hub": {"HR Dashboard"},
	"Finance Hub": {"Finance Dashboard"},
	"Executive Hub": {"Executive Dashboard"},
}

DASHBOARDS = sorted({d for ds in HUB_DASHBOARDS.values() for d in ds})


def slug(name):
	return name.lower().replace(" ", "-")


def load(path):
	return json.loads(path.read_text(encoding="utf-8"))


def workspace(name):
	for path in APP.glob("*/workspace/*/*.json"):
		doc = load(path)
		if doc.get("name") == name:
			return doc
	raise AssertionError(f"no workspace JSON named {name!r}")


def blocks(doc):
	return json.loads(doc.get("content") or "[]")


def placed(doc, btype, key):
	return [(b.get("data") or {}).get(key) for b in blocks(doc) if b.get("type") == btype]


def literal(path, name):
	for node in ast.parse(path.read_text(encoding="utf-8")).body:
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
			return ast.literal_eval(node.value)
	raise AssertionError(f"{name} not found in {path.name}")


def js_hubs_map():
	"""The HUBS object literal in dashboard_hub_links.js, as {dashboard: [hubs]}."""
	text = HUB_LINKS_JS.read_text(encoding="utf-8")
	body = re.search(r"const HUBS = \{(.*?)\n    \};", text, re.S).group(1)
	return {
		dash: re.findall(r'"([^"]+)"', hubs) for dash, hubs in re.findall(r'"([^"]+)":\s*\[([^\]]*)\]', body)
	}


class TestHubsLinkTheirDashboards(unittest.TestCase):
	def test_each_hub_has_a_url_shortcut_to_each_of_its_dashboards_and_places_it(self):
		for hub, dashboards in HUB_DASHBOARDS.items():
			doc = workspace(hub)
			urls = {s.get("url"): s["label"] for s in doc.get("shortcuts") or [] if s.get("type") == "URL"}
			shown = set(placed(doc, "shortcut", "shortcut_name"))
			for dashboard in dashboards:
				with self.subTest(hub=hub, dashboard=dashboard):
					url = "/desk/" + slug(dashboard)
					self.assertIn(url, urls, f"{hub} has no shortcut to {url}")
					self.assertIn(urls[url], shown, f"{hub}'s {dashboard} shortcut is never placed")

	def test_the_pairs_cover_every_hub_and_every_department_dashboard(self):
		"""Anti-vacuity: a hub or a dashboard missing from the table is checked by nothing."""
		hubs = {load(p)["name"] for p in APP.glob("*/workspace/*/*.json") if load(p)["name"].endswith(" Hub")}
		self.assertEqual(hubs - {"Integration Hub"}, set(HUB_DASHBOARDS))
		self.assertEqual(set(DASHBOARDS), set(literal(SEEDER, "KPI_DEPARTMENT_DASHBOARDS")))


class TestDashboardsLeadBackToTheirHubs(unittest.TestCase):
	def test_the_block_map_is_the_hub_pairs_inverted(self):
		expected = {}
		for hub, dashboards in HUB_DASHBOARDS.items():
			for dashboard in dashboards:
				expected.setdefault(dashboard, set()).add(hub)
		got = {dash: set(hubs) for dash, hubs in js_hubs_map().items()}
		self.assertEqual(got, expected)

	def test_each_dashboard_opens_with_the_block_and_carries_its_row(self):
		for dashboard in DASHBOARDS:
			doc = workspace(dashboard)
			with self.subTest(dashboard=dashboard):
				self.assertEqual(blocks(doc)[0]["type"], "custom_block")
				self.assertEqual(blocks(doc)[0]["data"]["custom_block_name"], HUB_LINKS)
				rows = [r["custom_block_name"] for r in doc.get("custom_blocks") or []]
				self.assertEqual(rows[0], HUB_LINKS, "v16 renders a content block only with its row")
				self.assertGreaterEqual(doc["modified"][:19], BUMPED_ON_OR_AFTER)

	def test_no_dashboard_links_a_hub_with_a_url_shortcut(self):
		"""The dead-tile trap this block exists to avoid."""
		hub_urls = {"/desk/" + slug(h) for h in HUB_DASHBOARDS}
		for dashboard in DASHBOARDS:
			for row in workspace(dashboard).get("shortcuts") or []:
				with self.subTest(dashboard=dashboard, shortcut=row.get("label")):
					self.assertNotIn(row.get("url"), hub_urls)

	def test_the_block_is_seeded_and_restored_on_every_dashboard(self):
		seeded = dict(literal(SEEDER, "BLOCKS"))
		self.assertEqual(seeded.get(HUB_LINKS), "dashboard_hub_links")
		self.assertEqual(literal(SEEDER, "DASHBOARD_HUB_LINKS"), HUB_LINKS)
		self.assertIn("for workspace in KPI_DEPARTMENT_DASHBOARDS:", SEEDER.read_text(encoding="utf-8"))

	def test_the_block_draws_only_hubs_the_viewer_can_open(self):
		code = HUB_LINKS_JS.read_text(encoding="utf-8")
		self.assertIn("const open = window.frappe && frappe.workspaces;", code)
		self.assertIn(".filter(canOpen)", code)
		self.assertIn('root.style.display = hubs.length ? "" : "none"', code)
		# A link inside a shadow root routes in place, or it reloads the whole Desk.
		self.assertIn("frappe.set_route(slug(hub))", code)
		self.assertNotIn("frappe.call", code)


class TestOperationsHub(unittest.TestCase):
	def setUp(self):
		self.doc = workspace("Operations Hub")

	def test_links_stock_scan(self):
		urls = {s.get("url"): s["label"] for s in self.doc["shortcuts"] if s.get("type") == "URL"}
		self.assertIn("/stock-scan", urls)
		self.assertIn(urls["/stock-scan"], placed(self.doc, "shortcut", "shortcut_name"))

	def test_has_an_inventory_card(self):
		self.assertIn("Inventory", placed(self.doc, "card", "card_name"))
		targets = {row.get("link_to") for row in self.doc["links"]}
		for target in ("Stock Entry", "Storage Location", "Inventory Count Session", "Stock Balance"):
			with self.subTest(target=target):
				self.assertIn(target, targets)


class TestProductDashboard(unittest.TestCase):
	def test_it_has_more_than_the_cockpit(self):
		doc = workspace("Product Dashboard")
		self.assertGreaterEqual(len(doc["shortcuts"]), 4)
		self.assertEqual(set(placed(doc, "card", "card_name")), {"Catalog", "Reports"})
		self.assertIn("KPI Cockpit", placed(doc, "custom_block", "custom_block_name"))
		self.assertEqual(doc["hide_custom"], 1)


class TestMorningBriefingWorkspace(unittest.TestCase):
	def setUp(self):
		self.doc = workspace("Morning Briefing")

	def test_the_briefing_is_the_first_thing_on_it(self):
		first = blocks(self.doc)[0]
		self.assertEqual(
			(first["type"], first["data"]["custom_block_name"]), ("custom_block", "Morning Briefing")
		)
		self.assertEqual([r["custom_block_name"] for r in self.doc["custom_blocks"]], ["Morning Briefing"])

	def test_it_is_gated_on_the_roles_the_briefing_serves(self):
		"""v16.50's Workspace.is_permitted checks only module visibility (Block Module) and the
		workspace's roles; the DocPerm module gate is gone. With no roles the page reached all 17
		system users on prod, and 3 of them hold no staff role: the briefing endpoint and /wall
		both refuse them, so the page would show them a no-access panel and a dead link."""
		self.assertEqual(roles_of(self.doc), literal(TASK_DASHBOARD, "STAFF_ROLES"))

	def test_the_seeder_restores_it_there(self):
		placement = literal(SEEDER, "DEPARTMENT_DASHBOARD_BLOCKS")
		self.assertEqual(placement.get("Morning Briefing"), ("Morning Briefing",))

	def test_the_system_manager_lists_are_one_card_and_no_shortcut(self):
		"""Daily Briefing and the Settings are System Manager only. In a card, `get_links` drops
		what a viewer cannot open; a DocType shortcut beside the card only repeated it."""
		self.assertEqual([s["type"] for s in self.doc["shortcuts"]], ["URL"])
		self.assertEqual(placed(self.doc, "card", "card_name"), ["Briefing records"])


class TestTheKioskIsLinkedDirectly(unittest.TestCase):
	def test_no_workspace_links_the_redirect_page(self):
		for path in APP.glob("*/workspace/*/*.json"):
			doc = load(path)
			rows = (doc.get("shortcuts") or []) + (doc.get("links") or [])
			with self.subTest(workspace=doc["name"]):
				self.assertNotIn("time-kiosk", [r.get("link_to") for r in rows])

	def test_production_and_workforce_link_the_kiosk_once(self):
		for name in ("Production Hub", "Workforce"):
			doc = workspace(name)
			with self.subTest(workspace=name):
				urls = [s.get("url") for s in doc["shortcuts"]]
				self.assertEqual(urls.count("/kiosk"), 1)

	def test_a_fresh_site_seeds_the_tile_at_the_kiosk(self):
		defaults = literal(APP / "patches" / "seed_desk_shortcuts.py", "DEFAULTS")
		kiosk = next(d for d in defaults if d["label"] == "Time Kiosk")
		self.assertEqual((kiosk.get("link_type"), kiosk.get("url")), ("URL", "/kiosk"))
		self.assertNotIn("link_to", kiosk)


def page(name):
	for path in APP.glob("*/page/*/*.json"):
		doc = load(path)
		if doc.get("name") == name:
			return doc
	raise AssertionError(f"no page {name}")


def roles_of(doc):
	return {r["role"] for r in doc.get("roles") or []}


class TestPagesAdmitTheRolesTheirHubsGateOn(unittest.TestCase):
	def test_ga4_admits_the_marketing_roles(self):
		# The hub's GA4 Dashboard shortcut opens Marketing Performance since v1.756.0.
		hub = workspace("Marketing Hub")
		self.assertIn("marketing-performance", [s.get("link_to") for s in hub["shortcuts"]])
		roles = roles_of(page("marketing-performance"))
		self.assertIn("Marketing Team", roles)
		self.assertIn("Marketing Manager", roles)

	def test_the_day_board_admits_maintenance_manager_on_the_page_and_in_the_api(self):
		hub = workspace("Production Hub")
		self.assertIn("Maintenance Manager", roles_of(hub))
		self.assertIn("maintenance-day-board", [s.get("link_to") for s in hub["shortcuts"]])
		board = page("maintenance-day-board")
		self.assertIn("Maintenance Manager", roles_of(board))
		self.assertEqual(roles_of(board), literal(BOARD_API, "BOARD_ROLES"), "page and API disagree")


class TestTopCompanyPriorityProjects(unittest.TestCase):
	def test_it_is_a_standard_report_of_this_app(self):
		path = (
			APP
			/ "project_enhancements/report/top_company_priority_projects/top_company_priority_projects.json"
		)
		doc = load(path)
		self.assertEqual(doc["is_standard"], "Yes")
		self.assertEqual(doc["report_type"], "Query Report")
		self.assertEqual(doc["module"], "Project Enhancements")
		self.assertTrue(doc["query"].lstrip().upper().startswith("SELECT"))
		self.assertGreaterEqual(doc["modified"][:19], BUMPED_ON_OR_AFTER)
		executive = workspace("Executive Hub")
		self.assertIn("Top Company Priority Projects", [r.get("link_to") for r in executive["links"]])


# ------------------------------------------------------------------ a frappe stub


class _Row(dict):
	__getattr__ = dict.get


class _Raises:
	"""Anything asked of it raises."""

	def __getattr__(self, name):
		def boom(*args, **kwargs):
			raise RuntimeError(f"stub: {name}")

		return boom


def _stub_frappe(**overrides):
	frappe = types.ModuleType("frappe")
	frappe.logged = []
	frappe.get_app_path = lambda app, *parts: str(Path(APP, *parts))
	frappe.get_traceback = lambda: "traceback"
	frappe.log_error = lambda *args, **kwargs: frappe.logged.append(kwargs.get("title") or args)
	frappe.clear_cache = lambda: None
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe._ = lambda text: text
	frappe.db = _Raises()
	for key, value in overrides.items():
		setattr(frappe, key, value)
	return frappe


_SAVED = {}
_NAMES = (
	"frappe",
	"frappe.modules",
	"frappe.modules.import_file",
	"frappe.utils",
	"frappe.query_builder",
	"frappe.query_builder.functions",
)


def setUpModule():
	for name in _NAMES:
		_SAVED[name] = sys.modules.get(name)


def tearDownModule():
	for name, module in _SAVED.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def _install(frappe, import_file_by_path):
	modules = types.ModuleType("frappe.modules")
	import_file = types.ModuleType("frappe.modules.import_file")
	import_file.import_file_by_path = import_file_by_path
	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda value: int(value or 0)
	utils.date_diff = lambda a, b: 0
	utils.nowdate = lambda: "2026-10-14"
	qb = types.ModuleType("frappe.query_builder")
	functions = types.ModuleType("frappe.query_builder.functions")
	functions.Count = object
	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.modules": modules,
			"frappe.modules.import_file": import_file,
			"frappe.utils": utils,
			"frappe.query_builder": qb,
			"frappe.query_builder.functions": functions,
		}
	)


def _load(path, name):
	spec = importlib.util.spec_from_file_location(name, path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


class TestThePatch(unittest.TestCase):
	def test_registered_after_s1(self):
		entries = [line.strip() for line in (APP / "patches.txt").read_text(encoding="utf-8").splitlines()]
		self.assertIn("erpnext_enhancements.patches.custom_pages_s4a_hub_links", entries)
		self.assertGreater(
			entries.index("erpnext_enhancements.patches.custom_pages_s4a_hub_links"),
			entries.index("erpnext_enhancements.patches.custom_pages_s1_navigation"),
		)

	def test_every_listed_file_exists_and_is_bumped(self):
		listed = [("workspace", f, s) for f, s in literal(PATCH, "WORKSPACES")]
		listed += [("page", f, s) for f, s in literal(PATCH, "PAGES")]
		listed += [("report", f, s) for f, s in literal(PATCH, "REPORTS")]
		self.assertGreaterEqual(len(listed), 19)
		for kind, folder, name in listed:
			path = APP / folder / kind / name / f"{name}.json"
			with self.subTest(file=f"{folder}/{kind}/{name}"):
				self.assertTrue(path.exists(), f"{path} is missing")
				self.assertGreaterEqual(load(path)["modified"][:19], BUMPED_ON_OR_AFTER)

	def test_it_reimports_every_workspace_this_release_edits(self):
		listed = {name for _folder, name in literal(PATCH, "WORKSPACES")}
		for name in [*HUB_DASHBOARDS, *DASHBOARDS, "Morning Briefing", "Workforce"]:
			doc = workspace(name)
			later = LATER_RESYNCS.get(name)
			if later and doc["modified"][:19] >= later[1]:
				# Bumped again by a later release, whose own patch re-imports it.
				with self.subTest(workspace=name, patch=later[0]):
					source = (APP / "patches" / f"{later[0]}.py").read_text(encoding="utf-8")
					self.assertIn(f'"{name.lower().replace(" ", "_")}"', source)
				continue
			if doc["modified"][:19] >= BUMPED_ON_OR_AFTER:
				with self.subTest(workspace=name):
					self.assertIn(name.lower().replace(" ", "_"), listed)

	def test_it_never_raises_when_everything_it_calls_does(self):
		def refuse(*args, **kwargs):
			raise RuntimeError("stub: import refused")

		frappe = _stub_frappe(get_all=refuse, clear_cache=refuse)
		_install(frappe, refuse)
		patch = _load(PATCH, "_s4a_patch_failing")
		patch.execute()  # must not raise
		expected = len(patch.WORKSPACES) + len(patch.PAGES) + len(patch.REPORTS) + 2 + 1
		self.assertEqual(len(frappe.logged), expected)

	def test_a_good_run_imports_with_force_and_moves_the_kiosk_rows(self):
		imported, writes, deletes = [], [], []

		class DB:
			def exists(self, doctype, name=None):
				return True

			def set_value(self, doctype, name, values):
				writes.append((doctype, name, values))

			def delete(self, doctype, filters):
				deletes.append((doctype, filters))

		def get_all(doctype, filters=None, fields=None, pluck=None):
			if doctype == "Enhancement Desk Shortcut":
				return ["Time Kiosk"]
			if pluck:
				return ["sidebar-item-1"]
			return [_Row(name="pwa-dead", url=None), _Row(name="pwa-live", url="/kiosk")]

		frappe = _stub_frappe(get_all=get_all)
		frappe.db = DB()
		_install(frappe, lambda path, force=False, reset_permissions=False: imported.append((path, force)))
		patch = _load(PATCH, "_s4a_patch_good")
		patch.execute()
		self.assertEqual(frappe.logged, [])
		self.assertTrue(imported and all(force for _path, force in imported))
		self.assertEqual(len(imported), len(patch.WORKSPACES) + len(patch.PAGES) + len(patch.REPORTS))
		kiosk = {"link_type": "URL", "url": "/kiosk", "link_to": None}
		self.assertIn(("Enhancement Desk Shortcut", "Time Kiosk", kiosk), writes)
		self.assertIn(("Workspace Sidebar Item", "sidebar-item-1", kiosk), writes)
		self.assertEqual(deletes, [("Workspace Sidebar Item", {"name": "pwa-dead"})])


class TestMyPriorityTasksEndpoint(unittest.TestCase):
	def _run(self, tasks, projects):
		asked = {}

		def get_list(doctype, **kwargs):
			asked["tasks"] = kwargs
			return [_Row(t) for t in tasks]

		def get_all(doctype, **kwargs):
			asked["projects"] = kwargs
			return [_Row(p) for p in projects]

		frappe = _stub_frappe(get_list=get_list, get_all=get_all)
		frappe.session = types.SimpleNamespace(user="tech@example.com")
		_install(frappe, None)
		module = _load(TASK_DASHBOARD, "_s4a_task_dashboard")
		return module, module.get_my_priority_tasks(), asked

	def test_only_the_viewers_open_tasks_are_asked_for(self):
		module, _rows, asked = self._run([], [])
		filters = asked["tasks"]["filters"]
		self.assertIn(["_assign", "like", '%"tech@example.com"%'], filters)
		self.assertIn(["status", "not in", module.CLOSED_TASK_STATUSES], filters)
		self.assertNotIn("projects", asked, "asked for projects with no tasks")

	def test_ranked_projects_first_then_the_project_then_urgency(self):
		tasks = [
			{"name": "T1", "subject": "a", "priority": "Low", "project": "P-unranked"},
			{"name": "T2", "subject": "b", "priority": "Low", "project": "P-rank-2"},
			{"name": "T3", "subject": "c", "priority": "Urgent", "project": "P-rank-2"},
			{"name": "T4", "subject": "d", "priority": "High", "project": "P-rank-1"},
			{"name": "T5", "subject": "e", "priority": "Urgent", "project": None},
		]
		projects = [
			{"name": "P-rank-1", "project_name": "One", "custom_company_priority": "1"},
			{"name": "P-rank-2", "project_name": "Two", "custom_company_priority": "2"},
			{"name": "P-unranked", "project_name": "Later", "custom_company_priority": "Not Assigned"},
		]
		_module, rows, _asked = self._run(tasks, projects)
		self.assertEqual([r["name"] for r in rows], ["T4", "T3", "T2", "T5", "T1"])
		self.assertEqual([r["rank"] for r in rows], [1, 2, 2, 0, 0])
		self.assertEqual(rows[0]["project_name"], "One")

	def test_company_rank_reads_the_select_as_text(self):
		module, _rows, _asked = self._run([], [])
		for value, rank in (
			("1", 1),
			("30", 30),
			("31", 0),
			("0", 0),
			("Maintenance", 0),
			("", 0),
			(None, 0),
		):
			with self.subTest(value=value):
				self.assertEqual(module.company_rank(value), rank)


if __name__ == "__main__":
	unittest.main()
