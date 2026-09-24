"""Bench-free tests for the customer-facing sales print formats (WI-020).

A print format fails in the worst possible way: the deploy succeeds, nothing logs, and
the defect is discovered by a customer holding the PDF. The Purchase Order format spent
its first month going to suppliers with no logo on it because a custom Jinja format has
to render its own letter head and nobody noticed.

So this suite checks the things that fail silently:

* the templates **compile** — a Jinja syntax error would otherwise surface as a blank
  document at print time, not at deploy time;
* the **letter head** is rendered by every template (the month-long bug above);
* `description` goes through **`ps_line`**, which passes markup through and escapes plain
  text — the template never prints it raw and never escapes it itself; get this wrong
  and the customer reads raw `&lt;div&gt;` markup, or 1,657 imported lines run together;
* the page **adds up**: the Amount column sums to the Subtotal, and Subtotal − Discount +
  taxes is the grand total (until v1.533.0 the Subtotal was `net_total`, which already had
  the discount taken off, so every discounted document subtracted it twice);
* QuickBooks **billable expenses** print as lines, not as tax;
* the invoice's **payment state** — credit note, draft, cancelled, unpaid, part-paid,
  paid in full — each prints what it should and nothing else;
* **print-safe CSS only** — no flexbox or grid, which the PDF backend on this host does
  not lay out reliably — and every cell style carries the `!important` that is the only
  thing frappe's own print stylesheets do not override;
* the Quotation template never reads `doc.project` or `doc.customer`, because
  **Quotation has neither column on this site**, and every `ps_*` global a template calls
  is one hooks.py actually registers.

The templates render in a plain jinja2 Environment with StrictUndefined, the real
`print_style` globals (frappe-free) and fakes for the three `print_lookup` ones, which
need a database. `_Doc` raises AttributeError on a field the doctype does not have on
production, as a v16 Document does, so reading one fails the render.

Run: python -m unittest erpnext_enhancements.tests.test_sales_print_formats
"""

import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The print design system is frappe-free on purpose, so its `ps_*` globals are the real
# ones here -- the same functions hooks.py registers.
from erpnext_enhancements import print_style as ps

# Shared with the Purchase Order format. Importable without a bench: that module is pure
# strings and never imports frappe.
from erpnext_enhancements.enhancements_core.company_contact import (
    COMPANY_ADDRESS_HTML,
    COMPANY_PHONE,
)

MODULE_PATH = (
    REPO_ROOT
    / "erpnext_enhancements"
    / "enhancements_core"
    / "setup_sales_print_formats.py"
)
HOOKS_PATH = REPO_ROOT / "erpnext_enhancements" / "hooks.py"

# The module imports `frappe` at the top, which is not available bench-free. It is a
# pure-string module otherwise, so exec it with a stub rather than importing it.
_NAMESPACE = {}


def setUpModule():
    sys.modules.setdefault("frappe", types.ModuleType("frappe"))
    code = MODULE_PATH.read_text(encoding="utf-8")
    exec(compile(code, str(MODULE_PATH), "exec"), _NAMESPACE)


def formats():
    return _NAMESPACE["FORMATS"]


def by_name(name):
    return dict((n, h) for n, _d, h in formats())[name]


QUOTATION = "Quotation - Sapphire"
SALES_ORDER = "Sales Order - Sapphire"
SALES_INVOICE = "Sales Invoice - Sapphire"


class TestTemplatesCompile(unittest.TestCase):
    def test_jinja_compiles(self):
        try:
            from jinja2 import Environment
        except ImportError:  # pragma: no cover
            self.skipTest("jinja2 not installed")
        env = Environment()
        for name, _doctype, html in formats():
            with self.subTest(name):
                env.parse(html)  # raises TemplateSyntaxError on a malformed template

    def test_three_formats_declared(self):
        names = {n for n, _d, _h in formats()}
        self.assertEqual(names, {QUOTATION, SALES_ORDER, SALES_INVOICE})

    def test_each_targets_the_right_doctype(self):
        by_doctype = {n: d for n, d, _h in formats()}
        self.assertEqual(by_doctype[QUOTATION], "Quotation")
        self.assertEqual(by_doctype[SALES_ORDER], "Sales Order")
        self.assertEqual(by_doctype[SALES_INVOICE], "Sales Invoice")


# Fields each doctype does NOT have on production (prod field lists, 2026-09-24) that a
# sales template could plausibly reach for. A v16 Document raises AttributeError on them;
# Jinja turns that into Undefined, which prod prints as debug text.
_ABSENT = {
    "Quotation": {
        "customer", "project", "po_no", "posting_date", "due_date", "delivery_date",
        "is_return", "return_against", "outstanding_amount", "custom_stripe_payment_link",
    },
    "Sales Order": {
        "party_name", "quotation_to", "valid_till", "posting_date", "due_date",
        "is_return", "return_against", "outstanding_amount", "custom_stripe_payment_link",
    },
    "Sales Invoice": {"party_name", "quotation_to", "valid_till", "transaction_date", "delivery_date"},
}


