"""Bench-free tests for ``workforce/costing.py`` — pay rate resolution and labour cost.

Guards the class of bug where a Job Interval is costed at the wrong money: the
rate picked from the wrong effective date (a future-dated raise applied early, or
last year's rate because "latest" was read as "first"), a salaried employee costed
at their annual figure, a blank burden read as zero when the site default is 25 %
(or a deliberate 0 % overwritten by the default), paused time costed as worked, or
an Activity Cost row's hand-set ``billing_rate`` overwritten by the sync. The
arithmetic lives in pure helpers so it is pinned directly; the ``frappe``-touching
paths run against a stub installed in ``setUpModule``, so this suite needs its own
CI step.

Run: python -m unittest erpnext_enhancements.tests.test_workforce_costing
"""

import sys
import types
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

costing = None

#: Mutable state the stub reads at call time.
STATE = {
    "pay_rates": {},  # employee -> [row dicts]
    "tables": {"Employee Pay Rate", "Activity Cost"},
    "columns": {"Job Interval": {"burdened_rate"}, "Employee": {"custom_position", "custom_position_tier"}},
    "settings": {"default_burden_pct": 0},
    "employees": {},  # name -> {custom_position, custom_position_tier}
    "activity_costs": {},  # (employee, activity) -> {name, costing_rate, billing_rate}
    "activity_types": ["Execution", "Travel"],
    "active_employees": [],
    "inserted": [],
    "set_values": [],
    "errors": [],
}


class StubThrow(Exception):
    pass


def _cint(v):
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _flt(v, precision=None):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _getdate(v=None):
    if v is None:
        return date.today()
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _get_datetime(v):
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    text = str(v)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(text)


class _Row(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class _StubDoc(_Row):
    """A get_doc({...}) result that records its insert."""

    def insert(self, ignore_permissions=False):
        STATE["inserted"].append(dict(self))
        key = (self.get("employee"), self.get("activity_type"))
        STATE["activity_costs"][key] = {"name": f"AC-{len(STATE['inserted'])}", "costing_rate": self.get("costing_rate"),
                                        "billing_rate": self.get("billing_rate")}
        return self


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")
    frappe._ = lambda s: s
    frappe.flags = _Row(in_migrate=False, in_install=False, in_patch=False)

    def throw(msg, exc=None, title=None):
        raise StubThrow(msg)

    frappe.throw = throw
    frappe.ValidationError = StubThrow
    frappe.PermissionError = StubThrow

    def log_error(message=None, title=None):
        STATE["errors"].append(title or message)

    frappe.log_error = log_error

    def get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, limit=None, **kw):
        filters = filters or {}
        if doctype == "Employee Pay Rate":
            rows = [_Row(r) for r in STATE["pay_rates"].get(filters.get("parent"), [])]
            return sorted(rows, key=lambda r: r["effective_from"])
        if doctype == "Employee":
            return list(STATE["active_employees"])
        if doctype == "Activity Type":
            return list(STATE["activity_types"])
        raise AssertionError(f"unexpected get_all({doctype})")

    frappe.get_all = get_all

    def get_doc(spec):
        return _StubDoc(spec)

    frappe.get_doc = get_doc

    def get_cached_doc(doctype):
        assert doctype == "Time Kiosk Settings"
        return _Row(STATE["settings"])

    frappe.get_cached_doc = get_cached_doc

    def db_get_value(doctype, name, fields=None, as_dict=False, **kw):
        if doctype == "Employee":
            row = STATE["employees"].get(name)
            return _Row(row) if row and as_dict else row
        if doctype == "Activity Cost":
            key = (name.get("employee"), name.get("activity_type"))
            row = STATE["activity_costs"].get(key)
            return _Row(row) if row else None
        raise AssertionError(f"unexpected get_value({doctype})")

    def db_set_value(doctype, name, field, value=None, **kw):
        STATE["set_values"].append((doctype, name, field, value))
        if doctype == "Activity Cost":
            for row in STATE["activity_costs"].values():
                if row["name"] == name:
                    row[field] = value

    def has_column(doctype, column):
        if doctype not in STATE["columns"]:
            raise Exception("TableMissingError")  # frappe raises on an unknown table
        return column in STATE["columns"][doctype]

    frappe.db = types.SimpleNamespace(
        table_exists=lambda dt: dt in STATE["tables"],
        has_column=has_column,
        get_value=db_get_value,
        set_value=db_set_value,
        get_single_value=lambda dt, f: STATE["settings"].get(f),
    )

    utils = types.ModuleType("frappe.utils")
    utils.cint, utils.flt, utils.getdate, utils.get_datetime = _cint, _flt, _getdate, _get_datetime
    utils.nowdate = lambda: str(date.today())
    frappe.utils = utils

    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")

    class Document:
        pass

    document.Document = Document
    model.document = document

    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils
    sys.modules["frappe.model"] = model
    sys.modules["frappe.model.document"] = document


