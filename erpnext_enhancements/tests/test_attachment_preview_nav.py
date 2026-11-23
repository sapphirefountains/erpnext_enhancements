"""Previous / next arrows in the form sidebar's attachment preview (v1.780.0, Nik 2026-11-03).

The behavior is executed, not grepped, in ``scripts/test_attachment_preview_nav.js``: the module
runs under node against a stubbed frappe (``frappe.ui.form.Attachments`` with the five methods it
wraps, ``frappe.ui.button.html``, ``frappe.utils.icon``, ``__``, a ``frappe.router`` whose popstate
listener re-renders the route as frappe's does), a minimal DOM that draws the sidebar's rows, jQuery
for document events and ``:visible``, and a browser history that records every write. That harness
holds the order (the rows as drawn, read from the pills, then the files behind "Show all"), the wrap
at both ends, the counter, one file showing no arrows, the keys and where they are ignored, no
history entry and no popstate listener, the release on a route change, on the user's Back and on a
sidebar rebuild, the list re-read after an upload or a delete, and an older frappe left untouched.

This suite holds the seams that harness cannot see: the module ships in the global bundle, the
stylesheet names the classes the script produces, the two icons draw on the deployed release, the
module never touches ``window.history`` (review round 1: a Desk history entry cannot be made clean,
because frappe's router re-renders the form on every popstate), nothing routes away, and CI runs
both halves.

Bench-free: reads files and runs node, no ``frappe`` stub of its own.

Run: python -m unittest erpnext_enhancements.tests.test_attachment_preview_nav
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
MODULE = APP / "public/js/global_enhancements/attachment_preview_nav.js"
BUNDLE = APP / "public/js/erpnext_enhancements.bundle.js"
CSS = APP / "public/css/desk_enhancements.bundle.css"
ICON_NAMES = APP / "tests/data/desk_icon_names.txt"
HARNESS = REPO / "scripts/test_attachment_preview_nav.js"
CI = REPO / ".github/workflows/ci.yml"
README = APP / "public/README.md"


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	"""The script with comments stripped: an assertion must not be satisfied by its own prose."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


class TestShips(unittest.TestCase):
	def test_the_module_is_in_the_global_bundle(self):
		self.assertTrue(MODULE.is_file())
		self.assertIn('import "./global_enhancements/attachment_preview_nav.js";', _text(BUNDLE))

	def test_it_is_documented_in_the_public_readme(self):
		self.assertIn("attachment_preview_nav.js", _text(README))

	def test_it_wraps_only_when_the_five_methods_and_two_helpers_exist(self):
		code = _code(MODULE)
		for name in (
			"show_attachment_preview",
			"hide_attachment_preview",
			"get_attachments",
			"get_file_url",
			"setup_preview_area",
		):
			self.assertIn(f'"{name}"', code)
		# Both are used to draw the arrows; checked before anything is wrapped.
		self.assertIn("typeof frappe.ui.button.html !== \"function\") return;", code)
		self.assertIn("typeof frappe.utils.escape_html !== \"function\") return;", code)
		self.assertIn("if (proto.__ee_preview_nav) return;", code)


class TestNoHistoryEntry(unittest.TestCase):
	"""The pane takes no history entry on the Desk. frappe's router (router.js:18-22 at 16.53.0)
	calls route() on every popstate with no same-path check, so an entry of our own would have Back
	re-render the whole form (a new Sidebar, "Show all" reset, the dirty form's beforeunload prompt
	gone); capture/panel.js:36-37 refuses a Desk entry for the same reason. Escape and the close
	button close the pane; Back leaves the form, as frappe does."""

	def test_the_module_never_touches_history(self):
		code = _code(MODULE)
		for needle in ("pushState", "replaceState", "history.back", "history.forward", "history.go("):
			with self.subTest(needle=needle):
				self.assertNotIn(needle, code)
		self.assertNotIn("popstate", code)

	def test_a_route_change_releases_the_pane(self):
		code = _code(MODULE)
		self.assertIn('frappe.router.on("change", on_route_change);', code)
		self.assertIn('typeof frappe.router.on !== "function") return;', code)

	def test_nothing_routes_away(self):
		code = _code(MODULE)
		self.assertNotIn("set_route", code)
		self.assertNotIn("window.location", code)
		self.assertIsNone(re.search(r"['\"`]/app/", code))


class TestKeys(unittest.TestCase):
	def test_the_keys_have_their_own_namespace_beside_frappes(self):
		code = _code(MODULE)
		self.assertIn('const KEYS_NS = "keydown.attachment_preview_nav";', code)
		# frappe's own namespace (Escape) is never unbound by us.
		self.assertNotIn('"keydown.attachment_preview"', code)

	def test_open_means_visible(self):
		"""A hidden form page (a list, a report on top) must release the keys: the pane element is
		left in the DOM, so an element-only check would keep every Left and Right on every page."""
		code = _code(MODULE)
		self.assertIn('.is(":visible")', code)


class TestStylesheet(unittest.TestCase):
	def test_the_classes_the_script_produces_are_styled(self):
		css = _text(CSS)
		code = _code(MODULE)
		for const in ("NAV_CLASS", "COUNT_CLASS"):
			name = re.search(rf'const {const} = "([^"]+)";', code).group(1)
			with self.subTest(name=name):
				self.assertIn("." + name, css)

	def test_the_counter_is_small_muted_and_never_wraps(self):
		css = _text(CSS)
		at = css.index(".attachment-preview-count {")
		block = css[at : css.index("}", at)]
		self.assertIn("font-size: 12px;", block)
		self.assertIn("color: var(--text-muted);", block)
		self.assertIn("white-space: nowrap;", block)

	def test_the_group_keeps_frappes_four_pixel_gap(self):
		css = _text(CSS)
		at = css.index(".attachment-preview-nav {")
		block = css[at : css.index("}", at)]
		self.assertIn("gap: 4px;", block)
		self.assertIn("flex-shrink: 0;", block)


class TestIcons(unittest.TestCase):
	def test_both_chevrons_draw_on_the_deployed_release(self):
		names = {line for line in _text(ICON_NAMES).splitlines() if line and not line.startswith("#")}
		code = _code(MODULE)
		icons = re.findall(r'icon: "([^"]+)"', code)
		self.assertEqual(sorted(icons), ["chevron-left", "chevron-right"])
		for icon in icons:
			with self.subTest(icon=icon):
				self.assertIn(icon, names)


class TestCI(unittest.TestCase):
	def test_ci_runs_this_suite_in_its_own_step(self):
		self.assertIn(
			"python -m unittest erpnext_enhancements.tests.test_attachment_preview_nav -v", _text(CI)
		)


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheBehaviorHarness(unittest.TestCase):
	def test_the_harness_passes(self):
		result = subprocess.run(
			[shutil.which("node"), str(HARNESS)], capture_output=True, text=True, timeout=300
		)
		output = (result.stdout + result.stderr)[-6000:]
		self.assertEqual(result.returncode, 0, output)
		self.assertIn("checks passed", result.stdout, output)

	def test_the_module_parses(self):
		result = subprocess.run(
			[shutil.which("node"), "--check", str(MODULE)], capture_output=True, text=True
		)
		self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
	unittest.main()
