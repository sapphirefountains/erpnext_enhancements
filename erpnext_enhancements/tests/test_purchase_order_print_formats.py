"""What the Purchase Order print format says, and which of its rivals get deleted.

Two concerns, both of which fail silently on a supplier-facing document.

**What it says.** The header carries our own address and phone. The address prefers the
document's `billing_address_display` and falls back to a constant; the phone has no
data source at all and *is* the constant. Neither can go missing quietly — a print
format fails with a successful deploy, no log line, and a supplier holding the PDF.
This format already spent its first month going out with no logo on it for exactly
that reason.

**Which rivals go.** TASK-2026-01237 asked to purge every PO print format except
``Purchase Order - Sapphire``. Five had to go and **they do not go the same way**:

* ``Test Purchase Order Format`` and ``PO Test Print Format`` are custom
  (``standard = "No"``). Deleting them is real and permanent.
* ``Purchase Order Standard``, ``Purchase Order with Item Image`` and
  ``Drop Shipping Format`` ship with ERPNext. A deleted standard format has no row,
  so the next ``bench migrate`` imports it again from the app's JSON: a patch that
  deleted them would appear to work and undo itself — the worst shape of failure,
  because nobody looks again until they print a PO weeks later. They are disabled
  instead, which that import keeps (frappe v16 ``import_file.ignore_values``).

So the split is the design, and this pins it. Getting it backwards is invisible
until production.

**The Sales Invoice cleanup** (v1.533.0) rides the same disable pass —
``SUPERSEDED_SALES_FORMATS`` — and the three sales doctypes default to their
Sapphire formats through Property Setter fixtures; both are pinned here too.

Bench-free: reads the sources as text, and execs the setup module against a `frappe`
stub to get at the composed template. Own CI step, because that stub is process-wide.

Run: python -m unittest erpnext_enhancements.tests.test_purchase_order_print_formats
"""

import ast
import json
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The two constants live in `company_contact` now, shared with the sales formats.
# Importable without a bench: that module is pure strings and has no frappe import. So is
# the print design system, whose real `ps_*` globals are passed to the renders.
from erpnext_enhancements import print_style as ps
from erpnext_enhancements.enhancements_core.company_contact import (
    COMPANY_ADDRESS_HTML,
    COMPANY_PHONE,
)

APP = REPO_ROOT / "erpnext_enhancements"
SETUP = APP / "enhancements_core/setup_print_formats.py"
SALES_SETUP = APP / "enhancements_core/setup_sales_print_formats.py"
PATCH = APP / "patches/purge_purchase_order_print_formats.py"
PATCHES_TXT = APP / "patches.txt"
HOOKS = APP / "hooks.py"
PROPERTY_SETTERS = APP / "fixtures/property_setter.json"
PRINT_FORMAT_FIXTURES = APP / "fixtures/print_format.json"

KEEP = "Purchase Order - Sapphire"
DELETABLE = ("Test Purchase Order Format", "PO Test Print Format")
STANDARD = ("Purchase Order Standard", "Purchase Order with Item Image", "Drop Shipping Format")

# `_HTML` is composed with `.replace()`, so it is not an ast literal like the lists above.
# The module imports `frappe` at the top but only uses it inside functions, so a stub is
# enough to exec it and read the finished template.
_NAMESPACE = {}


def setUpModule():
    import types

    frappe = sys.modules.setdefault("frappe", types.ModuleType("frappe"))
    # `procurement_project` (imported below for the real jinja method) needs this much of
    # frappe at import time: `from frappe.utils import flt`, and `@frappe.whitelist()` on
    # the two endpoints that live beside the function we want.
    if not hasattr(frappe, "utils"):
        utils = types.ModuleType("frappe.utils")
        utils.flt = lambda v, precision=None: float(v or 0)
        frappe.utils = utils
        sys.modules["frappe.utils"] = utils
    if not hasattr(frappe, "whitelist"):
        frappe.whitelist = lambda *a, **kw: (lambda fn: fn)
    exec(compile(SETUP.read_text(encoding="utf-8"), str(SETUP), "exec"), _NAMESPACE)


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


