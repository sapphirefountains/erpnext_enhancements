"""Bench-free tests for the Master Subcontractor Agreement and Statement of Work in the maintenance theme.

Nik, 2026-10-13: "do the contract builder and building for all contract types, not just the ones
we have so far. Style wise they should follow the same theme as the current Maintenance Services
Agreement." This suite pins what would otherwise fail silently for the two subcontractor templates:

* both render, with no undefined name, for an empty contract and a filled one (empty child
  tables included), and the SOW keeps its v1.461.0 hold-point table with the checkbox fallback;
* the title block, ``{{ summary() }}`` and an information table open each one; every clause hangs
  its number (``ct-clause``), numbers are unique and in order, and every class used is styled;
* every ``doc.<field>`` either prints exists on Project Contract and is shown for that contract
  type by its own and its section's ``depends_on``, and the builder walks a step offering it; the
  only blanks left are the ones completed by hand;
* subcontractor initials: valid, unique keys inside the clauses they acknowledge, the Tier 2 and
  optional ones only when they apply; the Electronic Signatures sentence; ``sig()`` signatures;
* the plain-language summary restates only clauses the templates contain;
* the live-template patch replaces only the shipped bodies (the SOW's v1.5.0 one is what prod
  still has) and never raises.

Stubs a minimal ``frappe`` in ``setUpModule`` (never at import), like
``test_rental_owner_agreement_layout``. The render cases need ``jinja2``, which CI installs.

Run: python -m unittest erpnext_enhancements.tests.test_subcontractor_agreement_layout
"""

import json
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
TEMPLATES = APP / "templates" / "contracts"
MSA = TEMPLATES / "master_subcontractor_agreement.html"
SOW = TEMPLATES / "statement_of_work.html"
DOCTYPES = APP / "project_enhancements" / "doctype"
PC_JSON = DOCTYPES / "project_contract" / "project_contract.json"

#: The bodies this repo shipped and the patch may replace (verified against prod on 2026-10-13):
#: the MSA's v1.5.0 body, and the SOW's v1.5.0 body (prod) and v1.461.0 body (the old repo file).
SHIPPED = {
    "msa": {"89e019714fa8f428351fd8bd8e7431737f99b3151757ce0ccc665d1c7dacd778"},
    "sow": {
        "326db01b342e34bca128572d0aa2d6760fccff8121a8404ecb26e324624334d6",
        "e7045f53dd14c5c3eb133b8f706fe65894551e5e3fb3f06ae8bce0b073906627",
    },
}

MSA_INITIALS = ["pay_if_paid", "insurance", "indemnity", "liability_delay", "flow_down", "take_over", "arbitration"]
SOW_INITIALS = ["liquidated_damages", "retention", "corrections"]

#: The fields this change added, with their type (none carries a default: neither agreement states one).
NEW_SOW_FIELDS = {
    "sow_project_type": "Select",
    "sow_project_type_other": "Data",
    "sow_feature_type": "Select",
    "sow_feature_type_other": "Data",
    "sow_schedule_days": "Int",
    "sow_liquidated_damages": "Select",
    "sow_liquidated_damages_rate": "Currency",
    "sow_svc_site_prep": "Check",
    "sow_svc_excavation": "Check",
    "sow_svc_concrete": "Check",
    "sow_svc_plumbing": "Check",
    "sow_svc_electrical": "Check",
    "sow_svc_equipment": "Check",
    "sow_svc_assembly": "Check",
    "sow_svc_finish": "Check",
    "sow_svc_landscaping": "Check",
    "sow_svc_startup": "Check",
    "sow_svc_cleanup": "Check",
    "sow_svc_other": "Data",
    "sow_exclusions": "Small Text",
    "sow_company_furnished": "Select",
    "sow_company_furnished_items": "Small Text",
    "sow_due_completed_work": "Data",
    "sow_due_as_builts": "Data",
    "sow_due_equipment_docs": "Data",
    "sow_due_lien_waiver": "Data",
    "sow_other_deliverable": "Data",
    "sow_other_deliverable_standard": "Data",
    "sow_other_deliverable_due": "Data",
    "sow_compensation_type": "Select",
    "sow_retention_percent": "Percent",
    "sow_invoice_day": "Data",
    "sow_safety_requirements": "Small Text",
    "sow_correction_days": "Int",
    "sow_special_terms": "Small Text",
    "sow_hold_pre_pour": "Check",
    "sow_hold_rough_plumbing": "Check",
    "sow_hold_electrical": "Check",
    "sow_hold_equipment": "Check",
    "sow_hold_final": "Check",
    "sow_hold_other": "Data",
}

NEW_SOW_SECTIONS = ["sow_services_section", "sow_deliverables_section", "sow_compensation_section", "sow_terms_section"]

try:
    import jinja2
except ImportError:  # pragma: no cover - CI installs it
    jinja2 = None

initials = None
builder = None
summary = None
layout_patch = None
terms_patch = None


class _DF(dict):
    """A docfield: a dict that also answers attribute access, as frappe's does."""

    def __getattr__(self, key):
        return self.get(key)


