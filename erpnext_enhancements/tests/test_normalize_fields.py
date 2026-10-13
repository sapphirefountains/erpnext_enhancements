"""Bench-free tests for the normalization field registry (``utils/normalize_fields.py``).

The registry is a curated list, and a curated list rots in three ways this suite catches:

* a kind the registry names that ``utils.normalize`` has no rule for — the report would
  raise on the first row;
* a field both registered and excluded — the two halves of the list disagree about it;
* a field that does not exist. A ``custom_`` field must be in the Custom Field fixtures and a
  field on one of this app's doctypes must be in that doctype's JSON; a missing column is
  skipped quietly by the report, which is exactly why nothing else would notice.

Fields on stock doctypes (Address, Contact, Lead...) cannot be checked without a bench; they
are listed in :data:`TRUSTED_STOCK` below as the explicit record of what was taken on trust
(from production metadata, 2026-10-13).

Run: python -m unittest erpnext_enhancements.tests.test_normalize_fields -v
"""

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.utils import normalize
from erpnext_enhancements.utils import normalize_fields as reg

APP_DIR = REPO_ROOT / "erpnext_enhancements"
FIXTURES = APP_DIR / "fixtures"


def _scrub(name):
    return name.replace(" ", "_").replace("-", "_").lower()


def _custom_fields():
    found = set()
    for name in ("custom_field.json", "custom_field_hrms.json"):
        for record in json.loads((FIXTURES / name).read_text(encoding="utf-8")):
            found.add((record["dt"], record["fieldname"]))
    return found


def _app_doctype(doctype):
    """This app's DocType JSON for ``doctype``, or None for a stock doctype."""
    scrubbed = _scrub(doctype)
    for path in APP_DIR.glob(f"*/doctype/{scrubbed}/{scrubbed}.json"):
        return json.loads(path.read_text(encoding="utf-8"))
    return None


CUSTOM_FIELDS = _custom_fields()

#: Stock (Frappe / ERPNext / HRMS) fields taken on trust from production metadata.
TRUSTED_STOCK = {
    (doctype, fieldname)
    for doctype, fields in reg.FIELDS.items()
    if _app_doctype(doctype) is None
    for fieldname in fields
    if not fieldname.startswith("custom_") and (doctype, fieldname) not in CUSTOM_FIELDS
}


class TestRegistry(unittest.TestCase):
    def test_every_kind_has_a_rule(self):
        for kind in reg.kinds():
            with self.subTest(kind=kind):
                self.assertIn(kind, normalize.KINDS)

    def test_country_kinds_only_where_a_country_is_known(self):
        for doctype, fields in reg.FIELDS.items():
            for fieldname, kind in fields.items():
                if kind in normalize.COUNTRY_KINDS:
                    with self.subTest(field=f"{doctype}.{fieldname}"):
                        self.assertIn(doctype, reg.COUNTRY_FIELD)

    def test_nothing_is_both_registered_and_excluded(self):
        for doctype, fields in reg.FIELDS.items():
            self.assertNotIn(doctype, reg.EXCLUDED, f"{doctype} is registered and excluded whole")
            for fieldname in fields:
                with self.subTest(field=f"{doctype}.{fieldname}"):
                    self.assertFalse(reg.is_excluded(doctype, fieldname))

    def test_every_exclusion_says_why(self):
        for key, reason in reg.EXCLUDED.items():
            with self.subTest(key=key):
                self.assertTrue(reason and reason.strip())
                self.assertLessEqual(key.count("."), 1)

    def test_singles_and_child_tables_are_registered(self):
        self.assertFalse(reg.SINGLES & reg.CHILD_TABLES)
        for doctype in reg.SINGLES | reg.CHILD_TABLES:
            with self.subTest(doctype=doctype):
                self.assertIn(doctype, reg.FIELDS)

    def test_helpers(self):
        self.assertEqual(reg.fields_for("Address")["pincode"], "zip")
        self.assertEqual(reg.fields_for("No Such Doctype"), {})
        copy = reg.fields_for("Bank")
        copy["website"] = "phone"
        self.assertEqual(reg.FIELDS["Bank"]["website"], "website", "fields_for must return a copy")
        self.assertEqual(reg.kinds(), sorted(set(reg.kinds())))
        self.assertTrue(reg.is_excluded("Sales Invoice", "contact_email"))
        self.assertTrue(reg.is_excluded("Lead", "phone_ext"))
        self.assertFalse(reg.is_excluded("Lead", "phone"))


class TestFieldsExist(unittest.TestCase):
    def test_custom_fields_are_in_the_fixtures(self):
        for doctype, fields in reg.FIELDS.items():
            if _app_doctype(doctype) is not None:
                continue
            for fieldname in fields:
                if fieldname.startswith("custom_"):
                    with self.subTest(field=f"{doctype}.{fieldname}"):
                        self.assertIn((doctype, fieldname), CUSTOM_FIELDS)

    def test_app_doctype_fields_are_in_the_doctype_json(self):
        seen = 0
        for doctype, fields in reg.FIELDS.items():
            meta = _app_doctype(doctype)
            if meta is None:
                continue
            seen += 1
            names = {f.get("fieldname") for f in meta.get("fields") or []}
            for fieldname in fields:
                with self.subTest(field=f"{doctype}.{fieldname}"):
                    self.assertIn(fieldname, names)
        self.assertGreaterEqual(seen, 10, "the doctype JSON glob found too few of this app's doctypes")

    def test_app_singles_and_child_tables_match_their_json(self):
        """The report reads a Single with get_single_value and links a child row to its
        parent; both decisions come from these two sets, so they must match the doctype."""
        for doctype in reg.FIELDS:
            meta = _app_doctype(doctype)
            if meta is None:
                continue
            with self.subTest(doctype=doctype):
                self.assertEqual(bool(meta.get("issingle")), doctype in reg.SINGLES)
                self.assertEqual(bool(meta.get("istable")), doctype in reg.CHILD_TABLES)

    def test_the_trusted_stock_list_is_what_it_claims(self):
        """Taken on trust, and named: no custom_ field hides in it."""
        self.assertTrue(TRUSTED_STOCK)
        for doctype, fieldname in TRUSTED_STOCK:
            self.assertFalse(fieldname.startswith("custom_"), f"{doctype}.{fieldname}")


if __name__ == "__main__":
    unittest.main()
