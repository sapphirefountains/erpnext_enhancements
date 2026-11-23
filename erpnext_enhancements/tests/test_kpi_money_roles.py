"""Revenue and margin figures on the KPI dashboards go to Accounts Manager and System Manager only.

Owner decision, 2026-11-19: a figure that states the company's revenue or margin is returned
only to a caller holding Accounts Manager or System Manager. Every other role that may open a
department's dashboard keeps everything else that department shows, money figures that are not
revenue or margin included (receivables, cash collected, spend, stock value, pipeline, won
value, backlog, cost per lead). The department gate (``api.kpi.DEPARTMENT_ROLES``) is not part
of it. ``kpi_dashboards/money.py`` holds the registry and the one role helper.

Bench-free. The registry is checked against the aggregators' source (a new money-looking KPI
has to be classified, and one named for revenue or margin has to be restricted), and the rule
is RUN: through the cockpit endpoint, a snapshot document read, the KPI Snapshot list
condition (executed in SQLite against real rows, in the parent-query and the child-query
shape), the child-row and KPI Target conditions, the KPI Target document check, the executive
widgets, the assistant tool and the Task Dashboard wall band, each over a stand-in ``frappe``.

Run: python -m unittest erpnext_enhancements.tests.test_kpi_money_roles
"""

import ast
import datetime
import re
import sqlite3
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
SNAPSHOTS = APP / "kpi_dashboards" / "snapshots.py"
RENTAL_KPIS = APP / "asset_management" / "rental_kpis.py"
API = APP / "api"

REVENUE_ROLE_SETS = (["Accounts Manager"], ["System Manager"], ["Accounts Manager", "Sales Manager"])
OTHER_ROLE_SETS = (
	["Projects Manager"],
	["Accounts User"],
	["Sales Manager", "Sales User"],
	["Marketing Manager"],
	["Stock Manager", "Item Manager"],
	["Maintenance Manager", "Projects User"],
	["HR Manager", "HR Team"],
	[],
)

#: The owner's list: revenue and margin, and cash collected, which he counts with revenue.
EXPECTED_RESTRICTED = {
	"revenue_30",
	"revenue_mtd",
	"cash_collected_30",
	"catalog_revenue_30",
	"catalog_revenue_365",
	"rental_booked_revenue_90",
	"rental_revenue_per_fountain_90",
	"revenue_per_employee",
	"gross_margin_pct",
}

#: A key or label that reads like money has to be classified in kpi_dashboards/money.py.
MONEY_WORDS = re.compile(
	r"revenue|margin|profit|cash|value|cost|spend|receivable|payable|backlog|pipeline"
	r"|deal size|price|budget|income|\bdso\b",
	re.IGNORECASE,
)

#: A key or label that names revenue or margin is restricted, with no reviewed exception.
REVENUE_WORDS = re.compile(r"revenue|margin|profit|income", re.IGNORECASE)

STATE = {"roles": [], "docs": {}, "rows": {}, "snapshot_names": {}, "freshness": None}
_SAVED = {}
modules = {}


def _install():
	frappe = types.ModuleType("frappe")

	class _Dict(dict):
		def __getattr__(self, key):
			try:
				return self[key]
			except KeyError:
				raise AttributeError(key)

		def __setattr__(self, key, value):
			self[key] = value

	class _PermissionError(Exception):
		pass

	def throw(message, exc=None):
		raise (exc or Exception)(message)

	frappe._ = lambda s: s
	frappe._dict = _Dict
	frappe.PermissionError = _PermissionError
	frappe.throw = throw
	frappe.get_roles = lambda user=None: list(STATE["roles"])
	frappe.session = types.SimpleNamespace(user="someone@example.com")
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda: ""
	frappe.parse_json = lambda raw: raw
	frappe.cache = lambda: None

	def get_all(doctype, filters=None, fields=None, order_by=None, limit=None, pluck=None, **kwargs):
		if doctype == "KPI Snapshot":
			names = STATE["snapshot_names"].get((filters or {}).get("department"))
			return [names] if names else []
		if doctype == "KPI Snapshot Value":
			return [_Dict(r) for r in STATE["rows"].get((filters or {}).get("parent"), [])]
		return []

	frappe.get_all = get_all
	frappe.get_doc = lambda doctype, name=None: STATE["docs"][name]
	frappe.get_cached_doc = lambda doctype: _Dict(kpi_dashboards_enabled=1, wall_show_kpis=1)

	db = types.SimpleNamespace(
		exists=lambda *a, **k: True,
		escape=lambda value: "'" + str(value).replace("'", "''") + "'",
		get_value=lambda *a, **k: STATE["freshness"],
	)
	frappe.db = db

	utils = types.ModuleType("frappe.utils")
	utils.escape_html = lambda s: str(s)
	utils.cint = lambda v: int(v or 0)
	utils.flt = lambda v, precision=None: float(v or 0)
	utils.add_days = lambda d, n: d
	utils.add_months = lambda d, n: d
	utils.getdate = lambda v=None: v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v))
	utils.nowdate = lambda: "2026-11-19"
	utils.now_datetime = lambda: None
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class Document:
		def apply_fieldlevel_read_permissions(self):
			return None

	document.Document = Document
	model.document = document
	frappe.model = model

	for name, module in (
		("frappe", frappe),
		("frappe.utils", utils),
		("frappe.model", model),
		("frappe.model.document", document),
	):
		sys.modules[name] = module

	attribution = types.ModuleType("erpnext_enhancements.crm_enhancements.attribution")
	attribution.UNKNOWN_LEAD_SOURCE = "Unknown"
	sys.modules["erpnext_enhancements.crm_enhancements.attribution"] = attribution

	# The assistant tool's base class and gate live in frappe_assistant_core / a module that
	# pulls frappe; the tool's own logic is what is under test.
	base_tool = types.ModuleType("frappe_assistant_core.core.base_tool")

	class BaseTool:
		def __init__(self):
			pass

	base_tool.BaseTool = BaseTool
	for name in ("frappe_assistant_core", "frappe_assistant_core.core"):
		sys.modules[name] = types.ModuleType(name)
	sys.modules["frappe_assistant_core.core.base_tool"] = base_tool
	gate = types.ModuleType("erpnext_enhancements.assistant_tools._gate")
	gate.annotations_for = lambda name: {}
	gate.apply_gate = lambda: None
	sys.modules["erpnext_enhancements.assistant_tools._gate"] = gate

	# api.dashboard_widgets pulls the whole department gate; the executive widgets only need
	# its shape: a role gate, a fetch and the decorator.
	widgets = types.ModuleType("erpnext_enhancements.api.dashboard_widgets")
	widgets.can_view = lambda department: True
	widgets.fetch_all = lambda doctype, **kw: get_all(doctype, **kw)
	widgets.widget_feed = lambda department, widget: (lambda fn: fn)
	sys.modules["erpnext_enhancements.api.dashboard_widgets"] = widgets


