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


class TestTheThirdSignoffOutcome(unittest.TestCase):
    """`Supervised Only` — WI-073 A.

    Two outcomes forced a supervisor to choose between "I would send them alone"
    and "come back to me" for somebody who had just done the whole job correctly
    with help. Faced with that, supervisors pick Competent, and the system then
    reports somebody as ready to work alone on the strength of a job they did with
    someone standing next to them. At a pump vault that is the failure that puts a
    person at a site on their own.

    So every assertion here is about the outcome being treated as **not**
    competent. It is recorded progress; it is not an attestation.
    """

    SIGNOFF_JSON = APP / "training/doctype/training_signoff/training_signoff.json"
    SIGNOFF_PY = APP / "training/doctype/training_signoff/training_signoff.py"
    SIGNOFF_MODULE = APP / "training/signoff.py"
    SIGNOFF_FORM = APP / "training/doctype/training_signoff/training_signoff.js"
    EVALUATION_JS = APP / "public/js/training/training_evaluation.js"

    def test_the_option_exists_on_the_doctype(self):
        fields = {f["fieldname"]: f for f in json.loads(_text(self.SIGNOFF_JSON))["fields"]}
        options = fields["outcome"]["options"].splitlines()
        self.assertEqual(options, ["Competent", "Supervised Only", "Needs More Practice"])

    def test_every_surface_offers_all_three(self):
        """A picker that offers two is a picker that forces the overstatement."""
        for path in (PLAYER, self.SIGNOFF_FORM, self.EVALUATION_JS):
            with self.subTest(path=path.name):
                self.assertIn("Supervised Only", _text(path))

    def test_competent_is_not_the_prominent_button_on_the_phone(self):
        """A supervisor standing in the sun taps the obvious control, and the
        obvious control must not be the one attesting somebody can work alone."""
        body = _js(PLAYER)
        at = body.index('record("Competent")')
        line = body[body.rindex("button(", 0, at) : at]
        self.assertIn("tr-button-quiet", line)

    def test_a_note_is_required_for_it_too(self):
        """"Supervised only" with no note does not say what still needs watching,
        which is the one thing the next supervisor needs before deciding whether
        to stand there again."""
        body = _fn("_require_notes_when_not_competent", self.SIGNOFF_PY)
        self.assertIn("NOT_YET_SOLO", body)
        self.assertNotIn("== NEEDS_PRACTICE", body)

    def test_it_does_not_satisfy_a_rung_requirement(self):
        """The point of the whole outcome, asserted where it bites."""
        body = _fn("_signoff_line", PROGRESSION)
        at = body.index("Supervised Only")
        after = body[at:]
        self.assertIn("MISSING", after)
        self.assertNotIn("HELD", after)

    def test_it_says_what_it_is_rather_than_reading_as_nothing(self):
        """Progress that is visible and honest, rather than progress that quietly
        counts -- or progress that is invisible, which is how a supervisor learns
        that recording it was pointless."""
        body = _fn("_signoff_line", PROGRESSION)
        self.assertIn("not yet solo", body.lower())

    def test_nothing_that_means_go_out_alone_accepts_it(self):
        """Completion, badge, feed entry and the recertification clock are all
        gated on COMPETENT alone. Asserted through the AST on the comparison
        itself, because the surrounding prose necessarily names the other
        outcomes."""
        import ast

        tree = ast.parse(_text(self.SIGNOFF_MODULE))
        compares = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Compare)
            and isinstance(n.left, ast.Attribute)
            and n.left.attr == "outcome"
        ]
        self.assertTrue(compares, "expected at least one outcome comparison")
        for node in compares:
            for comparator in node.comparators:
                with self.subTest(line=node.lineno):
                    self.assertEqual(
                        getattr(comparator, "id", None),
                        "COMPETENT",
                        "an outcome gate must compare against COMPETENT, never against "
                        "a not-competent value -- a new outcome would slip past it",
                    )

    def test_the_rejection_message_is_built_from_the_tuple(self):
        """It grew from two outcomes to three. A hand-written sentence is how an
        error message ends up describing a version of the feature that no longer
        exists."""
        body = _fn("record_signoff", self.SIGNOFF_MODULE)
        at = body.index("outcome not in OUTCOMES")
        branch = body[at : at + 300]
        self.assertIn("OUTCOMES", branch)
        self.assertNotIn("NEEDS_PRACTICE", branch)

    def test_a_fourth_outcome_would_join_one_tuple(self):
        """`NOT_YET_SOLO` exists so no caller has to remember to name both."""
        src = _text(self.SIGNOFF_PY)
        self.assertIn("NOT_YET_SOLO = (SUPERVISED_ONLY, NEEDS_PRACTICE)", src)


