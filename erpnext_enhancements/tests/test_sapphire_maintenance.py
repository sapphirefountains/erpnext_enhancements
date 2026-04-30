import frappe
import unittest
from frappe.utils import nowdate, add_days

class TestSapphireMaintenance(unittest.TestCase):
    def setUp(self):
        # Create a generic item for water features if not exists
        if not frappe.db.exists("Item", "Customer Water Feature"):
            item = frappe.new_doc("Item")
            item.item_code = "Customer Water Feature"
            item.item_name = "Customer Water Feature"
            item.item_group = "All Item Groups"
            item.is_stock_item = 0
            item.insert(ignore_permissions=True)
        
        # Create a test Serial No
        self.serial_no = "TEST-SN-001"
        if not frappe.db.exists("Serial No", self.serial_no):
            sn = frappe.new_doc("Serial No")
            sn.item_code = "Customer Water Feature"
            sn.serial_no = self.serial_no
            sn.custom_site_instructions = "Follow safety gate."
            sn.insert(ignore_permissions=True)

        # Create a test Project
        self.project = "TEST-PROJECT-MNT"
        if not frappe.db.exists("Project", self.project):
            project = frappe.new_doc("Project")
            project.project_name = self.project
            project.status = "Open"
            project.insert(ignore_permissions=True)

    def test_maintenance_record_creation(self):
        """Verify record links to Serial No and fetches dashboard context."""
        doc = frappe.new_doc("Sapphire Maintenance Record")
        doc.project = self.project
        doc.serial_no = self.serial_no
        doc.technician = "Administrator"
        doc.insert()

        self.assertEqual(doc.serial_no, self.serial_no)
        
        # Test dashboard context API
        from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record import get_dashboard_context
        ctx = get_dashboard_context(self.project, self.serial_no)
        
        self.assertIn("serial_no", ctx)
        self.assertEqual(ctx["serial_no"].get("custom_site_instructions"), "Follow safety gate.")

    def test_predictive_maintenance_generation(self):
        """Verify tasks.py correctly generates records from Sales Order Item."""
        # Create Sales Order
        so = frappe.new_doc("Sales Order")
        so.customer = frappe.get_all("Customer", limit=1)[0].name
        so.transaction_date = nowdate()
        so.delivery_date = add_days(nowdate(), 30)
        so.project = self.project
        so.append("items", {
            "item_code": "Customer Water Feature",
            "qty": 1,
            "rate": 100,
            "custom_serial_no": self.serial_no,
            "custom_next_predictive_visit": nowdate(), # Trigger now
            "custom_maintenance_frequency": "Monthly"
        })
        so.insert(ignore_permissions=True)
        so.submit()

        from erpnext_enhancements.tasks import generate_predictive_maintenance_records
        generate_predictive_maintenance_records()

        # Check if record was created
        record_exists = frappe.db.exists("Sapphire Maintenance Record", {
            "project": self.project,
            "serial_no": self.serial_no,
            "docstatus": 0
        })
        self.assertTrue(record_exists)
