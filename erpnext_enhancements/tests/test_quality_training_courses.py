"""Bench-free tests for the four Quality course drafts (WI-075 training).

The failure these guard against is not a crash. It is **four Required courses landing in real
inboxes** before anybody has read them.

The plan assumed that could not happen: *"Training Settings ships dormant (training_enabled = 0,
auto_assign_enabled = 0). Four Required courses assign nothing and mail nobody while those are
off."* Measured on production 2026-09-14 that is no longer true — `training_enabled = 1`,
`auto_assign_enabled = 1`, `portal_enabled = 1`, against 14 live courses, 278 lessons and 123
questions. Training is in real use.

So the guards here are:

* **Every spec validates against the real validator**, not a copy of it. A spec that passes here
  survives `insert()` later; one that does not would abort a migrate.
* **Nothing in this branch publishes a course or sets a status.** `assignment.py` selects on
  `{"status": "Published", "weight": "Required", "auto_assign": 1}`, and Draft is the only one of
  those three this code does not set — which makes it load-bearing.
* **Every assignment rule points at a Position or Role that exists.** A rule naming something that
  does not matches nobody, silently, forever.
* **Every quiz question has exactly one defensible answer** and the lesson it sits in could have
  taught it.

The frappe stub is imported from `test_training_course_authoring` rather than copied, so the two
suites cannot drift about what the framework does. This suite therefore needs **its own CI step**
— `python -m unittest` shares a process, and two suites installing stubs would cross-talk.

Run: python -m unittest erpnext_enhancements.tests.test_quality_training_courses
"""

import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

course_spec = None
specs = None

PATCH = APP_ROOT / "patches" / "seed_quality_training_courses.py"
SPECS = APP_ROOT / "training" / "quality_course_specs.py"

#: Positions and Roles verified present on production 2026-09-14. `Technician` is the job-family
#: group above Junior / Senior / Master.
LIVE_POSITIONS = {
    "All Positions",
    "AP/AR, Purchasing Manager",
    "Chief Executive Officer",
    "Designer",
    "Electrical Designer",
    "Engineer",
    "Finance & Accounting Manager",
    "HR Manager",
    "Internal Systems Manager",
    "Junior Designer",
    "Junior Technician",
    "Marketing Specialist",
    "Master Technician",
    "Operations Manager",
    "Project Manager",
    "Purchasing Agent/Inventory Clerk",
    "Sales Representative",
    "Senior Technician",
    "Software Engineer",
    "Technician",
}

LIVE_ROLES = {"Production Team", "Sales Team", "Operations Team", "Executive Team", "Finance Team"}


def setUpModule():
    global course_spec, specs
    from erpnext_enhancements.tests.test_training_course_authoring import _install_stubs

    _install_stubs()
    from erpnext_enhancements.training import course_spec as spec_module
    from erpnext_enhancements.training import quality_course_specs as q

    course_spec = spec_module
    specs = q


def _raw(path):
    return path.read_text(encoding="utf-8")


def _code_only(path):
    src = re.sub(r'"""[\s\S]*?"""', "", _raw(path))
    return re.sub(r"(?m)^\s*#.*$", "", src)


# --------------------------------------------------------------------------------------------


class TestSpecsValidate(unittest.TestCase):
    def test_there_are_four_courses(self):
        """WI-075 names four, each authored after the feature it teaches."""
        self.assertEqual(len(specs.COURSES), 4)

    def test_every_spec_passes_the_real_validator(self):
        """Not a copy of it. A spec that validates here survives `insert()`; one that does not
        would abort a migrate, which on this repo is the deploy."""
        for spec in specs.COURSES:
            with self.subTest(spec["course"]["course_title"]):
                course_spec.validate_course_spec(spec)

    def test_every_course_title_is_unique(self):
        titles = specs.course_titles()
        self.assertEqual(len(titles), len(set(titles)))

    def test_every_course_is_required_and_internal(self):
        for spec in specs.COURSES:
            with self.subTest(spec["course"]["course_title"]):
                self.assertEqual(spec["course"]["weight"], "Required")
                self.assertEqual(spec["course"]["audience"], "Internal Staff")

    def test_no_course_is_visible_to_customers(self):
        """Internal quality process is not customer-facing content."""
        for spec in specs.COURSES:
            self.assertNotIn(spec["course"]["audience"], ("Customers", "Both"))

    def test_every_course_opens_with_the_draft_notice(self):
        """The learner is told what they are reading before they read it."""
        for spec in specs.COURSES:
            with self.subTest(spec["course"]["course_title"]):
                first = spec["lessons"][0]["blocks"][0]
                self.assertEqual(first["block_type"], "Callout")
                self.assertEqual(first["callout_tone"], "Warning")
                self.assertIn("draft", first["content"].lower())

    def test_the_notice_says_it_has_not_been_adopted(self):
        self.assertIn("not yet been reviewed or adopted", specs.DRAFT_NOTICE)

    def test_every_course_has_a_summary(self):
        for spec in specs.COURSES:
            self.assertTrue(spec["course"].get("summary", "").strip())


