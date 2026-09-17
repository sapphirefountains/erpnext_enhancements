"""Static guards for the v1.480.0 kiosk overhaul's wiring: correction requests, the
Job Interval schema, permissions and hooks.

Guards the class of bug that fails silently: a Select option with no rule behind it
(a request type nobody can file correctly), an endpoint the kiosk dials that was
never whitelisted (a 403 in the field), the three places that name the reviewer
roles drifting apart (a button drawn for someone the server refuses, or the
reverse), a permlevel-1 pay field that landed at permlevel 0, a hook or patch
written and never registered, and a settings default that the JSON and
``DEFAULTS`` disagree about. Filesystem, JSON and ``ast`` only — no stub, so it
can share a process with anything.

Run: python -m unittest erpnext_enhancements.tests.test_time_correction_requests
"""

import ast
import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
WORKFORCE = APP / "workforce"
API = APP / "api" / "time_kiosk.py"
HOOKS = APP / "hooks.py"

TCR_JSON = WORKFORCE / "doctype" / "time_correction_request" / "time_correction_request.json"
TCR_PY = WORKFORCE / "doctype" / "time_correction_request" / "time_correction_request.py"
TCR_JS = WORKFORCE / "doctype" / "time_correction_request" / "time_correction_request.js"
JI_JSON = WORKFORCE / "doctype" / "job_interval" / "job_interval.json"
JI_JS = WORKFORCE / "doctype" / "job_interval" / "job_interval.js"
EPR_JSON = WORKFORCE / "doctype" / "employee_pay_rate" / "employee_pay_rate.json"
TKS_JSON = WORKFORCE / "doctype" / "time_kiosk_settings" / "time_kiosk_settings.json"
TKS_PY = WORKFORCE / "doctype" / "time_kiosk_settings" / "time_kiosk_settings.py"
TKL_JSON = WORKFORCE / "doctype" / "time_kiosk_log" / "time_kiosk_log.json"
PERMISSIONS = WORKFORCE / "permissions.py"
CORRECTIONS = WORKFORCE / "corrections.py"
FIXTURE = APP / "fixtures" / "custom_field.json"
PATCHES_TXT = APP / "patches.txt"

#: Endpoints the kiosk and the timeline page dial, per the v1.480.0 contract.
CONTRACT_ENDPOINTS = {
    "log_time", "get_current_status", "get_kiosk_options", "get_my_day", "get_my_history", "get_my_trail",
    "get_shift_summary", "submit_correction_request", "get_my_correction_requests", "cancel_correction_request",
    "get_live_positions", "get_employees_for_timeline", "get_location_history", "export_location_history",
    "refresh_tracking_health", "log_geolocation_batch", "get_kiosk_bootstrap", "record_job_photo",
}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def fields_of(path):
    return {f["fieldname"]: f for f in load(path)["fields"]}


