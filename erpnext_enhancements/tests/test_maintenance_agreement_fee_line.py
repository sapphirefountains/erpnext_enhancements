"""The maintenance agreement prints its per-period Service Fee (v1.724.0).

Nik, 2026-10-22: "add a monthly (per-period) fee line to the maintenance agreement template, filled
from the linked maintenance contract's Recurring Amount when set, plus fix SF-MAINT-2026-0018 so Day
Dairy prints $1,490 per month." Pinned here:

* where the figure comes from: the maintenance contract's Recurring Amount on the agreement's own
  cadence, else the Annual Fee split over the billed periods (the billing season included), else
  nothing, and never $0.00;
* the wording: per month / quarter / year, the season when there is one, no line for Per Visit;
* an agreement with no fee renders byte for byte as the v1.611.1 template did;
* the summary states the same figure;
* the patch replaces only a shipped body, freezes paper-signed copies first, waits on a live signing
  link, never raises and does nothing the second time.

Stubs a minimal ``frappe`` in ``setUpModule`` and loads the real billing helper, so it has its own CI
step. The template cases need ``jinja2``, which CI installs.

Run: python -m unittest erpnext_enhancements.tests.test_maintenance_agreement_fee_line -v
"""

import hashlib
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
TEMPLATE = APP / "templates" / "contracts" / "maintenance_services_agreement.html"

try:
    import jinja2
except ImportError:  # pragma: no cover - CI installs it
    jinja2 = None

#: The prod body this release replaces (v1.611.1), checked against prod on 2026-10-22.
V1_611_1_SHA256 = "c2baa1612d4d819f76e6becbdbde50b26d85318a62874440e6a2f3cea2609ba6"

#: (v1.611.1 text, v1.724.0 text): the three changes, so the old body can be rebuilt from the new.
CHANGES = [
    (
        "base rate {{ money(_o.price if _o else None) }} per visit, plus chemicals</p>",
        "{% if _o and _o.included and not _o.price and period_fee %}base rate in the Service Fee below{% else %}"
        "base rate {{ money(_o.price if _o else None) }} per visit{% endif %}, plus chemicals</p>",
    ),
    (
        "<p><b>Annual Service Fee Total (if applicable): {{ money(doc.annual_maintenance_fee) }}</b></p>\n"
        "<p>The Annual Service Fee Total counts base rates only.",
        "{% if period_fee %}<p><b>Service Fee: {{ money(period_fee.amount) }} per {{ period_fee.per }}</b>"
        "{% if period_fee.season %}, billed {{ period_fee.season }}{% endif %}.</p>{% endif %}"
        "{% if doc.annual_maintenance_fee or not period_fee %}<p><b>Annual Service Fee Total (if applicable): "
        "{{ money(doc.annual_maintenance_fee) }}</b></p>{% endif %}\n"
        "<p>The {% if period_fee and doc.annual_maintenance_fee %}Service Fee and the Annual Service Fee Total count"
        "{% elif period_fee %}Service Fee counts{% else %}Annual Service Fee Total counts{% endif %} base rates only.",
    ),
]

STUBBED = (
    "frappe",
    "frappe.utils",
    "erpnext_enhancements.project_enhancements",
    "erpnext_enhancements.api.maintenance_billing",
    "erpnext_enhancements.sapphire_maintenance.chemical_billing",
    "erpnext_enhancements.project_enhancements.maintenance_fee",
    "erpnext_enhancements.project_enhancements.contract_summary",
    "erpnext_enhancements.patches.update_maintenance_agreement_terms",
    "erpnext_enhancements.patches.refresh_maintenance_agreement_fee_line",
)

STATE = {}
_saved = {}
fee = None
summary = None
patch = None


class _Doc(dict):
    def __getattr__(self, key):
        return self.get(key)


