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
summary = None
summary_patch = None


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
    for name in ("add_days", "add_months", "add_years", "getdate", "nowdate"):
        setattr(utils, name, lambda *a, **k: None)
    frappe.utils = utils

    rate_limiter = types.ModuleType("frappe.rate_limiter")
    rate_limiter.rate_limit = lambda *a, **k: (lambda fn: fn)
    frappe.rate_limiter = rate_limiter

    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils
    sys.modules["frappe.rate_limiter"] = rate_limiter


def setUpModule():
    global initials, builder, renewal, terms_patch, summary, summary_patch
    _install_frappe_stub()
    for mod in list(sys.modules):
        if mod.startswith(
            (
                "erpnext_enhancements.project_enhancements",
                "erpnext_enhancements.api.maintenance_renewal",
                "erpnext_enhancements.patches.update_maintenance_agreement_terms",
                "erpnext_enhancements.patches.add_contract_summaries",
                "erpnext_enhancements.feature_flags",
            )
        ):
            sys.modules.pop(mod, None)
    from erpnext_enhancements.api import maintenance_renewal as _renewal
    from erpnext_enhancements.patches import add_contract_summaries as _summary_patch
    from erpnext_enhancements.patches import update_maintenance_agreement_terms as _patch
    from erpnext_enhancements.project_enhancements import contract_builder as _builder
    from erpnext_enhancements.project_enhancements import contract_summary as _summary
    from erpnext_enhancements.project_enhancements.esign import initials as _initials

    initials, builder, renewal, terms_patch = _initials, _builder, _renewal, _patch
    summary, summary_patch = _summary, _summary_patch


def tearDownModule():
    sys.modules.pop("frappe", None)
    sys.modules.pop("frappe.utils", None)
    sys.modules.pop("frappe.rate_limiter", None)


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
            template_key="maintenance",
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
            summary=lambda: summary.summary_html(doc),
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

    def test_everything_the_word_copy_fills_in_has_an_input(self):
        """Nik: "the matching should come from inputted information. If we are missing check boxes or
        fields or addresses or whatever, we need inputs for those so we can set them." Matching the
        Hardware / Salt Development agreement of 2026-09-30 needs each of these."""
        html = self.render(
            agreement_party_name="Salt Development — Hardware Buildings",
            billing_address="455 West 200 North",
            billing_city_state_zip="Salt Lake City, UT 84103",
            property_type="Other",
            property_type_other="Mixed use",
            visit_frequency="Custom",
            custom_visit_schedule="Weekly in summer",
            initial_term="Other",
            initial_term_other="Six (6) months",
        )
        for text in (
            "Salt Development — Hardware Buildings",
            "<b>City / State / Zip:</b></td><td>Salt Lake City, UT 84103",
            "Other: Mixed use",
            "Custom: Weekly in summer",
            "Other: Six (6) months",
        ):
            self.assertIn(text, html)
        self.assertNotIn(">Salt Development</td>", html)  # the customer record's name is overridden

    def test_city_state_zip_row_only_when_needed(self):
        self.assertNotIn("City / State / Zip:", self.render(billing_address="455 W 200 N, Salt Lake City, UT 84103"))
        self.assertIn("City / State / Zip:", self.render(billing_address=None))  # the paper form keeps its line

    def test_no_printed_blank_lacks_an_input_except_the_card_form(self):
        """Every blank() the template prints outside Exhibit B's card form must be a field's fallback;
        the card form stays paper-only because card data never enters ERPNext."""
        text = TEMPLATE.read_text(encoding="utf-8")
        start = text.index("<h2>Credit Card Authorization</h2>")
        card_form = text[start:text.index("<h2>Bank Account (ACH) Authorization</h2>", start)]
        outside = text.replace(card_form, "")
        self.assertEqual(re.findall(r"\{\{ blank\(\d*\) \}\}", outside), [])

    def test_the_summary_opens_the_agreement(self):
        html = self.render(
            visit_frequency="Bi-Weekly",
            service_options=[_Doc(option_key="standard", included=1, price=690)],
        )
        self.assertLess(html.index("What you are agreeing to"), html.index("CLIENT &amp; PROPERTY INFORMATION"))
        self.assertIn("every two weeks at a base rate of $690.00 per visit", html)

    def test_exhibit_a_ticks_and_prints_a_custom_plan(self):
        html = self.render(custom_plan_details="Monthly lobby wall descale")
        self.assertIn("[x]  Custom Plan", html)
        self.assertIn("Monthly lobby wall descale", html)
        self.assertIn("[ ]  Custom Plan", self.render())


# --------------------------------------------------------------------------- plain-language summary


TEMPLATES = APP / "templates" / "contracts"