class _Doc:
    """Stands in for a Frappe document: attribute access plus .get().

    Deliberately NOT a dict subclass. `doc.items` is the line-item table on a real
    Frappe document, and on a dict subclass it silently resolves to `dict.items` --
    the bound method -- so every template loop iterates a method instead of the rows.

    An unset field reads None, exactly as on a real document; a field the doctype does
    not have raises AttributeError, exactly as v16 does.
    """

    def __init__(self, **fields):
        self.__dict__.update(fields)

    def __getattr__(self, key):
        if key.startswith("__"):
            raise AttributeError(key)
        if key in _ABSENT.get(self.__dict__.get("doctype"), ()):
            raise AttributeError(f"{self.__dict__.get('doctype')} has no field {key!r}")
        return None

    def get(self, key, default=None):
        return self.__dict__.get(key, default)


# frappe.db.get_value answers, keyed (doctype, name, field).
_DB = {
    ("User", "lisa@example.com", "full_name"): "Lisa Carter",
    ("User", "Administrator", "full_name"): "Administrator",
    ("Project", "PRJ-00706", "project_name"): "Riverside Commons Entry Fountain",
    ("Project", "PRJ-00580", "project_name"): "PRJ-00580",  # a project named after itself
}


def _stub_frappe():
    """Enough of the frappe template context to actually render."""
    utils = types.SimpleNamespace(
        fmt_money=lambda v, currency=None: f"{currency or ''} {float(v or 0):,.2f}".strip()
    )
    db = types.SimpleNamespace(get_value=lambda dt, name, field: _DB.get((dt, name, field)))
    return types.SimpleNamespace(format=lambda v, opts=None: str(v), utils=utils, db=db)


# Fakes for the print_lookup globals, which need a database. The real ps_party walks the
# document, its contact, its address and the party's record; this one prints what the
# document carries. The real charge/tax split asks each row's Account for its type; here a
# row says which it is.
def _fake_party(doc):
    return ps.party_block(
        doc.get("customer_name"),
        doc.get("address_display"),
        doc.get("contact_display"),
        doc.get("contact_mobile"),
        doc.get("contact_email"),
    )


def _fake_charge_rows(doc):
    return [
        {"description": ps.rich_text(t.get("description")), "amount": t.get("tax_amount")}
        for t in doc.get("taxes") or []
        if t.get("is_charge") and t.get("tax_amount")
    ]


def _fake_tax_rows(doc):
    return [
        {"label": ps.escape_html(ps.clean_label(t.get("description"), "SF")), "amount": t.get("tax_amount")}
        for t in doc.get("taxes") or []
        if not t.get("is_charge") and t.get("tax_amount")
    ]


def _globals():
    names = {name: getattr(ps, name) for name in dir(ps) if name.startswith("ps_")}
    names.update(
        ps_party=_fake_party,
        ps_charge_rows=_fake_charge_rows,
        ps_tax_rows=_fake_tax_rows,
        ps_rfq_suppliers=lambda doc: "",
    )
    return names


def _render(html, doc, letter_head="<div>LETTERHEAD</div>"):
    from jinja2 import Environment, StrictUndefined

    env = Environment(undefined=StrictUndefined)
    env.globals.update(_globals())
    return env.from_string(html).render(doc=doc, frappe=_stub_frappe(), letter_head=letter_head)


TAX_RATE = 0.0625


def _sample(doctype, rows, discount=0.0, charges=(), **overrides):
    """A document whose numbers agree with each other, the way ERPNext's are: `total` is
    the sum of the lines, the discount comes off the Net Total (the only way this site
    applies one), tax is on the net, and charges are Actual rows in the taxes table."""
    items = [
        _Doc(
            item_code=f"ITEM-{i}",
            item_name=f"Item {i}",
            description="<p>Markup <b>from the item master</b></p>" if i % 2 else None,
            qty=float(i),
            uom="Nos",
            rate=10.0,
            amount=10.0 * i,
        )
        for i in range(1, rows + 1)
    ]
    total = round(sum(r.amount for r in items), 2)
    net = round(total - discount, 2)
    tax = round(net * TAX_RATE, 2)
    charge_total = round(sum(amount for _d, amount in charges), 2)
    taxes = [_Doc(description=d, tax_amount=a, is_charge=True) for d, a in charges]
    taxes.append(_Doc(description="Utah Sales Tax - SF", tax_amount=tax))
    taxes.append(_Doc(description="Transient Room Tax", tax_amount=0.0))  # a zero row: skipped
    grand = round(net + tax + charge_total, 2)
    fields = dict(
        name=f"{doctype[:3].upper()}-0001",
        doctype=doctype,
        docstatus=0 if doctype == "Quotation" else 1,
        company="Sapphire Fountains",
        currency="USD",
        customer="CUST-0001",
        party_name="CUST-0001",
        quotation_to="Customer",
        customer_name="A Customer",
        customer_address="A Customer-Billing",
        owner="lisa@example.com",
        address_display="1 Street<br>Salt Lake City<br>",
        contact_display="A Person",
        company_address_display="2 Other Street",
        transaction_date="2026-08-03",
        posting_date="2026-08-03",
        due_date="2026-09-02",
        total=total,
        net_total=net,
        discount_amount=discount,
        grand_total=grand,
        taxes=taxes,
        items=items,
    )
    fields.update(overrides)
    for key in _ABSENT.get(doctype, ()):
        fields.pop(key, None)  # the field does not exist, so neither does its value
    return _Doc(**fields)


