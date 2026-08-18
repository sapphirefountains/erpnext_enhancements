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
  ``Drop Shipping Format`` ship with ERPNext. **Standard formats re-sync from
  their app's JSON on migrate**, which is the same fact that forced
  ``ensure_chrome_pdf_generator`` to be an every-migrate hook rather than the
  one-off data fix somebody tried first. A patch that deleted them would appear
  to work and undo itself at the next ``bench migrate`` — the worst shape of
  failure, because nobody looks again until they print a PO weeks later.

So the split is the design, and this pins it. Getting it backwards is invisible
until production.

Bench-free: reads the sources as text, and execs the setup module against a `frappe`
stub to get at the composed template. Own CI step, because that stub is process-wide.

Run: python -m unittest erpnext_enhancements.tests.test_purchase_order_print_formats
"""

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The two constants live in `company_contact` now, shared with the sales formats.
# Importable without a bench: that module is pure strings and has no frappe import.
from erpnext_enhancements.enhancements_core.company_contact import (
    COMPANY_ADDRESS_HTML,
    COMPANY_PHONE,
)

APP = REPO_ROOT / "erpnext_enhancements"
SETUP = APP / "enhancements_core/setup_print_formats.py"
PATCH = APP / "patches/purge_purchase_order_print_formats.py"
PATCHES_TXT = APP / "patches.txt"
HOOKS = APP / "hooks.py"

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


def _render(doc, letter_head="<div>LETTERHEAD</div>", project_name=None):
    """Render the composed template with every jinja method the hook registers.

    One helper rather than three copies: a template gaining a method it can call is a
    template that raises `UndefinedError` at render time in every test that forgot to pass
    it, and the useful failure is the one that says the *format* is wrong.

    The methods are the real ones, not stand-ins. They are what decide which project the
    sheet names when the header and item rows disagree, and what makes the printed
    identifier the same string as the PDF's filename; a stub answering either differently
    from production would make this suite worse than no suite.
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

    def test_the_letter_head_is_still_rendered(self):
        """The month-long bug: a custom_format template gets no letterhead injected."""
        self.assertIn("{{ letter_head }}", _NAMESPACE["_HTML"])
        self.assertIn("LETTERHEAD", self.render())

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
