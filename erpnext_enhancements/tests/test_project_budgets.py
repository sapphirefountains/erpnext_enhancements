"""Bench-free tests for project budgets and reallocations (WI-075 sub-phase M).

Every failure guarded here is a **wrong number that looks right**, which is the only kind this
part of the system produces. A budget does not crash; it reports.

* **A zero that means "nobody records this" read as "nothing was spent."** Measured on prod
  2026-09-14: `tabTimesheet` holds 0 rows and no Purchase Invoice has submitted lines, so a naked
  `actual` column would read $0.00 on every line of every project, forever, and look correct.
  That is the trailing-space failure in another costume, and the coverage verdict is the whole
  defence against it.
* **A percentage of a zero budget.** Rendered as 0% it says "on budget"; rendered as a huge
  number it says "catastrophically over". Both are confident and both are wrong; the answer is
  that there isn't one.
* **A reallocation that is not net zero.** The project total is the sum of the lines, so a move
  that changed it would silently rewrite the denominator WI-058 will eventually divide by.
* **A second approval from the same hand.** A protected-category rule one person can satisfy by
  clicking twice is a control in appearance only.
* **Purchase spend that names no category.** Folding it into a category invents an attribution
  nobody made; dropping it under-reports the job silently, in the direction that looks clean.

Run: python -m unittest erpnext_enhancements.tests.test_project_budgets
"""

import json
import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.quality import budgets

DOCTYPE_ROOT = APP_ROOT / "project_enhancements" / "doctype"
CATEGORY_DIR = DOCTYPE_ROOT / "project_budget_category"
LINE_DIR = DOCTYPE_ROOT / "project_budget_line"
REALLOCATION_DIR = DOCTYPE_ROOT / "budget_reallocation"
ROLLUP = APP_ROOT / "project_enhancements" / "budget_rollup.py"
API = APP_ROOT / "api" / "project_budget.py"
PATCH = APP_ROOT / "patches" / "seed_budget_categories.py"
CUSTOM_FIELDS = APP_ROOT / "fixtures" / "custom_field.json"


def _raw(path):
    return path.read_text(encoding="utf-8")


def _code_only(path):
    """Source with docstrings and comments removed.

    A test asserting a token is ABSENT must strip these first: the comment explaining why the
    token is absent names the token, so the assertion fires on its own explanation. Never use
    this on a file whose triple-quoted strings are load-bearing — `budget_rollup.py` holds its
    SQL that way, and stripping would delete the very queries under test.
    """
    src = _raw(path)
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    src = re.sub(r"(?m)^\s*#.*$", "", src)
    return src


def _no_comments(path):
    """Source with `#` comments removed but every string literal intact.

    The middle ground the two other readers leave out. An absence assertion needs the comments
    gone — the comment explaining why a token is absent names that token, which is how this
    caught itself below — but `budget_rollup.py` keeps its SQL in triple-quoted strings, so the
    full docstring stripper would delete the queries other tests here check.
    """
    return re.sub(r"(?m)^\s*#.*$", "", _raw(path))


def line(category, amount):
    return {"category": category, "budgeted_amount": amount}


# --------------------------------------------------------------------------------------------
# Categories and protection
# --------------------------------------------------------------------------------------------


