# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Recurring billing bills a seasonal agreement only in season (v1.625.0).

Valley Fair Mall's agreement reads "service starts May 1st through September 30th, billed
monthly", Village at Day Dairy's the same. The recurring-billing job billed a Monthly
contract's base fee every month of the year, so both would have been billed through the
winter, and Valley Fair's first period (9 September to 9 October 2026) would have billed a
September before its agreement started on 5 October. Pinned here, through the real module:

* the season's months, wrapping the year end;
* a period bills its base fee when at least half its days are service days (in season and
  on or after the Start Date), which makes five May-to-September invoices whatever day of
  the month the contract bills on;
* a period that is not billed drafts nothing, or a chemicals-only draft when its visits
  used chemicals, and the schedule moves on either way;
* Annually and year-round contracts bill as before;
* the contract refuses half a season.

Stubs a minimal ``frappe`` in ``setUpModule`` and loads its own copy of the billing module:
``test_maintenance_expiry_filters`` replaces ``_bill_period`` on the copy it loads.

Run: python -m unittest erpnext_enhancements.tests.test_maintenance_billing_season
"""

import datetime
import importlib
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BILLING = "erpnext_enhancements.api.maintenance_billing"
CONTRACT_MODULE = (
    "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract"
)
STUBBED = ("frappe", "frappe.utils", "frappe.model", "frappe.model.document", BILLING, CONTRACT_MODULE)

D = datetime.date
STATE = {}
_saved = {}
billing = None
contract_module = None


class _Thrown(Exception):
    pass


class _Row(dict):
    def __getattr__(self, key):
        return self.get(key)


class _Invoice:
    def __init__(self):
        self.items = []
        self.name = None

    def append(self, table, row):
        assert table == "items"
        self.items.append(row)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def set_missing_values(self):
        pass

    def insert(self):
        STATE["invoices"].append(self)
        self.name = f"SINV-{len(STATE['invoices'])}"


class _Contract:
    def __init__(self, **fields):
        self.__dict__.update(fields)

    def get(self, key, default=None):
        return self.__dict__.get(key, default)

    def add_comment(self, kind, text):
        STATE["comments"].append(text)


def _to_date(value=None):
    if value is None:
        return STATE["today"]
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, D):
        return value
    return D.fromisoformat(str(value)[:10])


def _add_months(value, months):
    day = _to_date(value)
    total = day.month - 1 + months
    year, month = day.year + total // 12, total % 12 + 1
    last = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return D(year, month, min(day.day, last))


def _reset():
    STATE.clear()
    STATE.update(
        {
            "today": D(2026, 10, 13),
            "invoices": [],
            "comments": [],
            "set_values": [],
            "records": [],
            "consumables": {},
            "contract": None,
            "item_price": 12.0,
            "fee_item": "MAINT-FEE",
            "annual": 0,
            "errors": [],
        }
    )


def setUpModule():
    global billing, contract_module
    for name in STUBBED:
        _saved[name] = sys.modules.pop(name, None)
    _forget_package_attrs()

    frappe = types.ModuleType("frappe")
    frappe.__dict__["_"] = lambda text, *a, **k: text
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.log_error = lambda *a, **k: STATE["errors"].append(k.get("title"))
    frappe.get_traceback = lambda: ""
    frappe.msgprint = lambda *a, **k: None
    frappe.get_link_to_form = lambda doctype, name: name
    frappe.new_doc = lambda doctype: _Invoice()
    frappe.get_doc = lambda doctype, name: STATE["contract"]
    frappe.get_single = lambda doctype: _Row(maintenance_fee_item=STATE["fee_item"], maintenance_services_group=None)

    def _get_value(doctype, filters, field=None, *a, **k):
        if doctype == "Item Price":
            return STATE["item_price"]
        if doctype == "Project Contract":
            assert field == "annual_maintenance_fee", field
            return STATE["annual"]
        return "Sapphire"

    def _throw(message, *a, **k):
        raise _Thrown(message)

    def _get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
        if doctype == "Sapphire Maintenance Record":
            assert filters["sales_invoice"] == ["is", "not set"], filters
            limit = filters["creation"][1]
            rows = [r for r in STATE["records"] if not r.get("sales_invoice") and r["creation"] < limit]
            return [r["name"] for r in rows]
        if doctype == "Sapphire Maintenance Consumable":
            return [_Row(row) for row in STATE["consumables"].get(filters["parent"], [])]
        raise AssertionError(doctype)

    def _set_value(doctype, name, field, value=None, update_modified=True):
        changes = field if isinstance(field, dict) else {field: value}
        STATE["set_values"].append((doctype, name, changes))
        if doctype == "Sapphire Maintenance Record":
            for record in STATE["records"]:
                if record["name"] == name:
                    record.update(changes)
        if doctype == "Sapphire Maintenance Contract":
            STATE["contract"].__dict__.update(changes)

    frappe.throw = _throw
    frappe.get_all = _get_all
    frappe.db = types.SimpleNamespace(
        get_value=_get_value,
        set_value=_set_value,
        get_single_value=lambda doctype, field: 1,
        savepoint=lambda name: None,
        rollback=lambda save_point=None: None,
    )
    frappe.defaults = types.SimpleNamespace(get_global_default=lambda key: "Sapphire")

    utils = types.ModuleType("frappe.utils")
    utils.getdate = _to_date
    utils.nowdate = lambda: STATE["today"].isoformat()
    utils.add_days = lambda d, n: _to_date(d) + datetime.timedelta(days=n)
    utils.add_months = _add_months
    utils.add_years = lambda d, n: _add_months(d, 12 * n)
    utils.cint = lambda v: int(v or 0)
    utils.flt = lambda v, *a: float(v or 0)
    utils.formatdate = lambda v=None, fmt=None: str(v)
    frappe.utils = utils

    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = _Contract
    model.document = document
    frappe.model = model

    sys.modules.update(
        {"frappe": frappe, "frappe.utils": utils, "frappe.model": model, "frappe.model.document": document}
    )
    billing = importlib.import_module(BILLING)
    contract_module = importlib.import_module(CONTRACT_MODULE)


def _forget_package_attrs():
    import erpnext_enhancements.api as api_package
    import erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract as contract_package

    for owner, attr in ((api_package, "maintenance_billing"), (contract_package, "sapphire_maintenance_contract")):
        if attr in vars(owner):
            delattr(owner, attr)


def tearDownModule():
    _forget_package_attrs()
    for name in STUBBED:
        sys.modules.pop(name, None)
        if _saved.get(name) is not None:
            sys.modules[name] = _saved[name]


def _contract(**fields):
    base = dict(
        name="MNT-VF",
        customer="Wasatch",
        project="PRJ-00183",
        sales_order=None,
        invoicing_frequency="Monthly",
        recurring_amount=7920,
        start_date=D(2026, 10, 5),
        billing_season_start="May",
        billing_season_end="September",
        next_billing_date=D(2027, 6, 9),
        last_billed_on=None,
    )
    base.update(fields)
    STATE["contract"] = _Contract(**base)
    return STATE["contract"]


def _year_of_bills(**fields):
    """Run the job over a year from ``next_billing_date``; return the billed periods' end dates."""
    contract = _contract(**fields)
    billed = []
    for _ in range(12 if contract.invoicing_frequency == "Monthly" else 4):
        end = contract.next_billing_date
        before = len(STATE["invoices"])
        billing._bill_period(contract.name)
        if len(STATE["invoices"]) > before and STATE["invoices"][-1].items[0]["item_code"] == "MAINT-FEE":
            billed.append(end)
    return billed


