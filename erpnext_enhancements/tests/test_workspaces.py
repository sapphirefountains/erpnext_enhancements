"""Bench-free structural tests for the app's Workspace JSONs (WI-018).

A Workspace is stored as two halves that nothing keeps in sync: ``content`` is a
JSON-encoded string of layout blocks, and the child tables (``shortcuts``,
``links``, ``custom_blocks``, ``number_cards``) are the data those blocks render.
They are joined **by label**, and disagreeing in either direction fails silently:

* a **block with no row** renders an empty ``<div>`` that still consumes its grid
  columns — this is what shipped in v1.146.0, where "every placement we'd ever
  made wrote content only, so the KPI Cockpit and the six Finance widgets
  rendered as empty divs everywhere";
* a **row with no block** never renders at all, so the record exists, the config
  looks right, and the user sees nothing.

Neither shows up in CI, in a diff review, or in a smoke test — only in someone
noticing a gap on a page. Hence this suite.

Note ``card`` blocks have no child table of their own: a card is a ``Card Break``
row inside ``links``, and the Links after it (until the next Card Break) are its
contents. ``link_count`` on the Card Break restates that number and is only read
when the workspace is edited in the desk, so a wrong value corrupts a later edit
rather than the render — checked here for that reason.

Run: python -m unittest erpnext_enhancements.tests.test_workspaces
"""

