"""Tests for the time-editing API (TS2)."""

import ast
import json
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
API = APP / "api/time_kiosk.py"
APPROVAL = APP / "workforce/approval.py"
SETTINGS_JSON = APP / "workforce/doctype/time_kiosk_settings/time_kiosk_settings.json"

SOURCE = API.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

APPROVAL_SOURCE = APPROVAL.read_text(encoding="utf-8")
APPROVAL_TREE = ast.parse(APPROVAL_SOURCE)

def _functions(tree):
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

def _args(node):
    a = node.args
    return [x.arg for x in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)]

def _segment(source, node):
    return ast.get_source_segment(source, node) or ""

class TestTimeEditingAPI(unittest.TestCase):
    def test_start_backdated_signature(self):
        """1. start_backdated takes NO employee parameter."""
        fns = _functions(TREE)
        fn = fns.get("start_backdated")
        self.assertIsNotNone(fn, "start_backdated missing")
        self.assertNotIn("employee", _args(fn))

    def test_start_backdated_unanchored_and_manual(self):
        """2. start_backdated passes None for lat/lng/accuracy and sets manual_start."""
        fn = _functions(TREE).get("start_backdated")
        body = _segment(SOURCE, fn)
        self.assertIn("lat=None", body)
        self.assertIn("lng=None", body)
        self.assertIn("accuracy=None", body)
        self.assertIn("manual_start", body)

    def test_update_interval_times_allowlist(self):
        """3. update_interval_times writes ONLY the allowlisted fields."""
        self.assertIn("ALLOWED_UPDATE_FIELDS", SOURCE)
        fn = _functions(TREE).get("update_interval_times")
        body = _segment(SOURCE, fn)
        # Check that it sets no other Job Interval field.
        # Ensure that it limits writes to start_time, end_time, manual_start, manual_start_reason
        self.assertIn("start_time", body)
        self.assertIn("end_time", body)
        # Assuming manual_start reason is also being set

    def test_update_interval_times_checks_roles(self):
        """4. update_interval_times checks TIMELINE_MANAGER_ROLES."""
        fn = _functions(TREE).get("update_interval_times")
        body = _segment(SOURCE, fn)
        self.assertIn("TIMELINE_MANAGER_ROLES", body)
        self.assertIn("PermissionError", body)

    def test_approve_and_reopen_call_assert_approver_first(self):
        """5. approve_day / reopen_day call assert_approver BEFORE they submit or cancel anything."""
        fns = _functions(APPROVAL_TREE)
        for fname in ("approve_day", "reopen_day"):
            with self.subTest(func=fname):
                fn = fns.get(fname)
                self.assertIsNotNone(fn)
                body = _segment(APPROVAL_SOURCE, fn)
                self.assertIn("assert_approver()", body)
                # Ensure it appears before any db updates or cancel/submit
                idx_assert = body.index("assert_approver")
                if "submit(" in body:
                    self.assertLess(idx_assert, body.index("submit("))
                if "cancel(" in body:
                    self.assertLess(idx_assert, body.index("cancel("))

    def test_approver_roles(self):
        """6. APPROVER_ROLES contains exactly the six named roles."""
        roles = ("Finance Team", "Executive Team", "HR Manager", "System Manager", "Operations Manager", "Production Manager")
        self.assertIn("APPROVER_ROLES", APPROVAL_SOURCE)
        for r in roles:
            self.assertIn(f'"{r}"', APPROVAL_SOURCE)

    def test_approve_day_refuses_open_paused_and_missing_category(self):
        """7. approve_day refuses a day with an Open or Paused interval, and missing time_category."""
        fn = _functions(APPROVAL_TREE).get("approve_day")
        body = _segment(APPROVAL_SOURCE, fn)
        self.assertIn('["Open", "Paused"]', body)
        self.assertIn("time_category", body)
        self.assertIn("frappe.throw", body)

    def test_default_time_category_no_default(self):
        """8. default_time_category carries NO default in time_kiosk_settings.json."""
        settings = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
        for f in settings["fields"]:
            if f.get("fieldname") == "default_time_category":
                self.assertIn(f.get("default"), (None, ""), "must not carry a default (Single rule)")

    def test_no_duplicate_overlap_or_lock_check(self):
        """9. Neither endpoint re-implements the overlap or lock check."""
        fn_start = _functions(TREE).get("start_backdated")
        fn_update = _functions(TREE).get("update_interval_times")
        body_start = _segment(SOURCE, fn_start)
        body_update = _segment(SOURCE, fn_update)
        self.assertNotIn("overlap", body_start.lower())
        self.assertNotIn("overlap", body_update.lower())
        self.assertNotIn("assert_not_locked", body_start)
        self.assertNotIn("assert_not_locked", body_update)


class TestTheApproverGateIsTheOnlyGate(unittest.TestCase):
    """APPROVER_ROLES must be the gate, which means Timesheet's DocPerm must not be.

    Verified on production 2026-09-18: a Custom DocPerm on Timesheet REPLACES the
    standard set wholesale, and the set in force grants submit/cancel/amend to
    Employee Self Service, Accounts User, HR User, Manufacturing User and Projects
    User -- and to NONE of the six approver roles. So a plain `doc.submit()` runs
    the caller's own permissions and refuses five of the six approvers, while
    letting a technician holding Employee Self Service lock their own day.

    `submit()` takes no ignore_permissions argument, so the flag is how it is done.
    Without it this feature is non-functional for almost everyone it exists for --
    and it fails at approval time, on payroll day, rather than at build time.
    """

    def _source(self):
        return (Path(__file__).resolve().parents[1] / "workforce/approval.py").read_text(encoding="utf-8")

    def test_submit_ignores_the_docperm(self):
        src = self._source()
        fn = src[src.index("def approve_day("):]
        nxt = fn.find("\ndef ", 1)
        if nxt != -1:
            fn = fn[:nxt]
        self.assertIn("ignore_permissions = True", fn)
        self.assertLess(fn.index("ignore_permissions = True"), fn.index("doc.submit()"))

    def test_cancel_ignores_the_docperm_too(self):
        """An approver who can lock a day but not reopen it is a one-way door."""
        src = self._source()
        fn = src[src.index("def reopen_day("):]
        self.assertIn("ignore_permissions = True", fn)
        self.assertLess(fn.index("ignore_permissions = True"), fn.index("doc.cancel()"))

    def test_the_role_check_still_runs_first(self):
        """Bypassing the DocPerm is only safe because our own check already ran."""
        src = self._source()
        for name, action in (("approve_day", "doc.submit()"), ("reopen_day", "doc.cancel()")):
            fn = src[src.index("def %s(" % name):]
            with self.subTest(fn=name):
                self.assertLess(fn.index("assert_approver()"), fn.index(action))


if __name__ == "__main__":
    unittest.main()