class _Doc:
    """Stands in for a Frappe document: attribute access plus .get().

    Deliberately NOT a dict subclass -- `doc.items` is the line-item table, and on a
    dict subclass it resolves to the bound `dict.items` method instead of the rows.
    """

    def __init__(self, **fields):
        self.__dict__.update(fields)

    def __getattr__(self, key):
        return None  # an unset field is empty, exactly as on a real document

    def get(self, key, default=None):
        return self.__dict__.get(key, default)


def _stub_frappe(project_name=None):
    """`project_name` is a `(docname) -> str` hook for the one lookup that varies.

    Per-project by default, because the header joins several: a stub answering every
    lookup with one string would hide a template printing the same name twice. Pass
    `lambda name: name` for the projects whose `project_name` IS their docname — there are
    such rows, and printing that under an identifier already containing it is the number
    twice and no information.
    """
    import types

    project_name = project_name or (lambda name: f"{name} Fountain")
    return types.SimpleNamespace(
        format=lambda v, opts=None: str(v),
        utils=types.SimpleNamespace(
            fmt_money=lambda v, currency=None: f"{currency or ''} {float(v or 0):,.2f}".strip()
        ),
        db=types.SimpleNamespace(
            get_value=lambda dt, name, field: (
                project_name(name) if dt == "Project" and field == "project_name" else f"<{field}>"
            )
        ),
    )


def _fake_party(doc):
    """Stands in for `print_lookup.ps_party`, which needs a bench: it follows the document's
    Contact, Address and Supplier records. Built on the real `print_style.party_block` from
    the document's own fields, so the block looks exactly as it does on production."""
    return ps.party_block(
        doc.get("supplier_name") or doc.get("supplier") or "",
        doc.get("address_display") or "",
        doc.get("contact_display") or "",
        doc.get("contact_mobile") or "",
        doc.get("contact_email") or "",
    )


def _jinja_globals(party=None):
    """Every `ps_*` global hooks.py registers: print_style's for real, print_lookup's faked."""
    methods = {name: getattr(ps, name) for name in dir(ps) if name.startswith("ps_")}
    methods["ps_party"] = party or _fake_party
    return methods


def _render(doc, letter_head="<div>LETTERHEAD</div>", project_name=None, party=None):
    """Render the composed template with every jinja method the hook registers.

    One helper rather than three copies: a template gaining a method it can call is a
    template that raises `UndefinedError` at render time in every test that forgot to pass
    it, and the useful failure is the one that says the *format* is wrong.

    The project methods are the real ones, not stand-ins. They are what decide which
    project the sheet names when the header and item rows disagree, and what makes the
    printed identifier the same string as the PDF's filename; a stub answering either
    differently from production would make this suite worse than no suite. So are the
    `print_style` globals. Only `ps_party` is faked, because it reads the database.
    """
    from jinja2 import Environment

    from erpnext_enhancements.po_pdf_filename import purchase_order_document_id
    from erpnext_enhancements.procurement_project import purchase_order_projects

    return (
        Environment()
        .from_string(_NAMESPACE["_HTML"])
        .render(
            doc=doc,
            frappe=_stub_frappe(project_name),
            letter_head=letter_head,
            purchase_order_projects=purchase_order_projects,
            purchase_order_document_id=purchase_order_document_id,
            **_jinja_globals(party),
        )
    )