class _Doc(dict):
    def __getattr__(self, key):
        return self.get(key)


def _meta(path):
    return types.SimpleNamespace(fields=[_DF(f) for f in json.loads(path.read_text(encoding="utf-8"))["fields"]])


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")
    frappe._ = lambda s, *a, **k: s
    frappe.throw = lambda *a, **k: (_ for _ in ()).throw(RuntimeError(a[0] if a else "throw"))
    frappe.log_error = lambda *a, **k: None
    frappe.get_traceback = lambda: ""

    def whitelist(*dargs, **dkwargs):
        def wrap(fn):
            return fn

        return wrap(dargs[0]) if dargs and callable(dargs[0]) else wrap

    frappe.whitelist = whitelist
    frappe.get_all = lambda *a, **k: []
    frappe.get_doc = lambda *a, **k: None
    frappe.session = types.SimpleNamespace(user="Administrator")
    metas = {"Project Contract": _meta(PC_JSON)}
    for child in (
        "Contract Service Option",
        "Contract Phase Item",
        "Contract Milestone",
        "Contract Equipment Item",
        "Contract Rate Line",
    ):
        slug = child.lower().replace(" ", "_")
        metas[child] = _meta(DOCTYPES / slug / f"{slug}.json")
    frappe.get_meta = lambda doctype: metas[doctype]
    frappe.db = types.SimpleNamespace(
        get_value=lambda *a, **k: None,
        get_single_value=lambda *a, **k: None,
        set_value=lambda *a, **k: None,
        commit=lambda: None,
    )
    frappe.defaults = types.SimpleNamespace(get_global_default=lambda key: "USD")

    utils = types.ModuleType("frappe.utils")
    utils.escape_html = lambda s: (
        str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )

    def flt(value, precision=None):
        try:
            number = float(value or 0)
        except (TypeError, ValueError):
            number = 0.0
        return round(number, precision) if precision is not None else number

    utils.flt = flt
    utils.cint = lambda value: int(float(value or 0))
    utils.fmt_money = lambda value, currency=None: f"${flt(value):,.2f}"
    utils.formatdate = lambda value: str(value)
    for name in ("add_days", "add_years", "getdate", "nowdate"):
        setattr(utils, name, lambda *a, **k: None)
    frappe.utils = utils

    # contract_builder's endpoints are rate limited; the decorator is a no-op here.
    limiter = types.ModuleType("frappe.rate_limiter")
    limiter.rate_limit = lambda *a, **k: (lambda fn: fn)
    frappe.rate_limiter = limiter

    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils
    sys.modules["frappe.rate_limiter"] = limiter


def setUpModule():
    global initials, builder, summary, layout_patch, terms_patch
    _install_frappe_stub()
    for mod in list(sys.modules):
        if mod.startswith(
            (
                "erpnext_enhancements.project_enhancements",
                "erpnext_enhancements.patches.update_maintenance_agreement_terms",
                "erpnext_enhancements.patches.refresh_subcontractor_agreement_layout",
                "erpnext_enhancements.feature_flags",
            )
        ):
            sys.modules.pop(mod, None)
    from erpnext_enhancements.patches import refresh_subcontractor_agreement_layout as _layout
    from erpnext_enhancements.patches import update_maintenance_agreement_terms as _terms
    from erpnext_enhancements.project_enhancements import contract_builder as _builder
    from erpnext_enhancements.project_enhancements import contract_summary as _summary
    from erpnext_enhancements.project_enhancements.esign import initials as _initials

    initials, builder, summary, layout_patch, terms_patch = _initials, _builder, _summary, _layout, _terms


def tearDownModule():
    sys.modules.pop("frappe", None)
    sys.modules.pop("frappe.utils", None)
    sys.modules.pop("frappe.rate_limiter", None)


def _text(path):
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- rendering


def _render(path, doc, hold_points=None):
    """Render a template the way project_contract._render_context does, with stub helpers.

    StrictUndefined: a name the context does not supply raises instead of printing nothing."""
    blank = lambda width=30: "____"  # noqa: E731

    def qty(v):
        if v in (None, "") or not float(v):
            return None
        number = float(v)
        return str(int(number)) if number == int(number) else f"{number:g}"

    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    return env.from_string(_text(path)).render(
        doc=doc,
        fill=lambda v, w=30: blank() if v in (None, "") else str(v),
        blank=blank,
        cb=lambda c: "[x]" if c else "[ ]",
        money=lambda v, w=14: blank() if not v else f"${float(v):,.2f}",
        dt=lambda v, w=18: blank() if not v else str(v),
        multiline=lambda v, w=80, n=3: "<br>".join([blank()] * n) if not v else str(v).replace("\n", "<br>"),
        qty=qty,
        pct=lambda v, w=6: f"{qty(v)}%" if qty(v) else blank() + "%",
        sig=lambda party="client", width=30: f"<sig:{party}>",
        initial=lambda key, label="": initials.initial_markup({}, key, label),
        summary=lambda: summary.summary_html(doc),
        signature=_Doc(),
        phases={},
        service_options={},
        hold_points=hold_points or [],
        frappe=_Doc(),
    )


