"""Regressions for the three defects the first real execution found.

Every phase of this module was verified by reading, and all of it passed. Then it
was executed once, end to end, and three things were wrong:

1. **The supervisor sign-off gate did not gate.** ``finish_attempt`` enforced every
   lesson gate and never looked at ``require_supervisor_signoff``, so a technician
   could be certified competent to drain a basin having only answered questions
   about it. That is the exact failure the Phase-4 contract was written to prevent
   — and it did not, because those assertions checked *source presence* rather than
   behaviour.
2. **A learner who scored 100 got a certificate reading 0.** ``_issue_completion``
   wrote ``"score"`` where the field is ``score_percent``, and ``_doc_payload``
   discarded the unknown key **silently**. The value was not missing in a way
   anybody would notice; it was present, plausible and wrong.
3. Same typo on the attempt itself.

The most useful test here is the third class, and it is static: **every field name
written through ``_doc_payload`` is checked against the DocType JSON.** That one
would have caught the score bug before it ever ran, without a bench, and it
generalises to any field added later.

Run: python -m unittest erpnext_enhancements.tests.test_training_runtime_regressions
"""

import ast
import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
RUNTIME = APP / "api/training.py"
GRADING = APP / "training/grading.py"
DOCTYPES = APP / "training/doctype"


def _src():
    return RUNTIME.read_text(encoding="utf-8")


def _fields(doctype):
    """Declared fieldnames for a Training doctype, from its JSON."""
    slug = doctype.lower().replace(" ", "_")
    path = DOCTYPES / slug / f"{slug}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    names = {f["fieldname"] for f in data["fields"]}
    # Frappe supplies these on every doc; they are legitimate to write.
    return names | {"doctype", "name", "owner", "docstatus", "amended_from", "naming_series"}


def _grading():
    return GRADING.read_text(encoding="utf-8")


def _gfn(name):
    src = _grading()
    start = src.index(f"def {name}")
    nxt = src.find("\ndef ", start + 1)
    return src[start : nxt if nxt != -1 else len(src)]


def _fn(name):
    src = _src()
    start = src.index(f"def {name}")
    nxt = src.find("\ndef ", start + 1)
    return src[start : nxt if nxt != -1 else len(src)]


class TestPayloadFieldNamesExist(unittest.TestCase):
    """The static check that would have caught the score bug before it shipped.

    ``_doc_payload`` filters unknown keys so an insert cannot die in front of a
    learner. That is right, but it means a typo becomes *silent data loss* rather
    than an error — so the names have to be verified somewhere, and here is
    cheaper than production.
    """

    def _payload_keys(self, doctype):
        """String keys in every ``_doc_payload("<doctype>", {...})`` call."""
        tree = ast.parse(_src())
        found = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_doc_payload"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            if node.args[0].value != doctype:
                continue
            if len(node.args) > 1 and isinstance(node.args[1], ast.Dict):
                for key in node.args[1].keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        found.add(key.value)
        return found

    def test_the_parser_finds_calls(self):
        """Guards every other assertion in this class from passing vacuously."""
        self.assertTrue(self._payload_keys("Training Completion"))

    def test_completion_payload_names_real_fields(self):
        declared = _fields("Training Completion")
        unknown = sorted(self._payload_keys("Training Completion") - declared)
        self.assertEqual(
            unknown, [], f"_doc_payload writes {unknown} which Training Completion does not declare"
        )

    def test_attempt_payload_names_real_fields(self):
        declared = _fields("Training Attempt")
        unknown = sorted(self._payload_keys("Training Attempt") - declared)
        self.assertEqual(unknown, [], f"_doc_payload writes {unknown} not on Training Attempt")

    def test_the_score_field_is_the_percent_one(self):
        """Named explicitly because this is the bug, not a general principle: a
        certificate reading 'score 0' for a learner who scored 100."""
        keys = self._payload_keys("Training Completion")
        self.assertIn("score_percent", keys)
        self.assertNotIn("score", keys)

    def test_db_set_calls_use_real_field_names(self):
        """``db_set`` is filtered through ``meta.has_field`` in the same way, so it
        swallows a typo just as quietly."""
        declared = _fields("Training Attempt")
        body = _fn("finish_attempt")
        written = set(re.findall(r'"(\w+)":\s', body))
        # Only the keys that are plausibly field writes, not dict payload keys the
        # function returns to the caller.
        response_keys = {"passed", "attempt", "status", "score", "outstanding", "completion",
                         "lesson_key", "title", "reasons"}
        unknown = sorted(k for k in written - response_keys if k not in declared)
        self.assertEqual(unknown, [], f"finish_attempt db_sets {unknown} which Training Attempt lacks")


