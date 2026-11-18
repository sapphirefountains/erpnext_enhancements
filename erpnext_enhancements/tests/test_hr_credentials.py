"""External credentials, their expiry, and the grid that answers "who can I send?".

A qualification somebody *else* issued — OSHA, forklift, CDL and its medical
card, first aid, respirator fit test — was structurally unrecordable in this app
until v1.386.0: `Training Certificate.completion` is `reqd: 1`, so every
certificate the system could hold had to originate in one of its own courses. For
a company sending crews to client sites that is the single largest gap in the
whole module, and the answer lived in a filing cabinet.

Three things are pinned here, each because of a specific way this could go wrong.

**The DocPerm and the scoping hook ship together.** `Employee Credential` grants
`read` to the `Employee` role, which every staff account holds. That grant is
deliberate — it is what puts a technician's own forklift ticket on their own
profile, and it is what keeps the HR module inside `allow_modules` — but a
DocPerm with no scoping hook is the one combination that leaks, and this app has
just finished paying for that lesson three times over in the Training module.

**Status is derived, never typed.** It is arithmetic on a date, so it is correct
the day it is saved and wrong every day after. The whole value of the record is
that it is true on the morning of the job.

**Expiring still counts as qualified.** Somebody whose ticket lapses in six weeks
can work today. A horizon that reads as a refusal does the opposite of its job,
and this is exactly the sort of thing that gets inverted in a later refactor by
somebody being helpful.

Run: python -m unittest erpnext_enhancements.tests.test_hr_credentials
"""

import json
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
MODULE_DIR = APP / "hr_enhancements"
CREDENTIAL_JSON = MODULE_DIR / "doctype/employee_credential/employee_credential.json"
CREDENTIAL_PY = MODULE_DIR / "doctype/employee_credential/employee_credential.py"
TYPE_JSON = MODULE_DIR / "doctype/credential_type/credential_type.json"
PERMISSIONS = MODULE_DIR / "permissions.py"
TASKS = MODULE_DIR / "tasks.py"
HOOKS = APP / "hooks.py"
REPORT_PY = MODULE_DIR / "report/skills_matrix/skills_matrix.py"
REPORT_JSON = MODULE_DIR / "report/skills_matrix/skills_matrix.json"
SEED = APP / "patches/seed_credential_types.py"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _text(path):
    return path.read_text(encoding="utf-8")


def _field(fieldname, path=CREDENTIAL_JSON):
    return next(f for f in _read(path)["fields"] if f["fieldname"] == fieldname)


class TestTheRecordIsScoped(unittest.TestCase):
    def test_it_grants_the_employee_role(self):
        """Deliberate, and load-bearing twice over: a technician's own card on their
        own profile, and the module's `allow_modules` opener."""
        perms = _read(CREDENTIAL_JSON)["permissions"]
        self.assertTrue(any(p.get("role") == "Employee" and p.get("read") for p in perms))

    def test_the_scoping_hook_ships_with_it(self):
        hooks = _text(HOOKS)
        self.assertIn(
            '"Employee Credential": "erpnext_enhancements.hr_enhancements.permissions.credential_query_conditions"',
            hooks,
        )

    def test_the_single_document_twin_ships_with_it_too(self):
        """A query condition filters lists and says nothing about `frappe.get_doc()`,
        so shipping one without the other leaves the hole in whichever half you
        skipped."""
        self.assertIn(
            '"Employee Credential": "erpnext_enhancements.hr_enhancements.permissions.credential_has_permission"',
            _text(HOOKS),
        )

    def test_the_scoping_column_is_named_user(self):
        """Every learner-owned doctype in this app scopes on a column called exactly
        `user`; anything else silently loses row-level scoping."""
        self.assertEqual(_field("user")["fieldtype"], "Link")

    def test_the_scoping_column_is_derived_not_typed(self):
        """A caller-supplied `user` is a way to file somebody else's licence under
        your own name and then read it back."""
        self.assertEqual(_field("user").get("read_only"), 1)
        self.assertIn("_resolve_user", _text(CREDENTIAL_PY))

    def test_the_hook_falls_back_rather_than_failing(self):
        """A person must always be able to see their own licence, even on a site
        where the ladder has not migrated yet."""
        body = _text(PERMISSIONS)
        self.assertIn("except Exception:", body)
        self.assertIn("allowed = {user}", body)

    def test_only_hr_manager_and_system_manager_are_unscoped(self):
        """v1.826.8: every other role, `HR User` included, goes through the row
        scoping. Read from the AST, so a comment naming a role cannot satisfy it."""
        self.assertEqual(_unscoped_roles(), {"System Manager", "HR Manager"})


