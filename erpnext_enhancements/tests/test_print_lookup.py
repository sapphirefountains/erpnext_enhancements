"""Bench-free tests for `print_lookup.py` — the facts a print format cannot find for
itself: who a document is for, and what its taxes table really holds.

Both halves fail silently. A party block that reads the wrong field prints a customer's
name with no phone and no email, and nothing logs, because an empty value is not an
error. A taxes table printed whole puts QuickBooks billable-expense lines ("25% markup
for ...") under Subtotal looking like tax. And a lookup that asks for a column the site
does not have raises inside a Jinja global, which is a blank page at print time.

So this suite pins, against production-shaped records:

* each value walks the document -> its Contact -> its Address -> the party record, and
  the first non-blank value wins; the app's own custom fields (``custom_email``,
  ``custom_mobile_number``, ``Customer.custom_accounts_*``, ``Supplier.custom_*``) are
  read, because ERPNext copies only the stock ones onto a document;
* a custom field the site's meta does not have is never requested — the stub raises
  exactly as MariaDB would, and the requested columns are asserted too;
* any failure falls back to what the document itself carries, and logs;
* the Attn line is only ever the contact the document names;
* an RFQ prints one block per supplier, or just ``vendor``'s when ERPNext sets it;
* the taxes table splits into charges and tax as a partition of its non-zero rows.

The stub is installed in ``setUpModule`` and every ``sys.modules`` entry it replaced
is put back in ``tearDownModule``, so it does not leak into a suite that shares the
process. Own CI step all the same, as for every suite with its own frappe stub.

Run: python -m unittest erpnext_enhancements.tests.test_print_lookup -v
"""

import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

HOOKS = (REPO_ROOT / "erpnext_enhancements" / "hooks.py").read_text(encoding="utf-8")

_STUBBED = (
    "frappe",
    "frappe.contacts",
    "frappe.contacts.doctype",
    "frappe.contacts.doctype.address",
    "frappe.contacts.doctype.address.address",
    "erpnext_enhancements.print_lookup",
)
_SAVED = {}

pl = None  # erpnext_enhancements.print_lookup, imported under the stub
ps = None  # erpnext_enhancements.print_style


class StubUnknownColumn(Exception):
    """What MariaDB raises for a column the table does not have (1054)."""


class StubDBError(Exception):
    """A read that fails for any other reason."""


class _dict(dict):
    """frappe._dict: a dict with attribute access."""

    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value


# ---------------------------------------------------------------------------
# The site. Every field below exists on production (the custom ones are in
# fixtures/custom_field.json); a test that wants a fresh site removes them from META.

FULL_META = {
    "Customer": {
        "customer_name", "customer_primary_address", "custom_billing_address", "primary_address",
        "customer_primary_contact", "custom_accounts_phone_number", "mobile_no",
        "custom_accounts_email_address", "email_id",
    },
    "Supplier": {
        "supplier_name", "supplier_primary_address", "primary_address", "supplier_primary_contact",
        "custom_phone_number", "mobile_no", "custom_email", "email_id",
    },
    "Lead": {"company_name", "lead_name", "mobile_no", "phone", "email_id"},
    "Contact": {
        "custom_mobile_number", "custom_phone_number", "mobile_no", "phone", "custom_email", "email_id",
        "full_name", "first_name", "last_name",
    },
    "Address": {"phone", "email_id"},
}

# The United States Address Template, as rendered: every address ends `<br>\n`.
US_ADDRESS = "{line}<br>\n{city}, UT {zip}<br>\n"

STATE = {}