class TestSignoffGateIsEnforced(unittest.TestCase):
    """The gate that did not gate.

    The Phase-4 contract asserted the *compliance* module never throws, which was
    the right thing to check and a different thing entirely. Nothing asserted that
    a course requiring hands-on verification actually refuses to complete without
    it, so nothing noticed when it did not.
    """

    def test_finish_attempt_consults_the_requirement(self):
        self.assertIn("_signoff_outstanding", _fn("finish_attempt"))

    def test_the_helper_reads_the_course_flag(self):
        body = _fn("_signoff_outstanding")
        self.assertIn("require_supervisor_signoff", body)

    def test_only_a_submitted_competent_signoff_counts(self):
        """A draft is a request, not a verification; 'Needs More Practice' is the
        supervisor explicitly declining. Either one satisfying the gate would make
        it decorative."""
        body = _fn("_signoff_outstanding")
        self.assertIn('"outcome": "Competent"', body)
        self.assertIn('"docstatus": 1', body)

    def test_it_reports_rather_than_throws(self):
        """Reported as outstanding like every other unmet gate, so the player can
        tell the learner what is left instead of showing an error."""
        self.assertNotIn("frappe.throw", _fn("_signoff_outstanding"))

    def test_a_site_without_the_doctype_can_still_finish(self):
        """Phase 4 may not be migrated everywhere. Not being able to complete any
        course at all is a worse failure than an unenforced gate, so the absence is
        logged and skipped rather than blocking."""
        body = _fn("_signoff_outstanding")
        self.assertIn('frappe.db.exists("DocType", "Training Signoff")', body)
        self.assertIn("log_error", body)

    def test_the_completion_records_which_signoff_unlocked_it(self):
        self.assertIn("_competent_signoff", _fn("_issue_completion"))


class TestDroppedPayloadKeysAreLoud(unittest.TestCase):
    """Defensive must not mean silent.

    The filter stays — an insert that dies in front of a learner is worse than a
    missing value — but a discarded key now reaches the Error Log. The original
    docstring claimed the missing value would 'show up in the record, where
    somebody will notice it'. Nobody did, for four phases.
    """

    def test_dropped_keys_are_logged(self):
        body = _fn("_doc_payload")
        self.assertIn("log_error", body)

    def test_the_filter_still_exists(self):
        self.assertIn("has_field", _fn("_doc_payload"))


