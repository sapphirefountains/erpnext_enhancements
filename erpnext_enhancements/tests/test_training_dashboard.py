"""The training dashboard: a learner's own standing, and a manager's view of it.

Two things here are worth a build gate, and one of them is a decision rather than
a bug.

**Scores never reach a manager.** Showing a person their own quiz history is
feedback; showing it to their manager is assessment, and this module has already
taken that position once — the team feed was built to carry no number anyone could
be judged by, and reactions hang on a record with no performance fields at all for
the same reason. What makes it durable is the SHAPE: ``get_person_dashboard`` does
not build scores, so there is no key to omit and no argument that can put them
back. A filter would be one refactor away from leaking; an absent branch is not.

**Nothing is counted twice.** "Open", "overdue" and "visible" are predicates owned
by ``api/training.py``. This module has shipped the cost of a second definition
twice — a nullable-date filter that turned "no expiry" into "expired", and a tile
that counted four statuses while its drill-through selected two — so the dashboard
imports those rules rather than restating them, and counts its tiles off the same
rows it then draws.

Run: python -m unittest erpnext_enhancements.tests.test_training_dashboard
"""

import ast
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
DASHBOARD_PY = APP / "training" / "dashboard.py"
BLOCK_JS = APP / "custom_html_blocks" / "training_my_dashboard.js"
BLOCK_CSS = APP / "custom_html_blocks" / "training_my_dashboard.css"
BLOCK_HTML = APP / "custom_html_blocks" / "training_my_dashboard.html"
PERSON_JS = APP / "public" / "js" / "training" / "person_record.js"
PERSON_CSS = APP / "public" / "css" / "training" / "person_record.css"
SEEDER = APP / "setup" / "custom_html_blocks.py"
CI = APP.parent / ".github" / "workflows" / "ci.yml"

# Anything that would put a grade in front of somebody other than the person who
# earned it. `score` alone is too broad -- it matches `pass_score`, a course
# SETTING that says nothing about a learner.
SCORE_TOKENS = ("score_percent", "average_score", "best_score", "attempts", "quiz_runs")


def source(path):
    return path.read_text(encoding="utf-8")


def js_code(path):
    """JS with comments stripped, for assertions about absence.

    Needed here for the usual reason and a specific one: the comments in both
    scripts explain WHY scores are absent, so they necessarily name them.
    """
    src = re.sub(r"/\*.*?\*/", "", source(path), flags=re.S)
    return chr(10).join(line for line in src.splitlines() if not line.strip().startswith("//"))


def css_code(path):
    """CSS with comments stripped.

    Same reason as `js_code`, and the seventh time this shape has bitten in this
    repo: the comment explaining why a token is absent necessarily names it. The
    stylesheet below says "a `var(--tr-surface)` here would resolve to nothing",
    which is exactly what the assertion is looking for.
    """
    return re.sub(r"/\*.*?\*/", "", source(path), flags=re.S)


def function_body(name):
    """One function's source, by AST rather than by string slicing."""
    tree = ast.parse(source(DASHBOARD_PY))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source(DASHBOARD_PY), node)
    raise AssertionError(f"{name} is not defined in training/dashboard.py")


class TestTheManagerViewCarriesNoScores(unittest.TestCase):
    def test_the_manager_read_builds_none(self):
        """By construction, not by filter. There is no key to omit."""
        body = function_body("get_person_dashboard")
        for token in SCORE_TOKENS:
            with self.subTest(token):
                self.assertNotIn(token, body)
        self.assertNotIn("_scores(", body)

    def test_the_learner_read_does(self):
        """The other half of the statement. Without this, deleting the scores
        feature entirely would pass the test above."""
        body = function_body("get_my_dashboard")
        self.assertIn("_scores(user)", body)

    def test_the_score_helper_is_never_reached_from_the_manager_path(self):
        """A call graph check rather than a text one: _scores must be called by
        exactly one function."""
        tree = ast.parse(source(DASHBOARD_PY))
        callers = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "_scores":
                    callers.add(node.name)
        self.assertEqual(callers, {"get_my_dashboard"})

    def test_the_dialog_renders_no_score_field(self):
        code = js_code(PERSON_JS)
        for token in SCORE_TOKENS:
            with self.subTest(token):
                self.assertNotIn(token, code)