class TestCategories(unittest.TestCase):
    def test_seven_categories_are_seeded(self):
        self.assertEqual(len(budgets.SEED_CATEGORIES), 7)
        self.assertEqual(len(budgets.seed_rows()), 7)

    def test_exactly_three_categories_are_protected(self):
        self.assertEqual(
            set(budgets.PROTECTED),
            {
                budgets.CATEGORY_GENERAL_CONDITIONS,
                budgets.CATEGORY_CONTINGENCY,
                budgets.CATEGORY_FEE,
            },
        )

    def test_the_seed_and_the_rule_cannot_drift(self):
        """Every category the code protects is seeded protected, and vice versa."""
        seeded_protected = {
            row["category_name"] for row in budgets.seed_rows() if row["is_protected"]
        }
        self.assertEqual(seeded_protected, set(budgets.PROTECTED))

    def test_is_protected(self):
        self.assertTrue(budgets.is_protected(budgets.CATEGORY_CONTINGENCY))
        self.assertTrue(budgets.is_protected(budgets.CATEGORY_FEE))
        self.assertTrue(budgets.is_protected(budgets.CATEGORY_GENERAL_CONDITIONS))
        self.assertFalse(budgets.is_protected(budgets.CATEGORY_MATERIALS))
        self.assertFalse(budgets.is_protected(None))
        self.assertFalse(budgets.is_protected(""))

    def test_contingency_and_fee_declare_no_spend_source(self):
        """Money leaves them by reallocation, never by purchase.

        A spend column against a reserve is a category error rather than a gap, and declaring a
        source for one would produce a permanent, meaningless zero that reads as a fact.
        """
        rows = {row["category_name"]: row for row in budgets.seed_rows()}
        for name in (budgets.CATEGORY_CONTINGENCY, budgets.CATEGORY_FEE):
            self.assertEqual(rows[name]["committed_source"], budgets.SOURCE_NONE, name)
            self.assertEqual(rows[name]["actual_source"], budgets.SOURCE_NONE, name)

    def test_labor_declares_timesheets_and_no_committed_source(self):
        """There is no purchase instrument that commits own-crew labour."""
        labor = {row["category_name"]: row for row in budgets.seed_rows()}[budgets.CATEGORY_LABOR]
        self.assertEqual(labor["actual_source"], budgets.SOURCE_TIMESHEETS)
        self.assertEqual(labor["committed_source"], budgets.SOURCE_NONE)

    def test_every_declared_source_is_a_known_source(self):
        for row in budgets.seed_rows():
            self.assertIn(row["committed_source"], budgets.SOURCES, row["category_name"])
            self.assertIn(row["actual_source"], budgets.SOURCES, row["category_name"])

    def test_every_real_source_names_a_doctype(self):
        for source in budgets.SOURCES:
            if source == budgets.SOURCE_NONE:
                self.assertIsNone(budgets.source_doctype(source))
            else:
                self.assertTrue(budgets.source_doctype(source), source)


# --------------------------------------------------------------------------------------------
# Coverage — the load-bearing decision
# --------------------------------------------------------------------------------------------


class TestCoverage(unittest.TestCase):
    def test_no_declared_source_is_no_source(self):
        self.assertEqual(budgets.coverage(budgets.SOURCE_NONE, True), budgets.COVERAGE_NO_SOURCE)
        self.assertEqual(budgets.coverage(None, True), budgets.COVERAGE_NO_SOURCE)
        self.assertEqual(budgets.coverage("", True), budgets.COVERAGE_NO_SOURCE)

    def test_a_source_holding_no_rows_anywhere_is_not_tracked(self):
        """The Labour case on this site today: the category points at Timesheets, and
        `tabTimesheet` is empty."""
        self.assertEqual(
            budgets.coverage(budgets.SOURCE_TIMESHEETS, False), budgets.COVERAGE_NOT_TRACKED
        )

    def test_a_source_in_use_is_tracked(self):
        self.assertEqual(
            budgets.coverage(budgets.SOURCE_PURCHASE_ORDERS, True), budgets.COVERAGE_TRACKED
        )

    def test_only_a_tracked_figure_may_be_read_as_money(self):
        """This is the guard. A zero carrying either other verdict is about the instrument, not
        about the project, and a caller that totals or charts spend must skip it rather than add
        its zero."""
        self.assertTrue(budgets.spend_is_meaningful(budgets.COVERAGE_TRACKED))
        self.assertFalse(budgets.spend_is_meaningful(budgets.COVERAGE_NOT_TRACKED))
        self.assertFalse(budgets.spend_is_meaningful(budgets.COVERAGE_NO_SOURCE))
        self.assertFalse(budgets.spend_is_meaningful(None))
        self.assertFalse(budgets.spend_is_meaningful(""))

    def test_the_three_verdicts_are_distinct(self):
        self.assertEqual(len(set(budgets.COVERAGE_STATES)), 3)


# --------------------------------------------------------------------------------------------
# Variance
# --------------------------------------------------------------------------------------------


