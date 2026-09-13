# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The maintenance sweeps, and the nullable column that emptied the contract book.

`maintenance_renewal.expire_or_renew_contracts` filtered
``{"status": "Active", "end_date": ["<", today]}``. ``end_date`` is nullable and its own
field description states the invariant: **"Blank never expires."** Frappe wraps a
comparison on a nullable column in an ifnull sentinel set to the *minimum* of the type
(``frappe/model/db_query.py``, ``prepare_filter_condition``), so
``ifnull(end_date, '0001-01-01') < today`` matched precisely the contracts that were
never supposed to expire. A blank end date also implies the term is not in
``FIXED_YEAR_TERMS``, so ``renewing`` was False and each fell through to the ``else`` and
was force-expired.

``status = "Active"`` gates **both** revenue paths — visit scheduling and recurring
billing — so those contracts silently stopped producing either. Fifteen of the site's
sixteen went that way on 2026-09-11, two days after they were entered.

**Why this file exists at all.** ``test_sapphire_maintenance`` and
``test_maintenance_sections`` both build real documents and need a bench, so neither runs
in CI. These sweeps therefore had *no* automated coverage of any kind. This suite is
bench-free — its own `frappe` stub, its own CI step — and it exists to run the sweeps
rather than read them.

**The stub models the framework, not intuition.** The Python-obvious answer for
``None < date`` is "no match", and that is what production does NOT do. A stub that is
more correct than the thing it stands in for hides that thing's behaviour exactly as
thoroughly as one that mirrors a mistake — which is how the identical bug in
``training/certificates.py`` survived a correct test for months.

