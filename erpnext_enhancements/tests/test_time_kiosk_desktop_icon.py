"""The Time Kiosk desk tile is two records that fail silently apart (v1.482.0).

A ``Desktop Icon`` of type ``Link`` only renders when a same-named ``Workspace Sidebar``
has visible items — ``get_desktop_icons`` resolves ``bootinfo.workspace_sidebar_item[label
.lower()]`` and drops the tile without a word when that is missing, whatever its
``link_type`` says. And the tile only goes anywhere because ``link_type = "External"``
carries a ``link``. So this pins the seed patch's shape, the shipped sidebar's shape, the
artwork entry in the tile map, and that all three agree on the one label.

Run: python -m unittest erpnext_enhancements.tests.test_time_kiosk_desktop_icon
"""

import ast
import json
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PATCH = APP / "patches" / "seed_time_kiosk_desktop_icon.py"
PATCHES_TXT = APP / "patches.txt"
SIDEBAR = APP / "workspace_sidebar" / "time_kiosk.json"
ICON_MAP = APP / "setup" / "desktop_icon_map.py"
ARTWORK = APP / "public" / "desktop_icons" / "time_kiosk.svg"

LABEL = "Time Kiosk"
LINK = "/kiosk"


def _text(path):
    return path.read_text(encoding="utf-8")


def _constants(path, names):
    tree = ast.parse(_text(path))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    found[target.id] = ast.literal_eval(node.value)
    return found


class TestThePatch(unittest.TestCase):
    def test_it_seeds_an_external_link_tile_to_the_kiosk(self):
        consts = _constants(PATCH, {"LABEL", "LINK", "AFTER"})
        self.assertEqual(consts["LABEL"], LABEL)
        self.assertEqual(consts["LINK"], LINK)
        self.assertEqual(consts["AFTER"], "Workforce")
        source = _text(PATCH)
        self.assertIn('icon.link_type = "External"', source)
        self.assertIn('icon.icon_type = "Link"', source)
        self.assertIn("icon.standard = 1", source)
        self.assertNotIn("roles", source.split("def seed_time_kiosk_desktop_icon")[1],
                         "no roles: every signed-in user should see the clock")

    def test_it_is_insert_only_and_clears_both_caches(self):
        source = _text(PATCH)
        self.assertIn('frappe.db.exists("Desktop Icon", LABEL)', source)
        self.assertIn('frappe.cache.delete_key("desktop_icons")', source)
        self.assertIn('frappe.cache.delete_key("bootinfo")', source)

    def test_it_is_registered_after_the_model_sync(self):
        post = _text(PATCHES_TXT).split("[post_model_sync]", 1)[1]
        self.assertIn("erpnext_enhancements.patches.seed_time_kiosk_desktop_icon", post)


class TestTheSidebar(unittest.TestCase):
    """Without this record the tile is filtered out server-side."""

    def test_it_exists_with_the_tiles_label(self):
        record = json.loads(_text(SIDEBAR))
        self.assertEqual(record["doctype"], "Workspace Sidebar")
        self.assertEqual(record["title"], LABEL)
        self.assertEqual(record["name"], LABEL)
        self.assertEqual(record["app"], "erpnext_enhancements")
        self.assertEqual(record.get("standard"), 1)

    def test_it_has_a_visible_link_item_to_the_kiosk(self):
        record = json.loads(_text(SIDEBAR))
        links = [i for i in record["items"] if i.get("type") == "Link"]
        self.assertTrue(links, "a sidebar with no Link item hides the tile")
        first = links[0]
        self.assertEqual(first["link_type"], "URL")
        self.assertEqual(first["url"], LINK)

    def test_it_is_pure_json_in_an_app_level_sync_folder(self):
        # A non-JSON file in workspace_sidebar/ took prod's migrate down once
        # (docs/workspace-sidebars.md). Nothing but .json may live there.
        for path in SIDEBAR.parent.iterdir():
            self.assertEqual(path.suffix, ".json", f"{path.name} is not JSON")


class TestTheArtwork(unittest.TestCase):
    def test_the_map_has_the_tile_and_the_svg_is_committed(self):
        source = _text(ICON_MAP)
        self.assertIn('"Time Kiosk": ("time_kiosk", "timer", FIELD)', source)
        self.assertTrue(ARTWORK.is_file(), "run scripts/build_desktop_icons.py and commit the SVG")


if __name__ == "__main__":
    unittest.main()
