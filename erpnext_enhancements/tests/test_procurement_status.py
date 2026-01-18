import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.project_enhancements import get_procurement_status


class TestProcurementStatus(FrappeTestCase):
	def setUp(self):
		# Ensure Company exists
		self.company = "_Test Company_"
		if not frappe.db.exists("Company", self.company):
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": self.company,
					"abbr": "TC",
					"default_currency": "USD",
					"country": "United States",
				}
			).insert()

		# Create a Project
		self.project = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": "Test Procurement Project",
				"status": "Open",
				"company": self.company,
			}
		).insert()

		# Ensure Item Group exists
		if not frappe.db.exists("Item Group", "All Item Groups"):
			frappe.get_doc(
				{"doctype": "Item Group", "item_group_name": "All Item Groups", "is_group": 1}
			).insert()

		# Ensure UOM exists
		if not frappe.db.exists("UOM", "Nos"):
			frappe.get_doc({"doctype": "UOM", "uom_name": "Nos"}).insert()

		# Create an Item
		if not frappe.db.exists("Item", "Test Item Proc"):
			self.item = frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": "Test Item Proc",
					"item_group": "All Item Groups",
					"stock_uom": "Nos",
					"is_stock_item": 1,
				}
			).insert()
		else:
			self.item = frappe.get_doc("Item", "Test Item Proc")

		# Ensure Supplier Group exists
		if not frappe.db.exists("Supplier Group", "All Supplier Groups"):
			frappe.get_doc(
				{"doctype": "Supplier Group", "supplier_group_name": "All Supplier Groups"}
			).insert()

		# Create a Supplier
		if not frappe.db.exists("Supplier", "Test Supplier Proc"):
			frappe.get_doc(
				{
					"doctype": "Supplier",
					"supplier_name": "Test Supplier Proc",
					"supplier_group": "All Supplier Groups",
				}
			).insert()

		# Ensure Warehouse exists
		self.warehouse = f"Stores - {frappe.db.get_value('Company', self.company, 'abbr')}"
		if not frappe.db.exists("Warehouse", self.warehouse):
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": "Stores",
					"company": self.company,
				}
			).insert()

	def tearDown(self):
		frappe.db.rollback()

	def test_get_procurement_status_internal_transfer(self):
		# Scenario: Material Request (Transfer) -> Stock Entry
		mr = frappe.get_doc(
			{
				"doctype": "Material Request",
				"material_request_type": "Material Transfer",
				"transaction_date": frappe.utils.nowdate(),
				"items": [
					{
						"item_code": self.item.item_code,
						"qty": 5,
						"schedule_date": frappe.utils.nowdate(),
						"project": self.project.name,
					}
				],
			}
		).insert()
		mr.submit()

		# Find valid warehouses for the company
		warehouses = frappe.get_all("Warehouse", filters={"company": self.company}, limit=2)
		s_warehouse = (
			warehouses[0].name if warehouses else "Stores - " + frappe.get_site_config().get("abbr", "EMP")
		)
		t_warehouse = warehouses[1].name if len(warehouses) > 1 else s_warehouse  # Fallback

		# Create Stock Entry linked to MR
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Transfer",
				"items": [
					{
						"item_code": self.item.item_code,
						"qty": 5,
						"s_warehouse": s_warehouse,
						"t_warehouse": t_warehouse,
						"material_request": mr.name,
						"material_request_item": mr.items[0].name,
					}
				],
			}
		)
		se.insert(ignore_permissions=True)

		# Run function
		status = get_procurement_status(self.project.name)

		# Expect the item to be "graduated" to the Stock Entry stage
		self.assertIn("Stock Entry", status, "Result should contain 'Stock Entry' key")

		# Find the entry
		se_list = status["Stock Entry"]
		mr_entry = next((x for x in se_list if x["mr"] == mr.name), None)

		self.assertIsNotNone(mr_entry, "Item should be found under Stock Entry group")

		# Check if we can see SE info
		self.assertEqual(mr_entry["stock_entry"], se.name)
		self.assertEqual(mr_entry["stock_entry_status"], "Draft")  # docstatus 0

	def test_get_procurement_status_direct_po(self):
		# Scenario: Direct Purchase Order (No MR)
		po = frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"supplier": "Test Supplier Proc",
				"transaction_date": frappe.utils.nowdate(),
				"items": [
					{
						"item_code": self.item.item_code,
						"qty": 10,
						"rate": 100,
						"schedule_date": frappe.utils.nowdate(),
						"project": self.project.name,
						"material_request": None,  # Explicitly None
					}
				],
			}
		).insert()

		# Run function
		status = get_procurement_status(self.project.name)

		# Expect the item to be "graduated" to the Purchase Order stage
		self.assertIn("Purchase Order", status, "Result should contain 'Purchase Order' key")

		# Verify
		po_list = status["Purchase Order"]
		po_entry = next((x for x in po_list if x.get("po") == po.name), None)

		self.assertIsNotNone(po_entry, "Direct PO not found in Purchase Order group")
		self.assertEqual(po_entry["ordered_qty"], 10)
