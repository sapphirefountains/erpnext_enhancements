# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The private half of an HR file — WI-073 E.

**The finding that prompted this was half wrong, and the correction is the useful
part.** The survey said any of the sixteen could open a colleague's Employee
record and read who is salaried. Checked against prod 2026-09-11:

* all 119 Employee fields sat at permlevel 0 — no field-level protection at all;
* `Custom DocPerm` grants the `Employee` role read **and write** at level 0;
* **but 19 `User Permission` rows scope each person to their own record.** They
  cannot read a colleague's. The claim is refuted.

What is true is narrower and still worth fixing: the whole protection rested on
those nineteen rows being correct and complete, and an employee could edit their
**own** cost-to-company because self-service write sat at level 0 with nothing
above it. And no field in that group holds data today, which is the right time to
put a level above it — before somebody types a bank account into an unprotected
one.

Run: python -m unittest erpnext_enhancements.tests.test_hr_personnel_file
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

CASE_JSON = DT / "hr_case_record/hr_case_record.json"
CASE_PY = DT / "hr_case_record/hr_case_record.py"
ACK_JSON = DT / "policy_acknowledgement/policy_acknowledgement.json"
ACK_PY = DT / "policy_acknowledgement/policy_acknowledgement.py"
ACK_JS = DT / "policy_acknowledgement/policy_acknowledgement.js"
ACK_LIST_JS = DT / "policy_acknowledgement/policy_acknowledgement_list.js"
POLICIES = MODULE / "policies.py"
PERMISSIONS = MODULE / "permissions.py"
PATCH = APP / "patches/protect_employee_compensation_fields.py"
SETTERS = APP / "fixtures/property_setter.json"
HOOKS = APP / "hooks.py"

#: The ten fields moved above the line, and the one deliberately left alone.
PROTECTED = (
    "ctc", "salary_currency", "salary_mode", "custom_healthcare_stipend",
    "bank_name", "bank_ac_no", "iban", "passport_number", "health_details", "blood_group",
)


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


class TestTheCompensationFieldsMoveAboveTheLine(unittest.TestCase):
    def test_every_one_of_them_has_a_permlevel_setter(self):
        setters = {
            s["name"]: s
            for s in json.loads(_text(SETTERS))
            if s.get("doc_type") == "Employee" and s.get("property") == "permlevel"
        }
        for field in PROTECTED:
            with self.subTest(field=field):
                setter = setters.get(f"Employee-{field}-permlevel")
                self.assertIsNotNone(setter, f"{field} has no permlevel setter")
                self.assertEqual(setter["value"], "1")

    def test_date_of_birth_is_deliberately_left_alone(self):
        """The one field in the group with real data (16 of 16), far below a bank
        account in sensitivity, and raising it would hide it from the person
        themselves."""
        setters = {s["name"] for s in json.loads(_text(SETTERS))}
        self.assertNotIn("Employee-date_of_birth-permlevel", setters)

    def test_hr_is_granted_the_level_it_now_needs(self):
        """Without this the Property Setters hide the fields from EVERYBODY — no
        role on this site holds any permission at level 1."""
        src = _text(PATCH)
        self.assertIn('ROLES = ("HR Manager", "System Manager")', src)
        self.assertIn("PERMLEVEL = 1", src)

    def test_hr_user_is_not_granted_it(self):
        """All sixteen staff hold `HR User` — it is one of the twenty-one universal
        roles — so granting it there would be granting it to everybody."""
        src = _text(PATCH)
        at = src.index("ROLES = ")
        self.assertNotIn("HR User", src[at : src.index("PERMLEVEL")])

    def test_the_grant_goes_through_the_framework_api(self):
        """`Custom DocPerm` is the one table where this repo's fixture habit is
        dangerous: `setup_custom_perms` copies the standard set wholesale, and the
        six rows already on prod are not in this repo's fixture file. Managing two
        new rows through a file that does not know about the other six is how the
        other six get lost."""
        body = _fn("grant_employee_field_permissions", PATCH)
        self.assertIn("from frappe.permissions import add_permission", body)
        self.assertIn("add_permission(DOCTYPE, role, PERMLEVEL)", body)

    def test_it_is_idempotent_and_never_raises(self):
        body = _fn("grant_employee_field_permissions", PATCH)
        self.assertIn('frappe.db.exists(', body)
        self.assertIn("except Exception", body)

    def test_there_is_an_after_migrate_twin(self):
        """The Property Setters are fixtures, and `sync_fixtures()` runs after the
        post-model-sync patches."""
        self.assertIn(
            "protect_employee_compensation_fields.grant_employee_field_permissions", _text(HOOKS)
        )


