"""Bench-free guards for the print design system (`print_style.py`) and the one Jinja
fixture format that consumes it at print time, `Maintenance Record Print`.

A print format fails in the worst possible way: the deploy succeeds, nothing logs, and
the defect is found by a customer holding the PDF — or, for a fixture, by the customer
whose service report `api/maintenance_workflow.py` attaches to an email on finalize.
Nothing compiles a fixture's html before that moment, so this suite does: it renders
the template against sample records under the same `ps_*` globals hooks.py registers.

What it pins, in order of how silently each would fail:

* every text colour clears AA on white, measured — the design system records three
  pairings that do not, and the one this app uses (sapphire fill) gets a navy label;
* the pillar records are complete and the stripe paints flat before it paints a
  gradient, so a renderer without gradients still shows the pillar's colour;
* the display font and the wordmark are in the repo and read as what they claim to be;
* every `ps_*` global the fixture calls is registered in hooks.py, and nothing
  un-prefixed from the module is — get_jinja_hooks would put it in every template;
* every table-cell style forces its geometry with an inline `!important` — frappe's
  own print stylesheet and the site's Print Style force theirs, and an ordinary inline
  style loses to both without a word;
* the content helpers (v1.535.0) print production-shaped values the way a person
  writes them: `8015550100` as `(801) 555-0100`, `1.0` as `1`, `Nos` as `ea`, an
  address without the break the US Address Template ends on, a line whose description
  only repeats its name as the name alone, a party block with no dangling breaks and
  no phone or email printed twice, and a red DRAFT on a draft;
* the fixture compiles, renders per-site and single-feature records, with and without
  a signature, with an out-of-range reading, with nothing but a header, and never
  prints `None` or a raw brace.

Run: python -m unittest erpnext_enhancements.tests.test_print_style
"""

import json
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements import print_style as ps  # frappe-free by design
from erpnext_enhancements.enhancements_core.company_contact import (
    COMPANY_ADDRESS_HTML,
    COMPANY_PHONE,
)

APP = REPO_ROOT / "erpnext_enhancements"
HOOKS = (APP / "hooks.py").read_text(encoding="utf-8")
FIXTURE = APP / "fixtures" / "print_format.json"