def _sample(**overrides):
    doc = _Doc(
        name="PO-2026-00262",
        company="Sapphire Fountains",
        currency="USD",
        supplier="SUP-0001",
        supplier_name="A Supplier",
        owner="buyer@example.com",
        docstatus=1,
        status="To Receive and Bill",
        transaction_date="2026-08-14",
        billing_address_display="85 W 300 S<br>\nBountiful, UT 84010<br>\n",
        net_total=100.0,
        grand_total=106.25,
        taxes=[],
        payment_schedule=[],
        items=[
            _Doc(
                item_code="ITEM-1",
                item_name="Pump",
                description="<p>Markup <b>from the item master</b></p>",
                qty=2,
                uom="Nos",
                rate=50.0,
                amount=100.0,
                project="PRJ-00001",
            )
        ],
    )
    doc.__dict__.update(overrides)
    return doc


class TestTheHeaderNamesTheJob(unittest.TestCase):
    """ER-2026-256847: the person filing these cannot tell one PO from another.

    Two failures are guarded, and neither is a blank space on a page.

    **Naming the wrong job.** `Purchase Order.project` and `Purchase Order Item.project` can
    disagree, and a template reading either alone is silently wrong on the documents where
    they do. It calls the same `purchase_order_projects` everything else here calls.

    **The sheet and the file disagreeing.** The printed identifier is character-for-character
    the PDF's filename stem, because it is the same function call. Somebody holding the
    printout next to the file it came from is exactly who this feature is for, and two
    renderings of one idea drift — the id bounds a long tail of jobs with `plus-N`, and the
    template loop this replaced would not have.
    """

    def setUp(self):
        try:
            import jinja2
        except ImportError:  # pragma: no cover
            self.skipTest("jinja2 not installed")

    def render(self, **overrides):
        return _render(_sample(**overrides), letter_head="")

    def test_the_identifier_carries_the_project(self):
        self.assertIn("PO-2026-00262-PRJ-00706", self.render(project="PRJ-00706"))

    def test_it_is_exactly_the_pdf_filename(self):
        """The whole point. If these ever diverge the feature quietly stops being one."""
        from erpnext_enhancements.po_pdf_filename import purchase_order_filename

        doc = _sample(project="PRJ-00706")
        stem = purchase_order_filename(doc)[: -len(".pdf")]
        self.assertIn(stem, _render(doc, ""))

    def test_they_agree_on_the_bounded_long_tail(self):
        """The case a second rendering gets wrong: past the cap the filename says `plus-N`
        and a template loop would have listed every job."""
        from erpnext_enhancements.po_pdf_filename import purchase_order_filename

        doc = _sample(project=None)
        doc.__dict__["items"] = [_Doc(project=f"PRJ-0000{i}") for i in range(1, 7)]
        out = _render(doc, "")
        self.assertIn("plus-3", out)
        self.assertIn(purchase_order_filename(doc)[: -len(".pdf")], out)

    def test_a_row_project_is_found_when_the_header_has_none(self):
        """44 of 204 lines were once blank under a PO whose header named the job; the
        reverse happens too, and the union is the only rule right either way."""
        self.assertIn("PO-2026-00262-PRJ-00001", self.render(project=None))

    def test_the_header_project_leads(self):
        out = self.render(project="PRJ-00706")
        self.assertLess(out.index("PRJ-00706"), out.index("PRJ-00001"))

    def test_the_readable_name_prints_under_the_identifier(self):
        """Nobody files by PRJ-00706. The number is the identifier; the name is what lets a
        human use it."""
        out = self.render(project="PRJ-00706")
        self.assertIn("PRJ-00706 Fountain", out)
        self.assertLess(out.index("PO-2026-00262-PRJ-00706"), out.index("PRJ-00706 Fountain"))

    def test_a_project_with_no_name_of_its_own_prints_no_second_line(self):
        """`project_name` equals the docname on some projects. Printing it under an
        identifier that already contains it is the number twice and no information."""
        doc = _sample(project="PRJ-00706")
        doc.items[0].__dict__["project"] = "PRJ-00706"
        out = _render(doc, "", project_name=lambda name: name)
        after_id = out.split("PO-2026-00262-PRJ-00706", 1)[1].split("<table", 1)[0]
        self.assertNotIn("PRJ-00706", after_id, "the project id was printed a second time")

    def test_a_purchase_order_on_no_project_prints_no_placeholder(self):
        """61 of 158 orders carry none. A dash or the word None in the identifier block
        would be worse than the blank it replaces."""
        doc = _sample(project=None)
        doc.items[0].__dict__["project"] = None
        out = _render(doc, "")
        self.assertNotIn("None", out)
        self.assertIn("PO-2026-00262", out)

    def test_both_jinja_methods_are_registered(self):
        """A template calling an unregistered method raises at render time -- i.e. the
        supplier gets no PDF at all, not a PDF missing a line."""
        hooks = HOOKS.read_text(encoding="utf-8")
        for method in (
            "erpnext_enhancements.procurement_project.purchase_order_projects",
            "erpnext_enhancements.po_pdf_filename.purchase_order_document_id",
        ):
            with self.subTest(method):
                self.assertIn(method, hooks)
        self.assertIn("purchase_order_document_id(doc)", _NAMESPACE["_HTML"])

    def test_every_ps_global_the_template_calls_is_registered(self):
        """Same failure, for the print design system's globals: each `ps_*` the order calls
        must be a registered `print_style` or `print_lookup` function."""
        hooks = HOOKS.read_text(encoding="utf-8")
        called = set(re.findall(r"\b(ps_\w+)\(", _NAMESPACE["_HTML"]))
        self.assertTrue({"ps_party", "ps_address", "ps_qty", "ps_uom", "ps_rich"} <= called, called)
        for name in sorted(called):
            with self.subTest(name):
                self.assertTrue(
                    f'"erpnext_enhancements.print_style.{name}"' in hooks
                    or f'"erpnext_enhancements.print_lookup.{name}"' in hooks,
                    f"{name} is called by the template but not registered in hooks.py jinja methods",
                )


