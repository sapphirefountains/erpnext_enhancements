# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""R3: the classic Training Builder is gone, and nothing is left pointing at it.

The end of a staged retirement, approved by Nik on 2026-09-12:

| | |
|---|---|
| R1 (v1.416.0) | Unlinked it — no button anywhere, URL still reachable. |
| v1.417.0 | Ported video registration, the last capability that existed only there. |
| R2 (v1.418.0) | Added `classic_builder_retired`, defaulting off. |
| R3 (v1.422.0) | Deleted the page. **One-way.** |

**The order was load-bearing and is worth keeping on the record.** R2 shipped a switch that
was never thrown, because until v1.417.0 ticking it would have removed the only way to
register a video from Drive. The flag is removed in this release too — it gated a page that
no longer exists, and a settings checkbox that does nothing is exactly the kind of stale
claim the whole retirement was cleaning up.

This file replaces the R2 suite of the same name. That one asserted the page files existed
and the flag's polarity was safe; both claims are now moot, so the assertions become their
opposites: **nothing may reference the page, and the flag must be gone from every layer.**

What it does NOT assert is that the API is gone, because it is not. `register_video_asset`,
`retry_video_copy`, `_builder_video_assets` and `_probe_drive_video` stay in
`api/training_author.py` — the canvas calls all four as of v1.417.0. Deleting a page is not
deleting an API, and a test that conflated the two would block the next person from reading
this correctly.

Bench-free: filesystem, `json` and `ast` only.

Run: python -m unittest erpnext_enhancements.tests.test_classic_builder_retirement
"""

import ast
import json
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PAGE_DIR = APP / "training/page/training_builder"
CANVAS_JS = APP / "training/page/training_canvas/training_canvas.js"
SETTINGS_JSON = APP / "training/doctype/training_settings/training_settings.json"
BOOT_PY = APP / "boot.py"
PATCH = APP / "patches/delete_classic_training_builder.py"
PATCHES_TXT = APP / "patches.txt"

ROUTE = "training-builder"
FIELD = "classic_builder_retired"

# The sweep covers SHIPPED code only, and deliberately excludes `tests/`.
#
# Not a convenience: a test that asserts the route is absent must NAME the route to do
# so, and several legitimately do -- `test_training_authoring_entry` pins that the Course
# form has exactly one authoring door by asserting "training-builder" is not in it. A
# sweep that flagged those would be the absence-assertion trap one level up, punishing
# exactly the tests doing the right thing. What ships is what matters here.
#
# The patch that performs the deletion is excluded for the same reason.
SWEEP_SKIP_DIRS = {"node_modules", "tests"}
SWEEP_SKIP_FILES = {"delete_classic_training_builder.py"}


def _js(path):
    """JS/CSS/HTML with whole-line and block comments removed.

    Needed because this release writes comments that NAME the route while explaining that
    nothing uses it — the trap this project has now hit thirteen times.
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
        if stripped.startswith("<!--"):
            in_block = "-->" not in stripped
            continue
        if stripped.startswith("//"):
            continue
        out.append(line)
    return "\n".join(out)


def _py(path):
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
    return ast.unparse(ast.fix_missing_locations(tree)).replace("'", '"')


class TestThePageIsGone(unittest.TestCase):
    def test_the_directory_no_longer_exists(self):
        self.assertFalse(PAGE_DIR.exists(), f"{PAGE_DIR} still present")

    def test_nothing_in_the_app_routes_to_it(self):
        """The check that makes R3 real rather than cosmetic. Swept over comment-stripped
        source across every .js and .py in the app, because this release writes several
        comments naming the route while explaining that nothing uses it."""
        offenders = []
        for path in sorted(list(APP.rglob("*.js")) + list(APP.rglob("*.py"))):
            if SWEEP_SKIP_DIRS & set(path.parts) or path.name in SWEEP_SKIP_FILES:
                continue
            code = _py(path) if path.suffix == ".py" else _js(path)
            if ROUTE in code:
                offenders.append(str(path.relative_to(APP)))
        self.assertEqual(offenders, [], f"still reference the deleted page: {offenders}")

    def test_the_canvas_has_no_hand_off_left(self):
        code = _js(CANVAS_JS)
        self.assertNotIn("open_classic", code)
        self.assertNotIn(ROUTE, code)