class TestAnswersAreFiledForAnalytics(unittest.TestCase):
    """A report reading a table nothing writes.

    ``Training Attempt Question`` was fully built — doctype, controller,
    permission hooks, and a Script Report that groups over it to name the question
    everybody misses. Nothing ever inserted a row. The report did not error; it
    returned an empty set, which on screen is indistinguishable from "no problems
    found".
    """

    def test_quiz_answers_are_filed(self):
        self.assertIn("_file_quiz_answers", _gfn("grade_quiz"))

    def test_checkpoint_answers_are_filed(self):
        self.assertIn("_file_checkpoint_answer", _gfn("grade_checkpoint"))

    def test_the_filed_rows_name_real_fields(self):
        """Same class of bug as the score typo, so checked the same way."""
        declared = _fields("Training Attempt Question")
        written = set()
        for fn in ("_file_quiz_answers", "_file_checkpoint_answer"):
            written |= set(re.findall(r'"(\w+)":', _gfn(fn)))
        # Filter keys of the filters dict, not the payload.
        written -= {"lesson_key", "coverage", "reasons", "ok", "gated", "waived"}
        unknown = sorted(k for k in written if k not in declared)
        self.assertEqual(unknown, [], f"answer filing writes {unknown} which the doctype lacks")

    def test_filing_never_costs_a_learner_their_submission(self):
        for fn in ("_file_quiz_answers", "_file_checkpoint_answer"):
            body = _gfn(fn)
            self.assertIn("except Exception", body, f"{fn} may not propagate")
            # Swallowed, but never quietly — that is exactly how the missing rows
            # went unnoticed for four phases.
            self.assertIn("log_error", body, f"{fn} swallows failures silently")

    def test_a_regrade_of_the_same_run_is_not_filed_twice(self):
        """Double-filing would weight one learner twice in every percentage the
        report prints."""
        self.assertIn("pluck=\"question\"", _gfn("_file_quiz_answers"))

    def test_the_answer_is_stored_as_text_not_an_option_key(self):
        """The report's most useful column names the dominant distractor by
        grouping on this value. Option keys group correctly but read as 'opt_3'."""
        body = _gfn("_answer_in_words")
        self.assertIn("option_key", body)
        self.assertIn("text", body)


class TestCoverageReachesTheRecord(unittest.TestCase):
    """Every completion recorded 0% video coverage.

    ``video_coverage_percent`` was read off the attempt and written onto the
    permanent Training Completion, and nothing ever populated it. The gate was
    never affected — it computes from the stored intervals — but the completion is
    the artefact you would produce in a dispute, and it understated everybody.
    """

    def test_the_gate_reports_whether_anything_was_gated(self):
        """0.0 coverage means 'watched nothing' and 'there was no video' alike;
        without this the record of a text-only course reads as a failure."""
        self.assertIn('"gated"', _gfn("evaluate_gates"))

    def test_completion_takes_coverage_from_the_gate(self):
        body = _fn("finish_attempt")
        self.assertIn('verdict.get("gated")', body)
        self.assertIn("_issue_completion(doc, score, coverage)", body)

    def test_no_video_writes_no_coverage_rather_than_zero(self):
        self.assertIn("if coverages else None", _fn("finish_attempt"))

    def test_the_stale_attempt_field_is_no_longer_the_source(self):
        """The bug itself: `flt(getattr(doc, "video_coverage_percent", 0))`."""
        self.assertNotIn(
            'getattr(doc, "video_coverage_percent"', _fn("_issue_completion")
        )

    def test_the_summary_counters_are_written(self):
        self.assertIn("_refresh_attempt_summary", _fn("finish_attempt"))
        body = _fn("_refresh_attempt_summary")
        for field in ("quiz_runs", "checkpoints_answered", "checkpoints_correct"):
            self.assertIn(field, body)

    def test_the_counters_never_break_a_completion(self):
        body = _fn("_refresh_attempt_summary")
        self.assertIn("except Exception", body)
        self.assertIn("log_error", body)

    def test_the_counters_do_not_churn_modified(self):
        """Matched on the call, not on the text.

        The first version of this asserted the string appeared anywhere in the
        function — and the comment immediately above the call says
        ``update_modified=False`` too, so deleting it from the code left the test
        green. Mutation testing caught that; reading it would not have.
        """
        call = re.search(r"set_value\([^)]*\)", _fn("_refresh_attempt_summary"), re.S)
        self.assertIsNotNone(call, "no set_value call to check")
        self.assertIn("update_modified=False", call.group(0))


if __name__ == "__main__":
    unittest.main()


