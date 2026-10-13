"""Bench-free tests for the normalization cleanup (form kit P2-C).

The cleanup rewrites stored phones, emails, websites, address parts and names across the
site, once, on deploy. Nik approved exactly what the Data Normalization Preview report listed
as "Will change", so the tests hold it to that and to everything it must leave alone:

* it writes the report's "Will change" rows and nothing else — never a "Can't normalize"
  value, a submitted or cancelled record, or a Single;
* it never turns a value blank;
* a value edited between the read and the write keeps the edit (compared exactly, so PAD SPACE
  cannot call two values equal);
* it writes with ``update_modified=False`` and clears the parent's cache for a child row;
* the undo file is saved before the first write, and when it cannot be saved nothing is written;
* the summary comment on the report carries counts and never a value;
* the kill switch stops it, a second run plans nothing, and neither it nor the patch can raise;
* ``undo()`` restores only values that still hold what the cleanup wrote.

A minimal ``frappe`` stub is installed for the import and removed afterwards. Its ``db.sql``
answers the report's own query, so the plan is the report's plan.

Run: python -m unittest erpnext_enhancements.tests.test_normalize_cleanup -v
"""

import datetime
import importlib
import io
import json
import re
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REPORT_MODULE = "erpnext_enhancements.enhancements_core.report.data_normalization_preview.data_normalization_preview"
MODULES = (
    REPORT_MODULE,
    "erpnext_enhancements.utils.normalize_hook",
    "erpnext_enhancements.utils.normalize_cleanup",
    "erpnext_enhancements.patches.normalize_stored_values",
)

_saved = {}
cleanup = None
report = None
patch = None


class _Dict(dict):
    __getattr__ = dict.get


class FakeDB:
    def __init__(self, tables=None, singles=None):
        self.tables = tables or {}
        self.singles = singles or {}
        self.writes = []
        self.commits = 0
        self.events = []
        # {(table, name, field): value} applied on the first get_value of that cell, as if a
        # person saved the record after the plan was read.
        self.edits = {}
        self.fail_reads = set()

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
        if fieldname not in self.singles.get(doctype, {}):
            raise Exception(f"Field {fieldname} does not exist on {doctype}")
        return self.singles[doctype][fieldname]

    def sql(self, query, values=None, as_dict=False):
        assert "tabSingles" not in query
        m = re.match(r"select (.+) from `tab([^`]+)` where (.+)$", query, re.IGNORECASE)
        assert m, query
        columns = re.findall(r"`([^`]+)`", m.group(1))
        tested = re.findall(r"length\(`([^`]+)`\) > 0", m.group(3))
        return [
            _Dict({c: row.get(c) for c in columns})
            for row in self.tables[m.group(2)]
            if any(len(row.get(c) or "") > 0 for c in tested)
        ]

    def _row(self, table, name):
        for row in self.tables.get(table, []):
            if row["name"] == name:
                return row
        raise Exception(f"{table} {name} not found")

    def get_value(self, table, name, fieldname):
        key = (table, name, fieldname)
        if key in self.fail_reads:
            raise Exception("OperationalError")
        if key in self.edits:
            self._row(table, name)[fieldname] = self.edits.pop(key)
        return self._row(table, name).get(fieldname)

    def set_value(self, table, name, fieldname, value, update_modified=True):
        self.writes.append((table, name, fieldname, value, update_modified))
        self.events.append(("write", table, name, fieldname))
        self._row(table, name)[fieldname] = value

    def commit(self):
        self.commits += 1


class FakeDocs:
    """``frappe.get_doc`` for the two documents the cleanup inserts and the one undo reads."""

    def __init__(self, db):
        self.db = db
        self.inserted = []
        self.fail_file = False

    def get_doc(self, spec, filters=None):
        if isinstance(spec, dict):
            return _Inserted(self, spec)
        assert spec == "File", spec
        for doc in self.inserted:
            if doc["doctype"] == "File" and doc["file_name"] == filters["file_name"]:
                return types.SimpleNamespace(get_content=lambda doc=doc: doc["content"])
        raise Exception("DoesNotExistError")


class _Inserted:
    def __init__(self, docs, spec):
        self.docs = docs
        self.spec = spec

    def insert(self, ignore_permissions=False):
        if self.spec["doctype"] == "File" and self.docs.fail_file:
            raise Exception("PermissionError")
        self.docs.inserted.append(dict(self.spec))
        self.docs.db.events.append(("insert", self.spec["doctype"]))
        return self


def setUpModule():
    global cleanup, report, patch
    for name in ("frappe", *MODULES):
        _saved[name] = sys.modules.get(name)
        sys.modules.pop(name, None)
    frappe = types.ModuleType("frappe")
    frappe._dict = _Dict
    frappe._ = lambda text, *a, **k: text
    frappe.conf = {}
    frappe.flags = _Dict()
    frappe.utils = types.SimpleNamespace(now_datetime=lambda: datetime.datetime(2026, 10, 13, 18, 5, 1))
    sys.modules["frappe"] = frappe
    report = importlib.import_module(REPORT_MODULE)
    cleanup = importlib.import_module("erpnext_enhancements.utils.normalize_cleanup")
    patch = importlib.import_module("erpnext_enhancements.patches.normalize_stored_values")


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


