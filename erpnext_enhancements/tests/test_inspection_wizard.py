# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Guards for the Inspection Wizard — WI-075 sub-phase H.

Bench-free and frappe-free: assertions about the contents of ``inspection_wizard.js`` and the
allowlists in ``api/quality_wizard.py``. unittest, not pytest, so it can ride an existing
``python -m unittest`` step in ci.yml — a pytest-style suite appended to a unittest module list
is silently collected as nothing, which is what left the QuickBooks suite running nowhere.

The failures guarded here are the ones that leave the screen looking right:

1. **A wizard that can write a frozen field.** The whole freeze rests on the server allowlist
   being short. A ``min_value`` in it would let somebody turn a failing measurement into a
   passing one from a phone, on site, with nothing in the diff to see.
2. **Escaped Text Editor markup.** ``frappe.utils.xss_sanitise`` escapes ``<``, ``>``, ``"``,
   ``'`` and ``/`` by default, so safety instructions render as visible tags. The panel still
   looks populated — which is why it shipped once on the maintenance side and survived review.
3. **A silent failed autosave.** On a bad signal this loses a whole inspection, and the
   inspector finds out after leaving the site.
4. **Required rows that are only revealed by the submit refusal.** ``is_mandatory`` and
   ``requires_photo`` reach the ``before_submit`` gate whether or not anybody saw them. A
   checklist that only says what it wanted once you try to finish is a checklist that lied.
5. **Raw control bytes**, which make git treat the file as binary and stop producing diffs.

Run: python -m unittest erpnext_enhancements.tests.test_inspection_wizard
"""

import io
import os
import re
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIZARD = os.path.join(
    APP_DIR, "quality", "page", "inspection_wizard", "inspection_wizard.js"
)
PAGE_JSON = os.path.join(
    APP_DIR, "quality", "page", "inspection_wizard", "inspection_wizard.json"
)
API = os.path.join(APP_DIR, "api", "quality_wizard.py")

#: Frozen fields on ``Inspection Result``: the standard being inspected against, copied from the
#: master template at generation. None of these may ever appear in the server's row allowlist.
FROZEN_ROW_FIELDS = (
    "label",
    "acceptance_criteria",
    "source",
    "source_key",
    "section_title",
    "sequence",
    "check_type",
    "method",
    "is_mandatory",
    "requires_photo",
    "min_value",
    "max_value",
    "uom",
    "options",
    "location_note",
    "out_of_range",
    "non_conformance",
)


def _strip_comments(source):
    """Drop // comments so assertions about *code* are not fooled by prose.

    A comment explaining why a token is absent necessarily names that token — a trap this repo
    has walked into three times in one session.
    """
    return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)


class TestWizardAllowlist(unittest.TestCase):
    """The server half. This is the freeze's last line of defence."""

    @classmethod
    def setUpClass(cls):
        with io.open(API, encoding="utf-8") as handle:
            cls.source = handle.read()
        cls.code = re.sub(r'"""[\s\S]*?"""', "", cls.source)

    def test_the_row_allowlist_is_exactly_the_answer_fields(self):
        match = re.search(r"ALLOWED_ROW_FIELDS\s*=\s*\{([^}]*)\}", self.code)
        self.assertIsNotNone(match, "ALLOWED_ROW_FIELDS is missing from api/quality_wizard.py")
        fields = set(re.findall(r'"(\w+)"', match.group(1)))
        self.assertEqual(
            fields,
            {"outcome", "measured_value", "notes", "photo"},
            "The wizard may write an answer and its evidence, nothing else.",
        )

    def test_no_frozen_field_is_writable(self):
        match = re.search(r"ALLOWED_ROW_FIELDS\s*=\s*\{([^}]*)\}", self.code)
        allowed = set(re.findall(r'"(\w+)"', match.group(1)))
        leaked = sorted(allowed & set(FROZEN_ROW_FIELDS))
        self.assertEqual(
            leaked,
            [],
            "These frozen fields are writable from the wizard: %s. They are the standard being "
            "inspected against, copied at generation. A wizard that can edit a bound can turn a "
            "failing measurement into a passing one from a phone." % leaked,
        )

    def test_the_parent_allowlist_excludes_identity_and_provenance(self):
        match = re.search(r"ALLOWED_PARENT_FIELDS\s*=\s*\{([^}]*)\}", self.code)
        self.assertIsNotNone(match)
        allowed = set(re.findall(r'"(\w+)"', match.group(1)))
        for field in (
            "project",
            "milestone",
            "master_template",
            "master_template_revision",
            "snapshot_hash",
            "scope_of_work",
            "inspector",
            "status",
            "fail_count",
        ):
            self.assertNotIn(field, allowed, f"{field} must not be writable from the wizard")

    def test_there_is_no_append_path_for_result_rows(self):
        """Every row was frozen at generation. One that could be added could be a check nobody
        contracted for, or a quiet replacement for one the inspector could not answer."""
        self.assertNotIn(
            'doc.append("results"',
            self.code,
            "the wizard API appends result rows; the row list is frozen at generation",
        )

    def test_writes_are_refused_on_a_submitted_inspection(self):
        self.assertEqual(
            self.code.count("already submitted"),
            2,
            "both save_inspection and finish_inspection must refuse a submitted inspection",
        )

    def test_stale_writes_are_rejected_rather_than_merged(self):
        """Merging blind would silently overwrite an answer nobody knows was lost."""
        self.assertIn("def _check_not_stale", self.code)
        # Indented call sites only -- the `def` line contains the same text.
        calls = re.findall(r"^\s+_check_not_stale\(doc, modified\)", self.code, re.M)
        self.assertEqual(
            len(calls),
            2,
            "both the save and the finish path must check the optimistic lock",
        )

    def test_every_endpoint_checks_permission_explicitly(self):
        """A whitelisted function is reachable over HTTP by any logged-in user."""
        endpoints = re.findall(r"@frappe\.whitelist\(\)\s*\ndef (\w+)", self.source)
        self.assertEqual(
            sorted(endpoints),
            [
                "finish_inspection",
                "get_inspection_bootstrap",
                "get_open_inspections",
                "save_inspection",
            ],
        )
        # get_open_inspections is scoped to the session user by its own filter, so it needs no
        # separate has_permission call; the other three address a document by name.
        self.assertEqual(self.code.count("frappe.has_permission("), 3)