class TestTheCaseRecordIsTheNarrowestThingInTheApp(unittest.TestCase):
    def test_only_two_roles_can_read_it(self):
        roles = {p["role"] for p in json.loads(_text(CASE_JSON))["permissions"]}
        self.assertEqual(roles, {"HR Manager", "System Manager"})

    def test_hr_user_cannot(self):
        """Every one of the sixteen holds it."""
        roles = {p["role"] for p in json.loads(_text(CASE_JSON))["permissions"]}
        self.assertNotIn("HR User", roles)
        self.assertNotIn("Employee", roles)

    def test_it_cannot_be_exported_or_shared(self):
        for perm in json.loads(_text(CASE_JSON))["permissions"]:
            with self.subTest(role=perm["role"]):
                self.assertEqual(perm.get("export", 0), 0)
                self.assertEqual(perm.get("share", 0), 0)

    def test_nobody_files_their_own(self):
        """The two people holding HR Manager are also employees, so this is
        reachable rather than theoretical — and an entry somebody wrote about
        themselves is the one an outside reader discounts entirely."""
        body = _fn("_reject_self_filing", CASE_PY)
        self.assertIn("PermissionError", body)

    def test_the_author_is_stamped_once(self):
        """A dated record whose author can be changed afterwards is not a record."""
        self.assertIn("self.raised_by = frappe.session.user", _fn("before_insert", CASE_PY))
        self.assertEqual(_fields(CASE_JSON)["raised_by"].get("read_only"), 1)

    def test_a_conversation_is_a_first_class_kind(self):
        """A form offering only "written warning" and "final warning" is one people
        avoid until things are already bad — at which point there is no earlier
        record showing anybody tried."""
        options = _fields(CASE_JSON)["case_type"]["options"].splitlines()
        self.assertEqual(options[0], "Conversation")

    def test_their_response_is_recordable(self):
        """A file with only one side of it is worth less, not more."""
        self.assertIn("employee_comment", _fields(CASE_JSON))


class TestSigningIsSomethingTheyDid(unittest.TestCase):
    def test_only_the_person_can_sign(self):
        body = _fn("sign", POLICIES)
        self.assertIn("doc.user != me", body)
        self.assertIn("PermissionError", body)

    def test_the_typed_name_must_match(self):
        """The failure being caught is a manager helpfully signing on somebody's
        behalf — real, common, the one thing that makes the register worthless, and
        invisible afterwards."""
        body = _fn("sign", POLICIES)
        self.assertIn("_names_match", body)
        self.assertIn("frappe.throw", body)

    def test_the_match_is_not_fuzzy(self):
        """Every leniency that lets "J Griffin" through also lets a colleague's
        surname through."""
        body = _src(POLICIES)
        at = body.index("def _names_match")
        block = body[at : body.index("def _notify")]
        for fuzzy in ("startswith", "difflib", "SequenceMatcher", "in actual"):
            with self.subTest(token=fuzzy):
                self.assertNotIn(fuzzy, block)

    def test_the_name_is_not_prefilled_on_the_form(self):
        """A prefilled name is a name nobody typed, and typing it is the act being
        recorded."""
        body = _js(ACK_JS)
        at = body.index('fieldname: "typed_name"')
        block = body[at : at + 400]
        self.assertNotIn("default:", block)

    def test_declining_is_offered_and_recorded(self):
        """A register that only accepts yes is a register that gets a yes."""
        self.assertIn("def decline", _text(POLICIES))
        body = _fn("decline", POLICIES)
        self.assertIn("frappe.throw", body)  # a reason is required
        self.assertIn("I do not agree", _js(ACK_JS))

    def test_a_decline_reaches_hr(self):
        self.assertIn("_notify_declined", _fn("decline", POLICIES))

    def test_the_status_cannot_be_set_directly(self):
        """`Employee` holds write here because signing IS a write, so without the
        guard anybody could POST their own row to Signed without ever opening the
        document — precisely what the record exists to prove they did."""
        self.assertEqual(_fields(ACK_JSON)["status"].get("read_only"), 1)
        body = _fn("_guard_status", ACK_PY)
        self.assertIn("get_doc_before_save", body)
        self.assertIn("policy_transition", body)

    def test_the_version_is_part_of_the_identity(self):
        """Without it, "he signed the handbook" says nothing about which handbook,
        and the version people argue about is the one that changed."""
        self.assertIn("version_label", _fields(ACK_JSON))
        self.assertIn("version_label", _fn("request_acknowledgement", POLICIES))

    def test_asking_twice_does_not_duplicate(self):
        body = _fn("request_acknowledgement", POLICIES)
        self.assertIn("frappe.db.exists", body)
        self.assertIn("continue", body)


