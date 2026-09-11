"""Who may sign whom — the predicate, and whether every door actually asks it.

The behavioural half is straightforward. The structural half is the one that
matters, and it exists because of a failure this module has already had twice.

**A sign-off can be submitted through two doors.** ``record_signoff`` sets
``ignore_permissions = True`` and calls ``submit()``; the Desk form's Submit
button calls ``submit()`` directly and never touches the endpoint. So a rule that
lives only in ``signoff._assert_may_sign`` is not a rule — it is a suggestion
that one of the two doors happens to make. That is the same shape as the six
whitelisted sign-off functions that shipped complete in v1.215.0 with no caller
of any kind, and the same shape as the Phase-4 contract that passed while the
gate it described did not gate. So the tests below assert *the call sites*, by
walking the AST, not by grepping — a comment naming ``authority_basis`` is not a
call to it.

Five sites, and ``before_submit`` is the load-bearing one:

1. ``signoff._assert_may_sign`` — the endpoint.
2. ``TrainingSignoff.before_submit`` — the Desk door.
3. ``permissions.signoff_query_conditions`` — list views.
4. ``permissions.signoff_has_permission`` — reading one row.
5. ``signoff.get_signoff_queue`` — the supervisor worklist.

Sites 3 to 5 are not decoration either. Authority you cannot see a queue for is
authority nobody exercises: on this site the one Senior Technician is not the
``reports_to`` of any of the four Junior Technicians he is now allowed to sign,
so without the tier arm on the filters he would be granted the power and shown an
empty list.

Run: python -m unittest erpnext_enhancements.tests.test_training_authority
"""

import ast
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
AUTHORITY = APP / "training/authority.py"
SIGNOFF = APP / "training/signoff.py"
PERMISSIONS = APP / "training/permissions.py"
CONTROLLER = APP / "training/doctype/training_signoff/training_signoff.py"
SIGNOFF_JSON = APP / "training/doctype/training_signoff/training_signoff.json"

authority = None


# ------------------------------------------------------------------ AST helpers


