# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""R2: the switch that retires the classic Training Builder.

**The polarity is the whole design, and getting it backwards ships R3 a release early.**

`Training Settings` is `issingle: 1`. A `default` on a *new* field of a Single never
reaches the row that already exists: `bench migrate` writes no `tabSingles` row for a
newly declared field, and `load_from_db` applies no defaults — they fire in `new_doc()`,
i.e. on a fresh install and never again. So on every live site the field reads `None`,
and `cint(None)` is `0`.

* `classic_builder_retired` default `"0"` — the missing-row reading (0 = *not* retired)
  and the declared default **agree**. Dormant everywhere, nothing changes on deploy, and
  the door closes only when a human ticks the box.
* `classic_builder_enabled` default `"1"` would be the trap. Production reads `None` →
  `0` → **disabled**, so the builder switches off on the live site the day it deploys,
  chosen by nobody, while the JSON reads `1` so every cheap check says it is on.

The rule, and the reason this file exists: **name the flag so that the missing-row
reading is the status-quo answer.** Verified live before shipping — Training Settings
has 33 rows in `tabSingles` and every declared field has one, which is exactly why a
*newly* declared one will not.

No backfill patch, deliberately. There is nothing to backfill, and one keyed on
emptiness would match zero rows, commit, and record itself in `tabPatch Log` —
indistinguishable from a successful run (the v1.280.3 failure). It would also be a patch
on a Single, the one place a raise aborts `bench migrate`, which on this repo is the
deploy (v1.395.0).

Bench-free: filesystem, `json` and `ast` only.

Run: python -m unittest erpnext_enhancements.tests.test_classic_builder_retirement
"""

import ast
import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SETTINGS_JSON = APP / "training/doctype/training_settings/training_settings.json"
BOOT_PY = APP / "boot.py"
BUILDER_JS = APP / "training/page/training_builder/training_builder.js"
CANVAS_JS = APP / "training/page/training_canvas/training_canvas.js"

FIELD = "classic_builder_retired"


def _settings():
    return json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))


def _field():
    for row in _settings().get("fields", []):
        if row.get("fieldname") == FIELD:
            return row
    return None


def _js(path):
    """Source with whole-line and block comments removed.

    Every absence assertion below runs on this. The comments introducing the flag
    necessarily spell both `classic_builder_enabled` and `classic_builder_retired` while
    explaining which one is the trap — run raw, an assertion that the wrong name is
    absent would match the sentence saying so.
    """
    out, in_block = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            in_block = "*/" not in stripped
            continue
        if stripped.startswith("//"):
            continue
        out.append(line)
    return "\n".join(out)


def _py_no_comments(path):
    """Python source with comments and docstrings removed, via `ast`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


class TestThePolarityIsTheStatusQuo(unittest.TestCase):
    def test_the_field_exists_and_is_a_check(self):
        row = _field()
        self.assertIsNotNone(row, f"{FIELD} is not declared on Training Settings")
        self.assertEqual(row.get("fieldtype"), "Check")

    def test_it_defaults_to_zero(self):
        """0 = not retired. This is the assertion that keeps R2 from being R3."""
        self.assertEqual(
            _field().get("default"),
            "0",
            "A Single writes no row for a new field, so prod reads None -> 0. The declared "
            "default must AGREE with that reading, or the two disagree on every live site.",
        )

    def test_the_flag_is_named_for_the_action_not_the_state(self):
        """`_enabled` defaulting to 1 is the trap this whole file is about: prod would read
        the missing row as 0, disable the builder on deploy with nobody choosing it, and
        every cheap check would still say 1 because that is what the JSON says."""
        names = [row.get("fieldname") for row in _settings().get("fields", [])]
        self.assertIn(FIELD, names)
        self.assertNotIn("classic_builder_enabled", names)

    def test_it_is_declared_in_field_order(self):
        """A field missing from `field_order` does not render, so the switch could never
        be ticked — and the failure is invisible until somebody goes looking for it."""
        self.assertIn(FIELD, _settings().get("field_order", []))

    def test_the_description_says_it_is_reversible_and_not_a_permission(self):
        """The field description is the only place an operator reads about this before
        ticking it, so the two things they could get wrong belong there: that unticking
        restores the page, and that this is a retirement switch rather than access
        control (the Page's `roles` remain the boundary)."""
        description = (_field().get("description") or "").lower()
        self.assertIn("reversible", description)
        self.assertIn("not access control", description)

    def test_no_backfill_patch_was_written_for_it(self):
        """There is nothing to backfill — the declared default and the missing-row reading
        already agree. A patch keyed on emptiness would match zero rows, commit, and log
        itself in `tabPatch Log`, which is indistinguishable from a successful run."""
        hits = [
            p.name
            for p in (APP / "patches").glob("*.py")
            if FIELD in p.read_text(encoding="utf-8")
        ]
        self.assertEqual(hits, [], f"unexpected backfill patch for {FIELD}: {hits}")


