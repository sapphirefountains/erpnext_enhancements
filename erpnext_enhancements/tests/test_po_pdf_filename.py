"""Bench-free unit tests for the Purchase Order PDF filename (ER-2026-256847).

Stubs a minimal ``frappe`` — including ``frappe.utils.print_format`` — so the override in
``erpnext_enhancements.po_pdf_filename`` runs under plain unittest. The stub is installed in
``setUpModule`` (execution time), not at import, so it never fools the bench-only suites'
``import frappe`` skip-guards. Own CI step for the same reason as every other suite here:
the stub is process-wide and would cross-talk if two suites shared an interpreter.

What is worth pinning, all of it invisible until production:

* **Every other doctype is untouched.** This override sits on the route the whole system
  prints through. A Sales Invoice whose filename changed would be a regression nobody asked
  for, delivered by a Purchase Order feature.
* **frappe's own function is actually called, once, with everything it was given.** The
  signature has to mirror frappe's because ``frappe.call`` matches the request's form_dict
  against it — a parameter missing here is a parameter silently dropped from the request.
* **A failure renaming never breaks the download.** The PDF has already been rendered by
  the time we touch the filename.
* **The naming rule itself**: order number first, project appended, nothing appended when
  there is no project (61 of 158 orders on production), and the header/row union rather
  than either field alone.

Run: python -m unittest erpnext_enhancements.tests.test_po_pdf_filename
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
HOOKS = APP / "hooks.py"

# Mutable state the frappe stub reads at call time.
STATE = {"calls": [], "docs": {}, "errors": [], "raise_on_get_doc": False}
po_pdf_filename = None


class _Doc:
    """Attribute access plus .get(), like a Frappe document.

    Deliberately not a dict subclass: ``doc.items`` is the line-item table, and on a dict
    subclass that resolves to the bound ``dict.items`` method instead of the rows.
    """

    def __init__(self, **fields):
        self.__dict__.update(fields)

    def __getattr__(self, key):
        return None

    def get(self, key, default=None):
        return self.__dict__.get(key, default)


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")

    def get_doc(doctype, name):
        if STATE["raise_on_get_doc"]:
            raise RuntimeError("document gone")
        return STATE["docs"][name]

    frappe.get_doc = get_doc
    frappe.whitelist = lambda *a, **kw: (lambda fn: fn)
    frappe.get_traceback = lambda: "traceback"
    frappe.log_error = lambda message, title=None: STATE["errors"].append(title)
    frappe.local = types.SimpleNamespace(response=types.SimpleNamespace(filename=None))

    utils = types.ModuleType("frappe.utils")
    utils.flt = lambda v, precision=None: float(v or 0)

    print_format = types.ModuleType("frappe.utils.print_format")

    def download_pdf(doctype, name, **kwargs):
        """Stands in for frappe's: records the call and sets the filename frappe would."""
        STATE["calls"].append((doctype, name, kwargs))
        frappe.local.response.filename = name.replace(" ", "-").replace("/", "-") + ".pdf"
        return "PDF-BYTES"

    print_format.download_pdf = download_pdf
    utils.print_format = print_format
    frappe.utils = utils

    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils
    sys.modules["frappe.utils.print_format"] = print_format


def setUpModule():
    global po_pdf_filename
    _install_frappe_stub()
    for module in (
        "erpnext_enhancements.po_pdf_filename",
        "erpnext_enhancements.procurement_project",
    ):
        sys.modules.pop(module, None)
    from erpnext_enhancements import po_pdf_filename as mod

    po_pdf_filename = mod


def _reset(docs=None, raise_on_get_doc=False):
    STATE.update(calls=[], docs=dict(docs or {}), errors=[], raise_on_get_doc=raise_on_get_doc)
    sys.modules["frappe"].local.response.filename = None


def _po(name="PO-2026-00262", project=None, row_projects=()):
    return _Doc(name=name, project=project, items=[_Doc(project=p) for p in row_projects])


def _filename():
    return sys.modules["frappe"].local.response.filename