def _calls(path, name, cls=None):
    """Every function name called inside a function or method, via the AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scope = tree.body
    if cls:
        scope = next(
            n.body for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls
        )
    node = next(
        (n for n in scope if isinstance(n, ast.FunctionDef) and n.name == name), None
    )
    if node is None:
        raise AssertionError(f"{cls + '.' if cls else ''}{name} not found in {path.name}")
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            fn = n.func
            if isinstance(fn, ast.Name):
                names.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                names.add(fn.attr)
    return names


# ------------------------------------------------------------------ frappe stub


class _FakeDB:
    def __init__(self):
        self.positions = {}
        self.employees = {}
        # Keyed by DOCTYPE, matching real frappe. It used to be keyed "tabEmployee"
        # -- which reproduced the production bug exactly, so the stub answered True
        # for the one argument real frappe raises on, and CI stayed green while five
        # call sites were broken. A stub that mirrors a mistake hides it.
        self.columns = {"Employee": {"custom_position"}}

    def has_column(self, doctype, column):
        if doctype.startswith("tab"):
            # Real frappe prefixes "tab" itself and raises TableMissingError on the
            # resulting "tabtabEmployee". Raising here too is the whole point.
            raise AssertionError(
                f"has_column takes a DocType, not a table name (got {doctype!r})"
            )
        return column in self.columns.get(doctype, set())

    def get_value(self, doctype, name, fields=None, as_dict=False):
        if doctype == "Employee":
            if isinstance(name, dict):
                user = name.get("user_id")
                row = self.employees.get(user)
            else:
                row = None
            if not row:
                return None
            return row.get(fields) if isinstance(fields, str) else None
        row = self.positions.get(name)
        if not row:
            return None
        if as_dict:
            return types.SimpleNamespace(**{f: row.get(f) for f in fields})
        if isinstance(fields, list):
            return [row.get(f) for f in fields]
        return row.get(fields)


def _install_stub():
    fake = types.ModuleType("frappe")
    fake.db = _FakeDB()
    fake.roles = {}

    def get_all(doctype, filters=None, pluck=None, **kwargs):
        if doctype == "Employee":
            rows = list(fake.db.employees.values())
            for field, cond in (filters or {}).items():
                if isinstance(cond, list) and cond[0] == "in":
                    rows = [r for r in rows if r.get(field) in cond[1]]
                elif not isinstance(cond, list):
                    rows = [r for r in rows if r.get(field) == cond]
            return [r.get(pluck) for r in rows] if pluck else rows
        rows = list(fake.db.positions.values())
        for field, cond in (filters or {}).items():
            if isinstance(cond, list):
                op, value = cond
                rows = [r for r in rows if (r.get(field) or 0) < value] if op == "<" else rows
            else:
                rows = [r for r in rows if r.get(field) == cond]
        return [r["name"] for r in rows] if pluck else rows

    fake.get_all = get_all
    # position.py decorates get_position_children with @frappe.whitelist(), which
    # runs at import time.
    fake.whitelist = lambda *a, **k: (lambda fn: fn)
    fake.get_roles = lambda user=None: fake.roles.get(user, [])
    fake.throw = lambda *a, **k: (_ for _ in ()).throw(AssertionError("throw"))
    fake._ = lambda t: t
    sys.modules["frappe"] = fake

    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda v: int(v or 0)
    sys.modules["frappe.utils"] = utils
    fake.utils = utils

    nested = types.ModuleType("frappe.utils.nestedset")
    nested.NestedSet = type("NestedSet", (), {})
    sys.modules["frappe.utils.nestedset"] = nested

    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = type("Document", (), {})
    sys.modules["frappe.model"] = model
    sys.modules["frappe.model.document"] = document

    # authority imports MANAGER_ROLES from signoff lazily; stub the module so the
    # import inside the function resolves without dragging in the whole runtime.
    so = types.ModuleType("erpnext_enhancements.training.signoff")
    so.MANAGER_ROLES = {"Training Manager", "System Manager", "HR Manager"}
    sys.modules["erpnext_enhancements.training.signoff"] = so
    return fake


def setUpModule():
    global authority
    fake = _install_stub()
    from erpnext_enhancements.training import authority as module

    authority = module
    authority.frappe = fake
    sys.modules[
        "erpnext_enhancements.hr_enhancements.doctype.position.position"
    ].frappe = fake


def _place(user, position):
    sys.modules["frappe"].db.employees[user] = {
        "user_id": user,
        "custom_position": position,
        "status": "Active",
    }


def _rung(name, family, tier, is_active=1, is_group=0):
    sys.modules["frappe"].db.positions[name] = {
        "name": name, "job_family": family, "tier": tier,
        "is_active": is_active, "is_group": is_group,
    }


def _doc(user, supervisor_user=None):
    return {"user": user, "supervisor_user": supervisor_user}


# ------------------------------------------------------------------ structure


class TestEveryDoorAsksThePredicate(unittest.TestCase):
    def test_the_endpoint_asks(self):
        self.assertIn("may_sign", _calls(SIGNOFF, "_assert_may_sign"))

    def test_the_desk_door_asks(self):
        """The load-bearing one. `record_signoff` bypasses permissions and the Desk
        Submit button never reaches the endpoint, so this is the door a supervisor
        sitting in the Desk actually uses."""
        self.assertIn(
            "_require_authority", _calls(CONTROLLER, "before_submit", cls="TrainingSignoff")
        )
        self.assertIn(
            "snapshot_positions", _calls(CONTROLLER, "_require_authority", cls="TrainingSignoff")
        )

    def test_the_list_filter_asks(self):
        self.assertIn("signable_learner_users", _calls(PERMISSIONS, "signoff_query_conditions"))

    def test_the_single_document_read_asks(self):
        """A query condition filters lists and says nothing about `frappe.get_doc`,
        so without this a supervisor sees a request in the list and gets a
        permission error opening it."""
        self.assertIn("authority_basis", _calls(PERMISSIONS, "signoff_has_permission"))

    def test_the_queue_asks(self):
        self.assertIn("signable_learner_users", _calls(SIGNOFF, "get_signoff_queue"))

    def test_nobody_reimplements_it(self):
        """The rule is `outranks`, and it lives in one file. A second comparison of
        two tiers anywhere in training/ is a copy that will drift."""
        offenders = []
        for path in sorted(APP.glob("training/**/*.py")):
            if path.name == "authority.py":
                continue
            src = path.read_text(encoding="utf-8")
            if ".tier" in src and "outranks" not in src:
                offenders.append(str(path.relative_to(APP)))
        self.assertEqual(
            offenders, [], f"these read a tier without going through outranks(): {offenders}"
        )


class TestTheAttestationIsSnapshotted(unittest.TestCase):
    """A Position link resolves to today; an attestation is about what was true when
    it was made. Same doctrine as the completion's course-title and content-hash."""

    def setUp(self):
        self.fields = {
            f["fieldname"]: f
            for f in json.loads(SIGNOFF_JSON.read_text(encoding="utf-8"))["fields"]
        }

    def test_the_three_snapshot_fields_exist(self):
        for name in ("authority_basis", "supervisor_position", "learner_position"):
            with self.subTest(field=name):
                self.assertIn(name, self.fields)

    def test_they_are_read_only(self):
        """Editable, they would be a way to write a basis that was never true."""
        for name in ("authority_basis", "supervisor_position", "learner_position"):
            with self.subTest(field=name):
                self.assertEqual(self.fields[name].get("read_only"), 1)

    def test_the_basis_vocabulary_matches_the_code(self):
        declared = [o for o in self.fields["authority_basis"]["options"].split("\n") if o]
        self.assertEqual(sorted(declared), sorted([authority.OBSERVED, authority.TIER, authority.DELEGATE]))