def setUpModule():
    global costing
    _install_frappe_stub()
    for name in list(sys.modules):
        if name.startswith("erpnext_enhancements.workforce"):
            del sys.modules[name]
    from erpnext_enhancements.workforce import costing as mod

    costing = mod


def _reset(**overrides):
    STATE.update(
        pay_rates={}, settings={"default_burden_pct": 0}, employees={}, activity_costs={},
        active_employees=[], inserted=[], set_values=[], errors=[],
        tables={"Employee Pay Rate", "Activity Cost"},
        columns={"Job Interval": {"burdened_rate"}, "Employee": {"custom_position", "custom_position_tier"}},
    )
    STATE.update(overrides)


def rate(effective_from, pay_type="Hourly", hourly_rate=None, annual_salary=None, hourly_equivalent=None, burden_pct=None):
    return {"effective_from": effective_from, "pay_type": pay_type, "hourly_rate": hourly_rate,
            "annual_salary": annual_salary, "hourly_equivalent": hourly_equivalent, "burden_pct": burden_pct}


class TestPureArithmetic(unittest.TestCase):
    def test_hourly_equivalent_is_annual_over_2080(self):
        self.assertEqual(costing.hourly_equivalent(104000), 50.0)
        self.assertEqual(costing.hourly_equivalent(0), 0.0)
        self.assertEqual(costing.hourly_equivalent(None), 0.0)

    def test_burdened_rate(self):
        self.assertEqual(costing.burdened_rate(50, 25), 62.5)
        self.assertEqual(costing.burdened_rate(50, 0), 50.0)

    def test_worked_hours_net_of_pauses_and_clamped(self):
        start = datetime(2026, 9, 17, 8)
        self.assertEqual(costing.worked_hours(start, start + timedelta(hours=9), 3600), 8.0)
        self.assertEqual(costing.worked_hours(start, start + timedelta(minutes=30), 7200), 0.0)
        self.assertEqual(costing.worked_hours(start, None, 0), 0.0)

    def test_labor_cost_to_the_cent(self):
        start = datetime(2026, 9, 17, 8)
        # 7.5 h × 62.5 = 468.75
        self.assertEqual(costing.labor_cost(start, start + timedelta(hours=8), 1800, 62.5), 468.75)
        # 1/3 of an hour × 31.11 rounds
        self.assertEqual(costing.labor_cost(start, start + timedelta(minutes=20), 0, 31.11), 10.37)


