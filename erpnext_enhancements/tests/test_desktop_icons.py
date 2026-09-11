"""Bench-free test: the committed Desk tile artwork still matches the tile map.

``setup/desktop_icon_map.TILES`` is read by three things that never meet -- the
generator (``scripts/build_desktop_icons.py``), the ``after_migrate`` reconciler
(``setup/desktop_icons.py``) and the browser. This asserts they cannot drift:

  * every tile in the map has an SVG on disk, and every SVG on disk is in the map
  * every SVG parses, is a 28x28 tile, and carries the family colour the map declares
  * every glyph group actually contains shapes -- an empty ``<g>`` is a tile that
	renders as a bare coloured square, which no diff makes obvious
  * slugs are ``frappe.scrub(label)``, because ``logo_url`` is built from the slug and
	a mismatch means a 404 that only shows up as a missing icon in production

A missing or misnamed file does not error anywhere: ``<img src=...>`` 404s and the
grid shows an empty square. That silence is the reason this test exists rather than
a comment. The one thing it cannot check is whether a glyph *suits* its module --
regenerate and look at the SVGs for that.

Pure filesystem + xml, no frappe/bench needed.

Run: python -m unittest erpnext_enhancements.tests.test_desktop_icons
"""

import ast
import unittest
from pathlib import Path
from xml.etree import ElementTree

from erpnext_enhancements.setup.desktop_icon_map import ASSET_SUBDIR, TILES, logo_url

APP_DIR = Path(__file__).resolve().parents[1]  # the erpnext_enhancements/ package
ICON_DIR = APP_DIR / ASSET_SUBDIR
SVG_NS = "{http://www.w3.org/2000/svg}"


def _scrub(name):
	"""Mirror frappe.scrub: spaces and hyphens to underscores, lowercased."""
	return name.replace(" ", "_").replace("-", "_").lower()


class TestDesktopIconArtwork(unittest.TestCase):
	def test_map_is_not_empty(self):
		# A refactor that empties the map would make every other test here vacuous.
		self.assertGreaterEqual(len(TILES), 30, "tile map looks truncated")

	def test_slugs_are_unique(self):
		slugs = [slug for slug, _glyph, _colour in TILES.values()]
		self.assertEqual(len(slugs), len(set(slugs)), "two tiles share one slug")

	def test_slug_matches_scrubbed_label(self):
		for label, (slug, _glyph, _colour) in TILES.items():
			with self.subTest(label=label):
				self.assertEqual(slug, _scrub(label))

	def test_every_tile_has_artwork(self):
		for label, (slug, _glyph, _colour) in TILES.items():
			with self.subTest(label=label):
				self.assertTrue(
					(ICON_DIR / f"{slug}.svg").is_file(),
					f"missing {slug}.svg -- run scripts/build_desktop_icons.py",
				)

	def test_no_orphaned_artwork(self):
		expected = {f"{slug}.svg" for slug, _glyph, _colour in TILES.values()}
		found = {p.name for p in ICON_DIR.glob("*.svg")}
		self.assertEqual(
			found - expected,
			set(),
			"artwork on disk for a tile no longer in the map",
		)

	def test_artwork_is_a_well_formed_28px_tile(self):
		for label, (slug, _glyph, colour) in TILES.items():
			with self.subTest(label=label):
				root = ElementTree.parse(ICON_DIR / f"{slug}.svg").getroot()
				self.assertEqual(root.get("viewBox"), "0 0 28 28")

				# The squircle carries the family colour declared in the map.
				square = root.find(f"{SVG_NS}path")
				self.assertIsNotNone(square, "no background path")
				self.assertEqual(square.get("fill"), colour)

				# The glyph group must actually draw something.
				group = root.find(f"{SVG_NS}g")
				self.assertIsNotNone(group, "no glyph group")
				self.assertGreater(len(list(group)), 0, "glyph group is empty")
				self.assertEqual(group.get("stroke"), "#fff")

	def test_logo_url_points_at_a_file_that_exists(self):
		for label, (slug, _glyph, _colour) in TILES.items():
			with self.subTest(label=label):
				url = logo_url(slug)
				self.assertTrue(url.startswith("/assets/erpnext_enhancements/"))
				# `<app>/public` is served as `/assets/<app>`, so the tail of the URL
				# is the path under public/ -- that correspondence is the whole
				# contract between the reconciler and the committed artwork.
				tail = url.split("/assets/erpnext_enhancements/", 1)[1]
				self.assertTrue((APP_DIR / "public" / tail).is_file(), url)


class TestTileRolesAreDerivedNotKept(unittest.TestCase):
	"""WI-074. `Desktop Icon.roles` and `Workspace.roles` are two separate gates in
	v16, and keeping them as two hand-maintained lists guarantees silent drift in
	both directions: a tile that opens a refusal, or a page nobody can find.

	So the tile's roles are derived from the workspace's at every migrate, and the
	decision is recorded once -- in the workspace JSON that ships in this repo.
	"""

	SETUP = Path(__file__).resolve().parents[1] / "setup/desktop_icons.py"

	def _src(self):
		return self.SETUP.read_text(encoding="utf-8")

	def test_the_reconciler_exists_and_runs_in_the_sync(self):
		src = self._src()
		self.assertIn("def _sync_roles(", src)
		self.assertIn("if _sync_roles():", src)

	def test_it_reads_the_workspace_as_the_source_of_truth(self):
		src = self._src()
		at = src.index("def _sync_roles(")
		body = src[at : at + 2600]
		self.assertIn('"parenttype": "Workspace"', body)
		self.assertIn('"parenttype": "Desktop Icon"', body)

	def test_it_never_calls_get_desktop_icons(self):
		"""Without `bootinfo` that returns [] and CACHES the empty list per user,
		which would blank the home grid for everybody until the cache expired.

		Asserted on the parsed CALL nodes, not on the text: the docstring that warns
		against this call necessarily names it, and a substring check matches the
		warning. Sixth time that has bitten in this repo -- so stop text-matching.
		"""
		tree = ast.parse(self._src())
		called = {
			node.func.id if isinstance(node.func, ast.Name) else node.func.attr
			for node in ast.walk(tree)
			if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
		}
		self.assertNotIn("get_desktop_icons", called)

	def test_the_map_carries_no_roles(self):
		"""If roles appeared in TILES they would be a second list to keep in step,
		which is the thing this design exists to avoid. TILES stays (slug, glyph,
		colour)."""
		for label, value in TILES.items():
			with self.subTest(tile=label):
				self.assertEqual(len(value), 3)

	def test_every_team_hub_has_a_tile(self):
		for hub in (
			"HR Hub",
			"Design Hub",
			"Marketing Hub",
			"Sales Hub",
			"Operations Hub",
			"Production Hub",
			"Support Hub",
		):
			with self.subTest(hub=hub):
				self.assertIn(hub, TILES)


if __name__ == "__main__":
	unittest.main()
