# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One guard for the whole class: a nullable date compared with `<` must carry `is set`.

Frappe wraps a comparison on a nullable column in an ifnull sentinel set to the MINIMUM of
the type (``frappe/model/db_query.py``, ``prepare_filter_condition``)::

    ifnull(`expires_on`, '0001-01-01') < '2026-09-13'

So ``<`` and ``<=`` MATCH every NULL row, while ``>`` and ``>=`` do not, ``between``
disables the sentinel outright, and ``is set`` / ``is not set`` are exact. **It fails by
doing more than asked**, which is why every instance below looked like it was working.

--------------------------------------------------------------------------------------
What this cost, in one day
--------------------------------------------------------------------------------------

An audit on 2026-09-13 found a dozen instances across the app. Five were live:

| Where | A blank value means | What happened |
|---|---|---|
| `training/certificates.py` | never expires | 2 completions + 2 certificates marked Expired, 1 spurious retake |
| `api/maintenance_renewal.py` | "Blank never expires" | **15 of 16 contracts force-expired**, stopping visits *and* invoicing |
| `api/task_dashboard.py` | no deadline | overdue panel showed **0 of 1,066** genuinely overdue tasks |
| `api/briefing.py` | no deadline | the same, in every morning email |
| `status_alerts.py` | won-date unknown | daily alert reported **228** unconverted wins; the real figure is 31 |

In three of those the correct behaviour was already **written down and contradicted by the
SQL**: `TrainingCertificate._derive_status` guards `if self.expires_on`, the contract field
description says *"Blank never expires"*, and `TrainingAssignment._derive_overdue` guards
`if not self.due_date`. The rule was never in doubt; only the queries were.

--------------------------------------------------------------------------------------
Why this is keyed on (doctype, field) and not on the field name
--------------------------------------------------------------------------------------

The first version of this file keyed on the name alone and immediately flagged three
correct filters in ``travel_management``, because ``Travel Trip.end_date`` is ``reqd = 1``
while ``Sapphire Maintenance Contract.end_date`` is nullable and load-bearing. **A guard
that cries wolf gets switched off**, so the doctype is read out of the ORM call itself and
only the pairs listed below are policed.

The list is the memory: add a pair the moment another nullable date turns up, and say what
a blank one MEANS — the meaning is the whole reason the guard is needed.

--------------------------------------------------------------------------------------
What it cannot see
--------------------------------------------------------------------------------------

A shared builder has no doctype at the point the filter is written, so ORM-call scanning
cannot reach one. Those are listed explicitly in ``FILTER_BUILDERS`` instead. Anything
that assembles filters some third way is outside this net — which is the honest limit of
a static check, and the reason the behavioural suites next to each fix exist too.

Bench-free: filesystem and `ast` only.

