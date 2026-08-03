"""End-to-end smoke test for the Training module, run against a real bench.

    bench --site erp.sapphirefountains.com execute \
        erpnext_enhancements.training.smoke_test.run

Everything in the Training module has been verified by reading. Nothing has ever
executed. This script is the difference: it drives the real code paths —
publish, assign, attempt, quiz, gates, sign-off, completion, certificate — and
prints a pass/fail line per step.

**It writes real records.** It is safe to re-run: it resumes an existing attempt
rather than creating a second, and `--course` lets you point it at a throwaway
course instead of the live one.

Deliberately excludes video. Watch telemetry and byte-range serving are a
separate question with a separate failure mode, and debugging both at once is how
you end up unable to tell which half is broken. Run this first, add a video
second.

The assertions that matter most, in order:

1. **Publishing materialises an answer-free payload.** The whole grading-integrity
   story is that `published_content_json` never contains a correct answer. This
   walks the real published payload to any depth and fails on any answer marker —
   the same check that caught a real leak in Phase 1, but against live data
   rather than a fixture.
2. **Gates actually gate.** A lesson with an unanswered quiz must not complete,
   and a course requiring sign-off must not complete on a passing score alone.
   A gate that silently passes is worse than no gate.
3. **The certificate stores rendered HTML** and verifies without leaking PII.
"""

import json

import frappe
from frappe.utils import cint, flt

COURSE = "TRN-CRS-00001"
MARKERS = ("is_correct", "correct_text_answers")

_results = []


def _step(label, fn, critical=True):
    """Run one step, record the outcome, and keep the report readable."""
    try:
        detail = fn()
        _results.append(("PASS", label, detail or ""))
        print(f"  PASS  {label}" + (f"  — {detail}" if detail else ""))
        return True
    except Exception as exc:
        _results.append(("FAIL", label, str(exc)[:300]))
        print(f"  FAIL  {label}\n        {str(exc)[:300]}")
        if critical:
            print("\n  ^ critical step failed; the steps after this would report nonsense.\n")
        return False