def _fact(out, label, next_label):
    """The markup printed under one fact label, up to the next label."""
    return out.split(f">{label}<", 1)[1].split(f">{next_label}<", 1)[0]


def _visible(fragment):
    """A fragment's text: tags dropped (including the ones `_fact` cut in half), whitespace
    collapsed."""
    return " ".join(re.sub(r"<[^>]*>|^[^<]*>|<[^>]*$", " ", fragment).split())


class TestWhatTheSupplierReads(unittest.TestCase):
    """The facts a supplier acts on (v1.533.0): who the order is to, where it goes, whether
    it is an order at all, and the lines as a person would write them."""

    def setUp(self):
        try:
            import jinja2
        except ImportError:  # pragma: no cover
            self.skipTest("jinja2 not installed")

    def render(self, **overrides):
        return _render(_sample(**overrides), letter_head="")

    # --- SUPPLIER --------------------------------------------------------------------

    def test_the_supplier_block_is_the_party_helper(self):
        """Name, address, Attn, phone and email come from `print_lookup.ps_party`: this site
        keeps them on the Contact, Address and Supplier, not on the order."""
        self.assertIn("{{ ps_party(doc) }}", _NAMESPACE["_HTML"])
        self.assertNotIn("doc.address_display", _NAMESPACE["_HTML"])
        out = _render(_sample(), "", party=lambda doc: "PARTY-BLOCK-FOR-" + doc.name)
        self.assertIn("PARTY-BLOCK-FOR-PO-2026-00262", _fact(out, "SUPPLIER", "REQUIRED BY"))

    def test_the_supplier_name_is_escaped(self):
        out = self.render(supplier_name="Wasatch Stone & Tile")
        self.assertIn("Wasatch Stone &amp; Tile", _fact(out, "SUPPLIER", "REQUIRED BY"))

    # --- DELIVER TO ------------------------------------------------------------------

    def test_deliver_to_never_reads_the_address_link(self):
        """`shipping_address` is the Link to the Address record. Printing it put the record's
        name, "Sapphire Fountain-Billing", in front of the supplier on 221 of 230 orders."""
        html = _NAMESPACE["_HTML"]
        self.assertIsNone(re.search(r"doc\.shipping_address\b", html))
        self.assertIsNone(re.search(r"""get\(\s*["']shipping_address["']""", html))

    def test_deliver_to_prints_the_rendered_address_without_its_trailing_break(self):
        """Production's shape exactly: the Link set, and the display ending in `<br>`."""
        out = self.render(
            shipping_address="Sapphire Fountain-Billing",
            shipping_address_display="85 W 300 S<br>Bountiful, UT 84010<br>",
        )
        deliver_to = _fact(out, "DELIVER TO", "ORDER STATUS")
        self.assertIn("85 W 300 S<br>Bountiful, UT 84010</div>", deliver_to)
        self.assertNotIn("Sapphire Fountain-Billing", out)

    def test_deliver_to_falls_back_to_collection(self):
        for link in (None, "Sapphire Fountain-Billing"):
            with self.subTest(link=link):
                out = self.render(shipping_address=link, shipping_address_display=None)
                self.assertIn(
                    "Collection &mdash; see instructions below", _fact(out, "DELIVER TO", "ORDER STATUS")
                )

    # --- ORDER STATUS ----------------------------------------------------------------

    def status(self, **overrides):
        return _fact(self.render(**overrides), "ORDER STATUS", "APPROVED BY")

    def test_a_draft_says_it_is_not_an_order(self):
        """30 drafts on production; a supplier holding one must not ship against it."""
        out = self.status(docstatus=0, status="Draft")
        self.assertIn("Draft &mdash; not an order until approved", out)
        self.assertIn(ps.FAIL, out)

    def test_an_order_on_hold_says_do_not_ship(self):
        out = self.status(status="On Hold")
        self.assertIn("On hold &mdash; please do not ship yet", out)
        self.assertIn(ps.FAIL, out)

    def test_a_cancelled_order_says_do_not_supply(self):
        out = self.status(docstatus=2, status="Cancelled")
        self.assertIn("Cancelled &mdash; do not supply", out)
        self.assertIn(ps.FAIL, out)

    def test_every_other_submitted_order_is_issued(self):
        """ERPNext's words are ours: a supplier reads "Closed" (104 orders) as cancelled
        and "To Bill" (73) as a prompt to invoice."""
        for status in ("Closed", "To Bill", "To Receive and Bill", "To Receive", "Completed", "Delivered"):
            with self.subTest(status):
                out = self.status(status=status)
                self.assertEqual(_visible(out), "Issued")
                self.assertNotIn(status, out)
                self.assertNotIn(ps.FAIL, out)

    def test_the_label_is_still_order_status(self):
        self.assertIn(">ORDER STATUS<", self.render())
        self.assertNotIn("{{ doc.status }}", _NAMESPACE["_HTML"])

    # --- payment terms ---------------------------------------------------------------

    def test_net_30_is_printed_once(self):
        """The template's name is also its one schedule row's label."""
        out = self.render(
            payment_terms_template="Net 30",
            payment_schedule=[
                _Doc(payment_term="Net 30", description=None, payment_amount=106.25, due_date="2026-09-13")
            ],
        )
        self.assertEqual(out.count("Net 30"), 1)
        self.assertIn("USD 106.25 due 2026-09-13", out)

    def test_a_row_label_that_says_something_else_is_kept(self):
        out = self.render(
            payment_terms_template="Deposit and Balance",
            payment_schedule=[
                _Doc(payment_term="50% Deposit", description=None, payment_amount=53.13, due_date=None),
                _Doc(payment_term="Balance", description=None, payment_amount=53.12, due_date=None),
            ],
        )
        self.assertIn("Deposit and Balance", out)
        self.assertIn("50% Deposit &mdash; USD 53.13", out)
        self.assertIn("Balance &mdash; USD 53.12", out)

    # --- the line table --------------------------------------------------------------

    def line(self, **fields):
        row = dict(
            item_code="ITEM-1",
            item_name="Pump",
            description=None,
            qty=1.0,
            uom="Nos",
            rate=1.0,
            amount=1.0,
            project="PRJ-00001",
        )
        row.update(fields)
        return self.render(items=[_Doc(**row)]).split("<tbody>", 1)[1].split("</tbody>", 1)[0]

    def test_a_quantity_prints_as_a_person_writes_it(self):
        out = self.line(qty=12.0)
        self.assertIn(">12</td>", out)
        self.assertNotIn("12.0", out)
        self.assertIn(">62.5</td>", self.line(qty=62.5))

    def test_nos_prints_as_ea(self):
        self.assertIn(">ea</td>", self.line(uom="Nos"))
        self.assertIn(">Ft</td>", self.line(uom="Ft"))
        self.assertNotIn("None", self.line(uom=None))

    def test_a_plain_text_description_keeps_its_line_breaks(self):
        out = self.line(description="Submersible pump\n50 ft cord & bracket")
        self.assertIn("Submersible pump<br>50 ft cord &amp; bracket", out)

    def test_description_markup_passes_through(self):
        self.assertIn(
            "<b>from the item master</b>", self.line(description="<p>Markup <b>from the item master</b></p>")
        )
        self.assertIn("{{ ps_rich(row.description) }}", _NAMESPACE["_HTML"])
        self.assertNotIn("{{ row.description }}", _NAMESPACE["_HTML"])

    def test_no_description_falls_back_to_the_escaped_item_name(self):
        self.assertIn("Pump &amp; vault", self.line(item_name="Pump & vault", description=None))

    def test_the_item_code_is_kept_and_never_wraps(self):
        """Receiving and the supplier's counter staff work from it."""
        self.assertIn('white-space:nowrap;">{{ row.item_code | e }}</td>', _NAMESPACE["_HTML"])
        self.assertIn(">Item</th>", _NAMESPACE["_HTML"])

    # --- totals ----------------------------------------------------------------------

    def test_the_totals_use_the_shared_print_style_constants(self):
        """The one set of totals styles, with the inline `!important` frappe's print CSS
        demands; no module-local copy left to drift."""
        html = _NAMESPACE["_HTML"]
        for style in (ps.TOTAL_SPACER, ps.TOTAL_LABEL, ps.TOTAL_VALUE, ps.GRAND):
            with self.subTest(style[:30]):
                self.assertIn(style, html)
        for local in ("_TOTAL_LABEL_TD", "_TOTAL_VALUE_TD", "_GRAND_TD"):
            with self.subTest(local):
                self.assertNotIn(local, _NAMESPACE)


