"""Bench-free tests for the five starter badges' artwork and its backfill.

`training/setup.py`'s `ensure_training_badges` is **insert-only** by design — a badge an admin
renamed, re-priced or disabled stays that way — and the consequence is that adding an `image` to
`STARTER_BADGES` reaches a fresh install and **no existing site**. Production has held all five
since Phase 4, so the artwork needed a backfill patch or it would have rendered empty circles
forever while the code said otherwise.

The failure this suite mostly exists for is one the patch nearly shipped with.
`frappe.db.get_value` returns `None` for **both** "no such row" and "the field is NULL", and
measured on production 2026-09-15 all five badges hold `image IS NULL`. So the obvious
`if current is None: continue` — written to mean "this site does not have that badge" — would have
read every one of them as absent, skipped all five, committed nothing, and printed a success line.
That is tested by running the real `execute()` against a fake frappe whose rows return `None`, not
by grepping for `db.exists`.

Run: python -m unittest erpnext_enhancements.tests.test_starter_badge_artwork
"""

import re
import sys
import types
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SETUP_PY = APP_ROOT / "training" / "setup.py"
PATCH = APP_ROOT / "patches" / "backfill_starter_badge_images.py"
PROGRAM_INIT = APP_ROOT / "training" / "technician_program" / "__init__.py"
ART = APP_ROOT / "public" / "images" / "training" / "badges"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: The five, and the artwork each must have. Restated here rather than imported so that a silent
#: edit to STARTER_BADGES — a renamed badge, a dropped row — fails rather than redefining the truth
#: it is being checked against.
EXPECTED = {
    "First Course": "badge-first-course.svg",
    "Full Marks": "badge-full-marks.svg",
    "Five Courses": "badge-five-courses.svg",
    "Ten Courses": "badge-ten-courses.svg",
    "Steady Week": "badge-steady-week.svg",
}

setup = None


def setUpModule():
    global setup
    from erpnext_enhancements.tests.test_training_course_authoring import _install_stubs

    _install_stubs()
    from erpnext_enhancements.training import setup as setup_module

    setup = setup_module


def _raw(path):
    return path.read_text(encoding="utf-8")


def _code_only(path):
    src = re.sub(r'"""[\s\S]*?"""', "", _raw(path))
    return re.sub(r"(?m)^\s*#.*$", "", src)


class TestTheStarterBadgesDeclareArtwork(unittest.TestCase):
    def test_there_are_still_five(self):
        self.assertEqual(len(setup.STARTER_BADGES), 5)

    def test_every_row_carries_an_image(self):
        for row in setup.STARTER_BADGES:
            with self.subTest(row[0]):
                self.assertEqual(len(row), 6, "STARTER_BADGES rows must be 6-tuples")
                self.assertTrue(row[5], f"{row[0]} has no image file")

    def test_each_badge_gets_the_artwork_drawn_for_it(self):
        actual = {row[0]: row[5] for row in setup.STARTER_BADGES}
        self.assertEqual(actual, EXPECTED)

    def test_every_criteria_type_is_one_the_evaluator_answers(self):
        """`gamification._badge_is_earned` awards nothing for a criterion it cannot answer, so a
        drifted literal is a badge nobody can ever earn with nothing on screen to say so."""
        known = {
            "Course Completed",
            "Category Completed",
            "Courses Completed Count",
            "Streak Days",
            "Perfect Score",
            "First Completion",
        }
        for row in setup.STARTER_BADGES:
            with self.subTest(row[0]):
                self.assertIn(row[2], known)


class TestTheArtworkExists(unittest.TestCase):
    """An `Attach Image` pointing at nothing renders as an empty box — no broken-image icon, no
    error, nothing in the Error Log. The only way to know is to check the file is there."""

    def test_every_declared_file_is_on_disk(self):
        for row in setup.STARTER_BADGES:
            with self.subTest(row[0]):
                self.assertTrue((ART / row[5]).exists(), f"{row[5]} is missing from {ART}")

    def test_each_badge_has_its_own_artwork(self):
        files = [row[5] for row in setup.STARTER_BADGES]
        self.assertEqual(len(files), len(set(files)))

    def test_the_artwork_is_plain_vector_with_nothing_fetched_at_render(self):
        """These ship as static app assets and are rendered in an `<img>`. A script, an external
        reference or an embedded raster in one would be a surprise arriving through an image
        field — and SVG was chosen over PNG partly because it is inspectable."""
        for row in setup.STARTER_BADGES:
            body = (ART / row[5]).read_text(encoding="utf-8")
            with self.subTest(row[0]):
                for forbidden in ("<script", 'xlink:href="http', 'href="http', "<image", "<foreignObject"):
                    self.assertNotIn(forbidden, body, f"{row[5]} contains {forbidden}")

    def test_the_artwork_names_the_badge_it_is_for(self):
        """Cheap guard against a copy-paste that files the trophy under Steady Week."""
        for name, filename in EXPECTED.items():
            body = (ART / filename).read_text(encoding="utf-8")
            with self.subTest(name):
                self.assertIn(name, body, f"{filename} does not name {name!r} in its title")


class TestThePathHasOneHome(unittest.TestCase):
    def test_setup_owns_the_constant(self):
        self.assertTrue(setup.BADGE_IMAGE_BASE.startswith("/assets/erpnext_enhancements/"))
        self.assertIn("training/badges", setup.BADGE_IMAGE_BASE)

    def test_the_technician_program_imports_it_rather_than_restating_it(self):
        """Two copies of a path is two places a directory move can half-happen."""
        src = _code_only(PROGRAM_INIT)
        self.assertNotIn('BADGE_IMAGE_BASE = "', src)
        self.assertIn("from erpnext_enhancements.training.setup import BADGE_IMAGE_BASE", src)

    def test_the_seeder_writes_the_image(self):
        src = _code_only(SETUP_PY)
        self.assertIn('"image": f"{BADGE_IMAGE_BASE}/{image}"', src)