def site():
    return FakeDB(
        tables={
            "Contact": [
                contact("C-1", phone="801-555-0100", email_id="Nik@Example.com", first_name="Nik"),
                contact("C-2", phone="65-688-000-88"),
                contact("C-3", docstatus=1, phone="8015550100"),
                contact("C-4", docstatus=2, email_id="X@Y.COM"),
                contact("C-5", mobile_no="+18015550101"),
                contact("C-6", first_name="   "),
                contact("C-7", phone="(801) 555-0102 "),
            ],
            "Contact Phone": [
                {"name": "row1", "docstatus": 0, "parenttype": "Contact", "parent": "C-1",
                 "phone": "(801) 555-0100 x12"},
            ],
            "Address": [
                {"name": "A-2", "docstatus": 0, "country": "United States", "state": "utah",
                 "pincode": "8401", "address_line1": "2 Elm", "city": "Bountiful"},
            ],
        },
        singles={"Travel Settings": {"travel_desk_phone": "8015550199", "travel_desk_email": ""}},
    )


class CleanupCase(unittest.TestCase):
    def setUp(self):
        self.db = site()
        self.docs = FakeDocs(self.db)
        frappe = sys.modules["frappe"]
        frappe.db = self.db
        frappe.conf = {}
        frappe.get_doc = self.docs.get_doc
        frappe.get_meta = lambda dt: types.SimpleNamespace(
            has_field=lambda f, dt=dt: f in self.db.singles.get(dt, {})
        )
        self.cleared = []
        frappe.clear_document_cache = lambda dt, name=None: self.cleared.append((dt, name))
        self.logged = []
        frappe.log_error = lambda title=None, message=None, **k: self.logged.append((title, message))

    def value(self, table, name, field):
        return self.db._row(table, name)[field]

    def comment(self):
        (comment,) = [d for d in self.docs.inserted if d["doctype"] == "Comment"]
        return comment


class TestWhatItWrites(CleanupCase):
    def test_writes_exactly_the_will_change_rows(self):
        summary = cleanup.run()
        self.assertEqual(self.value("Contact", "C-1", "phone"), "+18015550100")
        self.assertEqual(self.value("Contact", "C-1", "email_id"), "nik@example.com")
        self.assertEqual(self.value("Contact", "C-7", "phone"), "+18015550102")
        self.assertEqual(self.value("Contact Phone", "row1", "phone"), "+18015550100,12")
        self.assertEqual(self.value("Address", "A-2", "state"), "UT")
        written = {(t, n, f) for t, n, f, _v, _m in self.db.writes}
        self.assertEqual(
            written,
            {
                ("Contact", "C-1", "phone"),
                ("Contact", "C-1", "email_id"),
                ("Contact", "C-7", "phone"),
                ("Contact Phone", "row1", "phone"),
                ("Address", "A-2", "state"),
            },
        )
        self.assertEqual((summary["planned"], summary["written"], summary["failed"]), (5, 5, 0))
        self.assertEqual(summary["by_field"]["Contact.phone"], 2)

    def test_the_plan_is_the_reports_will_change_list(self):
        _cols, listed, *_ = report.execute({"status": report.STATUS_CHANGE})
        planned = cleanup.plan()
        self.assertEqual(
            [(r["doctype"], r["record"], r["field"]) for r in planned],
            [(r["doctype"], r["record"], r["field"]) for r in listed if r["doctype"] != "Travel Settings"],
        )
        self.assertFalse([r for r in listed if "_target" in r], "the report sends no write targets")

    def test_leaves_what_it_must_not_touch(self):
        summary = cleanup.run()
        self.assertEqual(self.value("Contact", "C-2", "phone"), "65-688-000-88")
        self.assertEqual(self.value("Address", "A-2", "pincode"), "8401")
        self.assertEqual(self.value("Contact", "C-3", "phone"), "8015550100")
        self.assertEqual(self.value("Contact", "C-4", "email_id"), "X@Y.COM")
        self.assertEqual(self.value("Contact", "C-6", "first_name"), "   ")
        self.assertEqual(summary["left_blank"], 1)
        self.assertFalse([w for w in self.db.writes if w[0] == "Travel Settings"], "never a Single")

    def test_writes_without_touching_modified_and_clears_the_parent_cache(self):
        cleanup.run()
        self.assertTrue(self.db.writes)
        self.assertEqual({w[4] for w in self.db.writes}, {False})
        self.assertIn(("Contact", "C-1"), self.cleared)
        self.assertGreaterEqual(self.db.commits, 3, "one commit per table")

    def test_an_edit_made_while_it_runs_wins(self):
        self.db.edits[("Contact", "C-1", "email_id")] = "nik@sapphire.example"
        # PAD SPACE would call these equal in SQL; the comparison is exact.
        self.db.edits[("Contact", "C-7", "phone")] = "(801) 555-0102"
        summary = cleanup.run()
        self.assertEqual(self.value("Contact", "C-1", "email_id"), "nik@sapphire.example")
        self.assertEqual(self.value("Contact", "C-7", "phone"), "(801) 555-0102")
        self.assertEqual((summary["edited_since"], summary["written"]), (2, 3))

    def test_a_second_run_plans_nothing(self):
        cleanup.run()
        writes = len(self.db.writes)
        again = cleanup.run()
        self.assertEqual((again["planned"], again["written"]), (0, 0))
        self.assertEqual(len(self.db.writes), writes)