def _empty(template_key):
    # A fresh draft: every table is an empty list, every other field unset.
    return _Doc(
        template_key=template_key,
        name="SF-SOW-2026-0001" if template_key == "sow" else "SF-MSA-2026-0001",
        equipment_items=[],
        milestones=[],
        phases=[],
        service_options=[],
        rate_schedule=[],
    )


def _keys(html):
    return [key for key, _label in initials.initial_slots(html)]


def _filled_sow():
    doc = _empty("sow")
    doc.update(
        party_display="Wasatch Concrete LLC",
        agreement_party_name="Wasatch Concrete, LLC",
        billing_address="12 Industrial Way",
        billing_city_state_zip="Ogden, UT 84401",
        contact_person="Dana Reyes",
        contact_email="dana@example.com",
        project_title="Hardware Buildings Fountain",
        site_address="455 West 200 North, Salt Lake City",
        msa_effective_date="2026-09-01",
        sow_project_type="Other",
        sow_project_type_other="Basin rebuild",
        sow_feature_type="Commercial Fountain",
        sow_svc_concrete=1,
        sow_svc_plumbing=1,
        sow_svc_other="Core drilling",
        scope_of_work="<p>Pour the basin.</p>",
        sow_exclusions="Electrical service\nPermits",
        sow_due_completed_work="2026-12-01",
        sow_due_lien_waiver="With the final invoice",
        sow_other_deliverable="Pour log",
        sow_other_deliverable_standard="Daily",
        sow_other_deliverable_due="Weekly",
        mobilization_date="2026-11-02",
        substantial_completion_date="2026-11-20",
        sow_schedule_days=5,
        sow_liquidated_damages="Per Calendar Day",
        sow_liquidated_damages_rate=250,
        sow_compensation_type="Time & Materials",
        milestones=[_Doc(milestone="Mobilization", description="Start", due_upon="Signing", amount=5000)],
        milestones_total=5000,
        sow_retention_percent=10,
        rate_journeyman=95,
        materials_markup_percent=15,
        not_to_exceed=40000,
        sow_invoice_day="25th",
        sow_company_furnished="Company-Furnished Items",
        sow_company_furnished_items="Pumps\nNozzles",
        sow_safety_requirements="Fall protection at the vault",
        sow_correction_days=3,
        sow_hold_pre_pour=1,
        sow_hold_other="Leak test",
        sow_special_terms="Two-year warranty on the basin.",
    )
    return doc


