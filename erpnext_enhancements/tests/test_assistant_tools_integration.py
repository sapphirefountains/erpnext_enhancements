"""Bench-backed integration tests for the FAC assistant tools.

Needs a real bench with frappe_assistant_core installed; every test is
skip-guarded so the file collects cleanly on FAC-less benches and on machines
without frappe at all (e.g. the CI unit-test runner). All FAC imports happen
inside test methods for the same reason.

Run: bench --site <site> run-tests --app erpnext_enhancements \
    --module erpnext_enhancements.tests.test_assistant_tools_integration
"""

import unittest

try:  # collection-safe on benches/machines without frappe
    import frappe
    from frappe.utils import add_days, now_datetime, nowdate

    _HAS_FRAPPE = True
except Exception:
    _HAS_FRAPPE = False

if _HAS_FRAPPE:
    try:
        import frappe_assistant_core

        _HAS_FAC = True
    except Exception:
        _HAS_FAC = False
else:
    _HAS_FAC = False

TOOL_NAMES = [
    "maintenance_day_board",
    "maintenance_contract_status",
    "maintenance_visit_history",
    "maintenance_site_briefing",
    "project_status_overview",
    "project_procurement_status",
    "workforce_time_status",
]


@unittest.skipUnless(_HAS_FRAPPE and _HAS_FAC, "needs a bench with frappe_assistant_core installed")
class TestAssistantToolsIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")

        cls.project = "TEST-PROJECT-FAC-TOOLS"
        if not frappe.db.exists("Project", cls.project):
            project = frappe.new_doc("Project")
            project.project_name = cls.project
            project.status = "Open"
            project.insert(ignore_permissions=True)
        # The fixture project's autoname may differ from project_name.
        cls.project_id = frappe.db.get_value("Project", {"project_name": cls.project}, "name")

        # Customer (required on Sapphire Maintenance Contract).
        cls.customer = frappe.db.get_value("Customer", {}, "name")
        if not cls.customer:
            customer = frappe.new_doc("Customer")
            customer.customer_name = "FAC Tools Test Customer"
            customer.insert(ignore_permissions=True)
            cls.customer = customer.name

        # Water-feature Item + Serial No (required on the contract feature row);
        # same fixture style as test_sapphire_maintenance.py.
        if not frappe.db.exists("Item", "Customer Water Feature"):
            item = frappe.new_doc("Item")
            item.item_code = "Customer Water Feature"
            item.item_name = "Customer Water Feature"
            item.item_group = "All Item Groups"
            item.is_stock_item = 0
            item.insert(ignore_permissions=True)
        cls.serial_no = "TEST-SN-FAC-TOOLS"
        if not frappe.db.exists("Serial No", cls.serial_no):
            serial = frappe.new_doc("Serial No")
            serial.item_code = "Customer Water Feature"
            serial.serial_no = cls.serial_no
            serial.insert(ignore_permissions=True)

    def _registry(self):
        from frappe_assistant_core.core.tool_registry import get_tool_registry

        return get_tool_registry()

    # -- discovery ----------------------------------------------------------

    def test_all_tools_discovered(self):
        registry = self._registry()
        for name in TOOL_NAMES:
            tool = registry.get_tool(name)
            self.assertIsNotNone(tool, f"{name} not discovered — is the custom_tools plugin enabled?")
            self.assertEqual(tool.source_app, "erpnext_enhancements", name)
            metadata = tool.get_metadata()
            self.assertEqual(metadata["name"], name)

    # -- smoke executions (Administrator; empty result sets are valid) ------

    def test_workforce_time_status_now(self):
        tool = self._registry().get_tool("workforce_time_status")
        result = tool.execute({"mode": "now"})
        self.assertTrue(result["success"])
        self.assertIsInstance(result["intervals"], list)

    def test_workforce_time_status_reports_open_interval(self):
        employee = frappe.db.get_value("Employee", {"status": "Active"}, "name")
        if not employee:
            self.skipTest("no Active Employee on this site")
        interval = frappe.new_doc("Job Interval")
        interval.employee = employee
        interval.project = self.project_id
        interval.status = "Open"
        interval.start_time = now_datetime()
        interval.insert(ignore_permissions=True)
        try:
            tool = self._registry().get_tool("workforce_time_status")
            result = tool.execute({"mode": "now", "employee": employee})
            names = [row["name"] for row in result["intervals"]]
            self.assertIn(interval.name, names)
            row = next(r for r in result["intervals"] if r["name"] == interval.name)
            self.assertGreaterEqual(row["worked_hours"], 0)
        finally:
            frappe.delete_doc("Job Interval", interval.name, force=True, ignore_permissions=True)

    def test_maintenance_contract_status_lists_fixture_contract(self):
        # One Active contract per project is enforced — clear leftovers from
        # any earlier aborted run before inserting.
        for name in frappe.get_all(
            "Sapphire Maintenance Contract", filters={"project": self.project_id}, pluck="name"
        ):
            frappe.delete_doc("Sapphire Maintenance Contract", name, force=True, ignore_permissions=True)

        contract = frappe.new_doc("Sapphire Maintenance Contract")
        contract.customer = self.customer
        contract.project = self.project_id
        contract.status = "Active"
        contract.visit_shape = "Per Site Visit"
        contract.append(
            "covered_features",
            {
                "serial_no": self.serial_no,
                "frequency": "Weekly",
                "next_visit_date": add_days(nowdate(), 3),
            },
        )
        contract.insert(ignore_permissions=True)
        try:
            tool = self._registry().get_tool("maintenance_contract_status")
            result = tool.execute({"project": self.project_id, "upcoming_days": 7})
            self.assertTrue(result["success"])
            names = [c["name"] for c in result["contracts"]]
            self.assertIn(contract.name, names)
            upcoming = [u for u in result["upcoming"] if u["contract"] == contract.name]
            self.assertEqual(len(upcoming), 1)
            self.assertEqual(upcoming[0]["days_until"], 3)
        finally:
            frappe.delete_doc(
                "Sapphire Maintenance Contract", contract.name, force=True, ignore_permissions=True
            )

    def test_project_status_overview_project_scope(self):
        tool = self._registry().get_tool("project_status_overview")
        result = tool.execute({"scope": "project", "project": self.project_id})
        self.assertTrue(result["success"])
        self.assertEqual(result["project"]["name"], self.project_id)
        self.assertIn("health", result)
        self.assertIn("process_steps", result)

    def test_project_status_overview_portfolio_gate(self):
        # Portfolio scope is page-role gated; depending on site config the
        # Administrator may or may not hold the page role. Both outcomes are
        # valid — what must not happen is an unhandled exception.
        tool = self._registry().get_tool("project_status_overview")
        try:
            result = tool.execute({"scope": "portfolio"})
            self.assertTrue(result["success"])
            self.assertIsInstance(result["projects"], list)
        except frappe.PermissionError:
            pass

    def test_project_procurement_status_empty_project(self):
        tool = self._registry().get_tool("project_procurement_status")
        result = tool.execute({"project": self.project_id})
        self.assertTrue(result["success"])
        self.assertEqual(result["summary"]["total_items"], 0)

    def test_maintenance_site_briefing(self):
        tool = self._registry().get_tool("maintenance_site_briefing")
        result = tool.execute({"project": self.project_id})
        self.assertTrue(result["success"])
        for key in ("profile", "contract", "visits", "service_scope", "trends", "open_drafts"):
            self.assertIn(key, result["briefing"])

    # -- permissions ---------------------------------------------------------

    def test_roleless_user_denied(self):
        email = "fac-tools-roleless@example.com"
        if not frappe.db.exists("User", email):
            user = frappe.new_doc("User")
            user.email = email
            user.first_name = "FAC Roleless"
            user.send_welcome_email = 0
            user.insert(ignore_permissions=True)
        try:
            frappe.set_user(email)
            tool = self._registry().get_tool("workforce_time_status")
            with self.assertRaises(frappe.PermissionError):
                tool.check_permission()
        finally:
            frappe.set_user("Administrator")


if __name__ == "__main__":
    unittest.main()