class TestTheHeaderCarriesOurContactDetails(unittest.TestCase):
    """A supplier holding this PDF must be able to reach us without the buyer's inbox.

    The letter head cannot do it: `Sapphire Fountains Default` is a right-aligned logo
    and nothing else -- no address, no phone, empty footer.
    """

    def setUp(self):
        try:
            import jinja2
        except ImportError:  # pragma: no cover
            self.skipTest("jinja2 not installed")

    def render(self, letter_head="<div>LETTERHEAD</div>", **overrides):
        return _render(_sample(**overrides), letter_head=letter_head)

    def test_no_placeholder_survives_composition(self):
        """A typo in any marker prints `__COMPANY_PHONE__` to a supplier. Three
        substitutions happen now that the block is shared: the block itself, the
        doctype's address fieldname, and the two constants inside it."""
        for marker in ("__CONTACT_BLOCK__", "__ADDRESS_FIELD__", "__COMPANY_"):
            with self.subTest(marker):
                self.assertNotIn(marker, _NAMESPACE["_HTML"])

    def test_it_reads_this_doctypes_address_field(self):
        """Purchase Order has no `company_address_display` — the sales doctypes' name for
        the same thing. Wiring the wrong one prints nothing and raises nothing."""
        self.assertEqual(_NAMESPACE["ADDRESS_FIELD"], "billing_address_display")
        self.assertIn("doc.billing_address_display", _NAMESPACE["_HTML"])
        self.assertNotIn("company_address_display", _NAMESPACE["_HTML"])

    def test_the_phone_is_printed(self):
        self.assertIn(COMPANY_PHONE, self.render())

    def test_the_phone_has_no_data_source_so_it_is_never_conditional(self):
        """`Company.phone_no` and `Address.phone` are both null on this site. If the
        number is ever wrapped in an `{% if %}`, it prints on nothing."""
        self.assertIn(COMPANY_PHONE, self.render(billing_address_display=None))

    def test_the_document_address_wins_when_it_has_one(self):
        """147 of 157 POs carry `billing_address_display`; it is the truth for those."""
        out = self.render()
        self.assertIn("85 W 300 S<br>\nBountiful, UT 84010", out)

    def test_the_constant_covers_a_document_with_no_billing_address(self):
        """The other ten. A blank address block is the failure this guards."""
        out = self.render(billing_address_display=None)
        self.assertIn(COMPANY_ADDRESS_HTML, out)

    def test_the_address_is_never_blank(self):
        for label, kwargs in (
            ("with billing address", {}),
            ("without", {"billing_address_display": None}),
            ("empty string", {"billing_address_display": ""}),
        ):
            with self.subTest(label):
                self.assertIn("Bountiful, UT 84010", self.render(**kwargs))

    def test_the_wordmark_is_drawn_and_the_letter_head_is_not(self):
        """Since v1.495.0 `print_style.letterhead()` inlines the wordmark itself, so the
        site's Letter Head (a bare logo) must NOT also render: two logos on one page is
        the new version of the old unbranded-for-a-month bug."""
        self.assertNotIn("{{ letter_head }}", _NAMESPACE["_HTML"])
        self.assertIn("<svg", self.render())
        self.assertNotIn("LETTERHEAD", self.render())
        self.assertEqual(_NAMESPACE["_HTML"].count("linear-gradient(90deg"), 2, "a stripe top and bottom")
        self.assertIn("@font-face", _NAMESPACE["_HTML"])

    def test_it_renders_without_a_letter_head(self):
        out = self.render(letter_head=None)
        self.assertIn(COMPANY_PHONE, out)
        self.assertIn("ITEM-1", out)

    def test_nothing_is_left_unrendered(self):
        self.assertNotIn("{{", self.render())

    def test_no_unsafe_css(self):
        """The PDF backend on this host does not lay out flexbox or grid reliably, and
        the contact block is a two-cell table for that reason."""
        squashed = _NAMESPACE["_HTML"].replace(" ", "")
        self.assertNotIn("display:flex", squashed)
        self.assertNotIn("display:grid", squashed)


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