class TestTheEmergencyContactGapIsChased(unittest.TestCase):
    def test_the_nudge_goes_to_them_not_to_hr(self):
        """The only person who can fill it in is the person whose contact it is."""
        body = _fn("nudge_missing_emergency_contacts", POLICIES)
        self.assertIn("row.user_id", body)
        self.assertIn("emergency_phone_number", body)

    def test_it_only_writes_to_people_who_are_missing_one(self):
        body = _fn("nudge_missing_emergency_contacts", POLICIES)
        self.assertIn("if (row.emergency_phone_number or \"\").strip():", body)
        self.assertIn("continue", body)

    def test_it_is_scheduled_and_shares_its_key_safely(self):
        """Parsed rather than string-matched: a repeated cron key in a dict literal
        REPLACES the earlier one, which is how a duplicate silently disabled four
        chat sweeps earlier in this release."""
        tree = ast.parse(_text(HOOKS))
        events = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "scheduler_events":
                events = ast.literal_eval(node.value)
        jobs = (events.get("cron") or {}).get("30 7 * * 1") or []
        self.assertIn(
            "erpnext_enhancements.hr_enhancements.policies.nudge_missing_emergency_contacts", jobs
        )
        # And the digest it shares the slot with is still there.
        self.assertIn("erpnext_enhancements.hr_enhancements.tasks.send_expiry_digest", jobs)

    def test_it_never_takes_the_scheduler_down(self):
        body = _fn("nudge_missing_emergency_contacts", POLICIES)
        self.assertIn("except Exception", body)
        self.assertIn("log_error", body)


class TestEverythingIsReachableAndScoped(unittest.TestCase):
    def test_every_policy_endpoint_has_a_caller(self):
        tree = ast.parse(_text(POLICIES))
        whitelisted = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if getattr(target, "attr", None) == "whitelist":
                    whitelisted.add(node.name)
        self.assertTrue(whitelisted)
        callers = _js(ACK_JS) + _js(ACK_LIST_JS)
        for name in sorted(whitelisted):
            with self.subTest(endpoint=name):
                self.assertIn(f"policies.{name}", callers)

    def test_an_acknowledgement_is_scoped_to_its_owner(self):
        body = _fn("acknowledgement_query_conditions", PERMISSIONS)
        self.assertIn("`user` =", body)
        # No reports_to arm: whether a colleague signed the handbook is HR's
        # business rather than their manager's.
        self.assertNotIn("reports_to", body)

    def test_the_case_record_has_no_row_filter_because_it_needs_none(self):
        """It has no Employee DocPerm at all, so there is nothing for a row filter
        to narrow — and registering one would imply there was."""
        hooks = _text(HOOKS)
        at = hooks.index("permission_query_conditions")
        block = hooks[at : at + 6000]
        self.assertNotIn('"HR Case Record"', block)


if __name__ == "__main__":
    unittest.main()
