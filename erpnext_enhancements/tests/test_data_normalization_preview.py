"""Bench-free tests for the Data Normalization Preview report.

The report is a dry run the product owner approves the cleanup from, so what it must get
right is what it *leaves out* as much as what it lists:

* submitted and cancelled records are never listed, only counted;
* a Single is read through ``get_single_value`` and never with SQL against ``tabSingles``;
* a value no rule can fix is listed as typed, with its reason;
* a table or column this site does not have is skipped, not raised;
* the status filter narrows the list without changing the counts.

A minimal ``frappe`` stub is installed for the import and removed afterwards. Its ``db.sql``
reads the table and column names back out of the generated SQL, so the test exercises the
query the report really builds, including the ``LENGTH(col) > 0`` emptiness test.

Run: python -m unittest erpnext_enhancements.tests.test_data_normalization_preview -v
"""

import importlib
import json
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULE = "erpnext_enhancements.enhancements_core.report.data_normalization_preview.data_normalization_preview"
JS_PATH = (
    REPO_ROOT / "erpnext_enhancements" / "enhancements_core" / "report"
    / "data_normalization_preview" / "data_normalization_preview.js"
)

_saved = {}
report = None


class _Dict(dict):
    __getattr__ = dict.get


class FakeDB:
    def __init__(self, tables=None, singles=None):
        # {doctype: [row dicts]} — a row lists every column the table has.
        self.tables = tables or {}
        # {doctype: {fieldname: value}}
        self.singles = singles or {}
        self.queries = []
        self.single_reads = []

    def table_exists(self, doctype):
        return doctype in self.tables

    def has_column(self, doctype, column):
        if doctype not in self.tables:
            raise Exception(f"TableMissingError: tab{doctype}")
        rows = self.tables[doctype]
        return bool(rows) and column in rows[0]

    def exists(self, doctype, name):
        return doctype == "DocType" and name in self.singles

    def get_single_value(self, doctype, fieldname):
        self.single_reads.append((doctype, fieldname))
        if fieldname not in self.singles.get(doctype, {}):
            raise Exception(f"Field {fieldname} does not exist on {doctype}")
        return self.singles[doctype][fieldname]

    def sql(self, query, values=None, as_dict=False):
        self.queries.append(query)
        assert "tabSingles" not in query, "a Single must be read with get_single_value"
        m = re.match(r"select (.+) from `tab([^`]+)` where (.+)$", query, re.IGNORECASE)
        assert m, query
        columns = re.findall(r"`([^`]+)`", m.group(1))
        tested = re.findall(r"length\(`([^`]+)`\) > 0", m.group(3))
        assert tested, "the emptiness test must be LENGTH(col) > 0"
        out = []
        for row in self.tables[m.group(2)]:
            if any(len(row.get(c) or "") > 0 for c in tested):
                out.append(_Dict({c: row.get(c) for c in columns}))
        return out


class FakeMeta:
    def __init__(self, fields):
        self.fields = set(fields)

    def has_field(self, fieldname):
        return fieldname in self.fields


def _install(db):
    frappe = sys.modules["frappe"]
    frappe.db = db
    frappe.get_meta = lambda dt: FakeMeta(db.singles.get(dt, {}))


def setUpModule():
    global report
    for name in ("frappe", MODULE):
        _saved[name] = sys.modules.get(name)
    frappe = types.ModuleType("frappe")
    frappe._dict = _Dict
    frappe._ = lambda text, *a, **k: text
    frappe.db = FakeDB()
    sys.modules["frappe"] = frappe
    sys.modules.pop(MODULE, None)
    report = importlib.import_module(MODULE)


def tearDownModule():
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def contact(name, docstatus=0, **values):
    row = {
        "name": name, "docstatus": docstatus, "phone": None, "mobile_no": None, "email_id": None,
        "custom_home_phone": None, "first_name": None,
    }
    row.update(values)
    return row