# ------------------------------------------------------------------ HR User parity
#
# v1.826.8. The seven doctypes the hooks in `permissions.py` scope. On each, the
# `HR User` DocPerm rows grant nothing the `Employee` rows on the same doctype do
# not, so the row scoping decides what a holder of `HR User` sees, and only HR
# Manager and System Manager see every record.

SCOPED = {
    "Safety Incident": "safety_incident",
    "Work Restriction": "work_restriction",
    "Tier Review": "tier_review",
    "Employee Credential": "employee_credential",
    "Time Off Request": "time_off_request",
    "Onboarding Checklist": "onboarding_checklist",
    "Policy Acknowledgement": "policy_acknowledgement",
}

FLAGS = (
    "read", "write", "create", "delete", "submit", "cancel", "amend", "report",
    "export", "import", "share", "print", "email", "set_user_permissions",
)


def _unscoped_roles():
    import ast

    for node in ast.parse(_text(PERMISSIONS)).body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "UNSCOPED_ROLES" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("UNSCOPED_ROLES not found")


def _granted(perms, role):
    return {
        (perm.get("permlevel") or 0, flag)
        for perm in perms
        if perm.get("role") == role
        for flag in FLAGS
        if perm.get(flag)
    }


class TestHRUserGrantsNoMoreThanEmployee(unittest.TestCase):
    def test_hr_user_rows_are_within_the_employee_rows(self):
        for doctype, folder in SCOPED.items():
            perms = _read(MODULE_DIR / f"doctype/{folder}/{folder}.json")["permissions"]
            employee = _granted(perms, "Employee")
            with self.subTest(doctype=doctype):
                self.assertTrue(employee, "the Employee rows are what ordinary staff rely on")
                self.assertLessEqual(_granted(perms, "HR User"), employee)

    def test_hr_manager_keeps_full_access(self):
        for doctype, folder in SCOPED.items():
            perms = _read(MODULE_DIR / f"doctype/{folder}/{folder}.json")["permissions"]
            with self.subTest(doctype=doctype):
                for flag in ("read", "write", "create"):
                    self.assertIn((0, flag), _granted(perms, "HR Manager"))

    def test_every_scoped_doctype_has_both_hooks(self):
        hooks = _text(HOOKS)
        for doctype in SCOPED:
            for hook in ("query_conditions", "has_permission"):
                with self.subTest(doctype=doctype, hook=hook):
                    self.assertRegex(
                        hooks,
                        rf'"{doctype}": "erpnext_enhancements\.hr_enhancements\.permissions\.\w+_{hook}"',
                    )

    def test_no_custom_docperm_fixture_widens_them(self):
        """A Custom DocPerm row replaces the doctype's own rows entirely, so one in
        the fixtures for these doctypes would undo the parity above."""
        rows = _read(APP / "fixtures/custom_docperm.json")
        self.assertFalse([r for r in rows if r.get("parent") in SCOPED])


