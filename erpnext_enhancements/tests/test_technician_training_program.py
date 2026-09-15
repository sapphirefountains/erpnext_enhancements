"""Bench-free tests for the ten Technician Program course drafts and their badges.

Two different failures are guarded here, and they are not the same shape.

The first is the one the Quality drafts already taught us: **ten Required courses landing in real
inboxes** before anybody has read them. `assignment.py` selects on `{"status": "Published", "weight":
"Required", "auto_assign": 1}`, this branch sets the last two and carries a rule aimed at the
Technician job family, so `Draft` is the only thing holding. Measured on production 2026-09-15:
`training_enabled = 1`, `notifications_enabled = 1`, `gamification_enabled = 1`,
`auto_assign_enabled = 0`, `portal_enabled = 0`, against 18 live courses — and `auto_assign_enabled`
was 1 the day before, so it is a checkbox, not a guardrail.

The second is specific to a **trade** course and the Quality suite has no equivalent of it. Those
four could be written from the code, because what they teach is how this software behaves. These
teach solvent welding, chemical handling, confined space entry and anchor setting, and the failure
mode is a technician reading a cure time or a dose rate here, not checking the label, and making a
joint that fails under a slab in three years. So the content guards below are about **invented
numbers and invented company policy**, not only about invented deadlines.

The frappe stub is imported from `test_training_course_authoring` rather than copied, so the two
suites cannot drift about what the framework does. This suite therefore needs **its own CI step** —
`python -m unittest` shares a process, and two suites installing stubs would cross-talk.

Run: python -m unittest erpnext_enhancements.tests.test_technician_training_program
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

course_spec = None
program = None
setup = None

PATCH = APP_ROOT / "patches" / "seed_technician_training_program.py"
PKG = APP_ROOT / "training" / "technician_program"
BADGE_JSON = APP_ROOT / "training" / "doctype" / "training_badge" / "training_badge.json"
PUBLIC_BADGES = APP_ROOT / "public" / "images" / "training" / "badges"

#: Positions verified present on production 2026-09-15. `Technician` is the job-family group above
#: Junior / Senior / Master, which is why one rule per course covers the family.
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

#: Training Categories present on production 2026-09-15. `author_course_from_spec` **drops** a
#: category that does not exist rather than failing, so a typo here is silent: the course is created
#: with no category at all and simply never appears under the filter somebody browses by.
LIVE_CATEGORIES = {
    "Customer Handover",
    "ERPNext System Training",
    "Installation",
    "Safety",
    "Service & Maintenance",
    "Systems & Admin",
    "Water Chemistry",
}

#: The ten modules of Sapphire's outline and how many numbered topics each carried. One topic is one
#: lesson, so this is the shape of the thing that was asked for — pinned so that a module quietly
#: losing a lesson in a later edit fails the build instead of disappearing.
OUTLINE = (
    ("Technician Module 1 — Piping & Hydraulics", 5),
    ("Technician Module 2 — Aquatic System Equipment Installation", 10),
    ("Technician Module 3 — Water Chemistry", 5),
    ("Technician Module 4 — Materials of Water Features", 6),
    ("Technician Module 5 — Waterproofing", 3),
    ("Technician Module 6 — Electrical Components, Automation & Field Diagnostics", 9),
    ("Technician Module 7 — Service Operations", 3),
    ("Technician Module 8 — General Troubleshooting", 10),
    ("Technician Module 9 — Jobsite Safety, Best Practices & Code Requirements", 9),
    ("Technician Module 10 — Basic Design & Project Management Principles", 12),
)

MODULE_FILES = (
    "module_01_piping",
    "module_02_equipment",
    "module_03_water_chemistry",
    "module_04_materials",
    "module_05_waterproofing",
    "module_06_electrical",
    "module_07_service_ops",
    "module_08_troubleshooting",
    "module_09_safety",
    "module_10_design_pm",
)


def setUpModule():
    global course_spec, program, setup
    from erpnext_enhancements.tests.test_training_course_authoring import _install_stubs

    _install_stubs()
    from erpnext_enhancements.training import course_spec as spec_module
    from erpnext_enhancements.training import setup as setup_module
    from erpnext_enhancements.training import technician_program as p

    course_spec = spec_module
    program = p
    setup = setup_module


def _raw(path):
    return path.read_text(encoding="utf-8")


def _code_only(path):
    src = re.sub(r'"""[\s\S]*?"""', "", _raw(path))
    return re.sub(r"(?m)^\s*#.*$", "", src)


def _without_docstrings(src):
    """Blank out docstring bodies while keeping the line count, so a reported line number is real.

    The tab rule is about *indentation*. Prose inside a docstring indents for its own reasons — a
    bullet list, a hanging indent under a field — and rewriting English to satisfy a whitespace
    check would be the check driving the writing.
    """
    return re.sub(r'"""[\s\S]*?"""', lambda m: "\n" * m.group(0).count("\n"), src)


def _all_text(spec):
    """Every rendered string in a course: block content, headings, list items, cards, panels."""
    out = []
    for lesson in spec["lessons"]:
        for block in lesson["blocks"]:
            out.append(block.get("heading") or "")
            out.append(block.get("content") or "")
            out.extend(block.get("items") or [])
            for card in block.get("cards") or []:
                out.extend((card.get("front", ""), card.get("back", "")))
            for panel in block.get("panels") or []:
                out.extend((panel.get("title", ""), panel.get("body", "")))
    return "\n".join(out)


# --------------------------------------------------------------------------------------------


class TestSpecsValidate(unittest.TestCase):
    def test_there_are_ten_courses(self):
        """One per module of the outline Sapphire supplied."""
        self.assertEqual(len(program.COURSES), 10)

    def test_every_spec_passes_the_real_validator(self):
        """Not a copy of it. A spec that validates here survives `insert()`; one that does not would
        abort a migrate, which on this repo is the deploy."""
        for spec in program.COURSES:
            with self.subTest(spec["course"]["course_title"]):
                course_spec.validate_course_spec(spec)

    def test_the_courses_match_the_outline(self):
        """Title and lesson count, in order. Seventy-two numbered topics became seventy-two
        lessons; a module quietly losing one should fail here rather than vanish."""
        actual = tuple(
            (spec["course"]["course_title"], len(spec["lessons"])) for spec in program.COURSES
        )
        self.assertEqual(actual, OUTLINE)

    def test_seventy_two_lessons_in_total(self):
        self.assertEqual(sum(len(spec["lessons"]) for spec in program.COURSES), 72)

    def test_every_course_title_is_unique(self):
        titles = program.course_titles()
        self.assertEqual(len(titles), len(set(titles)))

    def test_no_title_collides_with_a_course_already_on_the_site(self):
        """The patch is keyed on `course_title`. A title matching one of the 18 courses measured on
        production 2026-09-15 would make the patch skip a module it never created."""
        live = {
            "A Maintenance Visit, End to End",
            "Accounting in ERPNext",
            "Confined space entry — before you get in",
            "Draining a Fountain Basin Safely",
            "How Sapphire Runs on ERPNext",
            "Non-conformances and corrective actions, end to end",
            "Reading the Numbers: ERPNext for Owners and Managers",
            "Running an inspection in the field",
            "Sales and Customers in ERPNext",
            "Security, and the device in your pocket",
            "Subcontractor agreements, purchase orders and rates",
            "Time, jobs and getting paid right",
            "Using the Training Module",
            "Water chemistry that keeps a feature clear",
            "Working alone, and being missed if something happens",
            "Writing scope that can be inspected",
            "Your first week at Sapphire",
            "Your vehicle, your trailer, your responsibility",
        }
        self.assertEqual(set(program.course_titles()) & live, set())

    def test_every_course_is_required_and_internal(self):
        for spec in program.COURSES:
            with self.subTest(spec["course"]["course_title"]):
                self.assertEqual(spec["course"]["weight"], "Required")
                self.assertEqual(spec["course"]["audience"], "Internal Staff")

    def test_no_course_is_visible_to_customers(self):
        """Internal technician training is not customer-facing content."""
        for spec in program.COURSES:
            self.assertNotIn(spec["course"]["audience"], ("Customers", "Both"))

    def test_every_category_exists_on_the_site(self):
        """`author_course_from_spec` drops an unknown category silently, so a typo produces a course
        with no category rather than an error."""
        for spec in program.COURSES:
            with self.subTest(spec["course"]["course_title"]):
                self.assertIn(spec["course"]["category"], LIVE_CATEGORIES)

    def test_every_course_has_a_summary(self):
        for spec in program.COURSES:
            self.assertTrue(spec["course"].get("summary", "").strip())

    def test_every_course_opens_with_the_draft_notice(self):
        """The learner is told what they are reading before they read it."""
        for spec in program.COURSES:
            with self.subTest(spec["course"]["course_title"]):
                first = spec["lessons"][0]["blocks"][0]
                self.assertEqual(first["block_type"], "Callout")
                self.assertEqual(first["callout_tone"], "Warning")
                self.assertIn("draft", first["content"].lower())

    def test_the_notice_says_it_has_not_been_adopted(self):
        self.assertIn("not yet been reviewed or adopted", program.DRAFT_NOTICE)

    def test_the_notice_sends_the_reader_to_the_label_for_real_numbers(self):
        """The guard that matters for a trade course. Somebody who takes a cure time or a dose rate
        from a training slide and does not check the product is the failure this whole package is
        trying not to cause, so the first thing every course says is where the real figure lives."""
        notice = program.DRAFT_NOTICE
        self.assertIn("where to read it", notice)
        for source in ("label", "data sheet", "specification"):
            self.assertIn(source, notice)

    def test_the_notice_says_it_does_not_replace_certified_training(self):
        notice = program.DRAFT_NOTICE.lower()
        self.assertIn("does not replace certified training", notice)
        for topic in ("confined space", "lock-out", "first aid"):
            self.assertIn(topic, notice)

    def test_every_lesson_is_complete(self):
        """A lesson with no summary, no estimate or no content is a lesson somebody has to open to
        discover is empty."""
        for spec in program.COURSES:
            for lesson in spec["lessons"]:
                with self.subTest(f"{spec['course']['course_title']} / {lesson['lesson_title']}"):
                    self.assertTrue(lesson.get("summary", "").strip())
                    self.assertGreater(lesson.get("estimated_minutes", 0), 0)
                    self.assertGreaterEqual(len(lesson["blocks"]), 3)

    def test_no_course_uses_chapters(self):
        """A course is its lessons, in order — no grouping layer.

        Chapters are a real feature of the model and these ten deliberately do not use one. The
        outline already names the module, the module IS the course, and a second level of grouping
        inside it only gave the learner a heading between them and the next lesson.

        Asserted rather than left implicit, because the seeding and rebuild paths both hand
        `spec["chapters"]` straight to `_apply_chapters`: a chapter reintroduced here would appear
        in the outline with no warning, and a lesson carrying a stale `chapter` index would be
        refused by the validator with a message about a chapter count."""
        for spec in program.COURSES:
            with self.subTest(spec["course"]["course_title"]):
                self.assertEqual(spec.get("chapters") or [], [])
                for lesson in spec["lessons"]:
                    self.assertNotIn("chapter", lesson, lesson["lesson_title"])

    def test_the_rebuild_clears_chapters_a_draft_already_has(self):
        """`_apply_chapters` returns early on an empty list — right for a new version, wrong for a
        rebuild, where the draft on production still holds the chapters seeded in v1.467.0. Without
        this the lessons referencing them are deleted and the empty groups survive."""
        src = _raw(APP_ROOT / "api" / "training_course_authoring.py")
        rebuild = src.split("def rebuild_draft_from_spec(", 1)[1]
        self.assertIn('{"chapters": []}', rebuild)

    def test_every_module_file_is_in_the_package(self):
        for name in MODULE_FILES:
            with self.subTest(name):
                self.assertTrue((PKG / f"{name}.py").exists())

    def test_every_module_file_uses_tabs(self):
        """`ruff format` is configured for tabs and the whole `training/` package uses them."""
        for name in (*MODULE_FILES, "__init__", "_common"):
            path = PKG / f"{name}.py"
            for i, line in enumerate(_without_docstrings(_raw(path)).split("\n"), 1):
                if line.startswith(" "):
                    self.fail(f"{name}.py line {i} is space-indented: {line[:60]!r}")


class TestEveryLessonHasAQuiz(unittest.TestCase):
    """The customer asked for quizzes as well as lessons. A lesson without one is silently just
    reading — the player shows no assessment and the completion records no score."""

    def test_every_lesson_has_a_quiz(self):
        for spec in program.COURSES:
            for lesson in spec["lessons"]:
                with self.subTest(f"{spec['course']['course_title']} / {lesson['lesson_title']}"):
                    quiz = lesson.get("quiz") or {}
                    self.assertTrue(quiz.get("questions"), "lesson has no quiz")

    def test_every_quiz_has_at_least_two_questions(self):
        for spec in program.COURSES:
            for lesson in spec["lessons"]:
                with self.subTest(f"{spec['course']['course_title']} / {lesson['lesson_title']}"):
                    self.assertGreaterEqual(len((lesson["quiz"] or {})["questions"]), 2)

    def test_no_quiz_sets_its_own_pass_score(self):
        """0 inherits the course setting. A model — or a package like this one — should not be
        silently deciding what score counts as competent at confined space entry."""
        for spec in program.COURSES:
            for lesson in spec["lessons"]:
                with self.subTest(f"{spec['course']['course_title']} / {lesson['lesson_title']}"):
                    self.assertFalse((lesson["quiz"] or {}).get("pass_score"))


class TestQuestions(unittest.TestCase):
    def _questions(self):
        for spec in program.COURSES:
            title = spec["course"]["course_title"]
            for lesson in spec["lessons"]:
                for q in (lesson.get("quiz") or {}).get("questions", []):
                    yield title, lesson["lesson_title"], q

    def test_there_are_questions_to_review(self):
        """Every one of these is stamped `ai_generated` with no reviewer, so this is also the size
        of the review somebody owes before any of it can be published."""
        self.assertGreaterEqual(len(list(self._questions())), 200)

    def test_every_single_choice_question_has_exactly_one_answer(self):
        """More than one correct option on a Single Choice question is unanswerable, and a learner
        who picks the other right one is marked wrong."""
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

    def test_every_true_false_question_offers_exactly_true_and_false(self):
        for title, lesson, q in self._questions():
            if q["type"] != "True-False":
                continue
            with self.subTest(f"{title} / {lesson}"):
                self.assertEqual(
                    sorted(o["text"].lower() for o in q["options"]), ["false", "true"], q["question"]
                )

    def test_no_question_is_all_correct_or_none_correct(self):
        for title, lesson, q in self._questions():
            correct = [o for o in q["options"] if o["is_correct"]]
            with self.subTest(f"{title} / {lesson}"):
                self.assertGreater(len(correct), 0, q["question"])
                self.assertLess(len(correct), len(q["options"]), q["question"])

    def test_every_question_explains_its_answer(self):
        """A quiz that marks you wrong without saying why teaches nothing — and the review screen
        is the one moment somebody is looking at a wrong answer of their own and asking why."""
        for title, lesson, q in self._questions():
            with self.subTest(f"{title} / {lesson}"):
                self.assertTrue(q.get("explanation", "").strip(), q["question"])

    def test_option_texts_are_distinct_within_a_question(self):
        for title, lesson, q in self._questions():
            texts = [o["text"].strip().lower() for o in q["options"]]
            with self.subTest(f"{title} / {lesson}"):
                self.assertEqual(len(texts), len(set(texts)), q["question"])

    def test_option_counts_are_within_the_controller_bounds(self):
        """`TrainingQuestion` enforces these on insert; a spec that passes here survives it."""
        for title, lesson, q in self._questions():
            with self.subTest(f"{title} / {lesson}"):
                self.assertGreaterEqual(len(q["options"]), course_spec.MIN_OPTIONS, q["question"])
                self.assertLessEqual(len(q["options"]), course_spec.MAX_OPTIONS, q["question"])


class TestBadges(unittest.TestCase):
    """One badge per module, which is what was asked for."""

    def test_one_badge_per_course(self):
        self.assertEqual(len(program.badges()), len(program.COURSES))

    def test_every_badge_names_a_course_that_exists_in_the_specs(self):
        known = set(program.course_titles())
        for course_title, badge_name, _description, _points, _image in program.badges():
            with self.subTest(badge_name):
                self.assertIn(course_title, known)

    def test_badge_names_are_unique(self):
        names = [b[1] for b in program.badges()]
        self.assertEqual(len(names), len(set(names)))

    def test_no_badge_name_collides_with_a_starter_badge(self):
        """`Training Badge` autonames on `badge_name` and the field is unique, so a collision is not
        cosmetic — it is the patch silently failing to create that badge."""
        starters = {b[0] for b in setup.STARTER_BADGES}
        self.assertEqual({b[1] for b in program.badges()} & starters, set())

    def test_no_badge_name_collides_with_one_already_on_the_site(self):
        """The names carry no programme prefix, at Sapphire's request. That reads better on a
        profile and puts ten plain, generic names into a namespace that is shared and **unique** —
        so this is the check the prefix used to be. Measured against production 2026-09-15, where
        the only five Training Badges are the starters."""
        live = {"First Course", "Full Marks", "Five Courses", "Ten Courses", "Steady Week"}
        self.assertEqual({b[1] for b in program.badges()} & live, set())

    def test_no_badge_name_is_also_a_course_title(self):
        """`Water Chemistry` is a badge, a Training Category and very nearly a course title. Two of
        those are different doctypes and cannot collide; a badge named exactly like one of these ten
        courses would just be confusing on screen."""
        self.assertEqual({b[1] for b in program.badges()} & set(program.course_titles()), set())

    def test_every_badge_points_at_artwork_that_exists(self):
        """An `Attach Image` holding a path to nothing renders as an empty box — no broken-image
        icon, no error, nothing in the log. The only way to know is to check the file is there."""
        for _course, badge_name, _description, _points, image in program.badges():
            with self.subTest(badge_name):
                self.assertTrue(image.startswith(program.BADGE_IMAGE_BASE + "/"), image)
                filename = image.rsplit("/", 1)[-1]
                self.assertTrue(
                    (PUBLIC_BADGES / filename).exists(), f"{badge_name}: no {filename} on disk"
                )

    def test_every_badge_has_its_own_artwork(self):
        images = [b[4] for b in program.badges()]
        self.assertEqual(len(images), len(set(images)))

    def test_the_artwork_is_plain_vector_with_nothing_fetched_at_render(self):
        """These ship as static app assets and are rendered in an `<img>` by the player. A script,
        an external reference or an embedded raster in one would be a surprise arriving through an
        image field — and the SVGs were chosen over the PNGs partly because they are inspectable."""
        for svg in sorted(PUBLIC_BADGES.glob("*.svg")):
            body = svg.read_text(encoding="utf-8")
            with self.subTest(svg.name):
                for forbidden in ("<script", "xlink:href=\"http", "href=\"http", "<image", "<foreignObject"):
                    self.assertNotIn(forbidden, body, f"{svg.name} contains {forbidden}")

    def test_the_patch_writes_the_image(self):
        source = _code_only(PATCH)
        self.assertIn('"image": image', source)

    def test_every_badge_says_what_somebody_did_to_earn_it(self):
        """The DocType's own guidance: say what somebody did, not what the badge is — the learner
        already knows they have it."""
        for _course, badge_name, description, _points, _image in program.badges():
            with self.subTest(badge_name):
                self.assertTrue(description.strip())
                self.assertIn("Finished", description)

    def test_points_are_derived_from_the_lesson_count(self):
        """Hand-typed points drift. The modules are three lessons to twelve, so a flat figure would
        price an afternoon and a fortnight the same."""
        by_title = {s["course"]["course_title"]: s for s in program.COURSES}
        for course_title, badge_name, _description, points, _image in program.badges():
            with self.subTest(badge_name):
                expected = program.POINTS_PER_LESSON * len(by_title[course_title]["lessons"])
                self.assertEqual(points, expected)
                self.assertGreater(points, 0)

    def test_the_patch_awards_on_course_completion(self):
        """`Course Completed` is the only criterion in `gamification._badge_is_earned` that means
        *this specific course*. A count-based criterion would be satisfied by any other course on
        the site, so 'a badge for each module' would become 'a badge for finishing anything'."""
        source = _code_only(PATCH)
        self.assertIn('"criteria_type": "Course Completed"', source)
        self.assertIn('"criteria_course"', source)

    def test_that_criterion_and_that_field_exist_on_the_doctype(self):
        """Pins the patch against the DocType rather than against a memory of it. An unrecognised
        `criteria_type` awards nothing — `_badge_is_earned` returns False for anything it cannot
        answer — so a drifted literal produces ten badges nobody can ever earn, silently."""
        doctype = json.loads(_raw(BADGE_JSON))
        fields = {f["fieldname"]: f for f in doctype["fields"]}
        self.assertIn("Course Completed", fields["criteria_type"]["options"].split("\n"))
        self.assertEqual(fields["criteria_course"]["options"], "Training Course")
        self.assertEqual(fields["badge_name"]["unique"], 1)

    def test_the_badge_seed_is_separate_from_the_course_loop(self):
        """So a re-run after a partial failure gives a badge to a course that already exists. Keyed
        on badge name, not on whether the course was created this time."""
        source = _code_only(PATCH)
        entry = source.split("def _seed_badges(", 1)[1]
        self.assertIn("frappe.db.exists(BADGE_DOCTYPE, badge_name)", entry)
        self.assertIn("continue", entry)

    def test_a_badge_is_not_created_without_its_course(self):
        """A badge with an empty `criteria_course` is earnable by nobody and obviously broken to
        nobody, which is the worst of both."""
        source = _code_only(PATCH)
        entry = source.split("def _seed_badges(", 1)[1]
        self.assertIn("if not course:", entry)
        self.assertIn("log_error", entry)

    def test_the_badge_doctype_may_not_have_migrated_yet(self):
        """Same guard `training/setup.py` carries for the starter badges, and for the same reason."""
        source = _code_only(PATCH)
        self.assertIn('frappe.db.exists("DocType", BADGE_DOCTYPE)', source)


class TestAssignmentRules(unittest.TestCase):
    def test_every_course_has_exactly_one_rule(self):
        rules = program.assignment_rules()
        self.assertEqual(len(rules), len(program.COURSES))
        self.assertEqual({r[0] for r in rules}, set(program.course_titles()))

    def test_every_rule_targets_a_position_that_exists(self):
        """A rule naming a Position that does not exist matches nobody — silently, and forever.
        Checked against the live list measured 2026-09-15."""
        for course_title, applies_to, value, _due in program.assignment_rules():
            with self.subTest(course_title):
                self.assertEqual(applies_to, "Position")
                self.assertIn(value, LIVE_POSITIONS)

    def test_the_rules_target_the_technician_family_not_one_tier(self):
        """`Technician` is the job-family group above Junior / Senior / Master, so one rule covers
        the family. Targeting `Senior Technician` would reach a handful of people."""
        self.assertEqual(program.ASSIGNMENT_POSITION, "Technician")

    def test_the_due_ladder_encodes_the_order_of_the_programme(self):
        """Module 1 before Module 10. The pace is whoever adopts these to set; what is recorded is
        the sequence."""
        due = [r[3] for r in program.assignment_rules()]
        self.assertEqual(due, sorted(due))
        self.assertEqual(len(set(due)), len(due))
        for value in due:
            self.assertGreater(value, 0)

    def test_the_patch_skips_a_rule_whose_target_is_missing(self):
        source = _code_only(PATCH)
        entry = source.split("def _apply_course_settings(", 1)[1]
        self.assertIn("_target_exists", entry)
        self.assertIn("log_error", entry)

    def test_the_patch_asks_for_no_signature_it_cannot_describe(self):
        """`TrainingCourse._validate_signoff` refuses the flag without a criterion — *"a sign-off
        with no stated criterion is a signature on nothing"*. v1.464.0 set the flag alone on a
        sibling patch, the controller threw, and because a patch that raises aborts `bench migrate`
        that took the whole production deploy down.

        Several of these modules describe competencies a quiz cannot prove, and a sign-off is the
        right instrument for them — but what a Sapphire supervisor is verifying is exactly the kind
        of thing this package does not get to invent. So it sets neither half."""
        source = _code_only(PATCH)
        self.assertNotIn("require_supervisor_signoff", source)
        self.assertNotIn("signoff_instructions", source)


class TestNothingPublishes(unittest.TestCase):
    """The load-bearing guard. `assignment.py` selects on status Published AND weight Required AND
    auto_assign; this branch sets the last two, so Draft is the only thing holding."""

    def test_the_patch_never_sets_a_status(self):
        source = _code_only(PATCH)
        self.assertNotIn('"status"', source)
        self.assertNotIn("Published", source)
        self.assertNotIn(".status =", source)

    def test_no_spec_module_ever_names_the_published_state(self):
        for name in (*MODULE_FILES, "__init__", "_common"):
            with self.subTest(name):
                self.assertNotIn("Published", _code_only(PKG / f"{name}.py"))

    def test_the_patch_is_insert_only(self):
        """It must not reset a course somebody has corrected, and must never un-publish one somebody
        has adopted."""
        source = _code_only(PATCH)
        self.assertIn("frappe.db.get_value(\"Training Course\", {\"course_title\": title}", source)
        self.assertIn("continue", source)

    def test_the_patch_builds_through_the_reviewed_path(self):
        """So every one of the 200-plus quiz questions is stamped ai_generated with no reviewer, and
        the publish gate holds. These were machine-written and carry the same gate as any other."""
        source = _code_only(PATCH)
        self.assertIn("author_course_from_spec", source)

    def test_the_patch_cannot_abort_the_deploy(self):
        """A patch that raises aborts `bench migrate`, which on this repo IS the deploy — and leaves
        it half-finished at the new version string."""
        source = _code_only(PATCH)
        self.assertIn("return", source.split("def execute(")[1][:400])
        self.assertIn("except Exception", source)

    def test_the_patch_is_registered(self):
        self.assertIn(
            "erpnext_enhancements.patches.seed_technician_training_program",
            _raw(APP_ROOT / "patches.txt"),
        )

    def test_it_runs_after_the_quality_seed(self):
        """Not a dependency, but the order is the order the courses were written in and the patch
        log reads better for it."""
        registered = _raw(APP_ROOT / "patches.txt")
        self.assertLess(
            registered.index("patches.seed_quality_training_courses"),
            registered.index("patches.seed_technician_training_program"),
        )

    def test_the_package_records_what_production_actually_says(self):
        """The Quality drafts leaned on `auto_assign_enabled = 1` being the alarming fact. A day
        later it reads 0 — and `gamification_enabled` reads 1, which is the new one. If that is not
        written down, the next person reads the older module and believes it."""
        source = _raw(PKG / "__init__.py")
        self.assertIn("2026-09-15", source)
        self.assertIn("gamification_enabled = 1", source)
        self.assertIn("auto_assign_enabled = 0", source)


class TestPatchCannotAbortAMigrate(unittest.TestCase):
    """The test that should have existed before v1.464.0.

    `TestNothingPublishes.test_the_patch_cannot_abort_the_deploy` greps the source for
    `except Exception` and a `return`. On the sibling patch both were present, it passed, and the
    patch still aborted a production migrate — because the call that threw sat *outside* the try
    block the grep found. A source check cannot see control flow.

    So this one runs `execute()` for real against a fake frappe, with the failure injected at each
    of the two places it could actually happen, and asserts nothing propagates.
    """

    def _run_execute(self, settings_raises=False, insert_raises=False):
        import importlib
        import types

        logged = []
        inserted = []

        courses = len(program.COURSES)

        class _FakeDB:
            """The two `get_value` call sites are byte-identical — `execute()` asking whether a
            course exists yet, and `_seed_badges` resolving the course it is about to point a badge
            at — so they cannot be told apart by their arguments. They are told apart by order:
            `execute()` makes all of its calls before `_seed_badges` makes any."""

            def __init__(self):
                self.lookups = 0

            def exists(self, doctype, *a, **k):
                # True for the DocType guards at the top of execute() and in _seed_badges, so both
                # halves actually run. False for "does this badge already exist".
                return doctype == "DocType"

            def get_value(self, doctype, *a, **k):
                self.lookups += 1
                if self.lookups <= courses:
                    return None  # nothing exists yet, so every course is built
                return "TRN-CRS-00001"  # the badge seed finds the course it just created

            def commit(self):
                pass

        class _FakeDoc:
            def insert(self, **k):
                if insert_raises:
                    raise RuntimeError("the badge would not insert")
                inserted.append(True)

        fake = types.ModuleType("frappe")
        fake.db = _FakeDB()
        fake.log_error = lambda **kw: logged.append(kw)
        fake.get_traceback = lambda: "traceback"
        # Only ever reached from `_seed_badges` here — `_apply_course_settings` is stubbed out.
        fake.get_doc = lambda *a, **k: _FakeDoc()

        authoring = types.ModuleType("erpnext_enhancements.api.training_course_authoring")
        authoring.author_course_from_spec = lambda spec: {"course": "TRN-CRS-00001"}

        keys = ("frappe", "erpnext_enhancements.api.training_course_authoring")
        saved = {k: sys.modules.get(k) for k in keys}
        sys.modules["frappe"] = fake
        sys.modules["erpnext_enhancements.api.training_course_authoring"] = authoring
        try:
            mod = importlib.reload(
                importlib.import_module("erpnext_enhancements.patches.seed_technician_training_program")
            )
            if settings_raises:
                mod._apply_course_settings = lambda name, title: (_ for _ in ()).throw(
                    RuntimeError("the controller refused the save")
                )
            else:
                mod._apply_course_settings = lambda name, title: None
            raised = None
            try:
                mod.execute()
            except Exception as exc:
                raised = exc
            return raised, logged, inserted
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

    def test_a_failure_applying_course_settings_never_propagates(self):
        """The exact v1.464.0 failure, reproduced on this patch. Swallowed and logged, once per
        course, and the other nine still get built."""
        raised, logged, _inserted = self._run_execute(settings_raises=True)
        self.assertIsNone(raised, f"execute() propagated {raised!r} and would abort the migrate")
        self.assertEqual(len(logged), len(program.COURSES))

    def test_the_badges_are_still_seeded_when_every_course_settings_call_fails(self):
        """The badge seed is deliberately outside the course loop, so a bad afternoon for the
        courses does not also cost the badges — and a later re-run gives a badge to a course that
        already exists."""
        _raised, _logged, inserted = self._run_execute(settings_raises=True)
        self.assertEqual(len(inserted), len(program.badges()))

    def test_a_badge_that_will_not_insert_never_propagates(self):
        """The second place this patch could abort a migrate. Badges are decoration; a deploy is
        not — the same rule `training/setup.py` keeps for the starter badges."""
        raised, logged, inserted = self._run_execute(insert_raises=True)
        self.assertIsNone(raised, f"execute() propagated {raised!r} and would abort the migrate")
        self.assertEqual(inserted, [])
        self.assertEqual(len(logged), len(program.badges()))

    def test_the_happy_path_logs_nothing_and_seeds_everything(self):
        raised, logged, inserted = self._run_execute()
        self.assertIsNone(raised)
        self.assertEqual(logged, [])
        self.assertEqual(len(inserted), len(program.badges()))


class TestContentSafety(unittest.TestCase):
    """The guards specific to a trade course.

    The Quality drafts could be written from the code. These teach solvent welding, chemical
    handling and confined space entry, and the dangerous failure is not a wrong claim about a screen
    — it is a plausible number somebody acts on.
    """

    def test_no_course_invents_a_company_deadline(self):
        """Teaching how a solvent weld works is knowable. Asserting how quickly Sapphire requires
        something is not."""
        banned = re.compile(
            r"\b(?:within|inside|no later than)\s+\d+\s*(?:hour|hours|day|days|week|weeks)\b", re.I
        )
        for spec in program.COURSES:
            hit = banned.search(_all_text(spec))
            with self.subTest(spec["course"]["course_title"]):
                self.assertIsNone(hit, hit.group(0) if hit else "")

    def test_no_course_invents_a_service_frequency(self):
        """'Clean the filter every 6 weeks' is a policy nobody at Sapphire has set, and it reads as
        one that has been. The courses teach the clean-pressure baseline instead, which is the
        answer that is true on every site."""
        banned = re.compile(
            r"\bevery\s+\d+\s*(?:day|days|week|weeks|month|months|year|years)\b", re.I
        )
        for spec in program.COURSES:
            hit = banned.search(_all_text(spec))
            with self.subTest(spec["course"]["course_title"]):
                self.assertIsNone(hit, hit.group(0) if hit else "")

    def test_every_course_sends_at_least_one_decision_back_to_the_site(self):
        """`ask_block` is how a lesson says 'the label / the engineer / your supervisor decides'.
        A whole module that never reaches that edge has almost certainly answered something it
        should have deferred."""
        for name in MODULE_FILES:
            with self.subTest(name):
                self.assertIn("ask_block(", _raw(PKG / f"{name}.py"))


if __name__ == "__main__":
    unittest.main()