@unittest.skipUnless(jinja2, "jinja2 is not installed")
class TestRender(unittest.TestCase):
    def assertComplete(self, html):
        self.assertNotIn("{{", html)
        self.assertNotIn("{%", html)

    def test_empty_contracts_render_as_paper_forms(self):
        for key, path in (("msa", MSA), ("sow", SOW)):
            html = _render(path, _empty(key))
            self.assertComplete(html)
            self.assertIn("City / State / Zip:", html)  # no address yet: the paper line stays
            self.assertNotIn("[x]", html, key)  # a fresh draft ticks nothing
        sow = _render(SOW, _empty("sow"))
        self.assertIn("[ ]  Pre-pour inspection (before concrete placement)", sow)  # no locked scope
        self.assertIn("<td>Mobilization Payment</td>", sow)  # no milestones yet: the paper rows

    def test_a_filled_msa_prints_its_inputs(self):
        doc = _empty("msa")
        doc.update(
            party_display="Wasatch Concrete LLC",
            agreement_party_name="Wasatch Concrete, LLC",
            billing_address="12 Industrial Way",
            billing_city_state_zip="Ogden, UT 84401",
            contact_person="Dana Reyes",
            contact_phone="801-555-0100",
            contact_email="dana@example.com",
            msa_tier="Tier 2 - Trade",
        )
        html = _render(MSA, doc)
        self.assertComplete(html)
        for text in (
            "<b>Subcontractor Name:</b></td><td>Wasatch Concrete, LLC",
            "<b>City / State / Zip:</b></td><td>Ogden, UT 84401",
            "<b>Wasatch Concrete, LLC</b>, an individual / entity with its principal place of business at "
            "12 Industrial Way, Ogden, UT 84401 (&quot;Subcontractor&quot;)",
            "Statements of Work with Wasatch Concrete, LLC (&quot;Subcontractor&quot;)",  # Exhibit B
            "dana@example.com",
        ):
            self.assertIn(text, html)
        self.assertEqual(html.count("[x]"), 1)  # Tier 2 only
        self.assertIn("[x]</td><td><b>TIER 2", html)
        self.assertNotIn("Wasatch Concrete LLC", html)

    def test_a_filled_sow_prints_its_inputs(self):
        html = _render(SOW, _filled_sow())
        self.assertComplete(html)
        for text in (
            "<b>Subcontractor:</b></td><td>Wasatch Concrete, LLC",
            "<b>City / State / Zip:</b></td><td>Ogden, UT 84401",
            "[x] Other: Basin rebuild",
            "[x] Commercial Fountain",
            "[x]  Concrete / masonry work",
            "[x]  Plumbing / hydraulic installation",
            "[ ]  Excavation and grading",
            "[x]  Other: Core drilling",
            "<div><p>Pour the basin.</p></div>",
            "Electrical service<br>Permits",
            "<td>Completed Work</td><td>Per scope in Section 2</td><td>2026-12-01</td>",
            "<td>With the final invoice</td>",
            "<td>Other: Pour log</td><td>Daily</td><td>Weekly</td>",
            "within 5 business days of SOW execution",
            "[ ] N/A   [x] $250.00 per calendar day beyond Substantial Completion Date.",
            "[x] Time &amp; Materials",
            "<td>Mobilization</td><td>Start</td><td>Signing</td><td>$5,000.00</td>",
            "Company shall withhold 10% of each progress payment",
            "Cost + 15% markup",
            "submitted by the 25th of each month",
            "[ ] None — all materials",
            "[x] Company-furnished items (list below):",
            "Pumps<br>Nozzles",
            "Fall protection at the vault",
            "within 3 business days of written notice",
            "[x]  Pre-pour inspection (before concrete placement)",
            "[x]  Other: Leak test",
            "Two-year warranty on the basin.",
        ):
            self.assertIn(text, html)
        self.assertNotIn("<td>Mobilization Payment</td>", html)  # the paper rows give way to the table

    def test_company_furnished_none_prints_no_list(self):
        doc = _empty("sow")
        doc.update(sow_company_furnished="None")
        html = _render(SOW, doc)
        section = html[html.index("6. COMPANY-FURNISHED ITEMS"):html.index("7. SUBCONTRACTOR PERSONNEL")]
        self.assertIn("[x] None", section)
        self.assertNotIn("____", section)

    def test_contracted_hold_points_replace_the_checkboxes(self):
        """v1.461.0's §9.4, which prod has never had: the locked scope's hold points, or the boxes."""
        hp = _Doc(criterion="Basin pre-pour", pass_standard="Rebar per S-2", verification_method="Visual",
                  responsible_party="Sapphire")
        html = _render(SOW, _empty("sow"), hold_points=[hp])
        self.assertIn("<td>Basin pre-pour</td><td>Rebar per S-2</td>", html)
        self.assertNotIn("Pre-pour inspection (before concrete placement)", html)

    def test_both_sign_with_sig(self):
        for key, path in (("msa", MSA), ("sow", SOW)):
            html = _render(path, _empty(key))
            signatures = html[html.index("<h1>SIGNATURES</h1>"):]
            self.assertIn("<sig:provider>", signatures, key)
            self.assertIn("<sig:client>", signatures, key)
            self.assertNotIn("Signature: ____", signatures, key)
            self.assertIn("<h3><b>SUBCONTRACTOR</b></h3>", signatures, key)

    def test_msa_initials(self):
        self.assertEqual(_keys(_render(MSA, _empty("msa"))), MSA_INITIALS)
        tier_1 = _render(MSA, _Doc(_empty("msa"), msa_tier="Tier 1 - Service & Design"))
        self.assertEqual(_keys(tier_1), MSA_INITIALS)
        # §4.4's retainage is Tier 2's alone, so only a Tier 2 subcontractor initials it.
        tier_2 = _keys(_render(MSA, _Doc(_empty("msa"), msa_tier="Tier 2 - Trade")))
        self.assertEqual(tier_2, MSA_INITIALS[:1] + ["retainage"] + MSA_INITIALS[1:])

    def test_sow_initials(self):
        self.assertEqual(_keys(_render(SOW, _empty("sow"))), SOW_INITIALS)
        # No liquidated damages: nothing to initial in §4.3. Special terms: initialled when there are some.
        no_damages = _Doc(_empty("sow"), sow_liquidated_damages="N/A", sow_special_terms="Night work allowed")
        self.assertEqual(_keys(_render(SOW, no_damages)), ["retention", "corrections", "special_terms"])
        self.assertEqual(_keys(_render(SOW, _filled_sow())), [*SOW_INITIALS, "special_terms"])

    def test_initials_are_valid_unique_and_bounded(self):
        for key, path, doc in (
            ("msa", MSA, _Doc(_empty("msa"), msa_tier="Tier 2 - Trade")),
            ("sow", SOW, _filled_sow()),
        ):
            slots = initials.initial_slots(_render(path, doc))
            keys = [slot_key for slot_key, _label in slots]
            self.assertEqual(len(keys), len(set(keys)), key)
            self.assertLessEqual(len(keys), initials.MAX_INITIALS, key)
            for slot_key, label in slots:
                self.assertTrue(initials.INITIAL_KEY_RE.match(slot_key), slot_key)
                self.assertTrue(label, slot_key)
                self.assertNotIn("&amp;#", label, slot_key)  # a Jinja literal holding an HTML entity

    def test_the_summary_opens_both(self):
        for key, path, heading in (
            ("msa", MSA, "SUBCONTRACTOR INFORMATION"),
            ("sow", SOW, "SOW REFERENCE INFORMATION"),
        ):
            html = _render(path, _empty(key))
            self.assertLess(html.index("What you are agreeing to"), html.index(heading), key)


