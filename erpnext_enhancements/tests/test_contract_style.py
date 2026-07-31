"""Bench-free unit tests for the contract's branded chrome (``contract_style``).

Stubs a minimal ``frappe`` (no site/bench) so the pure string-building runs under
plain unittest. The stub is installed in ``setUpModule`` (execution time), not at
import, so it never fools the bench-only suites' ``import frappe`` skip-guards —
the same discipline as ``test_contract_esign``.

The cases here are the ones where a mistake is expensive and *silent* — every one
of them would ship a contract that looks fine in the desk and wrong on the
customer's copy:

* the footer only ever gets its page numbers because frappe's
  ``pdf_header_footer.html`` runs a ``subst()`` script over elements with class
  ``page``/``topage`` inside ``#footer-html``. Rename either hook and the footer
  keeps rendering, just with the numbers blank;
* ``.contract-doc #footer-html { display: none }`` is the only thing keeping that
  footer off the screen surfaces, where the div is still sitting in the body.
  Lose the rule and every viewer prints a stray footer mid-document;
* ``wrap()`` must not touch the body. For a signed contract the body is the
  executed instrument, and rewriting so much as its whitespace would defeat the
  snapshot that makes the signature evidence;
* a missing logo file must degrade to an unbranded contract, never to a 500 on
  the public signing page.

Run: python -m unittest erpnext_enhancements.tests.test_contract_style
"""

import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP_DIR = Path(__file__).resolve().parents[1]
FIXTURE = APP_DIR / "fixtures" / "print_format.json"

contract_style = None


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")

    def get_app_path(app, *parts):
        return str(APP_DIR.joinpath(*parts))

    frappe.get_app_path = get_app_path

    # ``erpnext_enhancements.project_enhancements.__init__`` imports ``_`` and
    # decorates at import time, so importing anything beneath it needs these.
    frappe._ = lambda s: s

    def whitelist(*dargs, **dkwargs):
        def wrap(fn):
            return fn

        return wrap(dargs[0]) if dargs and callable(dargs[0]) else wrap

    frappe.whitelist = whitelist

    utils = types.ModuleType("frappe.utils")
    utils.escape_html = lambda s: (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    utils.cstr = lambda s: "" if s is None else str(s)
    frappe.utils = utils

    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils


def setUpModule():
    global contract_style
    _install_frappe_stub()
    sys.modules.pop("erpnext_enhancements.project_enhancements.contract_style", None)
    from erpnext_enhancements.project_enhancements import contract_style as _style

    contract_style = _style


class TestLogo(unittest.TestCase):
    def setUp(self):
        contract_style.logo_svg.cache_clear()

    def test_returns_inline_svg_without_the_xml_prolog(self):
        """Inline <svg>, not <img src>: Qt WebKit renders the latter unreliably,
        and an XML prolog part-way through an HTML document is a parse error."""
        svg = contract_style.logo_svg()
        self.assertTrue(svg.startswith("<svg"), svg[:60])
        self.assertNotIn("<?xml", svg)
        self.assertIn("</svg>", svg)

    def test_carries_the_brand_navy(self):
        self.assertIn("#00263E", contract_style.logo_svg().upper())

    def test_dimensions_are_restamped_on_the_root_element_only(self):
        """The asset ships at its natural 276x100 and wkhtmltopdf lays inline SVG
        out from the attributes, so leaving them prints an oversized logo."""
        svg = contract_style.logo_svg()
        root = svg[: svg.index(">") + 1]
        self.assertIn(f'width="{contract_style.LOGO_WIDTH}"', root)
        self.assertIn(f'height="{contract_style.LOGO_HEIGHT}"', root)
        self.assertNotIn('width="276"', root)
        self.assertNotIn('height="100"', root)

    def test_is_cached(self):
        self.assertIs(contract_style.logo_svg(), contract_style.logo_svg())

    def test_missing_asset_degrades_instead_of_raising(self):
        """A contract must still render — and the public signing page must still
        respond — when the logo cannot be read."""
        original = contract_style.LOGO_PATH
        try:
            contract_style.LOGO_PATH = ("public", "images", "no-such-file.svg")
            contract_style.logo_svg.cache_clear()
            self.assertEqual(contract_style.logo_svg(), "")
            # And the letterhead still opens the document, in type.
            self.assertIn("Sapphire Fountains", contract_style.letterhead_html())
        finally:
            contract_style.LOGO_PATH = original
            contract_style.logo_svg.cache_clear()


class TestLetterhead(unittest.TestCase):
    def test_is_a_single_ct_letterhead_element(self):
        """The stylesheet finds the document title by adjacent sibling
        (`.ct-letterhead + h3`), so the letterhead must be exactly one element —
        a second top-level node would push the title out of reach."""
        html = contract_style.letterhead_html()
        self.assertTrue(html.startswith('<div class="ct-letterhead">'))
        self.assertTrue(html.endswith("</div>"))

    def test_carries_no_address(self):
        """Six of the eight templates print their own company/address block; a
        second one under the wordmark reads as a mistake."""
        self.assertNotIn("85 W 300 S", contract_style.letterhead_html())


class TestFooter(unittest.TestCase):
    def test_uses_the_ids_and_classes_frappes_pdf_pipeline_looks_for(self):
        """frappe.utils.pdf extracts #footer-html into wkhtmltopdf's
        --footer-html, and its wrapper's subst() fills .page / .topage from the
        query string. These three names are the whole contract."""
        html = contract_style.footer_html({"name": "SF-MAINT-2026-0001"})
        self.assertIn('id="footer-html"', html)
        self.assertIn('class="page"', html)
        self.assertIn('class="topage"', html)

    def test_styling_is_inline(self):
        """The extracted footer is rendered as its own standalone document, so it
        cannot rely on the print format's stylesheet reaching it."""
        html = contract_style.footer_html({"name": "SF-MAINT-2026-0001"})
        self.assertIn("font-family:", html)
        self.assertIn("#00263E", html.upper())

    def test_names_the_contract(self):
        html = contract_style.footer_html({"name": "SF-MAINT-2026-0001"})
        self.assertIn("SF-MAINT-2026-0001", html)

    def test_falls_back_to_the_title_for_an_unsaved_draft(self):
        html = contract_style.footer_html({"name": None, "title": "Acme maintenance"})
        self.assertIn("Acme maintenance", html)

    def test_escapes_the_label(self):
        """The label lands in an unescaped HTML context; contract titles are
        user-supplied."""
        html = contract_style.footer_html({"name": '<img src=x onerror="alert(1)">'})
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)

    def test_survives_no_document(self):
        html = contract_style.footer_html(None)
        self.assertIn('id="footer-html"', html)


