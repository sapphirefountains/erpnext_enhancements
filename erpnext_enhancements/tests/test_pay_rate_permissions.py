"""Where a pay rate can be read, and by whom (v1.538.0).

ADR 0013 puts pay rates in ERPNext and grants read of them at permlevel 1 to System Manager,
HR Manager and Accounts Manager "and to nobody else". Frappe's permlevel enforces that where
frappe reads a document out (form loads, list views, ``GET /api/resource``) and on nothing
else: a raw-SQL Script Report, ``doc.as_dict()``, ``frappe.get_doc`` and a whitelisted
function's own ``frappe.db`` read all return the value to whoever reaches them. (So do a form's
version history and a write call's response; ``tests/test_fieldlevel_read.py`` covers those.)
v1.538.0 closed each place the rate, or a number that gives it away, was reachable around the
permlevel. Every fix fails **open** if it drifts, and none of the drift would raise anything:

* **One audience, three copies.** Job Interval's permlevel-1 rows, ``PAY_AUDIENCE_ROLES`` in
  ``api/activity_cost.py`` and the role set in ``payroll_export._may_export`` must stay equal.
* **Labor Cost Analysis.** Its raw SQL bypasses Job Interval's pay block, so its role list is
  the only gate. A Report JSON imports only when its ``modified`` is newer than the site
  row, hence the bumped stamp and the one-shot reload patch.
* **Standard-doctype fields at permlevel 1 by Property Setter**, never by Custom DocPerm: one
  Custom DocPerm row for a doctype replaces that doctype's standard DocPerms wholesale. And a
  fixture record missing a key has that key wiped on every migrate, so each record carries the
  full shape.
* **Employee Pay Rate's own fields.** A child field's permlevel is checked against the
  parent's DocPerms, and a direct query of the child doctype never consults the permlevel of
  the parent's Table field. The Table field alone did not protect the rows.
* **Sapphire Maintenance Record.total_labor_cost**, plus the two readers that hand the whole
  document out through ``as_dict`` / ``get_doc`` and so must strip it first.
* **ERPNext's ``get_activity_cost``**, overridden over HTTP so it zeroes ``costing_rate`` and
  never touches ``billing_rate``.

Bench-free: JSON, ``ast`` and text only; no ``frappe`` import and no stub. The ``ast`` checks
look at Call nodes, never at raw text, because the comments explaining each fix name the same
tokens.

Run: python -m unittest erpnext_enhancements.tests.test_pay_rate_permissions
"""

import ast
import json
import unittest
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
HOOKS = APP / "hooks.py"
JOB_INTERVAL_JSON = APP / "workforce" / "doctype" / "job_interval" / "job_interval.json"
EPR_JSON = APP / "workforce" / "doctype" / "employee_pay_rate" / "employee_pay_rate.json"
PAYROLL_EXPORT_PY = APP / "workforce" / "payroll_export.py"
REPORT_JSON = APP / "workforce" / "report" / "labor_cost_analysis" / "labor_cost_analysis.json"
ACTIVITY_COST_PY = APP / "api" / "activity_cost.py"
MAINTENANCE_VISIT_PY = APP / "api" / "maintenance_visit.py"
VISIT_HISTORY_PY = APP / "assistant_tools" / "maintenance_visit_history.py"
SMR_JSON = (
    APP / "sapphire_maintenance" / "doctype" / "sapphire_maintenance_record" / "sapphire_maintenance_record.json"
)
PATCHES_TXT = APP / "patches.txt"
PATCHES_README = APP / "patches" / "README.md"
RELOAD_PATCH = "reload_labor_cost_analysis_report"
RELOAD_PATCH_PY = APP / "patches" / f"{RELOAD_PATCH}.py"
FIXTURES = APP / "fixtures"

#: ADR 0013 (decisions/adr/0013-pay-rates-live-in-erpnext-at-permlevel-1.md): read at
#: permlevel 1 for these three roles "and to nobody else".
PAY_AUDIENCE = {"System Manager", "HR Manager", "Accounts Manager"}

OVERRIDDEN = "erpnext.projects.doctype.timesheet.timesheet.get_activity_cost"
OVERRIDE = "erpnext_enhancements.api.activity_cost.get_activity_cost"


def _text(path):
    return path.read_text(encoding="utf-8")


def _json(path):
    return json.loads(_text(path))


def _tree(path):
    return ast.parse(_text(path))


def _module_function(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"no module-level def {name}")