class TestSignoffMatchesTheCourseNotTheVersion(unittest.TestCase):
    """A sign-off follows the learner and the course, not the content revision.

    Somebody watched draining a basin last month has been watched draining a
    basin. A reworded paragraph should send them back through the material, not
    back in front of a supervisor — so `_signoff_outstanding` matches on
    ``course``, deliberately, and not on ``course_version``.

    Pinned here because it is the kind of decision a later reader "tightens" into
    a version match while believing they are hardening it, and because it is what
    makes the smoke test's sign-off step unrepeatable: the previous run's sign-off
    stays valid, the gate correctly opens, and the harness used to report that as
    a security regression that had not happened.
    """

    def test_the_lookup_is_scoped_to_the_course(self):
        body = _fn("_signoff_outstanding")
        self.assertIn('"course": doc.course', body)

    def test_it_is_not_scoped_to_the_version(self):
        body = _fn("_signoff_outstanding")
        self.assertNotIn("course_version", body)

    def test_the_harness_says_so_rather_than_failing(self):
        """The smoke test must skip with a reason when a prior sign-off exists,
        not report a gate failure."""
        harness = (APP / "training/smoke_test.py").read_text(encoding="utf-8")
        start = harness.index("def signoff_gate_holds()")
        body = harness[start : harness.index("_step(", start)]
        self.assertIn("Training Signoff", body)
        # Reachability, not presence. Checking only that the word "skipped"
        # appears passed a mutation that replaced the guard with `if False:` —
        # the message stayed in the file and could never be shown, which is the
        # same complete-and-unreachable shape this module keeps finding.
        self.assertIn("if prior:", body)
        guard = body.index("if prior:")
        # The distinctive message, not the word "skipped" — that also appears in
        # the "course does not require sign-off" branch 900 characters earlier,
        # so the ordering check compared against the wrong one and failed on
        # correct code.
        self.assertIn("already signs this learner off", body)
        self.assertLess(
            guard,
            body.index("already signs this learner off"),
            "the skip message is unreachable",
        )
        self.assertLess(
            guard,
            body.index("finish_attempt"),
            "the gate is exercised before the prior sign-off is checked, so a "
            "re-run reports a failure that has not happened",
        )


VIDEO_JS = APP / "public/js/training/video.js"


def _video_js():
    return VIDEO_JS.read_text(encoding="utf-8")


