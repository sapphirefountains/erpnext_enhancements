"""Bench-free tests for the Contract Builder and the 2026-10-13 maintenance terms.

Nik, 2026-10-13, comparing the Hardware / Salt Development agreement with what ERPNext
generates, asked for: a step-by-step builder for every contract type with the agreement
previewed as it is filled in; sign-here or send at the end; client initials on the
clauses that matter, each drawn on its own; Standard Maintenance as base rate plus
chemicals; $250 / $350 emergency rates; a 12% increase at each automatic renewal; and
any payment method on file, a card being optional. This suite pins each of those where
a mistake would be silent:

* the template renders for every payment method and prints the terms as decided;
* every field the maintenance agreement prints is reachable from a builder step, so the
  walk can never leave a printed blank with no place to fill it;
* initials: the blank box is deterministic (execution's drift check depends on it), the
  server asks for exactly the boxes in the signer's snapshot, and refuses a copied drawing;
* the template patch replaces only a body this repo shipped;
* the renewal increase compounds per term and only touches rates that are set.

Stubs a minimal ``frappe`` in ``setUpModule`` (never at import), like
``test_contract_esign``. The template cases need ``jinja2``, which CI installs.

Run: python -m unittest erpnext_enhancements.tests.test_contract_builder_terms
"""

import ast
import base64
import hashlib
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
TEMPLATE = APP / "templates" / "contracts" / "maintenance_services_agreement.html"
PC_JSON = APP / "project_enhancements" / "doctype" / "project_contract" / "project_contract.json"
OPTION_JSON = APP / "project_enhancements" / "doctype" / "contract_service_option" / "contract_service_option.json"
CSR_JSON = (
    APP / "project_enhancements" / "doctype" / "contract_signature_request" / "contract_signature_request.json"
)
SMC_JSON = (
    APP / "sapphire_maintenance" / "doctype" / "sapphire_maintenance_contract" / "sapphire_maintenance_contract.json"
)

try:
    import jinja2
except ImportError:  # pragma: no cover - CI installs it
    jinja2 = None

initials = None
builder = None
renewal = None
terms_patch = None


class _DF(dict):
    """A docfield: a dict that also answers attribute access, as frappe's does."""

    def __getattr__(self, key):
        return self.get(key)