class TestVariance(unittest.TestCase):
    def test_over_budget_is_positive(self):
        delta, percent = budgets.variance(1000, 1250)
        self.assertEqual(delta, 250.0)
        self.assertEqual(percent, 125.0)

    def test_under_budget_is_negative(self):
        delta, percent = budgets.variance(1000, 400)
        self.assertEqual(delta, -600.0)
        self.assertEqual(percent, 40.0)

    def test_a_percentage_of_a_zero_budget_is_unanswerable(self):
        """Not 0, not a huge number, not infinity. Sub-phase L made the same call for an MSA with
        no recorded expiry: unknown is a third answer, and collapsing it into either of the other
        two produces a confident wrong one."""
        delta, percent = budgets.variance(0, 500)
        self.assertEqual(delta, 500.0)
        self.assertIsNone(percent)

    def test_a_zero_budget_with_zero_spend_is_still_unanswerable(self):
        delta, percent = budgets.variance(0, 0)
        self.assertEqual(delta, 0.0)
        self.assertIsNone(percent)

    def test_unset_values_do_not_crash(self):
        self.assertEqual(budgets.variance(None, None), (0.0, None))
        self.assertEqual(budgets.variance("", "abc"), (0.0, None))


# --------------------------------------------------------------------------------------------
# Totals and line hygiene
# --------------------------------------------------------------------------------------------


class TestTotals(unittest.TestCase):
    def test_total_is_the_sum_of_the_lines(self):
        lines = [line("Labor", 1000), line("Materials", 2500.50), line("Fee", 300)]
        self.assertEqual(budgets.total_budgeted(lines), 3800.50)

    def test_no_lines_is_zero(self):
        self.assertEqual(budgets.total_budgeted([]), 0)
        self.assertEqual(budgets.total_budgeted(None), 0)

    def test_unparseable_amounts_count_as_zero_rather_than_crashing(self):
        self.assertEqual(budgets.total_budgeted([line("Labor", "abc"), line("Fee", 100)]), 100.0)

    def test_line_for_finds_by_category(self):
        lines = [line("Labor", 10), line("Fee", 20)]
        self.assertEqual(budgets.line_for(lines, "Fee")["budgeted_amount"], 20)
        self.assertIsNone(budgets.line_for(lines, "Contingency"))
        self.assertIsNone(budgets.line_for(lines, None))

    def test_duplicate_categories_are_reported(self):
        """Two lines for one category make every rollup ambiguous — a reallocation would debit
        whichever row a query returned first."""
        lines = [line("Labor", 1), line("Materials", 2), line("Labor", 3)]
        self.assertEqual(budgets.duplicate_categories(lines), ["Labor"])
        self.assertEqual(budgets.duplicate_categories([line("Labor", 1)]), [])

    def test_negative_lines_are_reported(self):
        lines = [line("Labor", -5), line("Fee", 10)]
        self.assertEqual(budgets.negative_lines(lines), ["Labor"])

    def test_unclassified_is_the_gap_not_a_share(self):
        self.assertEqual(budgets.unclassified_amount(1000, 600), 400.0)
        self.assertEqual(budgets.unclassified_amount(1000, 1000), 0.0)


# --------------------------------------------------------------------------------------------
# Reallocation rules
# --------------------------------------------------------------------------------------------


