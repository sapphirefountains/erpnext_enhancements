"""Bench-free tests for the NDA, Architect and Employee-Contractor agreements given the maintenance theme.

Nik, 2026-10-13: "do the contract builder and building for all contract types, not just the ones
we have so far. Style wise they should follow the same theme as the current Maintenance Services
Agreement." This suite pins what would otherwise fail silently, for all three:

* each renders, with no undefined name, for an empty contract and a filled one;
* the title block, the summary and the SIGNATURES block (with ``sig(``, so it can be e-signed) are
  where the theme puts them;
* every clause hangs its number (``ct-clause``), numbers are unique and in order, the sections are
  numbered 1..N, and the cross-references in the wording still point at the right section;
* every ``doc.<field>`` an agreement prints exists on Project Contract and is shown for that
  contract type by its own and its section's ``depends_on``, so the builder offers a place to
  fill it ("an input for everything it prints");
* the other party's initials: valid, unique keys inside the clauses they acknowledge, and the
  Electronic Signatures sentence saying each set is made separately;
* the plain-language summary quotes only figures the agreement contains;
* the live-template patch replaces only the shipped bodies and never raises.

Stubs a minimal ``frappe`` in ``setUpModule`` (never at import), like
``test_rental_owner_agreement_layout``. The render cases need ``jinja2``, which CI installs.

Run: python -m unittest erpnext_enhancements.tests.test_general_agreement_layout
"""

import datetime
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
NDA = TEMPLATES / "nondisclosure_agreement.html"
ARCHITECT = TEMPLATES / "architect_agreement.html"
EMPLOYEE = TEMPLATES / "employee_contractor_agreement.html"
DOCTYPES = APP / "project_enhancements" / "doctype"
PC_JSON = DOCTYPES / "project_contract" / "project_contract.json"

#: template key -> (file, the contract type's party type, how the agreement names the other party)
AGREEMENTS = {
    "nda": (NDA, "Any Party", "the Company"),
    "architect": (ARCHITECT, "Customer", "Architect"),
    "employee_contractor": (EMPLOYEE, "Any Party", "the Employee-Contractor"),
}

#: The live bodies on prod on 2026-10-13, byte-identical to the files as converted from Word.
SHIPPED = {
    "nda": "7c45c45576a934382375beb489414bff3b953c6eb73a8cfa555db6c4dd202bcc",
    "architect": "e2959ccd34f077c1b2f503a77992988d3bdd4bfe259aaeb3f1bd838c31c44d7f",
    "employee_contractor": "3147a13d6963506bab5cff96d0d888c6ec634520d64e6710e149856d2c9f849a",
}

EXPECTED_INITIALS = {
    "nda": ["obligations", "return_info", "remedies", "attorneys_fees", "jurisdiction"],
    "architect": ["reliance", "payment", "warranty", "liability", "force_majeure", "venue"],
    "employee_contractor": ["confidentiality", "assignment", "non_solicitation", "non_compete"],
}

#: Clause count and section count per agreement.
SHAPE = {"nda": (14, 8), "architect": (21, 10), "employee_contractor": (6, 6)}

try:
    import jinja2
    import jinja2.sandbox
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
    utils.formatdate = lambda value, *a, **k: str(value)
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
                "erpnext_enhancements.patches.refresh_general_agreement_layout",
                "erpnext_enhancements.feature_flags",
            )
        ):
            sys.modules.pop(mod, None)
    from erpnext_enhancements.patches import refresh_general_agreement_layout as _layout
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


def _formatdate(value, fmt=None):
    """The three babel patterns the NDA's opening line uses, on an ISO date string."""
    day = datetime.date.fromisoformat(str(value))
    return {"d": str(day.day), "MMMM": day.strftime("%B"), "yyyy": str(day.year)}.get(fmt, str(value))


def _render(path, doc):
    """Render a template the way project_contract._render_context does, with stub helpers.

    Sandboxed, as frappe.render_template is, and StrictUndefined: a name the context does not
    supply raises instead of printing nothing."""
    blank = lambda width=30: "____"  # noqa: E731

    def qty(v):
        if v in (None, "") or not float(v):
            return None
        number = float(v)
        return str(int(number)) if number == int(number) else f"{number:g}"

    env = jinja2.sandbox.SandboxedEnvironment(undefined=jinja2.StrictUndefined)
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
        hold_points=[],
        frappe=types.SimpleNamespace(utils=types.SimpleNamespace(formatdate=_formatdate)),
    )


