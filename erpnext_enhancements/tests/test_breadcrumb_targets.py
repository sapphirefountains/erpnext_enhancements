"""Form breadcrumbs that lead to this app's pages (v1.779.0, Nik 2026-11-03).

Bench-free: reads files only. The behaviour is executed, not grepped, in
``scripts/test_breadcrumb_targets.js``; this suite holds the seams that harness cannot see: that
the module ships in the global bundle, that it wraps frappe 16.53's own builder rather than the
deprecated ``frappe.breadcrumbs`` layer, that every target is a ``/desk/`` path, and that CI runs
the harness.

Run: python -m unittest erpnext_enhancements.tests.test_breadcrumb_targets
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
MODULE = APP / "public" / "js" / "global_enhancements" / "breadcrumb_targets.js"
BUNDLE = APP / "public" / "js" / "erpnext_enhancements.bundle.js"
HARNESS = REPO_ROOT / "scripts" / "test_breadcrumb_targets.js"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def read(path):
	return path.read_text(encoding="utf-8")


def code(path):
	"""The script with comments stripped: an assertion must not be satisfied by its own prose."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", read(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


class TestShips(unittest.TestCase):
	def test_the_module_is_in_the_global_bundle(self):
		self.assertTrue(MODULE.is_file())
		self.assertIn('import "./global_enhancements/breadcrumb_targets.js";', read(BUNDLE))

	def test_it_wraps_the_form_builder_not_the_deprecated_layer(self):
		"""frappe 16.53 paints a form's trail from frappe.ui.form.get_breadcrumbs (form/form.js);
		frappe.breadcrumbs.* is the compatibility layer marked for removal in v17."""
		src = code(MODULE)
		self.assertIn("const original = frappe.ui.form.get_breadcrumbs;", src)
		self.assertIn("frappe.ui.form.get_breadcrumbs = get_breadcrumbs;", src)
		self.assertNotIn("frappe.breadcrumbs.", src)
		# an older frappe without the function is left alone
		self.assertIn('if (typeof original !== "function" || original.ee_breadcrumb_targets) return;', src)

	def test_the_targets(self):
		src = code(MODULE)
		self.assertIn('href: "/desk/projects"', src)
		self.assertIn('href: "/desk/opportunity/view/kanban/Opportunity"', src)
		self.assertIn('Task: task_crumbs,', src)
		# every link is a /desk/ path, so the Desk routes it in place and Back works
		for href in re.findall(r'href: "([^"]+)"', src):
			self.assertTrue(href.startswith("/desk/"), href)
		self.assertIn('return "/desk/" + slug + "/" + encodeURIComponent(name);', src)

	def test_the_kanban_url_is_the_one_the_crm_sidebar_opens_on(self):
		tweaks = read(APP / "setup" / "workspace_tweaks.py")
		self.assertIn('"url": "/desk/opportunity/view/kanban/Opportunity"', tweaks)


class TestHarness(unittest.TestCase):
	def test_ci_runs_the_harness(self):
		self.assertIn("node scripts/test_breadcrumb_targets.js", read(CI))

	@unittest.skipUnless(shutil.which("node"), "node is not installed")
	def test_the_harness_passes(self):
		result = subprocess.run(
			["node", str(HARNESS)], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120
		)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
		self.assertIn("all checks passed", result.stdout)


if __name__ == "__main__":
	unittest.main()
