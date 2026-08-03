"""Bench-free tests for the customer-facing sales print formats (WI-020).

A print format fails in the worst possible way: the deploy succeeds, nothing logs, and
the defect is discovered by a customer holding the PDF. The Purchase Order format spent
its first month going to suppliers with no logo on it because a custom Jinja format has
to render its own letter head and nobody noticed.

So this suite checks the things that fail silently:

* the templates **compile** — a Jinja syntax error would otherwise surface as a blank
  document at print time, not at deploy time;
* the **letter head** is rendered by every template (the month-long bug above);
* `description` is rendered **unescaped** and `item_name` **escaped** — get this backwards
  and the customer reads raw `&lt;div&gt;` markup;
* **print-safe CSS only** — no flexbox or grid, which the PDF backend on this host does
  not lay out reliably;
* the Quotation template never references `doc.project`, because **Quotation has no
  `project` column on this site** and Jinja would render it as empty rather than error.

Run: python -m unittest erpnext_enhancements.tests.test_sales_print_formats
"""

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULE_PATH = (
    REPO_ROOT
    / "erpnext_enhancements"
    / "enhancements_core"
    / "setup_sales_print_formats.py"
)

# The module imports `frappe` at the top, which is not available bench-free. It is a
# pure-string module otherwise, so exec it with a stub rather than importing it.
_NAMESPACE = {}


def setUpModule():
    import types

    sys.modules.setdefault("frappe", types.ModuleType("frappe"))
    code = MODULE_PATH.read_text(encoding="utf-8")
    exec(compile(code, str(MODULE_PATH), "exec"), _NAMESPACE)


def formats():
    return _NAMESPACE["FORMATS"]


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
        self.assertEqual(
            names,
            {"Quotation - Sapphire", "Sales Order - Sapphire", "Sales Invoice - Sapphire"},
        )

    def test_each_targets_the_right_doctype(self):
        by_name = {n: d for n, d, _h in formats()}
        self.assertEqual(by_name["Quotation - Sapphire"], "Quotation")
        self.assertEqual(by_name["Sales Order - Sapphire"], "Sales Order")
        self.assertEqual(by_name["Sales Invoice - Sapphire"], "Sales Invoice")


class _Doc:
    """Stands in for a Frappe document: attribute access plus .get().

    Deliberately NOT a dict subclass. `doc.items` is the line-item table on a real
    Frappe document, and on a dict subclass it silently resolves to `dict.items` --
    the bound method -- so every template loop iterates a method instead of the rows.
    """

    def __init__(self, **fields):
        self.__dict__.update(fields)

    def __getattr__(self, key):
        return None  # an unset field is empty, exactly as on a real document

    def get(self, key, default=None):
        return self.__dict__.get(key, default)

    def update(self, other):
        self.__dict__.update(other)


def _stub_frappe():
    """Enough of the frappe template context to actually render."""
    import types

    utils = types.SimpleNamespace(
        fmt_money=lambda v, currency=None: f"{currency or ''} {float(v or 0):,.2f}".strip()
    )
    db = types.SimpleNamespace(get_value=lambda dt, name, field: f"<{field}>")
    return types.SimpleNamespace(
        format=lambda v, opts=None: str(v),
        utils=utils,
        db=db,
    )


def _sample(doctype, rows, **overrides):
    doc = _Doc(
        name=f"{doctype[:3].upper()}-0001",
        doctype=doctype,
        company="Sapphire Fountains",
        currency="USD",
        customer="CUST-0001",
        customer_name="A Customer",
        owner="lisa@example.com",
        address_display="1 Street<br>Salt Lake City",
        contact_display="A Person",
        company_address_display="2 Other Street",
        transaction_date="2026-08-03",
        posting_date="2026-08-03",
        net_total=100.0,
        grand_total=106.25,
        taxes=[_Doc(description="Utah Sales Tax 6.25%", tax_amount=6.25)],
        items=[
            _Doc(
                item_code=f"ITEM-{i}",
                item_name=f"Item {i}",
                description="<p>Markup <b>from the item master</b></p>" if i % 2 else None,
                qty=i,
                uom="Nos",
                rate=10.0,
                amount=10.0 * i,
            )
            for i in range(1, rows + 1)
        ],
    )
    doc.update(overrides)
    return doc