# ------------------------------------------------------------------ behaviour


class TestAuthorityBasis(unittest.TestCase):
    def setUp(self):
        fake = sys.modules["frappe"]
        fake.db.positions.clear()
        fake.db.employees.clear()
        fake.roles.clear()
        _rung("Junior Technician", "Technician", 1)
        _rung("Senior Technician", "Technician", 2)
        _rung("Master Technician", "Technician", 3)
        _rung("Senior Designer", "Design", 2)
        _place("junior@x", "Junior Technician")
        _place("junior2@x", "Junior Technician")
        _place("senior@x", "Senior Technician")
        _place("master@x", "Master Technician")
        _place("designer@x", "Senior Designer")

    def test_the_learner_never_signs_their_own(self):
        """First and unconditional, whatever roles or position they hold. A
        self-attestation is not a weaker attestation, it is no attestation."""
        sys.modules["frappe"].roles["junior@x"] = ["System Manager", "Training Manager"]
        _place("junior@x", "Master Technician")
        self.assertIsNone(authority.authority_basis(_doc("junior@x"), "junior@x"))

    def test_the_named_supervisor_may_sign(self):
        basis = authority.authority_basis(_doc("junior@x", "senior@x"), "senior@x")
        self.assertEqual(basis, authority.OBSERVED)

    def test_a_senior_may_sign_a_junior_on_the_same_ladder(self):
        """Nik's rule, and the one the reporting tree cannot express: on prod the
        Senior Technician has zero direct reports."""
        self.assertEqual(
            authority.authority_basis(_doc("junior@x"), "senior@x"), authority.TIER
        )

    def test_a_master_may_sign_a_senior_and_a_junior(self):
        self.assertEqual(authority.authority_basis(_doc("senior@x"), "master@x"), authority.TIER)
        self.assertEqual(authority.authority_basis(_doc("junior@x"), "master@x"), authority.TIER)

    def test_a_peer_may_not(self):
        self.assertIsNone(authority.authority_basis(_doc("junior@x"), "junior2@x"))

    def test_a_junior_may_not_sign_a_senior(self):
        self.assertIsNone(authority.authority_basis(_doc("senior@x"), "junior@x"))

    def test_authority_does_not_cross_job_families(self):
        self.assertIsNone(authority.authority_basis(_doc("junior@x"), "designer@x"))

    def test_a_manager_may_sign_anybody(self):
        sys.modules["frappe"].roles["boss@x"] = ["Training Manager"]
        self.assertEqual(
            authority.authority_basis(_doc("junior@x"), "boss@x"), authority.DELEGATE
        )

    def test_hr_manager_counts_as_a_delegate(self):
        sys.modules["frappe"].roles["hr@x"] = ["HR Manager"]
        self.assertEqual(authority.authority_basis(_doc("junior@x"), "hr@x"), authority.DELEGATE)

    def test_somebody_with_nothing_may_not(self):
        sys.modules["frappe"].roles["random@x"] = ["Employee"]
        self.assertIsNone(authority.authority_basis(_doc("junior@x"), "random@x"))

    def test_no_employee_record_means_no_tier_authority(self):
        """A customer contact has a User and no Employee, and must never acquire
        authority over staff. `_position_of_user` returning None is the mechanism."""
        sys.modules["frappe"].roles["customer@x"] = ["Training Learner"]
        self.assertIsNone(authority.authority_basis(_doc("junior@x"), "customer@x"))

    def test_it_fails_closed_without_the_custom_field(self):
        """A site part-way through this release has no `custom_position` column. Tier
        authority is simply unavailable until it migrates; the other bases still
        work, so sign-offs keep being recordable."""
        sys.modules["frappe"].db.columns["Employee"] = set()
        try:
            self.assertIsNone(authority.authority_basis(_doc("junior@x"), "senior@x"))
            self.assertEqual(
                authority.authority_basis(_doc("junior@x", "senior@x"), "senior@x"),
                authority.OBSERVED,
            )
        finally:
            sys.modules["frappe"].db.columns["Employee"] = {"custom_position"}

    def test_may_sign_agrees_with_the_basis(self):
        for signer in ("senior@x", "master@x", "junior2@x", "designer@x"):
            with self.subTest(signer=signer):
                doc = _doc("junior@x")
                self.assertEqual(
                    authority.may_sign(doc, signer),
                    authority.authority_basis(doc, signer) is not None,
                )