class TestResolveRate(unittest.TestCase):
    ROWS = [rate("2026-01-01", hourly_rate=20), rate("2026-07-01", hourly_rate=25), rate("2027-01-01", hourly_rate=30)]

    def test_latest_on_or_before_wins(self):
        self.assertEqual(costing.resolve_rate(self.ROWS, "2026-09-17")["pay_rate"], 25.0)

    def test_the_effective_day_itself_counts(self):
        self.assertEqual(costing.resolve_rate(self.ROWS, "2026-07-01")["pay_rate"], 25.0)
        self.assertEqual(costing.resolve_rate(self.ROWS, "2026-06-30")["pay_rate"], 20.0)

    def test_future_dated_rows_are_ignored(self):
        self.assertEqual(costing.resolve_rate(self.ROWS, "2026-12-31")["pay_rate"], 25.0)

    def test_nothing_in_force_yet_is_none(self):
        self.assertIsNone(costing.resolve_rate(self.ROWS, "2025-12-31"))
        self.assertIsNone(costing.resolve_rate([], "2026-09-17"))

    def test_row_order_does_not_matter(self):
        self.assertEqual(costing.resolve_rate(list(reversed(self.ROWS)), "2026-09-17")["pay_rate"], 25.0)

    def test_salaried_uses_the_hourly_equivalent(self):
        rows = [rate("2026-01-01", "Salaried", annual_salary=104000, hourly_equivalent=50)]
        r = costing.resolve_rate(rows, "2026-09-17")
        self.assertEqual((r["pay_type"], r["pay_rate"]), ("Salaried", 50.0))

    def test_salaried_computes_the_equivalent_when_blank(self):
        rows = [rate("2026-01-01", "Salaried", annual_salary=104000)]
        self.assertEqual(costing.resolve_rate(rows, "2026-09-17")["pay_rate"], 50.0)

    def test_blank_burden_takes_the_default(self):
        r = costing.resolve_rate([rate("2026-01-01", hourly_rate=40)], "2026-09-17", default_burden_pct=25)
        self.assertEqual((r["burden_pct"], r["burdened_rate"]), (25.0, 50.0))

    def test_an_explicit_zero_burden_is_not_blank(self):
        r = costing.resolve_rate([rate("2026-01-01", hourly_rate=40, burden_pct=0)], "2026-09-17", default_burden_pct=25)
        self.assertEqual((r["burden_pct"], r["burdened_rate"]), (0.0, 40.0))

    def test_row_burden_beats_the_default(self):
        r = costing.resolve_rate([rate("2026-01-01", hourly_rate=40, burden_pct=10)], "2026-09-17", default_burden_pct=25)
        self.assertEqual(r["burdened_rate"], 44.0)

    def test_accepts_row_objects(self):
        r = costing.resolve_rate([_Row(rate("2026-01-01", hourly_rate=40))], "2026-09-17")
        self.assertEqual(r["pay_rate"], 40.0)


class TestRateFor(unittest.TestCase):
    def test_reads_the_child_table_and_the_settings_default(self):
        _reset(pay_rates={"EMP-1": [rate("2026-01-01", hourly_rate=40)]}, settings={"default_burden_pct": 25})
        self.assertEqual(costing.rate_for("EMP-1", "2026-09-17")["burdened_rate"], 50.0)

    def test_no_table_is_none_not_a_crash(self):
        _reset(pay_rates={"EMP-1": [rate("2026-01-01", hourly_rate=40)]}, tables=set())
        self.assertIsNone(costing.rate_for("EMP-1", "2026-09-17"))

    def test_no_employee_is_none(self):
        _reset()
        self.assertIsNone(costing.rate_for(None, "2026-09-17"))


class TestStampCost(unittest.TestCase):
    def _interval(self, **kw):
        base = dict(employee="EMP-1", start_time=datetime(2026, 9, 17, 8), end_time=datetime(2026, 9, 17, 16),
                    total_paused_seconds=1800, pay_type=None, pay_rate=None, burden_pct=None, burdened_rate=None,
                    labor_cost=None)
        base.update(kw)
        return _Row(base)

    def test_stamps_rate_and_cost(self):
        _reset(pay_rates={"EMP-1": [rate("2026-01-01", hourly_rate=40, burden_pct=25)]})
        doc = self._interval()
        costing.stamp_cost(doc)
        self.assertEqual((doc.pay_type, doc.pay_rate, doc.burden_pct, doc.burdened_rate), ("Hourly", 40.0, 25.0, 50.0))
        self.assertEqual(doc.labor_cost, 375.0)  # 7.5 h × 50

    def test_rate_is_as_of_the_start_date(self):
        _reset(pay_rates={"EMP-1": [rate("2026-01-01", hourly_rate=40), rate("2026-09-18", hourly_rate=99)]})
        doc = self._interval()
        costing.stamp_cost(doc)
        self.assertEqual(doc.pay_rate, 40.0)

    def test_no_end_time_means_rate_only(self):
        _reset(pay_rates={"EMP-1": [rate("2026-01-01", hourly_rate=40)]})
        doc = self._interval(end_time=None)
        costing.stamp_cost(doc)
        self.assertEqual(doc.burdened_rate, 40.0)
        self.assertIsNone(doc.labor_cost)

    def test_no_rate_leaves_everything_blank(self):
        _reset()
        doc = self._interval(pay_rate=12, labor_cost=99)
        costing.stamp_cost(doc)
        self.assertIsNone(doc.pay_rate)
        self.assertIsNone(doc.labor_cost)

    def test_missing_columns_leave_the_doc_untouched(self):
        _reset(pay_rates={"EMP-1": [rate("2026-01-01", hourly_rate=40)]}, columns={"Job Interval": set()})
        doc = self._interval()
        costing.stamp_cost(doc)
        self.assertIsNone(doc.pay_rate)


