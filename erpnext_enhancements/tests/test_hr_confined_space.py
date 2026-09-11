# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Confined space, lockout/tagout and working alone — WI-073 D.

The highest legal exposure in the company, and the three requirements OSHA asks
for first that almost nobody has. Fountain vaults, wet wells and pump pits are the
textbook permit-required case: an atmosphere hazard from decomposing organic
matter and chlorine dosing, engulfment from water that can rise, and egress
through a hatch.

The assertions cluster around four decisions:

* **permit-required is the default**, because a space wrongly classified as
  needing a permit costs twenty minutes and one wrongly classified as not needing
  one is how people die in pits;
* **the atmosphere refuses** — the one place in this app where a check blocks
  rather than warns, because a permit that opens over a bad gas reading has
  stopped meaning anything;
* **the attendant is not the entrant**, since most confined-space fatalities are
  would-be rescuers;
* **the LOTO inspector is not the person being observed**, which is the half of
  the annual inspection everybody gets wrong.

Run: python -m unittest erpnext_enhancements.tests.test_hr_confined_space
"""

import ast
import io
import json
import re
import tokenize
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
MODULE = APP / "hr_enhancements"
DT = MODULE / "doctype"

SPACE_JSON = DT / "confined_space/confined_space.json"
SPACE_PY = DT / "confined_space/confined_space.py"
PERMIT_JSON = DT / "confined_space_entry_permit/confined_space_entry_permit.json"
PERMIT_PY = DT / "confined_space_entry_permit/confined_space_entry_permit.py"
PERMIT_JS = DT / "confined_space_entry_permit/confined_space_entry_permit.js"
PERMIT_LIST_JS = DT / "confined_space_entry_permit/confined_space_entry_permit_list.js"
LOTO_JSON = DT / "lockout_tagout_procedure/lockout_tagout_procedure.json"
LOTO_PY = DT / "lockout_tagout_procedure/lockout_tagout_procedure.py"
ENERGY_JSON = DT / "lockout_energy_source/lockout_energy_source.json"
INSPECTION_PY = DT / "lockout_periodic_inspection/lockout_periodic_inspection.py"
LONE_JSON = DT / "lone_work_session/lone_work_session.json"
LONE_PY = DT / "lone_work_session/lone_work_session.py"
LONE_JS = DT / "lone_work_session/lone_work_session.js"
PERMITS = MODULE / "permits.py"
LONEWORK = MODULE / "lonework.py"
HOOKS = APP / "hooks.py"
WIZARD = APP / "sapphire_maintenance/page/visit_wizard/visit_wizard.js"


def _text(path):
    return path.read_text(encoding="utf-8")


def _js(path):
    return re.sub(r"//.*$", "", _text(path), flags=re.M)


def _src(path):
    kept = [
        t
        for t in tokenize.generate_tokens(io.StringIO(_text(path)).readline)
        if t.type != tokenize.COMMENT
    ]
    tree = ast.parse(tokenize.untokenize(kept))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body[0].value.value = ""
    return ast.unparse(tree)


def _fn(name, path):
    src = _text(path)
    lines = src.splitlines()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            stmts = node.body
            if (
                stmts
                and isinstance(stmts[0], ast.Expr)
                and isinstance(stmts[0].value, ast.Constant)
                and isinstance(stmts[0].value.value, str)
            ):
                stmts = stmts[1:]
            return "\n".join(lines[stmts[0].lineno - 1 : node.end_lineno]) if stmts else ""
    raise AssertionError(f"{name} not found in {path.name}")


def _fields(path):
    return {f["fieldname"]: f for f in json.loads(_text(path))["fields"]}


class TestPermitRequiredIsTheDefault(unittest.TestCase):
    """A space wrongly classified as needing a permit costs twenty minutes. One
    wrongly classified as not needing one is how people die in pits.
    """

    def test_the_default_is_the_safe_one(self):
        self.assertEqual(_fields(SPACE_JSON)["classification"]["default"], "Permit-required")

    def test_downgrading_needs_a_name_and_a_date(self):
        """Not an approval step — just "who said this pit was safe", because that is
        the first question after an incident."""
        body = _fn("_require_a_reason_to_downgrade", SPACE_PY)
        self.assertIn("reviewed_by", body)
        self.assertIn("last_reviewed_on", body)
        self.assertIn("frappe.throw", body)

    def test_a_ticked_hazard_blocks_a_downgrade(self):
        """A space with a hazard is permit-required by definition, so the two
        cannot disagree on one record."""
        body = _fn("_require_a_reason_to_downgrade", SPACE_PY)
        self.assertIn("hazard_atmosphere", body)
        self.assertIn("hazard_engulfment", body)

    def test_the_atmosphere_hazard_defaults_on(self):
        """The usual one in a fountain pit, and the one you cannot see."""
        self.assertEqual(_fields(SPACE_JSON)["hazard_atmosphere"]["default"], "1")

    def test_a_permit_required_space_needs_a_real_rescue_plan(self):
        """"Call 911" is not a rescue plan — most confined-space deaths are the
        people who went in after somebody."""
        body = _fn("_require_a_rescue_plan", SPACE_PY)
        self.assertIn("frappe.throw", body)
        self.assertIn("PERMIT_REQUIRED", body)


class TestTheAtmosphereRefuses(unittest.TestCase):
    """The one place in this app where a check blocks rather than warns."""

    def test_the_limits_are_the_limits(self):
        src = _text(PERMIT_PY)
        self.assertIn("OXYGEN_MIN = 19.5", src)
        self.assertIn("OXYGEN_MAX = 23.5", src)
        self.assertIn("LEL_MAX = 10.0", src)
        self.assertIn("H2S_MAX = 10.0", src)
        self.assertIn("CO_MAX = 25.0", src)

    def test_oxygen_has_a_ceiling_not_only_a_floor(self):
        """An enriched atmosphere is a fire risk, and people forget that half."""
        body = _fn("atmosphere_problems", PERMIT_PY)
        self.assertIn("OXYGEN_MAX", body)
        self.assertIn("fire risk", body)

    def test_opening_throws_on_a_bad_reading(self):
        body = _fn("open_permit", PERMITS)
        at = body.index("atmosphere_problems()")
        self.assertIn("frappe.throw", body[at : at + 600])

    def test_the_atmosphere_cannot_be_overridden(self):
        """`acknowledge_controls` waives the human ticks and must never reach the
        gas: there is no argument to have with a meter."""
        body = _fn("open_permit", PERMITS)
        gas_at = body.index("atmosphere_problems()")
        ack_at = body.index("acknowledge_controls")
        # The refusal must come BEFORE the override is consulted.
        self.assertLess(gas_at, ack_at)
        gas_block = body[gas_at:ack_at]
        self.assertNotIn("acknowledge", gas_block)

    def test_the_verdict_is_derived_not_ticked(self):
        """A box labelled "atmosphere OK" is one somebody ticks while holding a
        meter they have not looked at."""
        self.assertEqual(_fields(PERMIT_JSON)["atmosphere_ok"].get("read_only"), 1)
        self.assertIn("self.atmosphere_ok", _fn("_derive_atmosphere", PERMIT_PY))

    def test_the_form_shows_the_verdict_as_you_type(self):
        """Stopping an entry earlier is strictly better than stopping it later."""
        body = _js(PERMIT_JS)
        for field in ("oxygen_pct", "lel_pct", "h2s_ppm", "co_ppm"):
            with self.subTest(field=field):
                self.assertIn(f"{field}: (frm) => frm.trigger", body)

    def test_the_form_does_not_offer_an_atmosphere_override(self):
        """A button that looks like it might override a gas reading is a button
        somebody will press."""
        body = _js(PERMIT_JS)
        at = body.index("ee_offer_override")
        block = body[at : at + 700]
        self.assertIn("atmosphere_ok", block)
        self.assertIn("if (problems.length) return", block)


class TestNobodyGoesInAlone(unittest.TestCase):
    def test_the_attendant_is_mandatory(self):
        self.assertEqual(_fields(PERMIT_JSON)["attendant"].get("reqd"), 1)

    def test_the_attendant_cannot_be_the_entrant(self):
        """The most likely data-entry shortcut on a phone at a hatch: one person in
        a hurry filling their own name into every Link."""
        body = _fn("_reject_self_attending", PERMIT_PY)
        self.assertIn("frappe.throw", body)

    def test_the_field_says_they_do_not_enter(self):
        """Most confined-space fatalities are would-be rescuers."""
        self.assertIn("never enters", _fields(PERMIT_JSON)["attendant"]["description"])

    def test_a_permit_expires_with_the_shift(self):
        """A permit is for THIS entry, not for the day — nobody works under
        yesterday's."""
        self.assertIn("PERMIT_HOURS = 8", _text(PERMIT_PY))
        self.assertIn("expiry_from", _fn("open_permit", PERMITS))

    def test_closing_out_asks_exactly_one_thing(self):
        """An attendant who cannot close it in ten seconds will close it in the
        truck on the way home."""
        body = _fn("close_permit", PERMITS)
        self.assertIn("everyone_out", body)

    def test_the_list_view_answers_is_anybody_in_a_hole(self):
        body = _js(PERMIT_LIST_JS)
        self.assertIn("permits.open_permits", body)
        self.assertIn("expired", body)