Run: python -m unittest erpnext_enhancements.tests.test_nullable_date_filters
"""

import ast
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

#: (doctype, field) -> what a blank value MEANS. Every pair verified against its DocType
#: JSON or its fixtures/custom_field.json entry: nullable, no default, NULL meaningful.
NULLABLE_DATE_FIELDS = {
    ("Training Completion", "expires_on"): "never expires — the course sets no validity period",
    ("Training Certificate", "expires_on"): "never expires — no certificate_valid_months on the course",
    ("Employee Credential", "expires_on"): "the ticket does not expire",
    ("Triton Chat Attachment", "expires_on"): "no retention clock; and this sweep DELETES",
    ("Sapphire Maintenance Contract", "end_date"): "never expires — the field description says exactly this",
    ("Sapphire Maintenance Contract", "next_billing_date"): "not yet on recurring billing",
    ("Sapphire Maintenance Contract", "rate_effective_date"): "no rate change scheduled yet",
    ("Task", "exp_end_date"): "no deadline — a task with none is not late",
    ("Task", "exp_start_date"): "no start date — the window does not contain today",
    ("Training Assignment", "due_date"): "an optional course carries no deadline, by design",
    ("Opportunity", "custom_date_closed_won"): "won-date unknown; the Zoho imports all landed without one",
    ("Sales Order Item", "custom_next_predictive_visit"): "no visit has ever been scheduled",
    ("ToDo", "date"): "no date — the Desk default fires on new_doc, not on every write path",
}

#: Shared filter builders: function -> the field it must guard. An ORM call that takes its
#: filters from one of these carries no doctype, so it cannot be checked at the call site.
FILTER_BUILDERS = {
    ("training/certificates.py", "_lapsed_filters"): "expires_on",
    ("api/task_dashboard.py", "overdue_task_filters"): "exp_end_date",
    ("api/task_dashboard.py", "spanning_task_filters"): "exp_start_date",
    (
        "training/doctype/training_assignment/training_assignment.py",
        "due_date_filters",
    ): "due_date",
}

#: Only the DatabaseQuery entry points. **The wrap is a property of the API, not of the
#: filter** — the same filter dict gets different SQL, and different answers, depending on
#: which call carries it. Measured on prod 2026-09-13 against `Employee.relieving_date`
#: (15 of 20 rows NULL), asking `<= '0002-01-01'` so that ONLY the sentinel could match:
#:
#:     frappe.get_all / db.get_all / db.get_list   ->  15 rows   wrapped
#:     frappe.db.count                             ->   0        no wrap
#:     frappe.db.get_value                         ->   None     no wrap
#:     frappe.db.exists                            ->   False    no wrap
#:
#: DatabaseQuery calls `prepare_filter_condition`, which builds the ifnull; the Query
#: Builder path the others take never does. So `count`, `get_value`, `exists` and `delete`
#: are **correct without a guard**, and the first version of this file scanned them —
#: latent false positives, of exactly the kind the (doctype, field) keying exists to
#: avoid. Nothing was failing, which is why it survived review: a guard only cries wolf
#: once somebody writes the call it would flag.
#:
#: This cost real time in the other direction too. Verifying the v1.426.5 deploy, the
#: 228-vs-31 alert bug was first "reproduced" with `frappe.db.count` and came back 31 =
#: 31, reading as no bug at all. Reproduce a DatabaseQuery bug with a DatabaseQuery call.
ORM_FUNCTIONS = {"get_all", "get_list"}
SKIP_DIRS = {"tests", "node_modules", "__pycache__"}
UNSAFE_OPERATORS = ("<", "<=")


def _module_constants(tree):
    """Module-level ``NAME = "literal"`` — so a call like ``get_all(DOCTYPE, ...)`` can
    still be resolved to its doctype. `triton_attachments.py` writes them that way."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        out[target.id] = node.value.value
    return out


def _doctype_of(call, constants):
    if not call.args:
        for kw in call.keywords:
            if kw.arg == "doctype":
                node = kw.value
                break
        else:
            return None
    else:
        node = call.args[0]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _filters_of(call):
    for kw in call.keywords:
        if kw.arg == "filters":
            return ast.unparse(kw.value).replace("'", '"')
    if len(call.args) > 1:
        return ast.unparse(call.args[1]).replace("'", '"')
    return None


def _compares(code, field):
    """Does this filter source compare `field` with an unsafe operator, in any form?"""
    for operator in UNSAFE_OPERATORS:
        for shape in (
            f'"{field}": ("{operator}"',
            f'"{field}": ["{operator}"',
            f'["{field}", "{operator}",',
            f'("{field}", "{operator}",',
        ):
            if shape in code:
                return operator
    # a builder parameterises the operator: ["due_date", operator, cutoff]
    if f'["{field}", operator,' in code:
        return "<param>"
    return None


def _guards(code, field):
    return (
        f'["{field}", "is", "set"]' in code
        or f'"{field}": ("is", "set")' in code
        or f'"{field}": ["is", "set"]' in code
    )


