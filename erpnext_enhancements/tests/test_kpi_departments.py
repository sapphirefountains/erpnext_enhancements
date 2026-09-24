"""Bench-free checks that every list of KPI departments agrees.

A department is named in seven places: the snapshot engine's ``AGGREGATORS``, the
``department`` Select on KPI Snapshot and on KPI Target, the role map in ``api/kpi.py``,
the cockpit's route map, the dashboards sidebar, and the Executive rollup's source
departments. v1.530.0 added Service (maintenance, under Production) and turned
Operations into the inventory dashboard, which touched all seven. Every one of them fails
quietly when it disagrees with the others:

* a department missing from a Select makes its nightly snapshot fail to save, and the
  batch logs it and moves on;
* a department missing from ``DEPARTMENT_ROLES`` is visible to System Managers only;
* an ``_EXEC_ROLLUP`` row that names a department no longer emitting the key is skipped
  without a word, so the number just leaves the Executive dashboard;
* a department missing from the cockpit map shows the picker instead of locking.

Like ``test_custom_html_blocks``, nothing here imports frappe. The Python constants are
read with ``ast``; the JSON and JS are read from disk.
"""

import ast
import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
SNAPSHOTS = APP / "kpi_dashboards" / "snapshots.py"
KPI_API = APP / "api" / "kpi.py"
SNAPSHOT_JSON = APP / "kpi_dashboards" / "doctype" / "kpi_snapshot" / "kpi_snapshot.json"
TARGET_JSON = APP / "kpi_dashboards" / "doctype" / "kpi_target" / "kpi_target.json"
COCKPIT_JS = APP / "custom_html_blocks" / "kpi_cockpit.js"
SIDEBARS = APP / "workspace_sidebar"


def _tree(path):
	return ast.parse(path.read_text(encoding="utf-8"))


def _assign(tree, name):
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(
			isinstance(t, ast.Name) and t.id == name for t in node.targets
		):
			return node.value
	raise AssertionError(f"{name} not found")


def _functions(tree):
	return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _strings(fn):
	"""Every string constant in a function, its docstring excluded: the docstring explains
	what moved where, so it names the very things an absence check is looking for."""
	body = fn.body
	if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
		body = body[1:]
	out = set()
	for stmt in body:
		for node in ast.walk(stmt):
			if isinstance(node, ast.Constant) and isinstance(node.value, str):
				out.add(node.value)
	return out


def _emitted_keys(fn):
	"""The first argument of every ``add("key", ...)`` call with a literal key."""
	keys = set()
	for node in ast.walk(fn):
		if (
			isinstance(node, ast.Call)
			and isinstance(node.func, ast.Name)
			and node.func.id == "add"
			and node.args
			and isinstance(node.args[0], ast.Constant)
		):
			keys.add(node.args[0].value)
	return keys


def _select_options(path):
	doc = json.loads(path.read_text(encoding="utf-8"))
	field = next(f for f in doc["fields"] if f["fieldname"] == "department")
	return field["options"].split("\n")


