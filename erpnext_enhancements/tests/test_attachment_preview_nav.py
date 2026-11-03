"""Previous / next arrows in the form sidebar's attachment preview (v1.780.0, Nik 2026-11-03).

The behaviour is executed, not grepped, in ``scripts/test_attachment_preview_nav.js``: the module
runs under node against a stubbed frappe (``frappe.ui.form.Attachments`` with the four methods it
wraps, ``frappe.ui.button.html``, ``frappe.utils.icon``, ``__``), a minimal DOM, jQuery for document
events and a browser history that queues popstate. That harness holds the order (the rows as the
sidebar draws them), the wrap at both ends, the counter, one file showing no arrows, the keys and
where they are ignored, one history entry per open pane and none per step, the close button and
Escape consuming the entry, popstate closing the pane, the list re-read after an upload or a delete,
and an older frappe left untouched.

This suite holds the seams that harness cannot see: the module ships in the global bundle, the
stylesheet names the classes the script produces, the two icons draw on the deployed release, the
only history write is a two-argument ``pushState`` (a URL would make frappe's router render the form
again), nothing routes away, and CI runs both halves.

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

	def test_it_wraps_only_when_the_four_methods_exist(self):
		code = _code(MODULE)
		for name in ("show_attachment_preview", "hide_attachment_preview", "get_attachments", "get_file_url"):
			self.assertIn(f'"{name}"', code)
		self.assertIn("if (proto.__ee_preview_nav) return;", code)


class TestHistory(unittest.TestCase):
	"""One URL-less entry per open pane. A URL in the push would re-route the form (router.js
	push_state -> route(), formview.show_doc removes the pane)."""

	def test_the_only_history_write_is_a_two_argument_push(self):
		code = _code(MODULE)
		pushes = re.findall(r"history\.pushState\(([^)]*)\)", code)
		self.assertEqual(pushes, ['state, ""'])
		self.assertNotIn("replaceState", code)
		self.assertIn('const STATE_KEY = "ee_attachment_preview";', code)

	def test_the_entry_is_consumed_with_one_back(self):
		code = _code(MODULE)
		self.assertEqual(code.count("history.back()"), 1)
		self.assertNotIn("history.forward", code)
		self.assertNotIn("history.go(", code)

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
class TestTheBehaviourHarness(unittest.TestCase):
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
