"""Bench-free tests for the approval stamps on Budget Reallocation and Change Order.

Two rules, both run here rather than read from the source:

* **Separation on a budget reallocation.** The person who requests it does not record its project
  manager approval, and the project manager approval and the second approval come from two
  different people, neither of them the requester.
* **The stamps are written only by the module's own endpoints.** ``read_only`` is a Desk display
  property and is not checked on the server, so each controller refuses a save that changes an
  approval stamp unless the approve, record or revoke endpoint set its flag on the document, and
  refuses a new document that arrives already carrying one. ``requested_by`` is always the session
  user on insert. Migrate, install, patches and a System Manager's Data Import keep what they
  write.

The controllers and the endpoints are loaded from their files over a stand-in ``frappe`` whose
``Document.save`` reloads the stored copy as ``get_doc_before_save`` and runs ``validate`` -- the
two pieces of the framework these rules depend on.

Run: python -m unittest erpnext_enhancements.tests.test_approval_stamps
"""

import datetime
import importlib.util
import sys
import types
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.quality import budgets  # frappe-free

DOCTYPE_ROOT = APP_ROOT / "project_enhancements" / "doctype"
BR_CONTROLLER = DOCTYPE_ROOT / "budget_reallocation" / "budget_reallocation.py"
CO_CONTROLLER = DOCTYPE_ROOT / "change_order" / "change_order.py"
BR_API = APP_ROOT / "api" / "project_budget.py"
CO_API = APP_ROOT / "api" / "change_order.py"

PE = "erpnext_enhancements.project_enhancements"
BR_MODULE = f"{PE}.doctype.budget_reallocation.budget_reallocation"
CO_MODULE = f"{PE}.doctype.change_order.change_order"

STUBBED = (
    "frappe",
    "frappe.model",
    "frappe.model.document",
    "frappe.utils",
    PE,
    f"{PE}.budget_rollup",
    f"{PE}.doctype",
    f"{PE}.doctype.budget_reallocation",
    BR_MODULE,
    f"{PE}.doctype.change_order",
    CO_MODULE,
)

REQUESTER = "requester@example.com"
PM = "pm@example.com"
FINANCE = "finance@example.com"

# Stored rows, keyed (doctype, name). ``Document.save`` reads the previous version from here.
DB = {}
CLASSES = {}
_saved_modules = {}
fake = None
br = co = br_api = co_api = None


class ValidationError(Exception):
    pass


class PermissionError_(Exception):
    pass


def _get_datetime(value):
    if isinstance(value, datetime.datetime):
        return value
    return datetime.datetime.fromisoformat(str(value))