def _luminance(hex_value):
    channels = []
    for i in (1, 3, 5):
        c = int(hex_value[i : i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg="#ffffff"):
    lighter, darker = max(_luminance(fg), _luminance(bg)), min(_luminance(fg), _luminance(bg))
    return (lighter + 0.05) / (darker + 0.05)


class TestPalette(unittest.TestCase):
    def test_every_text_colour_clears_aa_on_white(self):
        for name in ("DEEP_SEA_BLUE", "BAHAMA_BLUE", "INK_700", "INK_900", "RED", "NAVY_800", "NAVY_900"):
            value = getattr(ps, name)
            with self.subTest(name):
                self.assertGreaterEqual(contrast(value), 4.5, f"{name} {value} is {contrast(value):.2f}:1")

    def test_the_fills_are_never_asked_to_carry_text(self):
        """fresh-blue is 3.0:1, teal 1.8:1 and yellow 1.7:1 on white. They appear in
        the module as fills and rules; a style string must not colour text with them."""
        for name in ("TH", "TD", "LABEL", "STRONG", "FAIL", "PAGE_STYLE"):
            style = getattr(ps, name)
            for fill in (ps.FRESH_BLUE, ps.TEAL, ps.YELLOW):
                self.assertNotIn(f"color:{fill}", style, f"{name} colours text with {fill}")

    def test_the_button_pairing_the_design_system_flags_is_fixed_here(self):
        self.assertGreaterEqual(contrast(ps.NAVY_900, ps.FRESH_BLUE), 4.5)
        self.assertLess(contrast(ps.OFF_WHITE, ps.FRESH_BLUE), 4.5, "the ban would be stale")


class TestPillars(unittest.TestCase):
    def test_four_pillars_and_a_neutral(self):
        self.assertEqual(set(ps.PILLARS), {"service", "build", "design", "rent"})
        for key, record in ps.PILLARS.items():
            with self.subTest(key):
                self.assertEqual(set(record), {"name", "open", "deep"})
                self.assertEqual(record["name"], record["name"].upper())
                self.assertGreaterEqual(contrast(record["deep"]), 4.5, f"{key}'s closing stop fails AA")
        self.assertEqual(ps.NEUTRAL["name"], "", "a neutral document names no pillar")

    def test_lookup_is_forgiving(self):
        self.assertIs(ps.pillar(None), ps.NEUTRAL)
        self.assertIs(ps.pillar(""), ps.NEUTRAL)
        self.assertIs(ps.pillar("not-a-pillar"), ps.NEUTRAL)
        self.assertIs(ps.pillar("SERVICE"), ps.PILLARS["service"])

    def test_the_stripe_paints_flat_before_it_paints_a_gradient(self):
        for key in (None, "service", "build", "design", "rent"):
            css = ps.stripe_css(key)
            with self.subTest(key):
                flat = ps.pillar(key)["open"]
                self.assertTrue(css.startswith(f"background-color:{flat}"), css)
                self.assertIn("background-image:linear-gradient(90deg,", css)
                self.assertLess(css.index("background-color"), css.index("background-image"))

    def test_the_gradient_opens_on_the_opening_stop(self):
        for key, record in ps.PILLARS.items():
            with self.subTest(key):
                self.assertIn(f"linear-gradient(90deg,{record['open']} 0%,{record['deep']} 100%)", ps.stripe_css(key))


class TestAssets(unittest.TestCase):
    def test_the_display_font_ships_in_the_repo(self):
        self.assertTrue(Path(ps.FONT_PATH).is_file(), ps.FONT_PATH)
        with open(ps.FONT_PATH, "rb") as handle:
            self.assertEqual(handle.read(4), b"wOF2", "not a woff2")

    def test_the_font_face_is_inlined_as_a_data_uri(self):
        css = ps.font_face_css()
        self.assertIn("@font-face", css)
        self.assertIn("'Big Noodle Titling'", css)
        self.assertIn("data:font/woff2;base64,", css)
        self.assertNotIn("/assets/", css, "a URL would make every PDF depend on the worker reaching the site")

    def test_the_wordmark_inlines_at_its_natural_ratio(self):
        svg = ps.logo_svg(170)
        self.assertTrue(svg.startswith("<svg"), svg[:60])
        self.assertNotIn("<?xml", svg)
        self.assertIn('width="170"', svg)
        self.assertIn('height="62"', svg)  # 170 * 100 / 276
        self.assertTrue(svg.rstrip().endswith("</svg>"))


class TestChrome(unittest.TestCase):
    def test_page_open_carries_font_and_stripe(self):
        html = ps.page_open("service")
        self.assertIn("@font-face", html)
        self.assertIn(f"background-color:{ps.FRESH_BLUE}", html)
        self.assertLess(html.index("<style>"), html.index("background-color"))

    def test_page_close_carries_the_running_line_and_the_second_stripe(self):
        html = ps.page_close("rent")
        self.assertIn("Sapphire Fountains, LLC", html)
        self.assertIn("www.sapphirefountains.com", html)
        self.assertIn(f"background-color:{ps.TEAL}", html)

    def test_header_cell_rule_takes_the_pillars_opening_stop(self):
        self.assertIn(f"border-bottom:2px solid {ps.BAHAMA_BLUE}", ps.th("build"))
        self.assertIn(f"border-bottom:2px solid {ps.DEEP_SEA_BLUE}", ps.th(None))
        self.assertIn("text-align:right", ps.th("service", right=True))
        for key in (None, "service", "build", "design", "rent"):
            self.assertNotIn("__OPEN__", ps.th(key))

    def test_letterhead_names_the_pillar_and_the_document(self):
        html = ps.letterhead("service", "VISIT REPORT", "Maintenance Visit Report", "META")
        self.assertIn("SERVICE &middot; VISIT REPORT", html)
        self.assertIn("Maintenance Visit Report", html)
        self.assertIn("META", html)
        self.assertIn("<svg", html)
        self.assertIn(COMPANY_PHONE, html)
        neutral = ps.letterhead(None, "TRAINING &middot; CERTIFICATE", "Certificate of Completion", "")
        self.assertIn(">TRAINING &middot; CERTIFICATE<", neutral, "an eyebrow that adds something stays")

    def test_an_eyebrow_that_only_repeats_the_title_is_dropped(self):
        """From v1.494.0 every neutral document printed its own name twice, one line
        apart -- `INVOICE` over `INVOICE`, the display face being all capitals -- because
        the eyebrow exists to name a pillar and a neutral document has none. Nik spotted
        it on 2026-09-24."""
        neutral = ps.letterhead(None, "QUOTATION", "Quotation", "")
        self.assertNotIn(">QUOTATION<", neutral)
        self.assertEqual(neutral.count("QUOTATION"), 0, neutral[-600:])
        self.assertIn(">Quotation</h1>", neutral)
        self.assertIn("margin:0 0 0", neutral, "no eyebrow, no gap above the title")

    def test_a_jinja_eyebrow_repeating_a_jinja_title_is_dropped(self):
        """The credit-note / debit-note pairs are Jinja; the comparison is on the raw
        strings, case-insensitively, so the pair counts as a repeat."""
        html = ps.letterhead(
            None,
            "{% if doc.is_return %}CREDIT NOTE{% else %}INVOICE{% endif %}",
            "{% if doc.is_return %}Credit Note{% else %}Invoice{% endif %}",
            "",
        )
        self.assertNotIn("CREDIT NOTE", html)
        self.assertNotIn(">INVOICE", html)

    def test_a_pillar_keeps_its_name_when_the_eyebrow_repeats_the_title(self):
        html = ps.letterhead("service", "VISIT REPORT", "Visit Report", "")
        self.assertIn(">SERVICE<", html)
        self.assertNotIn("&middot; VISIT REPORT", html)

    def _render_letterhead(self, html, doc):
        """Render under the ps_* globals hooks.py registers -- the document's address
        now goes through `ps_address`, so a bare Environment cannot render it."""
        from jinja2 import Environment

        env = Environment()
        env.globals.update(_globals())
        return env.from_string(html).render(doc=doc)

    def test_letterhead_address_prefers_the_document_and_falls_back(self):
        html = ps.letterhead(None, "X", "T", "", address_field="company_address_display")
        self.assertIn("doc.company_address_display", html)
        self.assertIn("ps_address(doc.company_address_display)", html)

        class Doc:
            company_address_display = "2 Other Street"

        rendered = self._render_letterhead(html, Doc())
        self.assertIn("2 Other Street", rendered)
        self.assertNotIn(COMPANY_ADDRESS_HTML, rendered)

        Doc.company_address_display = None
        rendered = self._render_letterhead(html, Doc())
        self.assertIn(COMPANY_ADDRESS_HTML, rendered)

        plain = ps.letterhead("service", "X", "T", "")
        self.assertIn(COMPANY_ADDRESS_HTML, plain)
        self.assertNotIn("{%", plain)

    def test_the_us_templates_trailing_break_is_not_a_blank_line(self):
        """Every company address on the site ends `<br>\\n` (the United States Address
        Template). Printed raw, followed by the letterhead's own `<br>`, it left a blank
        line between the address and our phone number."""

        class Doc:
            company_address_display = "85 W 300 S<br>\nBountiful, UT 84010<br>\n"

        rendered = self._render_letterhead(
            ps.letterhead(None, "X", "T", "", address_field="company_address_display"), Doc()
        )
        self.assertIn("Bountiful, UT 84010<br>" + COMPANY_PHONE, rendered)
        self.assertNotRegex(rendered, r"<br>\s*<br>")

    def test_print_safe_css_only(self):
        """The PDF backends on this host do not lay out flex or grid."""
        for name, html in (
            ("page_open", ps.page_open("service")),
            ("letterhead", ps.letterhead("service", "X", "T", "")),
            ("facts", ps.facts_open() + ps.fact("A", "b") + ps.facts_close()),
            ("signature", ps.signature_lines("A", "B")),
            ("page_close", ps.page_close("service")),
        ):
            squashed = html.replace(" ", "")
            with self.subTest(name):
                self.assertNotIn("display:flex", squashed)
                self.assertNotIn("display:grid", squashed)


class TestHooksRegistration(unittest.TestCase):
    def _registered(self):
        return set(re.findall(r'"erpnext_enhancements\.print_style\.(\w+)"', HOOKS))

    def test_every_ps_global_is_registered(self):
        public = {name for name in dir(ps) if name.startswith("ps_")}
        self.assertTrue(public, "no ps_* globals found")
        self.assertEqual(public - self._registered(), set(), "ps_* globals missing from hooks.py")

    def test_nothing_unprefixed_is_registered(self):
        """A bare `letterhead` or `pillar` in every Print Format's namespace is the
        same trade the ee_ prefix refused."""
        for name in self._registered():
            self.assertTrue(name.startswith("ps_"), name)

    def test_every_registered_name_exists(self):
        """A hooks.py path to a function that is not there is a migrate-time crash."""
        for name in self._registered():
            self.assertTrue(callable(getattr(ps, name, None)), f"hooks.py registers print_style.{name}")


# ---------------------------------------------------------------------------
# Cell styles.

CELL_STYLES = ("TH", "TH_RIGHT", "TD", "TD_RIGHT", "TOTAL_LABEL", "TOTAL_VALUE", "GRAND", "TOTAL_SPACER")


def _declarations(style):
    """`{property: value}` for an inline style string."""
    out = {}
    for decl in style.split(";"):
        if decl.strip():
            prop, _, value = decl.partition(":")
            out[prop.strip()] = value.strip()
    return out


class TestCellStylesOutrankTheStylesheets(unittest.TestCase):
    """Why every cell style carries `!important` on its geometry.

    Frappe appends two stylesheets to every print format, custom formats included:
    `templates/styles/standard.css` sets `.print-format td, .print-format th
    {padding: 6px !important; vertical-align: top !important}`, and this site's
    "Redesign" Print Style sets `padding: 10px !important` and
    `border-bottom-width: 1px !important` on `th`. A stylesheet `!important` beats an
    ordinary inline style, so before v1.535.0 not one of these paddings, alignments or
    the 2px header rule reached a page — the rows printed looser than designed and a
    six-line invoice pushed its totals onto page 2, with nothing anywhere to say so.
    Only an inline `!important` outranks a stylesheet `!important`, so dropping one of
    these is a silent regression that looks fine in every code review.
    """

    def test_every_cell_forces_its_padding(self):
        for name in CELL_STYLES:
            with self.subTest(name):
                decls = _declarations(getattr(ps, name))
                self.assertIn("padding", decls, f"{name} sets no padding; standard.css's 6px wins")
                self.assertTrue(decls["padding"].endswith("!important"), f"{name}: {decls['padding']}")

    def test_the_header_cell_forces_its_alignment_and_its_rule(self):
        for name in ("TH", "TH_RIGHT"):
            with self.subTest(name):
                decls = _declarations(getattr(ps, name))
                self.assertEqual(decls["vertical-align"], "bottom !important")
                self.assertEqual(decls["border-bottom"], "2px solid __OPEN__ !important")
        for key in (None, "service", "build", "design", "rent"):
            with self.subTest(key):
                decls = _declarations(ps.th(key))
                self.assertEqual(decls["border-bottom"], f"2px solid {ps.pillar(key)['open']} !important")
                self.assertEqual(decls["padding"], "5px 8px !important")

    def test_the_body_cell_forces_its_alignment_and_its_hairline(self):
        for name in ("TD", "TD_RIGHT"):
            with self.subTest(name):
                decls = _declarations(getattr(ps, name))
                self.assertEqual(decls["vertical-align"], "top !important")
                self.assertEqual(decls["border-bottom"], f"1px solid {ps.BORDER_100} !important")
        self.assertEqual(_declarations(ps.TD_RIGHT)["text-align"], "right")
        self.assertEqual(ps.ps_td(), ps.TD)
        self.assertEqual(ps.ps_td(True), ps.TD_RIGHT)
        self.assertEqual(ps.ps_style("td"), ps.TD)

    def test_the_totals_are_borderless_by_force(self):
        """A site that switches Print Style must not grow rules between the totals."""
        for name in ("TOTAL_LABEL", "TOTAL_VALUE", "GRAND", "TOTAL_SPACER"):
            with self.subTest(name):
                self.assertEqual(_declarations(getattr(ps, name))["border"], "0 !important")
        self.assertEqual(_declarations(ps.GRAND)["border-top"], f"2px solid {ps.DEEP_SEA_BLUE} !important")
        self.assertEqual(_declarations(ps.TOTAL_SPACER)["padding"], "0 !important")
        self.assertEqual(_declarations(ps.TOTAL_VALUE)["white-space"], "nowrap")

    def test_every_important_is_a_whole_declaration(self):
        """`!important` must end a `property:value` pair; a stray one after a `;` is
        dropped by the parser and silently takes nothing with it."""
        for name in CELL_STYLES:
            for decl in getattr(ps, name).split(";"):
                if "!important" in decl:
                    with self.subTest(f"{name}: {decl}"):
                        self.assertRegex(decl.strip(), r"^[a-z-]+:[^:!]+ !important$")

    def test_colour_and_weight_are_left_ordinary(self):
        """Neither stylesheet forces them, so nothing here needs to."""
        for name in CELL_STYLES:
            decls = _declarations(getattr(ps, name))
            for prop in ("color", "font-weight"):
                if prop in decls:
                    with self.subTest(f"{name}.{prop}"):
                        self.assertNotIn("!important", decls[prop])


# ---------------------------------------------------------------------------
# Content helpers.


class TestEscapeHtml(unittest.TestCase):
    def test_the_five_characters(self):
        self.assertEqual(ps.escape_html("""<a href="x">Tom & Jerry's</a>"""),
                         "&lt;a href=&quot;x&quot;&gt;Tom &amp; Jerry&#x27;s&lt;/a&gt;")

    def test_an_entity_is_escaped_not_trusted(self):
        self.assertEqual(ps.escape_html("&lt;"), "&amp;lt;")

    def test_non_strings(self):
        self.assertEqual(ps.escape_html(12.5), "12.5")


class TestAddressHtml(unittest.TestCase):
    def test_the_us_template_trailing_break_is_trimmed(self):
        """The United States Address Template ends every address with `<br>` and a
        newline -- 1,629 of 1,629 Sales Invoices carry one."""
        self.assertEqual(
            ps.address_html("85 W 300 S<br>\nBountiful, UT 84010<br>\n"), "85 W 300 S<br>\nBountiful, UT 84010"
        )

    def test_leading_breaks_and_nbsp_are_trimmed(self):
        self.assertEqual(ps.address_html("<br>\n&nbsp;<BR/> 85 W 300 S<br />&nbsp; "), "85 W 300 S")

    def test_internal_markup_is_the_templates_and_is_kept(self):
        self.assertEqual(ps.address_html("<b>Suite 4</b><br>85 W 300 S<br>"), "<b>Suite 4</b><br>85 W 300 S")

    def test_empty(self):
        for value in (None, "", "<br>", "&nbsp;", "  <br>\n  "):
            with self.subTest(value):
                self.assertEqual(ps.address_html(value), "")

    def test_the_jinja_global_is_the_same_function(self):
        self.assertEqual(ps.ps_address("X<br>\n"), "X")


class TestPlainText(unittest.TestCase):
    def test_tags_dropped_and_whitespace_collapsed(self):
        self.assertEqual(ps.plain_text('<div class="ql-editor"><p>Pump</p>\n<p>Motor</p></div>'), "Pump Motor")
        self.assertEqual(ps.plain_text("a<br>b&nbsp;&nbsp;c"), "a b c")

    def test_entities_read_as_the_characters_they_print(self):
        """Visible text: `&quot;` is an inch mark on the page, so it is one here."""
        self.assertEqual(ps.plain_text("2&quot; JET &amp; Owner&#x27;s &lt;1&gt;"), "2\" JET & Owner's <1>")

    def test_empty(self):
        self.assertEqual(ps.plain_text(None), "")
        self.assertEqual(ps.plain_text(""), "")


class TestFormatPhone(unittest.TestCase):
    def test_ten_bare_digits(self):
        """How this site stores about half its numbers."""
        self.assertEqual(ps.format_phone("8015550100"), "(801) 555-0100")

    def test_already_formatted_north_american_shapes(self):
        for value in ("801-555-0100", "(801) 555-0100", "801.555.0100", " 801 555 0100 ",
                      "+1 801-555-0100", "1-801-555-0100", "+18015550100", "+1 (801)-555-0100"):
            with self.subTest(value):
                self.assertEqual(ps.format_phone(value), "(801) 555-0100")

    def test_an_extension_prints_as_stored(self):
        for value in ("801-555-0100 ext 12", "(801) 555-0100 x12", "8015550100 ext. 4"):
            with self.subTest(value):
                self.assertEqual(ps.format_phone(value), value)

    def test_an_international_number_prints_as_stored(self):
        """A reformat that guessed wrong would print a number nobody can dial. `+65 6123
        4567` (Singapore) and `+49 89 123456` (Munich) are ten digits each -- the count
        alone would make them `(656) 123-4567`, so a `+` that is not `+1` settles it."""
        for value in ("+44 20 7946 0958", "+52 55 1234 5678", "+65 6123 4567", "+49 89 123456"):
            with self.subTest(value):
                self.assertEqual(ps.format_phone(value), value)

    def test_a_foreign_number_without_its_plus_prints_as_stored(self):
        """A Singapore contact on this site is stored `65-688-000-88`: ten digits, no `+`.
        Only North American grouping is reshaped."""
        for value in ("65-688-000-88", "44 20 7946 0958", "852-2345-6789"):
            with self.subTest(value):
                self.assertEqual(ps.format_phone(value), value)

    def test_every_north_american_shape_on_production_is_reshaped(self):
        """The shapes production actually holds (2026-09-24), each to the same form."""
        for value, expected in (
            ("(208)-283-2638", "(208) 283-2638"),
            ("786-5361357", "(786) 536-1357"),
            ("(801)5968500", "(801) 596-8500"),
            ("1 (650) 353-0758", "(650) 353-0758"),
            ("+1-8012680093", "(801) 268-0093"),
            ("888) 885-0228", "(888) 885-0228"),
            ("954. 579-9476", "(954) 579-9476"),
            ("1800 407 6657", "(800) 407-6657"),
            ("13852899055", "(385) 289-9055"),
            ("\t8016826997", "(801) 682-6997"),
        ):
            with self.subTest(value):
                self.assertEqual(ps.format_phone(value), expected)

    def test_anything_else_prints_as_stored(self):
        for value in ("555-0100", "911", "28015550100", "Front desk", "801-555-010"):
            with self.subTest(value):
                self.assertEqual(ps.format_phone(value), value)

    def test_empty(self):
        for value in ("", None, "   "):
            with self.subTest(value):
                self.assertEqual(ps.format_phone(value), "")

    def test_the_jinja_global_escapes(self):
        self.assertEqual(ps.ps_phone("8015550100"), "(801) 555-0100")
        self.assertEqual(ps.ps_phone("<b>call</b>"), "&lt;b&gt;call&lt;/b&gt;")
        self.assertEqual(ps.ps_phone(None), "")


class TestQtyText(unittest.TestCase):
    def test_whole_numbers_lose_the_float(self):
        """`{{ row.qty }}` printed `1.0` on every line of every format."""
        self.assertEqual(ps.qty_text(1.0), "1")
        self.assertEqual(ps.qty_text(2.0), "2")
        self.assertEqual(ps.qty_text(-12.0), "-12")
        self.assertEqual(ps.qty_text(0), "0")

    def test_thousands_are_grouped(self):
        self.assertEqual(ps.qty_text(1200), "1,200")
        self.assertEqual(ps.qty_text(1234567.5), "1,234,567.5")

    def test_fractions_keep_three_places_and_no_trailing_zeros(self):
        self.assertEqual(ps.qty_text(2.5), "2.5")
        self.assertEqual(ps.qty_text(0.25), "0.25")
        self.assertEqual(ps.qty_text(0.3333333), "0.333")
        self.assertEqual(ps.qty_text(-0.5), "-0.5")
        self.assertEqual(ps.qty_text(10.0001), "10")

    def test_numeric_strings_and_decimals(self):
        from decimal import Decimal

        self.assertEqual(ps.qty_text("2.50"), "2.5")
        self.assertEqual(ps.qty_text(Decimal("3.000")), "3")

    def test_none_is_zero(self):
        self.assertEqual(ps.qty_text(None), "0")
        self.assertEqual(ps.qty_text(""), "0")

    def test_nan_and_infinity_print_as_given_not_raise(self):
        """`int()` of either raises; a print that raises is a blank page."""
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value):
                self.assertEqual(ps.qty_text(value), str(value))

    def test_a_non_number_is_escaped_not_raised(self):
        self.assertEqual(ps.qty_text("a few"), "a few")
        self.assertEqual(ps.qty_text("<b>2</b>"), "&lt;b&gt;2&lt;/b&gt;")

    def test_the_jinja_global_is_the_same_function(self):
        self.assertEqual(ps.ps_qty(1.0), "1")