class TestQuestions(unittest.TestCase):
    def _questions(self):
        for spec in specs.COURSES:
            title = spec["course"]["course_title"]
            for lesson in spec["lessons"]:
                for q in (lesson.get("quiz") or {}).get("questions", []):
                    yield title, lesson["lesson_title"], q

    def test_there_are_questions_to_review(self):
        self.assertGreaterEqual(len(list(self._questions())), 15)

    def test_every_single_choice_question_has_exactly_one_answer(self):
        """More than one correct option on a Single Choice question is unanswerable, and a
        learner who picks the other right one is marked wrong."""
        for title, lesson, q in self._questions():
            if q["type"] != "Single Choice":
                continue
            correct = [o for o in q["options"] if o["is_correct"]]
            with self.subTest(f"{title} / {lesson}"):
                self.assertEqual(len(correct), 1, q["question"])

    def test_every_multiple_choice_question_has_more_than_one(self):
        """A Multiple Choice question with one answer should have been Single Choice."""
        for title, lesson, q in self._questions():
            if q["type"] != "Multiple Choice":
                continue
            correct = [o for o in q["options"] if o["is_correct"]]
            with self.subTest(f"{title} / {lesson}"):
                self.assertGreater(len(correct), 1, q["question"])

    def test_no_question_is_all_correct_or_none_correct(self):
        for title, lesson, q in self._questions():
            correct = [o for o in q["options"] if o["is_correct"]]
            with self.subTest(f"{title} / {lesson}"):
                self.assertGreater(len(correct), 0, q["question"])
                self.assertLess(len(correct), len(q["options"]), q["question"])

    def test_every_question_explains_its_answer(self):
        """A quiz that marks you wrong without saying why teaches nothing."""
        for title, lesson, q in self._questions():
            with self.subTest(f"{title} / {lesson}"):
                self.assertTrue(q.get("explanation", "").strip(), q["question"])

    def test_option_texts_are_distinct_within_a_question(self):
        for title, lesson, q in self._questions():
            texts = [o["text"] for o in q["options"]]
            with self.subTest(f"{title} / {lesson}"):
                self.assertEqual(len(texts), len(set(texts)), q["question"])


class TestAssignmentRules(unittest.TestCase):
    def test_every_course_has_rules(self):
        self.assertEqual(set(specs.course_titles()), set(specs.ASSIGNMENT_RULES))

    def test_every_rule_targets_something_that_exists(self):
        """A rule naming a Position or Role that does not exist matches nobody — silently, and
        forever. Checked against the live list measured 2026-09-14."""
        for title, rules in specs.ASSIGNMENT_RULES.items():
            for applies_to, value, _due in rules:
                with self.subTest(f"{title}: {applies_to} {value}"):
                    self.assertIn(applies_to, ("Position", "Role"))
                    known = LIVE_POSITIONS if applies_to == "Position" else LIVE_ROLES
                    self.assertIn(value, known)

    def test_the_field_course_targets_the_technician_family_not_one_tier(self):
        """`Technician` is the job-family group above Junior / Senior / Master, so one rule
        covers the family. Targeting `Senior Technician` would reach two people."""
        rules = specs.ASSIGNMENT_RULES["Running an inspection in the field"]
        self.assertIn(("Position", "Technician", 30), rules)

    def test_every_rule_carries_a_due_window(self):
        for title, rules in specs.ASSIGNMENT_RULES.items():
            for _applies_to, value, due in rules:
                with self.subTest(f"{title}: {value}"):
                    self.assertGreater(due, 0)

    def test_only_the_field_course_requires_a_signature(self):
        """A practical competency. Passing a quiz about the wizard is not evidence somebody can
        run an inspection."""
        self.assertEqual(
            specs.REQUIRES_SIGNOFF, ("Running an inspection in the field",)
        )