class TestWrap(unittest.TestCase):
    BODY = "<h3><b>MAINTENANCE SERVICES AGREEMENT</b></h3>\n<p>  spaced  </p>"

    def test_letterhead_then_body_then_footer(self):
        out = contract_style.wrap(self.BODY, {"name": "SF-MAINT-2026-0001"})
        self.assertLess(out.index("ct-letterhead"), out.index("MAINTENANCE SERVICES"))
        self.assertLess(out.index("MAINTENANCE SERVICES"), out.index("footer-html"))

    def test_body_passes_through_byte_for_byte(self):
        """For a signed contract the body is the executed instrument. Normalising
        it — even its whitespace — would break the snapshot the signature attests
        to."""
        out = contract_style.wrap(self.BODY, None)
        self.assertIn(self.BODY, out)

    def test_letterhead_is_adjacent_to_the_body_title(self):
        """`.ct-letterhead + h3` styles the document title, and the adjacent
        sibling combinator tolerates no element between the two."""
        out = contract_style.wrap(self.BODY, None)
        self.assertIn('<div class="ct-letterhead">', out)
        self.assertIn("</div><h3>", out)

    def test_empty_body_still_produces_chrome(self):
        out = contract_style.wrap("", None)
        self.assertIn("ct-letterhead", out)
        self.assertIn("footer-html", out)


class TestPrintFormatFixture(unittest.TestCase):
    """Static guards on the fixture. It is the single source of truth for the
    document's look, and nothing else in the bench-free suite would notice it
    losing a rule."""

    @classmethod
    def setUpClass(cls):
        records = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.pf = next(r for r in records if r.get("name") == "Project Contract Print")

    def test_wrapper_calls_the_chrome(self):
        self.assertIn("doc.letterhead_html()", self.pf["html"])
        self.assertIn("doc.footer_html()", self.pf["html"])
        self.assertIn("doc.executed_body()", self.pf["html"])

    def test_letterhead_precedes_the_body_in_the_wrapper(self):
        html = self.pf["html"]
        self.assertLess(html.index("letterhead_html"), html.index("executed_body"))
        self.assertLess(html.index("executed_body"), html.index("footer_html"))

    def test_footer_is_hidden_on_screen(self):
        """The one rule keeping the footer off every non-PDF surface. It cannot
        match inside the extracted footer document, which is what lets the same
        markup be hidden here and visible there."""
        self.assertIn(".contract-doc #footer-html", self.pf["css"])
        self.assertIn("display: none", self.pf["css"])

    def test_every_rule_is_scoped_to_the_document(self):
        """This stylesheet is published document-wide by contract_viewer.js and by
        the public signing page. An unscoped selector would restyle the desk
        around the agreement."""
        body = self.pf["css"]
        # Strip comments, then check each selector list.
        while "/*" in body:
            start = body.index("/*")
            end = body.index("*/", start) + 2
            body = body[:start] + body[end:]
        for block in body.split("}"):
            selectors = block.split("{")[0].strip()
            if not selectors:
                continue
            for selector in selectors.split(","):
                selector = selector.strip()
                if selector:
                    self.assertTrue(
                        selector.startswith(".contract-doc"),
                        f"unscoped selector in the contract stylesheet: {selector!r}",
                    )


if __name__ == "__main__":
    unittest.main()
