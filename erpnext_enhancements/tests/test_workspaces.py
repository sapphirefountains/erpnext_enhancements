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

# A quick list is joined on its `label`, not on a `*_name` field -- `Block.make()`
# looks the row up with `page_data['quick_lists'].items.find(o => o.label == name)`.
# So two quick lists sharing a label silently render the same one twice.
BLOCK_SOURCES["quick_list"] = ("quick_lists", "quick_list_name", "label")

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
        """`Finance Team` was added in v1.401.0. It is the role people are actually
        granted when they join finance; `Accounts User` is the ERPNext permission that
        happens to come with it. Every current Finance Team holder was checked on prod
        for read access to an Accounting Intake doctype, because this workspace DOES
        carry a module and so is subject to the gate in `Workspace.__init__`."""
        roles = {r["role"] for r in self.doc["roles"]}
        self.assertEqual(
            roles,
            {"Accounts Manager", "Accounts User", "Finance Team", "System Manager"},
        )

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


#: Workspaces that deliberately ship with no `module`. Keep this list SHORT and
#: justified -- see TestModulelessWorkspacesAreDeliberate.
MODULELESS_HUBS = {
    "Executive Hub",
    "HR Hub",
    "Design Hub",
    "Marketing Hub",
    "Sales Hub",
    "Operations Hub",
    "Production Hub",
    "Support Hub",
}

TEAM_ROLE_FOR_HUB = {
    "Executive Hub": "Executive Team",
    "HR Hub": "HR Team",
    "Design Hub": "Design Team",
    "Marketing Hub": "Marketing Team",
    "Sales Hub": "Sales Team",
    "Operations Hub": "Operations Team",
    "Production Hub": "Production Team",
    "Support Hub": "Support Team",
}


class TestModulelessWorkspacesAreDeliberate(unittest.TestCase):
    """A workspace with no `module` is a real choice with real consequences, and
    nothing in the framework flags one. Two consequences worth naming:

    * `Workspace.on_update` calls `export_to_files` only `if self.module`, so on a
      developer bench a Desk edit to one of these is NOT written back to its JSON --
      the repo stops being the source of truth for it silently;
    * in exchange, `remove_orphan_entities` never force-deletes it (it filters on
      module AND app both set), and `is_permitted` skips the module gate entirely,
      which is what a cross-functional hub needs.

    So the answer is not "never" -- it is "only on purpose, and listed here".
    """

    def test_every_workspace_declares_a_real_module_or_is_listed(self):
        modules = {
            m.strip()
            for m in (APP_ROOT / "modules.txt").read_text(encoding="utf-8").splitlines()
            if m.strip()
        }
        offenders = []
        for path in workspace_files():
            doc = load(path)
            name = doc.get("name") or path.stem
            module = doc.get("module")
            if module:
                if module not in modules:
                    offenders.append(f"{name}: module {module!r} is not in modules.txt")
            elif name not in MODULELESS_HUBS:
                offenders.append(f"{name}: no module, and not listed in MODULELESS_HUBS")
        self.assertEqual(offenders, [], "; ".join(offenders))

    def test_the_scan_reaches_the_hubs(self):
        """An allow-list rule over an empty corpus passes forever."""
        names = {(load(p).get("name") or p.stem) for p in workspace_files()}
        self.assertTrue(MODULELESS_HUBS.issubset(names))


class TestTeamHubsAreGated(unittest.TestCase):
    """The hubs exist to be per-role home grids. An ungated one is just another page.

    `roles: []` reads as "public" and is not -- it means "no restriction beyond the
    module gate", and these hubs have no module, so their gate is empty. For them,
    and only for them, an empty roles table means genuinely everybody.
    """

    def _hub(self, name):
        for path in workspace_files():
            doc = load(path)
            if (doc.get("name") or path.stem) == name:
                return doc
        raise AssertionError(f"{name} not found")

    def test_each_hub_is_gated_on_its_team_role(self):
        for name, role in TEAM_ROLE_FOR_HUB.items():
            with self.subTest(hub=name):
                roles = {r["role"] for r in self._hub(name).get("roles") or []}
                self.assertIn(role, roles)
                self.assertIn("System Manager", roles, "somebody must always be able to open it")

    def test_no_hub_renders_empty(self):
        """The defect being fixed. All six carried content "[]" -- a grid with nothing
        in it, which reads as a broken page rather than an unfinished one."""
        for name in TEAM_ROLE_FOR_HUB:
            with self.subTest(hub=name):
                doc = self._hub(name)
                self.assertTrue(json.loads(doc.get("content") or "[]"))
                self.assertTrue(doc.get("links") or doc.get("shortcuts"))

    def test_every_content_block_has_a_backing_row(self):
        """v1.146.0 shipped seven widgets whose blocks had no child rows: each drew an
        empty div that still consumed its grid columns."""
        for name in TEAM_ROLE_FOR_HUB:
            with self.subTest(hub=name):
                doc = self._hub(name)
                cards = {c["label"] for c in doc.get("links") or [] if c.get("type") == "Card Break"}
                shortcuts = {s["label"] for s in doc.get("shortcuts") or []}
                for block in json.loads(doc.get("content") or "[]"):
                    data = block.get("data") or {}
                    if block.get("type") == "card":
                        self.assertIn(data.get("card_name"), cards)
                    elif block.get("type") == "shortcut":
                        self.assertIn(data.get("shortcut_name"), shortcuts)


