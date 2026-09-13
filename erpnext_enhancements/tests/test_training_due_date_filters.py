# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""An optional course carries no due date on purpose, and three jobs chased it anyway.

``TrainingAssignment._default_due_date`` says why the field is blank, and says what
happens if it is not::

    "Only Required courses carry a due date. Putting one on an optional library course
     would make it overdue and start chasing people over something nobody asked them
     to do."

Three sweeps did exactly that without ever putting a date on anything. ``due_date`` is
nullable, and frappe wraps a comparison on a nullable column in an ifnull sentinel set to
the MINIMUM of the type, so ``ifnull(due_date, '0001-01-01') < today`` is true for every
optional-course assignment:

* ``training.tasks.refresh_overdue_status`` wrote them to ``Overdue``;
* ``training.tasks.send_due_reminders`` put them in the learner's nightly digest;
* ``api.hr_dashboard.get_training_compliance`` counted them as non-compliance.

**The controller had it right all along**, which is the part worth remembering.
``_derive_overdue`` guards the same rule with ``if not self.due_date: return`` — in
Python, where NULL behaves the way everyone expects. So saving an assignment left it Not
Started and the nightly sweep flipped the same row to Overdue: two halves of one rule
disagreeing because only one of them went through SQL.

**Latent rather than live, and measured as such.** On 2026-09-13 every one of the 26
assignments on prod carried a due date, because all of them are Required-course rows. But
three Optional courses are Published with zero assignments, so the first self-enrolment
would have armed all three sweeps at once.