# --------------------------------------------------------------------------- layout


class TestLayout(unittest.TestCase):
    def test_title_block_summary_and_information_table(self):
        for path, title in ((MSA, "MASTER SUBCONTRACTOR AGREEMENT"), (SOW, "STATEMENT OF WORK")):
            lines = _text(path).split("\n")
            self.assertEqual(lines[0], f"<h3><b>{title}</b></h3>", path.name)
            self.assertEqual(
                lines[2:5],
                ["<p><b>Sapphire Fountains, LLC</b></p>", "<p>85 W 300 S, Bountiful, UT 84010</p>",
                 "<p>www.sapphirefountains.com</p>"],
                path.name,
            )
            self.assertEqual(lines[5], "{{ summary() }}", path.name)
            self.assertEqual(_text(path).count("{{ summary() }}"), 1, path.name)
            self.assertRegex(lines[6], r"^<h1>[A-Z &;]+</h1>$", path.name)
            self.assertTrue(lines[7].startswith('<table class="ct-table"><tr><td><b>'), path.name)

    def test_every_numbered_clause_hangs_its_number_in_order(self):
        for path, count in ((MSA, 72), (SOW, 26)):
            text = _text(path)
            self.assertEqual(re.findall(r"<p>\s*\d+\.\d*\s", text), [], path.name)
            self.assertEqual(re.findall(r"<h2>\s*\d", text), [], path.name)
            numbers = re.findall(r'<p class="ct-clause"><span class="ct-num">(\d+)\.(\d+)</span>', text)
            as_ints = [(int(a), int(b)) for a, b in numbers]
            self.assertEqual(len(as_ints), count, path.name)
            self.assertEqual(as_ints, sorted(set(as_ints)), path.name)  # unique and ascending
            for section in {a for a, _b in as_ints}:
                subs = [b for a, b in as_ints if a == section]
                self.assertEqual(subs, list(range(1, len(subs) + 1)), f"{path.name} §{section}")
            headings = [int(n) for n in re.findall(r"<h1>(\d+)\. ", text)]
            self.assertEqual(headings, list(range(1, len(headings) + 1)), path.name)
            self.assertTrue({a for a, _b in as_ints} <= set(headings), path.name)

    def test_titled_clauses_lead_in_bold(self):
        for path, titles in (
            (MSA, ("Pay If Paid.", "No Damages for Delay.", "Arbitration.", "Electronic Signatures.",
                   "Personal Guarantee.", "As-Built Drawings.", "Prohibited Conduct.")),
            (SOW, ("Project Type:", "Included Services", "Exclusions.", "Liquidated Damages", "Retention:",
                   "Electronic Signatures.")),
        ):
            text = _text(path)
            for title in titles:
                self.assertRegex(text, r'<span class="ct-num">\d+\.\d+</span><b>' + re.escape(title) + "</b>")

    def test_the_msa_coverages_are_a_list_and_the_guarantee_hangs_its_numbers(self):
        text = _text(MSA)
        lists = re.findall(r'<ul class="ct-list">(.*?)</ul>', text, flags=re.S)
        self.assertEqual([chunk.count("<li>") for chunk in lists], [5])
        guarantee = text[text.index("EXHIBIT B"):text.index("EXHIBIT C")]
        self.assertEqual(re.findall(r'<span class="ct-num">(\d)\.</span>', guarantee), ["1", "2", "3", "4"])

    def test_the_sow_boxes_are_option_lines(self):
        text = _text(SOW)
        self.assertNotIn("[ ]", text)
        # 2.1's twelve services, 5.1's one line of types, Section 6's two, and 9.4's six fallback boxes.
        self.assertEqual(len(re.findall(r'<p class="ct-option">', text)), 21)

    def test_the_print_format_styles_every_class_used(self):
        records = json.loads((APP / "fixtures" / "print_format.json").read_text(encoding="utf-8"))
        css = next(r for r in records if r["name"] == "Project Contract Print")["css"]
        for path in (MSA, SOW):
            used = {c for attr in re.findall(r'class="([^"]+)"', _text(path)) for c in attr.split()}
            for cls in used:
                self.assertIn(f".{cls}", css, f"{path.name}: .{cls}")

    def test_initials_sit_inside_the_clause_they_acknowledge(self):
        for path in (MSA, SOW):
            for line in _text(path).split("\n"):
                if "initial(" in line:
                    self.assertRegex(line, r'^<p class="ct-(clause|sub)">', path.name)
                    self.assertTrue(line.endswith((") }}</p>", ") }}{% endif %}</p>")), line[:80])

    def test_both_say_each_set_of_initials_is_made_separately(self):
        for path, party in ((MSA, "this Agreement"), (SOW, "this Statement of Work")):
            text = _text(path)
            clause = text[text.index("<b>Electronic Signatures.</b>"):]
            clause = clause[:clause.index("</p>")]
            self.assertIn(
                f"Where {party} asks for Subcontractor&#x27;s initials, each set of initials is made separately "
                "and acknowledges the clause it sits beside.",
                clause,
            )

    def test_the_sow_keeps_its_hold_points(self):
        """test_msa_rates pins the same: v1.461.0's table, with the checkboxes as the fallback."""
        text = _text(SOW)
        for needle in ("{% if hold_points %}", "{% else %}", "hp.pass_standard", "hp.criterion",
                       "Pre-pour inspection (before concrete placement)"):
            self.assertIn(needle, text)


