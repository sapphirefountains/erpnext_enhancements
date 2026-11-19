"""Money figures on the KPI dashboards go to Accounts Manager and System Manager only.

Owner decision, 2026-11-19: a figure that states the company's money is returned only to a
caller holding Accounts Manager or System Manager. Every other role that may open a
department's dashboard keeps that department's operational figures, and the money figures
are left out of the answer. The department gate (``api.kpi.DEPARTMENT_ROLES``) is not part
of it. ``kpi_dashboards/money.py`` holds the registry and the one role helper.

Bench-free. The registry is checked against the aggregators' source (a new money-looking
KPI has to be classified), and the filter is RUN: through the cockpit endpoint, a snapshot
document read, the child-row list condition, the executive widgets, the assistant tool, the
Task Dashboard wall band and the shared worklist helper, each over a stand-in ``frappe``.

Run: python -m unittest erpnext_enhancements.tests.test_kpi_money_roles
"""

import ast
import datetime
import re
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
SNAPSHOTS = APP / "kpi_dashboards" / "snapshots.py"
RENTAL_KPIS = APP / "asset_management" / "rental_kpis.py"
API = APP / "api"

ACCOUNTS = ["Accounts Manager", "Accounts User", "Projects Manager"]
MONEY_ROLE_SETS = (["Accounts Manager"], ["System Manager"], ["Accounts Manager", "Sales Manager"])
NO_MONEY_ROLE_SETS = (
	["Projects Manager"],
	["Accounts User"],
	["Sales Manager", "Sales User"],
	["Stock Manager", "Item Manager"],
	["Maintenance Manager", "Projects User"],
	["HR Manager", "HR Team"],
	[],
)

#: A key or label that reads like money has to be classified in kpi_dashboards/money.py.
MONEY_WORDS = re.compile(
	r"revenue|margin|profit|cash|value|cost|spend|receivable|payable|backlog|pipeline"
	r"|deal size|price|budget|income|\bdso\b",
	re.IGNORECASE,
)

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


# ------------------------------------------------------------------- the registry


class TestTheRegistryCoversEveryAggregator(unittest.TestCase):
	def test_the_scan_finds_the_figures(self):
		self.assertGreater(len(figures()), 100)
		self.assertIn(("_finance_metrics", "revenue_30", "Revenue (30d)", "USD"), figures())
		self.assertTrue(any(key == "rental_booked_revenue_90" for _fn, key, _l, _u in figures()))
		self.assertTrue(any(fn == "_executive_metrics" and key == "backlog_value" for fn, key, _l, _u in figures()))

	def test_every_dollar_figure_is_marked(self):
		money = modules["money"]
		missing = sorted({key for _fn, key, _l, unit in figures() if unit == "USD" and key not in money.MONEY_KPIS})
		self.assertEqual(missing, [], "a USD figure is not in MONEY_KPIS")

	def test_every_money_looking_key_or_label_is_classified(self):
		money = modules["money"]
		unclassified = sorted(
			{
				key
				for _fn, key, label, _unit in figures()
				if (MONEY_WORDS.search(key) or MONEY_WORDS.search(label))
				and key not in money.MONEY_KPIS
				and key not in money.REVIEWED_NOT_MONEY
			}
		)
		self.assertEqual(
			unclassified, [], "mark it in MONEY_KPIS, or list it in REVIEWED_NOT_MONEY with the reason"
		)

	def test_margin_in_percent_is_marked(self):
		self.assertIn("gross_margin_pct", modules["money"].MONEY_KPIS)

	def test_the_two_tables_do_not_overlap(self):
		money = modules["money"]
		self.assertEqual(set(money.MONEY_KPIS) & set(money.REVIEWED_NOT_MONEY), set())

	def test_every_registry_entry_is_a_real_figure(self):
		"""A key an aggregator no longer emits is a stale entry, and hides a name for nothing."""
		money = modules["money"]
		emitted = {key for _fn, key, _l, _u in figures()}
		self.assertEqual(sorted(set(money.MONEY_KPIS) - emitted), [])
		self.assertEqual(sorted(set(money.REVIEWED_NOT_MONEY) - emitted), [])

	def test_registry_labels_match_the_aggregators(self):
		money = modules["money"]
		labels = {}
		for _fn, key, label, _u in figures():
			labels.setdefault(key, set()).add(label)
		for key, label in money.MONEY_KPIS.items():
			self.assertIn(label, labels[key], key)

	def test_every_reviewed_exception_gives_a_reason(self):
		for key, reason in modules["money"].REVIEWED_NOT_MONEY.items():
			self.assertTrue(reason.strip(), key)