Run: python -m unittest erpnext_enhancements.tests.test_training_due_date_filters
"""

import ast
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
ASSIGNMENT_PY = APP / "training/doctype/training_assignment/training_assignment.py"
TASKS_PY = APP / "training/tasks.py"
HR_DASHBOARD_PY = APP / "api/hr_dashboard.py"

TODAY = "2026-09-13"
NULL_FALLBACK = ""

assignment = None


def setUpModule():
    global assignment
    fake = types.ModuleType("frappe")
    fake._ = lambda text: text
    fake.whitelist = lambda *a, **k: (lambda fn: fn)
    fake.get_all = lambda *a, **k: []
    fake.db = types.SimpleNamespace(
        get_value=lambda *a, **k: None, get_single_value=lambda *a, **k: None, exists=lambda *a, **k: False
    )
    fake.throw = lambda *a, **k: (_ for _ in ()).throw(AssertionError("throw"))
    sys.modules["frappe"] = fake

    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda v: int(v or 0)
    utils.nowdate = lambda: TODAY
    utils.today = lambda: TODAY
    utils.getdate = lambda d=None: str(d)[:10] if d else None
    utils.add_days = lambda d, n: d
    sys.modules["frappe.utils"] = utils
    fake.utils = utils

    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = type("Document", (), {})
    sys.modules["frappe.model"] = model
    sys.modules["frappe.model.document"] = document

    from erpnext_enhancements.training.doctype.training_assignment import (
        training_assignment as _assignment,
    )

    assignment = _assignment


# ------------------------------------------------------- a faithful filter engine


def _key(value):
    return NULL_FALLBACK if value is None else str(value)


def _matches(row, clauses):
    for field, operator, target in clauses:
        raw = row.get(field)
        value, goal = _key(raw), _key(target)
        if operator == "in":
            if raw not in target:
                return False
        elif operator == "<":
            if not value < goal:
                return False
        elif operator == "<=":
            if not value <= goal:
                return False
        elif operator == "is":
            if target == "set" and not raw:
                return False
            if target == "not set" and raw:
                return False
        else:
            raise AssertionError(f"stub does not implement operator {operator!r}")
    return True


def _row(name, **overrides):
    row = {"name": name, "status": "Not Started", "due_date": None}
    row.update(overrides)
    return row


def _select(rows, clauses):
    return [r["name"] for r in rows if _matches(r, clauses)]


# --------------------------------------------------------------------- behaviour


class TestAnOptionalCourseIsNeverChased(unittest.TestCase):
    def setUp(self):
        self.rows = [
            _row("OPTIONAL"),  # a library course: no due date, by design
            _row("LATE", due_date="2026-09-01"),
            _row("DUE-TODAY", due_date=TODAY),
            _row("SOON", due_date="2026-09-15"),
            _row("DONE", status="Completed", due_date="2026-09-01"),
        ]

    def test_the_overdue_sweep_leaves_it_alone(self):
        picked = _select(
            self.rows, assignment.due_date_filters("<", TODAY, ("Not Started", "In Progress"))
        )
        self.assertNotIn("OPTIONAL", picked)

    def test_the_overdue_sweep_still_catches_a_real_one(self):
        """Vacuity guard: a filter narrowed until it matches nothing would pass the
        assertion above while silently ending overdue tracking altogether."""
        picked = _select(
            self.rows, assignment.due_date_filters("<", TODAY, ("Not Started", "In Progress"))
        )
        self.assertEqual(picked, ["LATE"])

    def test_the_reminder_digest_leaves_it_alone(self):
        picked = _select(self.rows, assignment.due_date_filters("<=", "2026-09-16"))
        self.assertNotIn("OPTIONAL", picked)

    def test_the_reminder_digest_still_nudges_what_is_due(self):
        picked = _select(self.rows, assignment.due_date_filters("<=", "2026-09-16"))
        self.assertEqual(sorted(picked), ["DUE-TODAY", "LATE", "SOON"])

    def test_the_compliance_widget_leaves_it_alone(self):
        picked = _select(self.rows, assignment.due_date_filters("<=", "2026-09-27"))
        self.assertNotIn("OPTIONAL", picked)

    def test_a_closed_assignment_is_never_selected(self):
        self.assertNotIn("DONE", _select(self.rows, assignment.due_date_filters("<=", "2026-12-01")))

    def test_the_overdue_sweep_uses_the_narrow_status_set(self):
        """Only the two states that can BECOME overdue. Awaiting Sign-off is waiting on
        somebody else, and an already-Overdue row needs no rewriting."""
        rows = [
            _row("AWAITING", status="Awaiting Sign-off", due_date="2026-09-01"),
            _row("ALREADY", status="Overdue", due_date="2026-09-01"),
            _row("LATE", due_date="2026-09-01"),
        ]
        picked = _select(rows, assignment.due_date_filters("<", TODAY, ("Not Started", "In Progress")))
        self.assertEqual(picked, ["LATE"])


class TestTheSweepNoLongerFightsTheController(unittest.TestCase):
    """`_derive_overdue` refuses to call a dateless assignment overdue. The sweep used
    to disagree on the very same row, so saving it said Not Started and that night's job
    said Overdue."""

    def _derive(self, **values):
        fields = {"status": "Not Started", "due_date": None}
        fields.update(values)
        doc = types.SimpleNamespace(**fields)
        assignment.TrainingAssignment._derive_overdue(doc)
        return doc.status

    def test_the_controller_leaves_a_dateless_assignment_alone(self):
        self.assertEqual(self._derive(due_date=None), "Not Started")

    def test_the_controller_marks_a_real_one_overdue(self):
        self.assertEqual(self._derive(due_date="2026-09-01"), "Overdue")

    def test_both_halves_now_agree_on_every_row(self):
        """The actual claim: for each row, 'the controller would call this overdue' and
        'the sweep would select this row' are the same answer."""
        rows = [
            _row("OPTIONAL"),
            _row("LATE", due_date="2026-09-01"),
            _row("FUTURE", due_date="2026-12-01"),
        ]
        swept = set(
            _select(rows, assignment.due_date_filters("<", TODAY, ("Not Started", "In Progress")))
        )
        for row in rows:
            with self.subTest(row=row["name"]):
                controller_says = self._derive(due_date=row["due_date"]) == "Overdue"
                self.assertEqual(controller_says, row["name"] in swept)


class TestTheStubModelsTheFrameworkRatherThanIntuition(unittest.TestCase):
    def test_a_null_matches_a_less_than(self):
        self.assertTrue(_matches({"due_date": None}, [["due_date", "<", TODAY]]))

    def test_a_null_matches_a_less_or_equal(self):
        self.assertTrue(_matches({"due_date": None}, [["due_date", "<=", TODAY]]))

    def test_is_set_is_exact(self):
        self.assertFalse(_matches({"due_date": None}, [["due_date", "is", "set"]]))
        self.assertTrue(_matches({"due_date": TODAY}, [["due_date", "is", "set"]]))


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


class TestAllThreeCallersUseTheSharedBuilder(unittest.TestCase):
    """Three independent copies of one rule is how all three came to be wrong."""

    def test_no_caller_builds_a_due_date_comparison_by_hand(self):
        """Both filter forms, because a guard that covered only the dict spelling let a
        hand-rolled LIST straight through when this was mutation-tested elsewhere."""
        for path in (TASKS_PY, HR_DASHBOARD_PY):
            code = _code(path)
            for banned in (
                '"due_date": ["<"',
                '"due_date": ["<="',
                '"due_date": ("<"',
                '"due_date": ("<="',
                '["due_date", "<",',
                '["due_date", "<=",',
            ):
                with self.subTest(where=path.name, token=banned):
                    self.assertNotIn(banned, code)

    def test_the_comparison_exists_once_in_the_whole_app(self):
        code = _code(ASSIGNMENT_PY)
        self.assertEqual(code.count('["due_date", operator, cutoff]'), 1)
        self.assertIn('["due_date", "is", "set"]', code)

    def test_every_caller_imports_it(self):
        for path in (TASKS_PY, HR_DASHBOARD_PY):
            with self.subTest(where=path.name):
                code = _code(path)
                self.assertIn("due_date_filters", code)
                self.assertNotIn("def due_date_filters", code)

    def test_the_builder_sits_with_the_rule_it_enforces(self):
        """`_default_due_date` is why the column is nullable; the filter that has to
        respect that belongs in the same file, not in three jobs downstream."""
        code = _code(ASSIGNMENT_PY)
        self.assertIn("def due_date_filters", code)
        self.assertIn("def _default_due_date", code)

    def test_it_is_wired_into_ci(self):
        ci = (APP.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("test_training_due_date_filters", ci)


if __name__ == "__main__":
    unittest.main()