class TestItIsGated(unittest.TestCase):
    def test_the_manager_read_checks_roles_before_anything_else(self):
        """'They passed me a user id' is not authorisation."""
        body = function_body("get_person_dashboard")
        lines = [line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith(('"', "#"))]
        # First statement after the signature and docstring.
        self.assertIn("_require_manager()", lines[1:4])

    def test_the_manager_set_matches_the_one_analytics_uses(self):
        """Two different answers to 'who may see across learners' is how a page and
        its endpoint end up disagreeing about who is allowed in."""
        analytics = source(APP / "training" / "analytics.py")
        mine = re.search(r"MANAGER_ROLES = \{([^}]*)\}", source(DASHBOARD_PY)).group(1)
        theirs = re.search(r"MANAGER_ROLES = \{([^}]*)\}", analytics).group(1)
        self.assertEqual(set(re.findall(r'"([^"]+)"', mine)), set(re.findall(r'"([^"]+)"', theirs)))

    def test_both_reads_are_post_only(self):
        text = source(DASHBOARD_PY)
        self.assertEqual(text.count('@frappe.whitelist(methods=["POST"])'), 2)
        self.assertEqual(text.count("@frappe.whitelist("), 2)


class TestItDefinesNothingOfItsOwn(unittest.TestCase):
    def test_it_imports_the_runtime_predicates(self):
        body = function_body("_course_rows")
        for helper in ("_open_assignments", "_completed_courses", "_visible_course_names", "_percent_complete"):
            with self.subTest(helper):
                self.assertIn(f"runtime.{helper}", body)

    def test_overdue_is_the_predicate_and_not_the_status_column(self):
        """`refresh_overdue_status` writes that column once a day, so between sweeps
        it is stale — which is exactly the divergence that made a manager's tile and
        the list it opened disagree."""
        body = function_body("_course_rows")
        self.assertIn("due < today_d", body)
        self.assertNotIn('status == "Overdue"', body)

    def test_the_split_uses_the_authors_own_word(self):
        """`Training Course.weight` is a two-value Select. A dashboard that invented
        its own idea of 'required' would disagree with the course form."""
        body = function_body("_split")
        self.assertIn('row["weight"] == "Required"', body)

    def test_the_tiles_are_counted_off_the_rows_that_are_drawn(self):
        """So a number and the list beneath it cannot disagree."""
        body = function_body("_compliance")
        self.assertIn("_split(rows)", body)
        self.assertNotIn("frappe.get_all", body)
        self.assertNotIn("frappe.db.count", body)

    def test_no_required_courses_reads_as_none_not_zero(self):
        """0% reads as failure; 'nothing is required of you' is not a failure."""
        body = function_body("_compliance")
        self.assertIn("else None", body)