class TestPreview(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB(
            tables={
                "Contact": [
                    contact("C-1", phone="801-555-0100", email_id="Nik@Example.com", first_name="Nik"),
                    contact("C-2", phone="65-688-000-88"),
                    contact("C-3", docstatus=1, phone="8015550100"),
                    contact("C-4", docstatus=2, email_id="X@Y.COM"),
                    contact("C-5", mobile_no="+18015550101"),
                    contact("C-6", first_name="   "),
                    contact("C-7", phone="X801-555-0142"),
                ],
                "Contact Phone": [
                    {"name": "row1", "docstatus": 0, "parenttype": "Contact", "parent": "C-1", "phone": "(801) 555-0100 x12"},
                ],
                "Address": [
                    {"name": "A-1", "docstatus": 0, "country": "Canada", "state": "Ontario", "pincode": "M5V 3L9",
                     "address_line1": "1 Main St,", "city": "Toronto"},
                    {"name": "A-2", "docstatus": 0, "country": "United States", "state": "utah", "pincode": "8401",
                     "address_line1": "2 Elm", "city": "Bountiful"},
                ],
                # Registered but has none of the columns the registry names: skipped quietly.
                "Bank": [{"name": "B-1", "docstatus": 0, "bank_name": "Zions"}],
            },
            singles={"Travel Settings": {"travel_desk_phone": "8015550199", "travel_desk_email": ""}},
        )
        _install(self.db)

    def _rows(self, **filters):
        return report.execute(filters)

    def _find(self, data, record, field):
        return [r for r in data if r["record"] == record and r["field"] == field]

    def test_submitted_and_cancelled_records_are_counted_not_listed(self):
        _cols, data, _msg, _chart, summary = self._rows(status=report.FILTER_EVERYTHING)
        self.assertFalse([r for r in data if r["record"] in ("C-3", "C-4")])
        counted = {s["label"]: s["value"] for s in summary}
        self.assertEqual(counted["Submitted records — left alone"], 2)

    def test_a_value_that_cannot_be_normalized_is_listed_as_typed(self):
        _cols, data, *_ = self._rows()
        (row,) = self._find(data, "C-2", "phone")
        self.assertEqual(row["status"], report.STATUS_STUCK)
        self.assertEqual(row["current"], "65-688-000-88")
        self.assertEqual(row["normalized"], "")
        self.assertEqual(row["reason"], "not a North American number and no +country code")

    def test_will_change_and_already_clean(self):
        _cols, data, *_ = self._rows(status=report.FILTER_EVERYTHING)
        (phone,) = self._find(data, "C-1", "phone")
        self.assertEqual((phone["status"], phone["normalized"]), (report.STATUS_CHANGE, "+18015550100"))
        (email,) = self._find(data, "C-1", "email_id")
        self.assertEqual(email["normalized"], "nik@example.com")
        (clean,) = self._find(data, "C-5", "mobile_no")
        self.assertEqual(clean["status"], report.STATUS_CLEAN)
        (blank,) = self._find(data, "C-6", "first_name")
        self.assertEqual((blank["status"], blank["normalized"]), (report.STATUS_CHANGE, ""),
                         "a whitespace-only value is found (LENGTH, not PAD SPACE equality)")

    def test_child_rows_link_to_their_parent(self):
        _cols, data, *_ = self._rows()
        (row,) = self._find(data, "C-1", "Contact Phone.phone")
        self.assertEqual(row["doctype"], "Contact")
        self.assertEqual(row["normalized"], "+18015550100,12")

    def test_address_state_and_zip_use_the_records_country(self):
        _cols, data, *_ = self._rows(status=report.FILTER_EVERYTHING)
        self.assertEqual(self._find(data, "A-1", "state")[0]["status"], report.STATUS_CLEAN)
        self.assertEqual(self._find(data, "A-1", "pincode")[0]["status"], report.STATUS_CLEAN)
        self.assertEqual(self._find(data, "A-1", "address_line1")[0]["normalized"], "1 Main St")
        self.assertEqual(self._find(data, "A-2", "state")[0]["normalized"], "UT")
        zip_row = self._find(data, "A-2", "pincode")[0]
        self.assertEqual(zip_row["status"], report.STATUS_STUCK)
        self.assertIn("leading zero", zip_row["reason"])

    def test_singles_go_through_get_single_value(self):
        _cols, data, *_ = self._rows()
        self.assertIn(("Travel Settings", "travel_desk_phone"), self.db.single_reads)
        (row,) = self._find(data, "Travel Settings", "travel_desk_phone")
        self.assertEqual((row["doctype"], row["normalized"]), ("Travel Settings", "+18015550199"))
        self.assertFalse([q for q in self.db.queries if "Travel Settings" in q])

    def test_unknown_tables_and_columns_are_skipped_without_raising(self):
        # Most registry doctypes are absent from the fake site and Bank has none of its
        # columns: the run must still finish, with one query per table that has columns.
        _cols, data, *_ = self._rows(status=report.FILTER_EVERYTHING)
        self.assertFalse([r for r in data if r["doctype"] == "Bank"])
        tables = sorted(re.search(r"from `tab([^`]+)`", q).group(1) for q in self.db.queries)
        self.assertEqual(tables, ["Address", "Contact", "Contact Phone"])

    def test_status_filter(self):
        _cols, attention, *_ = self._rows()
        self.assertEqual({r["status"] for r in attention}, {report.STATUS_CHANGE, report.STATUS_STUCK})
        _cols, stuck, *_ = self._rows(status=report.STATUS_STUCK)
        self.assertEqual({r["status"] for r in stuck}, {report.STATUS_STUCK})
        _cols, change, *_ = self._rows(status=report.STATUS_CHANGE)
        self.assertEqual({r["status"] for r in change}, {report.STATUS_CHANGE})
        _cols, everything, _msg, _chart, summary = self._rows(status=report.FILTER_EVERYTHING)
        self.assertIn(report.STATUS_CLEAN, {r["status"] for r in everything})
        self.assertEqual(len(everything), len(attention) + sum(r["status"] == report.STATUS_CLEAN for r in everything))
        # The counts describe the whole walk whatever the filter shows.
        _cols, _data, _msg, _chart, narrow = self._rows(status=report.STATUS_STUCK)
        self.assertEqual(narrow, summary)

    def test_doctype_and_kind_filters(self):
        _cols, data, *_ = self._rows(doctype="Address", status=report.FILTER_EVERYTHING)
        self.assertEqual({r["doctype"] for r in data}, {"Address"})
        _cols, data, *_ = self._rows(kind="email", status=report.FILTER_EVERYTHING)
        self.assertEqual({r["kind"] for r in data}, {"email"})
        _cols, data, *_ = self._rows(doctype="No Such Doctype")
        self.assertEqual(data, [])

    def test_a_value_no_rule_can_read_gets_a_proposed_fix_when_one_is_sure(self):
        _cols, data, *_ = self._rows()
        (row,) = self._find(data, "C-7", "phone")
        self.assertEqual(row["status"], report.STATUS_STUCK)
        self.assertEqual(row["proposed"], "+18015550142")
        self.assertIn("phone number in the text", row["fix_why"])
        self.assertEqual(json.loads(row["fix_key"]), ["Contact", "C-7", "phone"])
        self.assertNotIn("_fix", row, "the write plan never reaches the browser")
        # A whitespace-only value the cleanup would blank is offered as a fix too.
        (blank,) = self._find(data, "C-6", "first_name")
        self.assertNotIn("fix_key", blank, "text has no fix rules: names are left for a person")
        (zip_row,) = self._find(data, "A-2", "pincode")
        self.assertNotIn("fix_key", zip_row, "a Utah ZIP never gets a leading zero")

    def test_the_proposed_fix_filter_lists_only_rows_with_one(self):
        _cols, data, *_ = self._rows(status=report.FILTER_FIXES)
        self.assertTrue(data)
        self.assertTrue(all(row.get("fix_key") for row in data))
        self.assertIn("C-7", {row["record"] for row in data})

    def test_the_proposal_columns_are_shown(self):
        names = [column["fieldname"] for column in report.get_columns()]
        self.assertEqual(names[-2:], ["proposed", "fix_why"])

    def test_the_js_applies_only_ticked_rows_through_apply_fixes(self):
        source = JS_PATH.read_text(encoding="utf-8")
        self.assertIn("checkboxColumn: true", source)
        self.assertIn("get_checked_items()", source)
        self.assertIn('"erpnext_enhancements.utils.normalize_cleanup.apply_fixes"', source)
        self.assertIn("row.fix_key", source)

    def test_identifiers_with_a_backtick_are_refused(self):
        with self.assertRaises(ValueError):
            report._quote("tabContact` where 1=1 -- ")

    def test_the_js_filter_offers_exactly_the_status_choices(self):
        source = JS_PATH.read_text(encoding="utf-8")
        for choice in report._WANTED:
            self.assertIn(f'"{choice}"', source)
        self.assertIn(f'default: "{report.FILTER_ATTENTION}"', source)


if __name__ == "__main__":
    unittest.main()