def _module_constant(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not a module-level literal")


def _calls_to(node, attr):
    """Every call of ``<something>.attr(...)`` under ``node``, in source order."""
    calls = [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr
    ]
    return sorted(calls, key=lambda n: (n.lineno, n.col_offset))


def _is_whitelist_decorator(decorator):
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "whitelist"
        and isinstance(target.value, ast.Name)
        and target.value.id == "frappe"
    )


def _hooks_assignment(name):
    tree = _tree(HOOKS)
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not in hooks.py")


def _level_one_roles(permissions):
    return {p["role"] for p in permissions if p.get("permlevel") == 1 and p.get("read")}


def _level_zero_readers(permissions):
    return {p["role"] for p in permissions if p.get("read") and not p.get("permlevel")}


class TestPayAudience(unittest.TestCase):
    """The audience is written down three times; the three copies must agree with the ADR."""

    def test_job_interval_permlevel_1_readers_are_the_audience(self):
        self.assertEqual(_level_one_roles(_json(JOB_INTERVAL_JSON)["permissions"]), PAY_AUDIENCE)

    def test_activity_cost_override_names_the_audience(self):
        roles = _module_constant(_tree(ACTIVITY_COST_PY), "PAY_AUDIENCE_ROLES")
        self.assertEqual(set(roles), PAY_AUDIENCE)
        self.assertEqual(len(roles), len(set(roles)), "a duplicated role in PAY_AUDIENCE_ROLES")

    def test_payroll_export_gate_is_the_audience(self):
        gate = _module_function(_tree(PAYROLL_EXPORT_PY), "_may_export")
        sets = [n for n in ast.walk(gate) if isinstance(n, ast.Set)]
        self.assertEqual(len(sets), 1, "_may_export should hold exactly one set literal of roles")
        self.assertEqual(ast.literal_eval(sets[0]), PAY_AUDIENCE)


class TestLaborCostReport(unittest.TestCase):
    """A raw-SQL report is exactly as private as its role list."""

    def test_roles_are_the_pay_audience(self):
        roles = {r["role"] for r in _json(REPORT_JSON)["roles"]}
        self.assertEqual(roles, PAY_AUDIENCE)
        self.assertNotIn(
            "Projects Manager", roles, "Projects Manager reads Job Interval but not its pay block; the SQL bypasses it"
        )

    def test_modified_is_bumped_past_the_site_row(self):
        """A Report JSON is age-gated: a stamp that is not newer than the site row never imports."""
        stamp = datetime.strptime(_json(REPORT_JSON)["modified"], "%Y-%m-%d %H:%M:%S.%f")
        self.assertGreater(stamp, datetime(2026, 9, 17, 12, 0, 0))

    def test_reload_patch_is_registered_post_model_sync(self):
        lines = [line.strip() for line in _text(PATCHES_TXT).splitlines()]
        header = lines.index("[post_model_sync]")
        entry = f"erpnext_enhancements.patches.{RELOAD_PATCH}"
        self.assertIn(entry, lines, f"{RELOAD_PATCH} is not in patches.txt")
        self.assertGreater(lines.index(entry), header, f"{RELOAD_PATCH} must run after model sync")
        self.assertEqual(lines.count(entry), 1)

    def test_reload_patch_exists_and_is_indexed(self):
        self.assertTrue(RELOAD_PATCH_PY.is_file(), f"{RELOAD_PATCH_PY} is missing")
        self.assertIn(f"| `{RELOAD_PATCH}` |", _text(PATCHES_README))

    def test_reload_patch_forces_the_report_inside_a_try(self):
        execute = _module_function(_tree(RELOAD_PATCH_PY), "execute")
        for statement in execute.body:
            self.assertIsInstance(statement, ast.Try, "a patch that raises aborts bench migrate, i.e. the deploy")
        found = False
        for block in (n for n in ast.walk(execute) if isinstance(n, ast.Try)):
            for call in (n for stmt in block.body for n in ast.walk(stmt)):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                    continue
                if call.func.attr != "reload_doc":
                    continue
                args = tuple(a.value for a in call.args if isinstance(a, ast.Constant))
                force = {k.arg: k.value for k in call.keywords}.get("force")
                if (
                    args == ("workforce", "report", "labor_cost_analysis")
                    and isinstance(force, ast.Constant)
                    and force.value is True
                ):
                    found = True
        self.assertTrue(found, "no reload_doc('workforce', 'report', 'labor_cost_analysis', force=True) inside a try")