def _money(text):
    text = text.strip()
    negative = text.startswith("-")
    number = float(re.sub(r"[^\d.\-]", "", text.lstrip("-")))
    return -number if negative else number


def _totals(out):
    """The totals block as [(label, amount)], top to bottom."""
    table = out[out.rindex("<table", 0, out.index(">Subtotal<")) :]
    table = table[: table.index("</table>")]
    rows = []
    for label, value in re.findall(r"<td[^>]*>([^<]+)</td>\s*<td[^>]*>([^<]*USD[^<]*)</td>", table):
        rows.append((label.strip(), _money(value)))
    return rows


def _amount_column(out):
    """The sum of the line table's Amount column -- item lines and billable expenses."""
    body = out[out.index("<tbody>") : out.index("</tbody>")]
    total = 0.0
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, flags=re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)
        if len(cells) == 5 and "USD" in cells[4]:
            total += _money(cells[4])
    return round(total, 2)


class _RenderCase(unittest.TestCase):
    def setUp(self):
        try:
            import jinja2
        except ImportError:  # pragma: no cover
            self.skipTest("jinja2 not installed")


class TestTemplatesRender(_RenderCase):
    """Parsing proves the syntax; rendering proves the field references."""

    def test_renders_with_one_item_and_with_ten(self):
        for name, doctype, html in formats():
            for rows in (1, 10):
                with self.subTest(f"{name}/{rows}"):
                    out = _render(html, _sample(doctype, rows))
                    self.assertIn("<svg", out, "the wordmark is drawn by the template")
                    self.assertNotIn("LETTERHEAD", out, "the site's Letter Head must not add a second logo")
                    self.assertIn("Item 1", out, "the item's name is the line")
                    self.assertNotIn("ITEM-1", out, "customers do not know our item codes")
                    self.assertNotIn("{{", out)

    def test_renders_with_no_items(self):
        for name, doctype, html in formats():
            with self.subTest(name):
                out = _render(html, _sample(doctype, 0))
                self.assertIn("No items on this document.", out)

    def test_renders_with_no_taxes(self):
        for name, doctype, html in formats():
            with self.subTest(name):
                out = _render(html, _sample(doctype, 2, taxes=[]))
                self.assertIn("Subtotal", out)

    def test_renders_with_empty_optional_fields(self):
        """Every optional field blank at once -- the sparsest real document."""
        blanks = dict(
            address_display=None, contact_display=None, company_address_display=None,
            valid_till=None, delivery_date=None, due_date=None, po_no=None,
            project=None, terms=None, outstanding_amount=None,
            custom_stripe_payment_link=None, payment_terms_template=None,
            shipping_address_name=None, shipping_address=None, customer_address=None,
            return_against=None, discount_amount=None,
        )
        for name, doctype, html in formats():
            with self.subTest(name):
                out = _render(html, _sample(doctype, 2, **blanks))
                self.assertNotIn("None", out)

    def test_item_description_markup_survives(self):
        for name, doctype, html in formats():
            with self.subTest(name):
                out = _render(html, _sample(doctype, 2))
                self.assertIn("<b>from the item master</b>", out)

    def test_plain_text_description_is_escaped_and_keeps_its_lines(self):
        """1,657 of 6,148 invoice lines are plain text with newlines, imported from QBO."""
        for name, doctype, html in formats():
            with self.subTest(name):
                doc = _sample(doctype, 1)
                doc.items[0].description = "Clean basin\nBalance water & keep pH < 7.8"
                out = _render(html, doc)
                self.assertIn("Clean basin<br>Balance water &amp; keep pH &lt; 7.8", out)

    def test_a_description_that_repeats_the_name_is_not_printed_twice(self):
        for name, doctype, html in formats():
            with self.subTest(name):
                doc = _sample(doctype, 1)
                doc.items[0].description = "Item 1"
                self.assertEqual(_render(html, doc).count("Item 1"), 1)

    def test_quantities_and_units_read_as_a_person_writes_them(self):
        for name, doctype, html in formats():
            with self.subTest(name):
                out = _render(html, _sample(doctype, 2))
                self.assertIn(">2</td>", out)
                self.assertNotIn(">2.0</td>", out)
                self.assertIn(">ea</td>", out)
                self.assertNotIn(">Nos</td>", out)

    def test_invoice_renders_the_stripe_link_only_when_set(self):
        link = "https://pay.example.com/abc"

        with_link = _render(by_name(SALES_INVOICE), _sample("Sales Invoice", 2, custom_stripe_payment_link=link))
        self.assertIn(link, with_link)
        self.assertIn("Pay online", with_link)

        without = _render(by_name(SALES_INVOICE), _sample("Sales Invoice", 2, custom_stripe_payment_link=None))
        self.assertNotIn("Pay online", without)
        self.assertIn("Remit to", without)

    def test_renders_without_a_letter_head(self):
        """The site has one, but a missing letterhead must not break the document."""
        for name, doctype, html in formats():
            with self.subTest(name):
                out = _render(html, _sample(doctype, 2), letter_head=None)
                self.assertIn("Item 1", out)


