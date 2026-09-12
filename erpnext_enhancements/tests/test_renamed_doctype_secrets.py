# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Guards on the `__Auth` rescue patch, and on the Frappe fact behind it.

**`frappe.rename_doc` on a DocType does not move its stored passwords.** A Password field
keeps a masked placeholder in `tabSingles` and the real encrypted value in the separate
`__Auth` table, keyed by `(doctype, name, fieldname)`. `rename_doc` rewrites the first and
leaves the second alone.

`rename_poseidon_settings_doctype` therefore stranded four secrets under the old name, and
**the Desk cannot show this**, because a Password field renders blank whether or not a value
is stored. The form for a correctly-configured site and a silently-broken one are identical.
Two of the four were re-entered by hand; `maps_api_key` and `twilio_auth_token` were not, so:

* every Vertex feature in the app had never worked — the briefing narrative,
  `api/communication` drafts, `api/training_ai`, `assistant_tools/draft_course_spec`;
* every inbound Twilio webhook was rejected, and call-recording downloads got a 401. That one
  fails **closed** — nothing was let through.

What this file protects is the two properties that make the repair safe to run against live
credentials:

1. **It never handles the secret.** The plaintext is never materialised — the rows are
   re-filed by rewriting `doctype`/`name`, and the `password` column is never selected. A
   decrypt/re-encrypt round trip would work too and is strictly worse: it puts a live secret
   in a local, where a traceback can publish it (`frappe.log_error` writes frame locals).
2. **It never rolls a credential backwards.** `admin_webhook_secret` and `twilio_api_secret`
   exist under both names; the hand-entered ones are newer and are what the site runs on.
   Overwriting them with a pre-rename value would be an unannounced credential rollback.

Bench-free: filesystem and `ast` only. Asserts the patch's shape, never live secret state.

