# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A task with no deadline is not late, and both overdue lists said it was.

``api/task_dashboard.py`` and ``api/briefing.py`` each filtered open tasks with
``{"exp_end_date": ("<", today)}``. ``exp_end_date`` is a nullable Datetime on core
``Task``, and frappe wraps a comparison on a nullable column in an ifnull sentinel set
to the MINIMUM of the type (``frappe/model/db_query.py``, ``prepare_filter_condition``),
so ``ifnull(exp_end_date, '0001-01-01 00:00:00') < today`` matched every deadline-less
task.

**It did far more than pad the list.** Both callers order by ``exp_end_date asc`` and
take a page, and MariaDB sorts NULLs **first** in ascending order — so the coalesced rows
filled the whole budget and pushed the real ones off the end.

Measured on prod 2026-09-13, before the fix: 330 open tasks carry no deadline, 1,066 are
genuinely overdue, and **all 21 rows the dashboard query returned had no deadline at
all**. The overdue panel and every morning briefing were reporting zero actually-overdue
work, while the overflow flag truthfully said there was more.

The two paths are independent, which is how both came to be wrong; the predicate is now
built once in ``task_dashboard`` and imported by ``briefing``.

**Why this file exists.** ``test_briefing`` and ``test_wall_dashboard`` both subclass
``FrappeTestCase`` and need a bench, so neither runs in CI — these queries had no
automated coverage of any kind. This suite is bench-free and runs the predicate.

Run: python -m unittest erpnext_enhancements.tests.test_task_overdue_filters
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
DASHBOARD_PY = APP / "api/task_dashboard.py"
BRIEFING_PY = APP / "api/briefing.py"

TODAY = "2026-09-13"

#: What frappe substitutes for a NULL. Empty string sorts below any real datetime string,
#: which is what makes `<` match a NULL and `>` not — exactly like the framework.
NULL_FALLBACK = ""

dashboard = None


def setUpModule():
    global dashboard
    fake = types.ModuleType("frappe")
    fake._ = lambda text: text
    fake.whitelist = lambda *a, **k: (lambda fn: fn)
    fake.get_all = lambda *a, **k: []
    fake.db = types.SimpleNamespace(
        get_value=lambda *a, **k: None,
        get_single_value=lambda *a, **k: None,
        exists=lambda *a, **k: False,
        count=lambda *a, **k: 0,
    )
    fake.get_roles = lambda *a, **k: []
    fake.session = types.SimpleNamespace(user="tester@x")
    fake.throw = lambda *a, **k: (_ for _ in ()).throw(AssertionError("throw"))
    fake.log_error = lambda *a, **k: None
    fake.cache = lambda: types.SimpleNamespace(get_value=lambda *a, **k: None, set_value=lambda *a, **k: None)
    sys.modules["frappe"] = fake

    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda v: int(v or 0)
    utils.flt = lambda v, *a: float(v or 0)
    utils.nowdate = lambda: TODAY
    utils.today = lambda: TODAY
    utils.getdate = lambda d=None: d
    utils.date_diff = lambda a, b: 0
    utils.add_days = lambda d, n: d
    utils.now_datetime = lambda: datetime.datetime(2026, 9, 13, 6, 30)
    utils.fmt_money = lambda v, currency=None: str(v)
    utils.formatdate = lambda v=None, fmt=None: str(v)
    sys.modules["frappe.utils"] = utils
    fake.utils = utils

    # `task_dashboard` imports the query builder at module scope for an unrelated
    # Count() aggregate. Stubbing the package path is enough; nothing under test
    # touches it.
    qb = types.ModuleType("frappe.query_builder")
    functions = types.ModuleType("frappe.query_builder.functions")
    functions.Count = lambda *a, **k: None
    qb.functions = functions
    sys.modules["frappe.query_builder"] = qb
    sys.modules["frappe.query_builder.functions"] = functions
    fake.query_builder = qb

    from erpnext_enhancements.api import task_dashboard as _dashboard

    dashboard = _dashboard


# ------------------------------------------------------- a faithful filter engine


def _key(value):
    return NULL_FALLBACK if value is None else str(value)


def _matches(row, clauses):
    """Frappe's own semantics, including the ifnull fallback."""
    for field, operator, target in clauses:
        raw = row.get(field)
        value, goal = _key(raw), _key(target)
        if operator == "=":
            if raw != target:
                return False
        elif operator == "<":
            if not value < goal:
                return False
        elif operator == "<=":
            if not value <= goal:
                return False
        elif operator == ">=":
            if not value >= goal:
                return False
        elif operator == "not in":
            if raw in target:
                return False
        elif operator == "is":
            if target == "set" and not raw:
                return False
            if target == "not set" and raw:
                return False
        elif operator == "like":
            needle = str(target).strip("%")
            if needle not in (raw or ""):
                return False
        else:
            raise AssertionError(f"stub does not implement operator {operator!r}")
    return True