class TestTheSeason(unittest.TestCase):
    def test_months_from_through_and_wrapping(self):
        self.assertEqual(billing.season_months("May", "September"), {5, 6, 7, 8, 9})
        self.assertEqual(billing.season_months("November", "February"), {11, 12, 1, 2})
        self.assertEqual(billing.season_months("July", "July"), {7})
        self.assertIsNone(billing.season_months(None, None))
        self.assertIsNone(billing.season_months("", ""))

    def test_the_periods_a_year_that_bill(self):
        """An Annual Fee is split over these: a five-month season gets a fifth each time."""
        self.assertEqual(billing.billed_periods_per_year("Monthly", "May", "September"), 5)
        self.assertEqual(billing.billed_periods_per_year("Monthly", None, None), 12)
        self.assertEqual(billing.billed_periods_per_year("Quarterly", "May", "September"), 4)
        self.assertEqual(billing.billed_periods_per_year("Annually", "May", "September"), 1)
        self.assertIsNone(billing.billed_periods_per_year("Per Visit"))


class TestFiveSeasonalInvoices(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_valley_fair_bills_may_through_september_only(self):
        billed = _year_of_bills(next_billing_date=D(2027, 1, 9))
        self.assertEqual(
            billed, [D(2027, 6, 9), D(2027, 7, 9), D(2027, 8, 9), D(2027, 9, 9), D(2027, 10, 9)]
        )

    def test_five_bills_whatever_day_of_the_month_it_bills_on(self):
        """Review of v1.625.0: counting in-season days tied at exactly half on the 16th, and
        billed six. The middle day gives five for every day, the 29th to 31st included."""
        for year in (2027, 2028):
            for day in range(1, 32):
                _reset()
                billed = _year_of_bills(next_billing_date=D(year, 1, day))
                self.assertEqual(len(billed), 5, (year, day, billed))

    def test_nothing_before_the_agreement_starts(self):
        """Valley Fair's first period, 9 September to 9 October 2026, ends after a 5 October start."""
        _contract(next_billing_date=D(2026, 10, 9))
        billing._bill_period("MNT-VF")
        self.assertEqual(STATE["invoices"], [])
        self.assertEqual(STATE["contract"].next_billing_date, D(2026, 11, 9))
        self.assertIsNone(STATE["contract"].last_billed_on)
        self.assertIn("No invoice for the period ending 2026-10-09", STATE["comments"][0])

    def test_a_season_across_the_year_end(self):
        billed = _year_of_bills(
            billing_season_start="November",
            billing_season_end="February",
            start_date=D(2026, 1, 1),
            next_billing_date=D(2027, 1, 1),
        )
        self.assertEqual([d.month for d in billed], [1, 2, 3, 12])

    def test_quarterly_ignores_the_season(self):
        """A quarter bills whole or not at all, so a five-month season would bill once or twice a
        year depending on the start month. Seasons are Monthly only."""
        billed = _year_of_bills(invoicing_frequency="Quarterly", start_date=D(2026, 1, 1), next_billing_date=D(2027, 3, 1))
        self.assertEqual(len(billed), 4)


class TestOutOfSeason(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_chemicals_still_bill_without_the_base_fee(self):
        """Day Dairy's October winterization: chemicals are always extra."""
        _contract(name="MNT-DD", recurring_amount=1330, next_billing_date=D(2026, 11, 5), start_date=D(2026, 5, 1))
        STATE["records"] = [{"name": "V-WINTER", "creation": D(2026, 10, 20), "sales_invoice": None}]
        STATE["consumables"] = {"V-WINTER": [{"item": "ACID", "qty": 2}]}
        billing._bill_period("MNT-DD")
        self.assertEqual(len(STATE["invoices"]), 1)
        invoice = STATE["invoices"][0]
        self.assertEqual([row["item_code"] for row in invoice.items], ["ACID"])
        self.assertEqual(STATE["records"][0]["sales_invoice"], "SINV-1")
        self.assertEqual(STATE["contract"].next_billing_date, D(2026, 12, 5))
        self.assertEqual(STATE["contract"].last_billed_on, D(2026, 11, 5))
        self.assertIn("No base fee for the period ending 2026-11-05", STATE["comments"][0])

    def test_chemicals_with_no_selling_price_draft_nothing(self):
        """A $0 chemicals-only draft is noise; the visit waits for the next invoice."""
        _contract(next_billing_date=D(2026, 12, 9), start_date=D(2026, 5, 1))
        STATE["item_price"] = 0
        STATE["records"] = [{"name": "V-1", "creation": D(2026, 11, 20), "sales_invoice": None}]
        STATE["consumables"] = {"V-1": [{"item": "UNPRICED", "qty": 1}]}
        billing._bill_period("MNT-VF")
        self.assertEqual(STATE["invoices"], [])
        self.assertIsNone(STATE["records"][0]["sales_invoice"])

    def test_out_of_season_needs_no_fee_item(self):
        """Review of v1.625.0: the fee-item check ran first, so a contract with none logged
        "misconfigured" every day off season and never moved on."""
        _contract(next_billing_date=D(2026, 12, 9), start_date=D(2026, 5, 1))
        STATE["fee_item"] = None
        billing._bill_period("MNT-VF")
        self.assertEqual(STATE["errors"], [])
        self.assertEqual(STATE["contract"].next_billing_date, D(2027, 1, 9))

    def test_a_visit_with_no_chemicals_waits_for_the_next_invoice(self):
        _contract(next_billing_date=D(2026, 12, 9), start_date=D(2026, 5, 1))
        STATE["records"] = [{"name": "V-1", "creation": D(2026, 11, 20), "sales_invoice": None}]
        billing._bill_period("MNT-VF")
        self.assertEqual(STATE["invoices"], [])
        self.assertIsNone(STATE["records"][0]["sales_invoice"])
        self.assertEqual(STATE["contract"].next_billing_date, D(2027, 1, 9))

    def test_in_season_bills_the_base_and_the_chemicals(self):
        _contract(next_billing_date=D(2027, 7, 9), start_date=D(2026, 10, 5))
        STATE["records"] = [{"name": "V-2", "creation": D(2027, 6, 20), "sales_invoice": None}]
        STATE["consumables"] = {"V-2": [{"item": "CHLORINE", "qty": 1}]}
        billing._bill_period("MNT-VF")
        self.assertEqual([row["item_code"] for row in STATE["invoices"][0].items], ["MAINT-FEE", "CHLORINE"])
        self.assertEqual(STATE["invoices"][0].items[0]["rate"], 7920)


class TestUnchangedWithoutASeason(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_a_year_round_contract_bills_every_month(self):
        billed = _year_of_bills(
            billing_season_start=None, billing_season_end=None, start_date=D(2026, 10, 5), next_billing_date=D(2026, 11, 5)
        )
        self.assertEqual(len(billed), 12)

    def test_annual_billing_ignores_the_season(self):
        _contract(invoicing_frequency="Annually", next_billing_date=D(2027, 1, 9), start_date=D(2026, 1, 9))
        billing._bill_period("MNT-VF")
        self.assertEqual(STATE["invoices"][0].items[0]["item_code"], "MAINT-FEE")


class TestTheContract(unittest.TestCase):
    def setUp(self):
        _reset()

    def _doc(self, **fields):
        base = dict(
            invoicing_frequency="Monthly",
            billing_season_start=None,
            billing_season_end=None,
            project_contract="SF-MAINT-VF",
            recurring_amount=0,
            next_billing_date=D(2026, 11, 9),
            last_billed_on=None,
            start_date=D(2026, 10, 5),
            _before=None,
        )
        base.update(fields)
        doc = contract_module.SapphireMaintenanceContract(**base)
        doc.get_doc_before_save = lambda: doc._before
        return doc

    def test_half_a_season_is_refused(self):
        with self.assertRaises(_Thrown):
            self._doc(billing_season_start="May")._validate_billing_season()
        self._doc()._validate_billing_season()
        self._doc(billing_season_start="May", billing_season_end="September")._validate_billing_season()

    def test_hidden_fields_are_not_checked(self):
        """The fields show for Monthly only; a leftover half on an Annually contract must not
        stop a save over fields nobody can see."""
        self._doc(invoicing_frequency="Annually", billing_season_start="May")._validate_billing_season()

    def test_an_annual_fee_is_split_over_the_season(self):
        """Review of v1.625.0: $7,920 a year split by twelve, billed five times, is $3,300."""
        STATE["annual"] = 7920
        doc = self._doc(billing_season_start="May", billing_season_end="September")
        doc._recurring_billing_setup()
        self.assertEqual(doc.recurring_amount, 1584)
        doc = self._doc()
        doc._recurring_billing_setup()
        self.assertEqual(doc.recurring_amount, 660)

    def test_setting_a_season_re_derives_an_amount_still_on_the_old_split(self):
        STATE["annual"] = 7920
        before = self._doc()
        doc = self._doc(recurring_amount=660, billing_season_start="May", billing_season_end="September", _before=before)
        doc._recurring_billing_setup()
        self.assertEqual(doc.recurring_amount, 1584)
        typed = self._doc(recurring_amount=7920, billing_season_start="May", billing_season_end="September", _before=before)
        typed._recurring_billing_setup()
        self.assertEqual(typed.recurring_amount, 7920, "an amount typed by hand is kept")

    def test_the_fields_offer_every_month(self):
        import json

        path = (
            REPO_ROOT
            / "erpnext_enhancements/sapphire_maintenance/doctype/sapphire_maintenance_contract/sapphire_maintenance_contract.json"
        )
        fields = {f["fieldname"]: f for f in json.loads(path.read_text(encoding="utf-8"))["fields"]}
        for name in ("billing_season_start", "billing_season_end"):
            self.assertEqual(fields[name]["options"].split("\n")[1:], list(billing.MONTHS))


if __name__ == "__main__":
    unittest.main()