def _reset():
    STATE["meta"] = {dt: set(fields) for dt, fields in FULL_META.items()}
    STATE["meta_raises"] = set()
    STATE["raise_on"] = set()
    STATE["calls"] = []
    STATE["rendered"] = []
    STATE["errors"] = []
    STATE["records"] = {
        "Customer": {
            "Canyon Ridge HOA": {
                "customer_name": "Canyon Ridge HOA",
                "customer_primary_address": None,
                "custom_billing_address": None,
                "primary_address": None,
                "customer_primary_contact": None,
                "custom_accounts_phone_number": "8015550142",
                "mobile_no": None,
                "custom_accounts_email_address": "ap@canyonridge.test",
                "email_id": None,
            },
        },
        "Supplier": {
            "Wasatch Pool Supply": {
                "supplier_name": "Wasatch Pool Supply",
                "supplier_primary_address": None,
                "primary_address": None,
                "supplier_primary_contact": None,
                "custom_phone_number": "801-555-0177",
                "mobile_no": None,
                "custom_email": "orders@wasatchpool.test",
                "email_id": None,
            },
            "Desert Pump Co": {
                "supplier_name": "Desert Pump Co.",
                "supplier_primary_address": "Desert Pump Co-Billing",
                "primary_address": None,
                "supplier_primary_contact": "Lee Park-Desert Pump Co",
                "custom_phone_number": None,
                "mobile_no": None,
                "custom_email": "sales@desertpump.test",
                "email_id": None,
            },
        },
        "Lead": {
            "CRM-LEAD-2026-00012": {
                "company_name": "Riverside Plaza",
                "lead_name": "Morgan Lee",
                "mobile_no": "4355550123",
                "phone": None,
                "email_id": "morgan@riversideplaza.test",
            },
        },
        "Contact": {
            "Dana Whitaker-Canyon Ridge HOA": {
                "full_name": "Dana Whitaker",
                "first_name": "Dana",
                "last_name": "Whitaker",
                "custom_mobile_number": "8015550199",
                "custom_phone_number": "801-555-0101",
                "mobile_no": "8015550000",
                "phone": "8015550001",
                "custom_email": "dana@canyonridge.test",
                "email_id": "dana.old@canyonridge.test",
            },
            "Sam Ortiz-Wasatch Pool Supply": {
                "full_name": None,
                "first_name": "Sam",
                "last_name": "Ortiz",
                "custom_mobile_number": None,
                "custom_phone_number": "801-555-0188",
                "mobile_no": None,
                "phone": None,
                "custom_email": "sam@wasatchpool.test",
                "email_id": None,
            },
            "Lee Park-Desert Pump Co": {
                "full_name": "Lee Park",
                "first_name": "Lee",
                "last_name": "Park",
                "custom_mobile_number": None,
                "custom_phone_number": None,
                "mobile_no": None,
                "phone": None,
                "custom_email": None,
                "email_id": "lee@desertpump.test",
            },
        },
        "Address": {
            "Canyon Ridge HOA-Billing": {
                "phone": "801-555-0150",
                "email_id": None,
                "_html": US_ADDRESS.format(line="1450 E Canyon Rd", city="Salt Lake City", zip="84108"),
            },
            "Wasatch Pool Supply-Billing": {
                "phone": "(801) 555-0160",
                "email_id": "billing@wasatchpool.test",
                "_html": US_ADDRESS.format(line="2200 S State St", city="South Salt Lake", zip="84115"),
            },
            "Desert Pump Co-Billing": {
                "phone": None,
                "email_id": None,
                "_html": US_ADDRESS.format(line="95 N Main St", city="St. George", zip="84770"),
            },
        },
        "Account": {
            "Cost of Goods Sold - SF": {"account_type": "Cost of Goods Sold"},
            "Sales - SF": {"account_type": "Income Account"},
            "Freight and Delivery - SF": {"account_type": "Chargeable"},
            "UT SPECIAL - SF": {"account_type": "Tax"},
            "Utah Sales Tax - Inactive - SF": {"account_type": "Tax"},
        },
        "Company": {
            "Sapphire Fountains": {"abbr": "SF"},
        },
    }


def _install_stub():
    frappe = types.ModuleType("frappe")

    def get_meta(doctype):
        if doctype in STATE["meta_raises"]:
            raise StubDBError(f"no meta for {doctype}")
        known = STATE["meta"].get(doctype, set())
        return types.SimpleNamespace(has_field=lambda f: f in known)

    class _DB:
        def get_value(self, doctype, name, fields, as_dict=False):
            fields = list(fields) if isinstance(fields, (list, tuple)) else [fields]
            STATE["calls"].append((doctype, name, tuple(fields)))
            if doctype in STATE["raise_on"]:
                raise StubDBError(f"{doctype} read failed")
            missing = [f for f in fields if f not in STATE["meta"].get(doctype, set())]
            if missing:
                # What frappe.db.get_value does on a column the table lacks.
                raise StubUnknownColumn(f"(1054, \"Unknown column '{missing[0]}' in 'SELECT'\")")
            row = STATE["records"].get(doctype, {}).get(name)
            if row is None:
                return None
            values = _dict({f: row.get(f) for f in fields})
            return values if as_dict else [values[f] for f in fields]

        def exists(self, doctype, name):
            return name in STATE["records"].get(doctype, {})

    def get_cached_value(doctype, name, field):
        if doctype in STATE["raise_on"]:
            raise StubDBError(f"{doctype} read failed")
        return (STATE["records"].get(doctype, {}).get(name) or {}).get(field)

    def log_error(*args, **kwargs):
        STATE["errors"].append((args, kwargs))

    frappe.get_meta = get_meta
    frappe.db = _DB()
    frappe.get_cached_value = get_cached_value
    frappe.log_error = log_error
    frappe.get_traceback = lambda *a, **kw: "Traceback (most recent call last):\nStubDBError: boom"
    frappe._dict = _dict

    def render_address(address, check_permissions=True):
        STATE["rendered"].append((address, check_permissions))
        return (STATE["records"]["Address"].get(address) or {}).get("_html")

    contacts = types.ModuleType("frappe.contacts")
    doctype_pkg = types.ModuleType("frappe.contacts.doctype")
    address_pkg = types.ModuleType("frappe.contacts.doctype.address")
    address_mod = types.ModuleType("frappe.contacts.doctype.address.address")
    address_mod.render_address = render_address
    frappe.contacts = contacts
    contacts.doctype = doctype_pkg
    doctype_pkg.address = address_pkg
    address_pkg.address = address_mod

    sys.modules["frappe"] = frappe
    sys.modules["frappe.contacts"] = contacts
    sys.modules["frappe.contacts.doctype"] = doctype_pkg
    sys.modules["frappe.contacts.doctype.address"] = address_pkg
    sys.modules["frappe.contacts.doctype.address.address"] = address_mod


def setUpModule():
    global pl, ps
    for name in _STUBBED:
        _SAVED[name] = sys.modules.get(name)
    _install_stub()
    sys.modules.pop("erpnext_enhancements.print_lookup", None)
    import erpnext_enhancements

    if "print_lookup" in vars(erpnext_enhancements):
        delattr(erpnext_enhancements, "print_lookup")
    from erpnext_enhancements import print_lookup, print_style

    pl, ps = print_lookup, print_style