class TestReallocationRules(unittest.TestCase):
    def setUp(self):
        self.lines = [
            line(budgets.CATEGORY_LABOR, 10000),
            line(budgets.CATEGORY_MATERIALS, 5000),
            line(budgets.CATEGORY_CONTINGENCY, 2000),
        ]

    def test_a_valid_move_has_no_errors(self):
        self.assertEqual(
            budgets.reallocation_errors(
                budgets.CATEGORY_CONTINGENCY, budgets.CATEGORY_MATERIALS, 500, self.lines
            ),
            [],
        )

    def test_the_same_category_on_both_sides_is_refused(self):
        errors = budgets.reallocation_errors(
            budgets.CATEGORY_MATERIALS, budgets.CATEGORY_MATERIALS, 100, self.lines
        )
        self.assertTrue(any("both sides" in e for e in errors), errors)

    def test_a_zero_amount_is_refused(self):
        errors = budgets.reallocation_errors(
            budgets.CATEGORY_CONTINGENCY, budgets.CATEGORY_MATERIALS, 0, self.lines
        )
        self.assertTrue(any("greater than zero" in e for e in errors), errors)

    def test_a_negative_amount_is_refused_rather_than_reversed(self):
        """A negative amount is not a move the other way — it is a move whose From and To labels
        are lies, and every report reading them would be wrong."""
        errors = budgets.reallocation_errors(
            budgets.CATEGORY_CONTINGENCY, budgets.CATEGORY_MATERIALS, -500, self.lines
        )
        self.assertTrue(any("greater than zero" in e for e in errors), errors)

    def test_moving_from_a_category_the_project_does_not_have_is_refused(self):
        errors = budgets.reallocation_errors(
            budgets.CATEGORY_FEE, budgets.CATEGORY_MATERIALS, 100, self.lines
        )
        self.assertTrue(any("nothing to move from" in e for e in errors), errors)

    def test_moving_more_than_a_line_holds_is_refused(self):
        errors = budgets.reallocation_errors(
            budgets.CATEGORY_CONTINGENCY, budgets.CATEGORY_MATERIALS, 2500, self.lines
        )
        self.assertTrue(any("negative" in e for e in errors), errors)

    def test_the_refusal_names_both_figures(self):
        """A message saying only 'insufficient' sends somebody to another screen to find out by
        how much."""
        errors = budgets.reallocation_errors(
            budgets.CATEGORY_CONTINGENCY, budgets.CATEGORY_MATERIALS, 2500, self.lines
        )
        joined = " ".join(errors)
        self.assertIn("2,000.00", joined)
        self.assertIn("2,500.00", joined)

    def test_moving_exactly_the_balance_is_allowed(self):
        self.assertEqual(
            budgets.reallocation_errors(
                budgets.CATEGORY_CONTINGENCY, budgets.CATEGORY_MATERIALS, 2000, self.lines
            ),
            [],
        )

    def test_every_problem_is_reported_at_once(self):
        """A form that reports one problem, is corrected, then reports the next is three round
        trips for a person who could have been told everything."""
        errors = budgets.reallocation_errors(None, None, 0, self.lines)
        self.assertGreaterEqual(len(errors), 3)


class TestSecondApproval(unittest.TestCase):
    def test_a_protected_category_on_either_side_needs_a_second_approval(self):
        for protected in budgets.PROTECTED:
            self.assertTrue(
                budgets.requires_second_approval(protected, budgets.CATEGORY_MATERIALS), protected
            )
            self.assertTrue(
                budgets.requires_second_approval(budgets.CATEGORY_MATERIALS, protected), protected
            )

    def test_two_ordinary_categories_do_not(self):
        self.assertFalse(
            budgets.requires_second_approval(
                budgets.CATEGORY_LABOR, budgets.CATEGORY_MATERIALS
            )
        )

    def test_no_approvals_at_all_is_blocked(self):
        errors = budgets.approval_errors(None, None, None, False)
        self.assertTrue(any("project manager" in e for e in errors), errors)

    def test_a_protected_move_with_only_a_pm_approval_is_blocked(self):
        errors = budgets.approval_errors("pm@x.com", "pm@x.com", None, True)
        self.assertTrue(any("second approval" in e for e in errors), errors)

    def test_the_second_approval_cannot_come_from_the_requester(self):
        errors = budgets.approval_errors("pm@x.com", "boss@x.com", "pm@x.com", True)
        self.assertTrue(any("requested it" in e for e in errors), errors)

    def test_the_second_approval_cannot_come_from_the_approving_pm(self):
        """The whole control. Without this the protected-category rule is satisfiable by one
        person clicking twice."""
        errors = budgets.approval_errors("someone@x.com", "pm@x.com", "pm@x.com", True)
        self.assertTrue(any("project manager who approved" in e for e in errors), errors)

    def test_a_genuine_third_party_clears_it(self):
        self.assertEqual(
            budgets.approval_errors("pm@x.com", "pm@x.com", "president@x.com", True), []
        )

    def test_an_unneeded_second_approval_is_not_an_error(self):
        """Somebody senior signed a reallocation that did not need it. That is stricter than the
        rule requires and never a reason to refuse the document."""
        self.assertEqual(
            budgets.approval_errors("pm@x.com", "pm@x.com", "president@x.com", False), []
        )


# --------------------------------------------------------------------------------------------
# Applying a reallocation — the invariant
# --------------------------------------------------------------------------------------------