class TestTheTotalsAddUp(_RenderCase):
    """The page is a sum a customer can check, and until v1.533.0 it did not check out."""

    def test_subtotal_is_the_total_and_the_page_adds_up(self):
        """All 47 discounted invoices apply the discount to the Net Total, so `net_total`
        already has it taken off. Printed as Subtotal, with the Discount row under it, the
        discount came off twice and the rows did not reach the grand total."""
        for name, doctype, html in formats():
            for discount in (0.0, 5.0):
                with self.subTest(f"{name}/discount {discount}"):
                    doc = _sample(doctype, 3, discount=discount)
                    out = _render(html, doc)
                    rows = _totals(out)
                    labels = [label for label, _v in rows]
                    self.assertEqual(rows[0], ("Subtotal", doc.total))
                    self.assertEqual(_amount_column(out), doc.total)
                    grand = labels.index(next(l for l in labels if l.endswith(" total")))
                    self.assertAlmostEqual(sum(v for _l, v in rows[:grand]), doc.grand_total, places=2)
                    self.assertEqual(rows[grand][1], doc.grand_total)
                    if discount:
                        self.assertIn(("Discount", -discount), rows)
                    else:
                        self.assertNotIn("Discount", labels)

    def test_billable_expenses_print_as_lines_not_as_tax(self):
        """154 invoices carry 1,030 QuickBooks billable-expense rows in the taxes table; they
        printed under Subtotal looking like tax."""
        charges = (("HAS15841 HASA MURIATIC ACID", 70.26), ("25% markup for HAS15841", 17.57))
        for name, doctype, html in formats():
            with self.subTest(name):
                doc = _sample(doctype, 2, charges=charges)
                out = _render(html, doc)
                self.assertIn("BILLABLE EXPENSES", out)
                lines = out[: out.index(">Subtotal<")]
                for description, _amount in charges:
                    self.assertIn(description, lines)
                labels = [label for label, _v in _totals(out)]
                self.assertNotIn("HAS15841 HASA MURIATIC ACID", " ".join(labels))
                # They count in the Subtotal, so the Amount column adds up to it...
                self.assertEqual(_totals(out)[0][1], round(doc.total + 70.26 + 17.57, 2))
                self.assertEqual(_amount_column(out), _totals(out)[0][1])
                # ...and the page still reaches its own grand total.
                self.assertIn(("Utah Sales Tax", round(doc.net_total * TAX_RATE, 2)), _totals(out))

    def test_no_billable_expenses_no_heading(self):
        for name, doctype, html in formats():
            with self.subTest(name):
                self.assertNotIn("BILLABLE EXPENSES", _render(html, _sample(doctype, 2)))

    def test_tax_rows_print_without_the_company_suffix(self):
        for name, doctype, html in formats():
            with self.subTest(name):
                out = _render(html, _sample(doctype, 2))
                self.assertIn(">Utah Sales Tax<", out)
                self.assertNotIn("Utah Sales Tax - SF", out)
                self.assertNotIn("Transient Room Tax", out, "a zero tax row prints nothing")