class TestThePageRecordIsDeletedToo(unittest.TestCase):
    """Removing the folder stops the page being *synced*; it does not delete the `Page` row
    already on the site. Same two-step rule `fixtures/README.md` states for Custom Fields."""

    def test_the_patch_exists_and_deletes_the_page(self):
        self.assertTrue(PATCH.is_file())
        code = _py(PATCH)
        self.assertIn('frappe.delete_doc("Page"', code)

    def test_it_also_drops_the_retired_flag_row(self):
        """The field's JSON declaration goes in this release; the `tabSingles` row outlives
        that. A stale row for a field no form shows is harmless until somebody greps for it
        and finds a setting that appears to exist."""
        code = _py(PATCH)
        self.assertIn("delete from tabSingles", code)
        self.assertIn(FIELD, code)

    def test_it_cannot_abort_the_deploy(self):
        code = _py(PATCH)
        self.assertIn("except Exception", code)
        self.assertGreaterEqual(code.count("except Exception"), 2)

    def test_it_runs_post_model_sync(self):
        text = PATCHES_TXT.read_text(encoding="utf-8")
        at = text.index("erpnext_enhancements.patches.delete_classic_training_builder")
        self.assertIn("[post_model_sync]", text[:at])


class TestTheFlagIsGoneFromEveryLayer(unittest.TestCase):
    """R2's flag gated a page that no longer exists. Left behind it would be a settings
    checkbox that does nothing — the exact kind of stale claim this retirement removed."""

    def test_it_is_not_declared_on_the_single(self):
        doc = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
        names = [f["fieldname"] for f in doc.get("fields", [])]
        self.assertNotIn(FIELD, names)
        self.assertNotIn("authoring_section", names)

    def test_field_order_and_fields_stay_in_step(self):
        """Removing from one and not the other leaves a form that cannot render."""
        doc = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
        names = [f["fieldname"] for f in doc.get("fields", [])]
        self.assertEqual(sorted(doc.get("field_order", [])), sorted(names))

    def test_the_boot_key_and_helper_are_gone(self):
        code = _py(BOOT_PY)
        self.assertNotIn("ee_training_classic_builder", code)
        self.assertNotIn("_classic_builder_retired", code)

    def test_boot_still_builds_its_other_keys(self):
        """The removal was surgical, not a truncation. If the helper deletion had taken a
        neighbour with it, every assertion above would still pass."""
        code = _py(BOOT_PY)
        self.assertIn("def boot_session", code)
        self.assertIn("ee_chat", code)
        self.assertIn("_chat_visible", code)


class TestTheApiSurvives(unittest.TestCase):
    """Deleting a page is not deleting an API. All four of these are called by the canvas
    as of v1.417.0; a retirement that swept them would be an irreversible capability loss
    dressed as tidying."""

    def test_the_video_endpoints_are_still_there(self):
        author = (APP / "api/training_author.py").read_text(encoding="utf-8")
        for symbol in (
            "def register_video_asset(",
            "def retry_video_copy(",
            "def _builder_video_assets(",
            "def _probe_drive_video(",
        ):
            with self.subTest(symbol=symbol):
                self.assertIn(symbol, author)

    def test_the_canvas_still_calls_them(self):
        code = _js(CANVAS_JS)
        self.assertIn("register_video_asset", code)
        self.assertIn("retry_video_copy", code)


class TestTheAssertionsCannotPassVacuously(unittest.TestCase):
    """The sweep above walks the app and asserts an absence. If the walk found no files, or
    the strippers over-stripped, it would pass while checking nothing."""

    def test_the_sweep_actually_reads_files(self):
        seen = [
            p
            for p in list(APP.rglob("*.js")) + list(APP.rglob("*.py"))
            if not (SWEEP_SKIP_DIRS & set(p.parts))
        ]
        self.assertGreater(len(seen), 200)

    def test_the_sweep_would_catch_a_real_reference(self):
        """The sweep is an absence assertion over a walk. Prove the walk plus the stripper
        would actually flag shipped code that routed to the page, rather than passing
        because nothing ever reaches the comparison."""
        planted = 'frappe.set_route("%s");' % ROUTE
        self.assertIn(ROUTE, planted)
        # and the stripper must not swallow it when it is real code rather than a comment
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.js"
            probe.write_text("// %s in a comment\n%s\n" % (ROUTE, planted), encoding="utf-8")
            stripped = _js(probe)
            self.assertIn(ROUTE, stripped, "the stripper swallowed real code")
            self.assertEqual(stripped.count(ROUTE), 1, "the comment was not stripped")

    def test_the_strippers_keep_the_code(self):
        self.assertIn("def boot_session", _py(BOOT_PY))
        self.assertIn("class TrainingCanvas", _js(CANVAS_JS))

    def test_the_strippers_drop_the_prose(self):
        self.assertNotIn("RETIREMENT", _js(CANVAS_JS))


# Runs LAST, deliberately — see tests/test_test_collection.py.
if __name__ == "__main__":
    unittest.main()