class TestApplyReallocation(unittest.TestCase):
    def setUp(self):
        self.lines = [
            line(budgets.CATEGORY_LABOR, 10000),
            line(budgets.CATEGORY_MATERIALS, 5000),
            line(budgets.CATEGORY_CONTINGENCY, 2000),
        ]

    def test_money_moves_from_one_line_to_the_other(self):
        after = budgets.apply_reallocation(
            self.lines, budgets.CATEGORY_CONTINGENCY, budgets.CATEGORY_MATERIALS, 750
        )
        self.assertEqual(
            budgets.line_for(after, budgets.CATEGORY_CONTINGENCY)["budgeted_amount"], 1250.0
        )
        self.assertEqual(
            budgets.line_for(after, budgets.CATEGORY_MATERIALS)["budgeted_amount"], 5750.0
        )

    def test_a_reallocation_is_net_zero(self):
        """The invariant. The project total is the sum of these lines, so a move that changed it
        would silently rewrite the denominator WI-058 will eventually divide by."""
        before_total = budgets.total_budgeted(self.lines)
        after = budgets.apply_reallocation(
            self.lines, budgets.CATEGORY_CONTINGENCY, budgets.CATEGORY_MATERIALS, 750
        )
        self.assertEqual(budgets.total_budgeted(after), before_total)

    def test_it_is_net_zero_for_awkward_cents_too(self):
        """Rounding at every write is what makes the invariant exact rather than approximate."""
        lines = [line("A", 100.10), line("B", 0.05)]
        after = budgets.apply_reallocation(lines, "A", "B", 33.37)
        self.assertEqual(budgets.total_budgeted(after), budgets.total_budgeted(lines))

    def test_a_category_the_project_lacks_is_created(self):
        """Moving contingency into a category the job did not originally carry is an ordinary
        thing to do; making somebody add an empty line first is friction with no control value."""
        after = budgets.apply_reallocation(
            self.lines, budgets.CATEGORY_CONTINGENCY, budgets.CATEGORY_EQUIPMENT, 500
        )
        self.assertEqual(
            budgets.line_for(after, budgets.CATEGORY_EQUIPMENT)["budgeted_amount"], 500.0
        )
        self.assertEqual(budgets.total_budgeted(after), budgets.total_budgeted(self.lines))

    def test_the_callers_rows_are_not_mutated(self):
        """Pure, so a controller can compute the result, check the invariant, and only then
        write."""
        budgets.apply_reallocation(
            self.lines, budgets.CATEGORY_CONTINGENCY, budgets.CATEGORY_MATERIALS, 750
        )
        self.assertEqual(
            budgets.line_for(self.lines, budgets.CATEGORY_CONTINGENCY)["budgeted_amount"], 2000
        )

    def test_reversing_a_move_restores_the_original(self):
        """Cancellation is the same function with the categories swapped."""
        after = budgets.apply_reallocation(
            self.lines, budgets.CATEGORY_CONTINGENCY, budgets.CATEGORY_MATERIALS, 750
        )
        back = budgets.apply_reallocation(
            after, budgets.CATEGORY_MATERIALS, budgets.CATEGORY_CONTINGENCY, 750
        )
        for category in (budgets.CATEGORY_CONTINGENCY, budgets.CATEGORY_MATERIALS):
            self.assertEqual(
                budgets.line_for(back, category)["budgeted_amount"],
                budgets.line_for(self.lines, category)["budgeted_amount"],
                category,
            )

    def test_the_description_says_when_a_second_signature_was_needed(self):
        text = budgets.describe_reallocation(
            budgets.CATEGORY_CONTINGENCY, budgets.CATEGORY_MATERIALS, 750
        )
        self.assertIn("750.00", text)
        self.assertIn("protected", text)
        plain = budgets.describe_reallocation(
            budgets.CATEGORY_LABOR, budgets.CATEGORY_MATERIALS, 100
        )
        self.assertNotIn("protected", plain)


# --------------------------------------------------------------------------------------------
# Schema and source guards
# --------------------------------------------------------------------------------------------


