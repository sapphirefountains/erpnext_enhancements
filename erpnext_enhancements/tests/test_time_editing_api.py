"""Tests for the time-editing API (TS2)."""

import ast
import re
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

def _code_only(source, node):
    """``_segment`` with the docstring and every ``#`` comment removed.

    An absence assertion cannot be written against raw source: the comment that
    explains why something is absent has to name the thing, so `assertNotIn` fires
    on the documentation rather than on a regression. That is not hypothetical —
    it broke three of the tests below the moment the invariants were written down
    next to the code that relies on them.
    """
    lines = source.splitlines()[node.lineno - 1 : node.end_lineno]

    # Blank the docstring by LINE RANGE, not by text. `ast.get_docstring` returns
    # the cleandoc'd string — dedented, leading/trailing whitespace gone — so a
    # `.replace()` of it against the raw indented source matches nothing at all
    # and silently leaves the docstring in. That failure looks exactly like a
    # regression in the code being tested.
    first = node.body[0] if node.body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        for i in range(first.lineno - node.lineno, first.end_lineno - node.lineno + 1):
            lines[i] = ""

    return "\n".join(line.split("#", 1)[0] for line in lines)

class TestTheStripperActuallyStrips(unittest.TestCase):
    """A helper that silently fails to strip turns every absence assertion below
    into a test of the documentation. The first version of `_code_only` did exactly
    that — `ast.get_docstring` returns cleandoc'd text that never matches the raw
    indented source — and the absence tests went on passing for the wrong reason
    right up until a docstring happened to name the token."""

    SAMPLE = (
        "def f():\n"
        '    """A docstring naming overlap and assert_not_locked."""\n'
        "    x = 1  # a comment naming overlap\n"
        "    return x\n"
    )

    def setUp(self):
        tree = ast.parse(self.SAMPLE)
        self.out = _code_only(self.SAMPLE, tree.body[0])

    def test_the_docstring_is_gone(self):
        self.assertNotIn("A docstring naming", self.out)

    def test_the_comment_is_gone(self):
        self.assertNotIn("a comment naming", self.out)

    def test_the_code_survives(self):
        self.assertIn("x = 1", self.out)
        self.assertIn("return x", self.out)


