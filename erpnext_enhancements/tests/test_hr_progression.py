# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The route up the ladder — WI-073 A.

WI-072 built a ladder whose tier **is** the sign-off authority and no route up it.
On prod that is a single point of failure rather than an HR nicety: four Junior
Technicians, one Senior, so one person is the only one in the company who can
attest for any of them.

The assertions here are mostly about **the empty case**, because that is where
this kind of feature fails. A rung with no requirements must say so; it must not
draw an empty checklist that reads as "ready". This release has already met that
bug three times — the dispatch advisory that could never fire, the whitespace
queries that passed vacuously, the backfill that recorded success having written
nothing — and a blank promotion checklist everybody signs is the same shape.

Run: python -m unittest erpnext_enhancements.tests.test_hr_progression
"""

import ast
import io
import json
import re
import tokenize
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
MODULE = APP / "hr_enhancements"
PROGRESSION = MODULE / "progression.py"
PROFILE = MODULE / "profile.py"
MATRIX = MODULE / "report/skills_matrix/skills_matrix.py"
REQ_JSON = MODULE / "doctype/position_requirement/position_requirement.json"
REQ_PY = MODULE / "doctype/position_requirement/position_requirement.py"
POSITION_JSON = MODULE / "doctype/position/position.json"
PLAYER = APP / "public/js/training/player.js"
CSS = APP / "public/css/training/player.css"


def _text(path):
    return path.read_text(encoding="utf-8")


def _js(path):
    """JS with `//` comments stripped -- an absence assertion must not read the
    comment explaining the absence. Six occurrences of that bug in WI-072."""
    return re.sub(r"//.*$", "", _text(path), flags=re.M)


def _src(path):
    """Python with comments AND docstrings stripped, for the same reason."""
    kept = [
        t
        for t in tokenize.generate_tokens(io.StringIO(_text(path)).readline)
        if t.type != tokenize.COMMENT
    ]
    tree = ast.parse(tokenize.untokenize(kept))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body[0].value.value = ""
    return ast.unparse(tree)


def _fn(name, path):
    src = _text(path)
    lines = src.splitlines()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            stmts = node.body
            if (
                stmts
                and isinstance(stmts[0], ast.Expr)
                and isinstance(stmts[0].value, ast.Constant)
                and isinstance(stmts[0].value.value, str)
            ):
                stmts = stmts[1:]
            return "\n".join(lines[stmts[0].lineno - 1 : node.end_lineno]) if stmts else ""
    raise AssertionError(f"{name} not found in {path.name}")


class TestTheEmptyCaseRefusesToLookReady(unittest.TestCase):
    """The whole reason this suite exists."""

    def test_readiness_reports_unconfigured_rather_than_zero_gaps(self):
        body = _fn("readiness", PROGRESSION)
        self.assertIn('"configured": False', body)
        # And the honest early return must come BEFORE any line evaluation, or a
        # rung with no rows reports 0 of 0 held, which renders as complete.
        self.assertLess(body.index('"configured": False'), body.index("_evaluate"))

    def test_the_profile_hides_the_panel_entirely_when_nothing_is_configured(self):
        body = _fn("_rung", PROFILE)
        self.assertIn("return None", body)
        self.assertIn("configured", body)

    def test_the_next_rung_says_so_instead_of_drawing_an_empty_list(self):
        """`rung.next_position` set with `rung.next.configured` false is the case:
        the rung above exists and nobody has said what it wants."""
        body = _js(PLAYER)
        at = body.index("function renderRung(")
        block = body[at : body.index("function rungBlock(")]
        self.assertIn("else if (rung.next_position)", block)
        self.assertIn("Nobody has written down", block)

    def test_the_matrix_says_when_its_gap_column_is_not_scoped(self):
        body = _fn("_scope_message", MATRIX)
        self.assertIn("if scoped == 0", body)
        self.assertIn("company-wide", body)

    def test_unconfigured_means_count_everything_not_count_nothing(self):
        """None and an empty set mean opposite things here. An empty set would
        report the whole company as having no gaps the moment nobody had filled
        the ladder in -- an unconfigured system reporting a clean bill of health,
        which is the exact failure direction this release keeps hitting."""
        body = _fn("required_keys", MATRIX)
        self.assertIn("return None", body)
        self.assertIn("return keys or None", body)
        # And the caller must treat None as "no scoping", not as "no requirements".
        self.assertIn("required is None or key in required", _src(MATRIX))


class TestReadinessIsComputedNeverStored(unittest.TestCase):
    def test_no_readiness_field_is_persisted_anywhere(self):
        """A stored "85% ready" is wrong the day after a credential lapses, and
        wrong in the optimistic direction is the number somebody acts on."""
        position = json.loads(_text(POSITION_JSON))
        names = {f["fieldname"] for f in position["fields"]}
        for absent in ("readiness", "ready_percent", "progress", "completion_percent"):
            with self.subTest(field=absent):
                self.assertNotIn(absent, names)

    def test_every_line_is_read_from_a_live_record(self):
        src = _src(PROGRESSION)
        for doctype in ("Training Completion", "Employee Credential", "Training Signoff"):
            with self.subTest(doctype=doctype):
                self.assertIn(doctype, src)

    def test_expiring_still_counts_as_held(self):
        """Somebody inside the horizon is qualified today. Reading it as a gap
        would make the horizon do the opposite of its job -- the same sentence the
        Skills Matrix docstring already commits to."""
        body = _fn("_date_state", PROGRESSION)
        at = body.index("<= HORIZON_DAYS")
        self.assertIn("EXPIRING", body[at : at + 120])
        self.assertNotIn("MISSING", body[at : at + 120])

    def test_the_horizon_matches_the_skills_matrix(self):
        """Two forward views disagreeing about what "soon" means is worse than
        either alone."""
        self.assertEqual(
            re.search(r"HORIZON_DAYS = (\d+)", _text(PROGRESSION)).group(1),
            re.search(r"HORIZON_DAYS = (\d+)", _text(MATRIX)).group(1),
        )


class TestTheLadderIsNotWidenedByAccident(unittest.TestCase):
    def test_the_next_rung_is_the_lowest_above_not_tier_plus_one(self):
        """A ladder is allowed to skip numbers, and a family to have gaps."""
        body = _fn("next_rung", PROGRESSION)
        self.assertIn('"tier": [">", cint(mine.tier)]', body)
        self.assertIn('order_by="tier asc"', body)
        self.assertNotIn("+ 1", body)

    def test_a_group_is_not_somewhere_a_person_stands(self):
        for fn in ("next_rung", "eligible_reviewers"):
            with self.subTest(fn=fn):
                self.assertIn("is_group", _fn(fn, PROGRESSION))

    def test_eligible_reviewers_does_not_fall_back_to_any_manager(self):
        """An empty list is the true answer and the useful one: on prod a Junior
        Technician's only eligible reviewer is the single Senior, and a fallback
        would hide exactly the fact this deliverable exists to surface."""
        body = _src(PROGRESSION)
        at = body.index("def eligible_reviewers")
        block = body[at:]
        for role in ("HR Manager", "System Manager", "Training Manager"):
            with self.subTest(role=role):
                self.assertNotIn(role, block)

    def test_a_signoff_line_does_not_re_derive_the_signer_authority(self):
        """A submitted Competent row IS the attestation. Re-checking the signer's
        current standing would mean a supervisor who changed position
        retroactively un-signs everybody they ever signed."""
        body = _src(PROGRESSION)
        at = body.index("def _signoff_line")
        block = body[at : body.index("def _date_state")]
        for token in ("outranks", "authority", "may_sign"):
            with self.subTest(token=token):
                self.assertNotIn(token, block)
        self.assertIn("docstatus", block)


class TestTheRungPanelIsSelfOnly(unittest.TestCase):
    """A gap list with somebody's name on it is the most performance-shaped data
    in this module. Yours is motivating; a colleague's is a ranking.
    """

    def test_rung_is_not_in_the_public_allowlist(self):
        public = _fn("colleague_profile", PROFILE)
        self.assertIn("PUBLIC_FIELDS", public)
        allow = re.search(r"PUBLIC_FIELDS = \((.*?)\)", _text(PROFILE), re.S).group(1)
        self.assertNotIn('"rung"', allow)

    def test_it_is_added_after_shared_returns(self):
        """Same construction as `expiring` and `devices`: a colleague payload
        never carries the key at all, so there is no filter to leave out."""
        body = _fn("my_profile", PROFILE)
        self.assertIn('"rung": _rung(user)', body)
        self.assertNotIn("rung", _fn("_shared", PROFILE))

    def test_the_renderer_has_no_is_self_branch(self):
        body = _js(PLAYER)
        at = body.index("if (person.rung) renderRung(")
        self.assertNotIn("is_self", body[at - 200 : at + 200])


class TestTheRequirementRowCannotBeEmpty(unittest.TestCase):
    """A requirement naming nothing renders as a row, counts toward the total and
    can never be satisfied -- somebody sits permanently short by one with no way
    to see why. It looks configured, which is the failure mode.
    """

    def test_it_refuses_a_row_with_no_target(self):
        body = _fn("_require_a_target", REQ_PY)
        self.assertEqual(body.count("frappe.throw"), 2)

    def test_the_other_link_is_cleared_not_left_behind(self):
        body = _fn("_require_a_target", REQ_PY)
        self.assertIn("self.training_course = None", body)
        self.assertIn("self.credential_type = None", body)

    def test_it_is_a_child_table_with_no_docperms(self):
        doc = json.loads(_text(REQ_JSON))
        self.assertEqual(doc["istable"], 1)
        self.assertEqual(doc["permissions"], [])
        self.assertEqual(doc["module"], "HR Enhancements")

    def test_signoff_and_course_requirements_share_the_course_link(self):
        """The matrix has one column per course, so both kinds must land on the
        same key rather than inventing a column the grid does not draw."""
        body = _fn("required_keys", MATRIX)
        self.assertIn('f"course:{row.training_course}"', body)


class TestTheStateIsNotColourOnly(unittest.TestCase):
    def test_the_mark_carries_the_state_as_well_as_the_colour(self):
        """A colour-only state is one a colour-blind technician reads as "all the
        same"."""
        body = _js(PLAYER)
        at = body.index("function rungBlock(")
        block = body[at : at + 1400]
        self.assertIn("tr-rung-mark", block)
        self.assertIn('"○"', block)
        self.assertIn('"●"', block)

    def test_each_state_has_a_style(self):
        css = _text(CSS)
        for state in ("held", "expiring", "missing"):
            with self.subTest(state=state):
                self.assertIn(f".tr-rung-{state}", css)


if __name__ == "__main__":
    unittest.main()