def _empty(template_key):
    # A fresh draft: every table is an empty list, every other field unset.
    return _Doc(
        template_key=template_key,
        equipment_items=[],
        milestones=[],
        phases=[],
        service_options=[],
        rate_schedule=[],
    )


@unittest.skipUnless(jinja2, "jinja2 is not installed")
class TestRender(unittest.TestCase):
    def assertComplete(self, html):
        self.assertNotIn("{{", html)
        self.assertNotIn("{%", html)

    def test_empty_contracts_render_as_paper_forms(self):
        for key, (path, _party, _word) in AGREEMENTS.items():
            html = _render(path, _empty(key))
            self.assertComplete(html)
            self.assertIn("City / State / Zip:", html, key)  # no address yet: the paper line stays
        nda = _render(NDA, _empty("nda"))
        self.assertIn("entered this ___ day of _____, 20__", nda)
        self.assertIn("in connection with a proposed business relationship between the Parties", nda)

    def test_a_filled_nda_prints_its_inputs(self):
        doc = _empty("nda")
        doc.update(
            contract_date="2026-10-13",
            party_display="Acme Water",
            agreement_party_name="Acme Water Features, Inc.",
            nda_entity_type="Delaware corporation",
            billing_address="1 Main St\nSuite 4",
            billing_city_state_zip="Provo, UT 84601",
            nda_purpose="joint fountain design",
        )
        html = _render(NDA, doc)
        self.assertComplete(html)
        for text in (
            "entered this 13 day of October, 2026",
            "<b>Acme Water Features, Inc.</b> (the “Company”), a Delaware corporation with its principal place of "
            "business at 1 Main St, Suite 4, Provo, UT 84601.",
            "in connection with a joint fountain design between the Parties",
            "<b>Entity Type:</b></td><td>Delaware corporation",
            "<b>City / State / Zip:</b></td><td>Provo, UT 84601",
        ):
            self.assertIn(text, html)
        self.assertNotIn(">Acme Water<", html)
        # No city row and no dangling comma when the address already carries it.
        doc.update(billing_address="1 Main St, Provo, UT 84601", billing_city_state_zip=None)
        html = _render(NDA, doc)
        self.assertIn("principal place of business at 1 Main St, Provo, UT 84601.", html)
        self.assertNotIn("City / State / Zip:", html)

    def test_a_filled_architect_agreement_prints_its_inputs(self):
        doc = _empty("architect")
        doc.update(
            name="SF-ARCH-2026-0001",
            contract_date="2026-10-13",
            party_display="Studio North",
            billing_address="9 Elm St, Ogden, UT 84401",
            contact_person="Dana Lee",
            architect_notice_attention="Legal Department",
            architect_owner="City of Ogden",
            architect_owner_agreement_date="2026-09-01",
            project_title="Plaza Fountain",
            sapphire_contact="James Harris",
            sapphire_contact_email="james@example.com",
            scope_of_work="<p>Design the plaza fountain.</p>",
            architect_services="Site survey\nStructural drawings",
            architect_sow_term="Through construction documents",
            architect_payment_terms="$12,000, half at signing",
        )
        html = _render(ARCHITECT, doc)
        self.assertComplete(html)
        for text in (
            "<b>Attention:</b></td><td>Legal Department",
            "<b>Primary Contact:</b></td><td>Dana Lee",
            "<b>Sapphire Primary Contact:</b></td><td>James Harris",
            "<b>Sapphire Email:</b></td><td>james@example.com",
            "agreement with <b>City of Ogden</b> (“<u>Owner</u>”) dated 2026-09-01",
            "<b>SOW No.</b></td><td>SF-ARCH-2026-0001",
            "<div><p>Design the plaza fountain.</p></div>",
            "Site survey<br>Structural drawings",
            "<b>Term:</b> Through construction documents",
            "$12,000, half at signing",
        ):
            self.assertIn(text, html)
        self.assertNotIn("City / State / Zip:", html)
        # The Attention line falls back to the contact person.
        self.assertIn("<b>Attention:</b></td><td>Dana Lee", _render(ARCHITECT, _Doc(doc, architect_notice_attention=None)))

    def test_a_filled_employee_contractor_agreement_prints_its_inputs(self):
        doc = _empty("employee_contractor")
        doc.update(
            contract_date="2026-10-13",
            party_display="Sam Rivera",
            billing_address="12 Oak Ave\nBountiful, UT 84010",
            contact_phone="801-555-0100",
            contact_email="sam@example.com",
        )
        html = _render(EMPLOYEE, doc)
        self.assertComplete(html)
        for text in ("<b>Name:</b></td><td>Sam Rivera", "12 Oak Ave<br>Bountiful, UT 84010", "801-555-0100"):
            self.assertIn(text, html)
        self.assertNotIn("City / State / Zip:", html)

    def test_each_signs_with_sig(self):
        for key, (path, _party, _word) in AGREEMENTS.items():
            html = _render(path, _empty(key))
            signatures = html[html.index("<h1>SIGNATURES</h1>"):]
            self.assertIn("<sig:provider>", signatures, key)
            self.assertIn("<sig:client>", signatures, key)
            self.assertNotIn("Signature: ____", signatures, key)
            self.assertNotIn("\t", signatures, key)  # the Word tab-stop tables are gone

    def test_initials_are_valid_unique_and_bounded(self):
        for key, (path, _party, _word) in AGREEMENTS.items():
            slots = initials.initial_slots(_render(path, _empty(key)))
            keys = [slot_key for slot_key, _label in slots]
            self.assertEqual(keys, EXPECTED_INITIALS[key])
            self.assertEqual(len(keys), len(set(keys)))
            self.assertLessEqual(len(keys), initials.MAX_INITIALS)
            for slot_key, label in slots:
                self.assertTrue(initials.INITIAL_KEY_RE.match(slot_key), slot_key)
                self.assertTrue(label, slot_key)

    def test_the_summary_opens_each(self):
        for key, (path, _party, _word) in AGREEMENTS.items():
            html = _render(path, _empty(key))
            self.assertIn("What you are agreeing to, in short", html, key)
            self.assertLess(html.index("What you are agreeing to"), html.index("<h1>"), key)