def tearDownModule():
    import erpnext_enhancements

    # The module is bound to this suite's stub; a later suite must import its own.
    if "print_lookup" in vars(erpnext_enhancements):
        delattr(erpnext_enhancements, "print_lookup")
    for name in _STUBBED:
        if _SAVED.get(name) is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = _SAVED[name]
    _SAVED.clear()


class _Doc:
    """A Frappe document stand-in: attribute access plus .get(). Not a dict subclass —
    `doc.items` on one resolves to the bound method."""

    def __init__(self, **fields):
        self.__dict__.update(fields)

    def __getattr__(self, key):
        return None

    def get(self, key, default=None):
        return self.__dict__.get(key, default)


def sales_invoice(**overrides):
    """A Sales Invoice as this site has them: the snapshot fields ERPNext copies are
    blank (contact_mobile/contact_email on all but 12 of 1,629)."""
    fields = dict(
        doctype="Sales Invoice",
        name="ACC-SINV-2026-00412",
        customer="Canyon Ridge HOA",
        customer_name="Canyon Ridge HOA",
        company="Sapphire Fountains",
        customer_address=None,
        address_display=None,
        contact_person=None,
        contact_display=None,
        contact_mobile=None,
        contact_phone=None,
        contact_email=None,
    )
    fields.update(overrides)
    return _Doc(**fields)


def purchase_order(**overrides):
    """A Purchase Order as this site has them: address_display on 17 of 230."""
    fields = dict(
        doctype="Purchase Order",
        name="PUR-ORD-2026-00231",
        supplier="Wasatch Pool Supply",
        supplier_name="Wasatch Pool Supply",
        company="Sapphire Fountains",
        supplier_address=None,
        address_display=None,
        contact_person=None,
        contact_display=None,
        contact_mobile=None,
        contact_email=None,
    )
    fields.update(overrides)
    return _Doc(**fields)


class _Case(unittest.TestCase):
    def setUp(self):
        _reset()

    def fields_requested(self, doctype):
        return [f for dt, _n, fields in STATE["calls"] if dt == doctype for f in fields]


# ---------------------------------------------------------------------------
# Who a document is for.


class TestPartyOf(_Case):
    def test_sales_and_buying_documents(self):
        self.assertEqual(pl.party_of(sales_invoice()), ("Customer", "Canyon Ridge HOA"))
        self.assertEqual(pl.party_of(purchase_order()), ("Supplier", "Wasatch Pool Supply"))
        self.assertEqual(
            pl.party_of(_Doc(doctype="Purchase Invoice", supplier="Desert Pump Co")), ("Supplier", "Desert Pump Co")
        )

    def test_a_quotation_names_its_party_in_party_name(self):
        self.assertEqual(
            pl.party_of(_Doc(doctype="Quotation", quotation_to="Customer", party_name="Canyon Ridge HOA")),
            ("Customer", "Canyon Ridge HOA"),
        )
        self.assertEqual(
            pl.party_of(_Doc(doctype="Quotation", quotation_to="Lead", party_name="CRM-LEAD-2026-00012")),
            ("Lead", "CRM-LEAD-2026-00012"),
        )
        self.assertEqual(
            pl.party_of(_Doc(doctype="Quotation", quotation_to=None, party_name="Canyon Ridge HOA")),
            ("Customer", "Canyon Ridge HOA"),
        )

    def test_a_document_with_no_party(self):
        self.assertEqual(pl.party_of(_Doc(doctype="Material Request")), (None, None))
        details = pl.party_details(_Doc(doctype="Material Request"))
        self.assertEqual(details, {"name": "", "address": "", "attention": "", "phone": "", "email": ""})
        self.assertEqual(STATE["calls"], [], "nothing to look up, so nothing is read")