class TestSignableLearnerUsers(unittest.TestCase):
    def setUp(self):
        fake = sys.modules["frappe"]
        fake.db.positions.clear()
        fake.db.employees.clear()
        fake.roles.clear()
        _rung("Junior Technician", "Technician", 1)
        _rung("Senior Technician", "Technician", 2)
        _rung("Senior Designer", "Design", 2)
        for user in ("j1@x", "j2@x", "j3@x", "j4@x"):
            _place(user, "Junior Technician")
        _place("jesse@x", "Senior Technician")
        _place("designer@x", "Senior Designer")

    def test_a_senior_sees_every_junior_on_their_ladder(self):
        """The prod case exactly: four Junior Technicians, one Senior, and not one
        of them reports to him."""
        self.assertEqual(
            sorted(authority.signable_learner_users("jesse@x")), ["j1@x", "j2@x", "j3@x", "j4@x"]
        )

    def test_it_does_not_reach_another_ladder(self):
        self.assertEqual(authority.signable_learner_users("designer@x"), [])

    def test_a_junior_sees_nobody(self):
        self.assertEqual(authority.signable_learner_users("j1@x"), [])

    def test_it_never_includes_yourself(self):
        _place("jesse@x", "Junior Technician")
        self.assertNotIn("jesse@x", authority.signable_learner_users("jesse@x"))

    def test_no_position_means_nobody(self):
        self.assertEqual(authority.signable_learner_users("nobody@x"), [])


if __name__ == "__main__":
    unittest.main()