def _cint(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


class _Clock:
    """``now_datetime`` that moves forward a second per call, with microseconds, like the real one."""

    def __init__(self):
        self.now = datetime.datetime(2026, 11, 19, 9, 0, 0, 123456)

    def __call__(self):
        self.now += datetime.timedelta(seconds=1)
        return self.now


class Document:
    """Just enough of frappe's Document for ``validate`` and the endpoints."""

    def __init__(self, **values):
        self.flags = {}
        self._new = True
        self._before = None
        self.docstatus = 0
        self.amended_from = None
        for key, value in values.items():
            setattr(self, key, value)

    def __getattr__(self, name):
        # A declared field that was never set reads None on a real document.
        if name.startswith("_"):
            raise AttributeError(name)
        return None

    def is_new(self):
        return self._new

    def get(self, key, default=None):
        return getattr(self, key, default)

    def set(self, key, value):
        setattr(self, key, value)

    def append(self, key, row):
        getattr(self, key).append(row)

    def get_doc_before_save(self):
        return self._before

    def check_permission(self, ptype=None):
        return True

    def _values(self):
        return {k: v for k, v in vars(self).items() if not k.startswith("_") and k != "flags"}

    def insert(self):
        self.validate()
        self._new = False
        DB[(self.doctype, self.name)] = self._values()
        return self

    def save(self):
        if self._new:
            return self.insert()
        self._before = _load(self.doctype, self.name)
        self.validate()
        DB[(self.doctype, self.name)] = self._values()
        return self


def _load(doctype, name):
    doc = CLASSES[doctype](**dict(DB[(doctype, name)]))
    doc._new = False
    return doc


def _project(_name):
    rows = [
        types.SimpleNamespace(category=budgets.CATEGORY_LABOR, budgeted_amount=10000),
        types.SimpleNamespace(category=budgets.CATEGORY_MATERIALS, budgeted_amount=5000),
        types.SimpleNamespace(category=budgets.CATEGORY_CONTINGENCY, budgeted_amount=2000),
    ]
    return types.SimpleNamespace(get=lambda key, default=None: rows if key == budgets.LINES_FIELD else default)


def _get_doc(doctype, name=None):
    if doctype == "Project":
        return _project(name)
    return _load(doctype, name)


def _throw(message, exc=None, title=None, **kwargs):
    raise (exc or ValidationError)(message)


def _file_module(path, alias):
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


def setUpModule():
    global fake, br, co, br_api, co_api
    for key in STUBBED:
        _saved_modules[key] = sys.modules.get(key)

    fake = types.ModuleType("frappe")
    fake._ = lambda text: text
    fake.flags = types.SimpleNamespace(in_migrate=False, in_install=False, in_patch=False, in_import=False)
    fake.session = types.SimpleNamespace(user=REQUESTER)
    fake.roles = ["Projects User"]
    fake.get_roles = lambda user=None: list(fake.roles)
    fake.PermissionError = PermissionError_
    fake.ValidationError = ValidationError
    fake.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})
    fake.UniqueValidationError = type("UniqueValidationError", (Exception,), {})
    fake.throw = _throw
    fake.whitelist = lambda *a, **k: (lambda fn: fn)
    fake.has_permission = lambda *a, **k: True
    fake.get_doc = _get_doc
    fake.get_all = lambda *a, **k: []
    fake.msgprint = lambda *a, **k: None
    fake.generate_hash = lambda *a, **k: "0123456789"
    fake.db = types.SimpleNamespace(commit=lambda: None, exists=lambda *a, **k: True)

    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = _Clock()
    utils.get_datetime = _get_datetime
    utils.cint = _cint
    utils.escape_html = lambda text: text
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = Document
    fake.utils, fake.model, model.document = utils, model, document
    for key, module in (
        ("frappe", fake),
        ("frappe.model", model),
        ("frappe.model.document", document),
        ("frappe.utils", utils),
    ):
        sys.modules[key] = module

    # The api modules import these; the real package __init__ is not needed for the rules.
    rollup = types.ModuleType(f"{PE}.budget_rollup")
    rollup.unclassified_for_project = lambda project: 0
    package = types.ModuleType(PE)
    package.budget_rollup = rollup
    sys.modules[PE] = package
    sys.modules[f"{PE}.budget_rollup"] = rollup
    for key in (f"{PE}.doctype", f"{PE}.doctype.budget_reallocation", f"{PE}.doctype.change_order"):
        sys.modules[key] = types.ModuleType(key)

    br = _file_module(BR_CONTROLLER, BR_MODULE)
    co = _file_module(CO_CONTROLLER, CO_MODULE)
    br.BudgetReallocation.doctype = "Budget Reallocation"
    co.ChangeOrder.doctype = "Change Order"
    CLASSES["Budget Reallocation"] = br.BudgetReallocation
    CLASSES["Change Order"] = co.ChangeOrder
    br_api = _file_module(BR_API, "_project_budget_api_under_test")
    co_api = _file_module(CO_API, "_change_order_api_under_test")


def tearDownModule():
    for alias in ("_project_budget_api_under_test", "_change_order_api_under_test"):
        sys.modules.pop(alias, None)
    for key, module in _saved_modules.items():
        if module is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = module


class _Base(unittest.TestCase):
    def setUp(self):
        DB.clear()
        fake.session.user = REQUESTER
        fake.roles = ["Projects User"]
        fake.flags.in_migrate = fake.flags.in_install = fake.flags.in_patch = False
        fake.flags.in_import = False

    def as_user(self, user):
        fake.session.user = user


# --------------------------------------------------------------------------------------------
# Budget Reallocation
# --------------------------------------------------------------------------------------------