def _task(name, **overrides):
    row = {
        "name": name,
        "status": "Open",
        "exp_start_date": None,
        "exp_end_date": None,
        "_assign": '["tester@x"]',
    }
    row.update(overrides)
    return row


def _select(rows, clauses):
    return [r["name"] for r in rows if _matches(r, clauses)]


# --------------------------------------------------------------------- behaviour


class TestATaskWithNoDeadlineIsNotOverdue(unittest.TestCase):
    def setUp(self):
        self.rows = [
            _task("NO-DEADLINE"),
            _task("LATE", exp_end_date="2026-09-01"),
            _task("DUE-TODAY", exp_end_date=TODAY),
            _task("FUTURE", exp_end_date="2026-12-01"),
            _task("DONE-BUT-LATE", status="Completed", exp_end_date="2026-09-01"),
        ]

    def test_the_deadline_less_task_is_excluded(self):
        picked = _select(self.rows, dashboard.overdue_task_filters(TODAY))
        self.assertNotIn("NO-DEADLINE", picked)

    def test_a_genuinely_late_task_is_still_picked_up(self):
        """The vacuity guard. A filter narrowed until it matches nothing would pass the
        assertion above while reporting an empty overdue list for ever."""
        self.assertEqual(_select(self.rows, dashboard.overdue_task_filters(TODAY)), ["LATE"])

    def test_a_task_due_today_is_not_yet_late(self):
        self.assertNotIn("DUE-TODAY", _select(self.rows, dashboard.overdue_task_filters(TODAY)))

    def test_a_closed_task_is_never_overdue(self):
        self.assertNotIn("DONE-BUT-LATE", _select(self.rows, dashboard.overdue_task_filters(TODAY)))

    def test_the_page_is_no_longer_filled_by_deadline_less_rows(self):
        """The real damage, reproduced. NULLs sort FIRST in MariaDB ascending order, so
        with the sentinel matching them the whole page went to tasks that were not late
        and every genuinely overdue one fell off the end. Prod had 330 of the former and
        1,066 of the latter against a 20-row budget."""
        rows = [_task(f"NO-DEADLINE-{i}") for i in range(30)]
        rows.append(_task("LATE", exp_end_date="2026-09-01"))
        picked = _select(rows, dashboard.overdue_task_filters(TODAY))
        page = sorted(picked, key=lambda n: "" if n.startswith("NO-DEADLINE") else n)[:20]
        self.assertIn("LATE", page, "a real overdue task must survive the page budget")
        self.assertEqual(picked, ["LATE"])


class TestTodaysTasksNeedBothDates(unittest.TestCase):
    """Without `exp_start_date is set`, a task with no start date and a deadline weeks
    away satisfied `exp_start_date <= today` through the sentinel and was reported as
    work for today."""

    def setUp(self):
        self.rows = [
            _task("SPANS", exp_start_date="2026-09-01", exp_end_date="2026-09-30"),
            _task("NO-START-FUTURE-END", exp_end_date="2026-12-01"),
            _task("NO-START-ENDS-TODAY", exp_end_date=TODAY),
            _task("STARTS-TOMORROW", exp_start_date="2026-09-14", exp_end_date="2026-09-30"),
            _task("NOTHING"),
        ]

    def test_a_task_that_really_spans_today_is_picked_up(self):
        self.assertIn("SPANS", _select(self.rows, dashboard.spanning_task_filters(TODAY)))

    def test_a_task_with_no_start_date_is_not_todays_work(self):
        self.assertNotIn(
            "NO-START-FUTURE-END", _select(self.rows, dashboard.spanning_task_filters(TODAY))
        )

    def test_the_edges_pass_gets_its_job_back(self):
        """Both callers follow the spanning query with an explicit pass commented "only
        one of the two dates set". That pass could never match anything while the
        spanning query had already swallowed those rows -- on prod it was matching zero.
        Requiring both dates here is what makes it reachable again."""
        picked = _select(self.rows, dashboard.spanning_task_filters(TODAY))
        self.assertNotIn("NO-START-ENDS-TODAY", picked)
        edges = [
            ["status", "not in", dashboard.CLOSED_TASK_STATUSES],
            ["exp_end_date", "=", TODAY],
            ["exp_start_date", "is", "not set"],
        ]
        self.assertEqual(_select(self.rows, edges), ["NO-START-ENDS-TODAY"])

    def test_a_task_starting_tomorrow_is_not_todays_work(self):
        self.assertNotIn("STARTS-TOMORROW", _select(self.rows, dashboard.spanning_task_filters(TODAY)))