class TestSchema(unittest.TestCase):
    def test_the_decision_module_imports_no_frappe(self):
        """It has to run in the bench-free CI tier, which is the only tier that runs at all."""
        self.assertNotIn("import frappe", _code_only(APP_ROOT / "quality" / "budgets.py"))

    def test_controller_class_names_are_what_frappe_derives(self):
        """`classname = doctype.replace(" ", "").replace("-", "")` — it strips characters and
        does NOT title-case. A mismatch makes `get_controller` raise, and `bench migrate`
        force-deletes the DocType silently while exiting 0."""
        for folder, doctype in (
            (CATEGORY_DIR, "Project Budget Category"),
            (LINE_DIR, "Project Budget Line"),
            (REALLOCATION_DIR, "Budget Reallocation"),
        ):
            expected = doctype.replace(" ", "").replace("-", "")
            source = _raw(folder / (folder.name + ".py"))
            self.assertIn(f"class {expected}(Document)", source, doctype)

    def test_every_doctype_declares_its_module(self):
        for folder in (CATEGORY_DIR, LINE_DIR, REALLOCATION_DIR):
            data = json.loads(_raw(folder / (folder.name + ".json")))
            self.assertEqual(data["module"], "Project Enhancements", folder.name)

    def test_the_budget_line_is_a_child_table(self):
        data = json.loads(_raw(LINE_DIR / "project_budget_line.json"))
        self.assertEqual(data.get("istable"), 1)

    def test_computed_spend_fields_are_read_only(self):
        """A keyed 'actual' is an opinion wearing a number's clothes."""
        data = json.loads(_raw(LINE_DIR / "project_budget_line.json"))
        fields = {f["fieldname"]: f for f in data["fields"]}
        for fieldname in ("committed_amount", "actual_amount", "spend_coverage", "budget_key"):
            self.assertEqual(fields[fieldname].get("read_only"), 1, fieldname)
        self.assertNotEqual(fields["budgeted_amount"].get("read_only"), 1)

    def test_the_coverage_options_match_the_code(self):
        data = json.loads(_raw(LINE_DIR / "project_budget_line.json"))
        fields = {f["fieldname"]: f for f in data["fields"]}
        options = [o for o in fields["spend_coverage"]["options"].split("\n") if o]
        self.assertEqual(set(options), set(budgets.COVERAGE_STATES))

    def test_the_category_source_options_match_the_code(self):
        data = json.loads(_raw(CATEGORY_DIR / "project_budget_category.json"))
        fields = {f["fieldname"]: f for f in data["fields"]}
        for fieldname in ("committed_source", "actual_source"):
            options = [o for o in fields[fieldname]["options"].split("\n") if o]
            self.assertEqual(set(options), set(budgets.SOURCES), fieldname)

    def test_every_approval_stamp_is_read_only(self):
        """Server clock and server session, never a client-proposed value. The old
        `complete_step` client path let the browser send the timestamp and the audit found
        retroactive box-checking."""
        data = json.loads(_raw(REALLOCATION_DIR / "budget_reallocation.json"))
        fields = {f["fieldname"]: f for f in data["fields"]}
        for fieldname in (
            "requested_by",
            "pm_approved_by",
            "pm_approved_on",
            "additional_approved_by",
            "additional_approved_on",
            "requires_additional_approval",
            "status",
            "from_balance_before",
            "from_balance_after",
            "to_balance_before",
            "to_balance_after",
        ):
            self.assertEqual(fields[fieldname].get("read_only"), 1, fieldname)

    def test_a_reallocation_must_carry_a_reason(self):
        """A reallocation with no reason is a number nobody can audit six months later."""
        data = json.loads(_raw(REALLOCATION_DIR / "budget_reallocation.json"))
        fields = {f["fieldname"]: f for f in data["fields"]}
        self.assertEqual(fields["reason"].get("reqd"), 1)

    def test_the_reallocation_is_submittable(self):
        data = json.loads(_raw(REALLOCATION_DIR / "budget_reallocation.json"))
        self.assertEqual(data.get("is_submittable"), 1)

    def test_canceled_is_spelled_with_one_l(self):
        """House style, and renaming a Select option after rows exist is a data migration — an
        off-options value makes every existing row unsaveable."""
        data = json.loads(_raw(REALLOCATION_DIR / "budget_reallocation.json"))
        fields = {f["fieldname"]: f for f in data["fields"]}
        options = fields["status"]["options"]
        self.assertIn("Canceled", options)
        self.assertNotIn("Cancelled", options)

    def test_amendment_is_refused(self):
        """Frappe's amend appends -1 and `on_submit` would move the money a second time."""
        source = _raw(REALLOCATION_DIR / "budget_reallocation.py")
        self.assertIn("_refuse_amendment", source)
        self.assertIn("amended_from", source)