class TestTheFlagIsReadSafely(unittest.TestCase):
    def test_it_is_read_with_get_single_value(self):
        source = _py_no_comments(BOOT_PY)
        self.assertIn("get_single_value", source)
        self.assertIn(FIELD, source)

    def test_it_does_not_go_through_is_enabled(self):
        """`training_settings.is_enabled` returns False for any switch while
        `training_enabled` is off, and that module's own docstring promises "Authoring
        works with every switch off". Routed through it, a dormant site would read
        "not retired" for a reason unrelated to the decision."""
        at = _py_no_comments(BOOT_PY).index("_classic_builder_retired")
        body = _py_no_comments(BOOT_PY)[at : at + 900]
        self.assertNotIn("is_enabled", body)

    def test_it_never_touches_the_singles_table_directly(self):
        """`tabSingles` has three columns and no `creation`, so a `get_value("Singles", …)`
        read cannot succeed against the default `order_by` — it fails on every site, every
        time. `tests/test_singles_table_access.py` fails the build on it."""
        self.assertNotIn('"Singles"', _py_no_comments(BOOT_PY))

    def test_every_failure_answers_not_retired(self):
        """`extend_bootinfo` runs on every desk load for every user. A flag that can raise
        turns a missing DocType into a blank desk — and a flag that answers "retired" when
        it does not know would retire the page by accident."""
        source = _py_no_comments(BOOT_PY)
        at = source.index("def _classic_builder_retired")
        body = source[at : source.index("\ndef ", at + 5)]
        self.assertIn("except Exception", body)
        self.assertIn("return False", body.split("except Exception")[1])

    def test_the_boot_key_is_set(self):
        self.assertIn("ee_training_classic_builder", _py_no_comments(BOOT_PY))


class TestThePageHonoursTheFlag(unittest.TestCase):
    def test_the_page_checks_the_boot_key(self):
        self.assertIn("ee_training_classic_builder", _js(BUILDER_JS))

    def test_an_unknown_value_means_not_retired(self):
        """An older desk session, or any boot that failed to set the key, leaves it
        undefined. Truthiness would read that as retired and close the tool on a stale tab,
        chosen by nobody — the same shape as the default-1 trap, one layer out."""
        self.assertIn("ee_training_classic_builder === 0", _js(BUILDER_JS))

    def test_it_returns_before_constructing_the_builder(self):
        """A notice rendered *beside* a working builder is not a retirement."""
        src = _js(BUILDER_JS)
        at = src.index("ee_training_classic_builder")
        gate = src[at : src.index("new TrainingBuilder(", at)]
        self.assertIn("return;", gate)

    def test_the_notice_sends_the_author_somewhere(self):
        """Being told a page is gone and left to find your own way back is how a
        retirement reads as a breakage. The course is carried through so an old bookmark
        lands on the same course rather than on a picker."""
        src = _js(BUILDER_JS)
        at = src.index("function tb_render_retired(")
        body = src[at : src.index("\n}", at)]
        self.assertIn("training-canvas", body)
        self.assertIn("frappe.route_options", body)

    def test_the_notice_says_how_to_undo_it(self):
        src = _js(BUILDER_JS)
        at = src.index("function tb_render_retired(")
        body = src[at : src.index("\n}", at)]
        self.assertIn("Training Settings", body)

    def test_nothing_of_the_builder_is_deleted(self):
        """R2 gates; R3 deletes. Unticking the box must restore the whole tool with no code
        change, which is what keeps R3 a separate and still-reversible decision."""
        for path in ("training_builder.js", "training_builder.css", "training_builder.json"):
            with self.subTest(file=path):
                self.assertTrue((APP / "training/page/training_builder" / path).is_file())
        source = BUILDER_JS.read_text(encoding="utf-8")
        self.assertIn("register_drive_video", source)
        self.assertIn("class TrainingBuilder", source)

    def test_the_page_roles_are_untouched(self):
        """Emptying `roles` would OPEN the page to every desk user: frappe v16's
        `Page.is_permitted` returns True on an empty allow-list. The retirement switch is
        not a permission and must not be implemented as one."""
        page = json.loads(
            (APP / "training/page/training_builder/training_builder.json").read_text(encoding="utf-8")
        )
        roles = {row.get("role") for row in page.get("roles", [])}
        self.assertEqual(roles, {"System Manager", "Training Author", "Training Manager"})


class TestTheSwitchIsSafeToThrow(unittest.TestCase):
    """R2 could not ship before the canvas could register a video (v1.417.0). Ticking the
    box with a hand-off still in place would have removed the only way to do it."""

    def test_the_canvas_needs_nothing_from_the_classic_builder(self):
        canvas = _js(CANVAS_JS)
        self.assertNotIn("training-builder", canvas)
        self.assertNotIn("open_classic", canvas)

    def test_the_canvas_can_register_and_repair_a_video(self):
        canvas = _js(CANVAS_JS)
        self.assertIn("register_video_asset", canvas)
        self.assertIn("retry_video_copy", canvas)

    def test_nothing_else_in_the_app_routes_to_the_page(self):
        """The check that has to hold before anyone ticks the box. Scanned over
        comment-stripped source across the whole app, because this release writes several
        comments that name the route while explaining that nothing uses it."""
        offenders = []
        for path in sorted(APP.rglob("*.js")):
            if "node_modules" in path.parts or path.parts[-2] == "training_builder":
                continue
            if "training-builder" in _js(path):
                offenders.append(str(path.relative_to(APP)))
        self.assertEqual(offenders, [], f"still routing to the retired page: {offenders}")


class TestTheAssertionsCannotPassVacuously(unittest.TestCase):
    """Several checks above are absence assertions over stripped source. If the stripper
    silently over-stripped — returning very little, or nothing — they would all pass while
    examining an empty string, which is the failure mode this project keeps meeting."""

    def test_the_js_stripper_keeps_the_code(self):
        stripped = _js(BUILDER_JS)
        self.assertGreater(len(stripped), 40000)
        self.assertIn("class TrainingBuilder", stripped)

    def test_the_js_stripper_removes_comments(self):
        self.assertNotIn("RETIREMENT (R2", _js(BUILDER_JS))

    def test_the_py_stripper_keeps_the_code(self):
        stripped = _py_no_comments(BOOT_PY)
        self.assertIn("def boot_session", stripped)
        self.assertNotIn("Never raises", stripped)


# Runs LAST, deliberately — see tests/test_test_collection.py.
if __name__ == "__main__":
    unittest.main()