class TestATierReviewIsEvidenceForAPermissionGrant(unittest.TestCase):
    """Not a performance review — WI-073 A.

    A `Position` tier IS the sign-off authority, so moving somebody up hands them
    standing to attest that other people can work alone. Everything this record
    does not have follows from that.
    """

    TR_JSON = MODULE / "doctype/tier_review/tier_review.json"
    TR_PY = MODULE / "doctype/tier_review/tier_review.py"
    TR_JS = MODULE / "doctype/tier_review/tier_review.js"
    LINE_JSON = MODULE / "doctype/tier_review_line/tier_review_line.json"
    ENDPOINTS = MODULE / "tier_review.py"
    PERMISSIONS = MODULE / "permissions.py"
    EMPLOYEE_JS = APP / "public/js/employee.js"

    def test_there_is_no_pay_field_anywhere(self):
        """Compensation is QuickBooks' world. The moment this grows one it stops
        being evidence for an authority grant and becomes a salary negotiation,
        and the two must not share a document."""
        names = {f["fieldname"] for f in json.loads(_text(self.TR_JSON))["fields"]}
        names |= {f["fieldname"] for f in json.loads(_text(self.LINE_JSON))["fields"]}
        for absent in ("salary", "pay", "rate", "compensation", "increase", "bonus", "wage"):
            with self.subTest(field=absent):
                self.assertFalse(
                    any(absent in n for n in names), f"a field matching {absent!r} exists"
                )

    def test_there_is_no_score(self):
        """The output is binary -- they stand on the rung or they do not. A number
        invites a threshold, and a threshold invites gaming."""
        names = {f["fieldname"] for f in json.loads(_text(self.TR_JSON))["fields"]}
        for absent in ("score", "rating_total", "percent", "weight", "points"):
            with self.subTest(field=absent):
                self.assertFalse(any(absent in n for n in names))

    def test_the_lines_are_a_frozen_snapshot(self):
        fields = {f["fieldname"]: f for f in json.loads(_text(self.LINE_JSON))["fields"]}
        for name in ("requirement_kind", "requirement_label", "held_at_open"):
            with self.subTest(field=name):
                self.assertEqual(fields[name].get("read_only"), 1)
        # And nothing recomputes them after the open.
        src = _src(self.ENDPOINTS)
        self.assertIn("held_at_open", _fn("open_review", self.ENDPOINTS))
        for fn in ("decide", "apply_promotion", "send_to_reviewer"):
            with self.subTest(fn=fn):
                self.assertNotIn("held_at_open", _fn(fn, self.ENDPOINTS))

    def test_it_refuses_to_open_against_an_unconfigured_rung(self):
        """The guardrail. A review with no lines is an empty checklist everybody
        signs, and the promotion it authorises would have a paper trail proving
        nothing -- a laundered permission grant."""
        body = _fn("open_review", self.ENDPOINTS)
        at = body.index('readiness.get("configured")')
        self.assertIn("frappe.throw", body[at : at + 400])
        # And the refusal must come BEFORE any document is created.
        self.assertLess(at, body.index("frappe.new_doc"))

    def test_opening_one_is_not_open_to_everybody(self):
        """The form button is drawn for HR only, but a client check decides what to
        DRAW and never what is allowed."""
        body = _fn("open_review", self.ENDPOINTS)
        self.assertIn("eligible_reviewers", body)
        self.assertIn("DECIDER_ROLES", body)
        self.assertIn("PermissionError", body)

    def test_self_review_is_refused_first_and_unconditionally(self):
        """The sentence an auditor reads out. A promotion somebody granted
        themselves is not evidence of anything."""
        controller = _fn("_reject_self_review", self.TR_PY)
        self.assertIn("PermissionError", controller)
        for fn in ("decide", "apply_promotion"):
            with self.subTest(fn=fn):
                body = _fn(fn, self.ENDPOINTS)
                self.assertIn("doc.user == me", body)

    def test_status_and_decision_cannot_be_edited_directly(self):
        """`Employee` holds write here -- it has to, somebody fills in their own
        self-assessment -- so an editable decision field would let any of the
        sixteen authorise their own promotion. read_only is not enough: Frappe does
        not enforce it against the API. Exactly the hole the WI-072 review found in
        Time Off Request, avoided here by writing the guard with the field."""
        fields = {f["fieldname"]: f for f in json.loads(_text(self.TR_JSON))["fields"]}
        for name in ("status", "decision", "decision_note"):
            with self.subTest(field=name):
                self.assertEqual(fields[name].get("read_only"), 1)
        for guard in ("_guard_status", "_guard_decision"):
            with self.subTest(guard=guard):
                body = _fn(guard, self.TR_PY)
                self.assertIn("get_doc_before_save", body)
                self.assertIn("frappe.throw", body)
        # And every endpoint that writes the document must flag its own
        # transition, or the guard refuses the legitimate path too. Derived from
        # the source rather than counted against a magic number: the count was
        # wrong on the first run, and a number in a test is a thing that goes
        # stale silently the next time a transition is added.
        import ast

        tree = ast.parse(_text(self.ENDPOINTS))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            writes = any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("save", "insert")
                and getattr(n.func.value, "id", None) == "doc"
                for n in ast.walk(node)
            )
            if not writes:
                continue
            flags = any(
                isinstance(n, ast.Attribute) and n.attr == "tier_transition"
                for n in ast.walk(node)
            )
            with self.subTest(fn=node.name):
                self.assertTrue(
                    flags,
                    f"{node.name} writes the document without flagging its transition, "
                    "so _guard_status will refuse it",
                )

    def test_deciding_and_promoting_are_separate_acts(self):
        """The system proposes, a human promotes -- WI-072 decision 6. The position
        carries authority over other people, so granting it should be somebody
        pressing a button, not a side effect of saving a form."""
        decide = _fn("decide", self.ENDPOINTS)
        self.assertNotIn("custom_position", decide)
        apply_ = _fn("apply_promotion", self.ENDPOINTS)
        self.assertIn("custom_position", apply_)
        self.assertIn("applied_on", apply_)

    def test_the_promotion_is_written_through_the_document(self):
        """So core `Version` records it and the audit trail is the framework's --
        and so the fetched tier is populated, which db.set_value would not do."""
        body = _fn("apply_promotion", self.ENDPOINTS)
        self.assertIn("employee.save(", body)
        self.assertNotIn("db.set_value", body)
        self.assertIn("custom_position_tier", body)

    def test_not_yet_requires_a_reason(self):
        """A refusal with no reason leaves somebody nothing to work on. Same rule
        as a declined time-off request and a non-competent sign-off."""
        body = _fn("decide", self.ENDPOINTS)
        at = body.index("decision == NOT_YET")
        self.assertIn("frappe.throw", body[at : at + 200])

    def test_the_reviewer_must_outrank_them_on_the_same_ladder(self):
        """The same predicate as sign-off authority, read from the same place, so
        the two cannot come to disagree about who has standing."""
        body = _fn("send_to_reviewer", self.ENDPOINTS)
        self.assertIn("eligible_reviewers", body)
        self.assertIn("PermissionError", body)

    def test_an_applied_promotion_cannot_be_cancelled_away(self):
        body = _fn("cancel_review", self.ENDPOINTS)
        self.assertIn("applied_on", body)