Run: python -m unittest erpnext_enhancements.tests.test_renamed_doctype_secrets
"""

import ast
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
RESCUE = APP / "patches/rescue_renamed_doctype_auth_rows.py"
RENAME = APP / "patches/rename_poseidon_settings_doctype.py"
BRIEFING = APP / "patches/enable_briefing_gemini_narrative.py"
PATCHES_TXT = APP / "patches.txt"


def _code(path):
    """Executable source: comments and docstrings stripped, quotes normalised.

    Every absence assertion below runs on this. The rescue patch's own docstring discusses
    the `password` column and decryption at length while explaining why it does neither —
    run raw, an assertion that it never touches them would match the explanation. That trap
    has been hit thirteen times in this project now.
    """
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


class TestItNeverHandlesTheSecret(unittest.TestCase):
    def test_it_never_selects_the_password_column(self):
        """The whole point of re-filing rather than re-encrypting. Frappe encrypts with the
        site key, not with the doctype name, so the ciphertext is valid under the new owner
        untouched — and a plaintext that is never loaded cannot be leaked by a traceback."""
        code = _code(RESCUE)
        self.assertNotIn("password", code.lower())

    def test_it_never_decrypts_or_re_encrypts(self):
        code = _code(RESCUE)
        for forbidden in (
            "get_decrypted_password",
            "set_encrypted_password",
            "get_password",
            "decrypt",
            "encrypt",
        ):
            with self.subTest(api=forbidden):
                self.assertNotIn(forbidden, code)

    def test_it_moves_the_rows_by_rewriting_the_owner(self):
        """Positive counterpart to the absences above: assert it does the thing, not merely
        that it avoids the alternatives."""
        code = _code(RESCUE)
        self.assertIn("update `__Auth`", code)
        self.assertIn("set doctype = %s, name = %s", code)


class TestItNeverRollsACredentialBackwards(unittest.TestCase):
    def test_it_only_moves_fieldnames_with_no_row_under_the_new_name(self):
        """`admin_webhook_secret` and `twilio_api_secret` exist under both names. The
        hand-entered ones are newer and are what the site authenticates with; replacing them
        with a pre-rename copy would be a silent credential rollback."""
        code = _code(RESCUE)
        self.assertIn("stranded - already", code)

    def test_the_superseded_copies_are_deleted_not_moved(self):
        """Leaving a second, stale copy of a live secret in the table is its own small
        hazard — and a later maintainer reading two rows cannot tell which one is current."""
        code = _code(RESCUE)
        self.assertIn("delete from `__Auth`", code)
        self.assertIn("stranded & already", code)

    def test_both_sets_are_computed_from_the_same_two_queries(self):
        """A version that computed `already` from anything but `__Auth` under the new name
        could mis-classify and overwrite. Pin that both come from the table itself."""
        self.assertEqual(_code(RESCUE).count("select fieldname from `__Auth`"), 2)


class TestItCannotAbortTheDeploy(unittest.TestCase):
    """A patch that raises aborts `bench migrate`, which on this repo IS the deploy —
    v1.395.0 left production schema-synced, half-patched and reporting the new version."""

    def test_the_rescue_is_wrapped_and_guarded(self):
        code = _code(RESCUE)
        self.assertIn("except Exception", code)
        self.assertIn('frappe.db.exists("DocType"', code)

    def test_the_briefing_patch_is_wrapped_and_guarded(self):
        code = _code(BRIEFING)
        self.assertIn("except Exception", code)
        self.assertIn('frappe.db.exists("DocType"', code)

    def test_the_briefing_patch_writes_with_set_single_value(self):
        code = _code(BRIEFING)
        self.assertIn("frappe.db.set_single_value", code)
        tree = ast.parse(BRIEFING.read_text(encoding="utf-8"))
        saves = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in ("save", "insert", "submit")
        ]
        self.assertEqual(saves, [])

    def test_both_are_registered(self):
        text = PATCHES_TXT.read_text(encoding="utf-8")
        for module in (
            "erpnext_enhancements.patches.rescue_renamed_doctype_auth_rows",
            "erpnext_enhancements.patches.enable_briefing_gemini_narrative",
        ):
            with self.subTest(patch=module):
                self.assertIn(module, text)


class TestTheBriefingSwitchStaysRevertible(unittest.TestCase):
    def test_it_writes_only_while_the_value_is_falsy(self):
        """Not idempotent-by-overwrite on purpose: switching the feature back off in the Desk
        must survive the next migrate, or the setting is not really a setting."""
        code = _code(BRIEFING)
        self.assertIn("if cint(current):", code)
        at = code.index("if cint(current):")
        self.assertIn("return", code[at : at + 120])

    def test_it_is_a_separate_patch_from_the_data_backfill(self):
        """It is a SPEND decision — a Vertex call per recipient every weekday morning — so it
        is kept out of `restore_drifted_single_defaults` and can be reverted on its own.
        `tests/test_single_default_drift.py` asserts the other side of this."""
        self.assertTrue(BRIEFING.is_file())
        backfill = (APP / "patches/restore_drifted_single_defaults.py").read_text(encoding="utf-8")
        tree = ast.parse(backfill)
        tables = [
            ast.literal_eval(n.value)
            for n in tree.body
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict)
        ]
        for table in tables:
            self.assertNotIn("briefing_use_gemini", table)


class TestTheRenamePatchNoLongerClaimsOtherwise(unittest.TestCase):
    def test_it_warns_that_secrets_did_not_carry_across(self):
        """Its docstring used to say the rename carried "secrets" across. It did not, and the
        claim is the reason this went unnoticed for months — so the correction lives where the
        next person will read it."""
        text = RENAME.read_text(encoding="utf-8")
        self.assertIn("__Auth", text)
        self.assertIn("did NOT carry the secrets across", text)

    def test_it_names_the_repair(self):
        self.assertIn("rescue_renamed_doctype_auth_rows", RENAME.read_text(encoding="utf-8"))


class TestTheAssertionsCannotPassVacuously(unittest.TestCase):
    """Five assertions above are absences over stripped source. If `_code` over-stripped,
    every one would pass while reading almost nothing."""

    def test_the_stripper_keeps_the_code(self):
        code = _code(RESCUE)
        self.assertIn("def execute", code)
        self.assertIn("__Auth", code)
        self.assertGreater(len(code), 600)

    def test_the_stripper_drops_the_prose(self):
        """The docstring says "password" and "decrypt" repeatedly while explaining why the
        patch does neither — which is exactly what made the first version of
        `test_it_never_selects_the_password_column` fail against correct code."""
        raw = RESCUE.read_text(encoding="utf-8").lower()
        self.assertIn("password", raw)
        self.assertIn("decrypt", raw)
        self.assertNotIn("password", _code(RESCUE).lower())


# Runs LAST, deliberately — see tests/test_test_collection.py.
if __name__ == "__main__":
    unittest.main()