def _get_all(doctype, filters=None, **kwargs):
    STATE["get_all"].append((doctype, filters, kwargs))
    if STATE.get("get_all_raises"):
        raise RuntimeError("database went away")
    return list(STATE["tables"].get(doctype, []))


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")
    frappe._ = lambda s, *a, **k: s
    frappe._dict = _Doc
    frappe.throw = lambda *a, **k: (_ for _ in ()).throw(RuntimeError(a[0] if a else "throw"))
    frappe.log_error = lambda *a, **k: STATE["errors"].append(k.get("title") or (a[1] if len(a) > 1 else ""))
    frappe.get_traceback = lambda: ""
    frappe.get_all = _get_all
    frappe.get_app_path = lambda app, *parts: str(APP.joinpath(*parts))

    def whitelist(*dargs, **dkwargs):
        return dargs[0] if dargs and callable(dargs[0]) else (lambda fn: fn)

    frappe.whitelist = whitelist  # the project_enhancements package whitelists at import

    class _Contract:
        def __init__(self, name):
            self.name = name

        def freeze_executed_copy(self):
            STATE["frozen"].append(self.name)

    frappe.get_doc = lambda doctype, name: _Contract(name)

    def get_value(doctype, name, field, *a, **k):
        if STATE.get("get_value_raises"):
            raise RuntimeError("database went away")
        return STATE["body"]

    def set_value(doctype, name, field, value=None, **k):
        STATE["writes"].append((doctype, name, field))
        STATE["body"] = value

    frappe.db = types.SimpleNamespace(
        get_value=get_value, set_value=set_value, commit=lambda: STATE.__setitem__("commits", STATE["commits"] + 1)
    )
    frappe.defaults = types.SimpleNamespace(get_global_default=lambda key: "USD")

    utils = types.ModuleType("frappe.utils")

    def flt(value, precision=None):
        try:
            number = float(value or 0)
        except (TypeError, ValueError):
            number = 0.0
        return round(number, precision) if precision is not None else number

    utils.flt = flt
    utils.cint = lambda value: int(float(value or 0))
    utils.escape_html = lambda s: (
        str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
    utils.fmt_money = lambda value, currency=None: f"${flt(value):,.2f}"
    for name in ("add_days", "add_months", "add_years", "formatdate", "getdate", "nowdate"):
        setattr(utils, name, lambda *a, **k: None)
    frappe.utils = utils

    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils
    # The billing module imports chemical_billing at the top; nothing here reaches it.
    sys.modules["erpnext_enhancements.sapphire_maintenance.chemical_billing"] = types.ModuleType("chemical_billing")


def setUpModule():
    global fee, summary, patch
    for mod in STUBBED:
        if mod in sys.modules:
            _saved[mod] = sys.modules.pop(mod)
    _install_frappe_stub()
    from erpnext_enhancements.patches import refresh_maintenance_agreement_fee_line as _patch
    from erpnext_enhancements.project_enhancements import contract_summary as _summary
    from erpnext_enhancements.project_enhancements import maintenance_fee as _fee

    fee, summary, patch = _fee, _summary, _patch


def tearDownModule():
    for mod in STUBBED:
        sys.modules.pop(mod, None)
        # `from package import module` reads the package attribute before sys.modules, so a suite run
        # after this one would otherwise get the copy bound to this suite's frappe stub.
        parent, _, child = mod.rpartition(".")
        if parent in sys.modules and getattr(sys.modules[parent], child, None) is not None:
            delattr(sys.modules[parent], child)
    sys.modules.update(_saved)


def _reset(**state):
    STATE.clear()
    STATE.update(tables={}, get_all=[], errors=[], writes=[], frozen=[], commits=0, body=None)
    STATE.update(state)


def _contract(**fields):
    row = _Doc(
        name="MNT-CON-2026-00002",
        status="Active",
        recurring_amount=1490,
        invoicing_frequency="Monthly",
        billing_season_start="May",
        billing_season_end="September",
    )
    row.update(fields)
    return row


# --------------------------------------------------------------------------- where the figure comes from


class TestPeriodFee(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_day_dairy_prints_the_maintenance_contracts_recurring_amount(self):
        result = fee.period_fee(_Doc(invoicing_frequency="Monthly", annual_maintenance_fee=0), _contract())
        self.assertEqual(
            result, {"amount": 1490.0, "per": "month", "season": "May through September", "source": "maintenance_contract"}
        )

    def test_the_recurring_amount_wins_over_the_annual_split(self):
        result = fee.period_fee(_Doc(invoicing_frequency="Monthly", annual_maintenance_fee=14534), _contract(
            recurring_amount=1118, billing_season_start=None, billing_season_end=None
        ))
        self.assertEqual((result["amount"], result["season"], result["source"]), (1118.0, "", "maintenance_contract"))

    def test_without_a_recurring_amount_the_annual_fee_is_split_over_the_season(self):
        result = fee.period_fee(_Doc(invoicing_frequency="Monthly", annual_maintenance_fee=7450), _contract(recurring_amount=0))
        self.assertEqual((result["amount"], result["season"], result["source"]), (1490.0, "May through September", "annual_fee"))

    def test_with_no_maintenance_contract_the_annual_fee_is_split_over_the_year(self):
        monthly = fee.period_fee(_Doc(invoicing_frequency="Monthly", annual_maintenance_fee=17940))
        self.assertEqual((monthly["amount"], monthly["per"], monthly["season"]), (1495.0, "month", ""))
        quarterly = fee.period_fee(_Doc(invoicing_frequency="Quarterly", annual_maintenance_fee=12000))
        self.assertEqual((quarterly["amount"], quarterly["per"]), (3000.0, "quarter"))
        # Annually: the split is the Annual Fee itself, which the Annual Service Fee Total already prints.
        self.assertIsNone(fee.period_fee(_Doc(invoicing_frequency="Annually", annual_maintenance_fee=9000)))
        yearly = fee.period_fee(
            _Doc(invoicing_frequency="Annually"), _contract(invoicing_frequency="Annually", recurring_amount=9000)
        )
        self.assertEqual((yearly["amount"], yearly["per"]), (9000.0, "year"))

    def test_per_visit_has_no_period_fee(self):
        self.assertIsNone(fee.period_fee(_Doc(invoicing_frequency="Per Visit", annual_maintenance_fee=9000), _contract()))
        self.assertIsNone(fee.period_fee(_Doc(invoicing_frequency="", annual_maintenance_fee=9000)))

    def test_a_plan_priced_per_visit_has_no_period_fee(self):
        """Hardware Apartments (SF-MAINT-2026-0011): $690 a visit, Annual Fee $17,940 = 26 visits. Split
        over twelve months that would have printed a flat "$1,495.00 per month" nobody agreed to."""
        hardware = _Doc(
            invoicing_frequency="Monthly",
            annual_maintenance_fee=17940,
            service_options=[_Doc(option_key="standard", included=1, price=690)],
        )
        self.assertIsNone(fee.period_fee(hardware))
        self.assertIsNone(fee.period_fee(hardware, _contract(recurring_amount=1118, billing_season_start=None)))
        # An unticked or unpriced Standard row does not count: Day Dairy's is both.
        for row in (_Doc(option_key="standard", included=0, price=690), _Doc(option_key="standard", included=1, price=0)):
            doc = _Doc(invoicing_frequency="Monthly", service_options=[row])
            self.assertEqual(fee.period_fee(doc, _contract())["amount"], 1490.0)

    def test_no_figure_means_no_line_never_zero(self):
        self.assertIsNone(fee.period_fee(_Doc(invoicing_frequency="Monthly", annual_maintenance_fee=0)))
        self.assertIsNone(
            fee.period_fee(_Doc(invoicing_frequency="Monthly", annual_maintenance_fee=None), _contract(recurring_amount=0))
        )
        self.assertIsNone(
            fee.period_fee(_Doc(invoicing_frequency="Monthly"), _contract(recurring_amount=-5))
        )

    def test_a_recurring_amount_on_another_cadence_is_not_printed(self):
        """A quarterly amount beside the agreement's "Monthly" tick would contradict §4.2."""
        doc = _Doc(invoicing_frequency="Monthly", annual_maintenance_fee=0)
        self.assertIsNone(fee.period_fee(doc, _contract(invoicing_frequency="Quarterly", recurring_amount=3000)))
        # Nor is the Annual Fee split: that contract does not invoice monthly either.
        doc["annual_maintenance_fee"] = 12000
        self.assertIsNone(fee.period_fee(doc, _contract(invoicing_frequency="Quarterly", recurring_amount=3000)))
        self.assertIsNone(fee.period_fee(doc, _contract(invoicing_frequency="Per Visit", recurring_amount=0)))
        # Before the maintenance contract exists, the split is what it will derive for itself.
        self.assertEqual(fee.period_fee(doc)["amount"], 1000.0)

    def test_a_season_only_applies_to_monthly_billing(self):
        result = fee.period_fee(
            _Doc(invoicing_frequency="Quarterly"), _contract(invoicing_frequency="Quarterly", recurring_amount=2000)
        )
        self.assertEqual((result["amount"], result["per"], result["season"]), (2000.0, "quarter", ""))

    def test_half_a_season_is_ignored(self):
        result = fee.period_fee(_Doc(invoicing_frequency="Monthly"), _contract(billing_season_end=None))
        self.assertEqual(result["season"], "")

    def test_a_wrapping_season_reads_in_order(self):
        result = fee.period_fee(
            _Doc(invoicing_frequency="Monthly", annual_maintenance_fee=4000),
            _contract(recurring_amount=0, billing_season_start="November", billing_season_end="February"),
        )
        self.assertEqual((result["amount"], result["season"]), (1000.0, "November through February"))


class TestLinkedContract(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_an_active_contract_beats_a_newer_expired_one(self):
        STATE["tables"]["Sapphire Maintenance Contract"] = [
            _contract(name="MNT-NEW", status="Expired"),
            _contract(name="MNT-OLD", status="Active"),
        ]
        self.assertEqual(fee.linked_maintenance_contract("SF-MAINT-2026-0018").name, "MNT-OLD")
        doctype, filters, kwargs = STATE["get_all"][0]
        self.assertEqual(filters, {"project_contract": "SF-MAINT-2026-0018", "status": ["!=", "Canceled"]})
        self.assertEqual(kwargs["order_by"], "modified desc")
        self.assertTrue(kwargs["ignore_permissions"])

    def test_otherwise_the_newest(self):
        STATE["tables"]["Sapphire Maintenance Contract"] = [_contract(name="A", status="Draft"), _contract(name="B", status="Expired")]
        self.assertEqual(fee.linked_maintenance_contract("X").name, "A")

    def test_an_unsaved_agreement_has_none_and_asks_nothing(self):
        self.assertIsNone(fee.linked_maintenance_contract(None))
        self.assertEqual(STATE["get_all"], [])

    def test_the_lookup_never_breaks_a_print(self):
        STATE["get_all_raises"] = True
        self.assertIsNone(fee.period_fee_for(_Doc(name="SF-MAINT-2026-0018", invoicing_frequency="Monthly")))
        self.assertEqual(STATE["errors"], ["Maintenance agreement: service fee lookup failed"])

    def test_period_fee_for_reads_the_linked_contract(self):
        STATE["tables"]["Sapphire Maintenance Contract"] = [_contract()]
        result = fee.period_fee_for(_Doc(name="SF-MAINT-2026-0018", invoicing_frequency="Monthly"))
        self.assertEqual((result["amount"], result["season"]), (1490.0, "May through September"))

    def test_the_render_context_passes_one_figure_to_the_template_and_the_summary(self):
        source = (APP / "project_enhancements" / "doctype" / "project_contract" / "project_contract.py").read_text(
            encoding="utf-8"
        )
        context = source[source.index("def _render_context("):source.index("def _hold_points(")]
        self.assertIn('fee = period_fee_for(doc) if doc.get("template_key") == "maintenance" else None', context)
        self.assertIn('"period_fee": fee,', context)
        self.assertIn("summary_html(doc, period_fee=fee)", context)


# --------------------------------------------------------------------------- the printed agreement


@unittest.skipUnless(jinja2, "jinja2 is not installed")
class TestTemplate(unittest.TestCase):
    def render(self, text=None, period_fee=None, options=None, **overrides):
        blank = lambda width=30: "____"  # noqa: E731
        fill = lambda v, w=30: blank() if v in (None, "") else str(v)  # noqa: E731
        money = lambda v, w=14: blank() if not v else f"${float(v):,.2f}"  # noqa: E731
        qty = lambda v: None if not v else f"{float(v):g}"  # noqa: E731
        doc = _Doc(
            template_key="maintenance",
            party_display="Wasatch Retail & Hospitality",
            payment_method="Invoice (Check or Bank Transfer)",
            payment_section_choice="Payment Link",
            invoicing_frequency="Monthly",
            visit_frequency="Bi-Weekly",
            emergency_rate=250,
            after_hours_emergency_rate=350,
            emergency_minimum_hours=1,
            renewal_increase_percent=12,
            invoice_due_days=30,
            annual_maintenance_fee=0,
        )
        doc.update(overrides)
        if options is None:
            options = {"standard": _Doc(included=0, price=0), "as_needed": _Doc(included=0, price=180)}
        env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
        return env.from_string(text if text is not None else TEMPLATE.read_text(encoding="utf-8")).render(
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
            initial=lambda key, label="": "[__]",
            summary=lambda: "",
            signature=_Doc(),
            service_options=options,
            period_fee=period_fee,
        )

    def day_dairy(self):
        return fee.period_fee(_Doc(invoicing_frequency="Monthly"), _contract())

    def test_day_dairy_prints_1490_a_month_for_the_season(self):
        html = self.render(period_fee=self.day_dairy())
        self.assertIn("<p><b>Service Fee: $1,490.00 per month</b>, billed May through September.</p>", html)
        self.assertNotIn("Annual Service Fee Total", html)  # no annual figure, so no blank line beside the fee
        self.assertIn("The Service Fee counts base rates only.", html)
        self.assertNotIn("$0.00", html)

    def test_quarterly_and_yearly_wording(self):
        quarterly = fee.period_fee(_Doc(invoicing_frequency="Quarterly", annual_maintenance_fee=12000))
        html = self.render(period_fee=quarterly, invoicing_frequency="Quarterly", annual_maintenance_fee=12000)
        self.assertIn("<p><b>Service Fee: $3,000.00 per quarter</b>.</p>", html)
        self.assertIn("Annual Service Fee Total (if applicable): $12,000.00", html)
        self.assertIn("The Service Fee and the Annual Service Fee Total count base rates only.", html)
        yearly = fee.period_fee(
            _Doc(invoicing_frequency="Annually"), _contract(invoicing_frequency="Annually", recurring_amount=9000)
        )
        self.assertIn("Service Fee: $9,000.00 per year</b>.", self.render(period_fee=yearly))

    def test_per_visit_prints_no_fee_line(self):
        per_visit = fee.period_fee(_Doc(invoicing_frequency="Per Visit"), _contract())
        html = self.render(
            period_fee=per_visit,
            invoicing_frequency="Per Visit",
            options={"standard": _Doc(included=1, price=690)},
        )
        self.assertNotIn("Service Fee:", html)
        self.assertIn("[x]  Standard Maintenance Plan  –  base rate $690.00 per visit, plus chemicals", html)

    def test_no_figure_prints_the_paper_blanks_as_before(self):
        html = self.render(period_fee=None)
        self.assertNotIn("Service Fee:", html)
        self.assertIn("Annual Service Fee Total (if applicable): ____", html)
        self.assertIn("The Annual Service Fee Total counts base rates only.", html)
        self.assertIn("base rate ____ per visit", html)

    def test_a_ticked_standard_plan_without_a_rate_points_at_the_fee(self):
        options = {"standard": _Doc(included=1, price=0)}
        html = self.render(period_fee=self.day_dairy(), options=options)
        self.assertIn("[x]  Standard Maintenance Plan  –  base rate in the Service Fee below, plus chemicals", html)
        priced = self.render(period_fee=self.day_dairy(), options={"standard": _Doc(included=1, price=690)})
        self.assertIn("base rate $690.00 per visit, plus chemicals", priced)

    def test_without_a_fee_the_agreement_renders_exactly_as_v1_611_1(self):
        new = TEMPLATE.read_text(encoding="utf-8")
        old = new
        for before, after in CHANGES:
            self.assertEqual(old.count(after), 1, after[:60])
            old = old.replace(after, before)
        self.assertEqual(hashlib.sha256(old.encode("utf-8")).hexdigest(), V1_611_1_SHA256)
        for overrides in ({}, {"annual_maintenance_fee": 17940}, {"invoicing_frequency": "Per Visit"}):
            for options in (None, {"standard": _Doc(included=1, price=690)}):
                self.assertEqual(
                    self.render(text=new, options=options, **overrides),
                    self.render(text=old, options=options, **overrides),
                    (overrides, options),
                )

    def test_renders_completely(self):
        html = self.render(period_fee=self.day_dairy())
        self.assertNotIn("{{", html)
        self.assertNotIn("{%", html)


class TestSummary(unittest.TestCase):
    def setUp(self):
        _reset()

    def doc(self, **overrides):
        doc = _Doc(
            template_key="maintenance",
            visit_frequency="Bi-Weekly",
            invoicing_frequency="Monthly",
            payment_method="Invoice (Check or Bank Transfer)",
            service_options=[_Doc(option_key="standard", included=1, price=0)],
        )
        doc.update(overrides)
        return doc

    def test_states_the_fee_the_agreement_prints(self):
        period = fee.period_fee(_Doc(invoicing_frequency="Monthly"), _contract())
        html = summary.summary_html(self.doc(), period_fee=period)
        self.assertIn("<b>Service fee:</b> $1,490.00 a month, billed May through September.", html)
        self.assertIn("standard maintenance visits every two weeks", html)

    def test_no_fee_no_row(self):
        self.assertNotIn("Service fee:", summary.summary_html(self.doc()))
        self.assertNotIn("Service fee:", summary.summary_html(self.doc(), period_fee=None))


# --------------------------------------------------------------------------- the patch


class TestPatch(unittest.TestCase):
    def setUp(self):
        self.new = TEMPLATE.read_text(encoding="utf-8")
        self.old = self.new
        for before, after in CHANGES:
            self.old = self.old.replace(after, before)
        _reset(body=self.old)
        STATE["tables"]["Project Contract"] = ["SF-MAINT-2026-0001"]

    def test_decides_from_the_shipped_hashes(self):
        self.assertEqual(patch.decide(self.new, self.new), "current")
        self.assertEqual(patch.decide(self.old, self.new), "replace")
        self.assertEqual(patch.decide(self.old + "<p>edited on the site</p>", self.new), "diverged")
        self.assertIn(V1_611_1_SHA256, patch.REPLACEABLE_BODY_SHA256)
        # v1.609.0 predates the 2026-10-13 terms; their own patch resets issued agreements before swapping.
        self.assertNotIn("4b77c564f827ccff3ab915a8cab6b582f72bb4493ff67ec730c4b0e07fba2967", patch.REPLACEABLE_BODY_SHA256)
        # Otherwise every migrate would see "replace" and rewrite the record.
        self.assertNotIn(patch.body_sha256(self.new), patch.REPLACEABLE_BODY_SHA256)

    def test_replaces_the_prod_body_after_freezing_paper_signed_copies(self):
        patch.execute()
        self.assertEqual(STATE["body"], self.new)
        self.assertEqual(STATE["writes"], [("Contract Template", "maintenance", "body")])
        self.assertEqual(STATE["frozen"], ["SF-MAINT-2026-0001"])
        self.assertEqual(STATE["errors"], [])
        self.assertEqual(STATE["commits"], 1)

    def test_a_second_run_does_nothing(self):
        patch.execute()
        STATE["writes"].clear()
        STATE["frozen"].clear()
        patch.execute()
        self.assertEqual((STATE["writes"], STATE["frozen"], STATE["errors"]), ([], [], []))

    def test_a_body_edited_on_the_site_is_logged_and_left_alone(self):
        STATE["body"] = self.old.replace("Sapphire Fountains, LLC", "Sapphire Fountains LLC", 1)
        patch.execute()
        self.assertEqual(STATE["writes"], [])
        self.assertEqual(STATE["frozen"], [])
        self.assertEqual(STATE["errors"], ["Maintenance agreement fee line: template not updated"])

    def test_waits_while_a_signing_link_is_out(self):
        STATE["tables"]["Contract Signature Request"] = ["CSR-0001"]
        patch.execute()
        self.assertEqual(STATE["writes"], [])
        self.assertEqual(STATE["errors"], ["Maintenance agreement fee line: template not updated yet"])

    def test_a_site_without_the_template_is_skipped(self):
        STATE["body"] = None
        patch.execute()
        self.assertEqual((STATE["writes"], STATE["errors"]), ([], []))

    def test_never_raises(self):
        STATE["get_value_raises"] = True
        patch.execute()
        self.assertEqual(STATE["errors"], ["Maintenance agreement fee line patch failed"])

    def test_registered_after_the_layout_refresh(self):
        lines = [line for line in (APP / "patches.txt").read_text(encoding="utf-8").splitlines() if line and line[0] != "#"]
        layout = lines.index("erpnext_enhancements.patches.refresh_maintenance_agreement_layout")
        self.assertGreater(lines.index("erpnext_enhancements.patches.refresh_maintenance_agreement_fee_line"), layout)


if __name__ == "__main__":
    unittest.main()