import json
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Block type -> (child table, key within the block's `data`, key on the child row)
BLOCK_SOURCES = {
    "shortcut": ("shortcuts", "shortcut_name", "label"),
    "custom_block": ("custom_blocks", "custom_block_name", "custom_block_name"),
    "number_card": ("number_card", "number_card_name", "number_card_name"),
}
# `number_cards` is the real field name; keep the mapping explicit rather than clever.
BLOCK_SOURCES["number_card"] = ("number_cards", "number_card_name", "number_card_name")

# Blocks that carry no child row and need no cross-check.
SELF_CONTAINED = {"header", "spacer", "paragraph", "onboarding"}


def workspace_files():
    return sorted(APP_ROOT.glob("*/workspace/*/*.json"))


def load(path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def blocks_of(doc):
    raw = doc.get("content") or "[]"
    return json.loads(raw)


def card_breaks(doc):
    """Card Break rows in `links`, in order, with the Links that follow each."""
    groups, current = [], None
    for row in doc.get("links") or []:
        if row.get("type") == "Card Break":
            current = (row, [])
            groups.append(current)
        elif current is not None:
            current[1].append(row)
    return groups


class TestWorkspaceStructure(unittest.TestCase):
    def test_files_exist(self):
        self.assertGreater(len(workspace_files()), 0, "no workspace JSONs found")

    def test_content_is_parseable(self):
        for path in workspace_files():
            with self.subTest(path.name):
                blocks = blocks_of(load(path))
                self.assertIsInstance(blocks, list)

    def test_directory_matches_filename(self):
        # get_doc_files() reads exactly <dir>/<dir>.json -- a mismatch is never imported.
        for path in workspace_files():
            with self.subTest(path.name):
                self.assertEqual(path.stem, path.parent.name)

    def test_every_block_has_its_row(self):
        """A block with no matching child row renders an empty div."""
        for path in workspace_files():
            doc = load(path)
            available = {
                block_type: {
                    row.get(row_key)
                    for row in (doc.get(table) or [])
                    if row.get(row_key)
                }
                for block_type, (table, _, row_key) in BLOCK_SOURCES.items()
            }
            available["card"] = {row.get("label") for row, _ in card_breaks(doc)}

            for block in blocks_of(doc):
                btype = block.get("type")
                if btype in SELF_CONTAINED:
                    continue
                with self.subTest(f"{path.name}:{btype}"):
                    self.assertIn(btype, available, f"unknown block type {btype!r}")
                    key = (
                        "card_name"
                        if btype == "card"
                        else BLOCK_SOURCES[btype][1]
                    )
                    name = (block.get("data") or {}).get(key)
                    self.assertIn(
                        name,
                        available[btype],
                        f"{path.name}: content block {btype} {name!r} has no matching row",
                    )

    def test_every_row_is_rendered(self):
        """A child row with no matching block never appears on the page."""
        for path in workspace_files():
            doc = load(path)
            blocks = blocks_of(doc)
            referenced = {}
            for block in blocks:
                btype = block.get("type")
                if btype in SELF_CONTAINED:
                    continue
                key = "card_name" if btype == "card" else BLOCK_SOURCES.get(btype, (None, None, None))[1]
                if not key:
                    continue
                referenced.setdefault(btype, set()).add((block.get("data") or {}).get(key))

            for btype, (table, _, row_key) in BLOCK_SOURCES.items():
                for row in doc.get(table) or []:
                    name = row.get(row_key)
                    with self.subTest(f"{path.name}:{table}:{name}"):
                        self.assertIn(
                            name,
                            referenced.get(btype, set()),
                            f"{path.name}: {table} row {name!r} is never placed in content",
                        )

            for row, _ in card_breaks(doc):
                with self.subTest(f"{path.name}:card:{row.get('label')}"):
                    self.assertIn(
                        row.get("label"),
                        referenced.get("card", set()),
                        f"{path.name}: card {row.get('label')!r} is never placed in content",
                    )

    def test_card_break_link_count_matches(self):
        """link_count is denormalised; a wrong value corrupts the next desk edit."""
        for path in workspace_files():
            for row, following in card_breaks(load(path)):
                with self.subTest(f"{path.name}:{row.get('label')}"):
                    self.assertEqual(
                        row.get("link_count"),
                        len(following),
                        f"{path.name}: card {row.get('label')!r} declares "
                        f"link_count={row.get('link_count')} but carries {len(following)} links",
                    )


class TestFinanceHub(unittest.TestCase):
    """WI-018 specifics. The workspace is hosted in Accounting Intake because
    Workspace.__init__ raises PermissionError when the module is not in the user's
    allow_modules -- which is derived from DocType read permissions, not from
    blocked modules. Accounting Intake is the app module with the most doctypes
    readable by the accounting roles, so the page cannot vanish because one
    unrelated permission row changed."""

    PATH = APP_ROOT / "accounting_intake" / "workspace" / "finance_hub" / "finance_hub.json"

    def setUp(self):
        self.doc = load(self.PATH)

    def test_hosted_in_accounting_intake(self):
        self.assertEqual(self.doc["module"], "Accounting Intake")
        self.assertEqual(self.doc["app"], "erpnext_enhancements")

    def test_module_is_declared(self):
        modules = (APP_ROOT / "modules.txt").read_text(encoding="utf-8").split("\n")
        self.assertIn(self.doc["module"], [m.strip() for m in modules])

    def test_restricted_to_finance_roles(self):
        roles = {r["role"] for r in self.doc["roles"]}
        self.assertEqual(roles, {"Accounts Manager", "Accounts User", "System Manager"})

    def test_custom_cards_suppressed(self):
        # get_links() otherwise auto-appends "Custom Documents"/"Custom Reports".
        self.assertEqual(self.doc["hide_custom"], 1)

    def test_has_the_daily_entry_points(self):
        shortcuts = {s["label"] for s in self.doc["shortcuts"]}
        for expected in (
            "Purchase Invoice",
            "Sales Invoice",
            "Payment Entry",
            "Journal Entry",
            "Bank Reconciliation Tool",
        ):
            self.assertIn(expected, shortcuts)

    def test_reports_are_flagged_as_query_reports(self):
        for row in self.doc["links"]:
            if row.get("link_type") == "Report":
                with self.subTest(row["label"]):
                    self.assertEqual(row.get("is_query_report"), 1)


if __name__ == "__main__":
    unittest.main()