Run: python -m unittest erpnext_enhancements.tests.test_maintenance_expiry_filters
"""

import ast
import datetime
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
RENEWAL_PY = APP / "api/maintenance_renewal.py"
BILLING_PY = APP / "api/maintenance_billing.py"
PATCH_PY = APP / "patches/restore_force_expired_maintenance_contracts.py"
PATCHES_TXT = APP / "patches.txt"
CONTRACT_JSON = (
    APP / "sapphire_maintenance/doctype/sapphire_maintenance_contract"
    "/sapphire_maintenance_contract.json"
)

CONTRACT = "Sapphire Maintenance Contract"
TODAY = datetime.date(2026, 9, 13)

#: What frappe substitutes for a NULL. Empty string sorts below any real date string and
#: is itself the fallback frappe uses for text columns, so one value models both the
#: ordering comparisons and the equality ones.
NULL_FALLBACK = ""

STATE = {"rows": {}, "writes": [], "comments": [], "notices": [], "billed": []}

renewal = None
billing = None


# ------------------------------------------------------------------- the stub


def _to_date(value):
    if isinstance(value, datetime.date):
        return value
    if not value:
        return None
    return datetime.date.fromisoformat(str(value)[:10])


def _key(value):
    """Comparison key that reproduces the ifnull fallback for a NULL."""
    if value is None:
        return NULL_FALLBACK
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value)


def _matches(row, filters):
    for field, operator, target in _conditions(filters):
        raw = row.get(field)
        value = _key(raw)
        goal = _key(target)
        if operator == "=":
            if raw != target:
                return False
        elif operator == "<":
            if not value < goal:
                return False
        elif operator == "<=":
            if not value <= goal:
                return False
        elif operator == ">":
            # `>` never matches a NULL: the sentinel is the minimum of the type.
            if not value > goal:
                return False
        elif operator == ">=":
            if not value >= goal:
                return False
        elif operator == "in":
            if raw not in target:
                return False
        elif operator == "not in":
            if value in [_key(t) for t in target]:
                return False
        elif operator == "!=":
            if value == goal:
                return False
        elif operator == "is":
            if target == "set" and not raw:
                return False
            if target == "not set" and raw:
                return False
        else:
            raise AssertionError(f"stub does not implement operator {operator!r}")
    return True


def _conditions(filters):
    """Both filter forms frappe accepts. The list form is what the fix needs."""
    if isinstance(filters, dict):
        for field, condition in (filters or {}).items():
            if isinstance(condition, list | tuple):
                yield field, condition[0], condition[1]
            else:
                yield field, "=", condition
        return
    for condition in filters or []:
        if len(condition) == 4:
            yield condition[1], condition[2], condition[3]
        else:
            yield condition[0], condition[1], condition[2]


def _add_years(value, years):
    day = _to_date(value)
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def _install_stub():
    fake = types.ModuleType("frappe")
    fake._ = lambda text: text
    fake.whitelist = lambda *a, **k: (lambda fn: fn)

    def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
        rows = [r for r in STATE["rows"].values() if _matches(r, filters)]
        if pluck:
            return [r.get(pluck) for r in rows]
        if fields:
            return [types.SimpleNamespace(**{f: r.get(f) for f in fields}) for r in rows]
        return rows

    def set_value(doctype, name, field, value=None, update_modified=True):
        target = STATE["rows"].get(name)
        changes = field if isinstance(field, dict) else {field: value}
        STATE["writes"].append((name, dict(changes)))
        if target:
            target.update(changes)

    fake.get_all = get_all
    fake.db = types.SimpleNamespace(
        get_single_value=lambda doctype, field: 1,
        set_value=set_value,
        exists=lambda *a, **k: True,
    )
    fake.log_error = lambda *a, **k: None
    fake.get_traceback = lambda: ""
    sys.modules["frappe"] = fake

    utils = types.ModuleType("frappe.utils")
    utils.getdate = _to_date
    utils.nowdate = lambda: TODAY.isoformat()
    utils.add_days = lambda d, n: _to_date(d) + datetime.timedelta(days=n)
    utils.add_years = _add_years
    utils.cint = lambda v: int(v or 0)
    utils.flt = lambda v, *a: float(v or 0)
    utils.fmt_money = lambda v, currency=None: str(v)
    utils.formatdate = lambda v=None, fmt=None: str(v)

    def _add_months(value, months):
        day = _to_date(value)
        total = day.month - 1 + months
        year, month = day.year + total // 12, total % 12 + 1
        last = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
                31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
        return datetime.date(year, month, min(day.day, last))

    utils.add_months = _add_months
    utils.today = lambda: TODAY.isoformat()
    utils.date_diff = lambda a, b: (_to_date(a) - _to_date(b)).days
    sys.modules["frappe.utils"] = utils
    fake.utils = utils
    fake.defaults = types.SimpleNamespace(
        get_global_default=lambda key: "USD", get_default=lambda key: "Sapphire Fountains"
    )
    return fake


def setUpModule():
    global renewal, billing
    fake = _install_stub()
    fake.db.savepoint = lambda name: None
    fake.db.rollback = lambda save_point=None: None
    from erpnext_enhancements.api import maintenance_billing as _billing
    from erpnext_enhancements.api import maintenance_renewal as _renewal

    renewal = _renewal
    billing = _billing
    # The notification stack and the Sales Invoice builder are separate seams; a unit
    # test of the FILTER must not exercise either. Recording which contracts reached
    # `_bill_period` is the whole assertion.
    renewal._notify = lambda *a, **k: True
    renewal._add_comment = lambda contract, text: STATE["comments"].append((contract, text))
    billing._bill_period = lambda name: STATE["billed"].append(name)


def _contract(name, **overrides):
    row = {
        "name": name,
        "doctype": CONTRACT,
        "customer": "A Customer",
        "status": "Active",
        "end_date": None,
        "initial_term": "",
        "auto_renew": 1,
        "non_renewal_notice": 0,
        "invoicing_frequency": "Per Visit",
        "recurring_amount": 0,
        "next_billing_date": None,
        "scheduled_rate": 0,
        "rate_effective_date": None,
        "rate_notice_sent": None,
    }
    row.update(overrides)
    STATE["rows"][name] = row
    return row


class _Case(unittest.TestCase):
    def setUp(self):
        STATE["rows"].clear()
        STATE["writes"].clear()
        STATE["comments"].clear()
        STATE["billed"].clear()


# --------------------------------------------------------------- the behaviour


class TestABlankEndDateNeverExpires(_Case):
    """The field says so in as many words, and fifteen live contracts said otherwise."""

    def test_a_month_to_month_contract_is_left_alone(self):
        row = _contract("MNT-1", initial_term="Month-to-Month")
        renewal.expire_or_renew_contracts(TODAY)
        self.assertEqual(row["status"], "Active")
        self.assertEqual(STATE["writes"], [])

    def test_a_blank_term_contract_is_left_alone(self):
        """The shape every one of the fifteen actually had: no term, no end date."""
        row = _contract("MNT-2")
        renewal.expire_or_renew_contracts(TODAY)
        self.assertEqual(row["status"], "Active")

    def test_the_sweep_writes_nothing_at_all_for_them(self):
        for i in range(5):
            _contract(f"MNT-BLANK-{i}")
        renewal.expire_or_renew_contracts(TODAY)
        self.assertEqual(STATE["writes"], [], "a never-expiring contract must not be touched")


class TestAGenuineExpiryStillHappens(_Case):
    """The vacuity guard. A filter narrowed until it matches nothing would pass every
    assertion above while doing no work whatsoever."""

    def test_a_lapsed_month_to_month_contract_expires(self):
        row = _contract("MNT-3", initial_term="Month-to-Month", end_date="2026-09-01")
        renewal.expire_or_renew_contracts(TODAY)
        self.assertEqual(row["status"], "Expired")

    def test_a_contract_ending_today_has_not_lapsed_yet(self):
        row = _contract("MNT-4", initial_term="Month-to-Month", end_date=TODAY.isoformat())
        renewal.expire_or_renew_contracts(TODAY)
        self.assertEqual(row["status"], "Active")

    def test_a_fixed_term_contract_still_auto_renews(self):
        """The other half of the sweep. If the fix had narrowed the filter too far,
        renewals would stop silently and nobody would see it for a year."""
        row = _contract("MNT-5", initial_term="One (1) Year", end_date="2025-09-01")
        renewal.expire_or_renew_contracts(TODAY)
        self.assertEqual(row["status"], "Active")
        # `while new_end < today` rolls forward in whole years until the term end is in
        # the FUTURE, which is the documented behaviour for a contract that lapsed more
        # than a year ago. 2025-09-01 -> 2026-09-01 is still behind 2026-09-13, so it
        # goes round once more. (My first version of this test expected 2026-09-01 and
        # was simply wrong about the loop.)
        self.assertEqual(_to_date(row["end_date"]), datetime.date(2027, 9, 1))
        self.assertTrue(STATE["comments"], "a renewal must leave a comment on the contract")

    def test_a_fixed_term_contract_with_notice_given_expires(self):
        row = _contract(
            "MNT-6", initial_term="One (1) Year", end_date="2025-09-01", non_renewal_notice=1
        )
        renewal.expire_or_renew_contracts(TODAY)
        self.assertEqual(row["status"], "Expired")


class TestTheRateNoticeNeedsAnEffectiveDate(_Case):
    """Third instance of the same defect, one function away in the same file."""

    def test_a_half_entered_rate_change_raises_no_notice(self):
        _contract("MNT-7", scheduled_rate=150, rate_effective_date=None)
        renewal.send_rate_change_notices(TODAY)
        self.assertEqual(STATE["writes"], [])

    def test_a_dated_rate_change_inside_the_window_still_raises_one(self):
        _contract("MNT-8", scheduled_rate=150, rate_effective_date="2026-09-20")
        renewal.send_rate_change_notices(TODAY)
        self.assertTrue(STATE["writes"], "the notice must still fire when a date is set")


class TestBillingNeedsAStartDate(_Case):
    """`next_billing_date` is nullable and its description says "Blank = not yet on
    recurring billing". `_bill_period` opens with `getdate(contract.next_billing_date)`,
    and `getdate(None)` returns TODAY -- so a matched NULL row would have been invoiced
    for a period the code invented."""

    def test_a_contract_not_yet_on_billing_is_not_billed(self):
        _contract(
            "MNT-9",
            invoicing_frequency="Monthly",
            recurring_amount=500,
            next_billing_date=None,
        )
        billing.generate_recurring_invoices(TODAY)
        self.assertEqual(STATE["billed"], [])

    def test_a_contract_that_is_due_is_still_billed(self):
        """Vacuity guard: a filter narrowed until it bills nobody would pass the test
        above while quietly stopping every invoice the company sends."""
        _contract(
            "MNT-10",
            invoicing_frequency="Monthly",
            recurring_amount=500,
            next_billing_date="2026-09-01",
        )
        billing.generate_recurring_invoices(TODAY)
        self.assertEqual(STATE["billed"], ["MNT-10"])

    def test_the_zero_amount_guard_is_what_kept_this_off_the_books(self):
        """Every one of the fifteen force-expired contracts carries recurring_amount 0,
        and `>` does not match the ifnull sentinel -- so restoring them to Active bills
        nobody. That was luck rather than design, which is why the filter above is
        fixed in the same release."""
        _contract(
            "MNT-11",
            invoicing_frequency="Monthly",
            recurring_amount=0,
            next_billing_date=None,
        )
        billing.generate_recurring_invoices(TODAY)
        self.assertEqual(STATE["billed"], [])

    def test_per_visit_contracts_are_not_recurring_at_all(self):
        _contract(
            "MNT-12",
            invoicing_frequency="Per Visit",
            recurring_amount=500,
            next_billing_date="2026-09-01",
        )
        billing.generate_recurring_invoices(TODAY)
        self.assertEqual(STATE["billed"], [])


class TestTheStubModelsTheFrameworkRatherThanIntuition(unittest.TestCase):
    """Guards the guard. Every assertion above depends on the stub reproducing frappe's
    ifnull fallback; if it drifted to Python semantics they would pass while testing
    nothing at all."""

    def test_a_null_matches_a_less_than(self):
        self.assertTrue(_matches({"end_date": None}, {"end_date": ["<", "2026-09-13"]}))

    def test_a_null_does_not_match_a_greater_than(self):
        self.assertFalse(_matches({"recurring_amount": None}, {"recurring_amount": [">", 0]}))

    def test_is_set_excludes_a_null_and_keeps_a_value(self):
        self.assertFalse(_matches({"end_date": None}, {"end_date": ["is", "set"]}))
        self.assertTrue(_matches({"end_date": "2026-01-01"}, {"end_date": ["is", "set"]}))

    def test_the_list_filter_form_is_understood(self):
        """The fix needs two conditions on one field, which a dict cannot express. A
        stub that ignored the list form would exercise none of the shipped filters."""
        clauses = [["status", "=", "Active"], ["end_date", "is", "set"], ["end_date", "<", "2026-09-13"]]
        self.assertFalse(_matches({"status": "Active", "end_date": None}, clauses))
        self.assertTrue(_matches({"status": "Active", "end_date": "2026-09-01"}, clauses))


# --------------------------------------------------------------- the structure


def _code(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree)).replace("'", '"')


class TestEveryNullableComparisonInBothModulesIsGuarded(unittest.TestCase):
    def test_no_bare_comparison_survives_in_either_module(self):
        """Swept over executable source, comments stripped: this release writes several
        comments that name the broken filters while explaining they are gone."""
        offenders = []
        for path in (RENEWAL_PY, BILLING_PY):
            code = _code(path)
            for field in ("end_date", "next_billing_date", "rate_effective_date"):
                for bad in (f'"{field}": ["<"', f'"{field}": ["<="'):
                    if bad in code:
                        offenders.append(f"{path.name}: {bad}")
        self.assertEqual(offenders, [], f"unguarded nullable comparison: {offenders}")

    def test_each_guarded_field_carries_an_is_set_clause(self):
        for path, field in (
            (RENEWAL_PY, "end_date"),
            (RENEWAL_PY, "rate_effective_date"),
            (BILLING_PY, "next_billing_date"),
        ):
            with self.subTest(where=f"{path.name}:{field}"):
                self.assertIn(f'["{field}", "is", "set"]', _code(path))

    def test_the_field_descriptions_still_say_what_the_fix_relies_on(self):
        """The invariant is documented on the doctype, and the fix exists to honour it.
        If somebody rewrote these descriptions the reasoning would be unfindable."""
        import json

        fields = {
            f["fieldname"]: f
            for f in json.loads(CONTRACT_JSON.read_text(encoding="utf-8"))["fields"]
        }
        self.assertIn("never expires", (fields["end_date"].get("description") or "").lower())
        self.assertIn(
            "not yet on recurring billing",
            (fields["next_billing_date"].get("description") or "").lower(),
        )


class TestTheRestorePatch(unittest.TestCase):
    def test_it_is_registered_post_model_sync(self):
        text = PATCHES_TXT.read_text(encoding="utf-8")
        module = "erpnext_enhancements.patches.restore_force_expired_maintenance_contracts"
        self.assertIn(module, text)
        self.assertIn("[post_model_sync]", text[: text.index(module)])

    def test_the_predicate_is_the_broken_filters_own_rule(self):
        code = _code(PATCH_PY)
        self.assertIn('["status", "=", EXPIRED]', code)
        self.assertIn('["end_date", "is", "not set"]', code)

    def test_it_will_not_resurrect_a_deliberately_ended_contract(self):
        """`non_renewal_notice` is how somebody says they meant to end it."""
        self.assertIn('["non_renewal_notice", "=", 0]', _code(PATCH_PY))

    def test_it_restores_to_active_and_touches_nothing_else(self):
        """`end_date` legitimately appears in the SELECT predicate, so the check is on
        what is WRITTEN: exactly one set_value, status only. `validate` re-derives
        several fields, which is why this does not go through the doc API."""
        code = _code(PATCH_PY)
        self.assertIn('"status", ACTIVE, update_modified=False', code)
        self.assertNotIn(".save(", code)
        self.assertNotIn("frappe.get_doc(", code)
        writes = [line for line in code.splitlines() if "set_value" in line]
        self.assertEqual(len(writes), 1, f"expected one write, found: {writes}")
        self.assertNotIn("end_date", writes[0])

    def test_it_names_every_row_it_changed(self):
        self.assertIn("restored %d contract(s)", PATCH_PY.read_text(encoding="utf-8"))

    def test_it_cannot_abort_the_deploy(self):
        code = _code(PATCH_PY)
        self.assertIn("except Exception", code)
        self.assertIn("log_error", code)


class TestItIsWiredIntoCi(unittest.TestCase):
    def test_a_bench_free_suite_that_ci_never_names_runs_nowhere(self):
        """The reason these sweeps had no coverage: both existing maintenance suites
        build real documents, so neither can run without a bench and neither is in
        ci.yml. This one is bench-free precisely so it can be."""
        ci = (APP.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("test_maintenance_expiry_filters", ci)


if __name__ == "__main__":
    unittest.main()