class TestStampPosition(unittest.TestCase):
    def test_copies_position_and_tier(self):
        _reset(employees={"EMP-1": {"custom_position": "Technician", "custom_position_tier": 2}})
        doc = _Row(employee="EMP-1", position=None, position_tier=None)
        costing.stamp_position(doc)
        self.assertEqual((doc.position, doc.position_tier), ("Technician", 2))

    def test_never_overwrites(self):
        _reset(employees={"EMP-1": {"custom_position": "Senior Technician", "custom_position_tier": 3}})
        doc = _Row(employee="EMP-1", position="Technician", position_tier=2)
        costing.stamp_position(doc)
        self.assertEqual(doc.position, "Technician")

    def test_unknown_table_is_survived(self):
        _reset(columns={})
        doc = _Row(employee="EMP-1", position=None, position_tier=None)
        costing.stamp_position(doc)  # no raise
        self.assertIsNone(doc.position)


class _PayRow(_Row):
    pass


class TestValidateEmployeePayRates(unittest.TestCase):
    def _doc(self, *rows):
        return _Row(name="EMP-1", custom_pay_rates=[_PayRow(dict(r, idx=i + 1)) for i, r in enumerate(rows)])

    def test_no_table_attribute_is_a_no_op(self):
        _reset()
        costing.validate_employee_pay_rates(_Row(name="EMP-1"))  # the test-bootstrap case

    def test_hourly_needs_a_rate(self):
        _reset()
        with self.assertRaises(StubThrow):
            costing.validate_employee_pay_rates(self._doc(rate("2026-01-01", hourly_rate=0)))

    def test_salaried_needs_a_salary_and_gets_an_equivalent(self):
        _reset()
        with self.assertRaises(StubThrow):
            costing.validate_employee_pay_rates(self._doc(rate("2026-01-01", "Salaried")))
        doc = self._doc(rate("2026-01-01", "Salaried", annual_salary=104000))
        costing.validate_employee_pay_rates(doc)
        self.assertEqual(doc.custom_pay_rates[0].hourly_equivalent, 50.0)

    def test_duplicate_effective_dates_throw(self):
        _reset()
        with self.assertRaises(StubThrow) as ctx:
            costing.validate_employee_pay_rates(self._doc(rate("2026-01-01", hourly_rate=1), rate("2026-01-01", hourly_rate=2)))
        self.assertIn("2026-01-01", str(ctx.exception))

    def test_rows_are_sorted_and_renumbered(self):
        _reset()
        doc = self._doc(rate("2026-07-01", hourly_rate=25), rate("2026-01-01", hourly_rate=20))
        costing.validate_employee_pay_rates(doc)
        self.assertEqual([r.effective_from for r in doc.custom_pay_rates], ["2026-01-01", "2026-07-01"])
        self.assertEqual([r.idx for r in doc.custom_pay_rates], [1, 2])

    def test_negative_burden_throws(self):
        _reset()
        with self.assertRaises(StubThrow):
            costing.validate_employee_pay_rates(self._doc(rate("2026-01-01", hourly_rate=1, burden_pct=-5)))