class TestInvoiceStates(_RenderCase):
    def render(self, **overrides):
        return _render(by_name(SALES_INVOICE), _sample("Sales Invoice", 2, **overrides))

    def labels(self, out):
        return [label for label, _v in _totals(out)]

    def test_a_credit_note_says_so_and_asks_for_nothing(self):
        out = self.render(is_return=1, return_against="ACC-SINV-2026-01748", outstanding_amount=-10.0)
        # Said once: the eyebrow that only repeated the title is gone (v1.535.0).
        self.assertNotIn("CREDIT NOTE", out)
        self.assertIn("Credit Note</h1>", out)
        self.assertNotIn("Invoice</h1>", out)
        self.assertIn("AGAINST INVOICE", out)
        self.assertIn("ACC-SINV-2026-01748", out)
        self.assertNotIn("PAYMENT DUE", out)
        self.assertIn("Credit total", self.labels(out))
        self.assertNotIn("Invoice total", self.labels(out))
        self.assertNotIn("How to pay", out)
        self.assertNotIn("Payments received", out)
        self.assertNotIn("Amount due", out)
        self.assertNotIn("Paid in full", out)

    def test_a_credit_note_without_its_invoice_prints_a_dash(self):
        out = self.render(is_return=1, return_against=None)
        self.assertIn("AGAINST INVOICE", out)
        self.assertIn("&mdash;", out[out.index("AGAINST INVOICE") :])

    def test_paid_in_full(self):
        """1,162 of 1,324 submitted invoices. Payment instructions on a paid invoice invite
        a second payment, so it says it is paid instead."""
        doc = _sample("Sales Invoice", 2)
        out = _render(by_name(SALES_INVOICE), _sample("Sales Invoice", 2, outstanding_amount=0.0))
        rows = _totals(out)
        self.assertIn(("Payments received", -doc.grand_total), rows)
        self.assertEqual(rows[-1], ("Amount due", 0.0))
        self.assertIn("Paid in full &mdash; thank you.", out)
        self.assertNotIn("How to pay", out)
        self.assertNotIn("Remit to", out)

    def test_part_paid(self):
        doc = _sample("Sales Invoice", 2)
        out = self.render(outstanding_amount=round(doc.grand_total - 20.0, 2))
        rows = _totals(out)
        self.assertIn(("Payments received", -20.0), rows)
        self.assertEqual(rows[-1], ("Amount due", round(doc.grand_total - 20.0, 2)))
        self.assertIn("How to pay", out)
        self.assertNotIn("Paid in full", out)

    def test_unpaid_prints_no_amount_due(self):
        """The total IS the amount due; a second row saying so is noise."""
        doc = _sample("Sales Invoice", 2)
        out = self.render(outstanding_amount=doc.grand_total)
        self.assertNotIn("Amount due", out)
        self.assertNotIn("Payments received", out)
        self.assertIn("How to pay", out)

    def test_a_draft_says_draft_and_prints_no_payments(self):
        out = self.render(docstatus=0, outstanding_amount=0.0)
        self.assertIn(">DRAFT</div>", out)
        self.assertNotIn("Payments received", out)
        self.assertNotIn("Amount due", out)
        self.assertNotIn("Paid in full", out)

    def test_a_cancelled_invoice_says_so_and_asks_for_nothing(self):
        out = self.render(docstatus=2, outstanding_amount=0.0)
        self.assertIn(">CANCELLED</div>", out)
        self.assertNotIn("How to pay", out)
        self.assertNotIn("Payments received", out)
        self.assertNotIn("Paid in full", out)

    def test_a_submitted_invoice_carries_no_marker(self):
        out = self.render()
        self.assertNotIn(">DRAFT</div>", out)
        self.assertNotIn(">CANCELLED</div>", out)

    def test_due_on_receipt(self):
        """due_date == posting_date on 1,583 of 1,629 invoices."""
        for label, over in (
            ("same day", {"due_date": "2026-08-03"}),
            ("no due date", {"due_date": None}),
        ):
            with self.subTest(label):
                out = self.render(**over)
                self.assertIn("Due on receipt", out)

    def test_a_later_due_date_prints_with_its_terms_under_it(self):
        out = self.render(due_date="2026-09-02", payment_terms_template="Net 30")
        self.assertIn("2026-09-02", out)
        self.assertNotIn("Due on receipt", out)
        self.assertIn("Net 30", out)

    def test_terms_that_say_due_on_receipt_are_not_printed_twice(self):
        for terms in ("Due on receipt", "Due Upon Receipt"):
            with self.subTest(terms):
                out = self.render(due_date="2026-08-03", payment_terms_template=terms)
                self.assertEqual(out.lower().count("due on receipt") + out.lower().count("due upon receipt"), 1)


class TestDraftMarker(_RenderCase):
    def test_sales_order_says_draft(self):
        self.assertIn(">DRAFT</div>", _render(by_name(SALES_ORDER), _sample("Sales Order", 2, docstatus=0)))
        self.assertNotIn(">DRAFT</div>", _render(by_name(SALES_ORDER), _sample("Sales Order", 2, docstatus=1)))

    def test_quotation_never_does(self):
        """All 673 quotations are drafts; a stamp on every one is noise."""
        self.assertNotIn("ps_state", by_name(QUOTATION))
        self.assertNotIn(">DRAFT</div>", _render(by_name(QUOTATION), _sample("Quotation", 2, docstatus=0)))