class TestFormScript(unittest.TestCase):
    """The form script is not polish here — it is the only way an approval can be recorded.

    Every approval field on the document is read-only and `before_submit` refuses a reallocation
    with no project-manager approval, so without these buttons **no reallocation could ever be
    submitted**. Sub-phase K shipped `api/change_order.py` with no form script at all; that is a
    gap there, and repeating it here would have been a non-functional feature rather than an
    unpolished one.
    """

    SCRIPT = REALLOCATION_DIR / "budget_reallocation.js"

    def test_the_form_script_exists(self):
        self.assertTrue(self.SCRIPT.exists())

    def test_it_calls_both_approval_endpoints(self):
        source = _raw(self.SCRIPT)
        self.assertIn("api.project_budget.approve_reallocation", source)
        self.assertIn("api.project_budget.give_second_approval", source)

    def test_it_is_not_also_registered_as_doctype_js(self):
        """Frappe already loads <module>/doctype/<name>/<name>.js for a DocType this app owns.
        A `doctype_js` entry appends the same file a second time with no dedupe, and a top-level
        `const` then becomes a SyntaxError that costs the form every button."""
        hooks = _raw(APP_ROOT / "hooks.py")
        self.assertNotIn('"Budget Reallocation"', hooks.split("doctype_js")[-1])

    def test_it_proposes_no_approver_and_no_timestamp(self):
        """The server stamps both, from the session and its own clock."""
        source = _raw(self.SCRIPT)
        self.assertNotIn("frappe.session.user", source)
        self.assertNotIn("frappe.datetime.now", source)


class TestFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fields = {
            (f["dt"], f["fieldname"]): f for f in json.loads(_raw(CUSTOM_FIELDS))
        }

    def test_the_lines_field_name_matches_the_code(self):
        """A fieldname typo would read as 'this project has no budget' — silently, on every
        project, with the rollup returning on its first line and nothing looking wrong."""
        self.assertIn(("Project", budgets.LINES_FIELD), self.fields)

    def test_the_lines_field_points_at_the_child_doctype(self):
        field = self.fields[("Project", budgets.LINES_FIELD)]
        self.assertEqual(field["fieldtype"], "Table")
        self.assertEqual(field["options"], "Project Budget Line")

    def test_purchase_order_lines_can_name_a_budget_category(self):
        """Without this the committed column has no attribution at all: 217 of the 324 live
        project-tagged purchase-order lines carry item group `Products`, and the item group tree
        has no labour / materials / equipment / subcontract axis anywhere in it."""
        field = self.fields[("Purchase Order Item", "custom_budget_category")]
        self.assertEqual(field["fieldtype"], "Link")
        self.assertEqual(field["options"], "Project Budget Category")

    def test_the_category_field_is_optional(self):
        """Making it mandatory would stop somebody buying materials over a classification they
        may not know yet. An unclassified line is reported, not prevented."""
        field = self.fields[("Purchase Order Item", "custom_budget_category")]
        self.assertNotEqual(field.get("reqd"), 1)


class TestRollup(unittest.TestCase):
    """Source-level guards on the frappe glue.

    Asserted against the RAW source: `budget_rollup.py` holds its SQL in triple-quoted strings,
    so a docstring stripper would delete the very queries under test. That has happened here
    before — the sub-phase L stripper ate `orders_for_project`.
    """

    def test_both_purchase_queries_use_the_project_union(self):
        """Either project field alone drops orders, silently. On PRJ-00566 a row-only match
        returns 37 lines where the union returns 63."""
        source = _raw(ROLLUP)
        self.assertIn("ifnull(nullif(poi.project, ''), po.project)", source)
        self.assertIn("ifnull(nullif(pii.project, ''), pi.project)", source)

    def test_only_submitted_documents_count(self):
        source = _raw(ROLLUP)
        self.assertIn("po.docstatus = 1", source)
        self.assertIn("pi.docstatus = 1", source)
        self.assertIn("t.docstatus = 1", source)

    def test_unclassified_spend_has_its_own_bucket(self):
        source = _raw(ROLLUP)
        self.assertIn("UNCLASSIFIED", source)
        self.assertIn("unclassified_for_project", source)

    def test_the_rollup_never_zeroes_a_total_it_cannot_derive(self):
        """A project with no category lines may still carry a total somebody typed — and WI-057's
        backfill is about to write exactly that figure onto hundreds of projects. Deriving a
        total from an empty list would erase it."""
        source = _no_comments(ROLLUP)
        body = source.split("def refresh(", 1)[1]
        early_return = body.split("spend = ", 1)[0]
        self.assertIn("return", early_return)
        self.assertNotIn("estimated_costing", early_return)

    def test_the_rollup_cannot_block_a_project_save(self):
        """It is a convenience on a field somebody else owns. A failure must log and let the
        project manager save their own job."""
        source = _code_only(ROLLUP)
        entry = source.split("def on_project_validate(", 1)[1]
        self.assertIn("except Exception", entry)
        self.assertIn("log_error", entry)


