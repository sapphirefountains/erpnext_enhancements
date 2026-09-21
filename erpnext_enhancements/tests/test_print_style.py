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
        neutral = ps.letterhead(None, "QUOTATION", "Quotation", "")
        self.assertIn(">QUOTATION<", neutral)
        self.assertNotIn("&middot; QUOTATION", neutral, "a neutral document names no pillar")

    def test_letterhead_address_prefers_the_document_and_falls_back(self):
        from jinja2 import Environment

        html = ps.letterhead(None, "X", "T", "", address_field="company_address_display")
        self.assertIn("doc.company_address_display", html)

        class Doc:
            company_address_display = "2 Other Street"

        rendered = Environment().from_string(html).render(doc=Doc())
        self.assertIn("2 Other Street", rendered)
        self.assertNotIn(COMPANY_ADDRESS_HTML, rendered)

        Doc.company_address_display = None
        rendered = Environment().from_string(html).render(doc=Doc())
        self.assertIn(COMPANY_ADDRESS_HTML, rendered)

        plain = ps.letterhead("service", "X", "T", "")
        self.assertIn(COMPANY_ADDRESS_HTML, plain)
        self.assertNotIn("{%", plain)

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
