# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A chemical sold in sizes is chosen by size on a visit, and every form says which (v1.712.0).

Nik, 2026-10-21: "add option for what size tabits (1" or 3")", and "the tabit size should
reflect across all maintenance forms that have chemicals or items used or added". Each size is
its own Item, tied to the others by the Item's Size Family and Size. Pinned here, through the
real module:

* sizes sort as a person reads them (1" before 3" before 10");
* a name gets its size only when it does not already say it, so a save is a no-op the second
  time and a well-named item is left alone;
* the wizard's choices group a family's items, sized and in order, and leave out a family with
  nothing to choose;
* a visit's chemical rows are named with their size on save, and rows of an unsized item are not;
* nothing reads the two Item fields before they exist (a fresh install, ERPNext's own tests);
* the record, the billing terms, the wizard bootstrap and the fixture are wired to it.

Stubs a minimal ``frappe`` in ``setUpModule``.

Run: python -m unittest erpnext_enhancements.tests.test_item_sizes
"""

import importlib
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
MODULE = "erpnext_enhancements.sapphire_maintenance.item_sizes"
STUBBED = ("frappe", MODULE)
STATE = {}
_saved = {}
item_sizes = None


class _Row(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None

    def __setattr__(self, key, value):
        self[key] = value


def _reset(items=None, has_column=True):
    STATE.clear()
    STATE.update({"items": items or {}, "has_column": has_column, "reads": []})


def setUpModule():
    global item_sizes
    for name in STUBBED:
        _saved[name] = sys.modules.pop(name, None)

    frappe = types.ModuleType("frappe")

    def _get_all(doctype, filters=None, fields=None, **kwargs):
        assert doctype == "Item", doctype
        STATE["reads"].append(fields)
        rows = []
        for name, item in STATE["items"].items():
            if "name" in (filters or {}) and name not in filters["name"][1]:
                continue
            if (filters or {}).get("disabled") == 0 and item.get("disabled"):
                continue
            rows.append(
                _Row(
                    name=name,
                    item_name=item.get("item_name", name),
                    custom_size_family=item.get("family"),
                    custom_size=item.get("size"),
                )
            )
        return rows

    frappe.get_all = _get_all
    frappe.db = types.SimpleNamespace(has_column=lambda doctype, column: STATE["has_column"])
    frappe.log_error = lambda *a, **k: None
    frappe.get_traceback = lambda: ""
    sys.modules["frappe"] = frappe
    item_sizes = importlib.import_module(MODULE)


def tearDownModule():
    for name in STUBBED:
        sys.modules.pop(name, None)
        if _saved.get(name) is not None:
            sys.modules[name] = _saved[name]


TABLETS = {
    "TAB-1": {"item_name": 'CHLORINE TABLETS, 1"', "family": "Chlorine Tablets", "size": '1"'},
    "TAB-3": {"item_name": "CHLORINE TABLETS", "family": "Chlorine Tablets", "size": '3"'},
    "ACID": {"item_name": "ACID, GALLON"},
}


class SizeOrder(unittest.TestCase):
    def test_numbers_sort_as_numbers(self):
        sizes = ['10"', '3"', '1"', "1.5\"", "Large"]
        self.assertEqual(sorted(sizes, key=item_sizes.size_key), ['1"', '1.5"', '3"', '10"', "Large"])


class WithSize(unittest.TestCase):
    def test_adds_the_size_a_name_does_not_say(self):
        self.assertEqual(item_sizes.with_size("CHLORINE TABLETS", '3"'), 'CHLORINE TABLETS (3")')

    def test_leaves_a_name_that_says_it(self):
        self.assertEqual(item_sizes.with_size('CHLORINE TABLETS, 3"', '3"'), 'CHLORINE TABLETS, 3"')
        self.assertEqual(item_sizes.with_size('CHLORINE TABLETS, 3 "', '3"'), 'CHLORINE TABLETS, 3 "')

    def test_twice_is_once(self):
        once = item_sizes.with_size("CHLORINE TABLETS", '3"')
        self.assertEqual(item_sizes.with_size(once, '3"'), once)

    def test_a_size_is_a_token_not_a_substring(self):
        """Review of #1329: 1" is not said by 11" or 1.1", nor 3 IN by 13 IN."""
        self.assertEqual(item_sizes.with_size('NOZZLE, 11"', '1"'), 'NOZZLE, 11" (1")')
        self.assertEqual(item_sizes.with_size('PIPE, 1.1"', '1"'), 'PIPE, 1.1" (1")')
        self.assertEqual(item_sizes.with_size("PIPE, 13 IN", "3 IN"), "PIPE, 13 IN (3 IN)")
        self.assertEqual(item_sizes.with_size('chlorine tablets 3"', '3"'), 'chlorine tablets 3"')
        self.assertEqual(item_sizes.with_size('CHLORINE TABLETS (3")', '3"'), 'CHLORINE TABLETS (3")')

    def test_never_longer_than_the_field(self):
        """Review of #1329: item_name is a 140-character Data field, and a longer value fails the save."""
        name = "CHLORINE TABLETS, " + "X" * 122  # 140 characters, the longest an Item name can be
        labelled = item_sizes.with_size(name, '3"')
        self.assertLessEqual(len(labelled), item_sizes.NAME_LENGTH)
        self.assertTrue(labelled.endswith('… (3")'), labelled)
        self.assertEqual(item_sizes.with_size(labelled, '3"'), labelled, "stable on the next save")
        self.assertEqual(len(item_sizes.with_size("", "Z" * 200)), item_sizes.NAME_LENGTH)
        self.assertLessEqual(len(item_sizes.with_size("A" * 140, "Z" * 150)), item_sizes.NAME_LENGTH)

    def test_no_size_no_change(self):
        self.assertEqual(item_sizes.with_size("ACID, GALLON", ""), "ACID, GALLON")
        self.assertEqual(item_sizes.with_size("ACID, GALLON", None), "ACID, GALLON")


class Choices(unittest.TestCase):
    def test_a_family_in_size_order_named_with_sizes(self):
        _reset(TABLETS)
        self.assertEqual(
            item_sizes.size_choices(),
            {
                "Chlorine Tablets": [
                    {"item": "TAB-1", "item_name": 'CHLORINE TABLETS, 1"', "size": '1"'},
                    {"item": "TAB-3", "item_name": 'CHLORINE TABLETS (3")', "size": '3"'},
                ]
            },
        )

    def test_a_family_of_one_has_nothing_to_choose(self):
        _reset({**TABLETS, "TAB-3": {**TABLETS["TAB-3"], "disabled": 1}})
        self.assertEqual(item_sizes.size_choices(), {})

    def test_a_size_without_a_family_is_not_a_choice(self):
        _reset({"X": {"size": '1"'}, "Y": {"family": "F"}})
        self.assertEqual(item_sizes.size_choices(), {})

    def test_before_the_fields_exist_nothing_is_read(self):
        _reset(TABLETS, has_column=False)
        self.assertEqual(item_sizes.size_choices(), {})
        self.assertEqual(item_sizes.item_sizes(["TAB-1"]), {})
        self.assertEqual(STATE["reads"], [])


class LabelConsumables(unittest.TestCase):
    def _doc(self, rows):
        return _Row(consumables=[_Row(row) for row in rows])

    def test_rows_say_their_size(self):
        _reset(TABLETS)
        doc = self._doc(
            [
                {"item": "TAB-3", "item_name": "CHLORINE TABLETS"},
                {"item": "TAB-1", "item_name": 'CHLORINE TABLETS, 1"'},
                {"item": "ACID", "item_name": "ACID, GALLON"},
                {"item": None, "item_name": ""},
            ]
        )
        item_sizes.label_consumables(doc)
        self.assertEqual(
            [row.item_name for row in doc.consumables],
            ['CHLORINE TABLETS (3")', 'CHLORINE TABLETS, 1"', "ACID, GALLON", ""],
        )
        # One read for the visit, however many rows.
        self.assertEqual(len(STATE["reads"]), 1)

    def test_a_fresh_install_is_left_alone(self):
        _reset(TABLETS, has_column=False)
        doc = self._doc([{"item": "TAB-3", "item_name": "CHLORINE TABLETS"}])
        item_sizes.label_consumables(doc)
        self.assertEqual(doc.consumables[0].item_name, "CHLORINE TABLETS")


class Wiring(unittest.TestCase):
    """Each place a chemical is named goes through the module (read from the source)."""

    def _read(self, *parts):
        return (APP.joinpath(*parts)).read_text(encoding="utf-8")

    def test_the_record_names_rows_on_save(self):
        source = self._read(
            "sapphire_maintenance", "doctype", "sapphire_maintenance_record", "sapphire_maintenance_record.py"
        )
        validate = source.split("def validate(self):", 1)[1].split("\n\tdef ", 1)[0]
        self.assertIn("label_consumables(self)", validate)

    def test_billing_terms_name_the_size(self):
        source = self._read("sapphire_maintenance", "chemical_billing.py")
        terms = source.split("def item_terms(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("with_size(", terms)

    def test_the_wizard_gets_the_choices(self):
        source = self._read("api", "maintenance_visit.py")
        bootstrap = source.split("def get_visit_bootstrap(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"sizes": size_choices()', bootstrap)
        wizard = self._read("sapphire_maintenance", "page", "visit_wizard", "visit_wizard.js")
        self.assertIn("this.sizes = data.sizes || {};", wizard)
        self.assertIn("this.render_size_choice($card, row);", wizard)

    def test_the_portal_and_the_desk_grid_show_the_name(self):
        """Review of #1329: both showed the item code, so the size the name carries never appeared."""
        record = ("sapphire_maintenance", "doctype", "sapphire_maintenance_record")
        portal = self._read(*record, "sapphire_maintenance_record.html")
        consumed = portal.split("{% for item in used_consumables %}", 1)[1].split("{% endfor %}", 1)[0]
        self.assertIn("{{ item.item_name or item.item }}", consumed)
        self.assertNotIn("{{ item.item }}", consumed)
        consumable = ("sapphire_maintenance", "doctype", "sapphire_maintenance_consumable")
        meta = json.loads(self._read(*consumable, "sapphire_maintenance_consumable.json"))
        in_grid = [field["fieldname"] for field in meta["fields"] if field.get("in_list_view")]
        self.assertIn("item_name", in_grid)

    def test_the_item_fields_ship_as_fixtures(self):
        fields = json.loads(self._read("fixtures", "custom_field.json"))
        by_name = {field["name"]: field for field in fields}
        for fieldname in (item_sizes.FAMILY_FIELD, item_sizes.SIZE_FIELD):
            field = by_name.get(f"Item-{fieldname}")
            self.assertIsNotNone(field, fieldname)
            self.assertEqual((field["dt"], field["fieldname"], field["fieldtype"]), ("Item", fieldname, "Data"))
        self.assertEqual(by_name["Item-custom_size_family"]["insert_after"], "custom_price_per_dose")
        self.assertEqual(by_name["Item-custom_size"]["insert_after"], "custom_size_family")


if __name__ == "__main__":
    unittest.main()