# --------------------------------------------------------------- the filter, run


def rows_for(department):
	return [{"kpi_key": key, "label": key} for key in sorted(keys_of_department(department))]


class TestTheFilter(unittest.TestCase):
	def test_money_roles_see_everything(self):
		money = modules["money"]
		for department in _aggregator_functions():
			rows = rows_for(department)
			for roles in MONEY_ROLE_SETS:
				self.assertEqual(money.without_money(rows, roles=roles), rows, (department, roles))

	def test_a_projects_manager_keeps_the_operational_figures_and_loses_every_marked_one(self):
		money = modules["money"]
		for department in ("Production", "Operations", "Design", "Service"):
			rows = rows_for(department)
			kept = {r["kpi_key"] for r in money.without_money(rows, roles=["Projects Manager"])}
			emitted = {r["kpi_key"] for r in rows}
			self.assertEqual(kept, emitted - set(money.MONEY_KPIS), department)
			self.assertEqual(kept & set(money.MONEY_KPIS), set(), department)

	def test_production_loses_exactly_its_marked_figures(self):
		money = modules["money"]
		kept = {r["kpi_key"] for r in money.without_money(rows_for("Production"), roles=["Projects Manager"])}
		self.assertNotIn("backlog_value", kept)
		self.assertNotIn("build_backlog_value", kept)
		self.assertNotIn("change_order_net_value", kept)
		for operational in ("active_projects", "projects_overdue", "on_time_milestone_rate", "change_orders"):
			self.assertIn(operational, kept)

	def test_no_role_but_the_two_sees_a_marked_figure_in_any_department(self):
		money = modules["money"]
		for department in _aggregator_functions():
			for roles in NO_MONEY_ROLE_SETS:
				kept = {r["kpi_key"] for r in money.without_money(rows_for(department), roles=roles)}
				self.assertEqual(kept & set(money.MONEY_KPIS), set(), (department, roles))

	def test_the_filter_works_on_documents_and_does_not_change_its_input(self):
		money = modules["money"]
		rows = [types.SimpleNamespace(kpi_key="revenue_30"), types.SimpleNamespace(kpi_key="active_projects")]
		out = money.without_money(rows, roles=["Projects Manager"])
		self.assertEqual([r.kpi_key for r in out], ["active_projects"])
		self.assertEqual(len(rows), 2)

	def test_the_role_rule_reads_the_session_when_given_no_roles(self):
		money = modules["money"]
		STATE["roles"] = ["Projects Manager"]
		self.assertFalse(money.can_see_money())
		STATE["roles"] = ["Projects Manager", "Accounts Manager"]
		self.assertTrue(money.can_see_money())

	def test_the_worklist_helper_drops_the_amount_for_everyone_else(self):
		money = modules["money"]
		rows = [{"title": "a", "amount": 5.0, "idle_days": 3}]
		self.assertEqual(
			money.drop_worklist_amounts([dict(r) for r in rows], roles=["Sales Manager"]),
			[{"title": "a", "idle_days": 3}],
		)
		self.assertEqual(money.drop_worklist_amounts([dict(r) for r in rows], roles=["System Manager"]), rows)


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
		self.money = modules["money"]
		self.kpi.snapshots.kpi_enabled = lambda settings=None: True

	def _keys(self, department):
		result = self.kpi.get_kpi_dashboard(department)
		return {v["kpi_key"] for v in result["snapshot"]["values"]}, result["snapshot"]

	def test_an_accounts_manager_sees_the_finance_money_figures(self):
		install_snapshot("Finance")
		STATE["roles"] = ["Accounts Manager"]
		keys, snap = self._keys("Finance")
		self.assertEqual(keys, keys_of_department("Finance"))
		self.assertFalse(snap["money_left_out"])

	def test_a_system_manager_sees_every_department_whole(self):
		STATE["roles"] = ["System Manager"]
		for department in _aggregator_functions():
			install_snapshot(department)
			keys, _snap = self._keys(department)
			self.assertEqual(keys, keys_of_department(department), department)

	def test_an_accounts_user_opens_finance_and_sees_no_marked_figure(self):
		install_snapshot("Finance")
		STATE["roles"] = ["Accounts User"]
		keys, snap = self._keys("Finance")
		self.assertEqual(keys & set(self.money.MONEY_KPIS), set())
		self.assertEqual(keys, keys_of_department("Finance") - set(self.money.MONEY_KPIS))
		self.assertIn("overdue_invoices", keys)
		self.assertTrue(snap["money_left_out"])

	def test_a_projects_manager_sees_production_operations_only(self):
		install_snapshot("Production")
		STATE["roles"] = ["Projects Manager"]
		keys, snap = self._keys("Production")
		self.assertEqual(keys, keys_of_department("Production") - set(self.money.MONEY_KPIS))
		self.assertTrue(snap["money_left_out"])
		blob = repr(snap)
		for marked in self.money.MONEY_KPIS:
			self.assertNotIn(marked, blob)

	def test_the_department_gate_is_unchanged(self):
		install_snapshot("Finance")
		STATE["roles"] = ["Projects Manager"]
		with self.assertRaises(sys.modules["frappe"].PermissionError):
			self.kpi.get_kpi_dashboard("Finance")
		self.assertEqual(
			self.kpi.visible_departments(),
			[d for d in self.kpi.AVAILABLE_DEPARTMENTS if d in ("Operations", "Design", "Production", "Service")],
		)

	def test_a_department_that_is_all_money_reports_nothing_left_not_a_zero(self):
		name = "KPI-Executive-Daily-2026-11-19"
		STATE["snapshot_names"]["Executive"] = name
		STATE["docs"][name] = snapshot_doc("Executive", {"revenue_30", "backlog_value"})
		STATE["roles"] = ["Sales Manager"]
		result = self.kpi.get_kpi_dashboard("Executive")
		self.assertEqual(result["snapshot"]["values"], [])
		self.assertTrue(result["snapshot"]["money_left_out"])

	def test_the_refresh_endpoint_answers_through_the_same_filter(self):
		doc = snapshot_doc("Production", keys_of_department("Production"))
		self.kpi.snapshots.build_department_snapshot = lambda department, period="Daily", generated_by="": doc
		self.kpi.frappe.db.commit = lambda: None
		STATE["roles"] = ["Projects Manager"]
		result = self.kpi.refresh_kpi_dashboard("Production")
		keys = {v["kpi_key"] for v in result["snapshot"]["values"]}
		self.assertEqual(keys & set(self.money.MONEY_KPIS), set())
		self.assertIn("active_projects", keys)


