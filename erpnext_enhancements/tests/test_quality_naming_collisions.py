"""Bench-free structural tests: this app never claims a name ERPNext's Quality already owns.

ERPNext ships a `Quality Management` module, a public Workspace whose **name and label are both
`Quality`**, the Desk tile that goes with it, and 22 DocTypes across `Quality Management` and
`Stock`. This app adds a `Quality` module of its own (WI-075), which puts every one of those
names one typo away.

Why a test rather than a note in the README
-------------------------------------------

Because the failure is silent in all three directions, and two of them are unrecoverable by the
thing you would reach for first.

* **Workspaces are timestamp-gated** by `import_file_by_path`, while DocTypes are hash-gated. A
  second file at ERPNext's `Quality` docname does not conflict — whichever side carries the newer
  `modified` simply rewrites the row on every migrate, **including its `module`**, silently
  re-homing a core workspace into this app and changing which DocPerms gate it. This is the
  Plaid Settings shape, and the verdict the repo already reached on that incident is the reason
  this file exists: *a `modified` bump can never fix a name clash; only distinct names do.*
* **Desk tiles are keyed by workspace label.** `setup/desktop_icons.py` creates a `Desktop Icon`
  named for the label and derives its `roles` from the same-named `Workspace`. A `TILES` key of
  `"Quality"` therefore stamps this app's artwork onto ERPNext's tile and inherits ERPNext's
  roles — with nothing in any log.
* **A Desk Page sharing a workspace's slug never renders**, because `frappe.router` resolves the
  first path segment against workspaces first. It fails per-user, since the workspace list is
  permission-filtered, so it reads like a permissions bug rather than a naming one.

None of the three shows up in CI, in a diff review, or in a smoke test. Hence this suite.

Run: python -m unittest erpnext_enhancements.tests.test_quality_naming_collisions
"""

import json
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Every DocType erpnext owns whose name contains "Quality", across both modules that hold them.
# Verified against `erpnext origin/version-16` and against production on 2026-09-14.
ERPNEXT_QUALITY_DOCTYPES = frozenset(
    {
        # Quality Management
        "Non Conformance",
        "Quality Action",
        "Quality Action Resolution",
        "Quality Feedback",
        "Quality Feedback Parameter",
        "Quality Feedback Template",
        "Quality Feedback Template Parameter",
        "Quality Goal",
        "Quality Goal Objective",
        "Quality Meeting",
        "Quality Meeting Agenda",
        "Quality Meeting Minutes",
        "Quality Procedure",
        "Quality Procedure Process",
        "Quality Review",
        "Quality Review Objective",
        # Stock — the inspection half, which ADR-0012 rejects rather than extends
        "Item Quality Inspection Parameter",
        "Quality Inspection",
        "Quality Inspection Parameter",
        "Quality Inspection Parameter Group",
        "Quality Inspection Reading",
        "Quality Inspection Template",
    }
)

#: Workspace / Desktop Icon names erpnext owns. Ours is `Quality Control`.
ERPNEXT_QUALITY_WORKSPACES = frozenset({"Quality", "Quality Management"})

#: Page routes that would be shadowed by a workspace of ours and so never render.
SHADOWED_PAGE_ROUTES = frozenset({"quality", "quality-control"})


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _corpus(paths, label, minimum):
    """Materialise a glob and refuse to let it be empty.

    Every check below is "no file in this corpus does X", which is vacuously true when the
    corpus is empty — so a moved directory or a changed layout would turn this whole suite
    green while checking nothing. That is the same failure direction as a trailing-space
    query written in SQL: **it passes**, and nobody goes looking. The floor is a blunt
    instrument on purpose; it only has to notice that the glob stopped matching.
    """
    found = list(paths)
    if len(found) < minimum:
        raise AssertionError(
            f"Expected at least {minimum} {label} to scan, found {len(found)}. The glob has "
            "stopped matching, so every check in this suite is now vacuously true."
        )
    return found


class TestNoDocTypeNameCollision(unittest.TestCase):
    def test_no_doctype_json_claims_an_erpnext_quality_name(self):
        """A DocType of ours at a core docname is hash-gated, so it fights core on every migrate."""
        offenders = []
        for path in _corpus(APP_ROOT.glob("*/doctype/*/*.json"), "DocType JSONs", 200):
            if path.stem != path.parent.name:
                continue  # not the DocType definition itself
            data = _load(path)
            if data.get("doctype") != "DocType":
                continue
            if data.get("name") in ERPNEXT_QUALITY_DOCTYPES:
                offenders.append(f"{data['name']} ({path.relative_to(APP_ROOT)})")

        self.assertEqual(
            offenders,
            [],
            "These DocType JSONs claim a name ERPNext already owns. Rename them — core's "
            "Quality DocTypes are reused via Custom Fields and Property Setters, never "
            "redeclared. See ADR-0012.\n  " + "\n  ".join(offenders),
        )


