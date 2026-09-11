# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The v1.403.0 training-content corrections, pinned. Bench-free.

The content being corrected lives ONLY in `tabTraining Content Block` on production — no repo
file carries it — so this suite cannot assert on the data. It asserts on the patch's
replacement table instead, which is where the mistakes would actually be made.

Note what is NOT asserted: that the `old` strings match production. That was verified
directly against prod before the patch was written (5 of 5, exactly one occurrence each) and
cannot be re-checked from CI. The patch is written to skip silently rather than force a
mismatch, so a drift shows up as a "no longer carries the old text" line in the migrate log
rather than as damage.

Every absence assertion here strips comments and docstrings first. The patch's own module
docstring names `Expense Claim` and `Plaid Settings` many times over — explaining why one is
gone and why the other must stay requires naming both. That trap has caught this project
seven times.

Run: python -m unittest erpnext_enhancements.tests.test_training_content_fix
"""

import ast
import io
import re
import tokenize
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PATCH = APP / "patches/fix_stale_training_course_content.py"
PATCHES_TXT = APP / "patches.txt"


def _text(path):
    return path.read_text(encoding="utf-8")


def _code(path):
    """Source with comments and docstrings stripped."""
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


def _const(path, name):
    for node in ast.parse(_text(path)).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path.name}")


class TestTheReplacementTable(unittest.TestCase):
    def test_every_block_edit_is_well_formed(self):
        for entry in _const(PATCH, "BLOCK_EDITS"):
            self.assertEqual(len(entry), 4, f"expected (name, key, old, new): {entry!r}")
            name, key, old, new = entry
            with self.subTest(block=name):
                for field in (name, key, old, new):
                    self.assertTrue(str(field).strip())
                self.assertNotEqual(old, new, "a no-op edit reads as done")

    def test_no_block_is_edited_twice(self):
        """Two entries for one block means the second `old` is matched against content the
        first has already rewritten — it silently does nothing."""
        seen = [(n, k) for n, k, _o, _n in _const(PATCH, "BLOCK_EDITS")]
        self.assertEqual(sorted(seen), sorted(set(seen)))

    def test_titles_are_well_formed_and_changed(self):
        for lesson, old, new in _const(PATCH, "TITLE_EDITS"):
            with self.subTest(lesson=lesson):
                self.assertTrue(old.strip() and new.strip())
                self.assertNotEqual(old, new)


class TestWhatTheCorrectionsMustSay(unittest.TestCase):
    """The five edits exist for specific reasons. These assert the reasons, not the prose."""

    def _new_blocks(self):
        return [new for _n, _k, _o, new in _const(PATCH, "BLOCK_EDITS")]

    def test_expense_claim_is_only_ever_mentioned_to_deny_it(self):
        """The defect is a lesson telling people the system creates an Expense Claim. It does
        not — HRMS is not installed and `expense_claims_available()` is False. Saying so
        explicitly is BETTER than deleting the words, so a blanket "must not appear" would be
        the wrong assertion. What must never return is the claim that it gets created."""
        for new in self._new_blocks():
            for match in re.finditer(r"Expense Claim", new):
                window = new[max(0, match.start() - 40) : match.start()]
                with self.subTest(window=window[-40:]):
                    self.assertIn(
                        "no ",
                        window,
                        "Expense Claim may only appear in a sentence that denies it exists",
                    )

    def test_the_replacement_names_what_actually_gets_created(self):
        joined = " ".join(self._new_blocks())
        for real in ("Purchase Invoice", "Reimbursement Bill", "Payment Entry", "Purchase Receipt"):
            with self.subTest(doctype=real):
                self.assertIn(real, joined)

    def test_plaid_settings_is_kept_not_renamed(self):
        """An inverted assertion, deliberately. `patches/rename_plaid_settings_doctype.py`
        handed the name `Plaid Settings` back to ERPNext's native integration, and the
        Invoicing workspace's Banking card still carries a link with exactly that label. The
        failure mode is a later pass 'helpfully' renaming this to Plaid Banking Settings and
        making the lesson describe a card that is not on the page."""
        plaid = [new for new in self._new_blocks() if "Plaid" in new]
        self.assertTrue(plaid, "the Plaid disambiguation went missing")
        for new in plaid:
            self.assertIn("Plaid Settings", new)
            self.assertIn("Plaid Banking Settings", new, "the two must be told apart, not merged")

    def test_no_new_title_carries_a_date_stamp(self):
        """`_materialize_lessons` freezes lesson titles into `toc_json` at publish and the
        version cannot be edited afterwards, so a dated title is permanent."""
        for _lesson, _old, new in _const(PATCH, "TITLE_EDITS"):
            with self.subTest(title=new):
                self.assertNotIn("as of", new.lower())
                self.assertIsNone(re.search(r"\b(19|20)\d{2}\b", new))

    def test_the_dating_disclaimer_is_added_where_the_stamp_is_removed(self):
        """Removing the stamp without this strips the only visible warning that the page is a
        snapshot. TRN-LSN-000091 already opens with the sentence; TRN-LSN-000107 did not."""
        joined = " ".join(self._new_blocks())
        self.assertIn("true on the date of writing", joined)


class TestItCannotAbortTheDeploy(unittest.TestCase):
    """A patch that raises aborts `bench migrate`, which on this repo is the deploy."""

    def test_the_patch_never_throws(self):
        body = _code(PATCH)
        self.assertNotIn("frappe.throw", body)

    def test_it_checks_the_version_is_still_a_draft(self):
        """`TrainingLesson._reject_edits_to_published_version` returns EARLY under
        `frappe.flags.in_patch`, so the controller will not stop this patch rewriting a
        published lesson. The guard has to live in the patch."""
        body = _code(PATCH)
        self.assertIn("docstatus", body)
        self.assertIn("_is_draft", body)

    def test_every_write_is_guarded_on_the_old_text_still_being_there(self):
        """Safe twice, and safe against somebody having edited the lesson by hand since."""
        body = _code(PATCH)
        self.assertIn("if old not in content:", body)

    def test_matching_is_done_in_python_not_sql(self):
        """MariaDB's default collation is PAD SPACE, so a trailing-space comparison in SQL is
        vacuously true. These strings also carry em-dashes and &amp; entities."""
        body = _code(PATCH)
        self.assertNotIn("like", body.lower().replace("unlike", ""))


class TestItActuallyRuns(unittest.TestCase):
    def test_registered_in_patches_txt(self):
        self.assertIn(
            "erpnext_enhancements.patches.fix_stale_training_course_content", _text(PATCHES_TXT)
        )

    def test_it_is_wired_into_ci(self):
        ci = (APP.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("test_training_content_fix", ci)


if __name__ == "__main__":
    unittest.main()