# -------------------------------------------------- snapshot documents and child rows


class TestSnapshotReads(unittest.TestCase):
	def setUp(self):
		STATE.update(roles=[])
		self.money = modules["money"]

	def _controller(self):
		path = APP / "kpi_dashboards" / "doctype" / "kpi_snapshot" / "kpi_snapshot.py"
		namespace = {"__name__": "kpi_snapshot_under_test"}
		exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
		return namespace["KPISnapshot"]

	def test_a_document_read_leaves_the_marked_rows_out(self):
		controller = self._controller()
		doc = controller()
		doc.values = snapshot_doc("Production", keys_of_department("Production")).values
		STATE["roles"] = ["Projects Manager"]
		doc.apply_fieldlevel_read_permissions()
		kept = {v.kpi_key for v in doc.values}
		self.assertEqual(kept, keys_of_department("Production") - set(self.money.MONEY_KPIS))

	def test_a_document_read_by_an_accounts_manager_is_whole(self):
		controller = self._controller()
		doc = controller()
		doc.values = snapshot_doc("Production", keys_of_department("Production")).values
		STATE["roles"] = ["Accounts Manager"]
		doc.apply_fieldlevel_read_permissions()
		self.assertEqual({v.kpi_key for v in doc.values}, keys_of_department("Production"))

	def test_the_child_list_condition_names_every_marked_key_for_everyone_else(self):
		permissions = modules["permissions"]
		STATE["roles"] = ["Sales Manager"]
		condition = permissions.get_value_permission_query_conditions()
		self.assertIn("`tabKPI Snapshot Value`.`kpi_key` NOT IN (", condition)
		for key in self.money.MONEY_KPIS:
			self.assertIn(f"'{key}'", condition)

	def test_the_child_list_condition_is_empty_for_the_money_roles(self):
		permissions = modules["permissions"]
		for roles in MONEY_ROLE_SETS:
			STATE["roles"] = roles
			self.assertEqual(permissions.get_value_permission_query_conditions(), "")

	def test_the_child_condition_is_registered_in_hooks(self):
		tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if isinstance(node, ast.Assign) and any(
				isinstance(t, ast.Name) and t.id == "permission_query_conditions" for t in node.targets
			):
				hooks = ast.literal_eval(node.value)
				self.assertEqual(
					hooks["KPI Snapshot Value"],
					"erpnext_enhancements.kpi_dashboards.permissions.get_value_permission_query_conditions",
				)
				return
		self.fail("permission_query_conditions not found in hooks.py")