class TestPartyDetails(_Case):
    def test_a_customer_through_the_accounts_fields(self):
        """Customer.custom_accounts_phone_number / custom_accounts_email_address: where
        this site keeps a customer's accounts-payable contact."""
        details = pl.party_details(sales_invoice())
        self.assertEqual(details["name"], "Canyon Ridge HOA")
        self.assertEqual(details["phone"], "8015550142")
        self.assertEqual(details["email"], "ap@canyonridge.test")
        self.assertEqual(details["address"], "")
        self.assertEqual(details["attention"], "")
        self.assertEqual(STATE["errors"], [])

    def test_the_stock_customer_fields_are_the_fallback(self):
        record = STATE["records"]["Customer"]["Canyon Ridge HOA"]
        record.update(custom_accounts_phone_number=None, custom_accounts_email_address="  ",
                      mobile_no="801-555-0111", email_id="office@canyonridge.test")
        details = pl.party_details(sales_invoice())
        self.assertEqual(details["phone"], "801-555-0111")
        self.assertEqual(details["email"], "office@canyonridge.test")

    def test_a_supplier_through_its_custom_fields(self):
        details = pl.party_details(purchase_order())
        self.assertEqual(details["name"], "Wasatch Pool Supply")
        self.assertEqual(details["phone"], "801-555-0177")
        self.assertEqual(details["email"], "orders@wasatchpool.test")

    def test_the_contact_the_document_names_wins_over_the_party(self):
        details = pl.party_details(sales_invoice(contact_person="Dana Whitaker-Canyon Ridge HOA"))
        self.assertEqual(details["phone"], "8015550199", "Contact.custom_mobile_number")
        self.assertEqual(details["email"], "dana@canyonridge.test", "Contact.custom_email")

    def test_the_contacts_custom_fields_win_over_its_stock_ones(self):
        """custom_email is filled on 1,741 of 2,792 contacts; the stock email_id on 436."""
        contact = STATE["records"]["Contact"]["Dana Whitaker-Canyon Ridge HOA"]
        doc = sales_invoice(contact_person="Dana Whitaker-Canyon Ridge HOA")
        for cleared, phone in (
            ("custom_mobile_number", "801-555-0101"),
            ("custom_phone_number", "8015550000"),
            ("mobile_no", "8015550001"),
        ):
            contact[cleared] = None
            with self.subTest(cleared):
                self.assertEqual(pl.party_details(doc)["phone"], phone)
        contact["custom_email"] = None
        self.assertEqual(pl.party_details(doc)["email"], "dana.old@canyonridge.test")

    def test_the_partys_primary_contact_fills_phone_and_email(self):
        STATE["records"]["Customer"]["Canyon Ridge HOA"]["customer_primary_contact"] = "Dana Whitaker-Canyon Ridge HOA"
        details = pl.party_details(sales_invoice())
        self.assertEqual(details["phone"], "8015550199")
        self.assertEqual(details["email"], "dana@canyonridge.test")

    def test_the_documents_own_values_win_over_everything(self):
        STATE["records"]["Customer"]["Canyon Ridge HOA"].update(
            customer_primary_contact="Dana Whitaker-Canyon Ridge HOA",
            customer_primary_address="Canyon Ridge HOA-Billing",
        )
        doc = sales_invoice(
            contact_person="Dana Whitaker-Canyon Ridge HOA",
            contact_display="Pat Nguyen",
            contact_mobile="801-555-0123",
            contact_email="pat@canyonridge.test",
            address_display="9 Gate Rd<br>\nPark City, UT 84060<br>\n",
        )
        details = pl.party_details(doc)
        self.assertEqual(
            details,
            {
                "name": "Canyon Ridge HOA",
                "address": "9 Gate Rd<br>\nPark City, UT 84060<br>\n",
                "attention": "Pat Nguyen",
                "phone": "801-555-0123",
                "email": "pat@canyonridge.test",
            },
        )
        self.assertEqual(STATE["rendered"], [], "the document printed an address; no other is rendered")

    def test_contact_phone_stands_in_for_contact_mobile(self):
        details = pl.party_details(sales_invoice(contact_mobile="  ", contact_phone="801-555-0124"))
        self.assertEqual(details["phone"], "801-555-0124")

    def test_the_address_falls_back_to_the_suppliers_primary_address(self):
        """35 of this site's address-less orders, from 4 suppliers, can take a primary
        address from the supplier's own record. It is rendered, and its phone is read from
        the record."""
        STATE["records"]["Supplier"]["Wasatch Pool Supply"].update(
            supplier_primary_address="Wasatch Pool Supply-Billing", custom_phone_number=None, custom_email=None
        )
        details = pl.party_details(purchase_order())
        self.assertEqual(details["address"], STATE["records"]["Address"]["Wasatch Pool Supply-Billing"]["_html"])
        self.assertEqual(STATE["rendered"], [("Wasatch Pool Supply-Billing", False)], "check_permissions=False")
        self.assertEqual(details["phone"], "(801) 555-0160")
        self.assertEqual(details["email"], "billing@wasatchpool.test")

    def test_the_fallback_order_for_phone_and_email(self):
        """Phone: contact, then the Address record, then the party. Email: contact, then
        the party, then the Address record."""
        STATE["records"]["Supplier"]["Wasatch Pool Supply"]["supplier_primary_address"] = "Wasatch Pool Supply-Billing"
        details = pl.party_details(purchase_order())
        self.assertEqual(details["phone"], "(801) 555-0160", "the Address record's phone before the supplier's")
        self.assertEqual(details["email"], "orders@wasatchpool.test", "the supplier's email before the Address's")

    def test_the_documents_address_link_supplies_the_phone(self):
        details = pl.party_details(
            purchase_order(
                supplier_address="Wasatch Pool Supply-Billing",
                address_display=STATE["records"]["Address"]["Wasatch Pool Supply-Billing"]["_html"],
            )
        )
        self.assertEqual(details["phone"], "(801) 555-0160")
        self.assertEqual(STATE["rendered"], [])

    def test_the_documents_address_link_wins_when_its_snapshot_is_blank(self):
        """A document that names an Address but printed no snapshot of it (saved before the
        address was filled in, or cleared by hand) still prints ITS address, not the
        party's primary one."""
        STATE["records"]["Supplier"]["Wasatch Pool Supply"]["supplier_primary_address"] = "Wasatch Pool Supply-Billing"
        STATE["records"]["Address"]["Wasatch Pool Supply-Yard"] = dict(
            STATE["records"]["Address"]["Wasatch Pool Supply-Billing"],
            _html="9 Yard Way<br>Ogden, UT 84401<br>",
            phone="801-555-0177",
        )
        details = pl.party_details(purchase_order(supplier_address="Wasatch Pool Supply-Yard", address_display=""))
        self.assertEqual(details["address"], "9 Yard Way<br>Ogden, UT 84401<br>")
        self.assertEqual(STATE["rendered"], [("Wasatch Pool Supply-Yard", False)])
        self.assertEqual(details["phone"], "801-555-0177", "the phone comes from the address that printed")

    def test_a_deleted_address_link_is_skipped(self):
        STATE["records"]["Customer"]["Canyon Ridge HOA"].update(
            customer_primary_address="Canyon Ridge HOA-Old", custom_billing_address="Canyon Ridge HOA-Billing"
        )
        details = pl.party_details(sales_invoice())
        self.assertIn("1450 E Canyon Rd", details["address"])
        self.assertEqual(STATE["rendered"], [("Canyon Ridge HOA-Billing", False)])

    def test_the_partys_snapshot_is_the_last_resort_for_the_address(self):
        STATE["records"]["Customer"]["Canyon Ridge HOA"].update(
            customer_primary_address="Canyon Ridge HOA-Old", primary_address="PO Box 7<br>Sandy, UT 84091<br>"
        )
        details = pl.party_details(sales_invoice())
        self.assertEqual(details["address"], "PO Box 7<br>Sandy, UT 84091<br>")
        self.assertEqual(STATE["rendered"], [])

    def test_a_party_with_no_records_prints_its_name_only(self):
        doc = sales_invoice(customer="Walk-in Customer", customer_name=None)
        details = pl.party_details(doc)
        self.assertEqual(details, {"name": "Walk-in Customer", "address": "", "attention": "", "phone": "", "email": ""})
        self.assertEqual(pl.ps_party(doc), f'<span style="{ps.STRONG}">Walk-in Customer</span>')
        self.assertEqual(STATE["errors"], [])

    def test_the_title_comes_from_the_record_when_the_document_has_none(self):
        details = pl.party_details(_Doc(doctype="Purchase Invoice", supplier="Desert Pump Co"))
        self.assertEqual(details["name"], "Desert Pump Co.")

    def test_the_documents_title_wins(self):
        details = pl.party_details(purchase_order(supplier="Desert Pump Co", supplier_name="Desert Pump (St. George)"))
        self.assertEqual(details["name"], "Desert Pump (St. George)")

    def test_a_drop_ship_orders_customer_never_heads_the_supplier_block(self):
        """ERPNext's make_purchase_order keeps the end customer's customer_name on a PO
        whenever a line is delivered by the supplier. The SUPPLIER block reads the
        supplier's name by party type, never whichever name field is filled."""
        details = pl.party_details(purchase_order(customer="Canyon Ridge HOA", customer_name="Canyon Ridge HOA"))
        self.assertEqual(details["name"], "Wasatch Pool Supply")
        self.assertNotIn("Canyon Ridge", pl.ps_party(purchase_order(customer_name="Canyon Ridge HOA")))
        blank = pl.party_details(purchase_order(supplier_name=None, customer_name="Canyon Ridge HOA"))
        self.assertEqual(blank["name"], "Wasatch Pool Supply", "from the Supplier record, not the customer")


