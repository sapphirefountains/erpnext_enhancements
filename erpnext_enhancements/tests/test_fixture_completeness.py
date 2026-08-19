"""A fixture is the whole document, not a patch over it.

`bench migrate` -> `sync_fixtures` -> `import_doc(path, sort=True)` ->
`import_file_by_path(force=True)` -> `frappe.modules.import_file.import_doc`, which
calls `delete_old_doc()` — a real `frappe.delete_doc(..., force=1)` — and then
`frappe.get_doc(docdict).insert()`. Two consequences, both silent:

* **Any key the JSON does not name comes back empty**, on every migrate, however
  correct the value on the site was. (`creation` resets too — the two Print Formats
  here both read `creation == modified == 2026-08-18 15:12:09`, the timestamp of the
  deploy that re-inserted them.)
* **`frappe.get_doc(dict).insert()` applies no DocField defaults.** Defaults fire in
  `new_doc()`, so a field declared `default: "X"` in the doctype lands NULL, not "X" —
  which is *not* what happens when the framework adds the column, because MariaDB
  writes a column default into every existing row as part of the `ALTER`. The site is
  therefore correct the day it upgrades and wrong after the next deploy.

`force=True` means the timestamp/hash skip inside `import_file_by_path` never applies,
so this happens on **every** migrate, not only when the fixture file changed.

That combination cost real money once: `condition_type` (a v16 addition to
Notification, `default: "Python"`) was absent from `notification.json`, v16
`evaluate_alert` reads `if alert.condition_type == "Python" and alert.condition:`, and
a NULL does not fall back to evaluating the condition — it skips the check and sends
unconditionally. All nineteen managed Notifications lost their conditions at once and
"Email Team on Opportunity Won" mailed four group addresses on every save of every
Opportunity (v1.331.1).

So this suite pins the fields whose absence is known to be load-bearing. It is a
hand-maintained table on purpose: the generic form ("every field with a non-empty
DocField default") needs `frappe.get_meta`, and these bench-free suites have no bench.
The audit that produced the table is in the v1.331.1 changelog entry; re-run it after a
framework upgrade rather than trusting this list to stay complete on its own.

Bench-free: reads the fixture JSON only.

Run: python -m unittest erpnext_enhancements.tests.test_fixture_completeness
"""

import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "erpnext_enhancements" / "fixtures"

# doctype -> {fieldname: why it must be declared}
#
# Only fields whose DocField default is NOT the falsy value belong here. A `Check`
# defaulting to '0' lands NULL, and NULL and 0 are both falsy, so nothing observable
# changes and pinning it would be noise.
REQUIRED = {
    "Notification": {
        "condition_type": "NULL means frappe skips the condition and sends unconditionally",
        "notification_type": "default 'Alert'; frappe falls back with `or \"Alert\"`, so latent",
        "message_type": "default 'Markdown'; our bodies are HTML from the ee_* macros",
    },
    "Print Format": {
        # Page geometry. Nothing renders wrong today — these two are custom formats
        # whose own CSS does the work, and 22 of the 66 formats on the site (mostly
        # app-shipped ERPNext ones) also sit at margin 0. What is pinned here is that
        # the values stop *moving*: undeclared, any margin or font tuned in the Desk
        # is silently reverted by the next deploy, with nothing in the diff to show it.
        "print_format_for": "default 'DocType'",
        "margin_top": "default 15",
        "margin_bottom": "default 15",
        "margin_left": "default 15",
        "margin_right": "default 15",
        "font_size": "default 14; feeds `html, body { font-size: Npx }` in print_format.css",
        "page_number": "default 'Hide'",
    },
}


def fixture_docs():
    """Every record in every fixture file, as (file, doc) pairs."""
    for path in sorted(FIXTURES.glob("*.json")):
        docs = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(docs, list):
            docs = [docs]
        for doc in docs:
            yield path.name, doc


class TestLoadBearingFieldsAreDeclared(unittest.TestCase):
    def test_every_record_declares_them(self):
        seen = {dt: 0 for dt in REQUIRED}
        for fname, doc in fixture_docs():
            dt = doc.get("doctype")
            if dt not in REQUIRED:
                continue
            seen[dt] += 1
            for field, why in REQUIRED[dt].items():
                self.assertIn(
                    field,
                    doc,
                    f"{fname}: {dt} '{doc.get('name')}' does not declare `{field}` — "
                    f"fixture sync will erase it on every migrate ({why})",
                )
        for dt, n in seen.items():
            self.assertTrue(n, f"no {dt} records found in fixtures/ — has the table gone stale?")

    def test_the_table_only_lists_fields_that_matter(self):
        """A Check defaulting to '0' lands NULL, and both are falsy — pinning those
        would be noise that hides the fields where NULL is genuinely different."""
        for dt, fields in REQUIRED.items():
            for field, why in fields.items():
                self.assertTrue(why.strip(), f"{dt}.{field} has no stated reason")


class TestEveryFixtureRecordIsAddressable(unittest.TestCase):
    """`import_doc` looks the document up by `doctype` + `name` before deleting it.
    A record missing either is not a no-op — it inserts a second copy."""

    def test_doctype_and_name_are_present(self):
        for fname, doc in fixture_docs():
            self.assertIn("doctype", doc, f"{fname}: a record has no `doctype`")
            self.assertIn("name", doc, f"{fname}: a {doc.get('doctype')} record has no `name`")


if __name__ == "__main__":
    unittest.main()