# --------------------------------------------------------------------------- an input for everything


def _visibility():
    """``{fieldname: (field depends_on, its section's depends_on)}`` from the doctype JSON."""
    out, section = {}, None
    for field in json.loads(PC_JSON.read_text(encoding="utf-8"))["fields"]:
        if field["fieldtype"] == "Section Break":
            section = field.get("depends_on")
        out[field["fieldname"]] = (field.get("depends_on"), section)
    return out


class TestEveryPrintedFieldHasAnInput(unittest.TestCase):
    """Nik: "the matching should come from inputted information. If we are missing check boxes or
    fields or addresses or whatever, we need inputs for those so we can set them." """

    def check(self, template_key, path):
        visibility = _visibility()
        # doc.name is the contract's number, not an input.
        printed = set(re.findall(r"\bdoc\.([a-z_][a-z0-9_]*)", _text(path))) - {"name"}
        self.assertTrue(printed)
        for fieldname in sorted(printed):
            self.assertIn(fieldname, visibility, f"{template_key}: doc.{fieldname} is not a Project Contract field")
            depends_on, section = visibility[fieldname]
            self.assertTrue(
                builder.section_applies(depends_on, template_key), f"{template_key}: {fieldname} is hidden"
            )
            self.assertTrue(
                builder.section_applies(section, template_key), f"{template_key}: {fieldname}'s section is hidden"
            )
        # ... and the builder walks a step that offers each one (signing fills the signature fields,
        # and the party name is stamped from the party).
        reachable = {f["fieldname"] for step in builder.builder_steps(template_key, "Supplier") for f in step["fields"]}
        self.assertEqual(printed - builder.SKIP_FIELDS - reachable, set(), template_key)
        return printed

    def test_msa(self):
        printed = self.check("msa", MSA)
        self.assertTrue({"agreement_party_name", "billing_city_state_zip", "msa_tier"} <= printed)

    def test_sow(self):
        printed = self.check("sow", SOW)
        self.assertTrue(set(NEW_SOW_FIELDS) <= printed, set(NEW_SOW_FIELDS) - printed)

    def test_the_new_fields(self):
        fields = {f["fieldname"]: f for f in json.loads(PC_JSON.read_text(encoding="utf-8"))["fields"]}
        for name, fieldtype in NEW_SOW_FIELDS.items():
            self.assertEqual(fields[name]["fieldtype"], fieldtype, name)
            self.assertTrue(fields[name]["depends_on"].startswith('eval:doc.template_key=="sow"'), name)
            self.assertNotIn("default", fields[name], name)  # the agreement states none
            for other in ("msa", "maintenance", "rental", "owner", "nda", "architect", "employee_contractor"):
                self.assertFalse(builder.section_applies(fields[name]["depends_on"], other), f"{name} on {other}")
        for name in ("agreement_party_name", "billing_city_state_zip"):
            for key in ("msa", "sow"):
                self.assertTrue(builder.section_applies(fields[name]["depends_on"], key), f"{name} on {key}")

    def test_the_new_sections_are_steps_for_the_sow_only(self):
        sow = [step["key"] for step in builder.builder_steps("sow", "Supplier")]
        for section in NEW_SOW_SECTIONS:
            self.assertIn(section, sow)
        self.assertLess(sow.index("sow_section"), sow.index("sow_services_section"))
        self.assertLess(sow.index("sow_services_section"), sow.index("scope_section"))
        for key in ("msa", "maintenance", "rental", "owner"):
            steps = {step["key"] for step in builder.builder_steps(key, "Supplier")}
            self.assertFalse(steps & set(NEW_SOW_SECTIONS), key)

    def test_no_blank_is_left_without_an_input_except_the_hand_completed_parts(self):
        """MSA: the Personal Guarantee is completed by the guarantor and the Exhibit C risk register is kept
        per job. SOW: Accepted By is signed when each deliverable is accepted. Empty scope and milestone
        tables print paper lines instead of their content."""
        msa = _text(MSA)
        msa = msa[: msa.index("<h1>EXHIBIT B")] + msa[msa.index("<h1>SIGNATURES</h1>"):]
        sow = _text(SOW).replace("<td>{{ blank(18) }}</td></tr>", "</tr>")  # Accepted By
        for start, end in (("{% else %}<p class=\"ct-sub\">{{ blank(30) }}", "{% endif %}"),
                           ("{% if not doc.milestones %}", "{% endif %}")):
            at = sow.index(start)
            sow = sow[:at] + sow[sow.index(end, at):]
        for text in (msa, sow):
            self.assertEqual(re.findall(r"blank\(\d*\)|money\(None\)|___|\[ \]", text), [])