def _meta(path):
    return types.SimpleNamespace(
        fields=[_DF(f) for f in json.loads(path.read_text(encoding="utf-8"))["fields"]]
    )


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
    metas = {"Project Contract": _meta(PC_JSON), "Contract Service Option": _meta(OPTION_JSON)}
    for child in ("Contract Phase Item", "Contract Milestone", "Contract Equipment Item", "Contract Rate Line"):
        path = APP / "project_enhancements" / "doctype" / child.lower().replace(" ", "_") / f"{child.lower().replace(' ', '_')}.json"
        metas[child] = _meta(path)
    frappe.get_meta = lambda doctype: metas[doctype]
    frappe.db = types.SimpleNamespace(
        get_value=lambda *a, **k: None,
        get_single_value=lambda *a, **k: None,
        set_value=lambda *a, **k: None,
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

    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils


def setUpModule():
    global initials, builder, renewal, terms_patch
    _install_frappe_stub()
    for mod in list(sys.modules):
        if mod.startswith(
            (
                "erpnext_enhancements.project_enhancements",
                "erpnext_enhancements.api.maintenance_renewal",
                "erpnext_enhancements.patches.update_maintenance_agreement_terms",
                "erpnext_enhancements.feature_flags",
            )
        ):
            sys.modules.pop(mod, None)
    from erpnext_enhancements.api import maintenance_renewal as _renewal
    from erpnext_enhancements.patches import update_maintenance_agreement_terms as _patch
    from erpnext_enhancements.project_enhancements import contract_builder as _builder
    from erpnext_enhancements.project_enhancements.esign import initials as _initials

    initials, builder, renewal, terms_patch = _initials, _builder, _renewal, _patch


def tearDownModule():
    sys.modules.pop("frappe", None)
    sys.modules.pop("frappe.utils", None)


def _png(seed):
    """A distinct, valid-looking PNG data URI per seed (the image validator is stubbed)."""
    return "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + seed.encode()).decode()


def _accept(value):
    """The portal's image check, reduced to "is it a PNG data URI" for these cases."""
    value = str(value or "")
    return value if value.startswith("data:image/png;base64,") else ""


# --------------------------------------------------------------------------- initials


class TestInitialsMarkup(unittest.TestCase):
    def test_the_blank_box_is_deterministic(self):
        """Execution re-renders the unsigned agreement and compares it with the snapshot the
        signer was sent; a box that varied between renders would flag every signature."""
        first = initials.initial_markup({}, "renewal", "Automatic renewal")
        self.assertEqual(first, initials.initial_markup({}, "renewal", "Automatic renewal"))
        self.assertIn('data-initial="renewal"', first)
        self.assertIn('data-label="Automatic renewal"', first)
        self.assertNotIn("<img", first)

    def test_a_signed_box_prints_the_drawing_and_is_not_asked_for_again(self):
        html = initials.initial_markup({"renewal": _png("a")}, "renewal", "x")
        self.assertIn('class="ct-initial is-signed"', html)
        self.assertIn("<img", html)
        self.assertEqual(initials.initial_slots(html), [])

    def test_a_bad_key_renders_nothing(self):
        for key in ("Renewal", "two words", "x" * 41, "", None, 'a"b'):
            self.assertEqual(initials.initial_markup({}, key, "label"), "", key)

    def test_labels_are_escaped_and_survive_the_round_trip(self):
        html = initials.initial_markup({}, "arbitration", "Arbitration & attorneys' \"fees\"")
        self.assertNotIn('"fees"', html)
        self.assertEqual(
            initials.initial_slots(html), [("arbitration", "Arbitration & attorneys' \"fees\"")]
        )

    def test_slots_keep_document_order_and_ask_once_per_key(self):
        html = "".join(
            initials.initial_markup({}, key, key.title()) for key in ("access", "payment", "access", "renewal")
        )
        self.assertEqual([k for k, _ in initials.initial_slots(html)], ["access", "payment", "renewal"])

    def test_signed_initials_ignore_anything_but_png(self):
        rows = [
            {"key": "a", "image": _png("a")},
            {"key": "b", "image": "data:image/svg+xml;base64,PHN2Zz4="},
            {"key": "", "image": _png("c")},
            "junk",
        ]
        self.assertEqual(initials.signed_initials({"initials_json": json.dumps(rows)}), {"a": _png("a")})
        self.assertEqual(initials.signed_initials({"initials_json": "not json"}), {})
        self.assertEqual(initials.signed_initials(None), {})


class TestInitialsValidation(unittest.TestCase):
    def snapshot(self, *keys):
        return "<p>" + "".join(initials.initial_markup({}, key, f"Clause {key}") for key in keys) + "</p>"

    def test_no_boxes_needs_nothing(self):
        self.assertEqual(initials.validate_initials("{}", "<p>no boxes</p>", _accept), ([], None))

    def test_every_box_is_required(self):
        captured, error = initials.validate_initials(
            json.dumps({"access": _png("1")}), self.snapshot("access", "payment", "renewal"), _accept
        )
        self.assertEqual(captured, [])
        self.assertIn("2 of 3", error)

    def test_a_copied_drawing_is_refused(self):
        """Nik: "each initial needs to be done as its own, not copy paste the initial"."""
        same = _png("same")
        captured, error = initials.validate_initials(
            json.dumps({"access": same, "payment": same}), self.snapshot("access", "payment"), _accept
        )
        self.assertEqual(captured, [])
        self.assertIn("drawn separately", error)

    def test_separate_drawings_are_accepted_in_order(self):
        supplied = {"payment": _png("2"), "access": _png("1")}
        captured, error = initials.validate_initials(json.dumps(supplied), self.snapshot("access", "payment"), _accept)
        self.assertIsNone(error)
        self.assertEqual([row["key"] for row in captured], ["access", "payment"])
        self.assertEqual(captured[0]["label"], "Clause access")
        self.assertEqual(
            captured[0]["sha256"], hashlib.sha256(base64.b64decode(_png("1").split(",", 1)[1])).hexdigest()
        )

    def test_boxes_come_from_the_snapshot_not_the_caller(self):
        """Extra keys the caller invents are ignored; missing real ones still fail."""
        captured, error = initials.validate_initials(
            json.dumps({"invented": _png("x")}), self.snapshot("access"), _accept
        )
        self.assertEqual(captured, [])
        self.assertIn("1 of 1", error)

    def test_garbage_is_treated_as_nothing_drawn(self):
        for raw in ("not json", "[1, 2]", None, 42):
            captured, error = initials.validate_initials(raw, self.snapshot("access"), _accept)
            self.assertEqual(captured, [])
            self.assertTrue(error)

    def test_an_absurd_number_of_boxes_is_refused(self):
        keys = [f"k{i}" for i in range(initials.MAX_INITIALS + 1)]
        supplied = {key: _png(key) for key in keys}
        captured, error = initials.validate_initials(json.dumps(supplied), self.snapshot(*keys), _accept)
        self.assertEqual(captured, [])
        self.assertTrue(error)

    def test_the_portal_delegates_and_reuses_the_signature_check(self):
        source = (APP / "project_enhancements" / "esign" / "portal.py").read_text(encoding="utf-8")
        self.assertIn("validate_initials(", source)
        self.assertIn("_validated_signature_image,", source)
        self.assertIn("_validated_initials(initials, request.document_snapshot)", source)


# --------------------------------------------------------------------------- template


EXPECTED_INITIALS = ["access", "payment", "chemicals", "emergency", "renewal", "cancellation", "liability", "arbitration"]
METHODS = ["", "Credit / Debit Card", "Bank Account (ACH)", "Invoice (Check or Bank Transfer)", "Other"]


class _Doc(dict):
    def __getattr__(self, key):
        return self.get(key)


@unittest.skipUnless(jinja2, "jinja2 is not installed")
class TestMaintenanceTemplate(unittest.TestCase):
    def render(self, **overrides):
        blank = lambda width=30: "____"  # noqa: E731
        fill = lambda v, w=30: blank() if v in (None, "") else str(v)  # noqa: E731
        money = lambda v, w=14: blank() if not v else f"${float(v):,.2f}"  # noqa: E731

        def qty(v):
            if v in (None, "") or not float(v):
                return None
            number = float(v)
            return str(int(number)) if number == int(number) else f"{number:g}"

        doc = _Doc(
            party_display="Salt Development",
            payment_method="Credit / Debit Card",
            payment_section_choice="Payment Link",
            emergency_rate=250,
            after_hours_emergency_rate=350,
            emergency_minimum_hours=1,
            business_hours="Monday to Friday, 7:00 a.m. to 5:00 p.m.",
            renewal_increase_percent=12,
            invoice_due_days=30,
            annual_maintenance_fee=17940,
        )
        doc.update(overrides)
        env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
        return env.from_string(TEMPLATE.read_text(encoding="utf-8")).render(
            doc=doc,
            fill=fill,
            blank=blank,
            cb=lambda c: "[x]" if c else "[ ]",
            money=money,
            dt=lambda v, w=18: blank() if not v else str(v),
            multiline=lambda v, w=80, n=3: blank() if not v else str(v),
            qty=qty,
            pct=lambda v, w=6: f"{qty(v)}%" if qty(v) else blank() + "%",
            sig=lambda party="client", width=30: blank(),
            initial=lambda key, label="": initials.initial_markup({}, key, label),
            signature=_Doc(),
            service_options={"standard": _Doc(included=1, price=690, unit="visit")},
        )

    def test_renders_completely_for_every_payment_method(self):
        for method in METHODS:
            html = self.render(payment_method=method)
            self.assertNotIn("{{", html, method)
            self.assertNotIn("{%", html, method)

    def test_eight_initials_each_with_its_own_key(self):
        html = self.render()
        self.assertEqual([key for key, _ in initials.initial_slots(html)], EXPECTED_INITIALS)
        for key, label in initials.initial_slots(html):
            self.assertTrue(label, key)

    def test_chemicals_are_always_billed_in_addition(self):
        html = self.render()
        self.assertIn("plus chemicals", html)
        self.assertIn("Chemicals are not included in the base rate", html)
        self.assertNotIn("cost of chemicals included", html)
        self.assertNotIn("included in the service fee", html)

    def test_emergency_rates_print_from_the_contract(self):
        html = self.render()
        self.assertIn("$250.00 per hour during business hours", html)
        self.assertIn("$350.00 per hour at any other time", html)
        self.assertIn("minimum charge of 1 hour(s)", html)

    def test_the_renewal_increase_is_stated_and_adjustable(self):
        self.assertIn("increases by 12%", self.render())
        self.assertIn("increases by 7.5%", self.render(renewal_increase_percent=7.5))
        none = self.render(renewal_increase_percent=0)
        self.assertIn("Rates carry over unchanged at renewal", none)
        self.assertNotIn("increases by", none)

    def test_a_card_is_optional_and_only_the_chosen_method_prints(self):
        clauses = {"(a) Card on file": "Credit / Debit Card", "(b) Bank account (ACH)": "Bank Account (ACH)",
                   "(c) Pay by invoice": "Invoice (Check or Bank Transfer)"}
        for method in METHODS[1:]:
            html = self.render(payment_method=method)
            self.assertIn("A credit card is not required", html)
            for clause, owner in clauses.items():
                expected = owner == method or (clause.startswith("(c)") and method == "Other")
                self.assertEqual(clause in html, expected, f"{clause} for {method}")
        blank_form = self.render(payment_method="")
        for clause in clauses:
            self.assertIn(clause, blank_form)

    def test_the_card_form_is_not_printed_for_an_invoice_client(self):
        html = self.render(payment_method="Invoice (Check or Bank Transfer)", payment_section_choice="Both")
        self.assertNotIn("Card Number:", html)
        self.assertIn("Due within 30 days of the invoice date", html)

    def test_exhibit_a_ticks_and_prints_a_custom_plan(self):
        html = self.render(custom_plan_details="Monthly lobby wall descale")
        self.assertIn("[x]  Custom Plan", html)
        self.assertIn("Monthly lobby wall descale", html)
        self.assertIn("[ ]  Custom Plan", self.render())


# --------------------------------------------------------------------------- template patch


class TestTermsPatch(unittest.TestCase):
    def test_replaces_only_a_body_this_repo_shipped(self):
        new = TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(terms_patch.decide(new, new), "current")
        self.assertEqual(terms_patch.decide("a body somebody wrote on the site", new), "diverged")
        shipped_before = "the v1.609.0 body"
        terms_patch.REPLACEABLE_BODY_SHA256.add(terms_patch.body_sha256(shipped_before))
        try:
            self.assertEqual(terms_patch.decide(shipped_before, new), "replace")
        finally:
            terms_patch.REPLACEABLE_BODY_SHA256.discard(terms_patch.body_sha256(shipped_before))

    def test_the_new_body_is_not_itself_replaceable(self):
        """Otherwise a re-run would see 'replace' forever and rewrite the record each migrate."""
        self.assertNotIn(
            terms_patch.body_sha256(TEMPLATE.read_text(encoding="utf-8")), terms_patch.REPLACEABLE_BODY_SHA256
        )

    def test_prod_body_hash_is_listed(self):
        # Prod's live body matched the v1.609.0 file byte for byte on 2026-10-13.
        self.assertIn(
            "4b77c564f827ccff3ab915a8cab6b582f72bb4493ff67ec730c4b0e07fba2967", terms_patch.REPLACEABLE_BODY_SHA256
        )

    def test_registered(self):
        patches = (APP / "patches.txt").read_text(encoding="utf-8")
        self.assertIn("erpnext_enhancements.patches.update_maintenance_agreement_terms", patches)


# --------------------------------------------------------------------------- renewal increase


class TestRenewalIncrease(unittest.TestCase):
    def row(self, **values):
        base = dict(visit_rate=690, recurring_amount=None, emergency_rate=250, after_hours_emergency_rate=350)
        base.update(values)
        return types.SimpleNamespace(**base)

    def test_twelve_percent_on_every_set_rate(self):
        self.assertEqual(
            renewal.escalated_rates(self.row(), 12),
            {"visit_rate": 772.8, "emergency_rate": 280.0, "after_hours_emergency_rate": 392.0},
        )

    def test_compounds_per_term(self):
        self.assertEqual(renewal.escalated_rates(self.row(), 12, terms=2)["visit_rate"], 865.54)

    def test_blank_or_zero_renews_at_the_same_rates(self):
        """Contracts from before v1.611.0 carry no percentage and must not change."""
        for percent in (None, "", 0, -5):
            self.assertEqual(renewal.escalated_rates(self.row(), percent), {})

    def test_recurring_amount_rises_too(self):
        self.assertEqual(renewal.escalated_rates(self.row(recurring_amount=7331.62), 12)["recurring_amount"], 8211.41)

    def test_the_renewal_job_reads_the_rates_it_raises(self):
        source = (APP / "api" / "maintenance_renewal.py").read_text(encoding="utf-8")
        self.assertIn('"renewal_increase_percent", *ESCALATED_RATES', source)

    def test_new_contracts_start_at_the_standard_and_old_ones_stay_blank(self):
        fields = {f["fieldname"]: f for f in json.loads(SMC_JSON.read_text(encoding="utf-8"))["fields"]}
        # No JSON default: on a normal doctype a default reaches every existing row through the
        # ALTER, which would put twelve percent on contracts signed under the old terms.
        self.assertNotIn("default", fields["renewal_increase_percent"])
        controller = (
            APP / "sapphire_maintenance" / "doctype" / "sapphire_maintenance_contract" / "sapphire_maintenance_contract.py"
        ).read_text(encoding="utf-8")
        self.assertIn("STANDARD_RENEWAL_INCREASE = 12", controller)
        self.assertIn('if self.is_new() and self.get("renewal_increase_percent") in (None, ""):', controller)


# --------------------------------------------------------------------------- builder


class TestBuilderSteps(unittest.TestCase):
    def keys(self, template_key, party_type="Customer"):
        return [step["key"] for step in builder.builder_steps(template_key, party_type)]

    def test_each_contract_type_walks_its_own_sections(self):
        self.assertEqual(
            self.keys("maintenance"),
            ["parties_section", "details_section", "maintenance_section", "maint_rates_section", "maint_fees_section"],
        )
        self.assertEqual(self.keys("rental"), ["parties_section", "details_section", "rental_section", "rental_fees_section"])
        self.assertEqual(
            self.keys("owner"),
            ["parties_section", "details_section", "owner_section", "design_section", "construction_section", "milestones_section"],
        )
        self.assertIn("scope_section", self.keys("sow", "Supplier"))
        self.assertIn("section_msa_rates", self.keys("msa", "Supplier"))
        for key in ("nda", "architect", "employee_contractor"):
            self.assertEqual(self.keys(key, "Any Party")[:2], ["parties_section", "details_section"])

    def test_no_step_offers_a_stamped_or_signed_field(self):
        for template_key in ("maintenance", "rental", "owner", "sow", "msa", "nda"):
            for step in builder.builder_steps(template_key, "Customer"):
                names = {f["fieldname"] for f in step["fields"]}
                self.assertFalse(names & builder.SKIP_FIELDS, f"{template_key}/{step['key']}")
                self.assertNotEqual(step["key"], "totals_section")

    def test_the_contract_date_leads_the_first_step(self):
        first = builder.builder_steps("maintenance", "Customer")[0]
        self.assertEqual(first["fields"][0]["fieldname"], "contract_date")

    def test_party_type_is_fixed_unless_the_template_lets_you_choose(self):
        def party_type(template_party_type):
            fields = builder.builder_steps("nda", template_party_type)[0]["fields"]
            return next(f for f in fields if f["fieldname"] == "party_type")

        self.assertTrue(party_type("Customer").get("read_only"))
        self.assertFalse(party_type("Any Party").get("read_only"))

    def test_every_field_the_agreement_prints_has_a_step(self):
        """The point of the walk: nothing the maintenance agreement prints is left without a
        place to fill it in. (Signature fields are filled by signing.)"""
        printed = set(re.findall(r"\bdoc\.([a-z_][a-z0-9_]*)", TEMPLATE.read_text(encoding="utf-8")))
        printed -= {"signed_by", "signed_on", "party_display"}
        reachable = {
            f["fieldname"] for step in builder.builder_steps("maintenance", "Customer") for f in step["fields"]
        }
        self.assertEqual(printed - reachable, set())

    def test_the_plan_is_a_fixed_menu_with_its_child_fields(self):
        step = next(s for s in builder.builder_steps("maintenance", "Customer") if s["key"] == "maintenance_section")
        options = next(f for f in step["fields"] if f["fieldname"] == "service_options")
        self.assertEqual(options["fixed_rows"], 1)
        self.assertEqual(
            {c["fieldname"] for c in options["child_fields"]}, {"option_key", "option_label", "included", "price", "unit"}
        )

    def test_save_assigns_only_what_the_steps_offer(self):
        allowed = builder._editable_fields(builder.builder_steps("maintenance", "Customer"))
        for field in ("payment_method", "renewal_increase_percent", "emergency_rate", "contract_date", "party"):
            self.assertIn(field, allowed)
        for field in ("status", "docstatus", "signed_by", "template_key", "party_type", "total_due_at_signing"):
            self.assertNotIn(field, allowed)
        self.assertIn("option_key", allowed["service_options"])

    def test_section_conditions(self):
        self.assertTrue(builder.section_applies(None, "rental"))
        self.assertTrue(builder.section_applies('eval:doc.template_key=="sow" || doc.template_key=="owner"', "owner"))
        self.assertFalse(builder.section_applies('eval:doc.template_key=="sow" || doc.template_key=="owner"', "rental"))
        self.assertTrue(builder.section_applies("eval:doc.payment_method", "rental"))

    def test_save_never_splats_the_payload(self):
        tree = ast.parse((APP / "project_enhancements" / "contract_builder.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update":
                self.fail("contract_builder must assign fields one by one, never doc.update(payload)")


# --------------------------------------------------------------------------- schema + wiring


class TestSchema(unittest.TestCase):
    def fields(self, path):
        return {f["fieldname"]: f for f in json.loads(path.read_text(encoding="utf-8"))["fields"]}

    def test_maintenance_agreement_fields_and_standards(self):
        fields = self.fields(PC_JSON)
        self.assertEqual(fields["emergency_rate"]["default"], "250")
        self.assertEqual(fields["after_hours_emergency_rate"]["default"], "350")
        self.assertEqual(fields["renewal_increase_percent"]["default"], "12")
        self.assertIn("Bank Account (ACH)", fields["payment_method"]["options"])
        self.assertIn("mandatory_depends_on", fields["payment_method"])

    def test_signature_request_evidence_is_read_only(self):
        fields = self.fields(CSR_JSON)
        for name in ("initials_json", "initials_count", "signing_channel", "presented_by"):
            self.assertEqual(fields[name].get("read_only"), 1, name)

    def test_in_person_signing_emails_nothing_and_records_who_presented_it(self):
        source = (APP / "project_enhancements" / "esign" / "api.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "start_in_person_signing")
        body = ast.get_source_segment(source, fn)
        self.assertNotIn("_email_invite", body)
        self.assertIn('request.signing_channel = "In Person"', body)
        self.assertIn("request.presented_by = frappe.session.user", body)
        self.assertIn("IN_PERSON_LINK_HOURS", body)
        def decorator_name(node):
            target = node.func if isinstance(node, ast.Call) else node
            return getattr(target, "attr", None) or getattr(target, "id", None)

        names = [decorator_name(d) for d in fn.decorator_list]
        self.assertEqual(names, ["whitelist", "rate_limit"])  # whitelist outermost, or it registers the inner function
        resend = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "resend_for_signature")
        self.assertIn('request.signing_channel = "Email Link"', ast.get_source_segment(source, resend))

    def test_signing_page_sends_initials_and_gates_on_them(self):
        js = (APP / "public" / "js" / "contract_sign" / "contract_sign.js").read_text(encoding="utf-8")
        self.assertIn("initials: JSON.stringify(collectInitials())", js)
        self.assertIn("pendingInitials().length", js)
        # Every set starts from an empty pad.
        self.assertIn("resetInitialPad();", js)

    def test_generate_contract_opens_the_builder(self):
        js = (APP / "public" / "js" / "contracts.js").read_text(encoding="utf-8")
        self.assertIn('frappe.set_route("contract-builder", r.message)', js)

    def test_builder_page_is_registered(self):
        page = json.loads(
            (APP / "project_enhancements" / "page" / "contract_builder" / "contract_builder.json").read_text(encoding="utf-8")
        )
        self.assertEqual(page["name"], "contract-builder")
        self.assertEqual(page["module"], "Project Enhancements")


if __name__ == "__main__":
    unittest.main()