def whitelisted(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(target, ast.Attribute) and target.attr == "whitelist":
                    names.add(node.name)
    return names


def module_set_constant(path, name):
    """A module-level ``NAME = {"a", "b"}`` set literal, evaluated."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            return set(ast.literal_eval(node.value))
    raise AssertionError(f"{name} not found in {path}")


def module_dict_constant(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


def js_role_list(path, const_name):
    match = re.search(const_name + r"\s*=\s*\[([^\]]*)\]", path.read_text(encoding="utf-8"))
    assert match, f"{const_name} not in {path}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def hooks_assignment(name):
    tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not in hooks.py")


class TestRequestTypeRules(unittest.TestCase):
    """Every request type the Select offers has a rule, and every rule names real fields."""

    def test_every_option_has_a_required_proposal_rule(self):
        options = [o for o in fields_of(TCR_JSON)["request_type"]["options"].split("\n") if o]
        rules = module_dict_constant(TCR_PY, "REQUIRED_PROPOSALS")
        self.assertEqual(set(options), set(rules), "a request type without a rule can be filed with nothing in it")

    def test_rules_name_real_fields_and_the_contract_shape(self):
        fields = fields_of(TCR_JSON)
        rules = module_dict_constant(TCR_PY, "REQUIRED_PROPOSALS")
        for request_type, required in rules.items():
            for fieldname in required:
                self.assertIn(fieldname, fields, f"{request_type} requires {fieldname}, which does not exist")
        self.assertEqual(set(rules["Adjust Times"]), {"proposed_start", "proposed_end"})
        self.assertEqual(set(rules["Change Project"]), {"proposed_project"})
        self.assertEqual(set(rules["Missed Clock-Out"]), {"proposed_end"})
        self.assertEqual(set(rules["Missed Entry"]), {"proposed_start", "proposed_end", "proposed_project"})

    def test_status_is_house_style(self):
        options = fields_of(TCR_JSON)["status"]["options"].split("\n")
        self.assertEqual(options, ["Requested", "Approved", "Declined", "Canceled"])
        self.assertNotIn("Cancelled", TCR_JSON.read_text(encoding="utf-8"))

    def test_naming_series_and_module(self):
        doc = load(TCR_JSON)
        self.assertEqual(doc["module"], "Workforce")
        self.assertEqual(doc["autoname"], "naming_series:")
        self.assertEqual(fields_of(TCR_JSON)["naming_series"]["options"], "TCR-.YYYY.-.#####")
        self.assertEqual(doc.get("track_changes"), 1)


class TestEndpointsExist(unittest.TestCase):
    def test_every_contract_endpoint_is_whitelisted(self):
        missing = CONTRACT_ENDPOINTS - whitelisted(API)
        self.assertEqual(missing, set(), f"the kiosk dials these and they are not whitelisted: {sorted(missing)}")

    def test_review_endpoints_are_whitelisted_where_the_form_dials_them(self):
        self.assertTrue({"approve_request", "decline_request"} <= whitelisted(CORRECTIONS))
        js = TCR_JS.read_text(encoding="utf-8")
        self.assertIn("erpnext_enhancements.workforce.corrections.approve_request", js)
        self.assertIn("erpnext_enhancements.workforce.corrections.decline_request", js)

    def test_the_interval_form_dials_a_real_endpoint(self):
        js = JI_JS.read_text(encoding="utf-8")
        self.assertIn("erpnext_enhancements.api.time_kiosk.refresh_tracking_health", js)
        self.assertIn("refresh_tracking_health", whitelisted(API))
        self.assertIn('frappe.set_route("location-timeline")', js)
        for key in ("employee", "from_date", "to_date"):
            self.assertIn(key, js)

    def test_helpers_are_not_whitelisted(self):
        leaked = {n for n in whitelisted(API) | whitelisted(CORRECTIONS) if n.startswith("_")}
        self.assertEqual(leaked, set())


class TestPermissionRolesAgree(unittest.TestCase):
    """The reviewer set is named in the JSON, in permissions.py and in the form script."""

    def test_tcr_manager_roles_match_json_write_roles(self):
        json_writers = {p["role"] for p in load(TCR_JSON)["permissions"] if p.get("write")}
        self.assertEqual(json_writers, module_set_constant(PERMISSIONS, "TCR_MANAGER_ROLES"))

    def test_tcr_form_script_draws_for_the_same_roles(self):
        self.assertEqual(js_role_list(TCR_JS, "EE_TCR_REVIEW_ROLES"), module_set_constant(PERMISSIONS, "TCR_MANAGER_ROLES"))

    def test_employee_may_create_and_read_but_not_write_or_delete(self):
        perm = next(p for p in load(TCR_JSON)["permissions"] if p["role"] == "Employee")
        self.assertEqual(perm.get("create"), 1)
        self.assertEqual(perm.get("read"), 1)
        self.assertFalse(perm.get("write"))
        self.assertFalse(perm.get("delete"))

    def test_job_interval_view_all_roles_hold_a_permlevel_0_read(self):
        readers = {p["role"] for p in load(JI_JSON)["permissions"] if p.get("read") and not p.get("permlevel")}
        view_all = module_set_constant(PERMISSIONS, "JOB_INTERVAL_VIEW_ALL_ROLES")
        self.assertTrue(view_all <= readers, f"{sorted(view_all - readers)} see all rows but have no read DocPerm")

    def test_timeline_manager_roles_are_a_subset_and_match_the_form_script(self):
        timeline = module_set_constant(API, "TIMELINE_MANAGER_ROLES")
        self.assertEqual(timeline, {"System Manager", "HR Manager", "Projects Manager"})
        self.assertTrue(timeline <= module_set_constant(PERMISSIONS, "JOB_INTERVAL_VIEW_ALL_ROLES"))
        self.assertEqual(js_role_list(JI_JS, "EE_TIMELINE_ROLES"), timeline)

    def test_both_permission_registers_carry_both_doctypes(self):
        query = hooks_assignment("permission_query_conditions")
        single = hooks_assignment("has_permission")
        for doctype in ("Job Interval", "Time Correction Request"):
            self.assertIn(doctype, query)
            self.assertIn(doctype, single)
            self.assertTrue(query[doctype].startswith("erpnext_enhancements.workforce.permissions."))
            self.assertTrue(single[doctype].startswith("erpnext_enhancements.workforce.permissions."))


class TestJobIntervalSchema(unittest.TestCase):
    PAY_FIELDS = {"pay_type", "pay_rate", "burden_pct", "burdened_rate", "labor_cost"}

    def test_pay_block_is_permlevel_1_and_nothing_else_is(self):
        fields = fields_of(JI_JSON)
        at_one = {n for n, f in fields.items() if f.get("permlevel") == 1}
        self.assertTrue(self.PAY_FIELDS <= at_one, f"pay fields at permlevel 0: {sorted(self.PAY_FIELDS - at_one)}")
        layout = {n for n in at_one if fields[n]["fieldtype"] in ("Section Break", "Column Break")}
        self.assertEqual(at_one - layout, self.PAY_FIELDS)
        for name in self.PAY_FIELDS:
            self.assertEqual(fields[name].get("read_only"), 1, f"{name} is editable")

    def test_permlevel_1_rows(self):
        rows = {(p["role"], bool(p.get("write"))) for p in load(JI_JSON)["permissions"] if p.get("permlevel") == 1}
        self.assertEqual(rows, {("System Manager", True), ("HR Manager", True), ("Accounts Manager", False)})

    def test_every_permlevel_1_role_can_reach_the_document(self):
        """Frappe derives doc-level access from permlevel-0 rows only; a permlevel-1 read
        with no permlevel-0 read behind it is a field nobody can ever open."""
        perms = load(JI_JSON)["permissions"]
        level_zero_readers = {p["role"] for p in perms if p.get("read") and not p.get("permlevel")}
        for role in {p["role"] for p in perms if p.get("permlevel") == 1}:
            self.assertIn(role, level_zero_readers, f"{role} holds a permlevel-1 read but no permlevel-0 read")

    def test_list_view_flags(self):
        fields = fields_of(JI_JSON)
        for name in ("tracking_health", "auto_closed", "offsite_start"):
            self.assertEqual(fields[name].get("in_list_view"), 1, name)
            self.assertEqual(fields[name].get("in_standard_filter"), 1, name)

    def test_contract_fields_exist(self):
        fields = fields_of(JI_JSON)
        expected = {
            "start_accuracy", "end_latitude", "end_longitude", "end_accuracy", "site_latitude", "site_longitude",
            "site_source", "site_radius_m", "start_distance_m", "end_distance_m", "offsite_start",
            "offsite_acknowledged", "offsite_end", "last_fix_at", "fix_count", "gap_minutes", "tracking_coverage_pct",
            "tracking_health", "planned_break_minutes", "auto_closed", "auto_close_reason", "corrected",
            "original_start_time", "original_end_time", "original_project", "correction_request", "position",
            "position_tier",
        }
        self.assertEqual(expected - set(fields), set())
        self.assertEqual(fields["tracking_health"]["options"].split("\n"), ["Pending", "Good", "Gaps", "None", "Off"])
        self.assertEqual(fields["correction_request"]["options"], "Time Correction Request")
        for name in ("corrected", "original_start_time", "original_end_time", "original_project", "correction_request"):
            self.assertEqual(fields[name].get("read_only"), 1, name)

    def test_start_anchor_fields_are_untouched(self):
        fields = fields_of(JI_JSON)
        self.assertEqual(fields["latitude"]["fieldtype"], "Data")
        self.assertEqual(fields["longitude"]["fieldtype"], "Data")


class TestPayRateAndFixtures(unittest.TestCase):
    def test_employee_pay_rate_is_a_workforce_child_table(self):
        doc = load(EPR_JSON)
        self.assertEqual(doc["istable"], 1)
        self.assertEqual(doc["module"], "Workforce")
        fields = fields_of(EPR_JSON)
        self.assertEqual(fields["pay_type"]["options"].split("\n"), ["Hourly", "Salaried"])
        self.assertEqual(fields["hourly_equivalent"].get("read_only"), 1)
        self.assertEqual(fields["effective_from"].get("reqd"), 1)

    def test_fixture_custom_fields(self):
        by_name = {r["name"]: r for r in load(FIXTURE)}
        pay = by_name["Employee-custom_pay_rates"]
        self.assertEqual((pay["fieldtype"], pay["options"], pay["permlevel"]), ("Table", "Employee Pay Rate", 1))
        self.assertEqual(pay["insert_after"], "custom_healthcare_stipend")
        link = by_name["Timesheet Detail-custom_job_interval"]
        self.assertEqual((link["fieldtype"], link["options"], link["read_only"]), ("Link", "Job Interval", 1))
        for name in ("custom_site_location_section", "custom_site_latitude", "custom_site_longitude",
                     "custom_site_location_source", "custom_site_geocoded_from"):
            self.assertIn(f"Project-{name}", by_name)
        self.assertEqual(by_name["Project-custom_site_location_source"]["options"], "\nGeocoded\nManual")
        self.assertEqual(by_name["Project-custom_site_latitude"]["precision"], "6")
        # every new record carries the full export shape (fixture sync erases omitted keys)
        template = set(by_name["Employee-custom_healthcare_stipend"])
        for name in ("Employee-custom_pay_rates", "Timesheet Detail-custom_job_interval", "Project-custom_site_latitude"):
            self.assertEqual(set(by_name[name]), template, name)


class TestSettingsAndLog(unittest.TestCase):
    def test_defaults_agree_with_the_json(self):
        defaults = module_dict_constant(TKS_PY, "DEFAULTS")
        for name, field in fields_of(TKS_JSON).items():
            if field["fieldtype"] in ("Section Break", "Column Break"):
                continue
            self.assertIn(name, defaults, f"{name} has no DEFAULTS entry")
            json_default, py_default = field.get("default", ""), defaults[name]
            try:
                self.assertEqual(float(json_default), float(py_default), name)
            except (TypeError, ValueError):
                self.assertEqual(str(json_default), str(py_default), name)

    def test_the_two_policy_flips_are_the_declared_defaults(self):
        fields = fields_of(TKS_JSON)
        self.assertEqual(fields["keep_wake_lock"]["default"], "1")
        self.assertEqual(fields["retention_days"]["default"], "0")

    def test_new_settings_fields_exist(self):
        fields = fields_of(TKS_JSON)
        for name in ("auto_close_after_hours", "tracking_gap_minutes", "keep_low_accuracy_fixes", "anchor_accuracy_m",
                     "offsite_warn", "overtime_week_start", "overtime_weekly_hours", "default_burden_pct",
                     "send_supervisor_digest"):
            self.assertIn(name, fields)
        self.assertEqual(fields["overtime_week_start"]["options"], "Sunday\nMonday")

    def test_log_gains_low_accuracy_and_fix_source(self):
        fields = fields_of(TKL_JSON)
        options = fields["log_status"]["options"].split("\n")
        self.assertEqual(options[:4], ["Success", "Permission Denied", "Error", "Offline Sync"], "existing options renamed")
        self.assertIn("Low Accuracy", options)
        self.assertEqual(fields["fix_source"]["options"].split("\n"), ["Watch", "Heartbeat", "Catch-up", "Anchor"])
        self.assertEqual(fields["fix_source"]["default"], "Watch")


class TestHooksAndPatches(unittest.TestCase):
    def test_doc_events(self):
        events = hooks_assignment("doc_events")
        self.assertEqual(events["Employee"]["validate"], "erpnext_enhancements.workforce.costing.validate_employee_pay_rates")
        self.assertIn("erpnext_enhancements.workforce.costing.on_employee_update", events["Employee"]["on_update"])
        self.assertEqual(events["Activity Type"]["after_insert"], "erpnext_enhancements.workforce.costing.on_activity_type_insert")
        self.assertIn("erpnext_enhancements.workforce.sites.on_project_update", events["Project"]["on_update"])

    def test_scheduler_events(self):
        sched = hooks_assignment("scheduler_events")
        self.assertEqual(sched["cron"]["45 6 * * *"], ["erpnext_enhancements.workforce.digest.send_supervisor_digests"])
        self.assertIn("erpnext_enhancements.workforce.sweeper.auto_close_stale_intervals", sched["hourly"])
        self.assertIn("erpnext_enhancements.workforce.costing.sync_all_activity_costs", sched["daily"])
        self.assertIn("erpnext_enhancements.workforce.sites.backfill_missing_site_coordinates", sched["daily"])
        self.assertIn("erpnext_enhancements.api.time_kiosk.purge_old_location_logs", sched["daily"])

    def test_no_doctype_js_for_our_own_scripts(self):
        doctype_js = hooks_assignment("doctype_js")
        for doctype in ("Job Interval", "Time Correction Request"):
            self.assertNotIn(doctype, doctype_js)

    def test_patches_are_registered_and_exist(self):
        text = PATCHES_TXT.read_text(encoding="utf-8")
        post = text.split("[post_model_sync]", 1)[1]
        for name in ("backfill_time_kiosk_settings_defaults", "seed_employee_pay_visibility", "backfill_project_site_coordinates"):
            self.assertIn(f"erpnext_enhancements.patches.{name}\n", post + "\n", name)
            self.assertTrue((APP / "patches" / f"{name}.py").is_file(), name)
            self.assertIn(f"`{name}`", (APP / "patches" / "README.md").read_text(encoding="utf-8"))

    def test_the_settings_patch_never_reads_singles_through_the_orm(self):
        # Checked on the CALLS, not the text: the docstring names the trap it avoids, and
        # an absence assertion over raw source would fail on the explanation.
        src = (APP / "patches" / "backfill_time_kiosk_settings_defaults.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and first.value == "Singles":
                    self.fail(f"{node.func.attr}('Singles', ...) reaches tabSingles through the ORM")
        self.assertIn("from tabSingles", src)
        self.assertIn("2026-09-17", src)
        self.assertIn("keep_wake_lock", src)
        self.assertIn("retention_days", src)

    def test_the_pay_visibility_patch_copies_standard_perms_first(self):
        src = (APP / "patches" / "seed_employee_pay_visibility.py").read_text(encoding="utf-8")
        self.assertIn("setup_custom_perms", src)
        self.assertIn('"Accounts Manager"', src)

    def test_the_controller_class_names_match_frappe_derivation(self):
        for path, doctype in ((TCR_PY, "Time Correction Request"),
                              (WORKFORCE / "doctype" / "employee_pay_rate" / "employee_pay_rate.py", "Employee Pay Rate")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            classes = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
            self.assertIn(doctype.replace(" ", "").replace("-", ""), classes)


if __name__ == "__main__":
    unittest.main()