class TestNothingPublishes(unittest.TestCase):
    """The load-bearing guard. `assignment.py` selects on status Published AND weight Required
    AND auto_assign; this branch sets the last two, so Draft is the only thing holding."""

    def test_the_patch_never_sets_a_status(self):
        source = _code_only(PATCH)
        self.assertNotIn('"status"', source)
        self.assertNotIn("Published", source)
        self.assertNotIn(".status =", source)

    def test_the_specs_never_set_a_status(self):
        source = _code_only(SPECS)
        self.assertNotIn("Published", source)

    def test_the_patch_is_insert_only(self):
        """It must not reset a course somebody has corrected, and must never un-publish one
        somebody has adopted."""
        source = _code_only(PATCH)
        self.assertIn("frappe.db.exists", source)
        self.assertIn("continue", source)

    def test_the_patch_builds_through_the_reviewed_path(self):
        """So every quiz question is stamped ai_generated with no reviewer, and the publish gate
        holds. These questions were machine-written and carry the same gate as any other."""
        source = _code_only(PATCH)
        self.assertIn("author_course_from_spec", source)

    def test_the_patch_cannot_abort_the_deploy(self):
        """A patch that raises aborts `bench migrate`, which on this repo IS the deploy — and
        leaves it half-finished at the new version string."""
        source = _code_only(PATCH)
        self.assertIn("return", source.split("def execute(")[1][:400])
        self.assertIn("except Exception", source)

    def test_a_dead_assignment_rule_is_skipped_loudly(self):
        source = _code_only(PATCH)
        entry = source.split("def _apply_course_settings(", 1)[1]
        self.assertIn("_target_exists", entry)
        self.assertIn("log_error", entry)

    def test_the_patch_is_registered(self):
        self.assertIn(
            "erpnext_enhancements.patches.seed_quality_training_courses",
            _raw(APP_ROOT / "patches.txt"),
        )

    def test_the_module_records_that_training_is_live(self):
        """The plan's guardrail was dormancy, and dormancy is gone. If that reasoning is not
        written down, the next person reads the plan and believes it."""
        source = _raw(SPECS)
        self.assertIn("training_enabled = 1", source)
        self.assertIn("no longer true", source)


class TestContentSafety(unittest.TestCase):
    def test_no_course_invents_a_company_deadline(self):
        """The strawman lesson: teaching how the software behaves is knowable from the code;
        asserting how quickly Sapphire requires something is not."""
        banned = re.compile(
            r"\b(?:within|inside|no later than)\s+\d+\s*(?:hour|hours|day|days|week|weeks)\b",
            re.I,
        )
        for spec in specs.COURSES:
            for lesson in spec["lessons"]:
                for block in lesson["blocks"]:
                    text = block.get("content") or ""
                    with self.subTest(f"{spec['course']['course_title']} / {lesson['lesson_title']}"):
                        self.assertIsNone(banned.search(text), text[:160])

    def test_the_scorecard_lesson_warns_against_reading_a_blank_as_good(self):
        """The single most dangerous misreading in the whole programme."""
        course = specs.SUBCONTRACTOR_AGREEMENTS
        text = " ".join(
            (b.get("content") or "")
            for lesson in course["lessons"]
            for b in lesson["blocks"]
        )
        self.assertIn("Not Measurable", text)
        self.assertIn("not zero", text.lower())

    def test_the_field_course_teaches_that_the_checklist_is_frozen(self):
        course = specs.FIELD_INSPECTION
        text = " ".join(
            (b.get("content") or "")
            for lesson in course["lessons"]
            for b in lesson["blocks"]
        )
        self.assertIn("copied", text.lower())

    def test_the_ncr_course_teaches_that_resolved_is_not_verified(self):
        course = specs.NCR_AND_ACTION
        text = " ".join(
            (b.get("content") or "")
            for lesson in course["lessons"]
            for b in lesson["blocks"]
        )
        self.assertIn("PM Resolved", text)


if __name__ == "__main__":
    unittest.main()