def setUpModule():
	for name in list(sys.modules):
		if name.startswith(("erpnext_enhancements", "frappe")):
			_SAVED[name] = sys.modules.pop(name)
	_install()
	from erpnext_enhancements.api import executive_dashboard, kpi
	from erpnext_enhancements.kpi_dashboards import money, permissions, snapshots

	modules.update(
		kpi=kpi, money=money, permissions=permissions, snapshots=snapshots, executive=executive_dashboard
	)


def tearDownModule():
	for name in list(sys.modules):
		if name.startswith(("erpnext_enhancements", "frappe")):
			sys.modules.pop(name, None)
	sys.modules.update(_SAVED)
	_SAVED.clear()
	modules.clear()


# ----------------------------------------------------------------- reading the source


def _literal(node):
	try:
		return ast.literal_eval(node)
	except (ValueError, SyntaxError):
		return None


def _collect_figures():
	"""(function, key, label, unit) for every figure an aggregator emits, from the source.

	``add(key, label, value, unit, ...)`` calls in snapshots.py, the Executive rollup rows,
	and the rental fleet's tuples. A figure whose key is not a literal is skipped (the rental
	loop and the rollup loop pass variables; their literals are read at the source).
	"""
	figures = []
	tree = ast.parse(SNAPSHOTS.read_text(encoding="utf-8"))
	for fn in tree.body:
		if isinstance(fn, ast.FunctionDef) and fn.name.endswith("_metrics"):
			for node in ast.walk(fn):
				if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "add" and len(node.args) >= 4:
					key, label, unit = (_literal(node.args[i]) for i in (0, 1, 3))
					if isinstance(key, str) and isinstance(label, str):
						figures.append((fn.name, key, label, unit))
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(
			isinstance(t, ast.Name) and t.id == "_EXEC_ROLLUP" for t in node.targets
		):
			for row in node.value.elts:
				if all(isinstance(e, ast.Constant) for e in (row.elts[0], row.elts[1], row.elts[4])):
					figures.append(("_executive_metrics", row.elts[0].value, row.elts[1].value, row.elts[4].value))
	rental = ast.parse(RENTAL_KPIS.read_text(encoding="utf-8"))
	for node in ast.walk(rental):
		if isinstance(node, ast.Tuple) and len(node.elts) == 6:
			key, label, unit = (_literal(node.elts[i]) for i in (0, 1, 3))
			if isinstance(key, str) and isinstance(label, str):
				figures.append(("_product_metrics", key, label, unit))
	return figures