class TestFacts(_RenderCase):
    def test_ship_to_prints_only_when_it_differs(self):
        ship = "9 Site Road<br>Provo<br>"
        cases = (
            ("no shipping address", {}, False),
            ("same record as billing", {"shipping_address_name": "A Customer-Billing", "shipping_address": ship}, False),
            ("different, but empty", {"shipping_address_name": "A Customer-Site", "shipping_address": ""}, False),
            (
                "different record, same text",
                {"shipping_address_name": "A Customer-Site", "shipping_address": "1 Street<br>Salt Lake City<br>"},
                False,
            ),
            ("different", {"shipping_address_name": "A Customer-Site", "shipping_address": ship}, True),
        )
        for name, doctype, html in formats():
            for label, over, expected in cases:
                with self.subTest(f"{name}/{label}"):
                    out = _render(html, _sample(doctype, 2, **over))
                    self.assertEqual("SHIP TO" in out, expected)
                    if expected:
                        self.assertIn("9 Site Road<br>Provo</div>", out, "trailing <br> trimmed")

    def test_the_party_block_is_ps_party(self):
        for name, _doctype, html in formats():
            with self.subTest(name):
                self.assertIn("ps_party(doc)", html)
                self.assertNotIn("doc.contact_display", html)
                self.assertNotIn("doc.address_display }}", html)

    def test_prepared_by_administrator_is_the_company(self):
        """24 quotations are owned by Administrator, whose full name is "Administrator"."""
        html = by_name(QUOTATION)
        out = _render(html, _sample("Quotation", 2, owner="Administrator"))
        self.assertIn("Sapphire Fountains", out[out.index("PREPARED BY") :][:400])
        self.assertNotIn(">Administrator<", out)
        self.assertIn("Lisa Carter", _render(html, _sample("Quotation", 2)))
        self.assertIn("someone@example.com", _render(html, _sample("Quotation", 2, owner="someone@example.com")))

    def test_valid_until_only_when_there_is_a_date(self):
        """valid_till is empty on all 673 quotations: VALID UNTIL printed a dash on every
        one, and the acceptance line pointed at a date above that was not there."""
        html = by_name(QUOTATION)
        dated = _render(html, _sample("Quotation", 2, valid_till="2026-10-24"))
        self.assertIn("VALID UNTIL", dated)
        self.assertIn("Valid until 2026-10-24. Sign and return to accept this quotation.", dated)

        undated = _render(html, _sample("Quotation", 2, valid_till=None))
        self.assertNotIn("VALID UNTIL", undated)
        self.assertNotIn("Valid until", undated)
        self.assertIn("Sign and return to accept this quotation.", undated)
        self.assertNotIn("PAYMENT TERMS", undated)

    def test_payment_terms_take_the_slot_when_there_is_no_date(self):
        html = by_name(QUOTATION)
        out = _render(html, _sample("Quotation", 2, valid_till=None, payment_terms_template="Net 30 <b>"))
        self.assertIn("PAYMENT TERMS", out)
        self.assertIn("Net 30 &lt;b&gt;", out)
        dated = _render(html, _sample("Quotation", 2, valid_till="2026-10-24", payment_terms_template="Net 30"))
        self.assertNotIn("PAYMENT TERMS", dated)

    def test_project_prints_its_name(self):
        for name in (SALES_ORDER, SALES_INVOICE):
            doctype = name.replace(" - Sapphire", "")
            with self.subTest(name):
                out = _render(by_name(name), _sample(doctype, 2, project="PRJ-00706"))
                self.assertIn("PRJ-00706", out)
                self.assertIn("Riverside Commons Entry Fountain", out)
                itself = _render(by_name(name), _sample(doctype, 2, project="PRJ-00580"))
                self.assertEqual(itself.count("PRJ-00580"), 1, "a name equal to the number is not repeated")
                none = _render(by_name(name), _sample(doctype, 2, project=None))
                self.assertIn("&mdash;", none[none.index("PROJECT") :][:400])

    def test_your_ref_only_when_the_customer_gave_one(self):
        """po_no is empty on all 1,629 invoices; YOUR REFERENCE printed a dash on each."""
        for name in (SALES_ORDER, SALES_INVOICE):
            doctype = name.replace(" - Sapphire", "")
            with self.subTest(name):
                out = _render(by_name(name), _sample(doctype, 2, po_no="HOA-114 & co"))
                self.assertIn("Your ref. HOA-114 &amp; co", out)
                none = _render(by_name(name), _sample(doctype, 2, po_no=None))
                self.assertNotIn("Your ref", none)
                self.assertNotIn("YOUR REFERENCE", none)