class TestTheBackfill(unittest.TestCase):
    """Behavioural. A source grep for `db.exists` would pass on a version that called it for the
    wrong reason; what matters is what `execute()` does to rows that hold NULL."""

    def _run(self, rows, set_should_raise=False):
        """Run the real patch against a fake frappe. `rows` maps badge name -> stored image value
        (`None` for NULL, absent from the dict for "no such row"). Returns (writes, logged, raised).
        """
        import importlib

        writes = {}
        logged = []

        class _FakeDB:
            def exists(self, doctype, name=None):
                if doctype == "DocType":
                    return True
                return name in rows

            def get_value(self, doctype, name, field):
                # Exactly frappe's behaviour: None for a missing row AND for a NULL field.
                return rows.get(name)

            def set_value(self, doctype, name, field, value, update_modified=True):
                if set_should_raise:
                    raise RuntimeError("the write failed")
                writes[name] = {"field": field, "value": value, "update_modified": update_modified}

            def commit(self):
                pass

        fake = types.ModuleType("frappe")
        fake.db = _FakeDB()
        fake.log_error = lambda **kw: logged.append(kw)
        fake.get_traceback = lambda: "traceback"

        saved = sys.modules.get("frappe")
        sys.modules["frappe"] = fake
        try:
            mod = importlib.reload(
                importlib.import_module("erpnext_enhancements.patches.backfill_starter_badge_images")
            )
            raised = None
            try:
                mod.execute()
            except Exception as exc:
                raised = exc
            return writes, logged, raised
        finally:
            if saved is None:
                sys.modules.pop("frappe", None)
            else:
                sys.modules["frappe"] = saved

    def test_a_null_image_is_backfilled(self):
        """The bug this patch nearly shipped with. All five rows on production hold NULL, and
        `db.get_value` returns None for that exactly as it does for a missing row — so a patch that
        read None as "absent" would skip every one and report success."""
        rows = dict.fromkeys(EXPECTED, None)
        writes, logged, raised = self._run(rows)
        self.assertIsNone(raised)
        self.assertEqual(set(writes), set(EXPECTED), "a NULL image was not backfilled")
        self.assertEqual(logged, [])

    def test_it_writes_the_right_file_to_the_right_badge(self):
        rows = dict.fromkeys(EXPECTED, None)
        writes, _logged, _raised = self._run(rows)
        for name, filename in EXPECTED.items():
            with self.subTest(name):
                self.assertEqual(writes[name]["field"], "image")
                self.assertTrue(writes[name]["value"].endswith("/" + filename))
                self.assertTrue(writes[name]["value"].startswith(setup.BADGE_IMAGE_BASE))

    def test_an_empty_string_is_backfilled_too(self):
        """A site seeded by a different route may hold '' rather than NULL."""
        rows = dict.fromkeys(EXPECTED, "")
        writes, _logged, _raised = self._run(rows)
        self.assertEqual(set(writes), set(EXPECTED))

    def test_an_image_somebody_chose_is_never_overwritten(self):
        """The whole reason the seeder is insert-only is to not walk over an admin's decision."""
        rows = dict.fromkeys(EXPECTED, None)
        rows["Full Marks"] = "/files/a-badge-somebody-uploaded.png"
        writes, _logged, _raised = self._run(rows)
        self.assertNotIn("Full Marks", writes)
        self.assertEqual(set(writes), set(EXPECTED) - {"Full Marks"})

    def test_a_badge_that_is_not_on_this_site_is_left_alone(self):
        """`ensure_training_badges` runs on after_migrate and will create it, with its image."""
        rows = {"First Course": None}
        writes, _logged, _raised = self._run(rows)
        self.assertEqual(set(writes), {"First Course"})

    def test_it_does_not_touch_modified(self):
        """A purely cosmetic backfill should not make five records look freshly edited in every
        timeline and list view."""
        writes, _logged, _raised = self._run(dict.fromkeys(EXPECTED, None))
        for name in EXPECTED:
            self.assertFalse(writes[name]["update_modified"], name)

    def test_a_write_failure_never_aborts_the_migrate(self):
        """A patch that raises aborts `bench migrate`, which on this repo IS the deploy — and the
        failure is not a stopped deploy but a half-finished one."""
        writes, logged, raised = self._run(dict.fromkeys(EXPECTED, None), set_should_raise=True)
        self.assertIsNone(raised, f"execute() propagated {raised!r}")
        self.assertEqual(writes, {})
        self.assertEqual(len(logged), len(EXPECTED))

    def test_running_it_twice_changes_nothing_the_second_time(self):
        first, _l, _r = self._run(dict.fromkeys(EXPECTED, None))
        settled = {name: first[name]["value"] for name in first}
        second, _l2, _r2 = self._run(settled)
        self.assertEqual(second, {})


class TestItIsWiredUp(unittest.TestCase):
    def test_the_patch_is_registered(self):
        self.assertIn(
            "erpnext_enhancements.patches.backfill_starter_badge_images",
            _raw(APP_ROOT / "patches.txt"),
        )

    def test_this_suite_runs_in_ci(self):
        self.assertIn("erpnext_enhancements.tests.test_starter_badge_artwork", _raw(CI))


if __name__ == "__main__":
    unittest.main()
