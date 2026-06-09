"""Controller for the Sapphire Maintenance Record doctype.

A Maintenance Record is the submittable field-service "visit" document at the
heart of the Sapphire Maintenance subsystem. It captures a technician's on-site
work against a Project / Serial No: a safety-gated checklist (``maintenance_results``
child table, pre-populated from the active Sapphire Maintenance Template),
consumables used (``consumables``), clock in/out times for labour costing, and a
client sign-off signature.

Lifecycle / wiring:
  * ``on_submit`` (below) enqueues the background worker
    ``api.maintenance_workflow.process_maintenance_submission`` (Stock Entry,
    Timesheet, Warranty/RMA, Sales Invoice) and synchronously calls
    ``api.maintenance_scheduling.update_sales_order_next_visit`` to back-fill the
    next predictive-visit date on the originating Sales Order. NOTE: that same
    scheduling function is ALSO registered as an ``on_submit`` doc-event in
    hooks.py, so it runs twice on submit (idempotent — it only writes dates).
  * The doctype has a workflow (Workflow State stored in ``workflow_state``) and
    two Notifications ("Maintenance Review Needed", "Maintenance Finalized") —
    see the module's fixtures in hooks.py.
  * ``route`` is "maintenance-records": the record is exposed on the customer
    portal at ``/maintenance-records`` and rendered by the print/web template
    ``sapphire_maintenance_record.html`` (see ``get_context`` below).

This module also exposes two whitelisted helpers used by the desk form's JS:
``get_template_items`` (populate the checklist) and ``get_dashboard_context``
(technician on-site safety/site/history dashboard).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from functools import cached_property
from frappe.utils import nowdate, getdate, add_days

class SapphireMaintenanceRecord(Document):
	@cached_property
	def historical_visits(self):
		"""Return the last 5 submitted visits for this record's Project.

		Backs the virtual ``historical_visits`` child table (the Sapphire
		Historical Visit doctype is ``is_virtual``), so the rows are computed on
		read rather than stored. Excludes the current record and returns
		``frappe._dict`` rows shaped to the virtual table's fields
		(visit_date / record_id / technician). Returns ``[]`` when no Project is
		set. Cached per-document instance via ``cached_property``.
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
		"""Submit lifecycle hook: kick off downstream automation.

		Side effects:
		  * Enqueues ``api.maintenance_workflow.process_maintenance_submission``
		    on the "default" queue to generate Stock Entry, Timesheet, Warranty/RMA
		    Material Request and a draft Sales Invoice in the background.
		  * Synchronously calls ``update_sales_order_next_visit`` to update the
		    Sales Order's last/next predictive-visit dates (also wired in hooks.py
		    as a redundant on_submit doc-event).
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
		"""Populate the web/portal render context for ``/maintenance-records``.

		Called by Frappe's web-view machinery when the record is rendered via the
		``sapphire_maintenance_record.html`` template. Sets ``context.show_labor``
		from the parent Maintenance Sales Order's ``custom_display_labor_hours``
		flag (controls whether the Service Duration block is shown), and adds the
		portal breadcrumb back to the "Maintenance Records" listing.

		Args:
			context: The mutable web render context (modified in place).
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
	"""Return checklist prompts from the active template for a Project.

	Whitelisted; called by the desk form JS (``populate_checklist``) to seed the
	``maintenance_results`` table. Looks up an Active Sapphire Maintenance
	Template by ``project`` first, then falls back to one matched on the Project's
	``customer``.

	Args:
		project (str): Project name (docname).

	Returns:
		list[dict]: Sapphire Template Item rows ({"question_prompt": ...}) ordered
		by ``sequence``; empty list if no Active template is found.
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
def get_dashboard_context(project, serial_no):
	"""Return the on-site briefing data for the technician dashboard widget.

	Whitelisted; called by the desk form JS (``render_dashboard``) to build the
	in-form HTML panel shown before the safety gate is acknowledged.

	Args:
		project (str): Project name (docname).
		serial_no (str): Serial No name (docname).

	Returns:
		dict: {
			"profile": Sapphire Maintenance Profile safety_instructions/access_codes,
			"serial_no": Serial No custom_site_instructions/item_name,
			"visits": last 3 submitted Sapphire Maintenance Records for the project
		}
	"""
	context = {}
	
	# 1. Profile Data
	profile = frappe.db.get_value("Sapphire Maintenance Profile", {"project": project}, ["safety_instructions", "access_codes"], as_dict=True)
	context['profile'] = profile or {}

	# 2. Serial No Data
	serial_no_data = frappe.db.get_value("Serial No", serial_no, ["custom_site_instructions", "item_name"], as_dict=True)
	context['serial_no'] = serial_no_data or {}

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