class TestQuotation(_Case):
    def test_a_customer_quotation(self):
        doc = _Doc(doctype="Quotation", quotation_to="Customer", party_name="Canyon Ridge HOA",
                   customer_name="Canyon Ridge HOA")
        details = pl.party_details(doc)
        self.assertEqual(details["name"], "Canyon Ridge HOA")
        self.assertEqual(details["phone"], "8015550142")
        self.assertEqual(details["email"], "ap@canyonridge.test")

    def test_a_lead_quotation(self):
        doc = _Doc(doctype="Quotation", quotation_to="Lead", party_name="CRM-LEAD-2026-00012")
        details = pl.party_details(doc)
        self.assertEqual(details["name"], "Riverside Plaza", "a lead's company before its person")
        self.assertEqual(details["phone"], "4355550123")
        self.assertEqual(details["email"], "morgan@riversideplaza.test")
        self.assertEqual(details["address"], "")
        self.assertEqual(set(self.fields_requested("Lead")), {"company_name", "lead_name", "mobile_no", "phone", "email_id"})
        self.assertNotIn("Contact", [dt for dt, _n, _f in STATE["calls"]], "a Lead names no contact")

    def test_a_lead_without_a_company_is_its_person(self):
        STATE["records"]["Lead"]["CRM-LEAD-2026-00012"].update(company_name=None, mobile_no=None, phone="435-555-0124")
        details = pl.party_details(_Doc(doctype="Quotation", quotation_to="Lead", party_name="CRM-LEAD-2026-00012"))
        self.assertEqual(details["name"], "Morgan Lee")
        self.assertEqual(details["phone"], "435-555-0124")

    def test_a_quotation_to_a_party_type_it_does_not_know(self):
        doc = _Doc(doctype="Quotation", quotation_to="Prospect", party_name="Canyon Ridge Prospect",
                   customer_name="Canyon Ridge")
        self.assertEqual(pl.party_details(doc)["name"], "Canyon Ridge")
        self.assertEqual(STATE["calls"], [])


