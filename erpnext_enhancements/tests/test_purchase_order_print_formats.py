"""Which Purchase Order print formats get deleted, and which only get disabled.

TASK-2026-01237 asked to purge every PO print format except
``Purchase Order - Sapphire``. Five had to go and **they do not go the same way**:

* ``Test Purchase Order Format`` and ``PO Test Print Format`` are custom
  (``standard = "No"``). Deleting them is real and permanent.
* ``Purchase Order Standard``, ``Purchase Order with Item Image`` and
  ``Drop Shipping Format`` ship with ERPNext. **Standard formats re-sync from
  their app's JSON on migrate**, which is the same fact that forced
  ``ensure_chrome_pdf_generator`` to be an every-migrate hook rather than the
  one-off data fix somebody tried first. A patch that deleted them would appear
  to work and undo itself at the next ``bench migrate`` — the worst shape of
  failure, because nobody looks again until they print a PO weeks later.

So the split is the design, and this pins it. Getting it backwards is invisible
until production.

Bench-free: reads the sources as text.

Run: python -m unittest erpnext_enhancements.tests.test_purchase_order_print_formats
"""

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
SETUP = APP / "enhancements_core/setup_print_formats.py"
PATCH = APP / "patches/purge_purchase_order_print_formats.py"
PATCHES_TXT = APP / "patches.txt"
HOOKS = APP / "hooks.py"

KEEP = "Purchase Order - Sapphire"
DELETABLE = ("Test Purchase Order Format", "PO Test Print Format")
STANDARD = ("Purchase Order Standard", "Purchase Order with Item Image", "Drop Shipping Format")


def literal(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path.name}")


class TestTheSplitIsRight(unittest.TestCase):
    def test_only_the_custom_formats_are_deleted(self):
        self.assertEqual(set(literal(PATCH, "DOOMED")), set(DELETABLE))

    def test_no_standard_format_is_deleted(self):
        """The one that undoes itself on the next migrate."""
        doomed = set(literal(PATCH, "DOOMED"))
        overlap = sorted(doomed & set(STANDARD))
        self.assertEqual(
            overlap,
            [],
            f"these ship with ERPNext and re-sync on migrate; disable them instead: {overlap}",
        )

    def test_the_standard_formats_are_disabled_every_migrate(self):
        self.assertEqual(
            set(literal(SETUP, "SUPERSEDED_PURCHASE_ORDER_FORMATS")), set(STANDARD)
        )

    def test_the_kept_format_is_in_neither_list(self):
        """The one format we actually print."""
        self.assertNotIn(KEEP, literal(PATCH, "DOOMED"))
        self.assertNotIn(KEEP, literal(SETUP, "SUPERSEDED_PURCHASE_ORDER_FORMATS"))

    def test_the_kept_format_is_still_created(self):
        self.assertIn(f'PURCHASE_ORDER_FORMAT = "{KEEP}"', SETUP.read_text(encoding="utf-8"))


class TestItIsWiredUp(unittest.TestCase):
    def test_the_patch_is_registered(self):
        self.assertIn(
            "erpnext_enhancements.patches.purge_purchase_order_print_formats",
            PATCHES_TXT.read_text(encoding="utf-8"),
        )

    def test_the_disable_pass_runs_after_migrate(self):
        """Not a patch. A patch runs once; these come back."""
        self.assertIn(
            "setup_print_formats.disable_superseded_print_formats",
            HOOKS.read_text(encoding="utf-8"),
        )

    def test_the_disable_pass_uses_the_low_level_write(self):
        """`Print Format.validate` throws "Standard Print Format cannot be updated",
        so the ORM cannot touch these at all."""
        source = SETUP.read_text(encoding="utf-8")
        start = source.index("def disable_superseded_print_formats(")
        body = source[start : start + 1400]
        self.assertIn("frappe.db.set_value", body)
        self.assertNotIn(".save(", body)


class TestThePatchIsDefensive(unittest.TestCase):
    def test_it_checks_for_references_before_deleting(self):
        source = PATCH.read_text(encoding="utf-8")
        self.assertIn("def _references(", source)
        self.assertIn("frappe.log_error", source)

    def test_it_bails_rather_than_orphaning_a_link(self):
        source = PATCH.read_text(encoding="utf-8")
        start = source.index("if referenced:")
        self.assertIn("continue", source[start : start + 400])

    def test_it_tolerates_a_missing_doctype_or_column(self):
        """A doctype from an app that is not installed, or a field added after this
        list was written, must not turn a cleanup patch into a failed migrate."""
        source = PATCH.read_text(encoding="utf-8")
        self.assertIn('frappe.db.exists("DocType", doctype)', source)
        self.assertIn("frappe.db.has_column(doctype, fieldname)", source)

    def test_property_setters_are_checked_too(self):
        """They name their target in `value`, not in a Link column, so a
        column-driven sweep misses them."""
        self.assertIn('"Property Setter", filters={"value": name}', PATCH.read_text(encoding="utf-8"))


class TestTheChromeGuardClosedItsLoop(unittest.TestCase):
    """`CHROME_EXCLUDED_FORMATS` existed for exactly one format, and its comment
    said the guard stays "until either the format is deleted or upstream bounds
    that index". This patch is that deletion."""

    def test_the_excluded_set_is_now_empty(self):
        self.assertEqual(literal(SETUP, "CHROME_EXCLUDED_FORMATS"), set())

    def test_the_mechanism_is_kept(self):
        """The upstream bug is still there — a header shorter than its body still
        raises IndexError — so the hook keeps its escape hatch."""
        source = SETUP.read_text(encoding="utf-8")
        self.assertIn("CHROME_EXCLUDED_FORMATS", source)
        self.assertIn("if name in CHROME_EXCLUDED_FORMATS:", source)


if __name__ == "__main__":
    unittest.main()