# --------------------------------------------------------------------------- summary


class TestSummary(unittest.TestCase):
    def test_msa_states_the_tier_and_its_retainage(self):
        tier_2 = summary.summary_html(_Doc(template_key="msa", msa_tier="Tier 2 - Trade"))
        self.assertIn("You are Tier 2 (trade work)", tier_2)
        self.assertIn("up to 10% of each progress payment", tier_2)
        tier_1 = summary.summary_html(_Doc(template_key="msa", msa_tier="Tier 1 - Service & Design"))
        self.assertIn("You are Tier 1 (service and design work): Sections 1–20 apply.", tier_1)
        self.assertNotIn("progress payment", tier_1)  # retainage is Tier 2's alone (§4.4)
        unset = summary.summary_html(_Doc(template_key="msa"))
        self.assertIn("The tier ticked on page 1 decides", unset)
        for html in (tier_1, tier_2, unset):
            self.assertIn("pay if paid", html)
            self.assertIn("where the two differ, the agreement governs", html)
            self.assertEqual(html.count("<li>"), 6)

    def test_sow_states_this_jobs_terms(self):
        html = summary.summary_html(_filled_sow())
        for text in (
            "Hardware Buildings Fountain at 455 West 200 North, Salt Lake City.",
            "concrete and masonry, plumbing and hydraulics and Core drilling, as Section 2 describes, except the "
            "items Section 6 lists, which we furnish.",
            "under our Master Services Agreement with you effective 2026-09-01",
            "the work starts 2026-11-02 and is substantially complete by 2026-11-20.",
            "within 5 business days of signing",
            "Finishing late costs $250.00 for each calendar day",
            "priced as time and materials; the payment schedule in Section 5.2 totals $5,000.00; time and "
            "materials work is not to exceed $40,000.00.",
            "We hold back 10% of each progress payment",
            "by the 25th of each month",
            "at no extra cost within 3 business days of written notice.",
            "<b>Special terms:</b>",
        ):
            self.assertIn(text, html)
        self.assertLessEqual(html.count("<li>"), 7)

    def test_sow_never_states_a_figure_the_agreement_leaves_blank(self):
        html = summary.summary_html(
            _Doc(template_key="sow", sow_liquidated_damages="Per Calendar Day", sow_retention_percent=0,
                 milestones_total=0, not_to_exceed=0)
        )
        self.assertNotIn("$", html)
        self.assertNotIn("%", html)
        self.assertNotIn("Finishing late", html)
        self.assertNotIn("Special terms", html)
        self.assertNotIn("The job:", html)
        self.assertIn("<b>When:</b> you send us a project schedule after signing", html)
        self.assertIn("<b>Getting paid:</b> we hold back retention from each progress payment", html)
        self.assertIn("the work Section 2 describes.", html)

    def test_is_escaped(self):
        html = summary.summary_html(_Doc(template_key="sow", project_title="<script>x</script>"))
        self.assertNotIn("<script>", html)

    def test_every_fixed_figure_it_quotes_is_in_the_agreement(self):
        """The summary restates clauses; this catches a clause changed in the repo template without
        the summary being changed with it."""
        clauses = {
            MSA: (
                "Sections 1–20 apply", "Trade Subcontractor Addendum (Sections 21–24)", "Pay If Paid",
                "within ten (10) business days of Company&#x27;s receipt", "in writing within ten (10) days",
                "up to ten percent (10%) of each progress payment", "thirty (30) days of final acceptance",
                "NO WORK SHALL COMMENCE", "Performance Bond and Payment Bond", "Personal Guarantee",
                "one (1) year from final acceptance", "forty-eight (48) hours of written notice",
                "48 hours&#x27; written notice", "equitable time extension", "three (3) months preceding the claim",
                "thirty (30) days&#x27; written notice", "binding arbitration", "Salt Lake City",
                "attorneys&#x27; fees",
            ),
            SOW: (
                "Master Services Agreement", "the terms of this SOW shall control", "update it weekly",
                "Time is of the essence with respect to the Substantial Completion Date",
                "per calendar day beyond Substantial Completion Date",
                "released upon Final Completion and delivery of final lien waiver",
                "of each month (or upon milestone completion)", "Payment terms are governed by the MSA",
                "shall not substitute key personnel without Company&#x27;s prior written consent",
                "tailgate safety meeting at the start of each new phase", "within 24 hours of occurrence",
                "at no additional cost", "supplement or modify the MSA",
            ),
        }
        for path, phrases in clauses.items():
            text = _text(path)
            for phrase in phrases:
                self.assertIn(phrase, text, f"{path.name}: {phrase}")


# --------------------------------------------------------------------------- live-template patch