class TestTheHeaderCarriesOurContactDetails(_RenderCase):
    """The customer must be able to reach us from the page they are holding.

    The letter head cannot do it -- `Sapphire Fountains Default` is a right-aligned logo
    and nothing else -- so the template draws the name, address and phone beside it. The
    block is shared with the Purchase Order format; these tests pin that it is wired to
    the *sales* address field, which is not the one Purchase Order uses.
    """

    def render(self, html, doctype, letter_head="<div>LETTERHEAD</div>", **overrides):
        return _render(html, _sample(doctype, 2, **overrides), letter_head=letter_head)

    def test_no_placeholder_survives_composition(self):
        for name, _doctype, html in formats():
            for marker in ("__CONTACT_BLOCK__", "__ADDRESS_FIELD__", "__COMPANY_", "__TOTAL_LABEL__", "__TERMS_LABEL__"):
                with self.subTest(f"{name}/{marker}"):
                    self.assertNotIn(marker, html)

    def test_the_phone_is_printed_on_all_three(self):
        for name, doctype, html in formats():
            with self.subTest(name):
                self.assertIn(COMPANY_PHONE, self.render(html, doctype))

    def test_the_phone_is_never_conditional(self):
        """It has no data source to be conditional on -- `Company.phone_no` and
        `Address.phone` are both null on this site."""
        for name, doctype, html in formats():
            with self.subTest(name):
                self.assertIn(
                    COMPANY_PHONE, self.render(html, doctype, company_address_display=None)
                )

    def test_the_document_address_wins_when_it_has_one(self):
        """673 of 673 Quotations and 1,629 of 1,629 Sales Invoices carry one."""
        for name, doctype, html in formats():
            with self.subTest(name):
                self.assertIn("2 Other Street", self.render(html, doctype))

    def test_the_constant_covers_a_document_without_one(self):
        for name, doctype, html in formats():
            with self.subTest(name):
                out = self.render(html, doctype, company_address_display=None)
                self.assertIn(COMPANY_ADDRESS_HTML, out)

    def test_the_address_is_never_blank(self):
        """Whichever source applies, something is always printed. The sample document's
        own address is deliberately NOT our real one, so these three cases also prove
        which source won rather than just that the words appeared."""
        for name, doctype, html in formats():
            for label, over, expected in (
                ("present", {}, "2 Other Street"),
                ("missing", {"company_address_display": None}, COMPANY_ADDRESS_HTML),
                ("empty", {"company_address_display": ""}, COMPANY_ADDRESS_HTML),
            ):
                with self.subTest(f"{name}/{label}"):
                    self.assertIn(expected, self.render(html, doctype, **over))

    def test_it_reads_the_sales_address_field_not_the_purchase_one(self):
        """Purchase Order calls the same thing `billing_address_display`. Wiring that
        here prints nothing and raises nothing -- Jinja renders a missing attribute as
        empty, so the block would silently fall back to the constant forever."""
        self.assertEqual(_NAMESPACE["ADDRESS_FIELD"], "company_address_display")
        for name, _doctype, html in formats():
            with self.subTest(name):
                self.assertIn("doc.company_address_display", html)
                self.assertNotIn("billing_address_display", html)