class _BudgetBase(_Base):
    def new_reallocation(self, name="BR-0001", protected=False, **values):
        fields = {
            "name": name,
            "project": "PRJ-TEST",
            "from_category": budgets.CATEGORY_CONTINGENCY if protected else budgets.CATEGORY_LABOR,
            "to_category": budgets.CATEGORY_MATERIALS,
            "amount": 500,
            "reason": "test",
        }
        fields.update(values)
        return br.BudgetReallocation(**fields)

    def stored(self, name="BR-0001"):
        return DB[("Budget Reallocation", name)]


class TestFirstApprovalSeparation(_BudgetBase):
    def test_the_requester_cannot_record_the_first_approval(self):
        self.new_reallocation().insert()
        with self.assertRaises(ValidationError) as raised:
            br_api.approve_reallocation("BR-0001")
        self.assertIn("requested it", str(raised.exception))
        self.assertIsNone(self.stored().get("pm_approved_by"))
        self.assertIsNone(self.stored().get("pm_approved_on"))

    def test_a_different_approver_can(self):
        self.new_reallocation().insert()
        self.as_user(PM)
        state = br_api.approve_reallocation("BR-0001")
        self.assertEqual(state["pm_approved_by"], PM)
        self.assertEqual(state["requested_by"], REQUESTER)
        self.assertEqual(state["blocking"], [])
        self.assertEqual(self.stored()["pm_approved_by"], PM)
        self.assertIsInstance(self.stored()["pm_approved_on"], datetime.datetime)

    def test_the_first_approval_cannot_come_from_the_second_approver(self):
        self.new_reallocation(protected=True).insert()
        self.as_user(PM)
        br_api.approve_reallocation("BR-0001")
        self.as_user(FINANCE)
        br_api.give_second_approval("BR-0001")
        self.as_user(PM)
        br_api.revoke_approval("BR-0001", "pm")
        self.as_user(FINANCE)
        with self.assertRaises(ValidationError) as raised:
            br_api.approve_reallocation("BR-0001")
        self.assertIn("second approval", str(raised.exception))
        self.assertIsNone(self.stored()["pm_approved_by"])

    def test_submit_refuses_a_first_approval_recorded_by_the_requester(self):
        """A row stamped before this rule, or by an import, still cannot be submitted."""
        doc = self.new_reallocation(requested_by=REQUESTER, pm_approved_by=REQUESTER)
        doc.requires_additional_approval = 0
        with self.assertRaises(ValidationError):
            doc._check_approvals()

    def test_the_pure_rule(self):
        self.assertEqual(budgets.first_approval_errors(REQUESTER, PM), [])
        self.assertEqual(len(budgets.first_approval_errors(REQUESTER, REQUESTER)), 1)
        self.assertEqual(len(budgets.first_approval_errors(REQUESTER, FINANCE, FINANCE)), 1)
        self.assertEqual(budgets.first_approval_errors(REQUESTER, None), [])
        errors = budgets.approval_errors(REQUESTER, REQUESTER, None, False)
        self.assertTrue(any("requested it" in e for e in errors), errors)


class TestSecondApprovalSeparation(_BudgetBase):
    def setUp(self):
        super().setUp()
        self.new_reallocation(protected=True).insert()
        self.as_user(PM)
        br_api.approve_reallocation("BR-0001")

    def test_the_requester_cannot_give_the_second_approval(self):
        self.as_user(REQUESTER)
        with self.assertRaises(ValidationError):
            br_api.give_second_approval("BR-0001")
        self.assertIsNone(self.stored().get("additional_approved_by"))

    def test_the_first_approver_cannot_give_the_second_approval(self):
        self.as_user(PM)
        with self.assertRaises(ValidationError):
            br_api.give_second_approval("BR-0001")
        self.assertIsNone(self.stored().get("additional_approved_by"))

    def test_a_third_person_clears_it(self):
        self.as_user(FINANCE)
        state = br_api.give_second_approval("BR-0001")
        self.assertEqual(state["additional_approved_by"], FINANCE)
        self.assertEqual(state["blocking"], [])