class TestDefensive(_Case):
    def test_a_custom_field_the_site_lacks_is_never_requested(self):
        """A fresh site has none of this app's fields, and get_value raises on a column
        that does not exist -- inside a Jinja global, that is a blank page."""
        STATE["meta"]["Customer"] -= {"custom_accounts_phone_number", "custom_accounts_email_address",
                                      "custom_billing_address"}
        STATE["meta"]["Contact"] -= {"custom_mobile_number", "custom_phone_number", "custom_email"}
        record = STATE["records"]["Customer"]["Canyon Ridge HOA"]
        record.update(customer_primary_contact="Dana Whitaker-Canyon Ridge HOA", mobile_no="801-555-0111")
        details = pl.party_details(sales_invoice())

        customer_fields = self.fields_requested("Customer")
        contact_fields = self.fields_requested("Contact")
        self.assertTrue(customer_fields and contact_fields)
        for field in customer_fields + contact_fields:
            with self.subTest(field):
                self.assertFalse(field.startswith("custom_"), f"{field} was requested")
        self.assertEqual(STATE["errors"], [], "nothing raised, so nothing was logged")
        self.assertEqual(details["phone"], "8015550000", "the stock Contact.mobile_no")
        self.assertEqual(details["email"], "dana.old@canyonridge.test")

    def test_every_requested_field_is_one_the_meta_has(self):
        STATE["records"]["Customer"]["Canyon Ridge HOA"].update(
            customer_primary_contact="Dana Whitaker-Canyon Ridge HOA", customer_primary_address="Canyon Ridge HOA-Billing"
        )
        pl.party_details(sales_invoice())
        self.assertTrue(STATE["calls"])
        for doctype, _name, fields in STATE["calls"]:
            with self.subTest(doctype):
                self.assertLessEqual(set(fields), STATE["meta"][doctype])
        self.assertEqual(STATE["errors"], [])

    def test_a_failed_read_falls_back_to_the_document_and_logs(self):
        STATE["raise_on"].add("Customer")
        doc = sales_invoice(contact_display="Pat Nguyen", contact_email="pat@canyonridge.test",
                            address_display="9 Gate Rd<br>")
        details = pl.party_details(doc)
        self.assertEqual(
            details,
            {"name": "Canyon Ridge HOA", "address": "9 Gate Rd<br>", "attention": "Pat Nguyen", "phone": "",
             "email": "pat@canyonridge.test"},
        )
        self.assertEqual(len(STATE["errors"]), 1)
        args, _kwargs = STATE["errors"][0]
        self.assertIn("Print party lookup", args)
        # And the block still prints.
        self.assertIn("Attn: Pat Nguyen", pl.ps_party(doc))

    def test_a_failed_contact_read_keeps_what_was_already_found(self):
        STATE["raise_on"].add("Contact")
        STATE["records"]["Supplier"]["Wasatch Pool Supply"]["supplier_primary_address"] = "Wasatch Pool Supply-Billing"
        details = pl.party_details(purchase_order(contact_person="Sam Ortiz-Wasatch Pool Supply"))
        self.assertIn("2200 S State St", details["address"])
        self.assertEqual(len(STATE["errors"]), 1)

    def test_meta_that_cannot_load_reads_nothing(self):
        STATE["meta_raises"].add("Customer")
        details = pl.party_details(sales_invoice())
        self.assertEqual(details["name"], "Canyon Ridge HOA")
        self.assertEqual(details["phone"], "")
        self.assertEqual(self.fields_requested("Customer"), [])
        self.assertEqual(STATE["errors"], [])


class TestAttention(_Case):
    """"Attn:" at somebody the document did not name addresses it to the wrong person."""

    def test_the_documents_contact_display_is_the_attn_line(self):
        details = pl.party_details(sales_invoice(contact_display="  Dana Whitaker "))
        self.assertEqual(details["attention"], "Dana Whitaker")

    def test_the_partys_primary_contact_is_never_the_attn_line(self):
        STATE["records"]["Customer"]["Canyon Ridge HOA"]["customer_primary_contact"] = "Dana Whitaker-Canyon Ridge HOA"
        details = pl.party_details(sales_invoice())
        self.assertEqual(details["attention"], "")
        self.assertEqual(details["phone"], "8015550199", "it still lends its phone")
        self.assertNotIn("Attn", pl.ps_party(sales_invoice()))

    def test_a_named_contact_without_a_display_name_is_not_the_attn_line_either(self):
        details = pl.party_details(purchase_order(contact_person="Sam Ortiz-Wasatch Pool Supply"))
        self.assertEqual(details["attention"], "")
        self.assertEqual(details["phone"], "801-555-0188")


class TestPsParty(_Case):
    def test_the_block_is_print_ready(self):
        STATE["records"]["Customer"]["Canyon Ridge HOA"]["customer_primary_address"] = "Canyon Ridge HOA-Billing"
        out = pl.ps_party(sales_invoice(contact_display="Dana Whitaker"))
        self.assertEqual(
            out,
            "<br>".join(
                (
                    f'<span style="{ps.STRONG}">Canyon Ridge HOA</span>',
                    "1450 E Canyon Rd<br>\nSalt Lake City, UT 84108",
                    "Attn: Dana Whitaker",
                    "(801) 555-0150",
                    "ap@canyonridge.test",
                )
            ),
        )

    def test_a_phone_the_address_prints_is_printed_once(self):
        STATE["records"]["Address"]["Canyon Ridge HOA-Billing"]["_html"] += "Phone: 801-555-0150<br>\n"
        STATE["records"]["Customer"]["Canyon Ridge HOA"]["customer_primary_address"] = "Canyon Ridge HOA-Billing"
        out = pl.ps_party(sales_invoice())
        self.assertEqual(out.count("555-0150"), 1, out)


# ---------------------------------------------------------------------------
# Request for Quotation.


def rfq(suppliers, vendor=None):
    return _Doc(doctype="Request for Quotation", name="PUR-RFQ-2026-00019", vendor=vendor, suppliers=suppliers)


