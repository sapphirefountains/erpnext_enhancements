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


if __name__ == "__main__":
	unittest.main()