def _aggregator_functions():
	"""department -> aggregator function name, from the AGGREGATORS literal."""
	tree = ast.parse(SNAPSHOTS.read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(
			isinstance(t, ast.Name) and t.id == "AGGREGATORS" for t in node.targets
		):
			return {k.value: v.id for k, v in zip(node.value.keys, node.value.values)}
	raise AssertionError("AGGREGATORS not found")


FIGURES = None


def figures():
	global FIGURES
	if FIGURES is None:
		FIGURES = _collect_figures()
	return FIGURES


def keys_of_department(department):
	function = _aggregator_functions()[department]
	return {key for fn, key, _label, _unit in figures() if fn == function}


def restricted():
	return set(modules["money"].RESTRICTED_KPIS)


# ------------------------------------------------------------------- the registry


class TestTheRegistryCoversEveryAggregator(unittest.TestCase):
	def test_the_scan_finds_the_figures(self):
		self.assertGreater(len(figures()), 100)
		self.assertIn(("_finance_metrics", "revenue_30", "Revenue (30d)", "USD"), figures())
		self.assertTrue(any(key == "rental_booked_revenue_90" for _fn, key, _l, _u in figures()))
		self.assertTrue(any(fn == "_executive_metrics" and key == "backlog_value" for fn, key, _l, _u in figures()))

	def test_the_restricted_list_is_revenue_and_margin(self):
		self.assertEqual(restricted(), EXPECTED_RESTRICTED)

	def test_every_dollar_figure_is_classified(self):
		money = modules["money"]
		unclassified = sorted(
			{
				key
				for _fn, key, _l, unit in figures()
				if unit == "USD" and key not in money.RESTRICTED_KPIS and key not in money.REVIEWED_UNRESTRICTED
			}
		)
		self.assertEqual(unclassified, [], "a USD figure is in neither RESTRICTED_KPIS nor REVIEWED_UNRESTRICTED")

	def test_every_money_looking_key_or_label_is_classified(self):
		money = modules["money"]
		unclassified = sorted(
			{
				key
				for _fn, key, label, _unit in figures()
				if (MONEY_WORDS.search(key) or MONEY_WORDS.search(label))
				and key not in money.RESTRICTED_KPIS
				and key not in money.REVIEWED_UNRESTRICTED
			}
		)
		self.assertEqual(
			unclassified,
			[],
			"is it a revenue or margin figure? Restrict it in RESTRICTED_KPIS, or list it in "
			"REVIEWED_UNRESTRICTED with the reason it is not",
		)

	def test_every_key_or_label_naming_revenue_or_margin_is_restricted(self):
		named = {
			key for _fn, key, label, _u in figures() if REVENUE_WORDS.search(key) or REVENUE_WORDS.search(label)
		}
		self.assertTrue(named)
		self.assertEqual(sorted(named - restricted()), [])
		self.assertEqual(set(modules["money"].REVIEWED_UNRESTRICTED) & named, set())

	def test_margin_in_percent_is_restricted(self):
		self.assertIn("gross_margin_pct", modules["money"].RESTRICTED_KPIS)

	def test_the_figures_department_roles_keep_are_reviewed_not_restricted(self):
		money = modules["money"]
		for key in (
			"ar_outstanding",
			"ap_outstanding",
			"store_run_spend_30",
			"inventory_stock_value",
			"open_pipeline_value",
			"won_value_30",
			"avg_deal_size",
			"backlog_value",
			"build_backlog_value",
			"change_order_net_value",
			"rental_backlog_value",
			"marketing_pipeline",
			"marketing_spend_mtd",
			"cpl_mtd",
		):
			self.assertIn(key, money.REVIEWED_UNRESTRICTED, key)
			self.assertNotIn(key, money.RESTRICTED_KPIS, key)

	def test_the_two_tables_do_not_overlap(self):
		money = modules["money"]
		self.assertEqual(set(money.RESTRICTED_KPIS) & set(money.REVIEWED_UNRESTRICTED), set())

	def test_every_registry_entry_is_a_real_figure(self):
		"""A key an aggregator no longer emits is a stale entry, and hides a name for nothing."""
		money = modules["money"]
		emitted = {key for _fn, key, _l, _u in figures()}
		self.assertEqual(sorted(set(money.RESTRICTED_KPIS) - emitted), [])
		self.assertEqual(sorted(set(money.REVIEWED_UNRESTRICTED) - emitted), [])

	def test_registry_labels_match_the_aggregators(self):
		money = modules["money"]
		labels = {}
		for _fn, key, label, _u in figures():
			labels.setdefault(key, set()).add(label)
		for key, label in money.RESTRICTED_KPIS.items():
			self.assertIn(label, labels[key], key)

	def test_every_reviewed_exception_gives_a_reason(self):
		for key, reason in modules["money"].REVIEWED_UNRESTRICTED.items():
			self.assertTrue(reason.strip(), key)


# --------------------------------------------------------------- the filter, run


def rows_for(department):
	return [{"kpi_key": key, "label": key} for key in sorted(keys_of_department(department))]


class TestTheFilter(unittest.TestCase):
	def test_revenue_roles_see_everything(self):
		money = modules["money"]
		for department in _aggregator_functions():
			rows = rows_for(department)
			for roles in REVENUE_ROLE_SETS:
				self.assertEqual(money.without_revenue(rows, roles=roles), rows, (department, roles))

	def test_every_other_role_loses_exactly_the_restricted_figures(self):
		money = modules["money"]
		for department in _aggregator_functions():
			emitted = keys_of_department(department)
			for roles in OTHER_ROLE_SETS:
				kept = {r["kpi_key"] for r in money.without_revenue(rows_for(department), roles=roles)}
				self.assertEqual(kept, emitted - restricted(), (department, roles))

	def test_department_roles_keep_their_money_figures(self):
		money = modules["money"]
		cases = (
			("Production", ["Projects Manager"], {"backlog_value", "build_backlog_value", "change_order_net_value"}),
			("Sales", ["Sales Manager"], {"open_pipeline_value", "won_value_30", "avg_deal_size"}),
			("Marketing", ["Marketing Manager"], {"marketing_pipeline", "marketing_spend_mtd", "cpl_mtd"}),
			("Finance", ["Accounts User"], {"ar_outstanding", "ap_outstanding", "dso"}),
			("Operations", ["Stock Manager"], {"store_run_spend_30", "inventory_stock_value"}),
		)
		for department, roles, expected in cases:
			kept = {r["kpi_key"] for r in money.without_revenue(rows_for(department), roles=roles)}
			self.assertLessEqual(expected, kept, department)

	def test_product_loses_its_revenue_and_margin_figures(self):
		money = modules["money"]
		kept = {r["kpi_key"] for r in money.without_revenue(rows_for("Product"), roles=["Item Manager"])}
		for key in (
			"catalog_revenue_30",
			"catalog_revenue_365",
			"rental_booked_revenue_90",
			"rental_revenue_per_fountain_90",
			"gross_margin_pct",
		):
			self.assertNotIn(key, kept)
		for key in ("rental_backlog_value", "active_sku_count", "fleet_utilization_30"):
			self.assertIn(key, kept)

	def test_the_filter_works_on_documents_and_does_not_change_its_input(self):
		money = modules["money"]
		rows = [types.SimpleNamespace(kpi_key="revenue_30"), types.SimpleNamespace(kpi_key="ar_outstanding")]
		out = money.without_revenue(rows, roles=["Accounts User"])
		self.assertEqual([r.kpi_key for r in out], ["ar_outstanding"])
		self.assertEqual(len(rows), 2)

	def test_the_role_rule_reads_the_session_when_given_no_roles(self):
		money = modules["money"]
		STATE["roles"] = ["Projects Manager"]
		self.assertFalse(money.can_see_revenue())
		STATE["roles"] = ["Projects Manager", "Accounts Manager"]
		self.assertTrue(money.can_see_revenue())


# ------------------------------------------------------------ the cockpit endpoint


def snapshot_doc(department, keys):
	values = [
		types.SimpleNamespace(
			kpi_key=key,
			label=key,
			value=1.0,
			value_text="1",
			unit="",
			target_value=None,
			status="Good",
			direction="Higher is better",
			trend_pct=0.0,
			source="",
			is_stale=0,
		)
		for key in sorted(keys)
	]
	return types.SimpleNamespace(
		department=department,
		period="Daily",
		snapshot_date="2026-11-19",
		generated_at=None,
		generated_by="Scheduler",
		values=values,
	)


def install_snapshot(department):
	name = f"KPI-{department}-Daily-2026-11-19"
	STATE["snapshot_names"][department] = name
	STATE["docs"][name] = snapshot_doc(department, keys_of_department(department))
	return name


class TestTheCockpitEndpoint(unittest.TestCase):
	def setUp(self):
		STATE.update(roles=[], docs={}, rows={}, snapshot_names={}, freshness=None)
		self.kpi = modules["kpi"]
		self.kpi.snapshots.kpi_enabled = lambda settings=None: True

	def _keys(self, department):
		result = self.kpi.get_kpi_dashboard(department)
		return {v["kpi_key"] for v in result["snapshot"]["values"]}, result["snapshot"]

	def test_an_accounts_manager_sees_the_finance_figures_whole(self):
		install_snapshot("Finance")
		STATE["roles"] = ["Accounts Manager"]
		keys, snap = self._keys("Finance")
		self.assertEqual(keys, keys_of_department("Finance"))
		self.assertFalse(snap["figures_left_out"])

	def test_a_system_manager_sees_every_department_whole(self):
		STATE["roles"] = ["System Manager"]
		for department in _aggregator_functions():
			install_snapshot(department)
			keys, _snap = self._keys(department)
			self.assertEqual(keys, keys_of_department(department), department)

	def test_an_accounts_user_opens_finance_without_revenue_and_keeps_receivables(self):
		install_snapshot("Finance")
		STATE["roles"] = ["Accounts User"]
		keys, snap = self._keys("Finance")
		self.assertEqual(keys, keys_of_department("Finance") - restricted())
		self.assertNotIn("revenue_30", keys)
		self.assertNotIn("revenue_mtd", keys)
		self.assertNotIn("cash_collected_30", keys)
		for kept in ("ar_outstanding", "ap_outstanding", "dso"):
			self.assertIn(kept, keys)
		self.assertTrue(snap["figures_left_out"])

	def test_a_projects_manager_sees_production_whole(self):
		install_snapshot("Production")
		STATE["roles"] = ["Projects Manager"]
		keys, snap = self._keys("Production")
		self.assertEqual(keys, keys_of_department("Production"))
		self.assertIn("backlog_value", keys)
		self.assertFalse(snap["figures_left_out"])

	def test_an_item_manager_opens_product_without_revenue_or_margin(self):
		install_snapshot("Product")
		STATE["roles"] = ["Item Manager"]
		keys, snap = self._keys("Product")
		self.assertEqual(keys, keys_of_department("Product") - restricted())
		self.assertTrue(snap["figures_left_out"])
		blob = repr(snap)
		for key in restricted():
			self.assertNotIn(key, blob)

	def test_the_department_gate_is_unchanged(self):
		install_snapshot("Finance")
		STATE["roles"] = ["Projects Manager"]
		with self.assertRaises(sys.modules["frappe"].PermissionError):
			self.kpi.get_kpi_dashboard("Finance")
		self.assertEqual(
			self.kpi.visible_departments(),
			[d for d in self.kpi.AVAILABLE_DEPARTMENTS if d in ("Operations", "Design", "Production", "Service")],
		)

	def test_a_snapshot_that_is_all_restricted_reports_nothing_left_not_a_zero(self):
		name = "KPI-Executive-Daily-2026-11-19"
		STATE["snapshot_names"]["Executive"] = name
		STATE["docs"][name] = snapshot_doc("Executive", {"revenue_30", "revenue_per_employee"})
		STATE["roles"] = ["Sales Manager"]
		result = self.kpi.get_kpi_dashboard("Executive")
		self.assertEqual(result["snapshot"]["values"], [])
		self.assertTrue(result["snapshot"]["figures_left_out"])

	def test_the_refresh_endpoint_answers_through_the_same_filter(self):
		doc = snapshot_doc("Product", keys_of_department("Product"))
		self.kpi.snapshots.build_department_snapshot = lambda department, period="Daily", generated_by="": doc
		self.kpi.frappe.db.commit = lambda: None
		STATE["roles"] = ["Stock Manager"]
		result = self.kpi.refresh_kpi_dashboard("Product")
		keys = {v["kpi_key"] for v in result["snapshot"]["values"]}
		self.assertEqual(keys & restricted(), set())
		self.assertIn("rental_backlog_value", keys)


# -------------------------------------------------- snapshot documents and list rules


def sqlite_db(snapshots):
	"""An in-memory database with the two tables, holding ``{name: (department, keys)}``."""
	conn = sqlite3.connect(":memory:")
	conn.execute("CREATE TABLE `tabKPI Snapshot` (name TEXT, department TEXT)")
	conn.execute(
		"CREATE TABLE `tabKPI Snapshot Value` (name TEXT, parent TEXT, parenttype TEXT, kpi_key TEXT)"
	)
	conn.execute("CREATE TABLE `tabKPI Target` (name TEXT, kpi_key TEXT)")
	for name, (department, keys) in snapshots.items():
		conn.execute("INSERT INTO `tabKPI Snapshot` VALUES (?, ?)", (name, department))
		for i, key in enumerate(sorted(keys)):
			conn.execute(
				"INSERT INTO `tabKPI Snapshot Value` VALUES (?, ?, 'KPI Snapshot', ?)", (f"{name}-{i}", name, key)
			)
	return conn


class TestSnapshotReads(unittest.TestCase):
	def setUp(self):
		STATE.update(roles=[])
		self.permissions = modules["permissions"]

	def _controller(self):
		path = APP / "kpi_dashboards" / "doctype" / "kpi_snapshot" / "kpi_snapshot.py"
		namespace = {"__name__": "kpi_snapshot_under_test"}
		exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
		return namespace["KPISnapshot"]

	def test_a_document_read_leaves_the_restricted_rows_out(self):
		controller = self._controller()
		doc = controller()
		doc.values = snapshot_doc("Product", keys_of_department("Product")).values
		STATE["roles"] = ["Stock Manager"]
		doc.apply_fieldlevel_read_permissions()
		kept = {v.kpi_key for v in doc.values}
		self.assertEqual(kept, keys_of_department("Product") - restricted())

	def test_a_document_read_by_an_accounts_manager_is_whole(self):
		controller = self._controller()
		doc = controller()
		doc.values = snapshot_doc("Product", keys_of_department("Product")).values
		STATE["roles"] = ["Accounts Manager"]
		doc.apply_fieldlevel_read_permissions()
		self.assertEqual({v.kpi_key for v in doc.values}, keys_of_department("Product"))

	def _data(self):
		return {
			"KPI-Finance": ("Finance", keys_of_department("Finance")),
			"KPI-Sales": ("Sales", keys_of_department("Sales")),
			"KPI-Product": ("Product", keys_of_department("Product")),
			"KPI-Product-old": ("Product", {"active_sku_count", "rental_backlog_value"}),
			"KPI-Executive": ("Executive", keys_of_department("Executive")),
		}

	def _parent_query(self, condition):
		conn = sqlite_db(self._data())
		self.addCleanup(conn.close)
		where = f" WHERE {condition}" if condition else ""
		return sorted(r[0] for r in conn.execute(f"SELECT `tabKPI Snapshot`.name FROM `tabKPI Snapshot`{where}"))

	def test_the_parent_list_returns_only_snapshots_without_restricted_figures(self):
		"""Run in SQLite: a Sales Manager's list holds Sales, and the Product snapshot that has
		no revenue or margin row; never Product or Executive with one."""
		STATE["roles"] = ["Sales Manager"]
		condition = self.permissions.get_permission_query_conditions("someone@example.com")
		self.assertEqual(self._parent_query(condition), ["KPI-Product-old", "KPI-Sales"])

	def test_the_parent_list_for_an_accounts_user_holds_no_finance_snapshot_with_revenue(self):
		STATE["roles"] = ["Accounts User"]
		condition = self.permissions.get_permission_query_conditions("someone@example.com")
		self.assertEqual(self._parent_query(condition), [])

	def test_the_parent_list_for_an_accounts_manager_is_department_scoped_only(self):
		STATE["roles"] = ["Accounts Manager"]
		condition = self.permissions.get_permission_query_conditions("someone@example.com")
		self.assertNotIn("NOT EXISTS", condition)
		self.assertEqual(self._parent_query(condition), ["KPI-Executive", "KPI-Finance"])

	def test_the_parent_list_for_a_system_manager_has_no_condition(self):
		STATE["roles"] = ["System Manager"]
		self.assertEqual(self.permissions.get_permission_query_conditions("someone@example.com"), "")

	def test_a_role_with_no_department_still_gets_an_explicit_empty_result(self):
		STATE["roles"] = ["Purchase User"]
		self.assertEqual(self.permissions.get_permission_query_conditions("someone@example.com"), "1 = 0")

	def test_the_parent_condition_holds_in_the_child_query_shape(self):
		"""A child-table query made with ``parent_doctype`` joins ``tabKPI Snapshot`` in and
		takes the parent's condition; it returns no row of a snapshot holding a restricted key."""
		STATE["roles"] = ["Sales Manager"]
		condition = self.permissions.get_permission_query_conditions("someone@example.com")
		conn = sqlite_db(self._data())
		self.addCleanup(conn.close)
		rows = conn.execute(
			"SELECT `tabKPI Snapshot Value`.parent, `tabKPI Snapshot Value`.kpi_key FROM `tabKPI Snapshot Value`"
			" INNER JOIN `tabKPI Snapshot` ON `tabKPI Snapshot Value`.parent = `tabKPI Snapshot`.name"
			f" WHERE {condition}"
		).fetchall()
		self.assertTrue(rows)
		self.assertEqual({parent for parent, _key in rows}, {"KPI-Sales", "KPI-Product-old"})
		self.assertEqual({key for _parent, key in rows} & restricted(), set())

	def test_the_parent_condition_quotes_every_restricted_key(self):
		STATE["roles"] = ["Sales Manager"]
		condition = self.permissions.get_permission_query_conditions("someone@example.com")
		for key in restricted():
			self.assertIn(f"'{key}'", condition)

	def test_the_child_condition_leaves_the_restricted_rows_out(self):
		STATE["roles"] = ["Sales Manager"]
		condition = self.permissions.get_value_permission_query_conditions("someone@example.com")
		conn = sqlite_db(self._data())
		self.addCleanup(conn.close)
		keys = {r[0] for r in conn.execute(f"SELECT kpi_key FROM `tabKPI Snapshot Value` WHERE {condition}")}
		self.assertTrue(keys)
		self.assertEqual(keys & restricted(), set())
		self.assertIn("ar_outstanding", keys)

	def test_the_child_condition_is_empty_for_the_revenue_roles(self):
		for roles in REVENUE_ROLE_SETS:
			STATE["roles"] = roles
			self.assertEqual(self.permissions.get_value_permission_query_conditions("someone@example.com"), "")


# ----------------------------------------------------------------------- KPI Target


class TestKpiTargets(unittest.TestCase):
	def setUp(self):
		STATE.update(roles=[])
		self.permissions = modules["permissions"]

	def test_the_doctype_is_readable_by_a_role_outside_the_revenue_roles(self):
		"""Why the rule exists here: Sales Manager holds read, write and create on KPI Target."""
		import json

		doc = json.loads((APP / "kpi_dashboards" / "doctype" / "kpi_target" / "kpi_target.json").read_text("utf-8"))
		readers = {p["role"] for p in doc["permissions"] if p.get("read")}
		self.assertTrue(readers - set(modules["money"].REVENUE_ROLES))

	def test_a_sales_manager_cannot_open_or_edit_a_revenue_target(self):
		STATE["roles"] = ["Sales Manager"]
		for key in restricted():
			for ptype in ("read", "write", "create", "delete"):
				doc = types.SimpleNamespace(kpi_key=key)
				self.assertFalse(self.permissions.target_has_permission(doc, ptype, "someone@example.com"), key)

	def test_a_sales_manager_keeps_every_other_target(self):
		STATE["roles"] = ["Sales Manager"]
		for key in ("win_rate_90", "open_pipeline_value", "won_value_30", "cpl_mtd"):
			doc = types.SimpleNamespace(kpi_key=key)
			self.assertTrue(self.permissions.target_has_permission(doc, "write", "someone@example.com"), key)

	def _stored(self, value):
		"""Answer ``frappe.db.get_value("KPI Target", name, "kpi_key")`` with ``value``
		(an exception class is raised) for the rest of the test."""
		db = self.permissions.frappe.db
		original = db.get_value
		asked = []

		def get_value(doctype, name=None, fieldname=None, *a, **k):
			asked.append((doctype, name, fieldname))
			if isinstance(value, type) and issubclass(value, Exception):
				raise value("no database")
			return value

		db.get_value = get_value
		self.addCleanup(setattr, db, "get_value", original)
		return asked

	def test_a_target_is_judged_on_the_key_it_is_stored_under_too(self):
		"""A save is checked with the document as the save sent it. A target stored for a
		revenue or margin figure stays with the revenue roles whatever key the save carries."""
		STATE["roles"] = ["Sales Manager"]
		asked = self._stored("revenue_30")
		doc = types.SimpleNamespace(name="TGT-Finance-revenue_30-Monthly", kpi_key="win_rate_90")
		for ptype in ("read", "write", "delete"):
			self.assertFalse(self.permissions.target_has_permission(doc, ptype, "someone@example.com"), ptype)
		self.assertEqual(asked[0], ("KPI Target", "TGT-Finance-revenue_30-Monthly", "kpi_key"))

	def test_a_target_cannot_be_moved_onto_a_restricted_key_either(self):
		STATE["roles"] = ["Sales Manager"]
		self._stored("win_rate_90")
		doc = types.SimpleNamespace(name="TGT-Sales-win_rate_90-Monthly", kpi_key="gross_margin_pct")
		self.assertFalse(self.permissions.target_has_permission(doc, "write", "someone@example.com"))

	def test_an_ordinary_stored_target_with_an_ordinary_key_is_still_open(self):
		STATE["roles"] = ["Sales Manager"]
		self._stored("win_rate_90")
		doc = types.SimpleNamespace(name="TGT-Sales-win_rate_90-Monthly", kpi_key="won_value_30")
		self.assertTrue(self.permissions.target_has_permission(doc, "write", "someone@example.com"))

	def test_a_stored_key_that_cannot_be_read_counts_as_restricted(self):
		STATE["roles"] = ["Sales Manager"]
		self._stored(RuntimeError)
		doc = types.SimpleNamespace(name="TGT-Sales-win_rate_90-Monthly", kpi_key="win_rate_90")
		self.assertFalse(self.permissions.target_has_permission(doc, "write", "someone@example.com"))
		STATE["roles"] = ["Accounts Manager"]
		self.assertTrue(self.permissions.target_has_permission(doc, "write", "someone@example.com"))

	def test_the_revenue_roles_keep_every_target(self):
		for roles in REVENUE_ROLE_SETS:
			STATE["roles"] = roles
			for key in ("revenue_30", "gross_margin_pct", "win_rate_90"):
				doc = types.SimpleNamespace(kpi_key=key)
				self.assertTrue(self.permissions.target_has_permission(doc, "read", "someone@example.com"))
			self.assertEqual(self.permissions.get_target_permission_query_conditions("someone@example.com"), "")

	def test_the_target_list_leaves_the_restricted_targets_out(self):
		STATE["roles"] = ["Sales Manager"]
		condition = self.permissions.get_target_permission_query_conditions("someone@example.com")
		conn = sqlite_db({})
		self.addCleanup(conn.close)
		for key in ("revenue_30", "gross_margin_pct", "win_rate_90", "cpl_mtd"):
			conn.execute("INSERT INTO `tabKPI Target` VALUES (?, ?)", (f"TGT-{key}", key))
		keys = {r[0] for r in conn.execute(f"SELECT kpi_key FROM `tabKPI Target` WHERE {condition}")}
		self.assertEqual(keys, {"win_rate_90", "cpl_mtd"})


class TestHooks(unittest.TestCase):
	def _hook(self, name):
		tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
				return ast.literal_eval(node.value)
		self.fail(f"{name} not found in hooks.py")

	def test_the_list_conditions_are_registered(self):
		hooks = self._hook("permission_query_conditions")
		prefix = "erpnext_enhancements.kpi_dashboards.permissions."
		self.assertEqual(hooks["KPI Snapshot"], prefix + "get_permission_query_conditions")
		self.assertEqual(hooks["KPI Snapshot Value"], prefix + "get_value_permission_query_conditions")
		self.assertEqual(hooks["KPI Target"], prefix + "get_target_permission_query_conditions")

	def test_the_document_checks_are_registered(self):
		hooks = self._hook("has_permission")
		prefix = "erpnext_enhancements.kpi_dashboards.permissions."
		self.assertEqual(hooks["KPI Snapshot"], prefix + "has_permission")
		self.assertEqual(hooks["KPI Target"], prefix + "target_has_permission")


# ------------------------------------------------------------ the executive widgets


class TestExecutiveWidgets(unittest.TestCase):
	def setUp(self):
		STATE.update(roles=[], docs={}, rows={}, snapshot_names={}, freshness=None)
		self.executive = modules["executive"]
		self.executive.fetch_all = self._fetch_all

	def _fetch_all(self, doctype, **kwargs):
		filters = kwargs.get("filters") or {}
		if doctype == "KPI Snapshot":
			name = STATE["snapshot_names"].get(filters.get("department"))
			if not name:
				return []
			return [types.SimpleNamespace(name=name, snapshot_date="2026-11-19", generated_at=None, generation_error="")]
		if doctype == "KPI Snapshot Value":
			return [types.SimpleNamespace(**r) for r in STATE["rows"].get(filters.get("parent"), [])]
		return []

	def _add(self, department, figures):
		name = f"KPI-{department}-Daily-2026-11-19"
		STATE["snapshot_names"][department] = name
		STATE["rows"][name] = [
			dict(kpi_key=k, label=k, value=1.0, value_text=f"text {k}", unit="", status=status, trend_pct=None, is_stale=0)
			for k, status in figures
		]

	def test_cash_and_receivables_for_an_accounts_manager(self):
		self._add("Finance", [("cash_collected_30", "Good"), ("revenue_30", "Good"), ("ar_outstanding", "Bad"), ("dso", "Good")])
		STATE["roles"] = ["Accounts Manager"]
		out = self.executive.get_cash_runway()
		self.assertEqual([f["key"] for f in out["figures"]], ["cash_collected_30", "revenue_30", "ar_outstanding", "dso"])

	def test_cash_and_receivables_for_a_sales_manager_leaves_out_revenue_and_cash_collected(self):
		self._add("Finance", [("cash_collected_30", "Good"), ("revenue_30", "Good"), ("ar_outstanding", "Bad"), ("dso", "Good")])
		STATE["roles"] = ["Sales Manager"]
		out = self.executive.get_cash_runway()
		self.assertEqual([f["key"] for f in out["figures"]], ["ar_outstanding", "dso"])

	def test_a_tile_with_nothing_left_says_so(self):
		self._add("Finance", [("revenue_30", "Good")])
		STATE["roles"] = ["Sales Manager"]
		out = self.executive.get_cash_runway()
		self.assertEqual(out["figures"], [])
		self.assertEqual(out["unavailable"], self.executive.NOTHING_FOR_ROLE)

	def test_bookings_and_backlog_are_whole_for_a_sales_manager(self):
		self._add("Sales", [("won_value_30", "Good"), ("open_pipeline_value", "Good"), ("win_rate_90", "Watch")])
		self._add("Production", [("backlog_value", "Good"), ("active_builds", "Good")])
		for roles in (["Sales Manager"], ["Accounts Manager"]):
			STATE["roles"] = roles
			out = self.executive.get_bookings_backlog()
			self.assertEqual(
				[f["key"] for f in out["figures"]],
				["won_value_30", "open_pipeline_value", "win_rate_90", "backlog_value", "active_builds"],
			)

	def test_the_scorecard_counts_only_figures_the_viewer_may_see(self):
		self._add("Finance", [("revenue_30", "Bad"), ("ar_outstanding", "Bad"), ("overdue_invoices", "Good")])
		STATE["roles"] = ["Sales Manager"]
		card = [c for c in self.executive.get_scorecard()["cards"] if c["department"] == "Finance"][0]
		self.assertEqual((card["counts"]["bad"], card["counts"]["good"], card["total"]), (1, 1, 2))
		STATE["roles"] = ["Accounts Manager"]
		card = [c for c in self.executive.get_scorecard()["cards"] if c["department"] == "Finance"][0]
		self.assertEqual((card["counts"]["bad"], card["total"]), (2, 3))

	def test_the_risk_queue_lists_no_restricted_figure_for_anyone_else(self):
		self._add("Finance", [("revenue_30", "Bad"), ("ar_outstanding", "Bad")])
		STATE["roles"] = ["Sales Manager"]
		bad = self.executive.get_risk_queue()["bad"]
		self.assertEqual([b["label"] for b in bad], ["ar_outstanding"])
		for b in bad:
			self.assertNotIn("revenue", repr(b))
		STATE["roles"] = ["System Manager"]
		self.assertEqual(len(self.executive.get_risk_queue()["bad"]), 2)


# ----------------------------------------------------------------- the assistant tool


class TestAssistantTool(unittest.TestCase):
	def setUp(self):
		STATE.update(roles=[], docs={}, rows={}, snapshot_names={}, freshness=None)
		modules["kpi"].snapshots.kpi_enabled = lambda settings=None: True
		from erpnext_enhancements.assistant_tools import kpi_dashboard_status

		self.tool = kpi_dashboard_status.KpiDashboardStatus()

	def test_a_named_department_comes_back_without_the_restricted_figures(self):
		install_snapshot("Product")
		STATE["roles"] = ["Item Manager"]
		out = self.tool.execute({"department": "Product"})
		keys = {v["kpi_key"] for v in out["values"]}
		self.assertEqual(keys, keys_of_department("Product") - restricted())
		self.assertEqual(out["value_count"], len(keys))
		blob = repr(out)
		for key in restricted():
			self.assertNotIn(key, blob)

	def test_the_exceptions_view_lists_no_restricted_figure_either(self):
		for department in ("Product", "Executive"):
			name = install_snapshot(department)
			for v in STATE["docs"][name].values:
				v.status = "Bad"
		STATE["roles"] = ["Sales Manager"]
		out = self.tool.execute({})
		listed = {v["kpi_key"] for d in out["departments"] for v in d.get("values", [])}
		self.assertTrue(listed)
		self.assertEqual(listed & restricted(), set())
		self.assertIn("backlog_value", listed)

	def test_an_accounts_manager_gets_the_whole_finance_snapshot(self):
		install_snapshot("Finance")
		STATE["roles"] = ["Accounts Manager"]
		out = self.tool.execute({"department": "Finance"})
		self.assertEqual({v["kpi_key"] for v in out["values"]}, keys_of_department("Finance"))


# -------------------------------------------------- the wall band and worklist feeds


def function_source(path, name):
	text = path.read_text(encoding="utf-8")
	tree = ast.parse(text)
	for node in ast.walk(tree):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return ast.get_source_segment(text, node), node
	raise AssertionError(f"{name} not found in {path.name}")


class TestWallBand(unittest.TestCase):
	def test_the_wall_band_leaves_the_restricted_figures_out(self):
		STATE.update(roles=["Projects Manager"], docs={}, rows={}, snapshot_names={}, freshness=None)
		source, _node = function_source(API / "task_dashboard.py", "_wall_kpi_block")
		for department in ("Executive", "Service", "Operations"):
			install_snapshot(department)
		namespace = {
			"frappe": sys.modules["frappe"],
			"cint": sys.modules["frappe.utils"].cint,
			"money": modules["money"],
			"WALL_KPI_DEPARTMENTS": ("Executive", "Service", "Operations"),
		}
		exec(source, namespace)
		blocks = namespace["_wall_kpi_block"]()
		self.assertEqual(len(blocks), 3)
		labels = {v["label"] for block in blocks for v in block["values"]}
		self.assertTrue(labels)
		self.assertEqual(labels & restricted(), set())
		self.assertIn("backlog_value", labels)
		STATE["roles"] = ["Accounts Manager"]
		whole = {v["label"] for block in namespace["_wall_kpi_block"]() for v in block["values"]}
		self.assertIn("revenue_30", whole)


class TestWorklistAmounts(unittest.TestCase):
	def test_the_department_worklist_feeds_keep_their_amounts(self):
		"""A per-row amount on a worklist is not a revenue or margin figure: no feed that builds
		a row with an ``amount`` passes it through the KPI registry."""
		checked = 0
		for path in sorted(API.glob("*_dashboard.py")):
			text = path.read_text(encoding="utf-8")
			for node in ast.walk(ast.parse(text)):
				if not isinstance(node, ast.FunctionDef):
					continue
				carries_amount = any(
					isinstance(d, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "amount" for k in d.keys)
					for d in ast.walk(node)
				)
				if not carries_amount:
					continue
				checked += 1
				names = {getattr(n, "id", None) for n in ast.walk(node) if isinstance(n, ast.Name)}
				self.assertNotIn("money", names, f"{path.name}:{node.name}")
		self.assertGreaterEqual(checked, 4, "stalled deals, hand-off backlog, renewal radar, work in progress")


class TestCockpitScript(unittest.TestCase):
	def test_the_cockpit_says_nothing_to_show_when_the_server_left_everything_out(self):
		source = (APP / "custom_html_blocks" / "kpi_cockpit.js").read_text(encoding="utf-8")
		self.assertIn("snap.figures_left_out", source)
		self.assertIn("Nothing to show for your role.", source)


if __name__ == "__main__":
	unittest.main()