class TestTheWorkspaceBlock(unittest.TestCase):
    def test_all_three_files_exist(self):
        for path in (BLOCK_JS, BLOCK_CSS, BLOCK_HTML):
            with self.subTest(path.name):
                self.assertTrue(path.exists(), f"{path} is missing")

    def test_it_is_registered_and_placed(self):
        """A placement naming an unseeded block renders an empty div with no other
        symptom; a seeded block with no placement is invisible."""
        text = source(SEEDER)
        self.assertIn('("My Training Dashboard", "training_my_dashboard")', text)
        self.assertIn('"My Training": ("My Training Dashboard",)', text)

    def test_it_is_placed_on_a_workspace_that_exists(self):
        workspace = APP / "training" / "workspace" / "my_training" / "my_training.json"
        self.assertTrue(workspace.exists(), "the My Training workspace is gone")

    def test_it_calls_the_dashboard_read_and_nothing_else(self):
        code = js_code(BLOCK_JS)
        self.assertIn("erpnext_enhancements.training.dashboard.get_my_dashboard", code)
        # The call is written `frappe` / newline / `.call({...})`, so count the
        # method invocation rather than a concatenation that never appears.
        self.assertEqual(code.count(".call({ method: METHOD })"), 1)
        self.assertEqual(code.count("frappe.xcall"), 0)

    def test_it_computes_no_predicate_of_its_own(self):
        """The server sends `overdue`, `due_soon`, `expiring_soon` already decided.
        A date comparison here would be a second answer, in a browser, to a question
        whose first answer took this module two bugs to get right."""
        code = js_code(BLOCK_JS)
        for token in ("new Date(", "getdate", "< today", "Date.now"):
            with self.subTest(token):
                self.assertNotIn(token, code)

    def test_it_escapes_everything_it_injects(self):
        """It builds HTML strings, so every interpolation must go through esc()."""
        code = js_code(BLOCK_JS)
        self.assertIn("frappe.utils.escape_html", code)
        # No bare template interpolation into innerHTML.
        self.assertNotIn("${", code)

    def test_it_survives_the_shadow_root_contract(self):
        """The workspace re-runs the whole script with a fresh root on every
        navigation, so nothing may be cached across renders."""
        code = js_code(BLOCK_JS)
        self.assertIn("root_element", code)

    def test_every_class_it_renders_has_a_rule(self):
        css = source(BLOCK_CSS)
        emitted = set(re.findall(r"\btmd-[a-z-]+", js_code(BLOCK_JS) + source(BLOCK_HTML)))
        self.assertGreater(len(emitted), 8)
        missing = sorted(c for c in emitted if f".{c}" not in css)
        self.assertEqual(missing, [], f"{missing} render unstyled")

    def test_it_reaches_for_no_aurora_token(self):
        """`--tr-*` is declared in player.css, which a workspace never loads — so a
        var(--tr-...) here resolves to nothing on the one page this file is used."""
        self.assertNotIn("var(--tr-", css_code(BLOCK_CSS))


class TestThePersonRecordDialog(unittest.TestCase):
    def test_it_is_loaded_on_demand_not_bundled(self):
        """A manager opens it rarely and a learner never."""
        bundle = source(APP / "public" / "js" / "erpnext_enhancements.bundle.js")
        self.assertNotIn("person_record", bundle)

    def test_the_one_door_is_in_the_global_helper(self):
        loader = source(APP / "public" / "js" / "training" / "desk_assets.js")
        self.assertIn("TR.openPersonRecord", loader)
        self.assertIn("js/training/person_record.js", loader)

    def test_both_entry_points_use_it(self):
        insights = js_code(APP / "training" / "page" / "training_insights" / "training_insights.js")
        employee = js_code(APP / "public" / "js" / "training" / "employee_training.js")
        self.assertIn("TR.openPersonRecord", insights)
        self.assertIn("TR.openPersonRecord", employee)

    def test_the_employee_button_is_role_gated_and_needs_a_user(self):
        """Training records belong to a User, so an Employee with no linked account
        has none — and an entry point that opens an empty dialog reads as the data
        being missing rather than absent by construction."""
        code = js_code(APP / "public" / "js" / "training" / "employee_training.js")
        self.assertIn("frm.doc.user_id", code)
        self.assertIn("EMP_TRAINING_MANAGERS", code)

    def test_the_employee_script_is_registered(self):
        self.assertIn("training/employee_training.js", source(APP / "hooks.py"))

    def test_every_class_it_renders_has_a_rule(self):
        css = source(PERSON_CSS)
        emitted = set(re.findall(r"\btpr-[a-z-]+", js_code(PERSON_JS)))
        self.assertGreater(len(emitted), 6)
        missing = sorted(c for c in emitted if f".{c}" not in css)
        self.assertEqual(missing, [], f"{missing} render unstyled")


class TestItIsWiredIntoCI(unittest.TestCase):
    def test_ci_runs_this_module(self):
        self.assertIn("erpnext_enhancements.tests.test_training_dashboard", source(CI))


if __name__ == "__main__":
    unittest.main()
