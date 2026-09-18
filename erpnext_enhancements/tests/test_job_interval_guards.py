"""Tests for Job Interval controller guards: overlap, lock, and manual-start fields.
"""
import sys
import types
import unittest
import ast
import json
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
WORKFORCE = APP / "workforce"
JI_PY = WORKFORCE / "doctype" / "job_interval" / "job_interval.py"
JI_JSON = WORKFORCE / "doctype" / "job_interval" / "job_interval.json"

if str(APP.parent) not in sys.path:
    sys.path.insert(0, str(APP.parent))

overlap = None
timesheet_lock = None

STATE = {
    "sql_calls": [],
    "sql_results": [],
    "has_column": True,
    "get_value_results": None,
    "throw_calls": [],
}

def setUpModule():
    global frappe, overlap, timesheet_lock
    frappe = types.ModuleType("frappe")
    frappe.db = types.SimpleNamespace()
    
    def mock_sql(query, params, as_dict=False):
        STATE["sql_calls"].append((query, params))
        return STATE["sql_results"].pop(0) if STATE["sql_results"] else []
        
    def mock_has_column(doctype, column):
        return STATE["has_column"]
        
    def mock_get_value(doctype, filters, fieldname, as_dict=False):
        return STATE["get_value_results"]
        
    def mock_throw(msg, title=None):
        STATE["throw_calls"].append(msg)
        raise Exception(msg)
        
    def mock_get_doc(doctype, name):
        return types.SimpleNamespace(
            name=name, project="P1", start_time="2023-01-01 10:00:00", end_time="2023-01-01 11:00:00"
        )
        
    def mock__(msg):
        return msg
        
    frappe.db.sql = mock_sql
    frappe.db.has_column = mock_has_column
    frappe.db.get_value = mock_get_value
    frappe.db.exists = lambda *args, **kwargs: False
    frappe.throw = mock_throw
    frappe.get_doc = mock_get_doc
    frappe._ = mock__
    
    frappe.model = types.SimpleNamespace()
    frappe.model.document = types.SimpleNamespace()
    class DummyDoc:
        pass
    frappe.model.document.Document = DummyDoc
    
    sys.modules["frappe"] = frappe
    sys.modules["frappe.model.document"] = frappe.model.document
    sys.modules["erpnext_enhancements.workforce.costing"] = types.SimpleNamespace(
        stamp_cost=lambda doc: None
    )
    
    from erpnext_enhancements.workforce import overlap as _overlap
    from erpnext_enhancements.workforce import timesheet_lock as _timesheet_lock
    overlap = _overlap
    timesheet_lock = _timesheet_lock

class TestJobIntervalGuards(unittest.TestCase):
    def setUp(self):
        STATE["sql_calls"] = []
        STATE["sql_results"] = []
        STATE["has_column"] = True
        STATE["get_value_results"] = None
        STATE["throw_calls"] = []
        
    def test_overlap_sql_touching_and_intersection(self):
        # Ranges touching at endpoints DO NOT overlap (a.start < b.end AND a.end > b.start)
        # Ranges that genuinely intersect DO overlap.
        # This is enforced by the SQL query strictly using < and >
        overlap.find_overlaps("EMP", "2023-01-01 10:00:00", "2023-01-01 11:00:00", exclude="JI-123")
        query, params = STATE["sql_calls"][0]
        
        self.assertIn("start_time < %(end)s", query)
        self.assertIn("end_time > %(start)s", query)
        self.assertEqual(params["start"], "2023-01-01 10:00:00")
        self.assertEqual(params["end"], "2023-01-01 11:00:00")
        
    def test_overlap_sql_null_trap(self):
        # An interval with a NULL end_time (still open) overlaps a later interval.
        # We ensure end_time IS NULL is included so SQL doesn't evaluate NULL > start as false.
        overlap.find_overlaps("EMP", "2023-01-01 10:00:00", None, exclude="JI-123")
        query, params = STATE["sql_calls"][0]
        
        self.assertIn("end_time IS NULL OR end_time > %(start)s", query)
        self.assertEqual(params["end"], "2999-12-31 23:59:59")
        
    def test_overlap_excludes_self(self):
        # The row being saved is excluded from its own overlap check
        overlap.find_overlaps("EMP", "2023-01-01 10:00:00", "2023-01-01 11:00:00", exclude="JI-123")
        query, params = STATE["sql_calls"][0]
        
        self.assertIn("name != %(exclude)s", query)
        self.assertEqual(params["exclude"], "JI-123")
        
    def test_assert_not_locked(self):
        doc = types.SimpleNamespace(name="JI-1")
        
        # Does nothing when none links
        STATE["get_value_results"] = None
        timesheet_lock.assert_not_locked(doc)
        
        # Throws when a submitted timesheet links it
        STATE["get_value_results"] = types.SimpleNamespace(parent="TS-1", docstatus=1)
        with self.assertRaises(Exception) as cm:
            timesheet_lock.assert_not_locked(doc)
        self.assertIn("TS-1", str(cm.exception))
        
    def test_timesheet_lock_no_column(self):
        doc = types.SimpleNamespace(name="JI-1")
        STATE["has_column"] = False
        STATE["get_value_results"] = types.SimpleNamespace(parent="TS-1", docstatus=1)
        
        # Should do nothing because the column doesn't exist
        timesheet_lock.assert_not_locked(doc)
        
    def test_manual_start_validation(self):
        from erpnext_enhancements.workforce.doctype.job_interval.job_interval import JobInterval
        
        doc = JobInterval()
        doc.name = "JI-NEW"
        doc.status = "Open"
        doc.employee = "EMP"
        doc.start_time = "2023-01-01 10:00:00"
        doc.end_time = None
        
        # Blank/whitespace reason is refused
        doc.manual_start = 1
        doc.manual_start_reason = "   "
        with self.assertRaises(Exception) as cm:
            doc.validate()
        self.assertIn("reason is required", str(cm.exception))
        
        # Real reason passes
        doc.manual_start_reason = "forgot to clock in"
        doc.validate()  # should not raise
        
    def test_json_defaults(self):
        """
        normal-doctype ALTER rule: A Check may safely carry "0", but a new String field
        must carry NO default or the ALTER stamps the default string across the whole history.
        """
        with open(JI_JSON) as f:
            schema = json.load(f)
            
        fields = {df["fieldname"]: df for df in schema["fields"]}
        
        self.assertEqual(fields["manual_start"].get("default"), "0")
        self.assertNotIn("default", fields["manual_start_reason"])
        
    def test_validate_order_structurally(self):
        with open(JI_PY) as f:
            src = f.read()
            
        tree = ast.parse(src)
        validate_def = [
            n for n in tree.body 
            if isinstance(n, ast.ClassDef) and n.name == "JobInterval"
        ][0].body
        
        validate_mth = [
            n for n in validate_def 
            if isinstance(n, ast.FunctionDef) and n.name == "validate"
        ][0]
        
        call_names = []
        for node in validate_mth.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Name):
                    call_names.append(func.id)
                    
        filtered = [n for n in call_names if n in ("assert_not_locked", "assert_no_overlap")]
        self.assertEqual(filtered, ["assert_not_locked", "assert_no_overlap"])