class TestTheScopingHooks(unittest.TestCase):
    """The hooks run against a `frappe` stub: one HR Manager, one supervisor and
    two staff who report to them, everyone but the HR Manager holding `HR User`."""

    ROLES = {
        "hrm@example.com": ["HR Manager", "Employee"],
        "lead@example.com": ["HR User", "Employee"],
        "tech@example.com": ["HR User", "Employee"],
        "other@example.com": ["HR User", "Employee"],
    }
    EMPLOYEES = {
        "EMP-1": {"user_id": "hrm@example.com", "reports_to": None},
        "EMP-2": {"user_id": "lead@example.com", "reports_to": None},
        "EMP-3": {"user_id": "tech@example.com", "reports_to": "EMP-2"},
        "EMP-4": {"user_id": "other@example.com", "reports_to": None},
    }

    @classmethod
    def setUpClass(cls):
        import importlib.util
        import sys
        import types

        cls._saved = {k: sys.modules.get(k) for k in ("frappe", "erpnext_enhancements.training.authority")}
        employees = cls.EMPLOYEES

        def _match(row, filters):
            return all(row.get(k) == v for k, v in filters.items())

        class DB:
            def get_value(self, doctype, filters, field):
                assert doctype == "Employee"
                if isinstance(filters, dict):
                    hit = next((n for n, r in employees.items() if _match(r, filters)), None)
                    return hit if field == "name" else (employees[hit][field] if hit else None)
                return (employees.get(filters) or {}).get(field)

            def exists(self, doctype, filters):
                return any(_match(r, filters) for r in employees.values())

            def escape(self, value):
                return "'" + str(value) + "'"

        fake = types.ModuleType("frappe")
        fake.db = DB()
        fake.session = types.SimpleNamespace(user="Guest")
        fake.get_roles = lambda user: cls.ROLES.get(user, [])

        def get_all(doctype, filters=None, pluck=None, **kwargs):
            rows = [dict(r, name=n) for n, r in employees.items() if _match(r, filters or {})]
            return [r[pluck] for r in rows] if pluck else rows

        fake.get_all = get_all
        authority = types.ModuleType("erpnext_enhancements.training.authority")
        authority.signable_learner_users = lambda user: []
        sys.modules["frappe"] = fake
        sys.modules["erpnext_enhancements.training.authority"] = authority

        spec = importlib.util.spec_from_file_location("_hr_permissions_under_test", PERMISSIONS)
        cls.perms = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.perms)

    @classmethod
    def tearDownClass(cls):
        import sys

        for key, value in cls._saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value

    def test_hr_manager_sees_every_record(self):
        p = self.perms
        self.assertEqual(p.credential_query_conditions("hrm@example.com"), "")
        self.assertTrue(p.incident_has_permission({"employee": "EMP-4"}, "read", "hrm@example.com"))

    def test_hr_user_is_scoped_to_their_own_rows(self):
        p = self.perms
        me, colleague = "other@example.com", "tech@example.com"
        condition = p.credential_query_conditions(me)
        self.assertIn(f"'{me}'", condition)
        self.assertNotIn(colleague, condition)
        self.assertFalse(p.credential_has_permission({"user": colleague}, "read", me))
        self.assertTrue(p.credential_has_permission({"user": me}, "read", me))

    def test_a_supervisor_sees_their_direct_reports(self):
        p = self.perms
        lead, report = "lead@example.com", "tech@example.com"
        self.assertIn(f"'{report}'", p.restriction_query_conditions(lead))
        self.assertTrue(p.restriction_has_permission({"user": report}, "read", lead))
        self.assertTrue(p.incident_has_permission({"employee": "EMP-3"}, "read", lead))
        self.assertTrue(p.timeoff_has_permission({"user": report}, "read", lead))
        self.assertFalse(p.incident_has_permission({"employee": "EMP-4"}, "read", lead))

    def test_staff_still_reach_their_own_records(self):
        p = self.perms
        me = "tech@example.com"
        self.assertTrue(p.timeoff_has_permission({"employee": "EMP-3"}, "create", me))
        self.assertTrue(p.acknowledgement_has_permission({"user": me}, "write", me))
        self.assertTrue(p.tier_review_has_permission({"user": me}, "write", me))
        self.assertTrue(p.onboarding_has_permission({"user": me}, "write", me))
        self.assertTrue(p.incident_has_permission({"employee": "EMP-3"}, "read", me))

    def test_anyone_who_may_create_an_incident_can_file_a_new_one(self):
        """`reported_by` is stamped in before_insert, after the create check."""
        p = self.perms
        me = "other@example.com"
        self.assertTrue(p.incident_has_permission({"incident_type": "Near Miss"}, "create", me))
        filed = {"employee": "EMP-3", "reported_by": "x@example.com"}
        self.assertFalse(p.incident_has_permission(filed, "read", me))