def rfq_rows():
    return [
        _Doc(supplier="Wasatch Pool Supply", supplier_name="Wasatch Pool Supply", email_id="quotes@wasatchpool.test",
             contact="Sam Ortiz-Wasatch Pool Supply"),
        _Doc(supplier="Desert Pump Co", supplier_name="Desert Pump Co.", email_id=None, contact=None),
    ]


class TestRfq(_Case):
    def test_no_vendor_addresses_every_supplier(self):
        details = pl.rfq_supplier_details(rfq(rfq_rows()))
        self.assertEqual([d["name"] for d in details], ["Wasatch Pool Supply", "Desert Pump Co."])

    def test_a_vendor_addresses_only_that_supplier(self):
        """ERPNext sets `vendor` before each per-supplier render."""
        details = pl.rfq_supplier_details(rfq(rfq_rows(), vendor="Desert Pump Co"))
        self.assertEqual([d["name"] for d in details], ["Desert Pump Co."])

    def test_a_vendor_not_in_the_table_still_gets_its_block(self):
        details = pl.rfq_supplier_details(rfq([], vendor="Desert Pump Co"))
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]["name"], "Desert Pump Co.")

    def test_the_rows_email_wins(self):
        details = pl.rfq_supplier_details(rfq(rfq_rows(), vendor="Wasatch Pool Supply"))[0]
        self.assertEqual(details["email"], "quotes@wasatchpool.test", "over Contact.custom_email and Supplier.custom_email")

    def test_the_rows_contact_is_the_attn_line_and_lends_its_phone(self):
        details = pl.rfq_supplier_details(rfq(rfq_rows(), vendor="Wasatch Pool Supply"))[0]
        self.assertEqual(details["attention"], "Sam Ortiz", "first + last when full_name is blank")
        self.assertEqual(details["phone"], "801-555-0188")

    def test_without_a_row_contact_the_primary_contact_lends_only_phone_and_email(self):
        details = pl.rfq_supplier_details(rfq(rfq_rows(), vendor="Desert Pump Co"))[0]
        self.assertEqual(details["attention"], "")
        self.assertEqual(details["email"], "lee@desertpump.test")
        self.assertIn("95 N Main St", details["address"])
        self.assertEqual(STATE["rendered"], [("Desert Pump Co-Billing", False)])

    def test_the_blocks_stack(self):
        out = pl.ps_rfq_suppliers(rfq(rfq_rows()))
        self.assertEqual(out.count(f'<span style="{ps.STRONG}">'), 2)
        self.assertIn('<div style="margin-top:8px"></div>', out)
        self.assertIn("Attn: Sam Ortiz", out)
        self.assertIn("quotes@wasatchpool.test", out)
        self.assertIn("(801) 555-0188", out)

    def test_no_suppliers_prints_a_dash(self):
        for doc in (rfq([]), rfq(None)):
            with self.subTest(doc.suppliers):
                out = pl.ps_rfq_suppliers(doc)
                self.assertIn("&mdash;", out)
                self.assertNotIn("<br>", out)

    def test_a_failed_read_keeps_the_rows_own_values_and_logs(self):
        STATE["raise_on"].add("Supplier")
        details = pl.rfq_supplier_details(rfq(rfq_rows()))
        self.assertEqual([d["name"] for d in details], ["Wasatch Pool Supply", "Desert Pump Co."])
        self.assertEqual(details[0]["email"], "quotes@wasatchpool.test")
        self.assertEqual(len(STATE["errors"]), 2, "one per row")


# ---------------------------------------------------------------------------
# Taxes and charges.


def tax_row(description, amount, account, charge_type="Actual", **extra):
    return _Doc(description=description, tax_amount=amount, account_head=account, charge_type=charge_type, **extra)


def invoice_taxes():
    """An invoice synced from QuickBooks: two billable-expense lines booked as Actual
    charges (the material, and its markup), then the tax."""
    return [
        tax_row("ACID-15841 MURIATIC ACID, 1 GAL", 42.50, "Cost of Goods Sold - SF"),
        tax_row("25% markup for ACID-15841", 10.63, "Sales - SF"),
        tax_row("Freight to site\nLiftgate", 85.00, "Freight and Delivery - SF"),
        tax_row("UT SPECIAL - SF", 3.12, "UT SPECIAL - SF"),
        tax_row("Utah Sales Tax - Inactive - SF", 18.40, "Utah Sales Tax - Inactive - SF",
                charge_type="On Net Total", rate=7.25),
        tax_row("Resort tax - SF", 1.10, "Sales - SF", charge_type="On Net Total", rate=1.1),
        tax_row("ACID-15842 zero line", 0, "Cost of Goods Sold - SF"),
        tax_row("UT SPECIAL - SF", 0.0, "UT SPECIAL - SF"),
    ]


