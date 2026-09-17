"""Bench-free contracts for Triton-initiated clocking (v1.481.0).

`api/time_kiosk.py` cannot be imported without a bench — it pulls in frappe plus
four workforce modules at import time — so, like `test_location_timeline_page`,
this suite reads it through the AST and as text. That is enough to pin the
invariants that actually carry the risk here, all of which are structural:

* **Triton may never clock anyone else IN.** A supervisor's phone is not the crew
  member's, and opening a job is exactly what the geofence exists to prove. So no
  endpoint may both accept an ``employee`` argument and open an interval.
* **The model never carries the coordinates.** The browser stashes a fix against
  the caller's own session; the clock-in path reads it back server-side. Nothing
  hands a location *out* to a client, so the stash reader must not be whitelisted
  — a whitelisted reader would turn "where is this person" into an API call.
* **A missing fix is not a fix at 0,0.** The Null Island bug: ``None``
  coordinates used to be flattened to 0.0, measured ~11,160,000 m from site, and
  recorded as a confident ``offsite_end = 1``. An unknown location must stay
  unknown — note the failure direction, since a false "off-site" verdict is an
  accusation about an employee.
* **The photo gate keeps exactly two throwing call sites.** The on-behalf path
  uses the non-throwing ``resolve`` instead, because there is nobody present to
  prompt.

Run: python -m unittest erpnext_enhancements.tests.test_workforce_assistant_clock
"""

import ast
import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
API = APP / "api/time_kiosk.py"
JOB_INTERVAL = APP / "workforce/doctype/job_interval/job_interval.json"

SOURCE = API.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _functions():
    return {n.name: n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef)}


def _is_whitelisted(node):
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute) and target.attr == "whitelist":
            return True
    return False


def _args(node):
    a = node.args
    return [x.arg for x in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)]


def _segment(node):
    return ast.get_source_segment(SOURCE, node) or ""


def _job_interval():
    return json.loads(JOB_INTERVAL.read_text(encoding="utf-8"))


class TestOnBehalfIsClockOutOnly(unittest.TestCase):
    def test_no_endpoint_opens_an_interval_for_another_employee(self):
        """The single most important rule in this feature.

        Anything that both takes an ``employee`` and starts a session is an
        on-behalf clock-in, which was explicitly ruled out.
        """
        opens = ("action=\"Start\"", "action='Start'", "_new_interval(")
        for name, node in _functions().items():
            if not _is_whitelisted(node) or "employee" not in _args(node):
                continue
            body = _segment(node)
            for token in opens:
                with self.subTest(func=name, token=token):
                    self.assertNotIn(
                        token,
                        body,
                        f"{name}() accepts an employee AND opens an interval — "
                        "that is an on-behalf clock-in",
                    )

    def test_the_clock_in_endpoint_cannot_name_another_employee(self):
        fn = _functions().get("clock_in_with_stashed_fix")
        self.assertIsNotNone(fn, "clock_in_with_stashed_fix is missing")
        self.assertNotIn("employee", _args(fn))

    def test_on_behalf_close_checks_roles_before_it_mutates(self):
        fn = _functions().get("close_interval_for_employee")
        self.assertIsNotNone(fn, "close_interval_for_employee is missing")
        body = _segment(fn)
        self.assertIn("TIMELINE_MANAGER_ROLES", body)
        self.assertIn("PermissionError", body)
        # The refusal must come before anything is written, or a rejected call
        # would still have closed somebody's day.
        self.assertLess(
            body.index("PermissionError"),
            body.index("_close_interval("),
            "the role check must precede the mutation",
        )

    def test_the_close_takes_a_row_lock(self):
        body = _segment(_functions()["close_interval_for_employee"])
        self.assertIn("for_update=True", body)


