"""Bench-free tests for the Rental Agreement and Owner Contract reworked like the maintenance agreement.

Nik, 2026-10-13, after the Maintenance Services Agreement gained the Word copy's layout and "an
input for everything it prints": "do the rental and owner agreements the same way." This suite
pins what would otherwise fail silently:

* both templates render, with no undefined name, for an empty contract and a filled one;
* every clause hangs its number (``ct-clause``), numbers are unique and in order, and every
  class the templates use is styled by the print format;
* every ``doc.<field>`` an agreement prints exists on Project Contract and is shown for that
  contract type by its own and its section's ``depends_on`` (asserted for maintenance too), so
  the builder offers a place to fill it; the only blanks left are the ones completed by hand;
* client initials: valid, unique keys inside the clauses they acknowledge;
* the owner contract can be e-signed (``sig(``);
* the live-template patch replaces only the two shipped v1.611.0 bodies and never raises.

Stubs a minimal ``frappe`` in ``setUpModule`` (never at import), like
``test_contract_builder_terms``. The render cases need ``jinja2``, which CI installs.

Run: python -m unittest erpnext_enhancements.tests.test_rental_owner_agreement_layout
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
RENTAL = TEMPLATES / "rental_agreement.html"
OWNER = TEMPLATES / "owner_contract.html"
MAINTENANCE = TEMPLATES / "maintenance_services_agreement.html"
DOCTYPES = APP / "project_enhancements" / "doctype"
PC_JSON = DOCTYPES / "project_contract" / "project_contract.json"

#: The live bodies on prod on 2026-10-13, byte-identical to the v1.611.0 files.
SHIPPED = {
    "rental": "7274e6bd87eca74f6df9b29cde2a348562261029bb5fe80637510eb5ce354149",
    "owner": "43490ef31d7fa831c7af7368c92293b472cb4edc4c43be710fe2de773bdd7079",
}

EXPECTED_INITIALS = {
    "rental": ["overholding", "late_payment", "deposit", "damage", "risk", "liability", "cancellation", "arbitration"],
    "owner": [
        "construction_payment",
        "change_orders",
        "utilities",
        "liability",
        "termination",
        "design_license",
        "arbitration",
        "lien_rights",
    ],
}

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
                "erpnext_enhancements.patches.refresh_rental_owner_agreement_layout",
                "erpnext_enhancements.feature_flags",
            )
        ):
            sys.modules.pop(mod, None)
    from erpnext_enhancements.patches import refresh_rental_owner_agreement_layout as _layout
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


def _render(path, doc, service_options=None, phases=None):
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
        phases=phases or {},
        service_options=service_options or {},
        hold_points=[],
        frappe=_Doc(),
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
        for key, path in (("rental", RENTAL), ("owner", OWNER)):
            html = _render(path, _empty(key))
            self.assertComplete(html)
            self.assertIn("City / State / Zip:", html)  # no address yet: the paper line stays

    def test_a_filled_rental_prints_its_inputs(self):
        doc = _empty("rental")
        doc.update(
            agreement_party_name="Hardware Buildings HOA",
            party_display="Salt Development",
            billing_address="455 West 200 North",
            billing_city_state_zip="Salt Lake City, UT 84103",
            balance_due_days=7,
            rental_start_date="2026-11-01",
            rental_end_date="2026-11-03",
            equipment_items=[_Doc(description="Tiered fountain", serial_id="TF-01")],
            base_rental_fee=900,
            other_fee_label="Lighting",
            other_fee=50,
            total_rental_amount=950,
            security_deposit=300,
            total_due_at_signing=775,
        )
        html = _render(RENTAL, doc)
        self.assertComplete(html)
        for text in (
            "<b>Renter Name:</b></td><td>Hardware Buildings HOA",
            "<b>City / State / Zip:</b></td><td>Salt Lake City, UT 84103",
            "no later than 7 days prior to the Rental Start Date",
            "Tiered fountain",
            "Other (Lighting)",
        ):
            self.assertIn(text, html)
        self.assertNotIn(">Salt Development</td>", html)
        self.assertNotIn("City / State / Zip:", _render(RENTAL, _Doc(doc, billing_city_state_zip=None)))

    def test_a_filled_owner_contract_prints_its_inputs(self):
        doc = _empty("owner")
        doc.update(
            party_display="Hardware Buildings",
            billing_address="455 West 200 North, Salt Lake City, UT 84103",
            property_type="Other",
            property_type_other="Mixed use",
            design_revision_rounds=2,
            additional_revision_rate=150,
            construction_scope="Basin and spillway\nPump vault",
            visit_frequency="Bi-Weekly",
            annual_maintenance_fee=17940,
            maintenance_deposit=500,
            invoicing_frequency="Monthly",
            milestones=[_Doc(milestone="Deposit", due_upon="Signing", percent=30, amount=15000)],
            milestones_total=15000,
        )
        options = {
            "standard": _Doc(included=1, price=690),
            "startup": _Doc(included=0, price=500),
            "as_needed": _Doc(included=1, price=180),
        }
        phases = {"maintenance": _Doc(included=1, fee=17940, retainer=500)}
        html = _render(OWNER, doc, service_options=options, phases=phases)
        self.assertComplete(html)
        for text in (
            "Other: Mixed use",
            "Up to 2 rounds of revisions per design phase (additional revisions at $150.00 / hr)",
            "[x]  Standard Maintenance Plan: 26 visits per  [ ] month  [x] year   at $690.00 per visit",
            "[ ]  Seasonal Startup (Spring): $500.00 per event",
            "[x]  As-Needed / On-Call Service: $180.00 per hour",
            "[ ]  Startup + Winterization Package: ____ per year",
            "$17,940.00",
            "[x] Monthly",
        ):
            self.assertIn(text, html)
        self.assertEqual(html.count("Basin and spillway<br>Pump vault"), 2)  # §3.1 and Exhibit A
        self.assertNotIn("City / State / Zip:", html)  # the address already carries it

    def test_a_custom_visit_schedule_fills_the_count(self):
        doc = _empty("owner")
        doc.update(visit_frequency="Custom", custom_visit_schedule="Weekly in summer")
        html = _render(OWNER, doc, service_options={"standard": _Doc(included=1, price=690)})
        self.assertIn("Standard Maintenance Plan: Weekly in summer visits per  [ ] month  [ ] year", html)

    def test_both_sign_with_sig(self):
        for key, path in (("rental", RENTAL), ("owner", OWNER)):
            html = _render(path, _empty(key))
            signatures = html[html.index("<h1>SIGNATURES</h1>"):]
            self.assertIn("<sig:provider>", signatures, key)
            self.assertIn("<sig:client>", signatures, key)
            self.assertNotIn("Signature: ____", signatures, key)

    def test_initials_are_valid_unique_and_bounded(self):
        for key, path in (("rental", RENTAL), ("owner", OWNER)):
            slots = initials.initial_slots(_render(path, _empty(key)))
            keys = [slot_key for slot_key, _label in slots]
            self.assertEqual(keys, EXPECTED_INITIALS[key])
            self.assertEqual(len(keys), len(set(keys)))
            self.assertLessEqual(len(keys), initials.MAX_INITIALS)
            for slot_key, label in slots:
                self.assertTrue(initials.INITIAL_KEY_RE.match(slot_key), slot_key)
                self.assertTrue(label, slot_key)

    def test_the_summary_opens_both(self):
        for key, path, heading in (
            ("rental", RENTAL, "RENTER &amp; RENTAL INFORMATION"),
            ("owner", OWNER, "PROJECT &amp; OWNER INFORMATION"),
        ):
            doc = _empty(key)
            doc.update(total_rental_amount=950, phases=[_Doc(phase_key="design", included=1, fee=8000)])
            html = _render(path, doc)
            self.assertLess(html.index("What you are agreeing to"), html.index(heading), key)


# --------------------------------------------------------------------------- layout


class TestLayout(unittest.TestCase):
    def test_every_numbered_clause_hangs_its_number_in_order(self):
        for path, count in ((RENTAL, 51), (OWNER, 59)):
            text = _text(path)
            self.assertEqual(re.findall(r"<p>\d+\.\d+\s", text), [], path.name)
            self.assertEqual(re.findall(r"<h2>\s*\d", text), [], path.name)
            numbers = re.findall(r'<p class="ct-clause"><span class="ct-num">(\d+)\.(\d+)</span>', text)
            as_ints = [(int(a), int(b)) for a, b in numbers]
            self.assertEqual(len(as_ints), count, path.name)
            self.assertEqual(as_ints, sorted(set(as_ints)), path.name)  # unique and ascending
            # Each section's clauses start at .1 and run without a gap.
            for section in {a for a, _b in as_ints}:
                subs = [b for a, b in as_ints if a == section]
                self.assertEqual(subs, list(range(1, len(subs) + 1)), f"{path.name} §{section}")
            # A clause number always follows its section heading's number.
            headings = [int(n) for n in re.findall(r"<h1>(\d+)\. ", text)]
            self.assertTrue({a for a, _b in as_ints} <= set(headings), path.name)

    def test_titled_clauses_lead_in_bold(self):
        for path, titles in (
            (RENTAL, ("Equipment Condition Documentation.", "Governing Law.", "JURY TRIAL WAIVER.", "Severability.")),
            (OWNER, ("Design Scope.", "Change Orders.", "Warranty Exclusions.", "Lien Rights.", "Arbitration.")),
        ):
            text = _text(path)
            for title in titles:
                self.assertRegex(text, r'<span class="ct-num">\d+\.\d+</span><b>' + re.escape(title) + "</b>")

    def test_the_owner_design_scope_is_a_list(self):
        lists = re.findall(r'<ul class="ct-list">(.*?)</ul>', _text(OWNER), flags=re.S)
        self.assertEqual([chunk.count("<li>") for chunk in lists], [7])
        self.assertEqual(len(re.findall(r'<p class="ct-option">', _text(OWNER))), 5)

    def test_the_print_format_styles_every_class_used(self):
        records = json.loads((APP / "fixtures" / "print_format.json").read_text(encoding="utf-8"))
        css = next(r for r in records if r["name"] == "Project Contract Print")["css"]
        for path in (RENTAL, OWNER):
            used = {c for attr in re.findall(r'class="([^"]+)"', _text(path)) for c in attr.split()}
            for cls in used:
                self.assertIn(f".{cls}", css, f"{path.name}: .{cls}")

    def test_initials_sit_inside_the_clause_they_acknowledge(self):
        for path in (RENTAL, OWNER):
            for line in _text(path).split("\n"):
                if "initial(" in line:
                    self.assertRegex(line, r'^<p class="ct-(clause|sub)">', path.name)
                    self.assertTrue(line.endswith(") }}</p>"), line[:80])


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

    def check(self, template_key, path, extra=()):
        visibility = _visibility()
        printed = set(re.findall(r"\bdoc\.([a-z_][a-z0-9_]*)", _text(path))) | set(extra)
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
        reachable = {f["fieldname"] for step in builder.builder_steps(template_key, "Customer") for f in step["fields"]}
        self.assertEqual(printed - builder.SKIP_FIELDS - {"total_due_at_signing"} - reachable, set(), template_key)

    def test_rental(self):
        self.check("rental", RENTAL)

    def test_owner(self):
        # §4.2 and the phase table read these through the context's service_options / phases.
        self.check("owner", OWNER, extra=("service_options", "phases"))

    def test_maintenance(self):
        self.check("maintenance", MAINTENANCE, extra=("service_options",))

    def test_the_new_fields(self):
        fields = {f["fieldname"]: f for f in json.loads(PC_JSON.read_text(encoding="utf-8"))["fields"]}
        for name, fieldtype, key in (
            ("balance_due_days", "Int", "rental"),
            ("design_revision_rounds", "Int", "owner"),
            ("additional_revision_rate", "Currency", "owner"),
            ("construction_scope", "Small Text", "owner"),
        ):
            self.assertEqual(fields[name]["fieldtype"], fieldtype, name)
            self.assertEqual(fields[name]["depends_on"], f'eval:doc.template_key=="{key}"', name)
            self.assertNotIn("default", fields[name], name)  # the agreements state none
        # Payment method & co stay with the maintenance agreement, which is the only one that prints them.
        for name in ("payment_method", "payment_method_notes", "invoice_due_days", "payment_section_choice"):
            self.assertFalse(builder.section_applies(fields[name]["depends_on"], "owner"), name)

    def test_no_blank_is_left_without_an_input_except_the_hand_completed_parts(self):
        """The rental's Exhibit A condition report and inspections are filled in on site at delivery and
        return; an empty equipment or milestone table prints paper lines instead of rows."""
        rental = _text(RENTAL)
        rental = rental[: rental.index("<h1>EXHIBIT A")] + rental[rental.index("<h1>SIGNATURES</h1>"):]
        owner = _text(OWNER)
        for text, fallback in ((rental, "{% if not doc.equipment_items %}"), (owner, "{% if not doc.milestones %}")):
            start = text.index(fallback)
            text = text[:start] + text[text.index("{% endif %}", start):]
            self.assertEqual(re.findall(r"blank\(\d*\)|money\(None\)|___|\[ \]", text), [])

    def test_the_owner_contract_seeds_the_plan_rows(self):
        source = _text(DOCTYPES / "project_contract" / "project_contract.py")
        self.assertIn('PLAN_OPTION_KEYS = ("maintenance", "owner")', source)
        seed = source[source.index("def _seed_fixed_rows("):source.index("def _ensure_maintenance_options(")]
        self.assertIn("if doc.template_key in PLAN_OPTION_KEYS:", seed)
        validate = source[source.index("def validate(self)"):source.index("def _fetch_template_props")]
        self.assertIn('self.get("template_key") in PLAN_OPTION_KEYS', validate)
        totals = source[source.index("def _compute_totals("):source.index("def _compute_maintenance_annual_fee(")]
        owner = totals[totals.index('== "owner"'):totals.index('== "rental"')]
        self.assertIn("self._compute_maintenance_annual_fee()", owner)  # never clobbers a typed fee


# --------------------------------------------------------------------------- summary


class TestSummary(unittest.TestCase):
    def test_rental_names_the_balance_due_days_only_when_set(self):
        with_days = summary.summary_html(_Doc(template_key="rental", balance_due_days=7))
        self.assertIn("the rest 7 days before the rental starts", with_days)
        self.assertIn("the rest before the rental starts", summary.summary_html(_Doc(template_key="rental")))

    def test_owner_states_the_maintenance_plan(self):
        html = summary.summary_html(
            _Doc(
                template_key="owner",
                phases=[_Doc(phase_key="maintenance", included=1, fee=0)],
                visit_frequency="Bi-Weekly",
                service_options=[
                    _Doc(option_key="standard", included=1, price=690),
                    _Doc(option_key="winterization", included=1, price=None),
                    _Doc(option_key="startup", included=0, price=500),
                ],
            )
        )
        self.assertIn("standard maintenance visits every two weeks at $690.00 per visit and a fall winterization.", html)
        self.assertIn("It renews every year", html)
        self.assertNotIn("$0.00", html)
        self.assertNotIn("spring startup", html)
        self.assertNotIn("base rate", html)  # the owner contract does not say base + chemicals

    def test_owner_without_a_plan_keeps_the_short_line(self):
        html = summary.summary_html(_Doc(template_key="owner", phases=[_Doc(phase_key="maintenance", included=1)]))
        self.assertIn("<b>Maintenance:</b> renews every year", html)


# --------------------------------------------------------------------------- live-template patch


class TestPatch(unittest.TestCase):
    def test_replaces_only_the_two_shipped_bodies(self):
        self.assertEqual(layout_patch.REPLACEABLE_BODY_SHA256, {key: {sha} for key, sha in SHIPPED.items()})
        for key, path in (("rental", RENTAL), ("owner", OWNER)):
            new = _text(path)
            self.assertEqual(layout_patch.decide(key, new, new), "current")
            self.assertEqual(layout_patch.decide(key, "edited on the site", new), "diverged")
            # The new body must not itself be replaceable, or every migrate would rewrite it.
            self.assertNotIn(terms_patch.body_sha256(new), layout_patch.REPLACEABLE_BODY_SHA256[key])
        fake = "the v1.611.0 rental body"
        layout_patch.REPLACEABLE_BODY_SHA256["rental"].add(terms_patch.body_sha256(fake))
        try:
            self.assertEqual(layout_patch.decide("rental", fake, _text(RENTAL)), "replace")
            self.assertEqual(layout_patch.decide("owner", fake, _text(OWNER)), "diverged")  # never crosses types
        finally:
            layout_patch.REPLACEABLE_BODY_SHA256["rental"].discard(terms_patch.body_sha256(fake))

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
        old = "the v1.611.0 owner body"
        layout_patch.REPLACEABLE_BODY_SHA256["owner"].add(terms_patch.body_sha256(old))
        try:
            writes, frozen, errors = self.run_patch(
                {"owner": old, "rental": "a rental body edited on the site"},
                contracts=[{"name": "SF-OC-2026-0001", "template_key": "owner", "docstatus": 1, "status": "Signed"}],
            )
        finally:
            layout_patch.REPLACEABLE_BODY_SHA256["owner"].discard(terms_patch.body_sha256(old))
        self.assertEqual(frozen, ["SF-OC-2026-0001"])
        self.assertEqual([(w[0], w[1]) for w in writes], [("Contract Template", "owner")])
        self.assertEqual(writes[0][2][1], _text(OWNER))
        self.assertTrue(any("Rental Agreement layout: template not updated" in e for e in errors))

    def test_waits_while_a_signing_link_is_out(self):
        old = "the v1.611.0 rental body"
        layout_patch.REPLACEABLE_BODY_SHA256["rental"].add(terms_patch.body_sha256(old))
        try:
            writes, _frozen, errors = self.run_patch(
                {"rental": old},
                contracts=[{"name": "SF-RA-2026-0002", "template_key": "rental", "docstatus": 1}],
                requests=["CSR-2026-00012"],
            )
        finally:
            layout_patch.REPLACEABLE_BODY_SHA256["rental"].discard(terms_patch.body_sha256(old))
        self.assertEqual(writes, [])
        self.assertTrue(any("not updated yet" in e for e in errors))

    def test_never_raises_and_leaves_current_bodies_alone(self):
        writes, _frozen, errors = self.run_patch({"rental": RuntimeError("db down"), "owner": _text(OWNER)})
        self.assertEqual(writes, [])
        self.assertEqual(errors, ["Rental Agreement layout patch failed"])
        self.assertEqual(self.run_patch({})[0], [])  # neither template on the site

    def test_registered_after_the_maintenance_layout_patch(self):
        lines = [line for line in _text(APP / "patches.txt").split("\n") if line and not line.startswith("#")]
        self.assertIn("erpnext_enhancements.patches.refresh_rental_owner_agreement_layout", lines)
        self.assertGreater(
            lines.index("erpnext_enhancements.patches.refresh_rental_owner_agreement_layout"),
            lines.index("erpnext_enhancements.patches.refresh_maintenance_agreement_layout"),
        )


if __name__ == "__main__":
    unittest.main()
