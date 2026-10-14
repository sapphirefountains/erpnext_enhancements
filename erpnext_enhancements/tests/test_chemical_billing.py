# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A visit's chemicals bill per dose, as a whole container, or not at all, as chosen (v1.628.0).

Visits record chemicals as doses ("1 cap full"), and both invoice builders billed that many of
the item at its selling price: a bottle per cap. Nik, 2026-10-13: it is "entirely situational",
some products must be billed whole once opened because they cure. Pinned here, through the
real module:

* a line per chemical, per dose at the item's Price per Dose or whole at its selling price, and
  nothing for Don't Bill; a missing price says so on the line;
* a row's own choice over its item's default, and the default over nothing;
* the reviewer's choices are checked against the visit's own rows;
* the Visit Review page sends them, the approval writes them before the workflow reloads the
  visit, and both invoice builders bill through this module;
* the Item fields and the row's options agree with the module.

Stubs a minimal ``frappe`` in ``setUpModule``.

Run: python -m unittest erpnext_enhancements.tests.test_chemical_billing
"""

import importlib
import json
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
MODULE = "erpnext_enhancements.sapphire_maintenance.chemical_billing"
STUBBED = ("frappe", "frappe.utils", MODULE)
STATE = {}
_saved = {}
chemical_billing = None


class _Thrown(Exception):
    pass


class _Row(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None


def _reset(**items):
    STATE.clear()
    STATE.update({"items": items, "prices": [], "has_column": True})


def setUpModule():
    global chemical_billing
    for name in STUBBED:
        _saved[name] = sys.modules.pop(name, None)
    _forget_package_attr()

    frappe = types.ModuleType("frappe")
    frappe.__dict__["_"] = lambda text, *a, **k: text

    def _throw(message, *a, **k):
        raise _Thrown(message)

    def _get_all(doctype, filters=None, fields=None, order_by=None, **kwargs):
        if doctype == "Item":
            rows = []
            for name in filters["name"][1]:
                if name in STATE["items"]:
                    row = _Row(
                        name=name,
                        item_name=STATE["items"][name].get("item_name", name),
                        stock_uom=STATE["items"][name].get("stock_uom", "Bottle"),
                    )
                    if "custom_maintenance_billing" in fields:
                        row["custom_maintenance_billing"] = STATE["items"][name].get("bill")
                        row["custom_price_per_dose"] = STATE["items"][name].get("per_dose")
                    rows.append(row)
            return rows
        if doctype == "Item Price":
            # Newest by creation, as frappe.db.get_value picked it before v1.628.0.
            assert filters["price_list"] == "Standard Selling" and order_by == "creation asc", (filters, order_by)
            return [_Row(item_code=c, price_list_rate=r) for c, r in STATE["prices"] if c in filters["item_code"][1]]
        raise AssertionError(doctype)

    frappe.throw = _throw
    frappe.get_all = _get_all
    frappe.parse_json = json.loads
    frappe.db = types.SimpleNamespace(has_column=lambda doctype, column: STATE["has_column"])
    utils = types.ModuleType("frappe.utils")
    utils.flt = lambda v, precision=None: round(float(v or 0), precision) if precision else float(v or 0)
    utils.fmt_money = lambda v, *a, **k: f"${float(v or 0):.2f}"
    frappe.utils = utils
    sys.modules.update({"frappe": frappe, "frappe.utils": utils})
    chemical_billing = importlib.import_module(MODULE)


def _forget_package_attr():
    import erpnext_enhancements.sapphire_maintenance as package

    if "chemical_billing" in vars(package):
        delattr(package, "chemical_billing")


def tearDownModule():
    _forget_package_attr()
    for name in STUBBED:
        sys.modules.pop(name, None)
        if _saved.get(name) is not None:
            sys.modules[name] = _saved[name]


def _row(name, item, qty, uom="cap full", bill_as=None, containers=None):
    return _Row(name=name, item=item, qty=qty, uom=uom, bill_as=bill_as, containers=containers)


class TestLines(unittest.TestCase):
    def setUp(self):
        _reset(
            ACID={"item_name": "Muriatic Acid", "bill": "Per Dose", "per_dose": 4},
            EPOXY={"item_name": "Epoxy Patch", "bill": "Whole Container", "per_dose": 0},
            BARE={"item_name": "Tablets"},
            CHLORINE={"item_name": "Liquid Chlorine", "stock_uom": "Gallon"},
        )
        STATE["prices"] = [("ACID", 30), ("EPOXY", 55), ("EPOXY", 60), ("CHLORINE", 9)]

    def lines(self, *rows, visit="PRJ-1-V001"):
        return chemical_billing.invoice_lines(list(rows), visit=visit)

    def test_per_dose_is_one_line_at_the_doses_times_the_price(self):
        (line,) = self.lines(_row("r1", "ACID", 2))
        self.assertEqual((line["item_code"], line["qty"], line["rate"]), ("ACID", 1, 8.0))
        self.assertEqual(line["description"], "Muriatic Acid: 2 cap full on PRJ-1-V001 at $4.00 per dose")

    def test_whole_container_bills_the_containers_at_the_newest_selling_price(self):
        (line,) = self.lines(_row("r1", "EPOXY", 0.5, uom="tube"))
        self.assertEqual((line["qty"], line["rate"]), (1, 60.0))
        self.assertEqual(line["description"], "Epoxy Patch: whole container (0.5 tube used on PRJ-1-V001)")
        (line,) = self.lines(_row("r1", "EPOXY", 3, bill_as="Whole Container", containers=2))
        self.assertEqual(line["qty"], 2)

    def test_dont_bill_and_nothing_used_bill_nothing(self):
        self.assertEqual(self.lines(_row("r1", "ACID", 2, bill_as="Don't Bill"), _row("r2", "ACID", 0)), [])

    def test_the_row_choice_beats_the_item_default(self):
        (line,) = self.lines(_row("r1", "ACID", 2, bill_as="Whole Container"))
        self.assertEqual((line["qty"], line["rate"]), (1, 30.0))
        (line,) = self.lines(_row("r1", "EPOXY", 2, bill_as="Per Dose"))
        self.assertIn("(no Price per Dose on the item)", line["description"])

    def test_an_item_without_a_default_or_a_price_says_so(self):
        (line,) = self.lines(_row("r1", "BARE", 3, uom="tablet"))
        self.assertEqual(line["rate"], 0)
        self.assertEqual(line["description"], "Tablets: 3 tablet on PRJ-1-V001 at $0.00 per dose (no Price per Dose on the item)")

    def test_every_line_names_its_own_list_price(self):
        """Review of v1.628.0: without price_list_rate ERPNext fills the list price and
        calculate_item_rate replaces a rate of 0 with it, so an unpriced dose billed a bottle."""
        rows = [_row("r1", "ACID", 2), _row("r2", "BARE", 1), _row("r3", "EPOXY", 1)]
        for line in self.lines(*rows):
            self.assertIn("price_list_rate", line)
            self.assertEqual(line["price_list_rate"], line["rate"], line)

    def test_a_dose_in_the_items_own_unit_bills_at_its_selling_price(self):
        """Gallons of a gallon item billed qty x price before v1.628.0, and still do."""
        (line,) = self.lines(_row("r1", "CHLORINE", 1.5, uom="gallon"))
        self.assertEqual((line["qty"], line["rate"]), (1, 13.5))
        self.assertEqual(line["description"], "Liquid Chlorine: 1.5 gallon on PRJ-1-V001 at $9.00 per dose")

    def test_the_timeline_says_how_each_was_billed(self):
        rows = [_row("r1", "ACID", 2), _row("r2", "EPOXY", 1, uom="tube"), _row("r3", "BARE", 1)]
        chosen = {"r1": ("Per Dose", 0), "r2": ("Whole Container", 2.0), "r3": ("Don't Bill", 0)}
        self.assertEqual(
            chemical_billing.describe_choices(rows, chosen),
            "Chemicals billed as: Muriatic Acid: per dose (2 cap full); Epoxy Patch: whole container x2; "
            "Tablets: not billed.",
        )
        self.assertEqual(chemical_billing.describe_choices(rows, {}), "")

    def test_before_the_item_fields_exist_everything_is_per_dose(self):
        STATE["has_column"] = False
        (line,) = self.lines(_row("r1", "EPOXY", 1))
        self.assertIn("per dose", line["description"])


class TestTheReviewerChoices(unittest.TestCase):
    def setUp(self):
        _reset(ACID={"item_name": "Muriatic Acid", "bill": "Whole Container", "per_dose": 4})
        STATE["prices"] = [("ACID", 30)]
        self.rows = [_row("r1", "ACID", 2), _row("r2", "ACID", 0)]

    def test_the_page_gets_what_was_used_and_its_default(self):
        doc = _Row(consumables=self.rows)
        self.assertEqual(
            chemical_billing.review_rows(doc),
            [
                {
                    "row": "r1",
                    "item_name": "Muriatic Acid",
                    "dose": "2 cap full",
                    "bill_as": "Whole Container",
                    "containers": 1,
                    "price_per_dose": 4.0,
                    "container_price": 30.0,
                }
            ],
        )

    def test_choices_are_checked_against_the_visit(self):
        chosen = chemical_billing.checked_choices(
            self.rows, json.dumps([{"row": "r1", "bill_as": "Whole Container", "containers": 2}])
        )
        self.assertEqual(chosen, {"r1": ("Whole Container", 2.0)})
        self.assertEqual(chemical_billing.checked_choices(self.rows, [{"row": "r1", "bill_as": "Per Dose"}]), {"r1": ("Per Dose", 0)})
        self.assertEqual(chemical_billing.checked_choices(self.rows, None), {})
        self.assertEqual(chemical_billing.checked_choices(self.rows, ""), {})
        for bad in (
            [{"row": "OTHER-VISIT", "bill_as": "Per Dose"}],
            [{"row": "r1", "bill_as": "Free"}],
            [{"row": "r1", "bill_as": "Whole Container", "containers": 0}],
        ):
            with self.assertRaises(_Thrown):
                chemical_billing.checked_choices(self.rows, bad)


class TestTheWiring(unittest.TestCase):
    @staticmethod
    def read(path):
        return (APP / path).read_text(encoding="utf-8")

    def test_both_invoice_builders_bill_through_the_module(self):
        self.assertIn("chemical_billing.invoice_lines(rows, visit=record)", self.read("api/maintenance_billing.py"))
        self.assertIn('"bill_as", "containers"', self.read("api/maintenance_billing.py"))
        self.assertIn("invoice_lines(doc.consumables, visit=doc.name)", self.read("api/maintenance_workflow.py"))
        # The old pricing (qty of the item at its selling price) is gone from both.
        for path in ("api/maintenance_billing.py", "api/maintenance_workflow.py"):
            self.assertNotIn('"qty": item.qty', self.read(path))
            self.assertNotIn('"qty": flt(row.qty)', self.read(path))

    def test_the_approval_writes_the_choices_before_the_workflow_reloads(self):
        source = self.read("api/maintenance_review.py")
        body = source[source.index("def approve_visit(") : source.index("def send_back_visit(")]
        written = body.index("chemical_billing.checked_choices(")
        self.assertLess(written, body.index("apply_workflow(doc"))
        self.assertIn('"Sapphire Maintenance Consumable"', body)
        self.assertIn("update_modified=False", body)
        self.assertIn('"chemicals": chemical_billing.review_rows(doc)', source)

    def test_the_page_sends_them_and_escapes_what_it_shows(self):
        source = self.read("sapphire_maintenance/page/visit_review/visit_review.js")
        self.assertIn("chemicals: JSON.stringify(this.chemical_choices(data))", source)
        self.assertIn("html += this.chemicals_html(data);", source)
        body = source[source.index("\tchemicals_html(data) {") : source.index("\tchemical_choices(data) {")]
        for match in re.finditer(r"\$\{([^}]*)\}", body):
            expression = match.group(1).strip()
            if expression.startswith("options"):
                # The option list: each value through esc(), each label one of the __() labels above.
                self.assertIn("${esc(value)", expression)
                continue
            self.assertRegex(
                expression,
                r"^(esc\(|money\(|__\(|label\(c\)|text$|i$|cell$|value === c\.bill_as \?|c\.bill_as === )",
                expression,
            )

    def test_the_fields_and_the_module_agree(self):
        records = {r["name"]: r for r in json.loads(self.read("fixtures/custom_field.json"))}
        section = records["Item-custom_maintenance_billing_section"]
        default = records["Item-custom_maintenance_billing"]
        price = records["Item-custom_price_per_dose"]
        self.assertEqual(section["insert_after"], "custom_sds_reviewed_on")
        self.assertEqual(default["insert_after"], section["fieldname"])
        self.assertEqual(price["insert_after"], default["fieldname"])
        self.assertEqual(tuple(default["options"].split("\n")), chemical_billing.ITEM_DEFAULTS)
        self.assertEqual(default["default"], chemical_billing.DEFAULT)
        consumable = json.loads(
            self.read("sapphire_maintenance/doctype/sapphire_maintenance_consumable/sapphire_maintenance_consumable.json")
        )
        bill_as = next(f for f in consumable["fields"] if f["fieldname"] == "bill_as")
        self.assertEqual(tuple(bill_as["options"].split("\n")[1:]), chemical_billing.BILL_AS)


if __name__ == "__main__":
    unittest.main()
