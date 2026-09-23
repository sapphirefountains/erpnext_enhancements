"""The Stock Scan desk tile and shortcut: three records that fail silently apart (v1.523.0).

Same trap as the Time Kiosk tile (``test_time_kiosk_desktop_icon``): a ``Desktop Icon`` of
type ``Link`` renders only when a same-named ``Workspace Sidebar`` has a visible item —
``get_desktop_icons`` resolves ``bootinfo.workspace_sidebar_item[label.lower()]`` and drops
the tile without a word when that is missing, whatever its ``link_type`` says. So this pins
the seed patch's shape, the shipped sidebar, the artwork entry, and that all of them agree
on one label and one URL — which must be the page's real route. It also pins that the tile
and the shortcut carry the page's own role gate (``stock_scan_rules.SCAN_ROLES``), so the
people who can open the page are exactly the people who see the door to it.

Run: python -m unittest erpnext_enhancements.tests.test_stock_scan_desktop_icon -v
"""

import ast
import json
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.inventory_enhancements import stock_scan_rules as rules
from erpnext_enhancements.setup.desktop_icon_map import TILES, logo_url

PATCH = APP / "patches" / "seed_stock_scan_shortcuts.py"
PATCHES_TXT = APP / "patches.txt"
SIDEBAR = APP / "workspace_sidebar" / "stock_scan.json"
PAGE = APP / "www" / "stock-scan.html"
LABEL = "Stock Scan"


def _text(path):
	return path.read_text(encoding="utf-8")


def _constants(path):
	found = {}
	for node in ast.parse(_text(path)).body:
		if isinstance(node, ast.Assign):
			for target in node.targets:
				if isinstance(target, ast.Name):
					try:
						found[target.id] = ast.literal_eval(node.value)
					except ValueError:
						continue
	return found


def _function(name):
	for node in ast.parse(_text(PATCH)).body:
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return ast.unparse(node)
	raise AssertionError(f"{name} is missing from {PATCH.name}")


class TestThePatch(unittest.TestCase):
	def test_it_points_at_the_real_page(self):
		consts = _constants(PATCH)
		self.assertEqual(consts["LABEL"], LABEL)
		self.assertEqual(consts["LINK"], rules.SCAN_ROUTE)
		self.assertTrue(PAGE.is_file(), "the tile must open a page that exists")

	def test_the_tile_is_an_external_link_tile_with_the_pages_roles(self):
		fn = _function("seed_stock_scan_desktop_icon")
		for needle in (
			"icon.link_type = 'External'",
			"icon.icon_type = 'Link'",
			"icon.standard = 1",
			"icon.link = LINK",
			"icon.append('roles'",
			"_existing_roles()",
			"frappe.db.exists('Desktop Icon', LABEL)",
		):
			self.assertIn(needle, fn)

	def test_the_shortcut_is_a_url_shortcut_with_the_pages_roles(self):
		fn = _function("seed_stock_scan_desk_shortcut")
		for needle in (
			"doc.link_type = 'URL'",
			"doc.url = LINK",
			"doc.visible_to_all = 0",
			"doc.append('roles'",
			"frappe.db.exists('Enhancement Desk Shortcut', LABEL)",
		):
			self.assertIn(needle, fn)

	def test_the_roles_are_the_pages_own_gate(self):
		fn = _function("_existing_roles")
		self.assertIn("SCAN_ROLES", fn)
		self.assertIn("frappe.db.exists('Role'", fn, "a role missing on a site must be skipped, not fail")

	def test_it_never_raises_and_clears_both_caches(self):
		fn = _function("execute")
		self.assertIn("except Exception", fn)
		self.assertIn("frappe.log_error", fn)
		self.assertIn("frappe.cache.delete_key('desktop_icons')", fn)
		self.assertIn("frappe.cache.delete_key('bootinfo')", fn)

	def test_it_is_registered_after_the_model_sync(self):
		post = _text(PATCHES_TXT).split("[post_model_sync]", 1)[1]
		self.assertIn("erpnext_enhancements.patches.seed_stock_scan_shortcuts", post)


class TestTheSidebar(unittest.TestCase):
	"""Without this record the tile is filtered out server-side."""

	def test_it_exists_with_the_tiles_label(self):
		record = json.loads(_text(SIDEBAR))
		self.assertEqual(record["doctype"], "Workspace Sidebar")
		self.assertEqual(record["name"], LABEL)
		self.assertEqual(record["title"], LABEL)
		self.assertEqual(record["app"], "erpnext_enhancements")
		self.assertEqual(record["module"], "Inventory Enhancements")
		self.assertEqual(record.get("standard"), 1)

	def test_its_first_link_opens_the_page(self):
		record = json.loads(_text(SIDEBAR))
		links = [item for item in record["items"] if item.get("type") == "Link"]
		self.assertTrue(links, "a sidebar with no Link item hides the tile")
		self.assertEqual(links[0]["link_type"], "URL")
		self.assertEqual(links[0]["url"], rules.SCAN_ROUTE)
		for item in links:
			self.assertTrue(item.get("url", "").startswith("/"), f"{item['label']} must be site-relative")


class TestTheArtwork(unittest.TestCase):
	def test_the_map_has_the_tile_and_the_svg_is_committed(self):
		self.assertIn(LABEL, TILES)
		slug = TILES[LABEL][0]
		self.assertEqual(slug, "stock_scan")
		self.assertTrue(
			(APP / "public" / "desktop_icons" / f"{slug}.svg").is_file(),
			"run scripts/build_desktop_icons.py and commit the SVG",
		)
		self.assertEqual(logo_url(slug), "/assets/erpnext_enhancements/desktop_icons/stock_scan.svg")


if __name__ == "__main__":
	unittest.main()