def hub_files():
    """Only the `* Hub` workspaces. The module workspaces are a different animal."""
    out = []
    for path in workspace_files():
        doc = load(path)
        if (doc.get("name") or path.stem).endswith("Hub"):
            out.append((path, doc))
    return out


class TestHubWidgets(unittest.TestCase):
    """The live widgets added in v1.401.0: count badges and quick lists.

    Both carry a filter expression in a Code field, and both fail *quietly* when it
    is wrong -- a badge that counts nothing reads as a healthy queue, and a quick
    list with a bad filter renders `No Data...`. Neither looks like an error, so
    the encodings are pinned here.
    """

    def test_the_scan_reaches_the_hubs(self):
        self.assertGreaterEqual(len(hub_files()), 9)

    def test_shortcut_labels_are_unique_within_a_workspace(self):
        """The label IS the join key between `content` and the child row. Two
        shortcuts sharing one means the second block renders the first row, and the
        second row never renders at all."""
        for path, doc in hub_files():
            with self.subTest(path.name):
                labels = [s["label"] for s in doc.get("shortcuts") or []]
                self.assertEqual(sorted(labels), sorted(set(labels)))

    def test_quick_list_labels_are_unique_within_a_workspace(self):
        for path, doc in hub_files():
            with self.subTest(path.name):
                labels = [q["label"] for q in doc.get("quick_lists") or []]
                self.assertEqual(sorted(labels), sorted(set(labels)))

    def test_url_shortcuts_carry_a_url_and_no_link_to(self):
        """`link_to` is a Dynamic Link on `type`, and "URL" is not a DocType. An
        empty value is skipped by `base_document.get_invalid_links`; a non-empty one
        sends the importer looking for a doctype that does not exist."""
        for path, doc in hub_files():
            for row in doc.get("shortcuts") or []:
                if row.get("type") != "URL":
                    continue
                with self.subTest(f"{path.name}:{row['label']}"):
                    self.assertTrue(row.get("url"))
                    self.assertIsNone(row.get("link_to"))

    def test_stats_filters_use_the_object_form(self):
        """`process_filter_expression` evals the string and hands the result to
        `frappe.db.count`, which takes either form -- but the live Finance Hub has
        shipped the object form since v1.146.0 and mixing the two in one app makes
        the next person guess."""
        seen = 0
        for path, doc in hub_files():
            for row in doc.get("shortcuts") or []:
                raw = row.get("stats_filter")
                if not raw:
                    continue
                seen += 1
                with self.subTest(f"{path.name}:{row['label']}"):
                    self.assertIsInstance(json.loads(raw), dict)
        self.assertGreater(seen, 0)

    def test_quick_list_filters_use_the_array_form(self):
        """The other half of the asymmetry, and the one with teeth. The quick list's
        own edit dialog calls `FilterGroup.add_filters_to_filter_group`, which reads
        4-tuples only. An object renders perfectly and then breaks the dialog the
        first time somebody clicks the filter icon."""
        seen = 0
        for path, doc in hub_files():
            for row in doc.get("quick_lists") or []:
                raw = row.get("quick_list_filter")
                if not raw:
                    continue
                seen += 1
                with self.subTest(f"{path.name}:{row['label']}"):
                    parsed = json.loads(raw)
                    self.assertIsInstance(parsed, list)
                    for clause in parsed:
                        self.assertIsInstance(clause, list)
                        self.assertEqual(len(clause), 4)
        self.assertGreater(seen, 0)

    def test_quick_list_filters_name_their_own_doctype(self):
        """A 4-tuple leads with the doctype, so a copied clause keeps pointing at the
        one it was copied from. `reportview.get` then filters Quotations on a column
        that belongs to Opportunity and returns nothing, forever, with no error."""
        for path, doc in hub_files():
            for row in doc.get("quick_lists") or []:
                raw = row.get("quick_list_filter")
                if not raw:
                    continue
                for clause in json.loads(raw):
                    with self.subTest(f"{path.name}:{row['label']}:{clause[1]}"):
                        self.assertEqual(clause[0], row["document_type"])

    def test_report_links_are_flagged_and_doctype_links_are_not(self):
        """`is_query_report` decides the route. A Report link without it routes to
        the Report Builder for a `ref_doctype` that a Script Report may not even
        have."""
        for path, doc in hub_files():
            for row in doc.get("links") or []:
                if row.get("type") != "Link":
                    continue
                with self.subTest(f"{path.name}:{row['label']}"):
                    self.assertEqual(
                        row.get("is_query_report"),
                        1 if row.get("link_type") == "Report" else 0,
                    )

if __name__ == "__main__":
    unittest.main()