# ------------------------------------------------------------ the executive widgets


class TestExecutiveWidgets(unittest.TestCase):
	def setUp(self):
		STATE.update(roles=[], docs={}, rows={}, snapshot_names={}, freshness=None)
		self.executive = modules["executive"]
		self.money = modules["money"]
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

	def test_cash_and_receivables_for_a_sales_manager_keeps_only_the_duration(self):
		self._add("Finance", [("cash_collected_30", "Good"), ("revenue_30", "Good"), ("ar_outstanding", "Bad"), ("dso", "Good")])
		STATE["roles"] = ["Sales Manager"]
		out = self.executive.get_cash_runway()
		self.assertEqual([f["key"] for f in out["figures"]], ["dso"])

	def test_a_tile_with_nothing_left_says_so(self):
		self._add("Finance", [("cash_collected_30", "Good"), ("revenue_30", "Good"), ("ar_outstanding", "Bad")])
		STATE["roles"] = ["Sales Manager"]
		out = self.executive.get_cash_runway()
		self.assertEqual(out["figures"], [])
		self.assertEqual(out["unavailable"], self.executive.NOTHING_FOR_ROLE)

	def test_bookings_and_backlog_keep_the_counts_and_rates(self):
		self._add("Sales", [("won_value_30", "Good"), ("open_pipeline_value", "Good"), ("win_rate_90", "Watch")])
		self._add("Production", [("backlog_value", "Good"), ("active_builds", "Good")])
		STATE["roles"] = ["Sales Manager"]
		out = self.executive.get_bookings_backlog()
		self.assertEqual([f["key"] for f in out["figures"]], ["win_rate_90", "active_builds"])
		STATE["roles"] = ["Accounts Manager"]
		out = self.executive.get_bookings_backlog()
		self.assertEqual(len(out["figures"]), 5)

	def test_the_scorecard_counts_only_figures_the_viewer_may_see(self):
		self._add("Finance", [("revenue_30", "Bad"), ("ar_outstanding", "Bad"), ("overdue_invoices", "Good")])
		STATE["roles"] = ["Sales Manager"]
		card = [c for c in self.executive.get_scorecard()["cards"] if c["department"] == "Finance"][0]
		self.assertEqual((card["counts"]["bad"], card["counts"]["good"], card["total"]), (0, 1, 1))
		STATE["roles"] = ["Accounts Manager"]
		card = [c for c in self.executive.get_scorecard()["cards"] if c["department"] == "Finance"][0]
		self.assertEqual((card["counts"]["bad"], card["total"]), (2, 3))

	def test_the_risk_queue_lists_no_marked_figure_for_anyone_else(self):
		self._add("Finance", [("revenue_30", "Bad"), ("overdue_invoices", "Bad")])
		STATE["roles"] = ["Sales Manager"]
		bad = self.executive.get_risk_queue()["bad"]
		self.assertEqual([b["label"] for b in bad], ["overdue_invoices"])
		for b in bad:
			self.assertNotIn("revenue", repr(b))
		STATE["roles"] = ["System Manager"]
		self.assertEqual(len(self.executive.get_risk_queue()["bad"]), 2)


