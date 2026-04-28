import frappe
from frappe import _
from frappe.model.document import Document
from functools import cached_property
from frappe.utils import nowdate, getdate, add_days

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
		Phase 3: Enqueue background processing.
		Generates Stock Entry, Timesheet, and handles Warranty logic.
		"""
		frappe.enqueue(
			"erpnext_enhancements.api.maintenance_workflow.process_maintenance_submission",
			record_name=self.name,
			queue="default"
		)
		
		# Also trigger the Phase 1 recalulation logic if not already hooked
		from erpnext_enhancements.api.maintenance_scheduling import update_sales_order_next_visit
		update_sales_order_next_visit(self, None)

	def get_context(self, context):
		"""
		Phase 5: Web View Context.
		Fetches the visibility flag for labor hours from the parent Sales Order.
		"""
		context.show_labor = False
		if self.project:
			show_labor = frappe.db.get_value("Sales Order", 
				{"project": self.project, "order_type": "Maintenance", "docstatus": 1}, 
				"custom_display_labor_hours")
			context.show_labor = True if show_labor else False

		context.parents = [{"name": _("Maintenance Records"), "route": "maintenance-records"}]

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

@frappe.whitelist()
def get_dashboard_context(project, asset):
	"""
	Phase 2: Fetch context for the technician dashboard.
	Returns Profile data, Asset data, and Last 3 Visits.
	"""
	context = {}
	
	# 1. Profile Data
	profile = frappe.db.get_value("Sapphire Maintenance Profile", {"project": project}, ["safety_instructions", "access_codes"], as_dict=True)
	context['profile'] = profile or {}

	# 2. Asset Data
	asset_data = frappe.db.get_value("Asset", asset, ["custom_site_instructions", "item_name"], as_dict=True)
	context['asset'] = asset_data or {}

	# 3. Last 3 Visits
	visits = frappe.get_all(
		"Sapphire Maintenance Record",
		filters={"project": project, "docstatus": 1},
		fields=["name", "creation", "technician"],
		order_by="creation desc",
		limit=3
	)
	context['visits'] = visits

	return context