def _shipped_format_names():
    """Every Print Format this app ships: each `*_FORMAT` / `*_PF` string constant in a
    `setup*print_format*.py` module, plus the Print Format fixtures."""
    names = set()
    for path in APP.rglob("setup*print_format*.py"):
        if "__pycache__" in path.parts:
            continue
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and re.search(r"(_FORMAT|_PF)$", node.targets[0].id)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                names.add(node.value.value)
    names.update(row["name"] for row in json.loads(PRINT_FORMAT_FIXTURES.read_text(encoding="utf-8")))
    return names


class TestTheSalesInvoiceCleanup(unittest.TestCase):
    """Nik: "clean up all the print formats for Sales Invoice and make the default of the
    print formats these new ones" (v1.533.0)."""

    EXPECTED = {
        # Broken today: TemplateNotFoundError, a blank preview.
        "Sales Invoice Print",
        "Sales Invoice PD Format v2",
        "Sales Order PD v2",
        # A leftover JS format; 0 POS Profiles, 0 POS invoices.
        "Point of Sale",
        # Superseded by Sales Invoice - Sapphire.
        "Sales Invoice Standard",
        "Sales Invoice with Item Image",
        "Sales Invoice Return",
        "Sales Auditing Voucher",
        # Regional, already disabled; listed so they stay so.
        "Detailed Tax Invoice",
        "Simplified Tax Invoice",
        "Tax Invoice",
    }

    def test_the_list_is_exactly_the_agreed_set(self):
        listed = literal(SETUP, "SUPERSEDED_SALES_FORMATS")
        self.assertEqual(set(listed), self.EXPECTED)
        self.assertEqual(len(listed), len(set(listed)), "a name listed twice")

    def test_the_quotation_and_sales_order_stock_formats_are_left_alone(self):
        """Not asked for, and they still render."""
        listed = set(literal(SETUP, "SUPERSEDED_SALES_FORMATS"))
        for name in (
            "Quotation Standard",
            "Quotation with Item Image",
            "Sales Order Standard",
            "Sales Order with Item Image",
        ):
            with self.subTest(name):
                self.assertNotIn(name, listed)

    def test_no_format_this_app_ships_is_on_any_disable_list(self):
        """Disabling one of our own would take it out of the dropdown on every migrate."""
        shipped = _shipped_format_names()
        # The discovery must actually find them, or this test passes on nothing.
        for name in (
            KEEP,
            "Sales Invoice - Sapphire",
            "Purchase Invoice - Sapphire",
            "Maintenance Record Print",
        ):
            self.assertIn(name, shipped)
        for listname in (
            "SUPERSEDED_PURCHASE_ORDER_FORMATS",
            "SUPERSEDED_PROCUREMENT_FORMATS",
            "SUPERSEDED_SALES_FORMATS",
        ):
            with self.subTest(listname):
                self.assertEqual(shipped & set(literal(SETUP, listname)), set())

    def test_the_disable_pass_covers_the_sales_list(self):
        source = SETUP.read_text(encoding="utf-8")
        start = source.index("def disable_superseded_print_formats(")
        end = source.index("\ndef ", start + 1)
        self.assertIn("SUPERSEDED_SALES_FORMATS", source[start:end])

    def test_the_sales_doctypes_default_to_their_sapphire_formats(self):
        """Exactly the shape of the Purchase Order's own default, which has worked since
        v1.519.0 -- and naming the formats the sales module actually ships."""
        rows = {row["name"]: row for row in json.loads(PROPERTY_SETTERS.read_text(encoding="utf-8"))}
        reference = rows["Purchase Order-main-default_print_format"]
        for doctype, constant in (
            ("Quotation", "QUOTATION_FORMAT"),
            ("Sales Order", "SALES_ORDER_FORMAT"),
            ("Sales Invoice", "SALES_INVOICE_FORMAT"),
        ):
            with self.subTest(doctype):
                name = f"{doctype}-main-default_print_format"
                self.assertIn(name, rows)
                expected = dict(reference, doc_type=doctype, name=name, value=f"{doctype} - Sapphire")
                self.assertEqual(rows[name], expected)
                self.assertEqual(rows[name]["value"], literal(SALES_SETUP, constant))

    def test_the_fixture_export_keeps_them(self):
        """hooks.py exports every Property Setter with is_system_generated = 0, minus a short
        exclusion list; one of these on that list would silently drop out of the fixtures on
        the next export."""
        hooks = HOOKS.read_text(encoding="utf-8")
        block = hooks[hooks.index('"dt": "Property Setter"') :]
        block = block[: block.index("},")]
        self.assertIn('["is_system_generated", "=", 0]', block)
        for doctype in ("Quotation", "Sales Order", "Sales Invoice"):
            with self.subTest(doctype):
                self.assertNotIn(f"{doctype}-main-default_print_format", block)


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
        # This function's body only: past its end the next function's docstring talks
        # about `doc.save()`, which is not a call this pass makes.
        body = source[start : start + 1400].split("\ndef ", 1)[0]
        self.assertIn('frappe.db.set_value("Print Format"', body)
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