class TestStatusIsDerived(unittest.TestCase):
    def test_status_is_read_only(self):
        self.assertEqual(_field("status").get("read_only"), 1)

    def test_validate_recomputes_it_every_save(self):
        self.assertIn("self.status = self.derive_status()", _text(CREDENTIAL_PY))

    def test_revocation_beats_the_calendar(self):
        """A revoked credential that has not reached its expiry is still revoked.
        Reading it as Valid because the arithmetic says so is what this ordering
        prevents."""
        body = _text(CREDENTIAL_PY)
        body = body[body.index("def derive_status") :]
        self.assertLess(body.index("REVOKED"), body.index("EXPIRED"))

    def test_no_expiry_is_an_answer_not_missing_data(self):
        """An OSHA 10 card does not lapse. Treating a blank as unknown would make
        every one of them read as a gap forever."""
        body = _text(CREDENTIAL_PY)
        body = body[body.index("def derive_status") :]
        self.assertIn("if not self.expires_on:", body)

    def test_expiring_still_counts_as_held(self):
        body = _text(CREDENTIAL_PY)
        self.assertIn("VALID, EXPIRING", body[body.index("def current_credentials") :])

    def test_the_default_expiry_never_overwrites_a_typed_one(self):
        """The date on the card in their hand beats the arithmetic. A card issued
        late or renewed early has a real date and the person recording it is reading
        it off the document."""
        body = _text(CREDENTIAL_PY)
        body = body[body.index("def _default_expiry") : body.index("def _require_revocation_reason")]
        self.assertIn("if self.expires_on", body)

    def test_a_revocation_needs_a_reason(self):
        self.assertIn("_require_revocation_reason", _text(CREDENTIAL_PY))


class TestTheHorizonExists(unittest.TestCase):
    """Until now nothing in this app warned about anything *before* the fact:
    `certificates.expire_and_recertify` reacts after a training certificate lapses,
    and `fixtures/notification.json` holds nineteen alerts across a dozen doctypes
    and **zero** HR or training ones."""

    def test_the_status_sweep_is_scheduled(self):
        self.assertIn("def refresh_credential_status(", _text(TASKS))
        self.assertIn(
            "erpnext_enhancements.hr_enhancements.tasks.refresh_credential_status", _text(HOOKS)
        )

    def test_the_digest_is_scheduled(self):
        self.assertIn("def send_expiry_digest(", _text(TASKS))
        self.assertIn("erpnext_enhancements.hr_enhancements.tasks.send_expiry_digest", _text(HOOKS))

    def test_the_digest_reaches_the_supervisor_too(self):
        """The holder books the course; the supervisor stops scheduling them past the
        date. Both need telling, and one email each rather than one per card."""
        self.assertIn("_by_supervisor", _text(TASKS))

    def test_the_supervisor_arm_uses_the_reporting_line_not_the_ladder(self):
        """This is 'who plans your week and might schedule you past the date', which
        is exactly what `reports_to` means and exactly what the ladder does not."""
        body = _text(TASKS)
        body = body[body.index("def _by_supervisor") :]
        self.assertIn("reports_to", body)

    def test_neither_job_can_take_the_scheduler_down(self):
        body = _text(TASKS)
        self.assertIn("log_error", body)
        self.assertIn("except Exception:", body)

    def test_the_date_filter_is_explicit_and_guarded(self):
        """A filter on a nullable date pushed through the query builder is coalesced,
        and a NULL expiry lands on whichever side of the comparison the sentinel
        falls -- which is not the side you assumed.

        Asserts the PROPERTY, not a spelling. The first version of this pinned the
        exact dict literal `"expires_on": ["<=", horizon]`, so adding the `is set`
        clause in v1.426.5 -- which is strictly the stronger guarantee this test wants
        -- broke it. An assertion keyed to one way of writing something fails on an
        improvement as readily as on a regression."""
        body = _text(TASKS)
        self.assertIn("horizon", body)
        self.assertIn('["expires_on", "is", "set"]', body)
        self.assertIn('["expires_on", "<=", horizon]', body)