class TestApiSurface(unittest.TestCase):
    def test_whitelist_sits_directly_on_every_endpoint(self):
        """`test_whitelist_placement.py` fails the build on a helper `def` between the decorator
        and the endpoint — a refactor silently de-whitelisted one on 2026-09-13."""
        source = _raw(API)
        for match in re.finditer(r"@frappe\.whitelist\(\)\n(.*)", source):
            self.assertTrue(match.group(1).startswith("def "), match.group(1))

    def test_the_api_is_four_space_indented(self):
        """`api/` is 4 spaces while most of the app is tabs. Never normalise a file you are only
        touching."""
        self.assertNotIn("\n\t", _raw(API))

    def test_the_second_approval_endpoint_enforces_segregation(self):
        source = _code_only(API)
        entry = source.split("def give_second_approval(", 1)[1]
        self.assertIn("approval_errors", entry)
        self.assertIn("frappe.session.user", entry)

    def test_approvals_are_stamped_from_the_server_clock(self):
        source = _code_only(API)
        self.assertIn("now_datetime()", source)

    def test_variance_percent_is_passed_through_not_defaulted(self):
        """Rendering an unanswerable percentage as 0 would say 'on budget'."""
        source = _code_only(API)
        self.assertIn('"variance_percent": percent', source)
        self.assertNotIn("percent or 0", source)


class TestPatch(unittest.TestCase):
    def test_the_patch_is_insert_only(self):
        """It must not reset a description Finance has rewritten or a source they re-pointed."""
        source = _code_only(PATCH)
        self.assertIn("frappe.db.exists", source)
        self.assertIn("continue", source)

    def test_the_patch_cannot_abort_the_deploy(self):
        """A patch that raises aborts `bench migrate`, which on this repo IS the deploy — and
        leaves it half-finished at the new version string."""
        source = _code_only(PATCH)
        self.assertIn("if not frappe.db.exists(\"DocType\", DOCTYPE):", source)
        self.assertIn("return", source)

    def test_protection_is_restored_but_never_cleared(self):
        source = _code_only(PATCH)
        entry = source.split("def _restore_protection(", 1)[1]
        self.assertIn('"is_protected", 1', entry)
        self.assertNotIn('"is_protected", 0', entry)

    def test_the_patch_is_registered(self):
        registered = _raw(APP_ROOT / "patches.txt")
        self.assertIn("erpnext_enhancements.patches.seed_budget_categories", registered)


class TestHooks(unittest.TestCase):
    def test_the_rollup_is_wired_to_project_validate(self):
        source = _raw(APP_ROOT / "hooks.py")
        self.assertIn(
            "erpnext_enhancements.project_enhancements.budget_rollup.on_project_validate", source
        )

    def test_hooks_has_exactly_one_project_key(self):
        """hooks.py is ONE dict literal and a repeated key silently discards the earlier value.
        A second `"Project"` key here would drop the hand-off gate."""
        import ast

        tree = ast.parse(_raw(APP_ROOT / "hooks.py"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "doc_events":
                keys = [k.value for k in node.value.keys]
                self.assertEqual(len(keys), len(set(keys)))
                project = [
                    v
                    for k, v in zip(node.value.keys, node.value.values, strict=False)
                    if k.value == "Project"
                ]
                self.assertEqual(len(project), 1)
                events = [k.value for k in project[0].keys]
                self.assertEqual(len(events), len(set(events)))
                self.assertIn("validate", events)
                # The hand-off gate must still be there.
                self.assertIn("before_insert", events)
                return
        self.fail("doc_events not found in hooks.py")


if __name__ == "__main__":
    unittest.main()
