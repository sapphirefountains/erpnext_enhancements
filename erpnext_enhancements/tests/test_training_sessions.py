"""Recording a training that already happened.

The honest answer to "make building a training simple enough that anyone can do
it" — and the half of that ask no design lens proposed. Most training at this
company is not a course: it is a ten-minute tailgate talk before a basin drain, a
manufacturer's rep walking three people through a filter, a ride-along. It has
*already happened*, in the yard, and what is needed is a record of it.

Nothing in this module could produce one. Every path assumed somebody sat down and
worked through content; `Training Completion` is only ever minted by
`api/training.py` off a finished attempt, and `Training Live Class` (WI-071 Phase
B) has no attendance or completion path out of it at all. So a safety talk left no
trace, and the reason safety talks do not get recorded is that recording one meant
authoring a course first.

Two things are pinned hardest here.

**No course is required.** Requiring one puts a content-authoring project between
somebody and a five-minute record, which is exactly the barrier this exists to
remove.

**Linking a course mints real completions, and that has to stay auditable.**
`attempt` is not required on a completion, so this is legitimate — somebody taught
the material in person by a competent person has met the course's substance. What
makes it evidence rather than a back door is the provenance: the completion names
the session, and the session names who led it, what was covered, when, and carries
the photo of the signed sheet. Cancelling the session withdraws the completions,
because a session that did not happen must not leave certifications behind.

Run: python -m unittest erpnext_enhancements.tests.test_training_sessions
"""

import ast
import json
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SESSION_JSON = APP / "training/doctype/training_session/training_session.json"
SESSION_PY = APP / "training/doctype/training_session/training_session.py"
ATTENDEE_JSON = APP / "training/doctype/training_session_attendee/training_session_attendee.json"
COMPLETION_JSON = APP / "training/doctype/training_completion/training_completion.json"
WORKSPACE = APP / "training/workspace/training/training.json"
PATCH = APP / "patches/resync_hr_and_training_workspaces.py"
PATCHES_TXT = APP / "patches.txt"


def _text(path):
    return path.read_text(encoding="utf-8")


def _fields(path):
    return {f["fieldname"]: f for f in json.loads(_text(path))["fields"]}


def _fn(name, path=SESSION_PY):
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


class TestNoCourseIsRequired(unittest.TestCase):
    def test_the_title_is_free_text(self):
        """Making somebody author a course before they can record a safety talk is
        exactly why safety talks never get recorded."""
        self.assertEqual(_fields(SESSION_JSON)["session_title"]["fieldtype"], "Data")
        self.assertEqual(_fields(SESSION_JSON)["session_title"].get("reqd"), 1)

    def test_the_course_link_is_optional(self):
        self.assertNotEqual(_fields(SESSION_JSON)["course"].get("reqd"), 1)

    def test_a_session_with_no_course_still_reaches_the_feed(self):
        """A tailgate talk is worth showing even when it certifies nothing — it is
        the commonest kind of training here and was previously invisible."""
        body = _fn("_record_achievements")
        self.assertIn("social.record", body)
        self.assertIn("if self.course else", body)

    def test_a_session_with_no_course_mints_no_completion(self):
        """Attendance is not certification. Minting one anyway would put people on
        a compliance record for a talk that was never measured against content."""
        body = _fn("_issue_completions")
        self.assertIn("if not self.course or not self.course_version:", body)
        self.assertIn("return", body)