class TestTheTierReviewIsReachable(unittest.TestCase):
    """Three correct server sides have shipped here with nothing calling them.
    This suite exists so that cannot be four.
    """

    ENDPOINTS = MODULE / "tier_review.py"
    TR_JS = MODULE / "doctype/tier_review/tier_review.js"
    EMPLOYEE_JS = APP / "public/js/employee.js"

    def test_every_whitelisted_endpoint_has_a_caller(self):
        import ast

        tree = ast.parse(_text(self.ENDPOINTS))
        whitelisted = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if getattr(target, "attr", None) == "whitelist":
                    whitelisted.add(node.name)
        self.assertGreaterEqual(len(whitelisted), 5)

        callers = _js(self.TR_JS) + _js(self.EMPLOYEE_JS)
        for name in sorted(whitelisted):
            with self.subTest(endpoint=name):
                self.assertIn(f"tier_review.{name}", callers)

    def test_the_entry_point_is_on_the_person_not_only_its_own_list(self):
        """"Is this person ready for the next rung" is a question somebody asks
        while looking at the person, and a feature reachable only from its own
        list view is one nobody finds."""
        self.assertIn("open_review", _js(self.EMPLOYEE_JS))

    def test_the_picker_says_so_when_nobody_can_review(self):
        """On prod a Junior Technician has exactly one eligible reviewer. An empty
        list is the most useful thing this screen can show -- it is the fact the
        deliverable exists to surface -- so it must not fail silently."""
        body = _js(self.TR_JS)
        at = body.index("reviewers_for")
        block = body[at : at + 1200]
        self.assertIn("if (!people.length)", block)
        self.assertIn("msgprint", block)


class TestATierReviewIsNotReadableByColleagues(unittest.TestCase):
    """A list of what somebody cannot yet do, in their own words. Tighter than
    time off, which only says they are away on Thursday.
    """

    PERMISSIONS = MODULE / "permissions.py"

    def test_the_row_filter_has_no_reports_to_arm(self):
        """Being somebody's manager is not a reason to read their
        self-assessment -- they see it by being named on it."""
        body = _fn("tier_review_query_conditions", self.PERMISSIONS)
        self.assertNotIn("reports_to", body)
        self.assertIn("reviewer_user", body)

    def test_there_is_a_document_level_twin(self):
        """A query condition filters lists and says nothing about get_doc(), which
        is exactly the gap that left three Training doctypes readable by customers
        until v1.386.0."""
        self.assertIn("def tier_review_has_permission", _text(self.PERMISSIONS))

    def test_both_are_registered_in_hooks(self):
        hooks = _text(APP / "hooks.py")
        for fn in ("tier_review_query_conditions", "tier_review_has_permission"):
            with self.subTest(fn=fn):
                self.assertIn(fn, hooks)

    def test_creating_one_for_somebody_else_is_not_refused(self):
        """`user` and `reviewer_user` are both derived in validate(), so both are
        empty when the permission check runs on a NEW row -- the same defect the
        WI-072 review found in time off and onboarding."""
        body = _fn("tier_review_has_permission", self.PERMISSIONS)
        self.assertIn('"employee"', body)
        self.assertIn('"reviewer"', body)


if __name__ == "__main__":
    unittest.main()