class TestTheLockoutProcedureVerifies(unittest.TestCase):
    def test_every_source_needs_a_verification_method(self):
        """The column everybody leaves out, and the only one whose absence can kill
        somebody: locking a breaker is not the same as confirming the pump will not
        start."""
        body = _fn("_require_verification_steps", LOTO_PY)
        self.assertIn("verification_method", body)
        self.assertIn("frappe.throw", body)

    def test_the_energy_source_row_carries_all_four_columns(self):
        fields = _fields(ENERGY_JSON)
        for name in ("energy_type", "source_location", "isolation_method", "verification_method"):
            with self.subTest(field=name):
                self.assertIn(name, fields)

    def test_the_inspector_cannot_be_the_person_observed(self):
        """Somebody checking their own habits finds nothing, which is why the rule
        names a second person — and it is the half almost always missed."""
        body = _fn("_reject_self_inspection", INSPECTION_PY)
        self.assertIn("frappe.throw", body)
        self.assertIn("authorised_employee", body)

    def test_the_inspection_dates_are_derived(self):
        """A self-reported date is exactly how the annual inspection stays the
        requirement nobody has."""
        fields = _fields(LOTO_JSON)
        self.assertEqual(fields["last_inspected_on"].get("read_only"), 1)
        self.assertEqual(fields["inspection_due_on"].get("read_only"), 1)
        self.assertIn("INSPECTION_MONTHS = 12", _text(LOTO_PY))

    def test_filing_an_inspection_moves_the_parent_date(self):
        self.assertIn("refresh_inspection_dates", _fn("on_update", INSPECTION_PY))

    def test_an_unwritten_procedure_is_due_now_not_never(self):
        """A blank due date reads as "nothing to do", which is the wrong way for
        this to fail."""
        self.assertIn("last or self.written_on", _fn("refresh_inspection_dates", LOTO_PY))