class TestTemplatesRender(unittest.TestCase):
    """Parsing proves the syntax; rendering proves the field references."""

    def _render(self, html, doc, letter_head="<div>LETTERHEAD</div>"):
        from jinja2 import Environment

        return Environment().from_string(html).render(
            doc=doc, frappe=_stub_frappe(), letter_head=letter_head
        )

    def setUp(self):
        try:
            import jinja2  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest("jinja2 not installed")

    def test_renders_with_one_item_and_with_ten(self):
        for name, doctype, html in formats():
            for rows in (1, 10):
                with self.subTest(f"{name}/{rows}"):
                    out = self._render(html, _sample(doctype, rows))
                    self.assertIn("LETTERHEAD", out)
                    self.assertIn("ITEM-1", out)
                    self.assertNotIn("{{", out)

    def test_renders_with_no_items(self):
        for name, doctype, html in formats():
            with self.subTest(name):
                out = self._render(html, _sample(doctype, 0))
                self.assertIn("No items on this document.", out)

    def test_renders_with_no_taxes(self):
        for name, doctype, html in formats():
            with self.subTest(name):
                out = self._render(html, _sample(doctype, 2, taxes=[]))
                self.assertIn("Subtotal", out)

    def test_renders_with_empty_optional_fields(self):
        """Every optional field blank at once -- the sparsest real document."""
        blanks = dict(
            address_display=None, contact_display=None, company_address_display=None,
            valid_till=None, delivery_date=None, due_date=None, po_no=None,
            project=None, terms=None, outstanding_amount=None,
            custom_stripe_payment_link=None,
        )
        for name, doctype, html in formats():
            with self.subTest(name):
                out = self._render(html, _sample(doctype, 2, **blanks))
                self.assertNotIn("None", out)

    def test_item_description_markup_survives_and_names_are_escaped(self):
        for name, doctype, html in formats():
            with self.subTest(name):
                out = self._render(html, _sample(doctype, 2))
                self.assertIn("<b>from the item master</b>", out)

    def test_invoice_renders_the_stripe_link_only_when_set(self):
        html = dict((n, h) for n, _d, h in formats())["Sales Invoice - Sapphire"]
        link = "https://pay.example.com/abc"

        with_link = self._render(html, _sample("Sales Invoice", 2, custom_stripe_payment_link=link))
        self.assertIn(link, with_link)
        self.assertIn("Pay online", with_link)

        without = self._render(html, _sample("Sales Invoice", 2, custom_stripe_payment_link=None))
        self.assertNotIn("Pay online", without)
        self.assertIn("Remit to", without)

    def test_invoice_shows_amount_due_only_when_part_paid(self):
        html = dict((n, h) for n, _d, h in formats())["Sales Invoice - Sapphire"]

        part_paid = self._render(html, _sample("Sales Invoice", 2, outstanding_amount=50.0))
        self.assertIn("Amount due", part_paid)

        unpaid = self._render(html, _sample("Sales Invoice", 2, outstanding_amount=106.25))
        self.assertNotIn("Amount due", unpaid)

    def test_renders_without_a_letter_head(self):
        """The site has one, but a missing letterhead must not break the document."""
        for name, doctype, html in formats():
            with self.subTest(name):
                out = self._render(html, _sample(doctype, 2), letter_head=None)
                self.assertIn("ITEM-1", out)


class TestSilentFailureModes(unittest.TestCase):
    def test_letter_head_is_rendered(self):
        """A custom_format template gets no letterhead injected -- it must render one."""
        for name, _doctype, html in formats():
            with self.subTest(name):
                self.assertIn("{{ letter_head }}", html)

    def test_description_unescaped_item_name_escaped(self):
        for name, _doctype, html in formats():
            with self.subTest(name):
                self.assertIn("{{ row.description }}", html)
                self.assertNotIn("row.description | e", html)
                self.assertIn("{{ row.item_name | e }}", html)

    def test_no_unsafe_css(self):
        """The PDF backend does not lay out flexbox or grid reliably."""
        for name, _doctype, html in formats():
            with self.subTest(name):
                self.assertNotIn("display:flex", html.replace(" ", ""))
                self.assertNotIn("display:grid", html.replace(" ", ""))

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
        html = dict((n, h) for n, _d, h in formats())["Quotation - Sapphire"]
        self.assertNotIn("doc.project", html)

    def test_money_always_carries_a_currency(self):
        """fmt_money without a currency silently prints the system default."""
        for name, _doctype, html in formats():
            with self.subTest(name):
                for call in re.findall(r"fmt_money\([^)]*\)", html):
                    self.assertIn("currency=", call, f"{name}: {call}")


class TestDocumentSpecifics(unittest.TestCase):
    def by_name(self, name):
        return dict((n, h) for n, _d, h in formats())[name]

    def test_quotation_shows_validity(self):
        self.assertIn("Valid until", self.by_name("Quotation - Sapphire"))
        self.assertIn("doc.valid_till", self.by_name("Quotation - Sapphire"))

    def test_sales_order_shows_delivery_and_reference(self):
        html = self.by_name("Sales Order - Sapphire")
        self.assertIn("doc.delivery_date", html)
        self.assertIn("doc.po_no", html)
        self.assertIn("doc.project", html)

    def test_invoice_shows_due_date_and_amount_due(self):
        html = self.by_name("Sales Invoice - Sapphire")
        self.assertIn("doc.due_date", html)
        self.assertIn("Amount due", html)
        self.assertIn("doc.outstanding_amount", html)

    def test_invoice_stripe_link_is_guarded(self):
        """No placeholder button: an unusable Pay now is worse than none."""
        html = self.by_name("Sales Invoice - Sapphire")
        self.assertIn('doc.get("custom_stripe_payment_link")', html)
        link_pos = html.index("custom_stripe_payment_link")
        guard_pos = html.index('{%- if doc.get("custom_stripe_payment_link") %}')
        self.assertLess(guard_pos, link_pos + 1)

    def test_invoice_tells_the_customer_how_to_pay(self):
        html = self.by_name("Sales Invoice - Sapphire")
        self.assertIn("How to pay", html)
        self.assertIn("Remit to", html)


if __name__ == "__main__":
    unittest.main()