def _fn_body(src, signature):
    """A JavaScript function's source, brace-matched from its signature."""
    start = src.index(signature)
    depth = 0
    for i in range(src.index("{", start), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


class TestCreditCannotLatchOffForever(unittest.TestCase):
    """Every flag that stops watch credit must have a way back on.

    The player's sampler is guarded by a row of booleans — ``seeking``,
    ``stalled``, ``blurredSince``, ``onScreen`` — each set by one DOM event and
    cleared by a *different* one. That is fine until the clearing event does not
    arrive, which browsers arrange in more ways than is comfortable: an aborted
    seek fires no ``seeked``, ``stalled`` fires at an idle paused video and is
    only cleared by ``playing``, a window that never regains focus never fires
    ``focus``, and an IntersectionObserver stops reporting when the element goes
    into native fullscreen.

    Any one of them latching means the learner watches the rest of the lesson for
    no credit and is told nothing at all — the meter simply stops. Four
    independent routes to one indistinguishable symptom, which is why this is
    checked structurally rather than case by case.
    """

    # (flag, the value that means "credit may flow again")
    LATCHES = (
        ("st.seeking", "false"),
        ("st.stalled", "false"),
        ("st.blurredSince", "0"),
        ("st.onScreen", "true"),
    )

    def test_the_watchdog_exists_and_runs_before_the_guards(self):
        body = _fn_body(_video_js(), "function tick()")
        heal = body.find("st.selfHealed")
        guard = body.find("if (video.paused || video.ended || st.seeking")
        self.assertNotEqual(heal, -1, "the credit watchdog is gone")
        self.assertNotEqual(guard, -1, "the sampler's guard row moved; re-read this test")
        self.assertLess(
            heal,
            guard,
            "the watchdog runs after the guard it is meant to unstick, so a "
            "latched st.seeking returns before anything can clear it",
        )

    def test_every_latch_is_cleared_by_the_watchdog(self):
        """Checked as *condition plus assignment*, not as a mention.

        Asserting the flag's name appears in the watchdog passed a mutation that
        changed ``if (st.stalled)`` to ``if (false)`` — the name was still right
        there on the assignment line below, doing nothing. Presence is not
        behaviour; this module has now learned that four times.
        """
        body = _fn_body(_video_js(), "function tick()")
        watchdog = body[body.index("Unstick the latches") : body.index("if (video.paused ||")]
        for latch, healed in self.LATCHES:
            self.assertRegex(
                watchdog,
                r"if \([^)]*" + re.escape(latch),
                f"{latch} is never tested by the watchdog, so whatever the lines "
                f"below it say, a latched {latch} is never noticed",
            )
            self.assertRegex(
                watchdog,
                re.escape(latch) + r"\s*=\s*" + re.escape(healed),
                f"the watchdog tests {latch} but never sets it back to {healed}",
            )

    def test_the_watchdog_only_fires_while_media_is_actually_advancing(self):
        """Otherwise it would clear a genuine seek's guard and hand out credit for
        a scrub — the one thing this whole file exists to prevent."""
        body = _fn_body(_video_js(), "function tick()")
        watchdog = body[body.index("Unstick the latches") : body.index("if (video.paused ||")]
        self.assertIn("dtMedia > 0", watchdog)
        self.assertIn("!video.paused", watchdog)


class TestTheBeatQueueCannotStarve(unittest.TestCase):
    """One refused beat must not hold up every beat behind it.

    ``drainQueue`` sends oldest-first. With a single ``.catch`` on the whole
    chain, the first rejection skipped every later beat *and* left the refused
    payload at the head of the queue, so the next drain failed on it again. A
    beat the server would never accept — a stale attempt, a lesson key from a
    republished version — stopped delivery permanently, and the only outward sign
    was a coverage meter that had quietly stopped moving.
    """

    def test_failures_are_handled_per_beat_not_per_drain(self):
        body = _fn_body(_video_js(), "function drainQueue()")
        self.assertIn("queue.fail(", body, "a refused beat is no longer counted or requeued")
        # The rejection handler must sit on the per-beat promise. As the second
        # argument to .then, or a .catch, INSIDE the reduce -- not trailing the
        # whole chain, which is what shipped.
        self.assertIn("function (err)", body)
        self.assertLess(
            body.index("queue.fail("),
            body.rindex("}, Promise.resolve())"),
            "the failure handler is outside the reduce, so it still aborts the "
            "whole drain on the first refusal",
        )

    def test_a_refused_beat_goes_to_the_back_and_is_eventually_abandoned(self):
        body = _fn_body(_video_js(), "fail: function (clientId)")
        self.assertIn("MAX_BEAT_ATTEMPTS", body, "a poison beat is retried forever")
        self.assertIn("kept.push(retry)", body, "a refused beat stays at the head of the queue")

    def test_something_retries_when_playback_is_not_driving_the_queue(self):
        src = _video_js()
        self.assertIn("function scheduleRetry()", src)
        # Called from BOTH arms of the flush: a drain that resolved with beats
        # still queued, and one that threw. Only wiring the success path leaves
        # the queue stranded in exactly the case it exists for.
        flush = _fn_body(src, "function flush(reason)")
        self.assertEqual(
            flush.count("scheduleRetry()"),
            2,
            "scheduleRetry is not called from both the resolve and the reject arm",
        )

    def test_a_stalled_queue_is_visible_to_the_learner(self):
        """`emit("offline")` had no subscriber anywhere in the app, so a queue
        that could not drain was indistinguishable from nobody watching."""
        src = _video_js()
        self.assertIn("function noticeIfStuck()", src)
        self.assertIn("showNotice(", _fn_body(src, "function noticeIfStuck()"))