QUILL = '<div class="ql-editor read-mode"><p>{}</p></div>'


class TestRichText(unittest.TestCase):
    def test_an_entity_in_plain_text_is_not_escaped_twice(self):
        """Item PDT-0014's description is `POOL &amp; FOUNTAIN CENTER MINI CONTROLLER`,
        with no tag. Escaped as it stood, the page read `POOL &amp; FOUNTAIN`."""
        self.assertEqual(ps.rich_text("POOL &amp; FOUNTAIN"), "POOL &amp; FOUNTAIN")
        self.assertEqual(ps.plain_text(ps.rich_text("2&quot; JET &amp; Co")), '2" JET & Co')
        self.assertEqual(ps.rich_text("A & B"), "A &amp; B", "a bare ampersand is still escaped")
        name = "POOL & FOUNTAIN CENTER MINI CONTROLLER"
        self.assertEqual(
            ps.line_html(name, "POOL &amp; FOUNTAIN CENTER MINI CONTROLLER"),
            f'<span style="{ps.STRONG}">POOL &amp; FOUNTAIN CENTER MINI CONTROLLER</span>',
            "the description only repeats the name once decoded",
        )

    def test_plain_text_keeps_its_line_breaks_and_is_escaped(self):
        """1,657 of 6,148 Sales Invoice lines carry newlines and no tag; as HTML they
        ran together into one paragraph."""
        self.assertEqual(ps.rich_text("Line 1\nLine 2"), "Line 1<br>Line 2")
        self.assertEqual(ps.rich_text("Line 1\r\nLine 2"), "Line 1<br>Line 2")
        self.assertEqual(ps.rich_text("  padded\n"), "padded")
        self.assertEqual(ps.rich_text("Tom & Jerry's\n3 > 2"), "Tom &amp; Jerry&#x27;s<br>3 &gt; 2")

    def test_quill_markup_passes_through_unchanged(self):
        """An escaped Text Editor value put a literal `&lt;div&gt;` in front of a supplier."""
        markup = QUILL.format("x")
        self.assertEqual(ps.rich_text(markup), markup)
        self.assertEqual(ps.rich_text("<p>a</p>\n<p>b</p>"), "<p>a</p>\n<p>b</p>")

    def test_a_less_than_sign_is_text_not_markup(self):
        self.assertEqual(ps.rich_text("a < b"), "a &lt; b")
        self.assertEqual(ps.rich_text("pipe <1 in"), "pipe &lt;1 in")

    def test_empty(self):
        self.assertEqual(ps.rich_text(None), "")
        self.assertEqual(ps.rich_text(""), "")

    def test_the_jinja_global_is_the_same_function(self):
        self.assertEqual(ps.ps_rich("a\nb"), "a<br>b")