class TestSummary(unittest.TestCase):
    """Nik: "a summary that summarizes what they've agreed to in simple terms and language"."""

    def maintenance(self, **overrides):
        doc = _Doc(
            template_key="maintenance",
            site_address="455 West 200 North\nSalt Lake City, UT 84103",
            visit_frequency="Bi-Weekly",
            service_options=[
                _Doc(option_key="standard", included=1, price=690),
                _Doc(option_key="as_needed", included=1, price=180),
                _Doc(option_key="startup", included=0, price=500),
            ],
            annual_maintenance_fee=17940,
            emergency_rate=250,
            after_hours_emergency_rate=350,
            emergency_minimum_hours=1,
            business_hours="Monday to Friday, 7:00 a.m. to 5:00 p.m.",
            agreement_start_date="2026-10-01",
            initial_term="One (1) Year",
            renewal_increase_percent=12,
            payment_method="Credit / Debit Card",
            invoicing_frequency="Monthly",
        )
        doc.update(overrides)
        return summary.summary_html(doc)

    def test_states_this_clients_terms_in_plain_words(self):
        html = self.maintenance()
        for fact in (
            "455 West 200 North, Salt Lake City, UT 84103",
            "every two weeks at a base rate of $690.00 per visit",
            "$180.00 an hour (1-hour minimum)",
            "Chemicals are extra:",
            "$250.00 an hour during business hours (Monday to Friday, 7:00 a.m. to 5:00 p.m.)",
            "$350.00 an hour at any other time",
            "Starts 2026-10-01 for one year, then renews by itself every year",
            "each renewal raises every rate by 12%",
            "Your card on file is charged automatically",
            "A credit card is not required",
            "the agreement governs",
        ):
            self.assertIn(fact, html)
        self.assertNotIn("spring startup", html)  # not ticked, not summarized
        self.assertNotIn("About a year", html)  # Nik asked for no yearly estimate
        self.assertNotIn("$17,940.00", html)

    def test_follows_the_choices(self):
        self.assertIn("rates stay the same at renewal", self.maintenance(renewal_increase_percent=0))
        self.assertIn("runs month to month", self.maintenance(initial_term="Month-to-Month"))
        self.assertIn("bank account is debited", self.maintenance(payment_method="Bank Account (ACH)"))
        self.assertIn(
            "check or bank transfer within 45 days",
            self.maintenance(payment_method="Invoice (Check or Bank Transfer)", invoice_due_days=45),
        )
        self.assertIn("You choose in Section 4.3", self.maintenance(payment_method=""))

    def test_never_states_a_figure_the_agreement_leaves_blank(self):
        html = self.maintenance(
            emergency_minimum_hours=0,
            after_hours_emergency_rate=0,
            service_options=[_Doc(option_key="startup", included=1, price=None)],
        )
        self.assertNotIn("$0.00", html)
        self.assertNotIn("minimum per call", html)
        self.assertIn("a spring startup.", html)
        self.assertNotIn("at any other time", html)

    def test_owner_late_payment_terms_follow_the_phases(self):
        design_only = summary.summary_html(
            _Doc(template_key="owner", phases=[_Doc(phase_key="design", included=1, fee=8000)])
        )
        self.assertNotIn("1.5% a month", design_only)
        self.assertIn("pause design work", design_only)
        build = summary.summary_html(
            _Doc(template_key="owner", phases=[_Doc(phase_key="construction", included=1, fee=1)])
        )
        self.assertIn("1.5% a month", build)
        self.assertIn("after 5 days' written notice", build)

    def test_is_deterministic_and_escaped(self):
        """The drift check re-renders and compares, and the site address is typed by staff."""
        self.assertEqual(self.maintenance(), self.maintenance())
        html = self.maintenance(site_address="<script>x</script>")
        self.assertNotIn("<script>", html)

    def test_every_contract_type_has_one(self):
        rental = summary.summary_html(
            _Doc(
                template_key="rental",
                equipment_items=[_Doc(description="Tiered fountain")],
                rental_start_date="2026-11-01",
                rental_end_date="2026-11-03",
                total_rental_amount=1200,
                security_deposit=300,
                total_due_at_signing=900,
            )
        )
        self.assertIn("Tiered fountain", rental)
        self.assertIn("$1,200.00", rental)
        self.assertIn("we keep half the rental fees", rental)
        owner = summary.summary_html(
            _Doc(
                template_key="owner",
                phases=[_Doc(phase_key="construction", included=1, fee=50000)],
                total_contract_value=50000,
            )
        )
        self.assertIn("construction and installation ($50,000.00)", owner)
        self.assertIn("signed Change Order", owner)
        self.assertNotIn("Design:", owner)
        # Since v1.613.0 every contract type has one (Nik: "do the contract builder and building for all
        # contract types"); an unknown type still has none.
        for key in ("msa", "sow", "nda", "architect", "employee_contractor"):
            self.assertIn("where the two differ, the agreement governs", summary.summary_html(_Doc(template_key=key)), key)
        self.assertEqual(summary.summary_html(_Doc(template_key="no_such_type")), "")

    def test_every_fixed_figure_it_quotes_is_in_the_agreement(self):
        """The summary restates clauses; this catches a clause changed in the repo template without
        the summary being changed with it."""
        clauses = {
            "maintenance_services_agreement.html": (
                "cost plus a 15% handling fee", "25% of the remaining prepaid balance", "SIX (6) CALENDAR MONTHS",
                "$35.00 declined-transaction fee", "$35.00 returned-payment fee", "binding arbitration in Salt Lake City",
                "thirty (30) days past due", "thirty (30) days&#x27; written notice", "attorneys&#x27; fees",
            ),
            "rental_agreement.html": (
                "5:00 PM on the Rental End Date", "3% processing fee", "1.5% per month", "fourteen (14) business days",
                "fewer than fifteen (15) days", "50% of rental fees retained", "JURY TRIAL WAIVER",
                "50% of the Total Rental Amount plus the full Security Deposit",
            ),
            "owner_contract.html": (
                "fourteen (14) calendar days", "within ten (10) days", "five (5) business days of each milestone",
                "one (1) year from Substantial Completion", "fourteen (14) days&#x27; written notice",
                "Monday through Friday, 7:00 AM to 5:00 PM", "1.5% per month", "five (5) days&#x27; written notice",
            ),
        }
        for template, phrases in clauses.items():
            text = (TEMPLATES / template).read_text(encoding="utf-8")
            for phrase in phrases:
                self.assertIn(phrase, text, f"{template}: {phrase}")

    def test_each_customer_template_places_it_once_after_the_title(self):
        for template in ("maintenance_services_agreement.html", "rental_agreement.html", "owner_contract.html"):
            text = (TEMPLATES / template).read_text(encoding="utf-8")
            self.assertEqual(text.count("{{ summary() }}"), 1, template)
            self.assertLess(text.index("{{ summary() }}"), text.index("<h1>"), template)

    def test_the_patch_adds_it_to_the_live_rental_and_owner_bodies(self):
        for key, template in (("rental", "rental_agreement.html"), ("owner", "owner_contract.html")):
            shipped = (TEMPLATES / template).read_text(encoding="utf-8")
            before = shipped.replace(summary_patch.MARKER, "", 1)
            new_body, changed = summary_patch.insert_summary(before, summary_patch.ANCHORS[key])
            self.assertTrue(changed, key)
            self.assertEqual(new_body, shipped, key)
            self.assertEqual(summary_patch.insert_summary(shipped, summary_patch.ANCHORS[key]), (shipped, False))
            diverged = before.replace(summary_patch.ANCHORS[key], "<h1>RENTER</h1>")
            self.assertEqual(summary_patch.insert_summary(diverged, summary_patch.ANCHORS[key])[1], False)
        self.assertIn(
            "erpnext_enhancements.patches.add_contract_summaries", (APP / "patches.txt").read_text(encoding="utf-8")
        )


