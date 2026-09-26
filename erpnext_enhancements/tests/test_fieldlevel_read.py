"""The responses frappe v16 sends without field-level read permissions (v1.542.0).

Frappe strips a caller's unreadable permlevels when it reads a document out, and skips that
step in two places, both of which carried pay (ADR 0013) past its permlevel:

* **The form's docinfo.** ``getdoc`` (and ``get_docinfo``, ``savedocs``, ``cancel``,
  ``discard``) attach ``versions``: the stored Version diffs, verbatim. Employee and Job
  Interval track changes, so pay-rate rows and every stamped ``labor_cost`` rode along to
  every reader of the record, hidden only by the browser's rendering.
* **Write responses.** ``frappe.client.set_value`` / ``insert`` / ``save`` / ``submit`` /
  ``cancel`` and ``apply_workflow`` return the saved document unstripped, and so do
  ``POST``/``PUT /api/resource`` and ``POST /api/v2/document``, which are routes rather than
  whitelisted methods and are handled by an ``after_request`` hook.

Every wrapper fails **open** if its wiring drifts, silently: a hook key misspelt, a wrapper
whose parameter list no longer matches frappe's (``frappe.call`` drops the argument), or one
whose HTTP methods are wider than frappe's (a ``GET`` savedocs skips the CSRF check). So the
wiring is pinned by ``ast`` against the v16 signatures, read with ``git show
origin/version-16:...``, and the scrub itself is run against the Employee, Job Interval and
Timesheet shapes through a ``frappe`` stub.

Bench-free: installs a ``frappe`` stub in ``setUpModule``, so it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_fieldlevel_read
"""

import ast
import json
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
HOOKS = APP / "hooks.py"
MODULE_PY = APP / "fieldlevel_read.py"
MODULE = "erpnext_enhancements.fieldlevel_read"

#: frappe v16's own parameter lists and HTTP methods (None = frappe.whitelist()'s default of
#: GET, POST, PUT, DELETE), read from origin/version-16: frappe/desk/form/load.py,
#: frappe/desk/form/save.py, frappe/client.py and frappe/model/workflow.py.
FRAPPE_V16 = {
    "frappe.desk.form.load.getdoc": ("getdoc", ["doctype", "name"], None),
    "frappe.desk.form.load.get_docinfo": ("get_docinfo", ["doc", "doctype", "name"], None),
    "frappe.desk.form.save.savedocs": ("savedocs", ["doc", "action"], ["POST", "PUT"]),
    "frappe.desk.form.save.cancel": (
        "desk_cancel",
        ["doctype", "name", "workflow_state_fieldname", "workflow_state"],
        ["POST", "PUT"],
    ),
    "frappe.desk.form.save.discard": ("desk_discard", ["doctype", "name"], ["POST", "PUT"]),
    "frappe.client.set_value": (
        "client_set_value",
        ["doctype", "name", "fieldname", "value"],
        ["POST", "PUT"],
    ),
    "frappe.client.insert": ("client_insert", ["doc"], ["POST", "PUT"]),
    "frappe.client.save": ("client_save", ["doc"], ["POST", "PUT"]),
    "frappe.client.submit": ("client_submit", ["doc"], ["POST", "PUT"]),
    "frappe.client.cancel": ("client_cancel", ["doctype", "name"], ["POST", "PUT"]),
    "frappe.model.workflow.apply_workflow": ("apply_workflow", ["doc", "action"], None),
}

#: frappe's defaults, positionally from the right, as the wrapper must restate them.
FRAPPE_V16_DEFAULTS = {
    "get_docinfo": 3,
    "desk_cancel": 4,
    "client_set_value": 1,
    "client_insert": 1,
}

AFTER_REQUEST = f"{MODULE}.scrub_rest_write_response"
BEFORE_REQUEST = f"{MODULE}.seal_wrapped_originals"

fieldlevel_read = None
STATE = {}
_SAVED_MODULES = {}