# --------------------------------------------------------------------------- layout


class TestLayout(unittest.TestCase):
    def test_the_title_block_and_summary_open_the_agreement(self):
        for key, (path, _party, _word) in AGREEMENTS.items():
            lines = _text(path).split("\n")
            self.assertRegex(lines[0], r"^<h3><b>[A-Z][A-Z -]+</b></h3>$", key)
            self.assertEqual(
                lines[2:6],
                [
                    "<p><b>Sapphire Fountains, LLC</b></p>",
                    "<p>85 W 300 S, Bountiful, UT 84010</p>",
                    "<p>www.sapphirefountains.com</p>",
                    "{{ summary() }}",
                ],
                key,
            )
            self.assertRegex(lines[1], r"^<p><b>.+</b></p>$", key)
            self.assertRegex(lines[6], r"^<h1>[A-Za-z &;-]+INFORMATION</h1>$", key)
            self.assertTrue(lines[7].startswith('<table class="ct-table"><tr><td><b>'), key)
            self.assertEqual(_text(path).count("{{ summary() }}"), 1, key)

    def test_every_numbered_clause_hangs_its_number_in_order(self):
        for key, (path, _party, _word) in AGREEMENTS.items():
            text = _text(path)
            self.assertEqual(re.findall(r"<p>\s*\d+\.\d*\s", text), [], key)
            self.assertEqual(re.findall(r"<h[23]>\s*\d", text), [], key)
            numbers = re.findall(r'<p class="ct-clause"><span class="ct-num">(\d+)\.(\d+)</span>', text)
            as_ints = [(int(a), int(b)) for a, b in numbers]
            clauses, sections = SHAPE[key]
            self.assertEqual(len(as_ints), clauses, key)
            self.assertEqual(text.count('class="ct-clause"'), clauses, key)  # none without its number
            self.assertEqual(as_ints, sorted(set(as_ints)), key)  # unique and ascending
            for section in {a for a, _b in as_ints}:
                subs = [b for a, b in as_ints if a == section]
                self.assertEqual(subs, list(range(1, len(subs) + 1)), f"{key} §{section}")
            headings = [int(n) for n in re.findall(r"<h1>(\d+)\. ", text)]
            self.assertEqual(headings, list(range(1, sections + 1)), key)
            self.assertTrue({a for a, _b in as_ints} <= set(headings), key)

    def test_the_cross_references_still_point_at_their_sections(self):
        """The Word copies' numbering was lost in conversion; their own references say what it was."""
        nda = _text(NDA)
        self.assertIn("<h1>6. RETURN OF PROPRIETARY INFORMATION</h1>", nda)
        section_6 = nda[nda.index("<h1>6. "):nda.index("<h1>7. ")]
        self.assertIn("included in this Paragraph 6", section_6)
        self.assertIn("Subject to the last sentence of Section 6", nda)
        architect = _text(ARCHITECT)
        headings = dict(re.findall(r"<h1>(\d+)\. ([A-Z ]+)</h1>", architect))
        for number, title in (
            ("3", "PAYMENT"),
            ("5", "LICENSE AND OWNERSHIP"),
            ("6", "LIMITED WARRANTY"),
            ("7", "LIMITATION OF LIABILITY"),
            ("8", "CONFIDENTIAL INFORMATION"),
            ("10", "MISCELLANEOUS"),
        ):
            self.assertEqual(headings[number], title)
            self.assertIn(f"{number} ({title.title().replace(' And ', ' and ').replace(' Of ', ' of ')})", architect)
        section_7 = architect[architect.index("<h1>7. "):architect.index("<h1>8. ")]
        self.assertIn("specified in this Section 7", section_7)

    def test_lists_and_sub_paragraphs(self):
        lists = re.findall(r'<ul class="ct-list">(.*?)</ul>', _text(EMPLOYEE), flags=re.S)
        self.assertEqual([chunk.count("<li>") for chunk in lists], [7])  # the seven expectations
        nda = _text(NDA)
        self.assertEqual(re.findall(r'<p class="ct-sub">\((\w+)\)', nda), ["a", "b", "c", "d", "e", "i", "ii", "iii", "iv", "v"])

    def test_titled_clauses_lead_in_bold(self):
        for path, titles in (
            (NDA, ("Integrated Agreement.", "Governing Law.", "Jurisdiction.", "Electronic Signatures.")),
            (ARCHITECT, ("Independent Contractors.", "Notices.", "Governing Law and Venue.", "Entire Agreement.")),
            (EMPLOYEE, ("Electronic Signatures.",)),
        ):
            text = _text(path)
            for title in titles:
                self.assertRegex(text, r'<span class="ct-num">\d+\.\d+</span><b>' + re.escape(title) + "</b>")

    def test_the_print_format_styles_every_class_used(self):
        records = json.loads((APP / "fixtures" / "print_format.json").read_text(encoding="utf-8"))
        css = next(r for r in records if r["name"] == "Project Contract Print")["css"]
        for path, _party, _word in AGREEMENTS.values():
            used = {c for attr in re.findall(r'class="([^"]+)"', _text(path)) for c in attr.split()}
            for cls in used:
                self.assertIn(f".{cls}", css, f"{path.name}: .{cls}")

    def test_initials_sit_inside_the_clause_they_acknowledge(self):
        for path, _party, _word in AGREEMENTS.values():
            for line in _text(path).split("\n"):
                if "initial(" in line:
                    self.assertRegex(line, r'^<p class="ct-(clause|sub)">', path.name)
                    self.assertTrue(line.endswith(") }}</p>"), line[:80])
                    self.assertEqual(line.count("initial("), 1, line[:80])

    def test_each_says_each_set_of_initials_is_made_separately(self):
        for key, (path, _party, word) in AGREEMENTS.items():
            text = _text(path)
            general = text[:text.index("<h1>SIGNATURES</h1>")]
            clause = general[general.index("<b>Electronic Signatures.</b>"):]
            clause = clause[:clause.index("</p>")]
            self.assertIn("This Agreement may be executed electronically, and such signatures are fully binding.", clause)
            self.assertIn(
                f"Where this Agreement asks for {word}’s initials, each set of initials is made separately and "
                "acknowledges the clause it sits beside.",
                clause,
                key,
            )

    def test_signatures_close_the_agreement(self):
        for key, (path, _party, word) in AGREEMENTS.items():
            text = _text(path)
            signatures = text[text.index("<h1>SIGNATURES</h1>"):]
            self.assertNotIn("<h1>", signatures[len("<h1>SIGNATURES</h1>"):], key)
            for part in (
                "Signature: {{ sig('provider') }}    Date: {{ dt(signature.countersigned_on, 30) }}",
                "Print Name: {{ fill(signature.countersigned_by) }}",
                "Title: {{ fill(signature.countersigned_title) }}",
                "Signature: {{ sig('client') }}    Date: {{ dt(doc.signed_on, 30) }}",
                "Print Name: {{ fill(doc.signed_by) }}",
                "Title: {{ fill(signature.signed_title) }}",
            ):
                self.assertIn(part, signatures, key)
            party = word.replace("the ", "").upper()
            self.assertIn(f"<h3><b>{party}</b></h3>", signatures, key)
            self.assertLess(signatures.index("sig('provider')"), signatures.index(f"<h3><b>{party}</b></h3>"), key)


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

    def test_every_printed_field_is_shown_for_its_contract_type(self):
        visibility = _visibility()
        for key, (path, party_type, _word) in AGREEMENTS.items():
            # doc.name is the document's own name (the architect SOW No.), not a field.
            printed = set(re.findall(r"\bdoc\.([a-z_][a-z0-9_]*)", _text(path))) - {"name"}
            self.assertTrue(printed)
            for fieldname in sorted(printed):
                self.assertIn(fieldname, visibility, f"{key}: doc.{fieldname} is not a Project Contract field")
                depends_on, section = visibility[fieldname]
                self.assertTrue(builder.section_applies(depends_on, key), f"{key}: {fieldname} is hidden")
                self.assertTrue(builder.section_applies(section, key), f"{key}: {fieldname}'s section is hidden")
            # ... and the builder walks a step that offers each one (signing fills the signature fields,
            # and the party name is stamped from the party).
            reachable = {f["fieldname"] for step in builder.builder_steps(key, party_type) for f in step["fields"]}
            self.assertEqual(printed - builder.SKIP_FIELDS - reachable, set(), key)

    def test_the_new_fields(self):
        fields = {f["fieldname"]: f for f in json.loads(PC_JSON.read_text(encoding="utf-8"))["fields"]}
        for name, fieldtype, key in (
            ("nda_entity_type", "Data", "nda"),
            ("architect_section", "Section Break", "architect"),
            ("architect_notice_attention", "Data", "architect"),
            ("sapphire_contact", "Data", "architect"),
            ("sapphire_contact_email", "Data", "architect"),
            ("architect_services", "Small Text", "architect"),
            ("architect_sow_term", "Data", "architect"),
            ("architect_payment_terms", "Small Text", "architect"),
        ):
            self.assertEqual(fields[name]["fieldtype"], fieldtype, name)
            self.assertEqual(fields[name]["depends_on"], f'eval:doc.template_key=="{key}"', name)
            self.assertNotIn("default", fields[name], name)  # the agreements state none
        order = json.loads(PC_JSON.read_text(encoding="utf-8"))["field_order"]
        mine = [n for n in fields if n.startswith(("nda_entity", "architect_", "sapphire_contact", "column_break_architect"))]
        self.assertEqual([n for n in order if n in mine], mine)  # field_order lists them where the fields sit
        # The architect's notices and Statement of Work get their own step; the walk still opens on
        # the party and the job, and Sapphire's own scope is the shared Scope of Work step.
        self.assertEqual(
            [s["key"] for s in builder.builder_steps("architect", "Customer")],
            ["parties_section", "details_section", "architect_section", "scope_section"],
        )
        for key in ("nda", "employee_contractor"):
            self.assertEqual([s["key"] for s in builder.builder_steps(key, "Any Party")], ["parties_section", "details_section"])
        # The shared rows were extended, not narrowed.
        for name in ("agreement_party_name", "billing_city_state_zip"):
            for key in ("maintenance", "rental", "owner"):
                self.assertTrue(builder.section_applies(fields[name]["depends_on"], key), (name, key))
        self.assertTrue(builder.section_applies(fields["scope_of_work"]["depends_on"], "sow"))

    def test_no_blank_is_left_without_an_input(self):
        """Every blank line these agreements print is a field left empty. The only literal blanks are
        the fallbacks of a field that has an input (the NDA's date, the architect's scope of work)."""
        for key, (path, _party, _word) in AGREEMENTS.items():
            text = _text(path)
            text = re.sub(r"\{% else %\}.*?\{% endif %\}", "", text)
            self.assertEqual(re.findall(r"blank\(\d*\)|money\(None\)|___|\[ \]", text), [], key)