class TestBudgetStampsAreEndpointOnly(_BudgetBase):
    def approved(self):
        self.new_reallocation().insert()
        self.as_user(PM)
        br_api.approve_reallocation("BR-0001")
        self.as_user(REQUESTER)
        return frappe_get("Budget Reallocation", "BR-0001")

    def test_a_save_that_changes_a_stamp_is_refused(self):
        for field, value in (
            ("pm_approved_by", FINANCE),
            ("pm_approved_on", "2026-01-01 08:00:00"),
            ("pm_approved_by", None),
            ("additional_approved_by", FINANCE),
            ("additional_approved_on", "2026-01-01 08:00:00"),
            ("requested_by", FINANCE),
        ):
            with self.subTest(field=field, value=value):
                DB.clear()
                doc = self.approved()
                doc.set(field, value)
                with self.assertRaises(PermissionError_):
                    doc.save()
                self.assertEqual(self.stored()["pm_approved_by"], PM)
                self.assertEqual(self.stored()["requested_by"], REQUESTER)

    def test_an_ordinary_save_that_leaves_the_stamps_alone_is_accepted(self):
        """The browser sends a stored datetime back as text; that is not a change."""
        doc = self.approved()
        doc.pm_approved_on = str(doc.pm_approved_on)
        doc.reason = "edited after approval"
        doc.save()
        self.assertEqual(self.stored()["reason"], "edited after approval")

    def test_a_new_reallocation_with_a_preset_approval_is_refused(self):
        for field, value in (
            ("pm_approved_by", PM),
            ("pm_approved_on", "2026-01-01 08:00:00"),
            ("additional_approved_by", FINANCE),
            ("additional_approved_on", "2026-01-01 08:00:00"),
        ):
            with self.subTest(field=field):
                DB.clear()
                with self.assertRaises(PermissionError_):
                    self.new_reallocation(**{field: value}).insert()
                self.assertNotIn(("Budget Reallocation", "BR-0001"), DB)

    def test_requested_by_is_the_session_user_on_insert(self):
        self.new_reallocation(requested_by=PM).insert()
        self.assertEqual(self.stored()["requested_by"], REQUESTER)

    def test_revoke_clears_through_the_endpoint(self):
        self.approved()
        self.as_user(PM)
        state = br_api.revoke_approval("BR-0001", "pm")
        self.assertIsNone(state["pm_approved_by"])
        self.assertIsNone(self.stored()["pm_approved_on"])

    def test_migrate_and_patches_keep_what_they_write(self):
        for flag in ("in_migrate", "in_install", "in_patch"):
            with self.subTest(flag=flag):
                DB.clear()
                setattr(fake.flags, flag, True)
                try:
                    self.new_reallocation(requested_by=PM, pm_approved_by=FINANCE).insert()
                    self.assertEqual(self.stored()["requested_by"], PM)
                    doc = frappe_get("Budget Reallocation", "BR-0001")
                    doc.pm_approved_by = None
                    doc.save()
                    self.assertIsNone(self.stored()["pm_approved_by"])
                finally:
                    setattr(fake.flags, flag, False)

    def test_a_system_manager_import_keeps_the_stamps(self):
        fake.flags.in_import = True
        fake.roles = ["System Manager"]
        self.new_reallocation(requested_by=PM, pm_approved_by=FINANCE).insert()
        self.assertEqual(self.stored()["pm_approved_by"], FINANCE)
        self.assertEqual(self.stored()["requested_by"], PM)

    def test_an_import_by_anyone_else_is_refused(self):
        fake.flags.in_import = True
        fake.roles = ["Project Manager", "Finance Team"]
        with self.assertRaises(PermissionError_):
            self.new_reallocation(pm_approved_by=FINANCE).insert()


# --------------------------------------------------------------------------------------------
# Change Order
# --------------------------------------------------------------------------------------------


class _ChangeOrderBase(_Base):
    def new_change_order(self, name="PRJ-TEST-CO-001", **values):
        fields = {
            "name": name,
            "project": "PRJ-TEST",
            "co_number": 1,
            "cause": "Customer Request",
            "client_request_description": "Add a light",
            "cost_impact": 1200,
            "added_criteria": [],
            "pm_approved": 0,
            "customer_approved": 0,
            "executed_on": None,
            "notes": None,
        }
        fields.update(values)
        return co.ChangeOrder(**fields)

    def stored(self, name="PRJ-TEST-CO-001"):
        return DB[("Change Order", name)]