# ----------------------------------------------------------------- the assistant tool


class TestAssistantTool(unittest.TestCase):
	def setUp(self):
		STATE.update(roles=[], docs={}, rows={}, snapshot_names={}, freshness=None)
		modules["kpi"].snapshots.kpi_enabled = lambda settings=None: True
		self.money = modules["money"]
		from erpnext_enhancements.assistant_tools import kpi_dashboard_status

		self.tool = kpi_dashboard_status.KpiDashboardStatus()

	def test_a_named_department_comes_back_without_the_marked_figures(self):
		install_snapshot("Production")
		STATE["roles"] = ["Projects Manager"]
		out = self.tool.execute({"department": "Production"})
		keys = {v["kpi_key"] for v in out["values"]}
		self.assertEqual(keys, keys_of_department("Production") - set(self.money.MONEY_KPIS))
		self.assertEqual(out["value_count"], len(keys))
		blob = repr(out)
		for marked in self.money.MONEY_KPIS:
			self.assertNotIn(marked, blob)

	def test_the_exceptions_view_lists_no_marked_figure_either(self):
		for department in ("Production", "Operations"):
			name = install_snapshot(department)
			for v in STATE["docs"][name].values:
				v.status = "Bad"
		STATE["roles"] = ["Projects Manager"]
		out = self.tool.execute({})
		listed = {v["kpi_key"] for d in out["departments"] for v in d.get("values", [])}
		self.assertTrue(listed)
		self.assertEqual(listed & set(self.money.MONEY_KPIS), set())

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
	def test_the_wall_band_leaves_the_marked_figures_out(self):
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
		self.assertEqual(labels & set(modules["money"].MONEY_KPIS), set())
		STATE["roles"] = ["Accounts Manager"]
		whole = {v["label"] for block in namespace["_wall_kpi_block"]() for v in block["values"]}
		self.assertIn("revenue_30", whole)


class TestWorklistAmounts(unittest.TestCase):
	def test_every_dashboard_feed_that_prints_an_amount_drops_it_for_everyone_else(self):
		"""A row carrying an ``amount`` key must pass through ``drop_worklist_amounts``."""
		offenders = []
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
				calls = {
					getattr(c.func, "attr", None) for c in ast.walk(node) if isinstance(c, ast.Call)
				}
				if "drop_worklist_amounts" not in calls:
					offenders.append(f"{path.name}:{node.name}")
		self.assertEqual(offenders, [])
		self.assertEqual(checked, 4, "stalled deals, hand-off backlog, renewal radar, work in progress")


class TestBankBalances(unittest.TestCase):
	def test_the_bank_balance_feed_is_read_by_the_money_roles_only(self):
		tree = ast.parse((APP / "plaid_banking" / "core" / "api.py").read_text(encoding="utf-8"))
		for node in tree.body:
			if isinstance(node, ast.Assign) and any(
				isinstance(t, ast.Name) and t.id == "FINANCE_READ_ROLES" for t in node.targets
			):
				self.assertEqual(set(ast.literal_eval(node.value)), set(modules["money"].MONEY_ROLES))
				return
		self.fail("FINANCE_READ_ROLES not found")


class TestCockpitScript(unittest.TestCase):
	def test_the_cockpit_says_nothing_to_show_when_the_server_left_everything_out(self):
		source = (APP / "custom_html_blocks" / "kpi_cockpit.js").read_text(encoding="utf-8")
		self.assertIn("snap.money_left_out", source)
		self.assertIn("Nothing to show for your role.", source)


if __name__ == "__main__":
	unittest.main()