class TestLineHtml(unittest.TestCase):
    def head(self, name):
        return f'<span style="{ps.STRONG}">{name}</span>'

    def test_a_description_that_repeats_the_name_is_dropped(self):
        """ERPNext copies the name into the description for any Item without one."""
        for description in (
            "Pump Motor",
            "PUMP MOTOR",
            "  Pump   Motor \n",
            "Pump\nMotor",
            QUILL.format("Pump Motor"),
            "<p>pump&nbsp;motor</p>",
        ):
            with self.subTest(description):
                self.assertEqual(ps.line_html("Pump Motor", description), self.head("Pump Motor"))

    def test_an_identical_copy_is_dropped_whatever_it_contains(self):
        """The copy is character-for-character the name, so it must never stutter: not
        with an inch mark or apostrophe (escaped on the way in), and not with the double
        space an imported name carries into its copy."""
        for name in ('NOZZLE, 2" JET', "Owner's Manual", "PIPE <1 IN", "Pump  Motor", "Pump & Motor"):
            with self.subTest(name):
                self.assertEqual(ps.line_html(name, name), self.head(ps.escape_html(name)))

    def test_a_different_description_prints_under_the_name(self):
        out = ps.line_html("Pump Motor", "1/2 HP, 115V\nTEFC")
        self.assertEqual(out, self.head("Pump Motor") + '<div style="margin-top:1px">1/2 HP, 115V<br>TEFC</div>')

    def test_the_description_alone_when_there_is_no_name(self):
        self.assertEqual(ps.line_html(None, "Service call"), "Service call")
        self.assertEqual(ps.line_html("  ", QUILL.format("x")), QUILL.format("x"))

    def test_the_name_alone_when_there_is_no_description(self):
        self.assertEqual(ps.line_html("Pump Motor", None), self.head("Pump Motor"))
        self.assertEqual(ps.line_html("Pump Motor", ""), self.head("Pump Motor"))
        self.assertEqual(ps.line_html(None, None), "")

    def test_the_name_is_escaped_and_the_markup_is_not(self):
        out = ps.line_html("<b>Pump</b> & Co", QUILL.format("<strong>1/2 HP</strong>"))
        self.assertIn("&lt;b&gt;Pump&lt;/b&gt; &amp; Co", out)
        self.assertIn(QUILL.format("<strong>1/2 HP</strong>"), out)

    def test_the_jinja_global_is_the_same_function(self):
        self.assertEqual(ps.ps_line("Pump"), self.head("Pump"))
        self.assertEqual(ps.ps_line("Pump", "Pump"), self.head("Pump"))