class TestKpiDepartments(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.tree = _tree(SNAPSHOTS)
		cls.fns = _functions(cls.tree)
		aggs = _assign(cls.tree, "AGGREGATORS")
		cls.aggregators = {k.value: v.id for k, v in zip(aggs.keys, aggs.values, strict=True)}
		cls.order = list(cls.aggregators)
		cls.roles = ast.literal_eval(_assign(_tree(KPI_API), "DEPARTMENT_ROLES"))

	def test_selects_list_exactly_the_aggregated_departments(self):
		for path in (SNAPSHOT_JSON, TARGET_JSON):
			with self.subTest(doctype=path.stem):
				self.assertEqual(set(_select_options(path)), set(self.aggregators))

	def test_every_department_has_roles(self):
		self.assertEqual(set(self.roles), set(self.aggregators))
		for dept, roles in self.roles.items():
			self.assertTrue(roles, dept)

	def test_executive_is_built_last(self):
		self.assertEqual(self.order[-1], "Executive")

	def test_exec_rollup_reads_keys_its_source_department_emits(self):
		rollup = ast.literal_eval(
			ast.unparse(_assign(self.tree, "_EXEC_ROLLUP"))
			.replace("metrics.HIGHER", "'H'")
			.replace("metrics.LOWER", "'L'")
		)
		for _exec_key, _label, dept, src_key, _unit, _direction in rollup:
			with self.subTest(key=src_key):
				self.assertIn(dept, self.aggregators)
				self.assertLess(self.order.index(dept), self.order.index("Executive"))
				self.assertIn(src_key, _emitted_keys(self.fns[self.aggregators[dept]]))

	def test_moved_keys_have_exactly_one_home(self):
		# Not a rule for every key: lead_conversion_90 is deliberately on both Sales and
		# Marketing. But a key this split moved must have left its old department, or the
		# patch that moves its KPI Target would leave one of the two ungraded.
		moved = (
			"visits_completed_30",
			"visits_open",
			"chem_oor_30",
			"chem_oor_rate",
			"active_contracts",
			"contracts_expiring_60",
			"items_below_reorder",
			"inventory_stock_value",
			"out_of_stock_sellable",
		)
		for key in moved:
			with self.subTest(key=key):
				homes = [d for d, fn in self.aggregators.items() if key in _emitted_keys(self.fns[fn])]
				self.assertEqual(len(homes), 1, homes)

	def test_maintenance_moved_to_service(self):
		ops = _strings(self.fns["_operations_metrics"])
		self.assertFalse([s for s in ops if "Sapphire Maintenance" in s])
		service = _emitted_keys(self.fns["_service_metrics"])
		for key in (
			"visits_completed_30",
			"visits_open",
			"chem_oor_30",
			"chem_oor_rate",
			"active_contracts",
			"contracts_expiring_60",
		):
			self.assertIn(key, service)

	def test_stock_levels_moved_to_operations(self):
		ops = _emitted_keys(self.fns["_operations_metrics"])
		product = _emitted_keys(self.fns["_product_metrics"])
		for key in ("items_below_reorder", "inventory_stock_value", "out_of_stock_sellable"):
			self.assertIn(key, ops)
			self.assertNotIn(key, product)
		for key in (
			"store_runs_30",
			"stocked_items_out",
			"placeholder_cost_stock_lines",
			"unpriced_po_lines_90",
		):
			self.assertIn(key, ops)

	def test_cockpit_locks_to_every_department_dashboard(self):
		js = COCKPIT_JS.read_text(encoding="utf-8")
		mapped = dict(re.findall(r'"([a-z ]+ dashboard)":\s*"([A-Za-z]+)"', js))
		for dept in self.aggregators:
			with self.subTest(dept=dept):
				self.assertEqual(mapped.get(f"{dept.lower()} dashboard"), dept)


class TestDashboardSidebars(unittest.TestCase):
	"""All eleven KPI sidebars carry one identical item list, and Service sits in a
	Production group. A v16 sidebar nests items only under a Section Break (sidebar.js
	``find_nested_items``), so "Service under Production" has to be a group."""

	@classmethod
	def setUpClass(cls):
		cls.sidebars = {
			p.stem: json.loads(p.read_text(encoding="utf-8"))
			for p in [*sorted(SIDEBARS.glob("*_dashboard.json")), SIDEBARS / "kpi_dashboards.json"]
		}

	def test_every_dashboard_has_a_sidebar(self):
		aggs = _assign(_tree(SNAPSHOTS), "AGGREGATORS")
		for key in aggs.keys:
			self.assertIn(f"{key.value.lower()}_dashboard", self.sidebars)

	def test_sidebars_share_one_item_list(self):
		lists = {name: json.dumps(d["items"], sort_keys=True) for name, d in self.sidebars.items()}
		self.assertEqual(len(set(lists.values())), 1, sorted(lists))

	def test_service_is_nested_under_production(self):
		items = next(iter(self.sidebars.values()))["items"]
		links = [i.get("link_to") for i in items if i["type"] == "Link"]
		self.assertIn("Production Dashboard", links)
		self.assertIn("Service Dashboard", links)
		i_prod = next(i for i, it in enumerate(items) if it.get("link_to") == "Production Dashboard")
		i_svc = next(i for i, it in enumerate(items) if it.get("link_to") == "Service Dashboard")
		section = max(i for i, it in enumerate(items[:i_prod]) if it["type"] == "Section Break")
		self.assertEqual(items[section]["label"], "Production")
		self.assertTrue(items[section]["indent"])
		for idx in (i_prod, i_svc):
			self.assertEqual(items[idx]["child"], 1)
			self.assertTrue(section < idx)
		# nothing between the group header and its two children
		self.assertEqual(sorted((i_prod, i_svc)), [section + 1, section + 2])


if __name__ == "__main__":
	unittest.main()
