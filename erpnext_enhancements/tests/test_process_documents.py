"""Bench-free contract tests for the Process Document seed data.

Runs with plain ``pytest``/``unittest`` (no Frappe import): the chart dict is
extracted from setup/process_documents.py with ``ast`` so the module's
``import frappe`` never executes.

The critical rule: **no ``<`` anywhere in a chart**. Frappe HTML-sanitizes
the Markdown Editor field on save the moment a value looks like HTML
(``<br/>``, ``<-->``, …), turning every ``-->`` into ``--&gt;`` — which both
breaks the rendered diagram and makes the seeder's drift check rewrite the
document on every migrate. Multi-line labels must use quoted strings with
real newlines instead.
"""

import ast
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent.parent / "setup" / "process_documents.py"


def load_charts():
	"""Extract PROCESS_DOCUMENTS from the module without importing frappe."""
	tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
	assigns = [node for node in tree.body if isinstance(node, ast.Assign)]
	namespace = {}
	exec(compile(ast.Module(body=assigns, type_ignores=[]), str(MODULE_PATH), "exec"), namespace)
	return namespace["PROCESS_DOCUMENTS"]


class TestProcessDocumentCharts(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.charts = load_charts()

	def test_charts_exist(self):
		"""The seed dict covers the eleven ported site charts plus the app chart."""
		self.assertGreaterEqual(len(self.charts), 12)
		self.assertIn("Sapphire Fountains Enhancements Flow", self.charts)

	def test_titles_are_valid_docnames(self):
		"""Titles become docnames (autoname: field:title) — non-empty, no edge whitespace."""
		for title in self.charts:
			self.assertTrue(title and title == title.strip(), f"bad title: {title!r}")

	def test_charts_are_flowcharts(self):
		for title, code in self.charts.items():
			self.assertTrue(code.strip().startswith("graph "), f"{title}: not a mermaid flowchart")

	def test_no_html_in_charts(self):
		"""No ``<`` at all: Frappe's HTML sanitizer would mangle the chart on save."""
		for title, code in self.charts.items():
			self.assertNotIn("<", code, f"{title}: contains '<' — Frappe will HTML-escape it on save")

	def test_no_preescaped_entities(self):
		"""A chart pasted back from a sanitized save would carry HTML entities."""
		for title, code in self.charts.items():
			for entity in ("&gt;", "&lt;", "&amp;", "&quot;"):
				self.assertNotIn(entity, code, f"{title}: contains pre-escaped {entity}")


if __name__ == "__main__":
	unittest.main()