class TestStandardFieldSetters(unittest.TestCase):
    """ERPNext fields raised to permlevel 1 by Property Setter, never by Custom DocPerm."""

    FIELDS = (
        ("Activity Cost", "costing_rate"),
        ("Timesheet", "total_costing_amount"),
        ("Timesheet", "base_total_costing_amount"),
        ("Timesheet Detail", "base_costing_rate"),
        ("Timesheet Detail", "base_costing_amount"),
    )

    @classmethod
    def setUpClass(cls):
        cls.records = {r["name"]: r for r in _json(FIXTURES / "property_setter.json")}

    def test_each_field_has_a_permlevel_1_setter(self):
        for doctype, fieldname in self.FIELDS:
            name = f"{doctype}-{fieldname}-permlevel"
            with self.subTest(name=name):
                self.assertIn(name, self.records)
                record = self.records[name]
                self.assertEqual(record["doc_type"], doctype)
                self.assertEqual(record["field_name"], fieldname)
                self.assertEqual(record["property"], "permlevel")
                self.assertEqual(record["property_type"], "Int")
                self.assertEqual(record["value"], "1")
                self.assertEqual(record["doctype_or_field"], "DocField")
                self.assertEqual(record["is_system_generated"], 0, "the fixture filter exports only non-system rows")

    def test_each_record_carries_the_full_shape(self):
        """Fixture sync deletes and re-inserts each record; a key it does not name is wiped."""
        reference = set(self.records["Employee-ctc-permlevel"])
        for doctype, fieldname in self.FIELDS:
            name = f"{doctype}-{fieldname}-permlevel"
            with self.subTest(name=name):
                self.assertEqual(set(self.records[name]), reference)

    def test_no_custom_docperm_restates_these_doctypes(self):
        """One Custom DocPerm row for a doctype replaces all of its standard DocPerms."""
        parents = {r["parent"] for r in _json(FIXTURES / "custom_docperm.json")}
        self.assertLessEqual(parents, {"Material Request", "Purchase Order"})
        for doctype in {d for d, _f in self.FIELDS}:
            self.assertNotIn(doctype, parents)


class TestEmployeePayRateFields(unittest.TestCase):
    """The parent's Table field at permlevel 1 does not cover a direct query of the child."""

    def test_every_child_field_is_permlevel_1(self):
        fields = _json(EPR_JSON)["fields"]
        self.assertTrue(fields)
        for field in fields:
            with self.subTest(field=field["fieldname"]):
                self.assertEqual(field.get("permlevel"), 1)

    def test_the_child_table_declares_no_permissions_of_its_own(self):
        self.assertEqual(_json(EPR_JSON).get("permissions") or [], [])

    def test_the_employee_table_field_is_still_permlevel_1(self):
        records = {r["name"]: r for r in _json(FIXTURES / "custom_field.json")}
        self.assertIn("Employee-custom_pay_rates", records)
        self.assertEqual(records["Employee-custom_pay_rates"].get("permlevel"), 1)


class TestMaintenanceRecordLaborCost(unittest.TestCase):
    """total_labor_cost plus the clock times gives away the technician's burdened rate."""

    NEVER = ("Customer", "Maintenance User", "Projects Manager", "Maintenance Supervisor")

    @classmethod
    def setUpClass(cls):
        cls.raw = SMR_JSON.read_bytes()
        cls.doc = json.loads(cls.raw.decode("utf-8"))

    def test_total_labor_cost_is_permlevel_1(self):
        fields = {f["fieldname"]: f for f in self.doc["fields"]}
        self.assertEqual(fields["total_labor_cost"].get("permlevel"), 1)

    def test_permlevel_1_readers_are_inside_the_audience(self):
        level_one = _level_one_roles(self.doc["permissions"])
        self.assertIn("System Manager", level_one)
        self.assertLessEqual(level_one, PAY_AUDIENCE)

    def test_every_permlevel_1_role_can_reach_the_document(self):
        """Frappe derives doc-level access from permlevel-0 rows only."""
        level_zero = _level_zero_readers(self.doc["permissions"])
        for role in {p["role"] for p in self.doc["permissions"] if p.get("permlevel") == 1}:
            self.assertIn(role, level_zero, f"{role} holds a permlevel-1 row but no permlevel-0 read")

    def test_field_staff_and_customers_hold_no_permlevel_1_row(self):
        level_one = {p["role"] for p in self.doc["permissions"] if p.get("permlevel") == 1}
        for role in self.NEVER:
            self.assertNotIn(role, level_one)

    def test_file_keeps_no_trailing_newline(self):
        self.assertFalse(self.raw.endswith(b"\n"), ".editorconfig: insert_final_newline = false for JSON")


