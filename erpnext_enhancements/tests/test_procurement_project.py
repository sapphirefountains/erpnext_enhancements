"""Bench-free unit tests for the PO header -> line Project cascade (WI-014).

Stubs a minimal ``frappe`` (no site/bench) so the pure fill logic in
``erpnext_enhancements.procurement_project`` runs under plain unittest. The stub
is installed in ``setUpModule`` (execution time), not at import, so it never
fools the bench-only suites' ``import frappe`` skip-guards.

Run: python -m unittest erpnext_enhancements.tests.test_procurement_project
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

procurement_project = None


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")
    frappe.whitelist = lambda *a, **kw: (lambda fn: fn)
    frappe.get_all = lambda *a, **kw: []
    sys.modules["frappe"] = frappe


def setUpModule():
    global procurement_project
    _install_frappe_stub()
    sys.modules.pop("erpnext_enhancements.procurement_project", None)
    from erpnext_enhancements import procurement_project as mod

    procurement_project = mod


class _Row(dict):
    """Minimal child-row stand-in: dict .get() plus assignable .project."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class _Doc(_Row):
    """Minimal Purchase Order stand-in."""


def _po(project=None, line_projects=()):
    return _Doc(project=project, items=[_Row(project=p) for p in line_projects])


def _line_projects(doc):
    return [row.get("project") for row in doc["items"]]


class TestCascadeProjectToItems(unittest.TestCase):
    def test_blank_lines_inherit_header(self):
        doc = _po(project="PRJ-00567", line_projects=[None, "", "   "])
        procurement_project.cascade_project_to_items(doc)
        self.assertEqual(_line_projects(doc), ["PRJ-00567"] * 3)

    def test_existing_line_projects_are_never_overwritten(self):
        # A PO legitimately spanning two jobs keeps its per-line attribution.
        doc = _po(project="PRJ-00567", line_projects=["PRJ-00473", None])
        procurement_project.cascade_project_to_items(doc)
        self.assertEqual(_line_projects(doc), ["PRJ-00473", "PRJ-00567"])

    def test_blank_header_leaves_lines_alone(self):
        # Nothing to cascade — the mandatory error still fires, correctly.
        doc = _po(project=None, line_projects=[None, "PRJ-00473"])
        procurement_project.cascade_project_to_items(doc)
        self.assertEqual(_line_projects(doc), [None, "PRJ-00473"])

    def test_whitespace_only_header_is_not_a_project(self):
        doc = _po(project="   ", line_projects=[None])
        procurement_project.cascade_project_to_items(doc)
        self.assertEqual(_line_projects(doc), [None])

    def test_header_is_stripped_before_it_is_copied_down(self):
        doc = _po(project="  PRJ-00567  ", line_projects=[None])
        procurement_project.cascade_project_to_items(doc)
        self.assertEqual(_line_projects(doc), ["PRJ-00567"])

    def test_overhead_bucket_cascades_like_any_other_project(self):
        # The office-supplies case: one pick on the header covers every line.
        doc = _po(project="PRJ-00745", line_projects=[None, None, None])
        procurement_project.cascade_project_to_items(doc)
        self.assertEqual(_line_projects(doc), ["PRJ-00745"] * 3)

    def test_no_items_is_a_no_op(self):
        doc = _Doc(project="PRJ-00567", items=None)
        procurement_project.cascade_project_to_items(doc)  # no raise

    def test_method_argument_is_accepted(self):
        # doc_events pass the hook name positionally.
        doc = _po(project="PRJ-00567", line_projects=[None])
        procurement_project.cascade_project_to_items(doc, "before_validate")
        self.assertEqual(_line_projects(doc), ["PRJ-00567"])


class TestOverheadProjectType(unittest.TestCase):
    def test_type_is_distinct_from_internal(self):
        # Internal is the 13 R&D jobs (IDP000-IDP016); overhead must stay separable.
        self.assertEqual(procurement_project.OVERHEAD_PROJECT_TYPE, "Overhead")
        self.assertNotEqual(procurement_project.OVERHEAD_PROJECT_TYPE, "Internal")

    def test_seed_patch_uses_the_same_constant(self):
        from erpnext_enhancements.patches import seed_overhead_projects

        self.assertEqual(
            seed_overhead_projects.OVERHEAD_PROJECT_TYPE,
            procurement_project.OVERHEAD_PROJECT_TYPE,
        )
        self.assertEqual(len(seed_overhead_projects.OVERHEAD_PROJECTS), 5)
        for project_name, notes in seed_overhead_projects.OVERHEAD_PROJECTS:
            self.assertTrue(project_name.startswith("Overhead - "), project_name)
            self.assertTrue(notes.strip())


if __name__ == "__main__":
    unittest.main()