class TestSyncActivityCosts(unittest.TestCase):
    def test_creates_rows_with_billing_zero(self):
        _reset(pay_rates={"EMP-1": [rate("2020-01-01", hourly_rate=40, burden_pct=25)]}, active_employees=["EMP-1"])
        written = costing.sync_activity_costs()
        self.assertEqual(written, 2)
        self.assertEqual({(i["employee"], i["activity_type"], i["costing_rate"], i["billing_rate"]) for i in STATE["inserted"]},
                         {("EMP-1", "Execution", 50.0, 0), ("EMP-1", "Travel", 50.0, 0)})

    def test_updates_costing_and_never_billing(self):
        _reset(pay_rates={"EMP-1": [rate("2020-01-01", hourly_rate=40)]}, active_employees=["EMP-1"],
               activity_costs={("EMP-1", "Execution"): {"name": "AC-9", "costing_rate": 30, "billing_rate": 120}})
        costing.sync_activity_costs(employee="EMP-1", activity_type="Execution")
        self.assertEqual(STATE["set_values"], [("Activity Cost", "AC-9", "costing_rate", 40.0)])
        self.assertEqual(STATE["activity_costs"][("EMP-1", "Execution")]["billing_rate"], 120)

    def test_an_unchanged_rate_writes_nothing(self):
        _reset(pay_rates={"EMP-1": [rate("2020-01-01", hourly_rate=40)]}, active_employees=["EMP-1"],
               activity_costs={("EMP-1", "Execution"): {"name": "AC-9", "costing_rate": 40.0, "billing_rate": 0}})
        self.assertEqual(costing.sync_activity_costs(employee="EMP-1", activity_type="Execution"), 0)
        self.assertEqual(STATE["set_values"], [])

    def test_employees_without_a_rate_are_skipped(self):
        _reset(active_employees=["EMP-1", "EMP-2"], pay_rates={"EMP-2": [rate("2020-01-01", hourly_rate=10)]})
        costing.sync_activity_costs()
        self.assertEqual({i["employee"] for i in STATE["inserted"]}, {"EMP-2"})

    def test_a_failure_is_logged_and_swallowed(self):
        _reset(pay_rates={"EMP-1": [rate("2020-01-01", hourly_rate=40)]}, active_employees=["EMP-1"])
        original = sys.modules["frappe"].get_doc
        sys.modules["frappe"].get_doc = lambda spec: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.assertEqual(costing.sync_activity_costs(employee="EMP-1"), 0)
        finally:
            sys.modules["frappe"].get_doc = original
        self.assertTrue(STATE["errors"])

    def test_missing_tables_are_a_no_op(self):
        _reset(tables=set(), active_employees=["EMP-1"], pay_rates={"EMP-1": [rate("2020-01-01", hourly_rate=40)]})
        self.assertEqual(costing.sync_activity_costs(), 0)


class TestOnEmployeeUpdate(unittest.TestCase):
    def test_unchanged_rates_do_not_sync(self):
        _reset(pay_rates={"EMP-1": [rate("2020-01-01", hourly_rate=40)]})
        rows = [_PayRow(rate("2020-01-01", hourly_rate=40))]
        before = _Row(name="EMP-1", custom_pay_rates=rows)
        doc = _Row(name="EMP-1", custom_pay_rates=list(rows))
        doc.get_doc_before_save = lambda: before
        costing.on_employee_update(doc)
        self.assertEqual(STATE["inserted"], [])

    def test_changed_rates_sync(self):
        _reset(pay_rates={"EMP-1": [rate("2020-01-01", hourly_rate=45)]}, activity_types=["Execution"])
        before = _Row(name="EMP-1", custom_pay_rates=[_PayRow(rate("2020-01-01", hourly_rate=40))])
        doc = _Row(name="EMP-1", custom_pay_rates=[_PayRow(rate("2020-01-01", hourly_rate=45))])
        doc.get_doc_before_save = lambda: before
        costing.on_employee_update(doc)
        self.assertEqual(len(STATE["inserted"]), 1)
        STATE["activity_types"] = ["Execution", "Travel"]

    def test_no_table_attribute_is_a_no_op(self):
        _reset()
        costing.on_employee_update(_Row(name="EMP-1"))
        self.assertEqual(STATE["inserted"], [])


if __name__ == "__main__":
    unittest.main()