class TestTheLoneWorkerClockEscalates(unittest.TestCase):
    def test_three_stages_ending_somewhere_a_human_reads(self):
        """A chain that ends in a mailbox nobody reads is not an escalation."""
        src = _text(LONEWORK)
        self.assertIn('(0, "worker")', src)
        self.assertIn('(15, "supervisor")', src)
        self.assertIn('(30, "executive")', src)

    def test_it_escalates_once_per_stage(self):
        """A sweep that re-sends every ten minutes trains people to filter it, and
        the filtered version is worth nothing."""
        body = _fn("_sweep", LONEWORK)
        self.assertIn("due_stage <= cint(row.escalation_stage)", body)
        self.assertIn("continue", body)

    def test_it_never_closes_a_session_by_itself(self):
        """"The sweep decided they were probably fine" is the judgement nobody
        should be making at 7pm."""
        body = _src(LONEWORK)
        at = body.index("def _sweep")
        block = body[at : body.index("def _escalate")]
        self.assertNotIn("CLOSED", block)

    def test_a_missing_supervisor_escalates_up_not_nowhere(self):
        """Silence is the one outcome this must never produce."""
        body = _fn("_recipients_for", LONEWORK)
        at = body.index('audience == "supervisor"')
        self.assertIn("_executives()", body[at : at + 400])

    def test_extending_resets_the_stage(self):
        """Somebody chased, who answered and extended, must not be escalated to
        their supervisor five minutes later on the old counter."""
        body = _fn("extend", LONEWORK)
        self.assertIn("doc.escalation_stage = 0", body)

    def test_checking_out_tells_whoever_was_alarmed(self):
        """An escalation with no resolution is how the next one gets ignored."""
        self.assertIn("_notify_resolved", _fn("check_out", LONEWORK))

    def test_the_sweep_cannot_take_the_scheduler_down(self):
        body = _fn("sweep_overdue_sessions", LONEWORK)
        self.assertIn("except Exception", body)
        self.assertIn("log_error", body)
        for flag in ("in_migrate", "in_install", "in_patch"):
            with self.subTest(flag=flag):
                self.assertIn(flag, body)

    def test_it_is_scheduled_often_enough_to_matter(self):
        """Parsed, not string-matched. The job shares the ten-minute key with the
        four chat sweeps -- a second `"*/10 * * * *"` entry would silently REPLACE
        them, which `test_hooks_integrity` caught on the first run -- so the
        assertion has to look inside the list rather than at a spelling."""
        tree = ast.parse(_text(HOOKS))
        events = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "scheduler_events":
                events = ast.literal_eval(node.value)
        jobs = (events.get("cron") or {}).get("*/10 * * * *") or []
        self.assertIn(
            "erpnext_enhancements.hr_enhancements.lonework.sweep_overdue_sessions", jobs
        )
        # And the neighbours it shares the key with are still there.
        self.assertGreaterEqual(len(jobs), 5)

    def test_one_open_session_per_person(self):
        """Two open sessions means one of them is stale, and a stale session is a
        false alarm that teaches people to ignore the real one."""
        self.assertIn("You already have an open session", _fn("start_session", LONEWORK))