class TestCertificationStaysAuditable(unittest.TestCase):
    def test_the_completion_names_the_session(self):
        """The provenance is what makes this evidence rather than a back door."""
        self.assertIn("source_session", _fields(COMPLETION_JSON))
        self.assertIn("completion.source_session = self.name", _fn("_issue_completions"))

    def test_that_field_is_read_only(self):
        self.assertEqual(_fields(COMPLETION_JSON)["source_session"].get("read_only"), 1)

    def test_the_session_records_what_an_auditor_would_ask(self):
        fields = _fields(SESSION_JSON)
        for expected in ("led_by", "topics_covered", "roster_photo", "held_on"):
            with self.subTest(field=expected):
                self.assertIn(expected, fields)
        self.assertEqual(fields["led_by"].get("reqd"), 1)
        self.assertEqual(fields["held_on"].get("reqd"), 1)

    def test_the_version_is_frozen_at_submit(self):
        """A completion has to name the exact content somebody was taught, and the
        live version moves."""
        body = _fn("_resolve_course_version")
        self.assertIn("if not self.course_version:", body)
        self.assertEqual(_fields(SESSION_JSON)["course_version"].get("read_only"), 1)

    def test_an_unpublished_course_is_refused(self):
        """There is nothing to certify anybody against."""
        self.assertIn("_require_published_course", _text(SESSION_PY))

    def test_a_future_session_is_refused(self):
        """This records what happened. Letting a future date through would mint
        completions for a talk nobody has given yet — Training Live Class is the
        doctype for something scheduled."""
        self.assertIn("_reject_future_dates", _text(SESSION_PY))

    def test_an_absentee_is_never_certified(self):
        """A roster that quietly records people who did not turn up as trained is
        worse than no roster."""
        body = _fn("_issue_completions")
        self.assertIn("if not cint(row.attended)", body)

    def test_somebody_has_to_have_been_there(self):
        self.assertIn("_require_someone_present", _text(SESSION_PY))

    def test_cancelling_withdraws_the_completions(self):
        """A session that did not happen must not leave certifications behind."""
        body = _fn("_withdraw_completions")
        self.assertIn("doc.cancel()", body)

    def test_it_is_submittable(self):
        """The roster is evidence, and evidence is not something you edit."""
        self.assertEqual(json.loads(_text(SESSION_JSON)).get("is_submittable"), 1)

    def test_one_bad_attendee_does_not_cost_the_others(self):
        body = _fn("_issue_completions")
        self.assertIn("except Exception:", body)
        self.assertIn("log_error", body)

    def test_re_submitting_does_not_double_certify(self):
        body = _fn("_issue_completions")
        self.assertIn("or row.completion", body)


class TestItIsReachable(unittest.TestCase):
    """This module has shipped complete-but-uncalled features twice, and shipped a
    whole visual editor that nothing linked to."""

    def test_the_workspace_offers_it(self):
        ws = json.loads(_text(WORKSPACE))
        labels = {link.get("link_to") for link in ws["links"]}
        self.assertIn("Training Session", labels)
        self.assertIn("Record a session", {s["label"] for s in ws["shortcuts"]})

    def test_the_shortcut_is_placed_on_the_page(self):
        """A declared shortcut absent from `content` is invisible."""
        ws = json.loads(_text(WORKSPACE))
        placed = {
            b["data"]["shortcut_name"]
            for b in json.loads(ws["content"])
            if b.get("type") == "shortcut"
        }
        self.assertIn("Record a session", placed)

    def test_the_workspace_stamp_was_bumped(self):
        """Workspace import is TIMESTAMP-gated, unlike a DocType which is
        hash-gated. A file no newer than the stored row is skipped in silence —
        exactly what stranded this workspace's extra cards for five weeks."""
        self.assertGreater(json.loads(_text(WORKSPACE))["modified"], "2026-09-08 12:00:00")

    def test_a_forced_reload_ships_with_it(self):
        """The belt to that suspenders, for a row somehow touched more recently
        than the file."""
        self.assertIn("erpnext_enhancements.patches.resync_hr_and_training_workspaces", _text(PATCHES_TXT))
        self.assertIn("force=True", _text(PATCH))

    def test_the_resync_never_aborts_a_migrate(self):
        """A desk page's layout is not worth a failed deploy carrying schema
        changes."""
        body = _text(PATCH)
        self.assertIn("except Exception:", body)
        self.assertIn("log_error", body)

    def test_it_does_not_reload_a_file_that_does_not_exist(self):
        """Listing a sidebar this repo does not ship would log an error on every
        migrate for ever."""
        for name in ast.literal_eval(
            _text(PATCH).split("SIDEBARS = ")[1].split("\n")[0]
        ):
            with self.subTest(sidebar=name):
                self.assertTrue((APP / f"workspace_sidebar/{name}.json").exists())


if __name__ == "__main__":
    unittest.main()
