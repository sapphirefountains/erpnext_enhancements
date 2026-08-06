"""Bench-free wiring tests for the department dashboard widgets.

A dashboard widget is only as good as four things agreeing:

1. ``api/dashboard_widgets.py``'s ``WIDGETS`` registry (which toggle backs it),
2. the Check fields on ``ERPNext Enhancements Settings`` (the toggle exists),
3. ``setup/custom_html_blocks.py``'s ``BLOCKS`` + ``DEPARTMENT_DASHBOARD_BLOCKS``
   (the block is seeded, and placed on exactly one workspace), and
4. the shipped workspace JSONs (the desk actually renders it).

Every one of those can drift silently. A placement naming an unseeded block
renders an **empty div** — no error, no log line. A toggle fieldname typo means a
widget that can never be switched on, and since the toggles default OFF that
looks exactly like "nobody has enabled it yet". So the wiring is asserted here
rather than discovered on a dashboard.

Like ``test_custom_html_blocks``, this avoids importing frappe: the constants are
pulled out of the modules with ``ast`` and the JSON is read from disk, so the
suite runs in CI with no bench.
"""

import ast
import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parent.parent

WIDGETS_MODULE = APP / "api" / "dashboard_widgets.py"
SEEDER_MODULE = APP / "setup" / "custom_html_blocks.py"
SETTINGS_JSON = (
	APP / "enhancements_core" / "doctype" / "erpnext_enhancements_settings" / "erpnext_enhancements_settings.json"
)
BLOCK_SOURCES = APP / "custom_html_blocks"
WORKSPACES = APP / "kpi_dashboards" / "workspace"

KPI_COCKPIT = "KPI Cockpit"

# department -> the api module whose feeds serve that department's widgets.
FEED_MODULES = {
	"Sales": "sales_dashboard",
	"Operations": "operations_dashboard",
	"Design": "design_dashboard",
	"Production": "production_dashboard",
	"Marketing": "marketing_dashboard",
	"HR": "hr_dashboard",
	"Executive": "executive_dashboard",
}


def _literal(path, name):
	"""Read one module-level literal assignment without importing the module."""
	tree = ast.parse(path.read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign):
			for target in node.targets:
				if isinstance(target, ast.Name) and target.id == name:
					return ast.literal_eval(node.value)
	raise AssertionError(f"{name} not found in {path}")


def _scrub(text):
	return text.lower().replace(" ", "_").replace("-", "_")


class DashboardWidgetWiring(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.widgets = _literal(WIDGETS_MODULE, "WIDGETS")
		cls.blocks = _literal(SEEDER_MODULE, "BLOCKS")
		cls.placement = _literal(SEEDER_MODULE, "DEPARTMENT_DASHBOARD_BLOCKS")
		cls.block_names = {name for name, _prefix in cls.blocks}
		cls.settings = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))

	def test_every_widget_toggle_exists_and_defaults_off(self):
		fields = {f["fieldname"]: f for f in self.settings["fields"] if f.get("fieldname")}
		for department, widgets in self.widgets.items():
			for widget, fieldname in widgets.items():
				with self.subTest(department=department, widget=widget):
					self.assertIn(fieldname, fields, f"no settings field {fieldname}")
					field = fields[fieldname]
					self.assertEqual(field["fieldtype"], "Check")
					# Default OFF is the contract: a widget nobody asked for must
					# not start querying on upgrade.
					self.assertEqual(str(field.get("default", "0")), "0")
					self.assertIn(fieldname, self.settings["field_order"])

	def test_widget_toggles_are_unique(self):
		seen = {}
		for department, widgets in self.widgets.items():
			for widget, fieldname in widgets.items():
				self.assertNotIn(
					fieldname, seen, f"{fieldname} backs both {seen.get(fieldname)} and {department}.{widget}"
				)
				seen[fieldname] = f"{department}.{widget}"

	def test_every_placed_block_is_seeded(self):
		"""A placement naming an unseeded block renders an empty div, silently."""
		for workspace, names in self.placement.items():
			for name in names:
				with self.subTest(workspace=workspace, block=name):
					self.assertIn(name, self.block_names)

	def test_every_seeded_block_has_its_three_source_files(self):
		for name, prefix in self.blocks:
			for ext in ("html", "js", "css"):
				with self.subTest(block=name, ext=ext):
					self.assertTrue(
						(BLOCK_SOURCES / f"{prefix}.{ext}").exists(),
						f"{prefix}.{ext} missing for {name!r}",
					)

	def test_each_block_is_placed_on_at_most_one_department_workspace(self):
		"""Department widgets are department-scoped; a block on two dashboards
		would be gated by one department's roles on both."""
		seen = {}
		for workspace, names in self.placement.items():
			for name in names:
				self.assertNotIn(name, seen, f"{name} is placed on both {seen.get(name)} and {workspace}")
				seen[name] = workspace

	def test_every_widget_department_has_a_workspace_placement(self):
		for department, widgets in self.widgets.items():
			workspace = f"{department} Dashboard"
			with self.subTest(department=department):
				self.assertIn(workspace, self.placement)
				self.assertEqual(
					len(widgets),
					len(self.placement[workspace]),
					f"{department} registers {len(widgets)} widgets but places "
					f"{len(self.placement[workspace])} blocks",
				)

	def test_workspace_json_matches_the_seeder_placement(self):
		"""The shipped JSON is what a fresh site gets; the seeder is the backstop.

		Both the ``content`` blob and the ``custom_blocks`` child rows are checked:
		v16 builds the block payload from ``custom_blocks``, so a content-only
		placement renders nothing at all.
		"""
		for workspace, names in self.placement.items():
			folder = _scrub(workspace)
			path = WORKSPACES / folder / f"{folder}.json"
			with self.subTest(workspace=workspace):
				self.assertTrue(path.exists(), f"{path} missing")
				doc = json.loads(path.read_text(encoding="utf-8"))

				expected = list(names) + [KPI_COCKPIT]

				content = json.loads(doc.get("content") or "[]")
				placed = [
					b["data"]["custom_block_name"]
					for b in content
					if b.get("type") == "custom_block"
				]
				self.assertEqual(placed, expected)

				rows = [r["custom_block_name"] for r in doc.get("custom_blocks") or []]
				self.assertEqual(rows, expected)

	def test_feed_modules_gate_every_widget_they_serve(self):
		"""Each widget key in the registry has a matching ``widget_feed`` call.

		This is the check that catches a renamed widget key: the decorator would
		silently return ``{"enabled": False}`` forever, because an unknown key
		reads as OFF by design.
		"""
		for department, module in FEED_MODULES.items():
			source = (APP / "api" / f"{module}.py").read_text(encoding="utf-8")
			decorated = set(re.findall(r'widget_feed\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)', source))
			expected = {(department, widget) for widget in self.widgets[department]}
			with self.subTest(department=department):
				self.assertEqual(decorated, expected)