class TestUomText(unittest.TestCase):
    def test_nos_reads_as_each(self):
        """ERPNext's default unit, on every one of the 6,148 Sales Invoice lines here."""
        for value in ("Nos", "nos", "NOS", " Nos ", "Nos."):
            with self.subTest(value):
                self.assertEqual(ps.uom_text(value), "ea")

    def test_everything_else_as_stored(self):
        self.assertEqual(ps.uom_text("Gallon"), "Gallon")
        self.assertEqual(ps.uom_text("lb"), "lb")
        self.assertEqual(ps.uom_text("<ft>"), "&lt;ft&gt;")
        self.assertEqual(ps.uom_text(None), "")
        self.assertEqual(ps.ps_uom("Nos"), "ea")


class TestCleanLabel(unittest.TestCase):
    def test_the_company_suffix_goes(self):
        self.assertEqual(ps.clean_label("UT SPECIAL - SF", "SF"), "UT SPECIAL")

    def test_the_inactive_marker_goes_too(self):
        """On 51 invoices: 33 of this one, 18 of the Weber - Ogden code."""
        self.assertEqual(ps.clean_label("Utah Sales Tax - Inactive - SF", "SF"), "Utah Sales Tax")
        self.assertEqual(ps.clean_label("Utah - Weber - Ogden - Inactive - SF", "SF"), "Utah - Weber - Ogden")
        self.assertEqual(ps.clean_label("Utah Sales Tax - inactive", "SF"), "Utah Sales Tax")

    def test_a_label_without_a_suffix_is_untouched(self):
        self.assertEqual(ps.clean_label("Sales Tax", "SF"), "Sales Tax")
        self.assertEqual(ps.clean_label("Davis County - SFX", "SF"), "Davis County - SFX")
        self.assertEqual(ps.clean_label("  Sales Tax  ", "SF"), "Sales Tax")

    def test_without_the_abbreviation_only_what_is_certain_goes(self):
        """No company, no way to know `- SF` is a suffix; and `Inactive` is then not
        the last word, so it stays too."""
        self.assertEqual(ps.clean_label("UT SPECIAL - SF", None), "UT SPECIAL - SF")
        self.assertEqual(ps.clean_label("Utah Sales Tax - Inactive - SF", None), "Utah Sales Tax - Inactive - SF")
        self.assertEqual(ps.clean_label("Utah Sales Tax - Inactive", None), "Utah Sales Tax")
        self.assertEqual(ps.clean_label(None, "SF"), "")