class TestChangeOrderStampsAreEndpointOnly(_ChangeOrderBase):
    def approved(self):
        self.new_change_order().insert()
        self.as_user(PM)
        co_api.approve_change_order("PRJ-TEST-CO-001")
        co_api.record_customer_approval("PRJ-TEST-CO-001", "Signed copy by email")
        return frappe_get("Change Order", "PRJ-TEST-CO-001")

    def test_the_endpoints_record_and_revoke(self):
        doc = self.approved()
        self.assertEqual(doc.pm_approved, 1)
        self.assertEqual(doc.pm_approved_by, PM)
        self.assertEqual(doc.customer_approved, 1)
        self.assertEqual(doc.customer_approval_note, "Signed copy by email")
        co_api.revoke_approval("PRJ-TEST-CO-001", "customer")
        self.assertEqual(self.stored()["customer_approved"], 0)
        self.assertIsNone(self.stored()["customer_approval_note"])
        co_api.revoke_approval("PRJ-TEST-CO-001", "pm")
        self.assertEqual(self.stored()["pm_approved"], 0)
        self.assertIsNone(self.stored()["pm_approved_by"])

    def test_a_save_that_changes_a_stamp_is_refused(self):
        for field, value in (
            ("pm_approved", 0),
            ("pm_approved_by", FINANCE),
            ("pm_approved_on", "2026-01-01 08:00:00"),
            ("customer_approved", 0),
            ("customer_approved_on", "2026-01-01 08:00:00"),
            ("customer_approval_note", "something else"),
        ):
            with self.subTest(field=field):
                DB.clear()
                doc = self.approved()
                doc.set(field, value)
                with self.assertRaises(PermissionError_):
                    doc.save()
                self.assertEqual(self.stored()["customer_approval_note"], "Signed copy by email")
                self.assertEqual(self.stored()["pm_approved_by"], PM)

    def test_an_ordinary_save_that_leaves_the_stamps_alone_is_accepted(self):
        doc = self.approved()
        doc.pm_approved = "1"
        doc.pm_approved_on = str(doc.pm_approved_on)
        doc.customer_approved_on = str(doc.customer_approved_on)
        doc.cost_basis = "re-measured"
        doc.save()
        self.assertEqual(self.stored()["cost_basis"], "re-measured")

    def test_a_new_change_order_with_a_preset_approval_is_refused(self):
        for field, value in (
            ("pm_approved", 1),
            ("pm_approved_by", PM),
            ("pm_approved_on", "2026-01-01 08:00:00"),
            ("customer_approved", 1),
            ("customer_approved_on", "2026-01-01 08:00:00"),
            ("customer_approval_note", "verbal"),
        ):
            with self.subTest(field=field):
                DB.clear()
                with self.assertRaises(PermissionError_):
                    self.new_change_order(**{field: value}).insert()
                self.assertNotIn(("Change Order", "PRJ-TEST-CO-001"), DB)

    def test_migrate_patches_and_a_system_manager_import_keep_what_they_write(self):
        for flag in ("in_migrate", "in_patch"):
            with self.subTest(flag=flag):
                DB.clear()
                setattr(fake.flags, flag, True)
                try:
                    self.new_change_order(pm_approved=1, pm_approved_by=PM).insert()
                    self.assertEqual(self.stored()["pm_approved_by"], PM)
                finally:
                    setattr(fake.flags, flag, False)
        DB.clear()
        fake.flags.in_import = True
        fake.roles = ["System Manager"]
        self.new_change_order(customer_approved=1, customer_approval_note="signed").insert()
        self.assertEqual(self.stored()["customer_approval_note"], "signed")
        DB.clear()
        fake.roles = ["Projects Manager"]
        with self.assertRaises(PermissionError_):
            self.new_change_order(customer_approved=1).insert()


class TestEndpointsKeepTheirMethods(unittest.TestCase):
    """Every approval endpoint stays POST-only."""

    def test_methods(self):
        import ast

        for path, names in (
            (BR_API, ("approve_reallocation", "give_second_approval", "revoke_approval")),
            (CO_API, ("approve_change_order", "record_customer_approval", "revoke_approval")),
        ):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found = {}
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name in names:
                    found[node.name] = ast.unparse(node.decorator_list[0])
            for name in names:
                with self.subTest(path=path.name, endpoint=name):
                    self.assertEqual(found.get(name), "frappe.whitelist(methods=['POST'])")


def frappe_get(doctype, name):
    return _get_doc(doctype, name)


if __name__ == "__main__":
    unittest.main()