class _Row(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


def _df(fieldname, permlevel=0, fieldtype="Data", options=None):
    return _Row(fieldname=fieldname, permlevel=permlevel, fieldtype=fieldtype, options=options)


def _perm(role, permlevel=0, read=1):
    return _Row(role=role, permlevel=permlevel, read=read)


class _Meta:
    def __init__(self, name, fields, permissions=(), istable=False):
        self.name = name
        self.fields = list(fields)
        self.permissions = list(permissions)
        self.istable = istable


#: Employee: ctc and the pay-rate table at permlevel 1 (as on the site). HR User reads level 0
#: only; HR Manager reads both. Employee Pay Rate is a child table with no DocPerms of its own.
#: Job Interval: the pay block at level 1, Projects Manager at level 0 only. Timesheet: the
#: time_logs table itself at level 0, with level-1 cost fields inside it.
METAS = {
    "Employee": _Meta(
        "Employee",
        [
            _df("employee_name"),
            _df("ctc", 1, "Currency"),
            _df("custom_pay_rates", 1, "Table", "Employee Pay Rate"),
        ],
        [_perm("HR User"), _perm("HR Manager"), _perm("HR Manager", 1), _perm("Employee")],
    ),
    "Employee Pay Rate": _Meta(
        "Employee Pay Rate",
        [
            _df("effective_from", 1, "Date"),
            _df("hourly_rate", 1, "Currency"),
            _df("burden_pct", 1, "Percent"),
        ],
        istable=True,
    ),
    "Job Interval": _Meta(
        "Job Interval",
        [
            _df("employee"),
            _df("end_time", 0, "Datetime"),
            _df("labor_cost", 1, "Currency"),
            _df("burdened_rate", 1, "Currency"),
        ],
        [_perm("Projects Manager"), _perm("HR Manager"), _perm("HR Manager", 1)],
    ),
    "Timesheet": _Meta(
        "Timesheet",
        [
            _df("note"),
            _df("total_costing_amount", 1, "Currency"),
            _df("time_logs", 0, "Table", "Timesheet Detail"),
        ],
        [_perm("Projects User"), _perm("Accounts Manager"), _perm("Accounts Manager", 1)],
    ),
    "Timesheet Detail": _Meta(
        "Timesheet Detail",
        [_df("hours", 0, "Float"), _df("base_costing_rate", 1, "Currency")],
        istable=True,
    ),
    "ToDo": _Meta("ToDo", [_df("description")], [_perm("All")]),
}


def _whitelist(allow_guest=False, xss_safe=False, methods=None):
    def decorate(fn):
        STATE.setdefault("whitelisted", {})[fn.__name__] = methods
        return fn

    return decorate


def _get_meta(doctype):
    STATE["meta_calls"].append(doctype)
    if doctype not in METAS:
        raise KeyError(f"DoesNotExistError: DocType {doctype}")
    return METAS[doctype]


def _log_error(title=None, message=None, **kw):
    STATE["errors"].append(title)
    STATE["deferred"].append(kw.get("defer_insert"))


def _get_attr(method_string):
    if STATE.get("get_attr_raises"):
        raise ImportError(method_string)
    module, _dot, attr = method_string.rpartition(".")
    return getattr(sys.modules[module], attr)


def _override_whitelisted_method(original):
    return STATE["overrides"].get(original, original)


def _install_frappe_stub():
    for name in list(sys.modules):
        if name == "frappe" or name.startswith("frappe.") or name == MODULE:
            _SAVED_MODULES[name] = sys.modules.pop(name)

    frappe = types.ModuleType("frappe")
    frappe.__path__ = []
    frappe.whitelist = _whitelist
    frappe.session = _Row(user="hr.user@example.com")
    frappe.response = _Row()
    frappe.get_meta = _get_meta
    frappe.get_roles = lambda user=None: list(STATE["roles"])
    frappe.log_error = _log_error
    frappe.get_traceback = lambda with_context=False: "traceback"
    frappe.whitelisted = set()
    frappe.get_attr = _get_attr
    frappe.override_whitelisted_method = _override_whitelisted_method

    def _record(name, value=None):
        def fn(*args, **kwargs):
            STATE["calls"].append((name, args, kwargs))
            return value() if callable(value) else value

        return fn

    def _with_docinfo(name):
        def fn(*args, **kwargs):
            STATE["calls"].append((name, args, kwargs))
            frappe.response["docinfo"] = STATE["docinfo"]
            frappe.response.setdefault("docs", []).append({"doctype": STATE["docinfo"].get("doctype")})

        return fn

    desk = types.ModuleType("frappe.desk")
    desk.__path__ = []
    form = types.ModuleType("frappe.desk.form")
    form.__path__ = []
    load = types.ModuleType("frappe.desk.form.load")
    load.getdoc = _with_docinfo("load.getdoc")
    load.get_docinfo = _with_docinfo("load.get_docinfo")
    save = types.ModuleType("frappe.desk.form.save")
    save.savedocs = _with_docinfo("save.savedocs")
    save.cancel = _with_docinfo("save.cancel")
    save.discard = _with_docinfo("save.discard")
    form.load, form.save = load, save
    desk.form = form

    client = types.ModuleType("frappe.client")
    for name in ("set_value", "insert", "save", "submit", "cancel"):
        setattr(client, name, _record(f"client.{name}", lambda: json.loads(json.dumps(STATE["returned"]))))

    model = types.ModuleType("frappe.model")
    model.__path__ = []
    workflow = types.ModuleType("frappe.model.workflow")
    workflow.apply_workflow = _record("workflow.apply_workflow", lambda: STATE["workflow_doc"])
    model.workflow = workflow

    frappe.desk, frappe.client, frappe.model = desk, client, model
    sys.modules.update(
        {
            "frappe": frappe,
            "frappe.desk": desk,
            "frappe.desk.form": form,
            "frappe.desk.form.load": load,
            "frappe.desk.form.save": save,
            "frappe.client": client,
            "frappe.model": model,
            "frappe.model.workflow": workflow,
        }
    )
    return frappe


def setUpModule():
    global fieldlevel_read
    _install_frappe_stub()
    _reset()
    import importlib

    fieldlevel_read = importlib.import_module(MODULE)


def tearDownModule():
    for name in list(sys.modules):
        if name == "frappe" or name.startswith("frappe.") or name == MODULE:
            del sys.modules[name]
    sys.modules.update(_SAVED_MODULES)


def _reset(user="hr.user@example.com", roles=("HR User", "Employee", "All")):
    frappe = sys.modules["frappe"]
    frappe.session = _Row(user=user)
    frappe.response = _Row()
    STATE.update(
        roles=list(roles),
        calls=[],
        errors=[],
        deferred=[],
        meta_calls=[],
        docinfo={},
        returned={},
        workflow_doc=None,
        overrides={},
        get_attr_raises=False,
    )


def _version(data):
    return {"name": "V-1", "owner": "someone@example.com", "creation": "2026-09-25", "data": json.dumps(data)}


EMPLOYEE_DIFF = {
    "changed": [
        ["employee_name", "Ann", "Anne"],
        ["ctc", 50000, 60000],
        ["docstatus", 0, 1],
        ["dropped_field", 1, 2],
    ],
    "added": [
        ["custom_pay_rates", {"name": "r2", "effective_from": "2026-10-01", "hourly_rate": 31.5, "idx": 2}]
    ],
    "removed": [["custom_pay_rates", {"name": "r0", "hourly_rate": 22.0}]],
    "row_changed": [["custom_pay_rates", 0, "r1", [["hourly_rate", 30.0, 31.5], ["burden_pct", 20, 22]]]],
    "data_import": None,
    "updater_reference": None,
}


class TestWiring(unittest.TestCase):
    """Each override is keyed on frappe's dotted path and restates its parameters and methods."""

    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(MODULE_PY.read_text(encoding="utf-8"))
        cls.functions = {n.name: n for n in cls.tree.body if isinstance(n, ast.FunctionDef)}
        hooks = ast.parse(HOOKS.read_text(encoding="utf-8"))
        cls.hooks = {}
        for node in hooks.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                try:
                    cls.hooks[node.targets[0].id] = ast.literal_eval(node.value)
                except Exception:  # not a literal (a call, a comprehension): not a hook we read
                    continue

    def _whitelist_methods(self, fn):
        for decorator in fn.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(target, ast.Attribute) and target.attr == "whitelist":
                keywords = {k.arg: k.value for k in getattr(decorator, "keywords", [])}
                self.assertNotIn(
                    "allow_guest", keywords, f"{fn.name}: frappe's original is not guest-callable"
                )
                return ast.literal_eval(keywords["methods"]) if "methods" in keywords else None
        self.fail(f"{fn.name} is not whitelisted")

    def test_every_route_is_overridden_by_this_module(self):
        overrides = self.hooks["override_whitelisted_methods"]
        for original, (wrapper, _params, _methods) in FRAPPE_V16.items():
            with self.subTest(original=original):
                self.assertEqual(overrides.get(original), f"{MODULE}.{wrapper}")
                self.assertIn(wrapper, self.functions)

    def test_parameters_mirror_frappe_v16(self):
        """frappe.call matches the request against the wrapper's signature: a missing name is dropped."""
        for _original, (wrapper, params, _methods) in FRAPPE_V16.items():
            with self.subTest(wrapper=wrapper):
                args = self.functions[wrapper].args
                self.assertEqual([a.arg for a in args.args], params)
                self.assertEqual(len(args.defaults), FRAPPE_V16_DEFAULTS.get(wrapper, 0))
                for default in args.defaults:
                    self.assertIsNone(ast.literal_eval(default))
                self.assertFalse(args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg)

    def test_http_methods_mirror_frappe_v16(self):
        """A wider list would, e.g., let a GET savedocs skip the CSRF check."""
        for _original, (wrapper, _params, methods) in FRAPPE_V16.items():
            with self.subTest(wrapper=wrapper):
                self.assertEqual(self._whitelist_methods(self.functions[wrapper]), methods)

    def test_each_wrapper_delegates_to_frappes_own_module(self):
        expected = {
            "frappe.desk.form.load": ("frappe.desk.form", "load"),
            "frappe.desk.form.save": ("frappe.desk.form", "save"),
            "frappe.client": ("frappe", "client"),
            "frappe.model.workflow": ("frappe.model", "workflow"),
        }
        for original, (wrapper, _params, _methods) in FRAPPE_V16.items():
            module_path, _dot, attr = original.rpartition(".")
            package, name = expected[module_path]
            with self.subTest(wrapper=wrapper):
                fn = self.functions[wrapper]
                imports = [
                    n
                    for n in ast.walk(fn)
                    if isinstance(n, ast.ImportFrom)
                    and n.module == package
                    and any(a.name == name for a in n.names)
                ]
                self.assertTrue(imports, f"{wrapper} must call frappe's {original}, not reimplement it")
                calls = [
                    n
                    for n in ast.walk(fn)
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == attr
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == name
                ]
                self.assertEqual(len(calls), 1, f"{wrapper} should call {name}.{attr} exactly once")

    def test_after_request_hook_is_registered_and_not_whitelisted(self):
        self.assertIn(AFTER_REQUEST, self.hooks["after_request"])
        fn = self.functions["scrub_rest_write_response"]
        self.assertEqual([a.arg for a in fn.args.args], ["response", "request"])
        for decorator in fn.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            self.assertFalse(isinstance(target, ast.Attribute) and target.attr == "whitelist")

    def test_before_request_seal_is_registered_and_not_whitelisted(self):
        self.assertIn(BEFORE_REQUEST, self.hooks["before_request"])
        fn = self.functions["seal_wrapped_originals"]
        self.assertFalse(fn.args.args or fn.args.vararg or fn.args.kwarg)

    def test_the_seal_covers_exactly_the_overridden_routes(self):
        """A route overridden but not sealed stays reachable under its aliases, unscrubbed."""
        self.assertEqual(set(fieldlevel_read.WRAPPED_ORIGINALS), set(FRAPPE_V16))
        self.assertEqual(len(fieldlevel_read.WRAPPED_ORIGINALS), len(FRAPPE_V16))

    def test_no_helper_is_whitelisted(self):
        wrappers = {w for w, _p, _m in FRAPPE_V16.values()}
        for name, fn in self.functions.items():
            if name in wrappers:
                continue
            for decorator in fn.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                with self.subTest(function=name):
                    self.assertFalse(isinstance(target, ast.Attribute) and target.attr == "whitelist")

    def test_stub_decorator_saw_the_same_methods(self):
        """The ast reading above, cross-checked against the decorator as Python applied it."""
        for _original, (wrapper, _params, methods) in FRAPPE_V16.items():
            with self.subTest(wrapper=wrapper):
                self.assertEqual(STATE["whitelisted"][wrapper], methods)


class TestVersionScrub(unittest.TestCase):
    def setUp(self):
        _reset()

    def _scrub(self, doctype, diff):
        versions = [_version(diff)]
        fieldlevel_read.scrub_versions(doctype, versions)
        return json.loads(versions[0]["data"])

    def test_hr_user_loses_the_pay_and_keeps_the_rest(self):
        data = self._scrub("Employee", EMPLOYEE_DIFF)
        self.assertEqual(data["changed"], [["employee_name", "Ann", "Anne"], ["docstatus", 0, 1]])
        self.assertEqual(data["added"], [])
        self.assertEqual(data["removed"], [])
        self.assertEqual(data["row_changed"], [])
        self.assertIn("data_import", data)
        self.assertIn("updater_reference", data)

    def test_the_pay_audience_gets_the_diff_unchanged(self):
        _reset(user="hr.manager@example.com", roles=("HR Manager", "All"))
        versions = [_version(EMPLOYEE_DIFF)]
        raw = versions[0]["data"]
        fieldlevel_read.scrub_versions("Employee", versions)
        self.assertIs(versions[0]["data"], raw)

    def test_administrator_is_never_scrubbed(self):
        _reset(user="Administrator", roles=())
        versions = [_version(EMPLOYEE_DIFF)]
        raw = versions[0]["data"]
        fieldlevel_read.scrub_versions("Employee", versions)
        self.assertIs(versions[0]["data"], raw)
        self.assertEqual(STATE["meta_calls"], [])

    def test_projects_manager_loses_labor_cost_on_job_interval(self):
        _reset(user="pm@example.com", roles=("Projects Manager", "All"))
        diff = {
            "changed": [
                ["end_time", None, "2026-09-25 17:00:00"],
                ["labor_cost", None, 412.5],
                ["burdened_rate", None, 55.0],
            ]
        }
        data = self._scrub("Job Interval", diff)
        self.assertEqual(data["changed"], [["end_time", None, "2026-09-25 17:00:00"]])

    def test_a_level_zero_table_keeps_its_rows_but_not_their_level_one_fields(self):
        _reset(user="pu@example.com", roles=("Projects User", "All"))
        diff = {
            "changed": [["total_costing_amount", 100, 150], ["note", "", "x"]],
            "added": [["time_logs", {"name": "t2", "hours": 2, "base_costing_rate": 55.0}]],
            "row_changed": [
                ["time_logs", 0, "t1", [["hours", 1, 2], ["base_costing_rate", 50, 55]]],
                ["time_logs", 1, "t3", [["base_costing_rate", 50, 55]]],
            ],
        }
        data = self._scrub("Timesheet", diff)
        self.assertEqual(data["changed"], [["note", "", "x"]])
        self.assertEqual(data["added"], [["time_logs", {"name": "t2", "hours": 2}]])
        self.assertEqual(data["row_changed"], [["time_logs", 0, "t1", [["hours", 1, 2]]]])

    def test_a_diff_that_is_not_an_object_is_sent_empty(self):
        versions = [{"name": "V-2", "data": "[1, 2]"}]
        fieldlevel_read.scrub_versions("Employee", versions)
        self.assertEqual(json.loads(versions[0]["data"]), {})

    def test_a_doctype_with_nothing_above_level_zero_is_left_alone(self):
        versions = [_version({"changed": [["description", "a", "b"]]})]
        raw = versions[0]["data"]
        fieldlevel_read.scrub_versions("ToDo", versions)
        self.assertIs(versions[0]["data"], raw)


class TestDocinfoWrappers(unittest.TestCase):
    ROUTES = (
        ("getdoc", ("Employee", "EMP-1"), {}),
        ("get_docinfo", (), {"doctype": "Employee", "name": "EMP-1"}),
        ("savedocs", ('{"doctype": "Employee"}', "Save"), {}),
        ("desk_cancel", (), {"doctype": "Employee", "name": "EMP-1"}),
        ("desk_discard", ("Employee", "EMP-1"), {}),
    )

    def setUp(self):
        _reset()

    def test_every_docinfo_route_sends_scrubbed_versions(self):
        for wrapper, args, kwargs in self.ROUTES:
            with self.subTest(wrapper=wrapper):
                _reset()
                STATE["docinfo"] = {
                    "doctype": "Employee",
                    "name": "EMP-1",
                    "versions": [_version(EMPLOYEE_DIFF)],
                }
                getattr(fieldlevel_read, wrapper)(*args, **kwargs)
                self.assertEqual(len(STATE["calls"]), 1, "frappe's own function runs exactly once")
                sent = sys.modules["frappe"].response["docinfo"]["versions"]
                data = json.loads(sent[0]["data"])
                self.assertNotIn("ctc", [c[0] for c in data["changed"]])
                self.assertEqual(data["added"], [])

    def test_the_arguments_reach_frappe_unchanged(self):
        STATE["docinfo"] = {"doctype": "Employee", "versions": []}
        fieldlevel_read.desk_cancel(
            doctype="Employee", name="EMP-1", workflow_state_fieldname="ws", workflow_state="X"
        )
        _name, args, kwargs = STATE["calls"][0]
        self.assertEqual(
            (args, kwargs),
            (
                (),
                {
                    "doctype": "Employee",
                    "name": "EMP-1",
                    "workflow_state_fieldname": "ws",
                    "workflow_state": "X",
                },
            ),
        )

    def test_an_unscrubbable_history_is_sent_empty_and_logged(self):
        STATE["docinfo"] = {"doctype": "No Such Doctype", "versions": [_version(EMPLOYEE_DIFF)]}
        fieldlevel_read.getdoc("No Such Doctype", "X")
        self.assertEqual(sys.modules["frappe"].response["docinfo"]["versions"], [])
        self.assertEqual(len(STATE["errors"]), 1)
        # A form load is a GET, rolled back at the end: only a deferred insert survives it.
        self.assertEqual(STATE["deferred"], [True])

    def test_a_missing_docinfo_is_not_an_error(self):
        fieldlevel_read._scrub_docinfo()
        self.assertEqual(STATE["errors"], [])


class TestWriteResponses(unittest.TestCase):
    EMPLOYEE = {
        "doctype": "Employee",
        "name": "EMP-1",
        "employee_name": "Anne",
        "ctc": 60000,
        "custom_pay_rates": [{"doctype": "Employee Pay Rate", "name": "r1", "hourly_rate": 31.5, "idx": 1}],
    }

    def setUp(self):
        _reset()

    def test_client_wrappers_strip_level_one_fields(self):
        calls = (
            ("client_set_value", ("Employee", "EMP-1", "employee_name", "Anne")),
            ("client_insert", ("{}",)),
            ("client_save", ("{}",)),
            ("client_submit", ("{}",)),
            ("client_cancel", ("Employee", "EMP-1")),
        )
        for wrapper, args in calls:
            with self.subTest(wrapper=wrapper):
                _reset()
                STATE["returned"] = self.EMPLOYEE
                result = getattr(fieldlevel_read, wrapper)(*args)
                self.assertEqual(result, {"doctype": "Employee", "name": "EMP-1", "employee_name": "Anne"})

    def test_a_level_zero_table_keeps_rows_without_their_level_one_fields(self):
        _reset(user="pu@example.com", roles=("Projects User", "All"))
        STATE["returned"] = {
            "doctype": "Timesheet",
            "name": "TS-1",
            "total_costing_amount": 150,
            "time_logs": [{"doctype": "Timesheet Detail", "hours": 2, "base_costing_rate": 55.0}],
        }
        result = fieldlevel_read.client_save("{}")
        self.assertEqual(
            result,
            {
                "doctype": "Timesheet",
                "name": "TS-1",
                "time_logs": [{"doctype": "Timesheet Detail", "hours": 2}],
            },
        )

    def test_a_child_row_answers_to_its_parent(self):
        row = {"doctype": "Employee Pay Rate", "parenttype": "Employee", "name": "r1", "hourly_rate": 31.5}
        self.assertEqual(
            fieldlevel_read.scrub_doc_dict(dict(row)), {k: v for k, v in row.items() if k != "hourly_rate"}
        )
        _reset(user="hr.manager@example.com", roles=("HR Manager",))
        self.assertEqual(fieldlevel_read.scrub_doc_dict(dict(row)), row)

    def test_a_child_row_with_no_parent_keeps_only_level_zero(self):
        _reset(user="hr.manager@example.com", roles=("HR Manager",))
        row = {"doctype": "Employee Pay Rate", "name": "r1", "hourly_rate": 31.5}
        self.assertEqual(fieldlevel_read.scrub_doc_dict(row), {"doctype": "Employee Pay Rate", "name": "r1"})

    def test_the_audience_and_administrator_get_everything(self):
        for user, roles in (("hr.manager@example.com", ("HR Manager",)), ("Administrator", ())):
            with self.subTest(user=user):
                _reset(user=user, roles=roles)
                STATE["returned"] = self.EMPLOYEE
                self.assertEqual(fieldlevel_read.client_save("{}"), self.EMPLOYEE)

    def test_an_unscrubbable_response_is_cut_to_doctype_and_name(self):
        STATE["returned"] = {"doctype": "No Such Doctype", "name": "X-1", "secret_rate": 9}
        self.assertEqual(fieldlevel_read.client_save("{}"), {"doctype": "No Such Doctype", "name": "X-1"})
        self.assertEqual(len(STATE["errors"]), 1)

    def test_apply_workflow_returns_a_scrubbed_copy_and_leaves_the_document_alone(self):
        class _Doc:
            def __init__(self, values):
                self.values = values
                self.as_dict_calls = []

            def as_dict(self, no_nulls=False):
                self.as_dict_calls.append(no_nulls)
                return json.loads(json.dumps(self.values))

        doc = _Doc(self.EMPLOYEE)
        STATE["workflow_doc"] = doc
        result = fieldlevel_read.apply_workflow("{}", "Approve")
        self.assertEqual(result, {"doctype": "Employee", "name": "EMP-1", "employee_name": "Anne"})
        self.assertEqual(doc.as_dict_calls, [True], "the same as_dict(no_nulls=True) frappe's __json__ sends")
        self.assertEqual(doc.values, self.EMPLOYEE)

    def test_a_queued_workflow_submission_passes_through(self):
        STATE["workflow_doc"] = None
        self.assertIsNone(fieldlevel_read.apply_workflow("{}", "Submit"))


class _Request:
    def __init__(self, method, path):
        self.method = method
        self.path = path


class _Response:
    def __init__(self, body, status_code=200, mimetype="application/json"):
        self._data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status_code = status_code
        self.mimetype = mimetype
        self.is_streamed = False
        self.set_data_calls = 0

    def get_data(self):
        return self._data

    def set_data(self, value):
        self.set_data_calls += 1
        self._data = value.encode() if isinstance(value, str) else value

    def json(self):
        return json.loads(self._data)


class TestRestWriteResponses(unittest.TestCase):
    BODY = {"data": dict(TestWriteResponses.EMPLOYEE)}

    def setUp(self):
        _reset()

    def _run(self, method, path, body=None, **response_kwargs):
        response = _Response(
            body if body is not None else json.loads(json.dumps(self.BODY)), **response_kwargs
        )
        fieldlevel_read.scrub_rest_write_response(response=response, request=_Request(method, path))
        return response

    def test_rest_writes_are_scrubbed(self):
        for method, path in (
            ("PUT", "/api/resource/Employee/EMP-1"),
            ("POST", "/api/resource/Employee"),
            ("PUT", "/api/v1/resource/Employee/EMP-1"),
            ("POST", "/api/v2/document/Employee"),
            ("PUT", "/api/v2/document/Employee/EMP-1"),
        ):
            with self.subTest(method=method, path=path):
                response = self._run(method, path)
                self.assertEqual(
                    response.json(),
                    {"data": {"doctype": "Employee", "name": "EMP-1", "employee_name": "Anne"}},
                )

    def test_everything_else_is_left_byte_for_byte(self):
        for method, path, kwargs in (
            ("GET", "/api/resource/Employee/EMP-1", {}),
            ("POST", "/api/method/frappe.client.save", {}),
            ("POST", "/app/employee", {}),
            ("PUT", "/api/resource/Employee/EMP-1", {"status_code": 417}),
            ("PUT", "/api/resource/Employee/EMP-1", {"mimetype": "text/html"}),
        ):
            with self.subTest(method=method, path=path, **kwargs):
                response = self._run(method, path, **kwargs)
                self.assertEqual(response.set_data_calls, 0)

    def test_nothing_is_rewritten_when_nothing_is_hidden(self):
        _reset(user="hr.manager@example.com", roles=("HR Manager",))
        self.assertEqual(self._run("PUT", "/api/resource/Employee/EMP-1").set_data_calls, 0)

    def test_bodies_that_are_not_a_document_are_left_alone(self):
        for body in (b"not json", {"data": "ok"}, {"data": {"name": "x"}}, [1, 2]):
            with self.subTest(body=body):
                self.assertEqual(self._run("POST", "/api/resource/Employee", body=body).set_data_calls, 0)

    def test_an_unscrubbable_document_is_cut_to_doctype_and_name(self):
        body = {"data": {"doctype": "No Such Doctype", "name": "X-1", "secret_rate": 9}}
        response = self._run("PUT", "/api/resource/No Such Doctype/X-1", body=body)
        self.assertEqual(response.json(), {"data": {"doctype": "No Such Doctype", "name": "X-1"}})
        self.assertEqual(len(STATE["errors"]), 1)

    def test_missing_request_or_response_is_a_no_op(self):
        fieldlevel_read.scrub_rest_write_response(response=None, request=None)
        fieldlevel_read.scrub_rest_write_response(response=_Response(self.BODY), request=None)


class TestSeal(unittest.TestCase):
    """frappe's originals come off its whitelist, so no alias of them answers HTTP."""

    def setUp(self):
        _reset()
        frappe = sys.modules["frappe"]
        self.originals = [_get_attr(path) for path in FRAPPE_V16]
        frappe.whitelisted.clear()
        frappe.whitelisted.update(self.originals)
        fieldlevel_read._SEALED.clear()
        fieldlevel_read._seal_failure_logged = False
        STATE["overrides"] = {path: f"{MODULE}.{wrapper}" for path, (wrapper, _p, _m) in FRAPPE_V16.items()}

    def test_overridden_originals_leave_the_whitelist(self):
        fieldlevel_read.seal_wrapped_originals()
        whitelisted = sys.modules["frappe"].whitelisted
        for path, fn in zip(FRAPPE_V16, self.originals, strict=True):
            with self.subTest(original=path):
                self.assertNotIn(fn, whitelisted)

    def test_an_alias_is_the_same_object_so_it_is_sealed_too(self):
        """frappe.email.inbox does `from frappe.client import set_value`: same function object."""
        inbox = types.ModuleType("frappe.email.inbox")
        inbox.set_value = sys.modules["frappe.client"].set_value
        fieldlevel_read.seal_wrapped_originals()
        self.assertNotIn(inbox.set_value, sys.modules["frappe"].whitelisted)

    def test_a_route_this_site_does_not_override_keeps_its_original(self):
        """Sealing an original whose canonical name is not overridden would 403 every call to it."""
        del STATE["overrides"]["frappe.desk.form.load.getdoc"]
        fieldlevel_read.seal_wrapped_originals()
        whitelisted = sys.modules["frappe"].whitelisted
        self.assertIn(_get_attr("frappe.desk.form.load.getdoc"), whitelisted)
        self.assertNotIn(_get_attr("frappe.client.save"), whitelisted)

    def test_an_original_goes_back_on_once_no_longer_overridden(self):
        """One process can serve several sites; only a site that overrides may keep it sealed."""
        fieldlevel_read.seal_wrapped_originals()
        STATE["overrides"] = {}
        fieldlevel_read.seal_wrapped_originals()
        whitelisted = sys.modules["frappe"].whitelisted
        for fn in self.originals:
            self.assertIn(fn, whitelisted)
        self.assertEqual(fieldlevel_read._SEALED, set())

    def test_it_never_touches_a_function_it_did_not_seal(self):
        """An original frappe never whitelisted is not put on the whitelist by this."""
        sys.modules["frappe"].whitelisted.discard(self.originals[0])
        STATE["overrides"] = {}
        fieldlevel_read.seal_wrapped_originals()
        self.assertNotIn(self.originals[0], sys.modules["frappe"].whitelisted)

    def test_it_is_idempotent(self):
        fieldlevel_read.seal_wrapped_originals()
        fieldlevel_read.seal_wrapped_originals()
        self.assertEqual(len(fieldlevel_read._SEALED), len(FRAPPE_V16))
        self.assertEqual(STATE["errors"], [])

    def test_a_failure_never_raises_and_is_logged_once_per_process(self):
        """A before_request hook that raises fails every request on the site."""
        STATE["get_attr_raises"] = True
        fieldlevel_read.seal_wrapped_originals()
        fieldlevel_read.seal_wrapped_originals()
        self.assertEqual(len(STATE["errors"]), 1)
        self.assertEqual(STATE["deferred"], [True])
        for fn in self.originals:
            self.assertIn(fn, sys.modules["frappe"].whitelisted)


if __name__ == "__main__":
    unittest.main()