class TestTheModelNeverCarriesTheCoordinates(unittest.TestCase):
    def test_the_stash_reader_is_not_whitelisted(self):
        """Stashing is a write the browser performs for itself; reading is not
        something any client may ask for. A whitelisted reader would publish a
        colleague's live position to anyone who could guess the method name."""
        fns = _functions()
        self.assertTrue(_is_whitelisted(fns["stash_location_fix"]))
        for name in ("get_stashed_location_fix", "consume_stashed_location_fix"):
            with self.subTest(func=name):
                self.assertFalse(
                    _is_whitelisted(fns[name]),
                    f"{name} must not be callable from a client",
                )

    def test_nothing_lets_a_caller_stash_for_another_user(self):
        body = _segment(_functions()["stash_location_fix"])
        self.assertNotIn("employee", body)
        self.assertNotIn("user=", body)

    def test_the_stash_is_short_lived(self):
        ttl = re.search(r"LOCATION_STASH_TTL_SEC\s*=\s*(\d+)", SOURCE)
        self.assertIsNotNone(ttl, "LOCATION_STASH_TTL_SEC is missing")
        self.assertLessEqual(
            int(ttl.group(1)), 300, "a fix is evidence of presence now, not a standing permission"
        )
        self.assertIn("expires_in_sec=LOCATION_STASH_TTL_SEC", SOURCE)

    def test_a_fix_is_consumed_rather_than_reused(self):
        body = _segment(_functions()["clock_in_with_stashed_fix"])
        self.assertIn("consume_stashed_location_fix()", body)

    def test_clock_in_refuses_without_a_fix(self):
        body = _segment(_functions()["clock_in_with_stashed_fix"])
        self.assertIn("NO_LOCATION_FIX_MSG", body)
        self.assertIn("frappe.throw", body)


class TestUnknownLocationStaysUnknown(unittest.TestCase):
    """The Null Island regression guard. Note the failure direction it protects:
    the old behaviour did not crash, it produced a confident false accusation."""

    def test_distance_is_none_when_coordinates_are_missing(self):
        body = _segment(_functions()["_distance_to_site"])
        self.assertIn("_valid_coords", body)
        self.assertIn("return None", body)

    def test_offsite_is_unknown_rather_than_true_for_an_unknown_distance(self):
        body = _segment(_functions()["_is_offsite"])
        self.assertRegex(
            body,
            r"if\s+distance\s+is\s+None:\s*\n\s*return\s+None",
            "an unknown distance must not resolve to an off-site verdict",
        )

    def test_an_unanchored_close_passes_no_coordinates(self):
        body = _segment(_functions()["close_interval_for_employee"])
        self.assertIn("_close_interval(doc, now_dt, None, None, None", body)
        self.assertIn("unanchored_close", body)


class TestThePhotoGateIsUnchanged(unittest.TestCase):
    def test_exactly_two_throwing_call_sites(self):
        """`log_time`'s Switch and Stop. The on-behalf path uses the non-throwing
        `resolve`, as the sweeper and corrections do, because nobody is there to
        be prompted for a photo."""
        self.assertEqual(SOURCE.count("photo_gate.check("), 2)

    def test_the_on_behalf_path_resolves_instead_of_checking(self):
        body = _segment(_functions()["close_interval_for_employee"])
        self.assertIn("photo_gate.resolve(", body)
        self.assertIn("skip_reason", body)


class TestTheProvenanceFields(unittest.TestCase):
    NEW_FIELDS = ("opened_via", "closed_via", "closed_requested_by", "unanchored_close")

    def test_the_fields_exist(self):
        names = {f.get("fieldname") for f in _job_interval()["fields"]}
        for field in self.NEW_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, names)

    def test_no_new_field_carries_a_default(self):
        """Job Interval is a NORMAL doctype, where the opposite of the Single rule
        applies: adding a column with a default is one ALTER, and MariaDB writes
        that default into every existing row as part of it. A default here would
        relabel the entire history as assistant-opened or assistant-closed."""
        for f in _job_interval()["fields"]:
            if f.get("fieldname") in self.NEW_FIELDS and f.get("fieldtype") != "Check":
                with self.subTest(field=f.get("fieldname")):
                    self.assertIn(f.get("default"), (None, ""), "must not carry a default")

    def test_projects_manager_can_write_what_triton_lets_them_do(self):
        """On-behalf clock-out was granted to the timeline-manager roles, and
        Projects Manager sat in that set with a read-only DocPerm — the endpoint
        would have granted a write the Desk refused. The DocPerm was widened to
        match rather than leaving the two disagreeing."""
        perms = _job_interval()["permissions"]
        pm = [p for p in perms if p.get("role") == "Projects Manager" and not p.get("permlevel")]
        self.assertTrue(pm, "Projects Manager has no permlevel-0 DocPerm on Job Interval")
        self.assertTrue(any(p.get("write") for p in pm), "Projects Manager still cannot write")

    def test_no_permlevel_1_row_was_widened(self):
        """permlevel 1 guards the pay/cost block. Widening it publishes wages."""
        allowed = {"System Manager", "HR Manager", "Accounts Manager"}
        for p in _job_interval()["permissions"]:
            if p.get("permlevel") == 1:
                with self.subTest(role=p.get("role")):
                    self.assertIn(p.get("role"), allowed)


if __name__ == "__main__":
    unittest.main()
