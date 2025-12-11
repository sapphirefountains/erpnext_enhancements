import frappe
from frappe.tests.utils import FrappeTestCase
from erpnext_enhancements.project_enhancements import get_procurement_status

class TestProcurementStatus(FrappeTestCase):
    def setUp(self):
        # Create a Project
        self.project = frappe.get_doc({
            "doctype": "Project",
            "project_name": "Test Procurement Project",
            "status": "Open"
        }).insert()

        # Create an Item
        if not frappe.db.exists("Item", "Test Item Proc"):
            self.item = frappe.get_doc({
                "doctype": "Item",
                "item_code": "Test Item Proc",
                "item_group": "All Item Groups",
                "stock_uom": "Nos",
                "is_stock_item": 1
            }).insert()
        else:
            self.item = frappe.get_doc("Item", "Test Item Proc")

        # Create a Supplier
        if not frappe.db.exists("Supplier", "Test Supplier Proc"):
            frappe.get_doc({
                "doctype": "Supplier",
                "supplier_name": "Test Supplier Proc",
                "supplier_group": "All Supplier Groups"
            }).insert()

        # Get Company for Warehouses (default usually exists)
        self.company = frappe.db.get_single_value('Global Defaults', 'default_company') or "_Test Company"

    def tearDown(self):
        frappe.db.rollback()

    def test_get_procurement_status_internal_transfer(self):
        # Scenario: Material Request (Transfer) -> Stock Entry
        mr = frappe.get_doc({
            "doctype": "Material Request",
            "material_request_type": "Material Transfer",
            "transaction_date": frappe.utils.nowdate(),
            "items": [{
                "item_code": self.item.item_code,
                "qty": 5,
                "schedule_date": frappe.utils.nowdate(),
                "project": self.project.name
            }]
        }).insert()
        mr.submit()

        # Find valid warehouses for the company
        warehouses = frappe.get_all("Warehouse", filters={"company": self.company}, limit=2)
        s_warehouse = warehouses[0].name if warehouses else "Stores - " + frappe.get_site_config().get("abbr", "EMP")
        t_warehouse = warehouses[1].name if len(warehouses) > 1 else s_warehouse # Fallback

        # Create Stock Entry linked to MR
        se = frappe.get_doc({
            "doctype": "Stock Entry",
            "stock_entry_type": "Material Transfer",
            "items": [{
                "item_code": self.item.item_code,
                "qty": 5,
                "s_warehouse": s_warehouse,
                "t_warehouse": t_warehouse,
                "material_request": mr.name,
                "material_request_item": mr.items[0].name
            }]
        })
        se.insert(ignore_permissions=True)

        # Run function
        status = get_procurement_status(self.project.name)

        mr_entry = next((x for x in status if x['mr'] == mr.name), None)
        self.assertIsNotNone(mr_entry)

        # Check if we can see SE info (now using the correct key 'stock_entry')
        self.assertIn('stock_entry', mr_entry, "Stock Entry field missing in result (Expected for Transfer)")
        self.assertEqual(mr_entry['stock_entry'], se.name)
        self.assertEqual(mr_entry['stock_entry_status'], 'Draft') # docstatus 0


    def test_get_procurement_status_direct_po(self):
        # Scenario: Direct Purchase Order (No MR)
        po = frappe.get_doc({
            "doctype": "Purchase Order",
            "supplier": "Test Supplier Proc",
            "transaction_date": frappe.utils.nowdate(),
            "items": [{
                "item_code": self.item.item_code,
                "qty": 10,
                "rate": 100,
                "schedule_date": frappe.utils.nowdate(),
                "project": self.project.name,
                "material_request": None # Explicitly None
            }]
        }).insert()

        # Run function
        status = get_procurement_status(self.project.name)

        # Verify
        # This should now be found
        po_entry = next((x for x in status if x.get('po') == po.name), None)
        self.assertIsNotNone(po_entry, "Direct PO not found in procurement status")
        self.assertEqual(po_entry['ordered_qty'], 10)