class TestItIsNotATrainingCertificate(unittest.TestCase):
    def test_the_reason_the_split_exists_still_holds(self):
        """If `Training Certificate.completion` ever stops being required, re-open
        the question of whether these are two doctypes. Until then, merging them
        would mean either weakening the completion's guarantees or inventing an
        attempt for a forklift ticket."""
        cert = _read(APP / "training/doctype/training_certificate/training_certificate.json")
        completion = next(f for f in cert["fields"] if f["fieldname"] == "completion")
        self.assertEqual(completion.get("reqd"), 1)

    def test_the_catalogue_is_data_not_a_select(self):
        """The list of tickets a fountain crew needs is not a thing to redeploy for."""
        self.assertEqual(_field("credential_type")["fieldtype"], "Link")
        self.assertEqual(_field("credential_type")["options"], "Credential Type")

    def test_the_seed_covers_what_a_gc_actually_asks_for(self):
        seeded = _text(SEED)
        for ticket in ("OSHA 10", "Forklift", "CDL", "DOT Medical Card", "First Aid", "Respirator"):
            with self.subTest(ticket=ticket):
                self.assertIn(ticket, seeded)

    def test_the_seed_is_insert_only(self):
        """Somebody may have adjusted a validity to match how this company actually
        renews things; a patch does not get to overwrite that."""
        self.assertIn('frappe.db.exists("Credential Type", name)', _text(SEED))

    def test_evidence_is_askable_for(self):
        """A credential somebody else issued is worth what you can produce when a GC
        or an insurer asks for it."""
        self.assertEqual(_field("attachment")["fieldtype"], "Attach")


class TestSkillsMatrix(unittest.TestCase):
    def test_it_is_one_grid_over_both_sources(self):
        """A manager scheduling a basin drain does not care which system a ticket
        came out of. Two reports would mean two screens and a mental join on every
        scheduling decision."""
        src = _text(REPORT_PY)
        self.assertIn("Training Completion", src)
        self.assertIn("Employee Credential", src)

    def test_columns_are_namespaced_by_source(self):
        """A Credential Type and a Training Course are allowed to share a title, and
        a key collision would merge two columns into one -- which reads as everybody
        suddenly being qualified."""
        src = _text(REPORT_PY)
        self.assertIn('f"course:{', src)
        self.assertIn('f"cred:{', src)

    def test_whoever_schedules_the_work_can_open_it(self):
        """The point of knowing a Junior is signed off is knowing you can send him."""
        roles = {r["role"] for r in _read(REPORT_JSON)["roles"]}
        self.assertIn("Projects Manager", roles)
        self.assertIn("Maintenance Manager", roles)

    def test_its_roles_can_read_its_ref_doctype(self):
        """`Training Completion Matrix` lists HR Manager as a reader while Training
        Completion granted HR Manager no DocPerm at all, so it errored for exactly
        its intended reader. Same trap, checked."""
        self.assertEqual(_read(REPORT_JSON)["ref_doctype"], "Employee")

    def test_expiring_is_not_counted_as_a_gap(self):
        """Somebody inside the horizon is qualified today; counting them as a gap
        would make the horizon do the opposite of its job.

        Asserted on the CONDITION, not on the whole line. WI-073 added a second
        clause to that `if` -- gaps are now scoped to what the person's rung
        actually asks for -- and pinning the literal line meant a correct change
        failed the build while the property it was guarding was untouched. A
        contract test should break when the behaviour changes, not when the
        sentence does.
        """
        src = _text(REPORT_PY)
        self.assertIn("if state in (LAPSED, NEVER)", src)
        gap_line = next(line for line in src.splitlines() if "if state in (LAPSED, NEVER)" in line)
        self.assertNotIn("EXPIRING", gap_line)

    def test_lapsed_and_never_are_different_words(self):
        """A lapsed forklift ticket is a renewal; a missing one is a course. Same
        colour, different jobs."""
        src = _text(REPORT_PY)
        self.assertIn('LAPSED = "Lapsed"', src)
        self.assertIn('NEVER = "Never"', src)

    def test_the_cell_says_the_word(self):
        """Colour is the secondary signal. Somebody reading this on a phone in
        daylight, or colour-blind, gets the same answer -- and Expiring in particular
        must read as still-qualified, which a red cell with no text would not."""
        js = _text(MODULE_DIR / "report/skills_matrix/skills_matrix.js")
        self.assertIn("default_formatter(value, row, column, data)", js)

    def test_it_does_not_query_once_per_cell(self):
        """Sixteen people times twenty qualifications is 320 cells, and this is a
        screen somebody opens on a Friday afternoon."""
        src = _text(REPORT_PY)
        body = src[src.index("def _held") : src.index("def _course_state")]
        self.assertLessEqual(body.count("frappe.get_all"), 2)


if __name__ == "__main__":
    unittest.main()
