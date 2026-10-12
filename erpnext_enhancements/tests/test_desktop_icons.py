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


class _FakeDB:
	"""The rows ``_sync_display_labels`` reads and writes: tiles, workspaces, the legacy Workspace
	Sidebar and saved layouts. Writes are recorded so a second run can be shown to write nothing."""

	def __init__(self, icons, workspaces, sidebars, layouts):
		self.rows = {"Desktop Icon": icons, "Workspace": workspaces, "Workspace Sidebar": sidebars}
		self.layouts = dict(layouts)
		self.writes = []

	def table_exists(self, doctype):
		return doctype in ("Desktop Layout", "Workspace Sidebar")

	def exists(self, doctype, name=None):
		rows = self.rows.get(doctype, {})
		if isinstance(name, dict):
			other = (name.get("name") or [None, None])[1]
			return any(row.get("label") == name.get("label") and key != other for key, row in rows.items())
		return name in rows

	def get_value(self, doctype, name, field):
		return (self.rows.get(doctype, {}).get(name) or {}).get(field)

	def set_value(self, doctype, name, field, value, update_modified=True):
		self.writes.append((doctype, name, field))
		if doctype == "Desktop Layout":
			self.layouts[name] = value
		else:
			self.rows[doctype][name][field] = value

	def get_all(self, doctype, fields=None, order_by=None, **kwargs):
		assert doctype == "Desktop Layout", doctype
		return [{"name": name, "layout": layout} for name, layout in sorted(self.layouts.items())]


class TestTheMaintenanceTileIsCalledMaintenance(unittest.TestCase):
	"""Nik, 2026-10-12: "can we call it just Maintenance instead of Sapphire Maintenance so people can
	find it on the homepage easier". Only the words change. Every docname and URL stays."""

	@classmethod
	def setUpClass(cls):
		import importlib
		import sys
		import types

		cls._saved = sys.modules.get("frappe")
		stub = types.ModuleType("frappe")
		stub.errors = []
		stub.log_error = lambda *a, **k: stub.errors.append(k.get("title"))
		sys.modules["frappe"] = stub
		sys.modules.pop("erpnext_enhancements.setup.desktop_icons", None)
		cls.icons = importlib.import_module("erpnext_enhancements.setup.desktop_icons")
		cls.frappe = stub

	@classmethod
	def tearDownClass(cls):
		import sys

		sys.modules.pop("erpnext_enhancements.setup.desktop_icons", None)
		if cls._saved is not None:
			sys.modules["frappe"] = cls._saved
		else:
			sys.modules.pop("frappe", None)

	def _site(self, layouts=(), icons=None):
		import json

		layouts = dict(layouts)
		db = _FakeDB(
			icons=icons
			or {
				"Sapphire Maintenance": {"label": "Sapphire Maintenance"},
				"Fleet Maintenance": {"label": "Fleet Maintenance"},
			},
			workspaces={"Sapphire Maintenance": {"module": "Sapphire Maintenance"}},
			sidebars={"Sapphire Maintenance": {"title": "Sapphire Maintenance"}},
			layouts={name: json.dumps(entries) for name, entries in layouts.items()},
		)
		self.frappe.db = db
		self.frappe.get_all = db.get_all
		self.frappe.errors.clear()
		return db

	def test_the_map_names_a_real_tile(self):
		from erpnext_enhancements.setup.desktop_icon_map import DISPLAY_LABELS

		self.assertEqual(DISPLAY_LABELS, {"Sapphire Maintenance": "Maintenance"})
		for name, label in DISPLAY_LABELS.items():
			self.assertIn(name, TILES)
			self.assertNotIn(label, TILES, "a display label may not be another tile's docname")

	def test_the_tile_and_the_sidebar_are_relabelled_and_the_docnames_kept(self):
		db = self._site()
		self.assertTrue(self.icons._sync_display_labels())
		self.assertEqual(db.rows["Desktop Icon"]["Sapphire Maintenance"]["label"], "Maintenance")
		self.assertEqual(db.rows["Workspace Sidebar"]["Sapphire Maintenance"]["title"], "Maintenance")
		self.assertIn("Sapphire Maintenance", db.rows["Desktop Icon"], "the docname must not move")
		self.assertEqual(db.rows["Desktop Icon"]["Fleet Maintenance"]["label"], "Fleet Maintenance")

	def test_a_saved_layout_gets_the_label_and_the_module(self):
		"""ERPNext ships a module called Maintenance. A frozen tile relabelled without its module
		would route through sidebar_for_module("Maintenance") and open ERPNext's pages."""
		import json

		db = self._site(
			layouts={
				"lisa@example.com": [
					{"name": "Sapphire Maintenance", "label": "Sapphire Maintenance", "idx": 3},
					{"name": "Fleet Maintenance", "label": "Fleet Maintenance", "idx": 4},
				]
			}
		)
		self.icons._sync_display_labels()
		entries = json.loads(db.layouts["lisa@example.com"])
		self.assertEqual(
			entries[0],
			{
				"name": "Sapphire Maintenance",
				"label": "Maintenance",
				"idx": 3,
				"module": "Sapphire Maintenance",
			},
		)
		self.assertEqual(entries[1], {"name": "Fleet Maintenance", "label": "Fleet Maintenance", "idx": 4})

	def test_a_layout_without_names_is_matched_on_the_old_label(self):
		import json

		layout = json.dumps([{"label": "Sapphire Maintenance", "idx": 1}])
		out = json.loads(self.icons.relabelled_layout(layout, {"Sapphire Maintenance": "Maintenance"}, {}))
		self.assertEqual(out, [{"label": "Maintenance", "idx": 1}])

	def test_a_second_run_writes_nothing(self):
		db = self._site(
			layouts={"lisa@example.com": [{"name": "Sapphire Maintenance", "label": "Sapphire Maintenance"}]}
		)
		self.icons._sync_display_labels()
		db.writes.clear()
		self.assertFalse(self.icons._sync_display_labels())
		self.assertEqual(db.writes, [])

	def test_a_label_another_tile_holds_is_left_alone_and_logged(self):
		db = self._site(
			icons={
				"Sapphire Maintenance": {"label": "Sapphire Maintenance"},
				"Something Else": {"label": "Maintenance"},
			}
		)
		self.icons._sync_display_labels()
		self.assertEqual(db.rows["Desktop Icon"]["Sapphire Maintenance"]["label"], "Sapphire Maintenance")
		self.assertTrue(self.frappe.errors)

	def test_an_unreadable_layout_is_left_alone(self):
		for layout in ("", "not json", "{}", "[1, 2]"):
			with self.subTest(layout=layout):
				self.assertIsNone(
					self.icons.relabelled_layout(layout, {"Sapphire Maintenance": "Maintenance"}, {})
				)

	def test_it_runs_in_the_sync(self):
		src = (APP_DIR / "setup/desktop_icons.py").read_text(encoding="utf-8")
		self.assertIn("if _sync_display_labels():", src)

	def test_the_workspace_page_is_titled_maintenance_and_keeps_its_name(self):
		import json

		path = APP_DIR / "sapphire_maintenance/workspace/sapphire_maintenance/sapphire_maintenance.json"
		meta = json.loads(path.read_text(encoding="utf-8"))
		self.assertEqual(meta["title"], "Maintenance")
		# The name and label are the docname: renaming them would move /desk/sapphire-maintenance
		# and break every link to it.
		self.assertEqual((meta["name"], meta["label"], meta["module"]), ("Sapphire Maintenance",) * 3)


if __name__ == "__main__":
	unittest.main()