class TestFieldLevelStripping(unittest.TestCase):
    """as_dict() and get_doc apply no permlevel, so both whole-document readers strip first."""

    def test_visit_bootstrap_strips_after_saving_and_before_serializing(self):
        fn = _module_function(_tree(MAINTENANCE_VISIT_PY), "get_visit_bootstrap")
        strips = _calls_to(fn, "apply_fieldlevel_read_permissions")
        saves = _calls_to(fn, "save")
        dicts = _calls_to(fn, "as_dict")
        self.assertEqual(len(strips), 1, "get_visit_bootstrap should strip exactly once")
        self.assertTrue(saves and dicts)
        self.assertGreater(strips[0].lineno, saves[-1].lineno, "stripping before the save would write null back")
        self.assertLess(strips[0].lineno, dicts[0].lineno, "the record is serialized before it is stripped")

    def test_assistant_tool_detail_strips_before_building_the_payload(self):
        tree = _tree(VISIT_HISTORY_PY)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MaintenanceVisitHistory")
        detail = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_detail")
        checks = _calls_to(detail, "has_permission")
        strips = _calls_to(detail, "apply_fieldlevel_read_permissions")
        header = [
            n
            for n in ast.walk(detail)
            if isinstance(n, ast.DictComp)
            and isinstance(n.generators[0].iter, ast.Name)
            and n.generators[0].iter.id == "_DETAIL_HEADER_FIELDS"
        ]
        self.assertTrue(checks and header)
        self.assertEqual(len(strips), 1, "_detail should strip exactly once")
        self.assertGreater(strips[0].lineno, checks[0].lineno)
        self.assertLess(strips[0].lineno, header[0].lineno)

    def test_assistant_tool_output_schema_is_unchanged(self):
        """The key stays and reads None without level-1 read, so no consumer loses a field."""
        fields = _module_constant(_tree(VISIT_HISTORY_PY), "_DETAIL_HEADER_FIELDS")
        self.assertIn("total_labor_cost", fields)


class TestActivityCostOverride(unittest.TestCase):
    """ERPNext's get_activity_cost handed any caller any employee's costing_rate."""

    @classmethod
    def setUpClass(cls):
        cls.tree = _tree(ACTIVITY_COST_PY)
        cls.fn = _module_function(cls.tree, "get_activity_cost")

    def test_hooks_route_erpnext_endpoint_to_the_override(self):
        overrides = _hooks_assignment("override_whitelisted_methods")
        self.assertEqual(overrides.get(OVERRIDDEN), OVERRIDE)
        module, _dot, name = OVERRIDE.rpartition(".")
        self.assertEqual(APP.parent / Path(*module.split(".")).with_suffix(".py"), ACTIVITY_COST_PY)
        self.assertEqual(name, self.fn.name)

    def test_override_is_whitelisted_with_erpnexts_signature(self):
        self.assertTrue(any(_is_whitelist_decorator(d) for d in self.fn.decorator_list))
        args = self.fn.args
        self.assertEqual([a.arg for a in args.args], ["employee", "activity_type", "currency"])
        self.assertEqual(len(args.defaults), 3)
        for default in args.defaults:
            self.assertIsInstance(default, ast.Constant)
            self.assertIsNone(default.value)
        self.assertFalse(args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg)

    def test_override_delegates_to_erpnexts_own_function(self):
        imports = [
            n
            for n in ast.walk(self.fn)
            if isinstance(n, ast.ImportFrom)
            and n.module == "erpnext.projects.doctype.timesheet.timesheet"
            and any(alias.name == "get_activity_cost" for alias in n.names)
        ]
        self.assertTrue(imports, "the override must call ERPNext's function, not reimplement it")

    def test_override_zeroes_costing_rate(self):
        zeroed = [
            n
            for n in ast.walk(self.fn)
            if isinstance(n, ast.keyword)
            and n.arg == "costing_rate"
            and isinstance(n.value, ast.Constant)
            and n.value.value == 0
        ]
        self.assertTrue(zeroed, "costing_rate is zeroed (kept as a key, so the form's arithmetic stays numeric)")

    def test_override_never_assigns_billing_rate(self):
        """ADR 0013: billing rates are left alone, so T&M billing stays configurable natively."""
        for node in ast.walk(self.fn):
            if isinstance(node, ast.keyword):
                self.assertNotEqual(node.arg, "billing_rate")
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
                key = node.slice
                self.assertFalse(isinstance(key, ast.Constant) and key.value == "billing_rate")
            if isinstance(node, ast.Dict):
                keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
                self.assertNotIn("billing_rate", keys)

    def test_the_gate_helper_is_not_whitelisted(self):
        helper = _module_function(self.tree, "_may_read_costing_rate")
        self.assertFalse(any(_is_whitelist_decorator(d) for d in helper.decorator_list))


if __name__ == "__main__":
    unittest.main()