# --------------------------------------------------------------------------- layout (v1.611.1)


class TestWordCopyLayout(unittest.TestCase):
    """Nik: "I need the printed agreement to match the DOCX better and be formatted with bullet points
    and numbering etc." The Word copy numbers every clause, bullets the lists under §2.1-§2.4, indents
    the plan options and shades the label column of its tables."""

    def setUp(self):
        self.text = TEMPLATE.read_text(encoding="utf-8")
        records = json.loads((APP / "fixtures" / "print_format.json").read_text(encoding="utf-8"))
        self.css = next(r for r in records if r["name"] == "Project Contract Print")["css"]

    def test_every_numbered_clause_hangs_its_number(self):
        self.assertEqual(re.findall(r"<p>\d+\.\d+\s", self.text), [])  # no plain-paragraph clause left
        numbers = re.findall(r'<p class="ct-clause"><span class="ct-num">(\d+\.\d+)</span>', self.text)
        self.assertEqual(len(numbers), 55)
        self.assertEqual(numbers[:4], ["2.1", "2.2", "2.3", "2.4"])

    def test_the_scope_lists_are_bulleted(self):
        lists = re.findall(r'<ul class="ct-list">(.*?)</ul>', self.text, flags=re.S)
        self.assertEqual([chunk.count("<li>") for chunk in lists], [11, 6, 5, 5])

    def test_titled_clauses_lead_in_bold(self):
        for title in ("Standard Maintenance Services.", "Liability Cap.", "Automatic Renewal and Annual Rate Increase."):
            self.assertIn(f"<b>{title}</b>", self.text)

    def test_the_print_format_styles_the_structure(self):
        for rule in (".contract-doc p.ct-clause", ".contract-doc ul.ct-list", ".contract-doc p.ct-option",
                     ".contract-doc .ct-val { font-weight: 700; }", "background: #e8f1f8",
                     ".contract-doc p.ct-clause * { text-indent: 0; }"):
            self.assertIn(rule, self.css)

    def test_filled_values_are_marked_and_blanks_are_not(self):
        controller = (APP / "project_enhancements" / "doctype" / "project_contract" / "project_contract.py").read_text(
            encoding="utf-8"
        )
        fill = controller[controller.index("def _fill("):controller.index("def _cb(")]
        self.assertIn("return _blank(width)", fill)
        self.assertIn("return _val(", fill)

    def test_the_footer_reads_like_the_word_copy(self):
        style = (APP / "project_enhancements" / "contract_style.py").read_text(encoding="utf-8")
        self.assertIn('("Sapphire Fountains, LLC", number, "Confidential")', style)

    def test_the_layout_patch_replaces_only_a_shipped_body_and_leaves_terms_alone(self):
        from erpnext_enhancements.patches import refresh_maintenance_agreement_layout as layout

        new = TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(layout.decide(new, new), "current")
        self.assertEqual(layout.decide("edited on the site", new), "diverged")
        layout.REPLACEABLE_BODY_SHA256.add(terms_patch.body_sha256("v1.611.0 body"))
        try:
            self.assertEqual(layout.decide("v1.611.0 body", new), "replace")
        finally:
            layout.REPLACEABLE_BODY_SHA256.discard(terms_patch.body_sha256("v1.611.0 body"))
        self.assertNotIn(terms_patch.body_sha256(new), layout.REPLACEABLE_BODY_SHA256)
        source = (APP / "patches" / "refresh_maintenance_agreement_layout.py").read_text(encoding="utf-8")
        self.assertNotIn("keep_issued_terms(", source)  # agreements issued since v1.611.0 keep their terms
        self.assertIn(
            "erpnext_enhancements.patches.refresh_maintenance_agreement_layout",
            (APP / "patches.txt").read_text(encoding="utf-8"),
        )


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

    def run_patch(self, contracts, requests, live_body):
        """Run the patch against a fake database; returns the writes it made and what it froze."""
        stub = sys.modules["frappe"]
        writes, frozen = [], []

        def get_all(doctype, filters=None, pluck=None, **kwargs):
            if doctype == "Project Contract":
                return [
                    c["name"]
                    for c in contracts
                    if all(c.get(k) == v for k, v in (filters or {}).items() if not isinstance(v, list))
                ]
            if doctype == "Contract Signature Request":
                return [name for name, status in requests if status in ("Sent", "Viewed")]
            return []

        class _Contract:
            def __init__(self, name):
                self.name = name

            def freeze_executed_copy(self):
                frozen.append(self.name)

        saved = (stub.get_all, stub.get_doc, stub.db, getattr(stub, "get_app_path", None))
        stub.get_all = get_all
        stub.get_doc = lambda doctype, name: _Contract(name)
        stub.db = types.SimpleNamespace(
            get_value=lambda *a, **k: live_body,
            set_value=lambda doctype, name, *a, **k: writes.append((doctype, name, a)),
            commit=lambda: None,
        )
        stub.get_app_path = lambda *parts: str(APP.joinpath(*parts[1:]))
        try:
            terms_patch.execute()
        finally:
            stub.get_all, stub.get_doc, stub.db, stub.get_app_path = saved
        return writes, frozen

    def test_freezes_paper_signed_and_keeps_issued_agreements_on_old_terms(self):
        """SF-MAINT-2026-0001 was marked signed on paper: its reprint must not gain the new terms,
        and its operational contract must not inherit a 12% increase it never agreed to."""
        old_body = "the v1.609.0 body"
        terms_patch.REPLACEABLE_BODY_SHA256.add(terms_patch.body_sha256(old_body))
        try:
            contracts = [
                {"name": "SF-MAINT-2026-0001", "template_key": "maintenance", "docstatus": 1, "status": "Signed"},
                {"name": "SF-MAINT-2026-0011", "template_key": "maintenance", "docstatus": 0, "status": "Draft"},
            ]
            writes, frozen = self.run_patch(contracts, [], old_body)
        finally:
            terms_patch.REPLACEABLE_BODY_SHA256.discard(terms_patch.body_sha256(old_body))
        self.assertEqual(frozen, ["SF-MAINT-2026-0001"])
        reset = [w for w in writes if w[0] == "Project Contract"]
        self.assertEqual([w[1] for w in reset], ["SF-MAINT-2026-0001"])  # the draft keeps the new terms
        self.assertEqual(reset[0][2][0]["renewal_increase_percent"], 0)
        self.assertTrue(any(w[0] == "Contract Template" for w in writes), "the body was swapped")

    def test_leaves_the_body_alone_while_a_link_is_out(self):
        old_body = "the v1.609.0 body"
        terms_patch.REPLACEABLE_BODY_SHA256.add(terms_patch.body_sha256(old_body))
        try:
            contracts = [
                {"name": "SF-MAINT-2026-0030", "template_key": "maintenance", "docstatus": 1, "status": "Out for Signature"}
            ]
            writes, _frozen = self.run_patch(contracts, [("CSR-2026-00009", "Viewed")], old_body)
        finally:
            terms_patch.REPLACEABLE_BODY_SHA256.discard(terms_patch.body_sha256(old_body))
        self.assertFalse(any(w[0] == "Contract Template" for w in writes))

    def test_a_paper_signature_freezes_the_executed_copy(self):
        source = (APP / "project_enhancements" / "doctype" / "project_contract" / "project_contract.py").read_text(
            encoding="utf-8"
        )
        hook = source[source.index("def on_update_after_submit"):source.index("def freeze_executed_copy")]
        self.assertIn("self.freeze_executed_copy()", hook)
        self.assertIn('frappe.db.get_value("Project Contract", name, "executed_html")', source)
        field = next(
            f for f in json.loads(PC_JSON.read_text(encoding="utf-8"))["fields"] if f["fieldname"] == "executed_html"
        )
        self.assertEqual(field.get("no_copy"), 1)  # an amendment is a new, unsigned revision


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

    def test_a_new_contract_takes_its_agreements_increase_or_none(self):
        """An increase is a term of the signed agreement; with no agreement nothing allows one."""
        fields = {f["fieldname"]: f for f in json.loads(SMC_JSON.read_text(encoding="utf-8"))["fields"]}
        # No JSON default: on a normal doctype a default reaches every existing row through the
        # ALTER, which would put twelve percent on contracts signed under the old terms.
        self.assertNotIn("default", fields["renewal_increase_percent"])
        controller = (
            APP / "sapphire_maintenance" / "doctype" / "sapphire_maintenance_contract" / "sapphire_maintenance_contract.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'self.renewal_increase_percent = _agreed_increase(self.get("project_contract"))', controller
        )
        body = controller[controller.index("def _agreed_increase("):controller.index("def _apply_project_contract(")]
        self.assertIn("if not project_contract:\n\t\treturn 0", body)

    def test_in_person_signing_is_treated_as_the_signing_page(self):
        """A failed auto-draft must reach the owner even when the signer's tab is a staff session."""
        lifecycle_src = (APP / "project_enhancements" / "esign" / "lifecycle.py").read_text(encoding="utf-8")
        self.assertIn("frappe.flags.esign_executing = True", lifecycle_src)
        self.assertLess(
            lifecycle_src.index("frappe.flags.esign_executing = True"), lifecycle_src.index("		contract.save()")
        )
        controller = (
            APP / "sapphire_maintenance" / "doctype" / "sapphire_maintenance_contract" / "sapphire_maintenance_contract.py"
        ).read_text(encoding="utf-8")
        self.assertIn('not frappe.flags.get("esign_executing")', controller)


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
            [
                "parties_section",
                "details_section",
                "owner_section",
                "design_section",
                "construction_section",
                "milestones_section",
                "maintenance_section",
                "maint_fees_section",
            ],
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