class TestSilentFailureModes(unittest.TestCase):
    def test_the_wordmark_is_rendered(self):
        """A custom_format template gets no letterhead injected -- it must draw one.

        Since v1.494.0 `print_style.letterhead()` inlines the wordmark SVG itself, so
        the site's Letter Head (a bare logo) must NOT also be rendered: two logos on
        one page is the new version of the old unbranded-for-a-month bug."""
        for name, _doctype, html in formats():
            with self.subTest(name):
                self.assertIn("<svg", html)
                self.assertIn("Sapphire Fountains, LLC", html)
                self.assertNotIn("{{ letter_head }}", html)

    def test_the_chrome_is_the_pillar_stripe(self):
        """Every sales document opens and closes with the stripe and carries the
        display face; a format that lost its chrome would still render, unbranded."""
        for name, _doctype, html in formats():
            with self.subTest(name):
                self.assertEqual(html.count("linear-gradient(90deg"), 2)
                self.assertIn("@font-face", html)
                self.assertIn("Big Noodle Titling", html)
                self.assertNotIn("__OPEN__", html)

    def test_description_goes_through_ps_line(self):
        """Never raw (plain text would run together), never escaped by the template
        (markup would print as `&lt;div&gt;`): ps_line decides, per line."""
        for name, _doctype, html in formats():
            with self.subTest(name):
                self.assertIn("{{ ps_line(row.item_name, row.description) }}", html)
                self.assertNotIn("row.description | e", html)
                self.assertNotIn("{{ row.description }}", html)
                self.assertNotIn("row.item_code", html)

    def test_no_unsafe_css(self):
        """The PDF backend does not lay out flexbox or grid reliably."""
        for name, _doctype, html in formats():
            with self.subTest(name):
                self.assertNotIn("display:flex", html.replace(" ", ""))
                self.assertNotIn("display:grid", html.replace(" ", ""))

    def test_every_cell_style_carries_important(self):
        """frappe's standard.css and the site's Redesign Print Style set td/th padding and
        alignment with !important; an inline style without it never reaches the page."""
        for name, _doctype, html in formats():
            with self.subTest(name):
                cells = re.findall(r'<t[dh]\b[^>]*style="([^"]*)"', html)
                self.assertTrue(cells)
                for style in cells:
                    self.assertIn("!important", style, style[:80])
                self.assertNotIn("<td>", html)
                self.assertNotIn("<td></td>", html)

    def test_the_totals_use_the_shared_styles(self):
        """One totals block for every priced format, from print_style: no local copies."""
        for key in ("_TOTAL_LABEL_TD", "_TOTAL_VALUE_TD", "_GRAND_TD"):
            self.assertNotIn(key, _NAMESPACE)
        for name, _doctype, html in formats():
            with self.subTest(name):
                self.assertIn(ps.TOTAL_LABEL, html)
                self.assertIn(ps.TOTAL_VALUE, html)
                self.assertIn(ps.GRAND, html)
                self.assertIn(ps.TOTAL_SPACER, html)

    def test_table_header_repeats_across_pages(self):
        for name, _doctype, html in formats():
            with self.subTest(name):
                self.assertIn("display:table-header-group", html)

    def test_rows_avoid_page_breaks(self):
        for name, _doctype, html in formats():
            with self.subTest(name):
                self.assertIn("page-break-inside:avoid", html)

    def test_quotation_never_references_project(self):
        """Quotation has no `project` column on this site; Jinja would render it blank."""
        self.assertNotIn("doc.project", by_name(QUOTATION))

    def test_quotation_never_reads_customer(self):
        """Quotation has no `customer` column either -- its party is `party_name`. A v16
        Document raises on the read, and prod prints the Undefined as debug text."""
        html = by_name(QUOTATION)
        self.assertIsNone(re.search(r"\bdoc\.customer\b", html))
        self.assertIsNone(re.search(r"""doc\.get\(\s*["']customer["']""", html))
        # And the render agrees: _Doc raises on `customer` for a Quotation, and a
        # StrictUndefined render of it fails loudly on any read.
        doc = _sample("Quotation", 2)
        with self.assertRaises(AttributeError):
            doc.customer
        _render(html, doc)

    def test_money_always_carries_a_currency(self):
        """fmt_money without a currency silently prints the system default."""
        for name, _doctype, html in formats():
            with self.subTest(name):
                calls = re.findall(r"fmt_money\([^)]*\)", html)
                self.assertTrue(calls)
                for call in calls:
                    self.assertIn("currency=doc.currency", call, f"{name}: {call}")

    def test_every_ps_global_the_templates_call_is_registered(self):
        """A global hooks.py does not register is Undefined on prod: the print raises or
        prints debug text, and nothing here would notice, because the suite supplies its
        own globals."""
        hooks = HOOKS_PATH.read_text(encoding="utf-8")
        registered = set(re.findall(r'"erpnext_enhancements\.print_(?:style|lookup)\.(ps_\w+)"', hooks))
        for name, _doctype, html in formats():
            with self.subTest(name):
                called = set(re.findall(r"\b(ps_\w+)\s*\(", html))
                self.assertTrue(called)
                self.assertEqual(called - registered, set())


class TestDocumentSpecifics(unittest.TestCase):
    def test_quotation_shows_validity(self):
        self.assertIn("Valid until", by_name(QUOTATION))
        self.assertIn("doc.valid_till", by_name(QUOTATION))

    def test_sales_order_shows_delivery_and_reference(self):
        html = by_name(SALES_ORDER)
        self.assertIn("doc.delivery_date", html)
        self.assertIn("doc.po_no", html)
        self.assertIn("doc.project", html)

    def test_invoice_shows_due_date_and_amount_due(self):
        html = by_name(SALES_INVOICE)
        self.assertIn("doc.due_date", html)
        self.assertIn("Amount due", html)
        self.assertIn("doc.outstanding_amount", html)

    def test_invoice_stripe_link_is_guarded(self):
        """No placeholder button: an unusable Pay now is worse than none."""
        html = by_name(SALES_INVOICE)
        self.assertIn('doc.get("custom_stripe_payment_link")', html)
        link_pos = html.index("custom_stripe_payment_link")
        guard_pos = html.index('{%- if doc.get("custom_stripe_payment_link") %}')
        self.assertLess(guard_pos, link_pos + 1)

    def test_invoice_tells_the_customer_how_to_pay(self):
        html = by_name(SALES_INVOICE)
        self.assertIn("How to pay", html)
        self.assertIn("Remit to", html)


if __name__ == "__main__":
    unittest.main()