class MarketingAttributionColumn(unittest.TestCase):
	"""The Marketing feeds must never read the orphaned ``source`` column.

	erpnext v15 renamed ``Lead.source`` to ``utm_source``; frappe never drops
	columns, so a query against ``source`` still runs and returns a plausible
	number measured against frozen pre-2023 data. That mistake silently broke four
	KPIs (see kpi_dashboards/README.md). ``kpi_dashboards`` guards its own copy of
	this rule; this guards the dashboard feeds.
	"""

	# Ways the dead column would actually get read. Deliberately narrow: the word
	# "source" is legitimate as a SQL *alias*, as a result-row attribute, and as a
	# dict key, and a pattern broad enough to catch those would just get muted.
	DEAD_COLUMN_PATTERNS = (
		r"coalesce\(\s*source\b",
		r"\b(?:l|o|lead|opp|opportunity)\.source\b",
		r"`source`",
		r"""has_column\(\s*["'](?:Lead|Opportunity)["']\s*,\s*["']source["']""",
	)

	@staticmethod
	def _code_strings(path):
		"""Every string literal in the module except docstrings.

		SQL lives in string literals; the warnings *about* this bug live in
		docstrings and comments. Scanning the raw file text would match the prose
		explaining the rule — so the scan reads what the code actually executes.
		"""
		tree = ast.parse(path.read_text(encoding="utf-8"))
		docstring_nodes = set()
		for node in ast.walk(tree):
			if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
				body = getattr(node, "body", None)
				if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
					if isinstance(body[0].value.value, str):
						docstring_nodes.add(id(body[0].value))
		return [
			node.value
			for node in ast.walk(tree)
			if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_nodes
		]

	def test_no_feed_reads_the_dead_source_column(self):
		blob = "\n".join(self._code_strings(APP / "api" / "marketing_dashboard.py"))
		for pattern in self.DEAD_COLUMN_PATTERNS:
			with self.subTest(pattern=pattern):
				self.assertIsNone(
					re.search(pattern, blob),
					f"marketing_dashboard.py reads the orphaned `source` column ({pattern})",
				)

	def test_attribution_reads_custom_lead_source(self):
		"""The positive half: the column helper names the field the app populates."""
		text = (APP / "api" / "marketing_dashboard.py").read_text(encoding="utf-8")
		self.assertIn('"custom_lead_source" if frappe.db.has_column("Lead", "custom_lead_source")', text)

	def test_unsourced_bucket_is_treated_as_unsourced(self):
		"""The 'Unknown (pre-Aug 2026)' bucket is a recorded gap, not a channel."""
		source = (APP / "api" / "marketing_dashboard.py").read_text(encoding="utf-8")
		self.assertIn("UNKNOWN_LEAD_SOURCE", source)
		self.assertIn("from erpnext_enhancements.crm_enhancements.attribution import", source)


if __name__ == "__main__":
	unittest.main()
