"""The UOM cleanup patches: rename, disable, default.

Three patches, one story, and the ordering between them is load-bearing — the
rename has to land before the disable pass, or that pass sees ``Gallon`` unused
and switches off a UOM three live documents point at.

The assertions worth reading are the ones about what the patches must *not* do:

* the rename is an **exact match**. Eight other seeded UOMs contain the word
  "Gallon", and ``Pound/Gallon (UK)`` / ``Ounce/Gallon (UK)`` / ``Grain/Gallon
  (UK)`` are *densities*. A substring rename would turn them into
  ``Pound/Gallon`` and merge two different units in a system that prices by them.
* the disable pass **ignores ``UOM Conversion Factor``** when deciding what is in
  use — it is ERPNext's seeded matrix and names nearly every UOM, so counting it
  keeps all 240 — but must still never disable something business data points at.
* the default is set through ``.save()``, not ``db.set_value``, because only
  ``Stock Settings.on_update`` writes the ``tabDefaultValue`` row that new Items
  actually read.

Bench-free: reads the patch sources.

Run: python -m unittest erpnext_enhancements.tests.test_uom_cleanup
"""

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
PATCHES = APP / "patches"
PATCHES_TXT = APP / "patches.txt"

RENAME = PATCHES / "rename_gallon_uk_uom.py"
DISABLE = PATCHES / "disable_unused_uoms.py"
DEFAULT = PATCHES / "set_default_stock_uom_unit.py"

# Seeded UOMs that contain "Gallon" but are NOT the one being renamed. The four
# with a slash are densities.
NOT_THE_TARGET = (
    "Gallon Dry (US)",
    "Gallon Liquid (US)",
    "Pound/Gallon (UK)",
    "Pound/Gallon (US)",
    "Ounce/Gallon (UK)",
    "Ounce/Gallon (US)",
    "Grain/Gallon (UK)",
    "Grain/Gallon (US)",
)


def literal(path, name):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path.name}")


def order_in_patches_txt():
    lines = [
        line.strip()
        for line in PATCHES_TXT.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("erpnext_enhancements.patches.")
    ]
    return lines


class TestTheRenameIsExact(unittest.TestCase):
    def test_it_renames_the_right_record(self):
        self.assertEqual(literal(RENAME, "OLD"), "Gallon (UK)")
        self.assertEqual(literal(RENAME, "NEW"), "Gallon")

    def test_it_names_no_other_gallon_uom(self):
        """A substring rename would merge densities into volumes."""
        source = RENAME.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        # Drop the module docstring, which lists them precisely to explain why.
        code = code.split('"""', 2)[-1]
        for name in NOT_THE_TARGET:
            self.assertNotIn(f'"{name}"', code, f"the rename patch references {name}")

    def test_it_uses_rename_doc_for_the_cascade(self):
        """Hand-updating the columns would miss whichever one is added next."""
        self.assertIn("frappe.rename_doc(", RENAME.read_text(encoding="utf-8"))

    def test_it_moves_the_autoname_field_too(self):
        """UOM is `autoname: field:uom_name`; leaving the field behind renames the
        record back on the next save."""
        self.assertIn('"uom_name"', RENAME.read_text(encoding="utf-8"))

    def test_it_refuses_to_merge(self):
        source = RENAME.read_text(encoding="utf-8")
        self.assertIn('frappe.db.exists("UOM", NEW)', source)
        self.assertIn("frappe.log_error", source)

    def test_it_is_idempotent(self):
        self.assertIn('frappe.db.exists("UOM", OLD)', RENAME.read_text(encoding="utf-8"))


class TestTheDisablePassIsSafe(unittest.TestCase):
    def test_it_disables_rather_than_deletes(self):
        source = DISABLE.read_text(encoding="utf-8")
        self.assertIn('"enabled", 0', source)
        self.assertNotIn("frappe.delete_doc", source)

    def test_it_ignores_the_seeded_conversion_matrix(self):
        """235 of the 240 are named by UOM Conversion Factor; counting it as usage
        keeps everything and makes the patch a no-op."""
        self.assertEqual(
            literal(DISABLE, "SEEDED_TABLES"), {"tabUOM", "tabUOM Conversion Factor"}
        )

    def test_it_discovers_columns_at_runtime(self):
        """119 UOM-bearing columns today; a hard-coded list is wrong after the next
        app install."""
        self.assertIn("INFORMATION_SCHEMA.COLUMNS", DISABLE.read_text(encoding="utf-8"))

    def test_it_keeps_anything_in_use(self):
        source = DISABLE.read_text(encoding="utf-8")
        self.assertIn("keep = _in_use()", source)
        self.assertIn("if name in keep:", source)

    def test_it_never_disables_the_site_default(self):
        """A default the picker refuses to offer is worse than a long picker."""
        self.assertIn('get_single_value("Stock Settings", "stock_uom")', DISABLE.read_text(encoding="utf-8"))

    def test_it_has_a_floor(self):
        self.assertTrue(literal(DISABLE, "ALWAYS_KEEP"))

    def test_a_vanishing_table_cannot_fail_the_migrate(self):
        source = DISABLE.read_text(encoding="utf-8")
        self.assertIn("except Exception:", source)
        self.assertIn("continue", source)


class TestTheDefaultGoesThroughTheController(unittest.TestCase):
    def test_it_saves_rather_than_setting_the_value(self):
        """Item.stock_uom has no default of its own — it reads tabDefaultValue, and
        only Stock Settings.on_update writes that. A db.set_value would leave the
        settings page reading "Unit" while new Items defaulted to the old value."""
        source = DEFAULT.read_text(encoding="utf-8")
        self.assertIn("settings.save(", source)
        self.assertNotIn('frappe.db.set_value("Stock Settings"', source)

    def test_it_checks_the_uom_exists_first(self):
        self.assertIn('frappe.db.exists("UOM", DEFAULT_UOM)', DEFAULT.read_text(encoding="utf-8"))

    def test_it_is_idempotent(self):
        self.assertIn('settings.get("stock_uom") == DEFAULT_UOM', DEFAULT.read_text(encoding="utf-8"))

    def test_the_default_is_unit(self):
        self.assertEqual(literal(DEFAULT, "DEFAULT_UOM"), "Unit")


class TestTheOrderIsRight(unittest.TestCase):
    def test_all_three_are_registered(self):
        registered = order_in_patches_txt()
        for name in (
            "erpnext_enhancements.patches.rename_gallon_uk_uom",
            "erpnext_enhancements.patches.disable_unused_uoms",
            "erpnext_enhancements.patches.set_default_stock_uom_unit",
        ):
            self.assertIn(name, registered)

    def test_the_rename_runs_before_the_disable(self):
        """Otherwise the disable pass sees `Gallon` unused and switches off a UOM
        three live documents point at."""
        registered = order_in_patches_txt()
        self.assertLess(
            registered.index("erpnext_enhancements.patches.rename_gallon_uk_uom"),
            registered.index("erpnext_enhancements.patches.disable_unused_uoms"),
        )

    def test_the_default_is_set_after_the_disable(self):
        """So the floor in the disable pass and the default agree about `Unit`."""
        registered = order_in_patches_txt()
        self.assertLess(
            registered.index("erpnext_enhancements.patches.disable_unused_uoms"),
            registered.index("erpnext_enhancements.patches.set_default_stock_uom_unit"),
        )


if __name__ == "__main__":
    unittest.main()