class TestTheBriefingAddsItsAssigneeClause(unittest.TestCase):
    def test_extra_clauses_are_appended_not_replaced(self):
        rows = [
            _task("MINE", exp_end_date="2026-09-01"),
            _task("THEIRS", exp_end_date="2026-09-01", _assign='["someone.else@x"]'),
        ]
        clauses = dashboard.overdue_task_filters(TODAY, [["_assign", "like", '%"tester@x"%']])
        self.assertEqual(_select(rows, clauses), ["MINE"])

    def test_the_base_clauses_survive_the_extra(self):
        """An `extra` that replaced rather than appended would drop the `is set` guard
        and quietly reinstate the bug on the briefing only."""
        clauses = dashboard.overdue_task_filters(TODAY, [["_assign", "like", "%x%"]])
        self.assertIn(["exp_end_date", "is", "set"], clauses)


class TestTheStubModelsTheFrameworkRatherThanIntuition(unittest.TestCase):
    """Guards the guard: every assertion above depends on the engine reproducing the
    ifnull fallback. The Python-obvious answer is the opposite of production."""

    def test_a_null_matches_a_less_than(self):
        self.assertTrue(_matches({"exp_end_date": None}, [["exp_end_date", "<", TODAY]]))

    def test_a_null_does_not_match_a_greater_or_equal(self):
        self.assertFalse(_matches({"exp_end_date": None}, [["exp_end_date", ">=", TODAY]]))

    def test_is_set_is_exact(self):
        self.assertFalse(_matches({"exp_end_date": None}, [["exp_end_date", "is", "set"]]))
        self.assertTrue(_matches({"exp_end_date": TODAY}, [["exp_end_date", "is", "set"]]))


# --------------------------------------------------------------------- structure


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


class TestBothPathsUseTheSharedPredicate(unittest.TestCase):
    """The two paths were independent, and that is precisely how both came to be wrong.
    A future edit inlining a dict again would revive the bug in one of them only."""

    def test_neither_module_builds_the_filter_by_hand(self):
        """Both filter FORMS are banned, and that matters: the first version of this
        test only looked for the dict spelling, so a mutation that hand-rolled the same
        broken filter as a LIST sailed straight past it. A guard against duplication has
        to cover every way the thing can be spelled."""
        for path in (DASHBOARD_PY, BRIEFING_PY):
            code = _code(path)
            for field in ("exp_end_date", "exp_start_date"):
                for banned in (
                    f'"{field}": ("<"',
                    f'"{field}": ("<="',
                    f'"{field}": ["<"',
                    f'"{field}": ["<="',
                ):
                    with self.subTest(where=path.name, token=banned):
                        self.assertNotIn(banned, code)

    def test_the_comparison_lives_only_inside_the_builders(self):
        """List-form spelling, scoped. Each comparison must appear exactly once in the
        whole app -- in the builder that guards it -- so no caller can assemble its own.

        Scoped to the COMPARISON rather than to the field name, because both callers
        legitimately name these fields afterwards in their edges pass, with `=` and
        `is not set`. Those two operators are safe on a nullable column; banning the
        field outright would have flagged correct code."""
        for field, operator in (
            ("exp_end_date", "<"),
            ("exp_start_date", "<="),
        ):
            with self.subTest(field=field):
                token = f'["{field}", "{operator}", '
                self.assertEqual(
                    _code(DASHBOARD_PY).count(token), 1, f"{token} should occur once"
                )
                self.assertNotIn(token, _code(BRIEFING_PY))


    def test_the_briefing_imports_the_builders_rather_than_copying_them(self):
        code = _code(BRIEFING_PY)
        self.assertIn("overdue_task_filters", code)
        self.assertIn("spanning_task_filters", code)
        self.assertNotIn("def overdue_task_filters", code)

    def test_each_builder_carries_its_is_set_clause(self):
        code = _code(DASHBOARD_PY)
        self.assertIn('["exp_end_date", "is", "set"]', code)
        self.assertIn('["exp_start_date", "is", "set"]', code)

    def test_it_is_wired_into_ci(self):
        """test_briefing and test_wall_dashboard both need a bench, which is why these
        queries had no coverage. A bench-free suite that ci.yml never names has none
        either."""
        ci = (APP.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("test_task_overdue_filters", ci)


if __name__ == "__main__":
    unittest.main()