class TestUndoFile(CleanupCase):
    def test_saved_privately_before_the_first_write(self):
        summary = cleanup.run()
        self.assertEqual(self.db.events[0], ("insert", "File"))
        (file_doc,) = [d for d in self.docs.inserted if d["doctype"] == "File"]
        self.assertEqual(file_doc["is_private"], 1)
        self.assertNotIn("attached_to_doctype", file_doc, "attached to nothing: Administrator only")
        self.assertEqual(file_doc["file_name"], "data-normalization-undo-20261013-180501.json")
        self.assertEqual(summary["undo_file"], file_doc["file_name"])
        changes = json.loads(file_doc["content"])["changes"]
        self.assertIn(
            {"doctype": "Contact Phone", "name": "row1", "field": "phone", "parent_doctype": "Contact",
             "parent": "C-1", "before": "(801) 555-0100 x12", "after": "+18015550100,12"},
            changes,
        )
        self.assertEqual(len(changes), 5)

    def test_no_undo_file_no_writes(self):
        self.docs.fail_file = True
        summary = cleanup.run()
        self.assertEqual(self.db.writes, [])
        self.assertIn("nothing was written", summary["stopped"])
        self.assertTrue(self.logged)

    def test_undo_restores_only_values_still_as_written(self):
        summary = cleanup.run()
        self.db._row("Contact", "C-7")["phone"] = "+13855550000"  # edited after the cleanup
        result = cleanup.undo(summary["undo_file"])
        self.assertEqual((result["restored"], result["edited_since"], result["failed"]), (4, 1, 0))
        self.assertEqual(self.value("Contact Phone", "row1", "phone"), "(801) 555-0100 x12")
        self.assertEqual(self.value("Contact", "C-1", "email_id"), "Nik@Example.com")
        self.assertEqual(self.value("Contact", "C-7", "phone"), "+13855550000")

    def test_undo_of_a_missing_file_does_not_raise(self):
        result = cleanup.undo("no-such-file.json")
        self.assertTrue(result["unreadable"])
        self.assertEqual(result["restored"], 0)


class TestRecordAndSafety(CleanupCase):
    def test_the_report_comment_has_counts_and_no_values(self):
        cleanup.run()
        comment = self.comment()
        self.assertEqual((comment["reference_doctype"], comment["reference_name"]), ("Report", cleanup.REPORT))
        self.assertIn("5 values rewritten of 5 planned", comment["content"])
        self.assertIn("Contact Phone.phone: 1", comment["content"])
        for value in ("801", "nik@", "Nik@", "utah", "UT<", "+1"):
            self.assertNotIn(value, comment["content"])

    def test_the_kill_switch_stops_it(self):
        sys.modules["frappe"].conf = {"disable_value_normalization": 1}
        summary = cleanup.run()
        self.assertEqual(self.db.writes, [])
        self.assertEqual(self.docs.inserted, [])
        self.assertIn("disable_value_normalization", summary["stopped"])

    def test_a_failed_write_is_counted_and_logged_once(self):
        self.db.fail_reads = {("Contact", "C-1", "phone"), ("Contact", "C-1", "email_id")}
        summary = cleanup.run()
        self.assertEqual((summary["failed"], summary["written"]), (2, 3))
        self.assertEqual(len(self.logged), 1)
        self.assertNotIn("801", self.logged[0][1])

    def test_run_never_raises(self):
        def broken(**kwargs):
            raise RuntimeError("boom")

        original = report.collect
        report.collect = broken
        try:
            summary = cleanup.run()
        finally:
            report.collect = original
        self.assertIn("RuntimeError", summary["stopped"])
        self.assertEqual(self.db.writes, [])

    def test_the_patch_runs_it_and_never_raises(self):
        out = io.StringIO()
        with redirect_stdout(out):
            patch.execute()
        self.assertIn("5 of 5 written", out.getvalue())

        original = cleanup.run
        cleanup.run = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            with redirect_stdout(io.StringIO()):
                patch.execute()
        finally:
            cleanup.run = original
        self.assertTrue(self.logged)


if __name__ == "__main__":
    unittest.main()