class TestPatch(unittest.TestCase):
    def test_replaces_only_the_shipped_bodies(self):
        self.assertEqual(layout_patch.REPLACEABLE_BODY_SHA256, SHIPPED)
        for key, path in (("msa", MSA), ("sow", SOW)):
            new = _text(path)
            self.assertEqual(layout_patch.decide(key, new, new), "current")
            self.assertEqual(layout_patch.decide(key, "edited on the site", new), "diverged")
            # The new body must not itself be replaceable, or every migrate would rewrite it.
            self.assertNotIn(terms_patch.body_sha256(new), layout_patch.REPLACEABLE_BODY_SHA256[key])
        fake = "the v1.5.0 sow body"
        layout_patch.REPLACEABLE_BODY_SHA256["sow"].add(terms_patch.body_sha256(fake))
        try:
            self.assertEqual(layout_patch.decide("sow", fake, _text(SOW)), "replace")
            self.assertEqual(layout_patch.decide("msa", fake, _text(MSA)), "diverged")  # never crosses types
        finally:
            layout_patch.REPLACEABLE_BODY_SHA256["sow"].discard(terms_patch.body_sha256(fake))

    def run_patch(self, live_bodies, contracts=(), requests=()):
        stub = sys.modules["frappe"]
        writes, frozen, errors = [], [], []

        def get_all(doctype, filters=None, pluck=None, **kwargs):
            if doctype == "Project Contract":
                return [
                    c["name"]
                    for c in contracts
                    if all(c.get(k) == v for k, v in (filters or {}).items() if not isinstance(v, list))
                ]
            if doctype == "Contract Signature Request":
                return list(requests)
            return []

        class _Contract:
            def __init__(self, name):
                self.name = name

            def freeze_executed_copy(self):
                frozen.append(self.name)

        def get_value(doctype, name, field, *a, **k):
            body = live_bodies.get(name)
            if isinstance(body, Exception):
                raise body
            return body

        saved = (stub.get_all, stub.get_doc, stub.db, stub.log_error, getattr(stub, "get_app_path", None))
        stub.get_all = get_all
        stub.get_doc = lambda doctype, name: _Contract(name)
        stub.db = types.SimpleNamespace(
            get_value=get_value,
            set_value=lambda doctype, name, *a, **k: writes.append((doctype, name, a)),
            commit=lambda: None,
        )
        stub.log_error = lambda *a, **k: errors.append(k.get("title") or (a[0] if a else ""))
        stub.get_app_path = lambda *parts: str(APP.joinpath(*parts[1:]))
        try:
            layout_patch.execute()
        finally:
            stub.get_all, stub.get_doc, stub.db, stub.log_error, stub.get_app_path = saved
        return writes, frozen, errors

    def test_swaps_a_shipped_body_after_freezing_paper_signed_copies(self):
        old = "the v1.5.0 msa body"
        layout_patch.REPLACEABLE_BODY_SHA256["msa"].add(terms_patch.body_sha256(old))
        try:
            writes, frozen, errors = self.run_patch(
                {"msa": old, "sow": "a statement of work edited on the site"},
                contracts=[{"name": "SF-MSA-2026-0001", "template_key": "msa", "docstatus": 1, "status": "Signed"}],
            )
        finally:
            layout_patch.REPLACEABLE_BODY_SHA256["msa"].discard(terms_patch.body_sha256(old))
        self.assertEqual(frozen, ["SF-MSA-2026-0001"])
        self.assertEqual([(w[0], w[1]) for w in writes], [("Contract Template", "msa")])
        self.assertEqual(writes[0][2][1], _text(MSA))
        self.assertTrue(any("Statement of Work layout: template not updated" in e for e in errors))

    def test_waits_while_a_signing_link_is_out(self):
        old = "the v1.461.0 sow body"
        layout_patch.REPLACEABLE_BODY_SHA256["sow"].add(terms_patch.body_sha256(old))
        try:
            writes, _frozen, errors = self.run_patch(
                {"sow": old},
                contracts=[{"name": "SF-SOW-2026-0002", "template_key": "sow", "docstatus": 1}],
                requests=["CSR-2026-00012"],
            )
        finally:
            layout_patch.REPLACEABLE_BODY_SHA256["sow"].discard(terms_patch.body_sha256(old))
        self.assertEqual(writes, [])
        self.assertTrue(any("not updated yet" in e for e in errors))

    def test_never_raises_and_leaves_current_bodies_alone(self):
        writes, _frozen, errors = self.run_patch({"msa": RuntimeError("db down"), "sow": _text(SOW)})
        self.assertEqual(writes, [])
        self.assertEqual(errors, ["Master Subcontractor Agreement layout patch failed"])
        self.assertEqual(self.run_patch({})[0], [])  # neither template on the site

    def test_registered_after_the_rental_owner_layout_patch(self):
        lines = [line for line in _text(APP / "patches.txt").split("\n") if line and not line.startswith("#")]
        self.assertIn("erpnext_enhancements.patches.refresh_subcontractor_agreement_layout", lines)
        self.assertGreater(
            lines.index("erpnext_enhancements.patches.refresh_subcontractor_agreement_layout"),
            lines.index("erpnext_enhancements.patches.refresh_rental_owner_agreement_layout"),
        )


if __name__ == "__main__":
    unittest.main()