# --------------------------------------------------------------------------- builder v2 (v1.612.0)


class TestInitialsClauses(unittest.TestCase):
    """Initials drawn in the builder carry the wording they were drawn beside; the server
    compares it with the issued snapshot before anything is signed."""

    def box(self, key):
        return initials.initial_markup({}, key, key.title())

    def test_the_clause_is_the_block_around_the_box(self):
        html = (
            '<h1>9. TERM</h1><p class="ct-clause"><span class="ct-num">9.2</span><b>Renewal.</b> Rates rise '
            f'<span class="ct-val">12%</span> a year.&nbsp; {self.box("renewal")}</p>'
            f"<ul><li>One item {self.box('item')}</li></ul>"
        )
        clauses = initials.clause_texts(html)
        self.assertEqual(clauses["renewal"], "9.2Renewal. Rates rise 12% a year.")
        self.assertEqual(clauses["item"], "One item")
        self.assertNotIn("Client initials", clauses["renewal"])

    def test_a_box_on_its_own_line_takes_the_clause_before_it(self):
        html = f'<p class="ct-sub">(c) Invoice within 30 days.</p><p class="ct-sub">{self.box("payment")}</p>'
        self.assertEqual(initials.clause_texts(html)["payment"], "(c) Invoice within 30 days.")

    def test_a_signed_box_is_not_asked_for(self):
        signed = initials.initial_markup({"renewal": _png("r")}, "renewal")
        self.assertEqual(initials.clause_texts(f"<p>Text {signed}</p>"), {})

    def test_changed_wording_is_stale_and_unchanged_is_not(self):
        snapshot = f"<p>Rates rise 12% a year. {self.box('renewal')}</p><p>Thirty days. {self.box('cancel')}</p>"
        drawn = {
            "renewal": {"image": _png("a"), "clause": "Rates rise 10% a year."},
            "cancel": {"image": _png("b"), "clause": "  Thirty   days. "},
        }
        self.assertEqual(initials.stale_initials(drawn, snapshot), ["renewal"])
        self.assertEqual(initials.stale_initials({}, snapshot), [])  # nothing drawn: validate_initials says so

    def test_the_page_reads_the_same_blocks(self):
        js = (APP / "project_enhancements" / "page" / "contract_builder" / "contract_builder.js").read_text(encoding="utf-8")
        blocks = re.search(r'const CB_BLOCKS = "([^"]+)";', js).group(1).split(",")
        self.assertEqual(set(blocks), set(initials.CLAUSE_BLOCKS))