class TestChargesAndTaxes(_Case):
    def doc(self, taxes=None, **overrides):
        fields = dict(doctype="Sales Invoice", company="Sapphire Fountains", taxes=invoice_taxes() if taxes is None else taxes)
        fields.update(overrides)
        return _Doc(**fields)

    def test_actual_rows_on_non_tax_accounts_are_charges(self):
        """1,030 of them on 155 invoices, up to 98 deep, printed as tax until now."""
        charges = pl.ps_charge_rows(self.doc())
        self.assertEqual(
            charges,
            [
                {"description": "ACID-15841 MURIATIC ACID, 1 GAL", "amount": 42.50},
                {"description": "25% markup for ACID-15841", "amount": 10.63},
                {"description": "Freight to site<br>Liftgate", "amount": 85.00},
            ],
        )

    def test_tax_accounts_and_percentage_rows_are_tax(self):
        taxes = pl.ps_tax_rows(self.doc())
        self.assertEqual(
            taxes,
            [
                {"label": "UT SPECIAL", "amount": 3.12},
                {"label": "Utah Sales Tax", "amount": 18.40},
                {"label": "Resort tax", "amount": 1.10},
            ],
        )

    def test_the_split_is_a_partition_of_the_non_zero_rows(self):
        rows = invoice_taxes()
        doc = self.doc(taxes=rows)
        charges = pl.ps_charge_rows(doc)
        taxes = pl.ps_tax_rows(doc)
        non_zero = sorted(r.tax_amount for r in rows if r.tax_amount)
        self.assertEqual(sorted([c["amount"] for c in charges] + [t["amount"] for t in taxes]), non_zero)
        self.assertEqual(len(charges) + len(taxes), len(non_zero))

    def test_zero_rows_print_nowhere(self):
        doc = self.doc()
        self.assertNotIn("zero line", str(pl.ps_charge_rows(doc)) + str(pl.ps_tax_rows(doc)))
        self.assertEqual(pl.ps_charge_rows(self.doc(taxes=[tax_row("x", None, "Sales - SF")])), [])
        self.assertEqual(pl.ps_tax_rows(self.doc(taxes=[tax_row("x", None, "UT SPECIAL - SF")])), [])

    def test_a_negative_row_is_not_zero(self):
        doc = self.doc(taxes=[tax_row("Tax credit - SF", -2.00, "UT SPECIAL - SF")])
        self.assertEqual(pl.ps_tax_rows(doc), [{"label": "Tax credit", "amount": -2.00}])

    def test_a_row_without_an_account_or_a_charge_type(self):
        """No account: tax, the conservative reading. No charge type: Actual."""
        doc = self.doc(taxes=[tax_row("Unbooked", 5.0, None), tax_row("Material", 6.0, "Sales - SF", charge_type=None)])
        self.assertEqual(pl.ps_tax_rows(doc), [{"label": "Unbooked", "amount": 5.0}])
        self.assertEqual(pl.ps_charge_rows(doc), [{"description": "Material", "amount": 6.0}])

    def test_text_is_escaped(self):
        doc = self.doc(taxes=[tax_row("<b>Tax</b> - SF", 1.0, "UT SPECIAL - SF"), tax_row("A & B", 2.0, "Sales - SF")])
        self.assertEqual(pl.ps_tax_rows(doc)[0]["label"], "&lt;b&gt;Tax&lt;/b&gt;")
        self.assertEqual(pl.ps_charge_rows(doc)[0]["description"], "A &amp; B")

    def test_without_a_company_the_labels_keep_their_suffix(self):
        taxes = pl.ps_tax_rows(self.doc(company=None))
        self.assertEqual([t["label"] for t in taxes],
                         ["UT SPECIAL - SF", "Utah Sales Tax - Inactive - SF", "Resort tax - SF"])

    def test_no_taxes(self):
        for taxes in ([], None):
            doc = _Doc(doctype="Sales Invoice", company="Sapphire Fountains", taxes=taxes)
            with self.subTest(taxes):
                self.assertEqual(pl.ps_charge_rows(doc), [])
                self.assertEqual(pl.ps_tax_rows(doc), [])

    def test_an_account_lookup_that_fails_prints_everything_as_tax(self):
        """The split stays a partition: nothing is printed twice and nothing vanishes."""
        STATE["raise_on"].add("Account")
        doc = self.doc()
        self.assertEqual(pl.ps_charge_rows(doc), [])
        self.assertEqual(len(pl.ps_tax_rows(doc)), 6)
        self.assertEqual(len(STATE["errors"]), 1)
        self.assertIn("Print charge rows", STATE["errors"][0][0])

    def test_a_company_lookup_that_fails_keeps_the_rows(self):
        STATE["raise_on"].add("Company")
        taxes = pl.ps_tax_rows(self.doc())
        self.assertEqual([t["label"] for t in taxes], ["UT SPECIAL - SF", "Utah Sales Tax - Inactive - SF", "Resort tax - SF"])


# ---------------------------------------------------------------------------
# Registration.


class TestHooksRegistration(_Case):
    def _registered(self):
        return set(re.findall(r'"erpnext_enhancements\.print_lookup\.(\w+)"', HOOKS))

    def test_every_ps_global_is_registered_and_exists(self):
        public = {name for name in vars(pl) if name.startswith("ps_") and callable(getattr(pl, name))}
        self.assertEqual(public, {"ps_party", "ps_rfq_suppliers", "ps_charge_rows", "ps_tax_rows"})
        self.assertEqual(public, self._registered())

    def test_nothing_unprefixed_is_registered(self):
        for name in self._registered():
            self.assertTrue(name.startswith("ps_"), name)

    def test_no_name_collides_with_print_style(self):
        """Both modules feed one Jinja namespace; the later registration would win."""
        self.assertEqual({n for n in vars(pl) if n.startswith("ps_")} & {n for n in vars(ps) if n.startswith("ps_")}, set())


if __name__ == "__main__":
    unittest.main()