class TestTimeEditingAPI(unittest.TestCase):
    def test_start_backdated_signature(self):
        """1. start_backdated takes NO employee parameter."""
        fns = _functions(TREE)
        fn = fns.get("start_backdated")
        self.assertIsNotNone(fn, "start_backdated missing")
        self.assertNotIn("employee", _args(fn))

    def test_start_backdated_unanchored_and_manual(self):
        """2. A manual entry carries no coordinates and is flagged manual_start.

        The build moved into `add_manual_interval`, which `start_backdated` now
        delegates to — so the invariant is asserted where the doc is actually
        constructed. It is the same rule the Triton clock-in tool obeys from the
        other side: a coordinate nobody's phone produced is not evidence of
        anything, so an unanchored entry records none at all.
        """
        fn = _functions(TREE).get("add_manual_interval")
        self.assertIsNotNone(fn, "add_manual_interval missing")
        body = _segment(SOURCE, fn)
        self.assertIn("lat=None", body)
        self.assertIn("lng=None", body)
        self.assertIn("accuracy=None", body)
        self.assertIn("manual_start", body)

    def test_start_backdated_still_exists_and_delegates(self):
        """The kiosk dials `start_backdated` by name, and `tests/test_kiosk_frontend.py`
        checks every dialled method is whitelisted. Folding it into the general
        endpoint must not remove it."""
        fn = _functions(TREE).get("start_backdated")
        self.assertIsNotNone(fn)
        body = _code_only(SOURCE, fn)
        self.assertIn("add_manual_interval(", body)
        self.assertIn("end_time=None", body)

    def test_start_backdated_takes_no_employee_but_the_general_one_does(self):
        """Opening a job on someone else's behalf is what the geofence exists to
        prove, so the live path stays self-only. A *finished* block is a different
        act — there was never a device there to anchor — so a supervisor may enter
        one, gated by `_assert_may_edit_time_for`."""
        self.assertNotIn("employee", _args(_functions(TREE)["start_backdated"]))
        self.assertIn("employee", _args(_functions(TREE)["add_manual_interval"]))

    def test_an_open_manual_entry_is_today_only_and_a_block_is_not(self):
        """The distinction the whole endpoint turns on. An interval with no end is
        still running, so it overlaps everything after its start — backdating an
        open start onto a day that already has later work is refused, and must be.
        A block with both ends is the thing that can be added to any open day."""
        body = _code_only(SOURCE, _functions(TREE)["add_manual_interval"])
        self.assertIn("end_dt is None", body)
        self.assertIn("start_dt.date() != now_dt.date()", body)
        # ... and the future is refused on both ends, whichever shape it is.
        self.assertIn("Start time cannot be in the future", body)
        self.assertIn("End time cannot be in the future", body)
        self.assertIn("End time must be after the start time", body)

    def test_a_completed_block_resolves_the_photo_gate(self):
        """A Completed row with the gate left at Required reads as a technician who
        walked away from the prompt. `resolve` never throws and never returns
        Required — it is the verdict for a closer with nobody to ask."""
        body = _code_only(SOURCE, _functions(TREE)["add_manual_interval"])
        self.assertIn("photo_gate.stamp(", body)
        self.assertIn("photo_gate.resolve(", body)

    def test_a_manual_block_is_not_scored_for_tracking_health(self):
        """It has no GPS trail to score. Stamping one would file it next to the
        intervals whose tracking genuinely failed."""
        body = _code_only(SOURCE, _functions(TREE)["add_manual_interval"])
        self.assertNotIn("_stamp_tracking_health", body)

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

    def test_the_allowlist_is_the_three_fields_and_nothing_else(self):
        """36 fields on Job Interval are `read_only: 1`, and read_only is a Desk
        hint rather than a server gate — a generic setter here would let a
        technician clear their own `offsite_start`, `auto_closed` or
        `tracking_health`. `time_category` joined in v1.488.0 because the
        technician chose it in the first place and is asked for it on every
        clock-in, so correcting it claims nothing they could not have claimed then.
        """
        allowed = re.search(r"ALLOWED_UPDATE_FIELDS = \{([^}]*)\}", SOURCE).group(1)
        names = set(re.findall(r'"([a-z_]+)"', allowed))
        self.assertEqual(names, {"start_time", "end_time", "time_category"})

    def test_update_interval_times_writes_only_allowlisted_fields(self):
        """The list is worth nothing if the body sets something it does not name."""
        body = _code_only(SOURCE, _functions(TREE)["update_interval_times"])
        assigned = set(re.findall(r"doc\.([a-z_]+)\s*=", body))
        audit = {"corrected", "time_edit_reason", "manual_start", "manual_start_reason"}
        allowed = {"start_time", "end_time", "time_category"}
        self.assertEqual(assigned - audit, allowed, f"writes outside the allowlist: {assigned - audit - allowed}")

    def test_a_blank_activity_is_ignored_rather_than_written(self):
        """Saving the edit sheet without touching the chips must not empty the
        category — and `approve_day` refuses a day with a missing one, so a blank
        written here would surface on payroll day rather than at the keystroke."""
        body = _code_only(SOURCE, _functions(TREE)["update_interval_times"])
        self.assertIn("if new_category:", body)
        self.assertLess(body.index("new_category = "), body.index("if new_category:"))

    def test_the_refusal_is_actionable_when_there_is_nothing_to_pick(self):
        """"Please pick one" is only useful advice when something exists to pick."""
        body = _code_only(SOURCE, _functions(TREE)["add_manual_interval"])
        self.assertIn('frappe.db.count("Activity Type")', body)
        self.assertIn("No Activity Types are set up yet", body)

    def test_update_interval_times_checks_roles(self):
        """4. Both writing endpoints go through one permission gate."""
        for fname in ("update_interval_times", "add_manual_interval"):
            with self.subTest(func=fname):
                body = _code_only(SOURCE, _functions(TREE)[fname])
                self.assertIn("_assert_may_edit_time_for", body)

    def test_editing_someone_elses_time_is_not_gated_on_the_timeline_roles(self):
        """`TIMELINE_MANAGER_ROLES` is pinned to the Location Timeline page's roles
        by `test_location_timeline_page.py`. Reusing it as the time-editing gate
        would mean that giving a supervisor the right to fix a timesheet also hands
        them everyone's GPS trail — two different powers behind one switch. It is
        also the narrower set: it predates Operations Manager and Production
        Manager, the roles the supervisors actually hold.
        """
        gate = _functions(TREE).get("_assert_may_edit_time_for")
        self.assertIsNotNone(gate, "_assert_may_edit_time_for missing")
        body = _code_only(SOURCE, gate)
        self.assertIn("APPROVER_ROLES", body)
        self.assertNotIn("TIMELINE_MANAGER_ROLES", body)
        self.assertIn("PermissionError", body)

    def test_an_edit_keeps_what_the_kiosk_recorded(self):
        """`original_start_time` / `original_end_time` are the only way to tell a
        hand-edited day from what actually happened. The reviewer-approved path
        already records them; a self-service edit that skipped it would leave the
        two paths disagreeing about the same field."""
        body = _code_only(SOURCE, _functions(TREE)["update_interval_times"])
        self.assertIn("_record_originals", body)
        self.assertIn("corrected = 1", body)
        self.assertIn("time_edit_reason", body)

    def test_manual_start_is_raised_only_when_the_start_actually_moved(self):
        """The badge means "this start was typed in". Fixing a forgotten clock-out
        does not make the start any less real, and flagging it would train everyone
        to ignore the badge."""
        body = _code_only(SOURCE, _functions(TREE)["update_interval_times"])
        self.assertIn("start_moved", body)
        idx = body.index("start_moved")
        self.assertIn("manual_start = 1", body[idx:])

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
        """9. No endpoint re-implements the overlap or lock check.

        `JobInterval.validate` runs both on every save, so every path that writes an
        interval — kiosk, correction, sweeper, desk — gets them. A second copy here
        is not defence in depth; it is a second answer that can drift from the first,
        and the one users hit would be whichever ran earlier.

        Read through `_code_only`: the comments and docstrings explaining exactly
        this invariant have to name `overlap`, and an assertion against raw source
        fires on the explanation.
        """
        for fname in ("start_backdated", "update_interval_times", "add_manual_interval"):
            with self.subTest(func=fname):
                body = _code_only(SOURCE, _functions(TREE)[fname])
                self.assertNotIn("overlap", body.lower())
                self.assertNotIn("assert_not_locked", body)


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