class TestEverythingIsReachable(unittest.TestCase):
    """Three correct server sides have shipped here with no caller. Not four."""

    def test_every_permit_endpoint_has_a_caller(self):
        self._assert_reachable(PERMITS, "permits", _js(PERMIT_JS) + _js(PERMIT_LIST_JS))

    def test_every_lonework_endpoint_has_a_caller(self):
        self._assert_reachable(LONEWORK, "lonework", _js(LONE_JS) + _js(WIZARD))

    def _assert_reachable(self, module, label, callers):
        tree = ast.parse(_text(module))
        whitelisted = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if getattr(target, "attr", None) == "whitelist":
                    whitelisted.add(node.name)
        self.assertTrue(whitelisted, f"{label}: expected whitelisted endpoints")
        for name in sorted(whitelisted):
            with self.subTest(endpoint=f"{label}.{name}"):
                self.assertIn(f"{label}.{name}", callers)

    def test_the_lone_work_check_in_is_on_the_visit_screen(self):
        """A check-in that lives anywhere else is a check-in that does not
        happen."""
        self.assertIn("render_lone_work", _js(WIZARD))

    def test_a_technician_can_open_their_own_permit(self):
        """Gating this behind a manager means the permit gets written afterwards,
        which is not a permit."""
        perms = {p["role"]: p for p in json.loads(_text(PERMIT_JSON))["permissions"]}
        self.assertEqual(perms["Employee"].get("create"), 1)
        self.assertEqual(perms["Employee"].get("write"), 1)

    def test_anybody_can_declare_their_own_lone_session(self):
        perms = {p["role"]: p for p in json.loads(_text(LONE_JSON))["permissions"]}
        self.assertEqual(perms["Employee"].get("create"), 1)


if __name__ == "__main__":
    unittest.main()