# --------------------------------------------------------------------------- summary


class TestSummary(unittest.TestCase):
    def test_each_has_one_that_ends_with_the_agreement_governing(self):
        for key in AGREEMENTS:
            html = summary.summary_html(_Doc(template_key=key))
            self.assertIn("where the two differ, the agreement governs", html, key)
            self.assertLessEqual(4, html.count("<li>"), key)
            self.assertLessEqual(html.count("<li>"), 7, key)
            self.assertNotIn("$0.00", html, key)
            self.assertNotIn("About a year", html, key)

    def test_the_nda_names_its_purpose_and_date_only_when_set(self):
        bare = summary.summary_html(_Doc(template_key="nda"))
        self.assertIn("in connection with a proposed business relationship:", bare)
        self.assertIn("until ten years after the Effective Date.", bare)
        filled = summary.summary_html(_Doc(template_key="nda", nda_purpose="joint design", contract_date="2026-10-13"))
        self.assertIn("in connection with joint design:", filled)
        self.assertIn("until ten years after the Effective Date (2026-10-13).", filled)

    def test_the_architect_summary_follows_the_statement_of_work(self):
        bare = summary.summary_html(_Doc(template_key="architect"))
        self.assertIn("What we do:</b> the services in the Statement of Work.", bare)
        self.assertIn("or our customary rates monthly where it does not say.", bare)
        filled = summary.summary_html(
            _Doc(
                template_key="architect",
                project_title="Plaza Fountain",
                architect_owner="City of Ogden",
                architect_payment_terms="$12,000, half at signing.\nBalance on delivery",
            )
        )
        self.assertIn("for Plaza Fountain, as part of your Prime Agreement with City of Ogden.", filled)
        self.assertIn("as the Statement of Work sets out: $12,000, half at signing; Balance on delivery. Invoices", filled)
        self.assertNotIn("customary rates", filled)
        escaped = summary.summary_html(_Doc(template_key="architect", architect_owner="<script>x</script>"))
        self.assertNotIn("<script>", escaped)

    def test_every_fixed_figure_it_quotes_is_in_the_agreement(self):
        """The summary restates clauses; this catches a clause changed in the repo template without
        the summary being changed with it."""
        clauses = {
            NDA: (
                "within Forty-Eight (48) hours", "Within Three (3) days", "confidential for the Ten (10) years",
                "until ten (10) years after the Effective Date", "without the requirement of posting a bond",
                "reasonable attorneys’ fees", "laws of the State of Utah", "state and federal courts in Utah",
                "reverse engineer", "Not export or re-export",
            ),
            ARCHITECT: (
                "customary rates for Services monthly", "due 30 days from receipt of invoice",
                "except U.S. taxes based on Sapphire’s net income", "pre-approved travel",
                "ten (10) days prior written notice", "within 30 days of delivery", "“AS IS”",
                "giving the other party 30 days written notice", "uncured for 30 days",
                "IN THE LAST TWELVE MONTHS UNDER THE APPLICABLE STATEMENT OF WORK", "in a federal court in the State of Utah",
                "in state court in Salt Lake County, Utah", "permits, licenses and inspections",
            ),
            EMPLOYEE: (
                "All hours worked must be billable to a client job or project", "Assist in company marketing efforts",
                "For a period of 36 months", "36 months past termination of employment", "CEO/Owner of the Company",
                "made public through no fault of the Employee or Contractor", "sole and exclusive property of the Company",
            ),
        }
        for path, phrases in clauses.items():
            text = _text(path)
            for phrase in phrases:
                self.assertIn(phrase, text, f"{path.name}: {phrase}")
        source = (APP / "project_enhancements" / "contract_summary.py").read_text(encoding="utf-8")
        for figure in ("48 hours", "3 days", "10 years", "ten years", "30 days", "10 days' written notice",
                       "last 12 months", "Salt Lake County", "36 months", "CEO/Owner"):
            self.assertIn(figure, source)

    def test_dispatch(self):
        for key in AGREEMENTS:
            self.assertIn(key, summary.BUILDERS)