class TestPartyBlock(unittest.TestCase):
    ADDRESS = "1450 E Canyon Rd<br>\nSalt Lake City, UT 84108<br>\n"

    def head(self, name):
        return f'<span style="{ps.STRONG}">{name}</span>'

    def test_the_sparsest_party_prints_its_name_and_nothing_else(self):
        self.assertEqual(ps.party_block("Canyon Ridge HOA"), self.head("Canyon Ridge HOA"))

    def test_every_line_in_order(self):
        out = ps.party_block("Canyon Ridge HOA", self.ADDRESS, "Dana Whitaker", "8015550142", "ap@canyonridge.test")
        self.assertEqual(
            out,
            "<br>".join(
                (
                    self.head("Canyon Ridge HOA"),
                    "1450 E Canyon Rd<br>\nSalt Lake City, UT 84108",
                    "Attn: Dana Whitaker",
                    "(801) 555-0142",
                    "ap@canyonridge.test",
                )
            ),
        )

    def test_no_dangling_or_doubled_breaks_in_any_combination(self):
        import itertools

        values = ("Canyon Ridge HOA", self.ADDRESS, "Dana Whitaker", "8015550142", "ap@canyonridge.test")
        for mask in itertools.product((True, False), repeat=len(values)):
            args = [v if keep else "" for v, keep in zip(values, mask, strict=True)]
            out = ps.party_block(*args)
            with self.subTest(mask):
                self.assertFalse(out.startswith("<br>"), out)
                self.assertFalse(out.endswith("<br>"), out)
                self.assertNotRegex(out, r"<br>\s*<br>")
                self.assertEqual(out == "", not any(mask))

    def test_an_address_of_nothing_but_breaks_is_no_line(self):
        self.assertEqual(ps.party_block("X", "<br>\n&nbsp;<br>"), self.head("X"))

    def test_text_is_escaped_and_the_address_is_not(self):
        out = ps.party_block("<Acme & Sons>", "<b>Suite 4</b><br>", "<i>Pat</i>", "", "a<b>@x.test")
        self.assertIn(self.head("&lt;Acme &amp; Sons&gt;"), out)
        self.assertIn("<b>Suite 4</b>", out)
        self.assertIn("Attn: &lt;i&gt;Pat&lt;/i&gt;", out)
        self.assertIn("a&lt;b&gt;@x.test", out)

    def test_a_phone_the_address_already_prints_is_not_repeated(self):
        """The stock Address Template prints `Phone:` itself."""
        address = self.ADDRESS + "Phone: 801-555-0142<br>"
        out = ps.party_block("Canyon Ridge HOA", address, "", "8015550142", "")
        self.assertEqual(out.count("555-0142"), 1, out)
        self.assertNotIn("(801) 555-0142", out)

    def test_the_same_number_with_its_country_code_is_not_repeated(self):
        """`+1 801 555 0142` and `801-555-0142` are one number; the leading 1 must not
        make the digits differ."""
        address = self.ADDRESS + "Phone: 801-555-0142<br>"
        out = ps.party_block("Canyon Ridge HOA", address, "", "+1 801 555 0142", "")
        self.assertEqual(out.count("555-0142"), 1, out)
        out = ps.party_block("Canyon Ridge HOA", address, "", "18015550142", "")
        self.assertEqual(out.count("555-0142"), 1, out)

    def test_a_different_phone_is_printed(self):
        address = self.ADDRESS + "Phone: 801-555-0142<br>"
        out = ps.party_block("Canyon Ridge HOA", address, "", "801-555-0199", "")
        self.assertIn("(801) 555-0199", out)

    def test_an_email_the_address_already_prints_is_not_repeated(self):
        address = self.ADDRESS + "Email: AP@CanyonRidge.test<br>"
        out = ps.party_block("Canyon Ridge HOA", address, "", "", " ap@canyonridge.TEST ")
        self.assertEqual(out.lower().count("ap@canyonridge.test"), 1, out)

    def test_a_short_phone_is_printed_even_when_its_digits_appear(self):
        """Seven digits or fewer could match a street number or a ZIP by accident."""
        out = ps.party_block("Front Gate", "911 Main St<br>", "", "911", "")
        self.assertTrue(out.endswith("<br>911"), out)

    def test_ps_party_lives_in_print_lookup(self):
        self.assertFalse(hasattr(ps, "ps_party"), "the lookup needs frappe; print_style must not")


class TestStateMarker(unittest.TestCase):
    """Frappe prints "Draft" only through the standard macros a custom format never
    calls, so a draft invoice left the building looking final."""

    def test_a_draft_says_so_in_red(self):
        for status in (0, "0", None, ""):
            with self.subTest(status):
                out = ps.state_marker(status)
                self.assertIn(">DRAFT<", out)
                self.assertIn(ps.FAIL, out)

    def test_a_submitted_document_prints_nothing(self):
        for status in (1, "1"):
            with self.subTest(status):
                self.assertEqual(ps.state_marker(status), "")

    def test_a_cancelled_document_says_so(self):
        for status in (2, "2"):
            with self.subTest(status):
                self.assertIn(">CANCELLED<", ps.state_marker(status))
        self.assertIn(">VOID &amp; REISSUED<", ps.state_marker(2, "VOID & REISSUED"))

    def test_garbage_reads_as_a_draft(self):
        """Printing DRAFT on something final is recoverable; printing nothing on a draft
        is the bug this exists for."""
        self.assertIn(">DRAFT<", ps.state_marker("submitted"))

    def test_the_jinja_global_is_the_same_function(self):
        self.assertEqual(ps.ps_state(1), "")
        self.assertIn(">DRAFT<", ps.ps_state(0))
        self.assertIn(">CANCELLED<", ps.ps_state(2, "CANCELLED"))


# ---------------------------------------------------------------------------
# The fixture.


class _Doc:
    """A Frappe document stand-in: attribute access plus .get(). Not a dict subclass —
    `doc.items` on one resolves to the bound method, and this doctype has child tables."""

    def __init__(self, **fields):
        self.__dict__.update(fields)

    def __getattr__(self, key):
        return None

    def get(self, key, default=None):
        return self.__dict__.get(key, default)


def _stub_frappe():
    import types

    return types.SimpleNamespace(
        format=lambda v, opts=None: str(v),
        db=types.SimpleNamespace(get_value=lambda dt, name, field: f"{name} {field}"),
    )


def _globals():
    return {name: getattr(ps, name) for name in dir(ps) if name.startswith("ps_")}


def _record(**overrides):
    doc = _Doc(
        name="SMR-2026-00318",
        customer="CUST-0001",
        project="PRJ-00001",
        maintenance_contract=None,
        serial_no="WF-0001",
        visit_label="Monthly",
        technician="tech@example.com",
        visit_date="2026-09-22",
        creation="2026-09-22 08:10:00",
        workflow_state="Finalized",
        visit_notes="Basin drained and refilled.\nOwner briefed.",
        client_sign_off="data:image/png;base64,iVBORw0KGgo=",
        maintenance_results=[
            _Doc(serial_no="WF-0001", question="Pump and motor operation", selection="Pass", answer="58 Hz, no vibration"),
            _Doc(serial_no="WF-0001", question="Autofill valve", selection="Fail", answer="Float sticking"),
            _Doc(serial_no="WF-0001", question="Lighting", selection="Other", answer="", other_details="Two lenses fogged"),
        ],
        chemistry_readings=[
            _Doc(serial_no="WF-0001", reading="pH", reading_value=7.4, uom="", min_value=7.2, max_value=7.8, out_of_range=0, notes=""),
            _Doc(serial_no="WF-0001", reading="Total alkalinity", reading_value=60.0, uom="ppm", min_value=80.0, max_value=120.0, out_of_range=1, notes="4 lb increaser added"),
        ],
        cleaning_tasks=[
            _Doc(serial_no="WF-0001", task="Vacuum basin floor", is_done=1, notes=""),
            _Doc(serial_no="WF-0001", task="Backwash filter", is_done=0, notes="Next visit"),
        ],
        consumables=[
            _Doc(serial_no="WF-0001", item="CHEM-CL3", item_name="Chlorine tablets, 3 in", uom="Nos", qty=2.0),
            _Doc(serial_no="WF-0001", item="CHEM-ALG", item_name="Algaecide", uom="qt", qty=0),
        ],
    )
    doc.__dict__.update(overrides)
    return doc


class TestMaintenanceRecordPrintFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        records = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.record = next(r for r in records if r["name"] == "Maintenance Record Print")

    def setUp(self):
        try:
            import jinja2
        except ImportError:  # pragma: no cover
            self.skipTest("jinja2 not installed")

    def render(self, doc):
        from jinja2 import Environment

        env = Environment()
        env.globals.update(_globals())
        return env.from_string(self.record["html"]).render(doc=doc, frappe=_stub_frappe())

    def test_it_is_a_jinja_format_with_frappes_margins(self):
        self.assertEqual(self.record["print_format_type"], "Jinja")
        self.assertEqual(self.record["custom_format"], 1)
        for key in ("margin_top", "margin_bottom", "margin_left", "margin_right"):
            self.assertEqual(self.record[key], 15, f"{key}: the stripe and letterhead need a margin")

    def test_it_opens_on_the_service_pillar(self):
        html = self.record["html"]
        self.assertIn('ps_page_open("service")', html)
        self.assertIn('ps_page_close("service")', html)
        self.assertIn('ps_letterhead("service"', html)

    def test_it_compiles(self):
        from jinja2 import Environment

        Environment().parse(self.record["html"])

    def test_it_renders_a_single_feature_record(self):
        out = self.render(_record())
        self.assertNotIn("{{", out)
        self.assertNotIn("None", out)
        self.assertIn("SMR-2026-00318", out)
        self.assertIn("CUST-0001 customer_name", out)
        self.assertIn("tech@example.com full_name", out)
        self.assertIn("Maintenance Visit Report", out)
        self.assertIn("Pump and motor operation", out)
        self.assertIn(">Fail<", out)
        self.assertIn("Two lenses fogged", out)
        self.assertIn("Out of range", out)
        self.assertIn("4 lb increaser added", out)
        self.assertIn("Not done", out)
        self.assertIn("Chlorine tablets, 3 in", out)
        self.assertNotIn("Algaecide", out, "a consumable with qty 0 was not used")
        self.assertIn("Basin drained and refilled.", out)
        self.assertIn("Digitally captured on site", out)
        self.assertIn('src="data:image/png;base64,iVBORw0KGgo="', out)
        self.assertNotIn(">Water Feature<", out, "single-feature records have no per-row feature column")

    def test_a_per_site_record_grows_the_feature_column(self):
        out = self.render(_record(serial_no=None, maintenance_contract="SMC-2026-0042"))
        self.assertIn("Site visit", out)
        self.assertIn("Contract SMC-2026-0042", out)
        self.assertEqual(out.count(">Water Feature<"), 4, "one per table")
        self.assertNotIn("None", out)

    def test_an_unsigned_record_says_so(self):
        out = self.render(_record(client_sign_off=None))
        self.assertIn("Not signed on site", out)
        self.assertNotIn("<img", out)

    def test_the_sparsest_record_still_renders(self):
        """Every table empty, every optional field blank — a record finalized straight
        from a template with nothing filled in."""
        out = self.render(
            _record(
                customer=None, project=None, technician=None, visit_label=None, visit_notes=None,
                workflow_state=None, client_sign_off=None, visit_date=None, scheduled_visit_date=None,
                maintenance_results=[], chemistry_readings=[], cleaning_tasks=[], consumables=[],
            )
        )
        self.assertNotIn("{{", out)
        self.assertNotIn("None", out)
        self.assertIn("Maintenance Visit Report", out)
        self.assertNotIn("Checklist Results", out)
        self.assertNotIn("Consumables Used", out)

    def test_child_text_is_escaped(self):
        out = self.render(_record(visit_notes="<script>x</script>", maintenance_results=[
            _Doc(question="<b>q</b>", selection="Pass", answer="<i>a</i>"),
        ]))
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;b&gt;q&lt;/b&gt;", out)
        self.assertIn("&lt;i&gt;a&lt;/i&gt;", out)

    def test_print_safe_css_only(self):
        squashed = self.record["html"].replace(" ", "")
        self.assertNotIn("display:flex", squashed)
        self.assertNotIn("display:grid", squashed)
        self.assertIn("display:table-header-group", squashed)
        self.assertIn("page-break-inside:avoid", squashed)

    def test_labor_hours_are_not_printed(self):
        """The portal shows them only when the Sales Order says so; the PDF goes to
        the customer unconditionally."""
        html = self.record["html"]
        for field in ("clock_in_time", "clock_out_time", "paused_duration", "total_labor_cost"):
            self.assertNotIn(field, html)


class TestFactsRows(unittest.TestCase):
    def test_a_second_row_can_drop_the_top_rule(self):
        self.assertIn(f"border-top:1px solid {ps.DEEP_SEA_BLUE}", ps.facts_open())
        self.assertNotIn("border-top", ps.facts_open(top=False))
        self.assertIn(f"border-bottom:1px solid {ps.BORDER_100}", ps.facts_open(top=False))


class TestProjectBriefClientCopy(unittest.TestCase):
    """The Project Brief is rendered in the browser (`project_brief.js`) and cannot
    read print_style, so it carries a copy of the pillar records. This keeps the
    copy honest, and pins the two assets it loads by raw path."""

    JS = (APP / "public" / "js" / "project_enhancements" / "project_brief.js").read_text(encoding="utf-8")

    def test_the_pillar_copy_matches_print_style(self):
        for key, record in ps.PILLARS.items():
            with self.subTest(key):
                match = re.search(rf'{key}: \{{ name: "([A-Z]+)", open: "(#[0-9a-f]{{6}})", end: "(#[0-9a-f]{{6}})", deep: "(#[0-9a-f]{{6}})" \}}', self.JS)
                self.assertIsNotNone(match, f"no {key} record in project_brief.js")
                self.assertEqual(match.group(1), record["name"])
                self.assertEqual(match.group(2), record["open"])
                self.assertEqual(match.group(3), record["deep"])
                self.assertEqual(match.group(4), record["deep"])
        neutral = re.search(r'NEUTRAL_PILLAR = \{ name: "", open: "(#[0-9a-f]{6})", end: "(#[0-9a-f]{6})", deep: "(#[0-9a-f]{6})" \}', self.JS)
        self.assertIsNotNone(neutral)
        self.assertEqual(neutral.group(1), ps.DEEP_SEA_BLUE)
        self.assertEqual(neutral.group(2), ps.NAVY_900)
        self.assertEqual(neutral.group(3), ps.NEUTRAL["deep"])

    def test_every_stream_maps_to_a_pillar(self):
        for stream in ("Design", "Build", "Products", "Service", "Events"):
            self.assertRegex(self.JS, rf'{stream}: "(design|build|service|rent)"')

    def test_it_loads_the_repo_assets(self):
        self.assertIn("/assets/erpnext_enhancements/images/fountain_move/logo.svg", self.JS)
        self.assertIn("/assets/erpnext_enhancements/fonts/big_noodle_titling.woff2", self.JS)
        self.assertTrue(Path(ps.LOGO_PATH).is_file())
        self.assertTrue(Path(ps.FONT_PATH).is_file())

    def test_the_print_window_waits_for_the_face_and_keeps_the_stripe(self):
        """`window.print()` on load fires before the webfont arrives, and a browser
        drops background colours when printing unless told not to."""
        self.assertIn("document.fonts.ready", self.JS)
        self.assertIn("print-color-adjust: exact", self.JS)
        self.assertIn("linear-gradient(90deg", self.JS)


HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
TOKEN_HEXES = {
    getattr(ps, name).lower()
    for name in dir(ps)
    if isinstance(getattr(ps, name), str) and HEX.fullmatch(getattr(ps, name))
}


class TestReportPrintSheets(unittest.TestCase):
    """The two report `.html` sheets. Frappe compiles these with microtemplate, which
    rewrites every double brace in the file (comments included) and escapes only the
    last apostrophe in a text run; and its print wrapper prints the letter head above
    the sheet, so the chrome here is everything BUT the wordmark."""

    SHEETS = {
        "crew_qualification_roster": APP / "hr_enhancements/report/crew_qualification_roster/crew_qualification_roster.html",
        "supplier_pickup_list": APP / "project_enhancements/report/supplier_pickup_list/supplier_pickup_list.html",
    }

    def _body(self, path):
        text = path.read_text(encoding="utf-8")
        return re.sub(r"<!--.*?-->", "", text, flags=re.S), text

    def test_no_double_brace_and_no_apostrophe(self):
        for name, path in self.SHEETS.items():
            body, text = self._body(path)
            with self.subTest(name):
                self.assertNotIn("{{", text)
                self.assertNotIn("}}", text)
                self.assertNotIn("'", body, "microtemplate escapes only the last apostrophe in a run")

    def test_the_wrapper_letter_head_is_not_duplicated(self):
        for name, path in self.SHEETS.items():
            body, _ = self._body(path)
            with self.subTest(name):
                self.assertNotIn("letter_head", body)
                self.assertNotIn("letterhead", body)
                self.assertNotIn("<svg", body)
                self.assertNotIn("logo", body)

    def test_the_chrome_is_present_and_prints(self):
        for name, path in self.SHEETS.items():
            body, _ = self._body(path)
            with self.subTest(name):
                self.assertIn("/assets/erpnext_enhancements/fonts/big_noodle_titling.woff2", body)
                self.assertIn('"Big Noodle Titling"', body)
                self.assertIn(f"background-color: {ps.DEEP_SEA_BLUE}", body)
                self.assertIn(f"linear-gradient(90deg, {ps.DEEP_SEA_BLUE} 0%, {ps.NAVY_900} 100%)", body)
                self.assertLess(body.index("background-color: "), body.index("background-image: "))
                self.assertIn("print-color-adjust: exact", body)

    def test_every_colour_is_a_token(self):
        for name, path in self.SHEETS.items():
            body, _ = self._body(path)
            with self.subTest(name):
                used = {h.lower() for h in HEX.findall(body)}
                self.assertEqual(used - TOKEN_HEXES, set(), "a colour that is not a design-system token")

    def test_the_font_is_by_relative_path(self):
        """The desk print window resolves it against the site and the PDF route makes
        it absolute in scrub_urls; an absolute URL would pin the sheet to one site."""
        for name, path in self.SHEETS.items():
            body, _ = self._body(path)
            with self.subTest(name):
                self.assertNotIn("http://", body)
                self.assertNotIn("https://", body)


class TestTrainingCertificate(unittest.TestCase):
    """Renders the certificate the way a bench would, against a stub frappe."""

    MODULE_PATH = APP / "training" / "setup_print_formats.py"

    @classmethod
    def setUpClass(cls):
        import types

        sys.modules.setdefault("frappe", types.ModuleType("frappe"))
        cls.ns = {}
        exec(compile(cls.MODULE_PATH.read_text(encoding="utf-8"), str(cls.MODULE_PATH), "exec"), cls.ns)

    def render(self, badge=None, signoff=None, **overrides):
        import types

        from jinja2 import Environment

        def get_value(dt, name, field):
            if dt == "Training Badge":
                return badge
            if dt == "Training Signoff":
                return signoff
            if dt == "Employee":
                return "Pat Supervisor"
            return None

        frappe = types.SimpleNamespace(
            format=lambda v, opts=None: str(v),
            db=types.SimpleNamespace(get_value=get_value),
            utils=types.SimpleNamespace(get_url=lambda path="": "https://erp.example.com" + path),
        )
        doc = _Doc(
            name="TC-2026-0042", course="COURSE-01", course_title="Confined Space Entry", holder_name="Sam Learner",
            user="sam@example.com", issued_on="2026-09-22", expires_on="2027-09-22", verification_code="SF-7Q2K-9X",
            version_number=3, score_percent=94,
        )
        doc.__dict__.update(overrides)
        return Environment().from_string(self.ns["_CERTIFICATE_HTML"]).render(doc=doc, frappe=frappe, letter_head="LETTERHEAD")

    def test_it_is_on_the_chrome_and_draws_no_second_logo(self):
        html = self.ns["_CERTIFICATE_HTML"]
        self.assertIn("@font-face", html)
        self.assertEqual(html.count("linear-gradient(90deg"), 2)
        self.assertIn("<svg", html)
        self.assertNotIn("{{ letter_head }}", html)
        squashed = html.replace(" ", "")
        self.assertNotIn("display:flex", squashed)
        self.assertNotIn("display:grid", squashed)
        self.assertIn("page-break-inside:avoid", squashed)

    def test_it_renders_a_full_certificate(self):
        out = self.render(badge="/files/badge.svg", signoff="EMP-0007")
        self.assertNotIn("{{", out)
        self.assertNotIn("None", out)
        self.assertNotIn("LETTERHEAD", out)
        for text in ("Sam Learner", "Confined Space Entry", "SF-7Q2K-9X", "TC-2026-0042", "version 3", "scored 94%",
                     "Pat Supervisor", "VERIFIED COMPETENT BY", 'src="/files/badge.svg"', "https://erp.example.com/training_certificate"):
            with self.subTest(text):
                self.assertIn(text, out)

    def test_the_sparse_certificate_still_renders(self):
        out = self.render(badge=None, signoff=None, expires_on=None, version_number=None, score_percent=None, course_title=None)
        self.assertNotIn("{{", out)
        self.assertNotIn("None", out)
        self.assertIn("Does not expire", out)
        self.assertIn("COURSE-01", out, "the course id stands in for a missing title")
        self.assertNotIn("<img", out, "no badge, no empty badge box")
        self.assertNotIn("VERIFIED COMPETENT BY", out)

    def test_the_holder_and_course_are_escaped(self):
        out = self.render(holder_name="<b>Sam</b>", course_title="<i>x</i>")
        self.assertIn("&lt;b&gt;Sam&lt;/b&gt;", out)
        self.assertIn("&lt;i&gt;x&lt;/i&gt;", out)


if __name__ == "__main__":
    unittest.main()
