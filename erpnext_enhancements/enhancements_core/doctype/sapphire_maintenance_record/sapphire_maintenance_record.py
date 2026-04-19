import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate, getdate, add_days, cached_property

class SapphireMaintenanceRecord(Document):
	@cached_property
	def historical_visits(self):
		"""
		Phase 4: Virtual Child Table Logic.
		Populates the 'Historical Visits' table with the last 5 records for the selected project.
		"""
		if not self.project:
			return []

		records = frappe.get_all(
			"Sapphire Maintenance Record",
			filters={"project": self.project, "name": ["!=", self.name or ""], "docstatus": 1},
			fields=["creation as visit_date", "name as record_id", "technician"],
			order_by="creation desc",
			limit=5,
		)
		
		# Map to virtual child table format
		return [frappe._dict(d) for d in records]

	def on_submit(self):
		"""
		Phase 4: Automated Stock Entry.
		Generates and submits a Stock Entry (Material Issue) for consumables.
		"""
		if not self.consumables:
			return

		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.purpose = "Material Issue"
		stock_entry.company = frappe.db.get_value("Project", self.project, "company") or frappe.defaults.get_global_default("company")
		
		for item in self.consumables:
			stock_entry.append("items", {
				"item_code": item.item,
				"s_warehouse": item.warehouse,
				"qty": item.qty,
				"serial_and_batch_bundle": item.serial_and_batch_bundle,
				"project": self.project
			})

		stock_entry.insert()
		stock_entry.submit()
		
		# Link Stock Entry to this record
		frappe.msgprint(_("Stock Entry {0} created and submitted.").format(
			frappe.utils.get_link_to_form("Stock Entry", stock_entry.name)
		))

@frappe.whitelist()
def get_template_items(project):
	"""
	Phase 3 Helper: Fetch items from the template linked to the project.
	"""
	template_name = frappe.db.get_value("Sapphire Maintenance Template", {"project": project, "status": "Active"}, "name")
	if not template_name:
		# Fallback to customer template if project-specific doesn't exist
		customer = frappe.db.get_value("Project", project, "customer")
		template_name = frappe.db.get_value("Sapphire Maintenance Template", {"customer": customer, "status": "Active"}, "name")

	if template_name:
		return frappe.get_all("Sapphire Template Item", filters={"parent": template_name}, fields=["question_prompt"], order_by="sequence")
	return []

def update_sales_order_metrics(maintenance_record):
	"""
	Phase 4: Helper for extend_doctype_class.
	Updates 'Last Visit Date' and 'Next Predictive Visit' on linked Sales Order.
	"""
	# In a real scenario, we'd find the Sales Order linked to the Project or via a custom field
	so_name = frappe.db.get_value("Project", maintenance_record.project, "sales_order")
	if so_name:
		last_visit = getdate(maintenance_record.creation)
		next_visit = add_days(last_visit, 30) # Example: 30 days logic
		
		frappe.db.set_value("Sales Order", so_name, {
			"custom_last_visit_date": last_visit,
			"custom_next_predictive_visit": next_visit
		})