class TestWizardMarkup(unittest.TestCase):
    """The client half."""

    @classmethod
    def setUpClass(cls):
        with io.open(WIZARD, "rb") as handle:
            cls.raw = handle.read()
        cls.source = cls.raw.decode("utf-8")
        cls.code = _strip_comments(cls.source)

    def test_rich_text_is_not_escaped(self):
        self.assertNotIn(
            "xss_sanitise(",
            self.code,
            "inspection_wizard.js calls frappe.utils.xss_sanitise, which escapes HTML by default "
            "and renders Text Editor content as visible tags. Use qw_rich_html() for Text Editor "
            "fields and qw_plain_html() for Small Text.",
        )

    def test_helpers_are_defined(self):
        for helper in ("function qw_rich_html(", "function qw_plain_html("):
            self.assertIn(helper, self.code, f"{helper} is missing from inspection_wizard.js")

    def test_rich_html_strips_executable_nodes(self):
        for needle in ("DOMParser", "script", "removeAttribute"):
            self.assertIn(needle, self.code, f"qw_rich_html no longer references {needle}")

    def test_plain_text_keeps_line_breaks(self):
        """A check label or a generation note is one item per line; collapsing them hides one."""
        self.assertIn("escape_html", self.code)
        self.assertRegex(
            self.code,
            r"qw_plain_html[\s\S]{0,400}?replace\(/\\n/g,\s*\"<br>\"\)",
            "qw_plain_html should escape and then convert newlines to <br>",
        )

    def test_autosave_failure_is_visible_and_retried(self):
        self.assertIn("set_save_state(", self.code)
        self.assertIn("schedule_retry(", self.code)
        self.assertRegex(
            self.code,
            r'set_save_state\("error"\)[\s\S]{0,120}?schedule_retry\(\)',
            "a failed save must both show an error and schedule a retry",
        )
        self.assertIn("beforeunload", self.code, "closing the tab must warn while edits are unsaved")

    def test_a_failed_save_puts_the_patch_back(self):
        """Dropping the patch on failure loses the answers silently, which is the whole failure."""
        self.assertRegex(
            self.code,
            r"catch\(\(\) => \{[\s\S]{0,600}?this\.pending = \{",
            "the catch branch must restore the pending patch before reporting the error",
        )

    def test_required_and_photo_rows_are_marked_before_submit(self):
        self.assertIn("function qw_required_chip(", self.code)
        for field in ("is_mandatory", "requires_photo"):
            self.assertRegex(
                self.code,
                rf"qw_required_chip[\s\S]{{0,400}}?row\.{field}",
                f"{field} rows are not marked in the wizard, so the submit refusal is the first "
                "time anybody hears about them",
            )

    def test_the_contracted_range_is_shown_on_a_measurement(self):
        """An inspector who cannot see the bound is guessing at what 'passes' means."""
        self.assertIn("function qw_range_text(", self.code)
        self.assertRegex(self.code, r"qw_range_text\(row\)")

    def test_out_of_range_comes_from_the_server(self):
        """Two implementations of the same arithmetic is one more than can stay correct."""
        self.assertRegex(
            self.code,
            r"row\.out_of_range = fresh\.out_of_range",
            "apply_state must take out_of_range from the server response",
        )
        self.assertNotRegex(
            self.code,
            r"(measured_value|value)\s*[<>]=?\s*row\.(min_value|max_value)",
            "the wizard re-derives out-of-range locally; it must display the server's answer",
        )

    def test_the_carried_and_contracted_sources_are_distinguishable(self):
        """A re-check and a contracted addendum row read differently from a template check, and
        an inspector who cannot tell them apart cannot tell what they are being asked."""
        for source in ("Carried Action", "Project Addendum"):
            self.assertIn(source, self.code, f"the wizard does not mark {source} rows")

    def test_no_raw_control_bytes(self):
        offenders = [
            i for i, byte in enumerate(self.raw) if byte < 9 or byte in (11, 12) or 14 <= byte < 32
        ]
        self.assertEqual(
            offenders,
            [],
            f"inspection_wizard.js contains raw control bytes at {offenders[:5]}. "
            "Write them as \\u0000-style escapes instead.",
        )


class TestWizardPage(unittest.TestCase):
    def test_the_page_is_not_named_after_a_workspace(self):
        """The router resolves the first path segment against workspaces before pages, so a Page
        named `quality` or `quality-control` would never render."""
        import json

        with io.open(PAGE_JSON, encoding="utf-8") as handle:
            page = json.load(handle)
        self.assertEqual(page["name"], "inspection-wizard")
        self.assertNotIn(page["name"], ("quality", "quality-control"))
        self.assertEqual(page["module"], "Quality")

    def test_the_page_grants_the_roles_that_do_inspections(self):
        import json

        with io.open(PAGE_JSON, encoding="utf-8") as handle:
            page = json.load(handle)
        roles = {row["role"] for row in page.get("roles", [])}
        self.assertIn("Quality Inspector", roles)
        self.assertIn("System Manager", roles)
        self.assertTrue(roles, "a Page with no roles is visible to everybody")


if __name__ == "__main__":
    unittest.main()