class TestNoWorkspaceNameCollision(unittest.TestCase):
    def test_no_workspace_json_is_named_quality(self):
        """A Workspace's `name` IS its `label`; a second file at core's docname re-homes it."""
        offenders = []
        for path in _corpus(APP_ROOT.glob("*/workspace/*/*.json"), "Workspace JSONs", 30):
            data = _load(path)
            if data.get("doctype") != "Workspace":
                continue
            for key in ("name", "label", "title"):
                if data.get(key) in ERPNEXT_QUALITY_WORKSPACES:
                    offenders.append(f"{path.relative_to(APP_ROOT)}: {key}={data[key]!r}")

        self.assertEqual(
            offenders,
            [],
            "A Workspace here is named/labelled the same as ERPNext's. Workspaces are "
            "TIMESTAMP-gated, so this does not conflict — it rewrites core's row, module and "
            "all, on whichever migrate has the newer stamp.\n  " + "\n  ".join(offenders),
        )

    def test_no_sidebar_is_named_quality(self):
        offenders = []
        for path in _corpus((APP_ROOT / "workspace_sidebar").glob("*.json"), "sidebar JSONs", 8):
            data = _load(path)
            if data.get("doctype") != "Workspace Sidebar":
                continue
            if data.get("name") in ERPNEXT_QUALITY_WORKSPACES:
                offenders.append(f"{path.name}: name={data['name']!r}")

        self.assertEqual(offenders, [], "\n  ".join(offenders))


class TestNoDesktopTileCollision(unittest.TestCase):
    def test_tiles_map_has_no_quality_key(self):
        """Tiles are keyed by workspace LABEL and derive roles from the same-named Workspace."""
        from erpnext_enhancements.setup.desktop_icon_map import TILES

        collisions = sorted(set(TILES) & ERPNEXT_QUALITY_WORKSPACES)
        self.assertEqual(
            collisions,
            [],
            "A TILES key matches an ERPNext workspace label, so this app's artwork would be "
            "stamped onto ERPNext's Desk tile and inherit ERPNext's roles: "
            + ", ".join(collisions),
        )

    def test_quality_control_tile_exists_and_is_unique(self):
        """The tile the Quality module actually ships, and its glyph is not shared."""
        from erpnext_enhancements.setup.desktop_icon_map import TILES

        self.assertIn(
            "Quality Control",
            TILES,
            "The Quality module ships a `Quality Control` workspace; without a TILES entry its "
            "Desk tile falls back to a grey letter avatar.",
        )
        slug, glyph, _colour = TILES["Quality Control"]
        self.assertEqual(slug, "quality_control")

        sharing = sorted(
            label for label, (_s, g, _c) in TILES.items() if g == glyph and label != "Quality Control"
        )
        self.assertEqual(
            sharing,
            [],
            f"Glyph {glyph!r} is also used by {sharing}. Two tiles with one glyph is worse than "
            "either having no tile.",
        )


class TestNoPageShadowsTheQualityWorkspace(unittest.TestCase):
    def test_no_page_uses_a_quality_workspace_slug(self):
        """`frappe.router` checks workspaces before pages, so such a Page never renders."""
        offenders = []
        for path in _corpus(APP_ROOT.glob("*/page/*/*.json"), "Page JSONs", 10):
            if path.stem != path.parent.name:
                continue
            data = _load(path)
            if data.get("doctype") != "Page":
                continue
            if data.get("name") in SHADOWED_PAGE_ROUTES:
                offenders.append(f"{data['name']} ({path.relative_to(APP_ROOT)})")

        self.assertEqual(
            offenders,
            [],
            "This Page shares a slug with a workspace and will never render — and it fails "
            "per-user, because the workspace list is permission-filtered, so it reads like a "
            "permissions bug.\n  " + "\n  ".join(offenders),
        )


class TestQualityModuleIsRegistered(unittest.TestCase):
    def test_quality_is_in_modules_txt(self):
        modules = (APP_ROOT / "modules.txt").read_text(encoding="utf-8").split("\n")
        self.assertIn(
            "Quality",
            [m.strip() for m in modules],
            "`modules.txt` is what makes the module importable by `sync_for`; without it the "
            "whole folder is skipped in silence.",
        )

    def test_quality_module_folder_is_a_package(self):
        self.assertTrue(
            (APP_ROOT / "quality" / "__init__.py").exists(),
            "`sync_for` calls `frappe.get_module()` on every entry in modules.txt and dies if "
            "it is not an importable package.",
        )

    def test_quality_module_has_a_readme(self):
        self.assertTrue((APP_ROOT / "quality" / "README.md").exists())


if __name__ == "__main__":
    unittest.main()