# --------------------------------------------------------------------------- live-template patch


class TestPatch(unittest.TestCase):
    def test_replaces_only_the_shipped_bodies(self):
        self.assertEqual(layout_patch.REPLACEABLE_BODY_SHA256, {key: {sha} for key, sha in SHIPPED.items()})
        self.assertEqual(set(layout_patch.TEMPLATE_FILES), set(AGREEMENTS))
        for key, (path, _party, _word) in AGREEMENTS.items():
            self.assertEqual(layout_patch.TEMPLATE_FILES[key], path.name)
            new = _text(path)
            self.assertEqual(layout_patch.decide(key, new, new), "current")
            self.assertEqual(layout_patch.decide(key, "edited on the site", new), "diverged")
            # The new body must not itself be replaceable, or every migrate would rewrite it.
            self.assertNotIn(terms_patch.body_sha256(new), layout_patch.REPLACEABLE_BODY_SHA256[key])
        fake = "the shipped nda body"
        layout_patch.REPLACEABLE_BODY_SHA256["nda"].add(terms_patch.body_sha256(fake))
        try:
            self.assertEqual(layout_patch.decide("nda", fake, _text(NDA)), "replace")
            self.assertEqual(layout_patch.decide("architect", fake, _text(ARCHITECT)), "diverged")  # never crosses types
        finally:
            layout_patch.REPLACEABLE_BODY_SHA256["nda"].discard(terms_patch.body_sha256(fake))

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
        old = "the shipped architect body"
        layout_patch.REPLACEABLE_BODY_SHA256["architect"].add(terms_patch.body_sha256(old))
        try:
            writes, frozen, errors = self.run_patch(
                {"architect": old, "nda": "an nda body edited on the site", "employee_contractor": _text(EMPLOYEE)},
                contracts=[{"name": "SF-ARCH-2026-0001", "template_key": "architect", "docstatus": 1, "status": "Signed"}],
            )
        finally:
            layout_patch.REPLACEABLE_BODY_SHA256["architect"].discard(terms_patch.body_sha256(old))
        self.assertEqual(frozen, ["SF-ARCH-2026-0001"])
        self.assertEqual([(w[0], w[1]) for w in writes], [("Contract Template", "architect")])
        self.assertEqual(writes[0][2][1], _text(ARCHITECT))
        self.assertEqual(errors, ["Mutual Non-Disclosure Agreement layout: template not updated"])

    def test_waits_while_a_signing_link_is_out(self):
        old = "the shipped employee-contractor body"
        layout_patch.REPLACEABLE_BODY_SHA256["employee_contractor"].add(terms_patch.body_sha256(old))
        try:
            writes, _frozen, errors = self.run_patch(
                {"employee_contractor": old},
                contracts=[{"name": "SF-EC-2026-0002", "template_key": "employee_contractor", "docstatus": 1}],
                requests=["CSR-2026-00012"],
            )
        finally:
            layout_patch.REPLACEABLE_BODY_SHA256["employee_contractor"].discard(terms_patch.body_sha256(old))
        self.assertEqual(writes, [])
        self.assertTrue(any("not updated yet" in e for e in errors))

    def test_never_raises_and_leaves_current_bodies_alone(self):
        writes, _frozen, errors = self.run_patch(
            {"nda": RuntimeError("db down"), "architect": _text(ARCHITECT), "employee_contractor": _text(EMPLOYEE)}
        )
        self.assertEqual(writes, [])
        self.assertEqual(errors, ["Mutual Non-Disclosure Agreement layout patch failed"])
        self.assertEqual(self.run_patch({})[0], [])  # none of the templates on the site

    def test_registered_after_the_rental_owner_layout_patch(self):
        lines = [line for line in _text(APP / "patches.txt").split("\n") if line and not line.startswith("#")]
        self.assertIn("erpnext_enhancements.patches.refresh_general_agreement_layout", lines)
        self.assertGreater(
            lines.index("erpnext_enhancements.patches.refresh_general_agreement_layout"),
            lines.index("erpnext_enhancements.patches.refresh_rental_owner_agreement_layout"),
        )

    def test_has_its_own_ci_step(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("python -m unittest erpnext_enhancements.tests.test_general_agreement_layout", ci)


if __name__ == "__main__":
    unittest.main()