class TestContractManagerRole(unittest.TestCase):
    """Nik, 2026-10-13: "make a Contract Manager role for sales, Lisa, James and me" (and Clegg).
    Issuing, signing, sending, cancelling, amending and template wording are the Contract Manager's;
    building and saving drafts stays open."""

    EXECUTE = {"submit", "cancel", "amend", "delete"}

    def perms(self, path):
        return {row["role"]: row for row in json.loads(path.read_text(encoding="utf-8"))["permissions"]}

    def test_only_a_contract_manager_issues_a_contract(self):
        perms = self.perms(PC_JSON)
        for role in ("System Manager", "Contract Manager"):
            self.assertTrue(all(perms[role].get(p) for p in self.EXECUTE | {"create", "write"}), role)
        for role in ("Projects Manager", "Sales Manager", "Sales User"):
            self.assertTrue(perms[role].get("create") and perms[role].get("write"), role)  # still builds drafts
            self.assertFalse([p for p in self.EXECUTE if perms[role].get(p)], role)
        holders = {role for role, row in perms.items() if row.get("submit")}
        self.assertEqual(holders, {"System Manager", "Contract Manager"})

    def test_only_a_contract_manager_edits_the_wording(self):
        perms = self.perms(APP / "project_enhancements" / "doctype" / "contract_template" / "contract_template.json")
        writers = {role for role, row in perms.items() if row.get("write")}
        self.assertEqual(writers, {"System Manager", "Contract Manager"})

    def test_signing_and_sending_are_the_contract_managers(self):
        self.assertEqual(builder.SIGN_ROLES, {"System Manager", "Contract Manager"})
        api = (APP / "project_enhancements" / "esign" / "api.py").read_text(encoding="utf-8")
        self.assertIn('SEND_ROLES = ("System Manager", "Contract Manager")', api)
        evidence = self.perms(CSR_JSON)
        self.assertTrue(evidence["Contract Manager"].get("read"))

    def test_marking_signed_on_paper_is_checked_on_the_server(self):
        """Defence in depth: Frappe 16 needs submit for an update after submit, which DocPerm now gives
        only a Contract Manager; the server check also covers submit granted through a document Share."""
        controller = (APP / "project_enhancements" / "doctype" / "project_contract" / "project_contract.py").read_text(
            encoding="utf-8"
        )
        gate = controller[controller.index("def before_update_after_submit(self):"):controller.index("def on_update_after_submit(self):")]
        self.assertIn('frappe.flags.get("esign_executing")', gate)  # online signing is not stopped
        self.assertIn('{"System Manager", "Contract Manager"}', gate)
        self.assertIn("frappe.PermissionError", gate)
        form = (APP / "project_enhancements" / "doctype" / "project_contract" / "project_contract.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('frappe.user.has_role(["System Manager", "Contract Manager"])', form)

    def test_the_role_exists_before_the_doctypes_that_name_it(self):
        patches = (APP / "patches.txt").read_text(encoding="utf-8")
        pre, post = patches.split("[post_model_sync]")
        self.assertIn("erpnext_enhancements.patches.seed_contract_manager_role", pre)
        self.assertNotIn("seed_contract_manager_role", post)
        seed = (APP / "patches" / "seed_contract_manager_role.py").read_text(encoding="utf-8")
        self.assertIn('ROLE = "Contract Manager"', seed)
        self.assertNotIn("add_roles", seed)  # grants it to nobody: who holds it is decided per site

    def test_a_builder_without_the_role_can_ask_for_it(self):
        source = (APP / "project_enhancements" / "contract_builder.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "ask_contract_manager")
        body = ast.get_source_segment(source, fn)
        self.assertIn('doc.check_permission("write")', body)
        self.assertIn("escape_html", body)
        js = (APP / "project_enhancements" / "page" / "contract_builder" / "contract_builder.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("`${CB_API}ask_contract_manager`", js)
        self.assertIn("if (data.can_ask)", js)


class TestShownFields(unittest.TestCase):
    """Review of #1230: a field only shown for one choice is not a blank while that choice is not made."""

    def test_the_conditions_this_doctype_uses(self):
        doc = {"template_key": "sow", "sow_project_type": "Commercial", "payment_method": "Invoice (Check or Bank Transfer)"}
        self.assertTrue(builder.shown('eval:doc.template_key=="sow"', doc))
        self.assertFalse(builder.shown('eval:doc.template_key=="sow" && doc.sow_project_type=="Other"', doc))
        self.assertTrue(builder.shown('eval:doc.template_key=="sow" && doc.sow_project_type!="Other"', doc))
        self.assertTrue(
            builder.shown(
                'eval:doc.template_key=="maintenance" || (doc.template_key=="sow" && (doc.payment_method=="Invoice (Check or Bank Transfer)" || doc.payment_method=="Other"))',
                doc,
            )
        )
        self.assertFalse(builder.shown('eval:doc.template_key=="sow" && (!doc.payment_method)', doc))
        self.assertTrue(builder.shown("eval:!doc.missing", doc))
        self.assertFalse(builder.shown("missing", doc))
        self.assertTrue(builder.shown(None, doc))
        # Anything it cannot read counts as shown, never as hidden.
        self.assertTrue(builder.shown("eval:(doc.phases||[]).some(p => p.included)", doc))
        self.assertTrue(builder.shown('eval:doc.template_key=="sow" &&', doc))

    def test_hidden_fields_are_neither_blanks_nor_progress(self):
        steps = builder.builder_steps("maintenance", "Customer")
        body = TEMPLATE.read_text(encoding="utf-8")
        plain = builder.progress({"template_key": "maintenance", "property_type": "Commercial"}, steps, body)
        other = builder.progress({"template_key": "maintenance", "property_type": "Other"}, steps, body)
        self.assertEqual(other["total"], plain["total"] + 1)  # Other Property Type appears only for Other

    def test_a_service_fee_hides_the_blank_annual_fee_from_blanks_and_progress(self):
        """v1.724.0: with a Service Fee and no Annual Fee the template leaves the Annual Service Fee Total
        out (Day Dairy), so the builder must not ask for it."""
        steps = builder.builder_steps("maintenance", "Customer")
        body = TEMPLATE.read_text(encoding="utf-8")
        frappe = sys.modules["frappe"]
        frappe.db.get_value = lambda *a, **k: body
        doc = {"template_key": "maintenance", "name": "SF-MAINT-2026-0018", "invoicing_frequency": "Monthly"}
        without = builder.progress(doc, steps, body)
        self.assertIn("annual_maintenance_fee", [row["fieldname"] for row in builder.blanks(doc, steps)])
        contract = {"name": "MNT-CON-2026-00002", "status": "Active", "recurring_amount": 1490,
                    "invoicing_frequency": "Monthly", "billing_season_start": "May", "billing_season_end": "September"}
        saved = frappe.get_all
        frappe.get_all = lambda doctype, *a, **k: [contract] if doctype == "Sapphire Maintenance Contract" else []
        try:
            self.assertNotIn("annual_maintenance_fee", [row["fieldname"] for row in builder.blanks(doc, steps)])
            self.assertEqual(builder.progress(doc, steps, body)["total"], without["total"] - 1)
            # A set Annual Fee prints beside the Service Fee, so it counts as filled, not hidden.
            set_fee = builder.progress(dict(doc, annual_maintenance_fee=7450), steps, body)
            self.assertEqual(set_fee["total"], without["total"])
        finally:
            frappe.get_all = saved
            frappe.db.get_value = lambda *a, **k: None


class TestPartyWording(unittest.TestCase):
    """Nik, 2026-10-13: the builder for every contract type, so "With the client" and "Client
    initials" must name the party each agreement is actually with."""

    def test_each_agreement_names_its_own_party(self):
        self.assertEqual(builder.party_noun("maintenance"), "client")
        self.assertEqual(builder.party_noun("rental"), "client")
        self.assertEqual(builder.party_noun("msa"), "subcontractor")
        self.assertEqual(builder.party_noun("sow"), "subcontractor")
        self.assertEqual(builder.party_noun("architect"), "architect")
        # The builder and the printed caption call the NDA's other side the same thing.
        self.assertEqual(builder.party_noun("nda"), "company")
        self.assertEqual(initials.INITIALS_CAPTIONS["nda"], "Company initials")
        self.assertEqual(builder.party_noun(None), "client")

    def test_customer_agreements_keep_their_caption_byte_for_byte(self):
        """The unsigned box is part of every snapshot: the customer agreements' must not change."""
        self.assertIn(">Client initials<", initials.initial_markup({}, "renewal", "Renewal"))
        self.assertNotIn("maintenance", initials.INITIALS_CAPTIONS)
        self.assertIn(">Subcontractor initials<", initials.initial_markup({}, "k", "L", caption="Subcontractor initials"))
        controller = (APP / "project_enhancements" / "doctype" / "project_contract" / "project_contract.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('caption=INITIALS_CAPTIONS.get(doc.get("template_key") or "")', controller)


class TestBuilderSections(unittest.TestCase):
    def test_fields_map_to_the_parts_of_the_agreement_they_print_in(self):
        headings, fields = builder.agreement_sections(TEMPLATE.read_text(encoding="utf-8"))
        self.assertIn("CLIENT & PROPERTY INFORMATION", headings)
        self.assertIn("CLIENT & PROPERTY INFORMATION", fields["site_address"])
        self.assertIn("8. ADDITIONAL / REPAIR SERVICES", fields["emergency_rate"])
        self.assertTrue(fields["service_options"])  # read through the render context, not doc.
        self.assertEqual(len(headings), len(set(headings)))

    def test_a_heading_with_jinja_matches_on_its_fixed_start(self):
        headings, fields = builder.agreement_sections(
            "<h1>EXHIBIT B – {% if doc.x %}CARD{% endif %}</h1><p>{{ fill(doc.x) }}</p><h1>{{ doc.y }}</h1><p>{{ doc.z }}</p>"
        )
        self.assertEqual(headings, ["EXHIBIT B –"])
        self.assertEqual(fields, {"x": ["EXHIBIT B –"]})

    def test_progress_counts_what_the_agreement_prints(self):
        steps = builder.builder_steps("maintenance", "Customer")
        body = TEMPLATE.read_text(encoding="utf-8")
        empty = builder.progress({"template_key": "maintenance"}, steps, body)
        self.assertGreater(empty["total"], 10)
        self.assertEqual(empty["filled"], 0)
        some = builder.progress(
            {"template_key": "maintenance", "site_address": "1 Main", "emergency_rate": 250, "visit_rate": 0}, steps, body
        )
        self.assertEqual(some["filled"], 2)


class TestSignHere(unittest.TestCase):
    """The builder's own signing: issue and execute in one transaction, refusals undo the issue."""

    def setUp(self):
        self.source = (APP / "project_enhancements" / "contract_builder.py").read_text(encoding="utf-8")
        tree = ast.parse(self.source)
        self.fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    def segment(self, name):
        return ast.get_source_segment(self.source, self.fns[name])

    def test_whitelist_is_outermost(self):
        def name(node):
            target = node.func if isinstance(node, ast.Call) else node
            return getattr(target, "attr", None) or getattr(target, "id", None)

        self.assertEqual([name(d) for d in self.fns["sign_here"].decorator_list], ["whitelist", "rate_limit"])
        self.assertEqual([name(d) for d in self.fns["start_card_on_file"].decorator_list], ["whitelist"])

    def test_the_signer_is_checked_before_anything_is_issued(self):
        body = self.segment("sign_here")
        submit = body.index("doc.submit()")
        for check in ("cint(agree)", "_clean_text(signed_name)", "_validated_signature_image", "_valid_single_email"):
            self.assertLess(body.index(check), submit, check)
        self.assertIn("frappe.only_for(", body)
        self.assertIn('check_permission("submit")', body)
        self.assertIn("throw_if_contract_esign_disabled()", body)

    def test_a_refusal_rolls_the_issue_back(self):
        body = self.segment("sign_here")
        self.assertLess(body.index("esign_api.sign_in_person("), body.index("frappe.db.rollback()"))

    def test_signing_reads_back_what_issuing_wrote(self):
        """Review of #1228: v16 writes the row before on_submit runs, so on_submit's status never
        reached the database and the builder's issue-then-sign was refused every time."""
        body = self.segment("sign_here")
        self.assertIn("doc.submit()\n\t\t# Read back", body)
        self.assertGreater(body.find("doc = frappe.get_doc(DOCTYPE, name)", body.index("doc.submit()")), 0)
        controller = (APP / "project_enhancements" / "doctype" / "project_contract" / "project_contract.py").read_text(
            encoding="utf-8"
        )
        on_submit = controller[controller.index("def on_submit(self):"):controller.index("def on_cancel(self):")]
        self.assertIn('self.db_set("status", "Out for Signature", update_modified=False)', on_submit)

    def test_the_card_offer_respects_the_clients_answer(self):
        body = self.segment("start_card_on_file")
        self.assertIn("throw_if_contract_esign_disabled()", body)
        self.assertIn('request.signing_channel != "In Person"', body)
        self.assertIn('request.autopay_outcome == "Declined"', body)

    def test_a_refusal_names_every_box_to_redo(self):
        box = lambda key: initials.initial_markup({}, key, key)  # noqa: E731
        snapshot = "".join(f"<p>Clause {key}. {box(key)}</p>" for key in ("a", "b", "c", "d"))
        supplied = {"a": _png("one"), "b": "data:image/gif;base64,AAAA", "c": _png("one")}
        self.assertEqual(initials.initials_to_redo(supplied, snapshot, _accept), ["b", "c", "d"])
        self.assertEqual(initials.initials_to_redo({}, "<p>none</p>", _accept), [])

    def test_execution_goes_through_the_signing_pages_evidence_path(self):
        api_src = (APP / "project_enhancements" / "esign" / "api.py").read_text(encoding="utf-8")
        tree = ast.parse(api_src)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "sign_in_person")
        body = ast.get_source_segment(api_src, fn)
        # The contract row is locked first, in the signing page's order, so a client signing an
        # emailed link at the same moment cannot deadlock with the builder.
        order = [
            "for_update=True",
            "open_in_person_request(",
            "stale_initials(",
            "_validated_initials(",
            "lifecycle.execute_contract(request, evidence)",
        ]
        positions = [body.index(step) for step in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("email_confirmed=0", body)  # recorded honestly: no re-typed address at the table

    def test_the_preview_marks_the_signature_line_and_the_snapshot_does_not(self):
        controller = (APP / "project_enhancements" / "doctype" / "project_contract" / "project_contract.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("doc.flags.contract_preview = True", controller)
        sig = controller[controller.index("def _sig("):controller.index("def _initial(")]
        self.assertIn('doc.flags.get("contract_preview") and not signature', sig)

    def test_the_page_sends_each_initials_clause_and_never_its_history(self):
        js = (APP / "project_enhancements" / "page" / "contract_builder" / "contract_builder.js").read_text(encoding="utf-8")
        self.assertIn("initials[box.key] = { image: drawn.image, clause: drawn.clause };", js)
        self.assertIn("`${CB_API}sign_here`", js)
        # Every set of initials starts from an empty pad, and a copied drawing is refused.
        self.assertIn("requestAnimationFrame(() => pad.reset());", js)
        self.assertIn("Each set of initials has to be drawn separately.", js)
        # Nik: "auto scroll to the next and the next".
        self.assertIn("this.advance_from(box);", js)
        self.assertNotIn("history.pushState", js)
        self.assertNotIn("history.replaceState", js)


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
        self.assertIn("open_in_person_request(contract, recipient)", body)
        # The request itself is opened by the helper the builder's own signing shares.
        opener = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "open_in_person_request")
        opened = ast.get_source_segment(source, opener)
        self.assertNotIn("_email_invite", opened)
        self.assertNotIn("frappe.db.commit()", opened)  # each caller owns its transaction
        self.assertIn('request.signing_channel = "In Person"', opened)
        self.assertIn("request.presented_by = frappe.session.user", opened)
        self.assertIn("IN_PERSON_LINK_HOURS", opened)
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
        # Nik: "auto scroll to the next and the next": every apply moves on to the next box.
        apply = js[js.index("function applyInitial()"):js.index("function advanceFrom(")]
        self.assertIn("advanceFrom(box);", apply)
        self.assertIn('$("cs-signature")', js)

    def test_generate_contract_opens_the_builder(self):
        # v1.686.0: the dialog and the create call moved to record_actions.js, shared by the form's
        # button (contracts.js) and the guided page's Actions menu.
        js = (APP / "public" / "js" / "global_enhancements" / "record_actions.js").read_text(encoding="utf-8")
        self.assertIn('frappe.set_route("contract-builder", r.message)', js)
        button = (APP / "public" / "js" / "contracts.js").read_text(encoding="utf-8")
        self.assertIn("actions.open_generate_contract(source_doctype, frm.doc.name)", button)

    def test_builder_page_is_registered(self):
        page = json.loads(
            (APP / "project_enhancements" / "page" / "contract_builder" / "contract_builder.json").read_text(encoding="utf-8")
        )
        self.assertEqual(page["name"], "contract-builder")
        self.assertEqual(page["module"], "Project Enhancements")


if __name__ == "__main__":
    unittest.main()