def _shipped_files():
    for path in sorted(APP.rglob("*.py")):
        if SKIP_DIRS & set(path.parts):
            continue
        yield path


def _orm_filters():
    """(path, doctype, filter source) for every ORM call whose doctype is resolvable."""
    for path in _shipped_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if name not in ORM_FUNCTIONS:
                continue
            doctype = _doctype_of(node, constants)
            filters = _filters_of(node)
            if doctype and filters:
                yield path, doctype, filters


def _function_source(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return ast.unparse(node).replace("'", '"')
    return None


class TestEveryNullableDateComparisonIsGuarded(unittest.TestCase):
    def test_no_unguarded_comparison_in_any_orm_call(self):
        """The whole point. Every live instance found on 2026-09-13 fails here."""
        offenders = []
        for path, doctype, filters in _orm_filters():
            for (declared, field), meaning in NULLABLE_DATE_FIELDS.items():
                if declared != doctype:
                    continue
                operator = _compares(filters, field)
                if operator and not _guards(filters, field):
                    offenders.append(
                        f"{path.relative_to(APP)} — {doctype}.{field} {operator} with no "
                        f"`is set` (blank means: {meaning})"
                    )
        self.assertEqual(
            offenders, [], "unguarded nullable-date comparison:\n  " + "\n  ".join(offenders)
        )

    def test_every_shared_builder_guards_its_field(self):
        """A builder has no doctype at the call site, so it is named explicitly."""
        for (relative, function), field in FILTER_BUILDERS.items():
            with self.subTest(builder=f"{relative}:{function}"):
                source = _function_source(APP / relative, function)
                self.assertIsNotNone(source, f"{function} not found in {relative}")
                self.assertTrue(
                    _guards(source, field), f"{function} must carry an `is set` on {field}"
                )


class TestTheGuardCannotPassVacuously(unittest.TestCase):
    """An absence assertion over a walk. If the walk found nothing, or the matchers
    stopped matching, it would report clean on a repo full of the bug."""

    def test_the_walk_reads_a_lot_of_orm_calls(self):
        found = list(_orm_filters())
        # 603 after the v1.426.6 narrowing, so the floor has room and still fails loudly
        # if the matchers stop matching.
        self.assertGreater(len(found), 400, "the ORM-call walk is not finding calls")

    def test_it_resolves_a_doctype_held_in_a_module_constant(self):
        """`triton_attachments.py` calls get_all(DOCTYPE, ...). Without constant
        resolution its deleting sweep would never be checked."""
        doctypes = {dt for _p, dt, _f in _orm_filters()}
        self.assertIn("Triton Chat Attachment", doctypes)

    def test_it_finds_the_guarded_comparisons_that_do_exist(self):
        """Fixes shipped across v1.425.0-v1.426.5; if none were visible to the matchers,
        the sweep above is inspecting nothing that matters."""
        guarded = set()
        for _path, doctype, filters in _orm_filters():
            for declared, field in NULLABLE_DATE_FIELDS:
                if declared == doctype and _compares(filters, field) and _guards(filters, field):
                    guarded.add(field)
        for expected in ("end_date", "next_billing_date", "custom_date_closed_won"):
            with self.subTest(field=expected):
                self.assertIn(expected, guarded)

    def test_it_would_catch_an_unguarded_dict_filter(self):
        code = '{"due_date": ("<=", horizon)}'
        self.assertEqual(_compares(code, "due_date"), "<=")
        self.assertFalse(_guards(code, "due_date"))


class TestTheScanIsNarrowedOnPurpose(unittest.TestCase):
    """The scan covers DatabaseQuery and nothing else, and that is a measurement.

    Measured on prod 2026-09-13 on `Employee.relieving_date`, 15 of 20 rows NULL, with
    `<= '0002-01-01'` so that only the ifnull sentinel could produce a match::

        frappe.get_all / db.get_all / db.get_list   ->  15      wrapped
        frappe.db.count                             ->   0      no wrap
        frappe.db.get_value                         ->   None   no wrap
        frappe.db.exists                            ->   False  no wrap

    Widening this set back is the tempting move — it reads as "being thorough" — and it
    is what the first version of this file did. It puts false positives in front of the
    next reader, on calls that are already correct.
    """

    def test_only_the_databasequery_entry_points_are_scanned(self):
        self.assertEqual(ORM_FUNCTIONS, {"get_all", "get_list"})

    def test_the_query_builder_apis_are_excluded_deliberately(self):
        for api in ("count", "get_value", "exists", "delete"):
            with self.subTest(api=api):
                self.assertNotIn(api, ORM_FUNCTIONS)

    def test_narrowing_did_not_gut_the_doctype_coverage(self):
        """If the two remaining names had been wrong, this collapses rather than passing
        quietly on a handful of calls."""
        doctypes = {doctype for _p, doctype, _f in _orm_filters()}
        self.assertGreater(len(doctypes), 100)
        for policed in {declared for declared, _field in NULLABLE_DATE_FIELDS}:
            with self.subTest(doctype=policed):
                self.assertIn(policed, doctypes)

    def test_it_would_catch_an_unguarded_list_filter(self):
        """The spelling that slipped past a narrower guard during mutation testing."""
        code = '[["due_date", "<", today]]'
        self.assertEqual(_compares(code, "due_date"), "<")
        self.assertFalse(_guards(code, "due_date"))

    def test_it_sees_a_builders_parameterised_operator(self):
        """`["due_date", operator, cutoff]` has no literal to match on, so a builder
        that dropped its guard would otherwise be invisible."""
        self.assertEqual(_compares('[["due_date", operator, cutoff]]', "due_date"), "<param>")

    def test_it_accepts_a_properly_guarded_filter(self):
        code = '[["due_date", "is", "set"], ["due_date", "<", today]]'
        self.assertTrue(_guards(code, "due_date"))

    def test_it_does_not_flag_the_safe_operators(self):
        for safe in (
            '{"expires_on": (">", today)}',
            '{"expires_on": (">=", today)}',
            '{"expires_on": ("between", (a, b))}',
            '{"expires_on": ("is", "not set")}',
        ):
            with self.subTest(code=safe):
                self.assertIsNone(_compares(safe, "expires_on"))

    def test_it_does_not_flag_the_same_field_on_a_doctype_where_it_is_required(self):
        """`Travel Trip.end_date` is reqd=1. The name-keyed first draft flagged three
        correct filters there, and a guard that cries wolf gets switched off."""
        self.assertNotIn(("Travel Trip", "end_date"), NULLABLE_DATE_FIELDS)
        trip_filters = [f for _p, dt, f in _orm_filters() if dt == "Travel Trip"]
        self.assertTrue(trip_filters, "expected Travel Trip queries to exist")
        self.assertTrue(
            any(_compares(f, "end_date") for f in trip_filters),
            "expected at least one unguarded Travel Trip end_date comparison to exist, "
            "so this test proves the doctype key is what spares it",
        )


class TestTheListIsTheMemory(unittest.TestCase):
    def test_every_pair_says_what_a_blank_one_means(self):
        for pair, meaning in NULLABLE_DATE_FIELDS.items():
            with self.subTest(pair=pair):
                self.assertGreater(len(meaning), 20, f"{pair} needs a real explanation")

    def test_the_five_that_were_live_are_all_covered(self):
        for pair in (
            ("Training Completion", "expires_on"),
            ("Sapphire Maintenance Contract", "end_date"),
            ("Task", "exp_end_date"),
            ("Training Assignment", "due_date"),
            ("Opportunity", "custom_date_closed_won"),
        ):
            with self.subTest(pair=pair):
                self.assertIn(pair, NULLABLE_DATE_FIELDS)

    def test_it_is_wired_into_ci(self):
        ci = (APP.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("test_nullable_date_filters", ci)


if __name__ == "__main__":
    unittest.main()