def _walk(value):
    """Every string in a nested structure, keys included."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _walk(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _walk(v)
    else:
        yield str(value)


def run(course=COURSE):
    from erpnext_enhancements.api import training, training_author

    frappe.set_user("Administrator")
    print(f"\nTraining smoke test — course {course}\n" + "=" * 64)

    state = {}

    # ---------------------------------------------------------------- publish
    def publish():
        version = frappe.db.get_value(
            "Training Course Version", {"course": course, "docstatus": 0}, "name"
        )
        if not version:
            live = frappe.db.get_value("Training Course", course, "current_version")
            if not live:
                raise Exception("no draft and no published version")
            state["version"] = live
            return f"already published as {live}"
        training_author.publish_version(
            version, "Material Change (require retake)", "Smoke test run."
        )
        state["version"] = version
        return version

    if not _step("publish the draft version", publish):
        return _report()

    # ------------------------------------------------- the leak check, live
    def no_answers_in_published():
        lessons = frappe.get_all(
            "Training Lesson",
            filters={"course_version": state["version"]},
            fields=["name", "lesson_key", "published_content_json"],
        )
        if not lessons:
            raise Exception("no lessons on the published version")
        checked = 0
        for lesson in lessons:
            if not lesson.published_content_json:
                raise Exception(f"{lesson.name} has an empty published payload")
            payload = json.loads(lesson.published_content_json)
            haystack = list(_walk(payload))
            for marker in MARKERS:
                if marker in haystack:
                    raise Exception(f"{marker!r} present in {lesson.name} payload")
            checked += 1
            state.setdefault("lesson_keys", []).append(lesson.lesson_key)
        return f"{checked} lesson payloads, no answer markers"

    _step("published payload contains no answer key", no_answers_in_published)

    def key_is_populated():
        rows = frappe.get_all(
            "Training Lesson",
            filters={"course_version": state["version"], "has_quiz": 1},
            pluck="name",
        )
        for name in rows:
            raw = frappe.db.get_value("Training Lesson", name, "answer_key_json")
            if not raw or not json.loads(raw).get("quiz"):
                raise Exception(f"{name} has a quiz but no answer key")
        return f"{len(rows)} quiz lesson(s) have a key"

    _step("answer key materialised separately", key_is_populated)

    # ----------------------------------------------------------------- assign
    def assign():
        user = frappe.session.user
        result = training_author.assign_course(course, users=[user])
        state["user"] = user
        row = frappe.db.get_value(
            "Training Assignment",
            {"course": course, "user": user, "status": ["!=", "Cancelled"]},
            ["name", "due_date"],
            as_dict=True,
        )
        if not row:
            raise Exception(f"no assignment created (returned {result})")
        state["assignment"] = row.name
        return f"{row.name}, due {row.due_date}"

    _step("assign the course", assign, critical=False)

    # ---------------------------------------------------------------- attempt
    def start():
        state["attempt"] = training.start_attempt(course)
        if isinstance(state["attempt"], dict):
            state["attempt"] = state["attempt"].get("attempt") or state["attempt"].get("name")
        if not state["attempt"]:
            raise Exception("start_attempt returned nothing")
        return state["attempt"]

    if not _step("start an attempt", start):
        return _report()

    def lesson_is_answer_free():
        key = state["lesson_keys"][0]
        payload = training.get_lesson(state["attempt"], key)
        haystack = list(_walk(payload))
        for marker in MARKERS:
            if marker in haystack:
                raise Exception(f"{marker!r} reached the learner payload")
        return "get_lesson returned no answer markers"

    _step("get_lesson leaks no answers", lesson_is_answer_free)

    # ------------------------------------------------------------- the gates
    def quiz_gate_holds():
        quiz_lesson = frappe.db.get_value(
            "Training Lesson", {"course_version": state["version"], "has_quiz": 1}, "lesson_key"
        )
        state["quiz_lesson_key"] = quiz_lesson
        try:
            training.complete_lesson(state["attempt"], quiz_lesson)
        except Exception as exc:
            return f"refused as expected: {str(exc)[:120]}"
        raise Exception("completed a quiz lesson without taking the quiz — the gate does not gate")

    _step("quiz gate blocks completion before the quiz", quiz_gate_holds, critical=False)

    def take_quiz():
        key = state["quiz_lesson_key"]
        drawn = training.get_quiz(state["attempt"], key)
        questions = drawn.get("questions") if isinstance(drawn, dict) else drawn
        if not questions:
            raise Exception("get_quiz returned no questions")
        # Answer from the SERVER's key, so this tests the grading path rather than
        # our ability to guess. A learner obviously cannot do this.
        lesson = frappe.db.get_value(
            "Training Lesson", {"course_version": state["version"], "has_quiz": 1}, "name"
        )
        key_json = json.loads(frappe.db.get_value("Training Lesson", lesson, "answer_key_json"))
        answers = {}
        for q in questions:
            qname = q.get("question") or q.get("name")
            correct = (key_json.get("quiz", {}).get(qname) or {}).get("correct") or []
            answers[qname] = correct
        result = training.submit_quiz(state["attempt"], key, answers)
        state["quiz_result"] = result
        state["quiz_score"] = result.get("score") if isinstance(result, dict) else None
        score = result.get("score") if isinstance(result, dict) else None
        if score is None:
            raise Exception(f"submit_quiz returned no score ({result})")
        if score < 100:
            raise Exception(f"answered from the key but scored {score} — grading disagrees with itself")
        return f"scored {score} on {len(questions)} question(s)"

    _step("quiz grades correctly against its own key", take_quiz, critical=False)

    def complete_lessons():
        done = []
        for key in state["lesson_keys"]:
            try:
                training.complete_lesson(state["attempt"], key)
                done.append(key)
            except Exception as exc:
                raise Exception(f"lesson {key} would not complete: {str(exc)[:150]}")
        return f"{len(done)} lesson(s) completed"

    _step("complete every lesson", complete_lessons, critical=False)

    # ------------------------------------------------------- sign-off gate
    def signoff_gate_holds():
        if not frappe.db.get_value("Training Course", course, "require_supervisor_signoff"):
            return "course does not require sign-off; skipped"
        try:
            training.finish_attempt(state["attempt"])
        except Exception as exc:
            return f"refused as expected: {str(exc)[:120]}"
        completion = frappe.db.exists("Training Completion", {"attempt": state["attempt"]})
        if completion:
            raise Exception("issued a completion without a sign-off — the gate does not gate")
        return "no completion issued without sign-off"

    _step("sign-off gate blocks completion", signoff_gate_holds, critical=False)

    def record_signoff():
        from erpnext_enhancements.training import signoff as signoff_module

        # A sign-off has to name the supervisor making it, and it may not be the
        # learner. The first run of this script asked for the Employee of the
        # session user -- Administrator, which has no Employee record -- and so
        # named nobody. That is a bug in this script, not in the sign-off rule.
        supervisor = frappe.db.get_value(
            "Employee", {"status": "Active", "user_id": ["!=", state["user"]]}, "name"
        ) or frappe.db.get_value("Employee", {"status": "Active"}, "name")
        if not supervisor:
            raise Exception("no active Employee on this site to act as the supervisor")
        doc = frappe.get_doc(
            {
                "doctype": "Training Signoff",
                "course": course,
                "course_version": state["version"],
                "user": state["user"],
                "supervisor": supervisor,
                "attempt": state["attempt"],
                "outcome": "Competent",
                "competency_notes": "Smoke test — observed by script.",
            }
        )
        doc.insert(ignore_permissions=True)
        doc.submit()
        state["signoff"] = doc.name
        return doc.name

    _step("record a supervisor sign-off", record_signoff, critical=False)

    # ------------------------------------------------------------- completion
    def finish():
        training.finish_attempt(state["attempt"])
        completion = frappe.db.get_value(
            "Training Completion",
            {"attempt": state["attempt"]},
            ["name", "docstatus", "score_percent", "course_version", "content_hash", "expires_on"],
            as_dict=True,
        )
        if not completion:
            raise Exception("finish_attempt produced no Training Completion")
        if completion.docstatus != 1:
            raise Exception(f"completion is docstatus {completion.docstatus}, expected submitted")
        if completion.course_version != state["version"]:
            raise Exception("completion is not pinned to the version that was taken")
        if not completion.content_hash:
            raise Exception("completion has no content_hash — nothing proves what was passed")
        # Asserted, not just printed. The first run of this script reported this
        # step as a PASS while the line it printed said "score 0.0" for a learner
        # who had just scored 100 -- the number was on screen the whole time and
        # nothing was checking it. A smoke test that prints a wrong value without
        # failing is worth very little.
        expected = flt(state.get("quiz_score"))
        if expected and flt(completion.score_percent) != expected:
            raise Exception(
                f"completion records score {completion.score_percent}, "
                f"but the quiz was scored {expected}"
            )
        state["completion"] = completion.name
        return f"{completion.name}, score {completion.score_percent}, expires {completion.expires_on}"

    _step("finish the attempt and record a completion", finish, critical=False)

    # ------------------------------------------------------------ certificate
    def certificate():
        if not frappe.db.get_value("Training Course", course, "issues_certificate"):
            return "course does not issue certificates; skipped"
        row = frappe.db.get_value(
            "Training Certificate",
            {"completion": state.get("completion")},
            ["name", "docstatus", "verification_code", "certificate_html", "status", "expires_on"],
            as_dict=True,
        )
        if not row:
            raise Exception("no certificate issued for the completion")
        if not row.certificate_html:
            raise Exception(
                "certificate has no rendered HTML — editing the print format would rewrite it"
            )
        if not row.verification_code:
            raise Exception("certificate has no verification code")
        state["certificate"] = row
        return f"{row.name}, status {row.status}, expires {row.expires_on}"

    _step("certificate issued with a rendered snapshot", certificate, critical=False)

    def verify_leaks_nothing():
        from erpnext_enhancements.training import certificates as cert_module

        cert = state.get("certificate")
        if not cert:
            return "no certificate to verify; skipped"
        result = cert_module.verify_certificate(cert.verification_code)
        blob = json.dumps(result).lower()
        full_name = (frappe.db.get_value("User", state["user"], "full_name") or "").lower()
        for leak, label in (
            (state["user"].lower(), "the learner's email"),
            # Skip the name check for a very short name — "Li" would match half
            # the alphabet soup in a JSON payload and report a leak that is not one.
            (full_name if len(full_name) > 3 else None, "the learner's full name"),
        ):
            if leak and leak in blob:
                raise Exception(f"verify_certificate leaked {label}")
        if not result.get("valid"):
            raise Exception(f"a live certificate verified as invalid: {result}")
        return f"verified valid, payload keys {sorted(result)}"

    _step("public verification leaks no PII", verify_leaks_nothing, critical=False)

    # ------------------------------------------------------------- analytics
    def analytics_rows_exist():
        """The per-question report reads Training Attempt Question. Nothing wrote
        it, so the report answered every question with an empty set rather than an
        error -- which reads exactly like "no problems found"."""
        rows = frappe.get_all(
            "Training Attempt Question",
            filters={"attempt": state["attempt"]},
            fields=["question", "source", "is_correct", "given_answer", "question_text_snapshot"],
        )
        if not rows:
            raise Exception("no Training Attempt Question rows — the analytics report has no data")
        blank = [r for r in rows if not (r.given_answer or "").strip()]
        if blank:
            raise Exception(f"{len(blank)} row(s) recorded no given_answer")
        # The report names the dominant distractor by grouping on this text, so an
        # opaque option key here would make its most useful column unreadable.
        keyish = [r for r in rows if (r.given_answer or "").startswith("opt_")]
        if keyish:
            raise Exception("given_answer holds option keys, not the text the learner saw")
        return f"{len(rows)} answer row(s) filed"

    _step("per-question analytics rows were written", analytics_rows_exist, critical=False)

    def summary_counters():
        row = frappe.db.get_value(
            "Training Attempt",
            state["attempt"],
            ["quiz_runs", "checkpoints_answered", "checkpoints_correct"],
            as_dict=True,
        )
        if not cint(row.quiz_runs):
            raise Exception("quiz_runs is 0 after a quiz was taken and passed")
        return f"quiz_runs {row.quiz_runs}, checkpoints {row.checkpoints_correct}/{row.checkpoints_answered}"

    _step("attempt summary counters are populated", summary_counters, critical=False)

    # ---------------------------------------------------------- assignment closed
    def assignment_closed():
        status = frappe.db.get_value("Training Assignment", state.get("assignment"), "status")
        if status != "Completed":
            raise Exception(f"assignment is still {status!r} after a passing completion")
        return "assignment marked Completed"

    _step("the assignment closed out", assignment_closed, critical=False)

    return _report()


def _report():
    passed = sum(1 for r in _results if r[0] == "PASS")
    failed = [r for r in _results if r[0] == "FAIL"]
    print("\n" + "=" * 64)
    print(f"  {passed} passed, {len(failed)} failed")
    if failed:
        print("\n  Failures:")
        for _s, label, detail in failed:
            print(f"    - {label}\n        {detail}")
    print()
    return {"passed": passed, "failed": [f[1] for f in failed]}