class TestTheNamingRule(unittest.TestCase):
    """Pure, so it can be read without the override around it."""

    def name_for(self, **kwargs):
        return po_pdf_filename.purchase_order_filename(_po(**kwargs))

    def test_the_project_is_appended_to_the_order_number(self):
        self.assertEqual(self.name_for(project="PRJ-00706"), "PO-2026-00262-PRJ-00706.pdf")

    def test_the_order_number_leads(self):
        """A folder of these still sorts the way everyone already expects; the project is
        what you scan for once you are looking."""
        self.assertTrue(self.name_for(project="PRJ-00706").startswith("PO-2026-00262"))

    def test_no_project_means_no_suffix_at_all(self):
        """61 of the 158 orders on production carry none. `PO-2026-00262-none.pdf` would
        be a worse filename than the one we started with."""
        self.assertEqual(self.name_for(), "PO-2026-00262.pdf")
        self.assertEqual(
            self.name_for(project="", row_projects=("", None)), "PO-2026-00262.pdf"
        )

    def test_a_row_project_is_used_when_the_header_has_none(self):
        self.assertEqual(
            self.name_for(row_projects=("PRJ-00566",)), "PO-2026-00262-PRJ-00566.pdf"
        )

    def test_the_header_leads_and_the_rows_follow(self):
        self.assertEqual(
            self.name_for(project="PRJ-00706", row_projects=("PRJ-00566",)),
            "PO-2026-00262-PRJ-00706-PRJ-00566.pdf",
        )

    def test_the_same_project_on_header_and_rows_is_named_once(self):
        """The normal case on production, and the one a naive concatenation gets wrong:
        PO-2026-00262-PRJ-00706-PRJ-00706.pdf."""
        self.assertEqual(
            self.name_for(project="PRJ-00706", row_projects=("PRJ-00706", "PRJ-00706")),
            "PO-2026-00262-PRJ-00706.pdf",
        )

    def test_a_long_tail_of_projects_is_bounded(self):
        """No order on production spans even two, but a filename has to fit in a folder
        and on a taskbar, and "silently unbounded" is not a length."""
        out = self.name_for(row_projects=tuple(f"PRJ-0000{i}" for i in range(1, 7)))
        self.assertIn("plus-3", out)
        self.assertNotIn("PRJ-00004", out)

    def test_separators_cannot_reach_the_filename(self):
        """frappe applies this rule to the docname before putting it in
        Content-Disposition; the segment we append has to clear the same bar."""
        out = self.name_for(project="PRJ 1/2")
        self.assertNotIn("/", out.replace(".pdf", ""))
        self.assertEqual(out, "PO-2026-00262-PRJ-1-2.pdf")


class TestTheOverrideIsATransparentDecoration(unittest.TestCase):
    """It sits on the route every doctype in the system prints through."""

    def test_a_purchase_order_gets_the_project_appended(self):
        _reset(docs={"PO-2026-00262": _po(project="PRJ-00706")})
        po_pdf_filename.download_pdf("Purchase Order", "PO-2026-00262")
        self.assertEqual(_filename(), "PO-2026-00262-PRJ-00706.pdf")

    def test_every_other_doctype_is_left_exactly_as_frappe_named_it(self):
        """The regression this feature could plausibly ship: a Sales Invoice renamed by a
        Purchase Order change."""
        _reset(docs={})
        po_pdf_filename.download_pdf("Sales Invoice", "ACC-SINV-2026-00001")
        self.assertEqual(_filename(), "ACC-SINV-2026-00001.pdf")
        self.assertEqual(STATE["errors"], [])

    def test_frappes_own_function_does_the_work(self):
        _reset(docs={"PO-2026-00262": _po(project="PRJ-00706")})
        result = po_pdf_filename.download_pdf("Purchase Order", "PO-2026-00262")
        self.assertEqual(result, "PDF-BYTES")
        self.assertEqual(len(STATE["calls"]), 1)

    def test_every_argument_is_passed_through(self):
        """`frappe.call` matches the request's form_dict against this signature, so a
        parameter dropped here is a parameter dropped from the request -- a print format
        or letterhead the user chose and did not get."""
        _reset(docs={"PO-2026-00262": _po()})
        po_pdf_filename.download_pdf(
            "Purchase Order",
            "PO-2026-00262",
            format="Purchase Order - Sapphire",
            doc=None,
            no_letterhead=1,
            language="en",
            letterhead="Sapphire Fountains Default",
            pdf_generator="chrome",
        )
        _doctype, _name, kwargs = STATE["calls"][0]
        self.assertEqual(kwargs["format"], "Purchase Order - Sapphire")
        self.assertEqual(kwargs["no_letterhead"], 1)
        self.assertEqual(kwargs["language"], "en")
        self.assertEqual(kwargs["letterhead"], "Sapphire Fountains Default")
        self.assertEqual(kwargs["pdf_generator"], "chrome")

    def test_a_failure_renaming_never_costs_the_download(self):
        """The PDF has already been rendered by the time we touch the filename. Frappe's
        name still works; losing the project off the end of it is not worth a 500."""
        _reset(docs={}, raise_on_get_doc=True)
        result = po_pdf_filename.download_pdf("Purchase Order", "PO-2026-00262")
        self.assertEqual(result, "PDF-BYTES")
        self.assertEqual(_filename(), "PO-2026-00262.pdf")
        self.assertEqual(STATE["errors"], ["Purchase Order PDF filename"])


class TestItIsWiredUp(unittest.TestCase):
    def test_the_override_is_registered_against_frappes_method(self):
        """Without this line the module is dead code and the filename never changes."""
        hooks = HOOKS.read_text(encoding="utf-8")
        self.assertIn('"frappe.utils.print_format.download_pdf":', hooks)
        self.assertIn("erpnext_enhancements.po_pdf_filename.download_pdf", hooks)

    def test_the_weasyprint_route_is_left_alone(self):
        """A different method on a different generator, which this print format does not
        use. Overriding it would be scope we cannot test here."""
        self.assertNotIn("frappe.utils.weasyprint", HOOKS.read_text(encoding="utf-8"))

    def test_it_shares_the_project_rule_with_the_print_format(self):
        """Two things naming the job on the same document must not arrive at it two
        different ways."""
        source = (APP / "po_pdf_filename.py").read_text(encoding="utf-8")
        self.assertIn(
            "from erpnext_enhancements.procurement_project import purchase_order_projects",
            source,
        )


if __name__ == "__main__":
    unittest.main()
