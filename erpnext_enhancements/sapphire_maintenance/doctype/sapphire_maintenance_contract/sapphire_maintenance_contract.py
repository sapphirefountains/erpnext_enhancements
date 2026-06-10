"""Controller for the Sapphire Maintenance Contract doctype.

The *operational* maintenance contract: which water features get serviced, how
often, with which modular form template, and from which warehouse their
chemicals are drawn. It sits between two existing documents:

  * **Project Contract** (Maintenance Services Agreement) — the signed legal
    terms. ``make_contract_from_project_contract`` maps one in, pulling visit
    frequency, invoicing frequency, the agreement start date and the included
    seasonal service options (startup/winterization) into ``seasonal_visits``.
  * **Sales Order** (order_type Maintenance) — the commercial source per-visit
    invoices are drawn against. ``make_contract_from_sales_order`` maps its
    water-feature items into ``covered_features``.

The daily scheduler (``tasks.generate_predictive_maintenance_records``) reads
Active contracts' feature rows (``next_visit_date``) and seasonal rows
(``target_month``) to draft Sapphire Maintenance Records; record submission
writes the rolling dates back (``api.maintenance_scheduling``).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate

# Project Contract uses a slightly different frequency vocabulary; "Custom"
# has no fixed interval, so it maps to blank (per-feature manual choice).
PROJECT_CONTRACT_FREQUENCY_MAP = {
	"Weekly": "Weekly",
	"Bi-Weekly": "Bi-Weekly",
	"Monthly": "Monthly",
	"Quarterly": "Quarterly",
}

MONTHS = [
	"January", "February", "March", "April", "May", "June",
	"July", "August", "September", "October", "November", "December",
]


class SapphireMaintenanceContract(Document):
	def validate(self):
		self._check_single_active_contract()
		if self.status == "Active" and not self.sales_order:
			frappe.msgprint(
				_("No Sales Order is linked — per-visit invoicing will fall back to the project's Maintenance Sales Order."),
				indicator="orange",
				alert=True,
			)

	def _check_single_active_contract(self):
		"""The scheduler and visit forms resolve "the" Active contract for a
		project, so two Active contracts on one project would be ambiguous."""
		if self.status != "Active" or not self.project:
			return
		other = frappe.db.exists(
			"Sapphire Maintenance Contract",
			{"project": self.project, "status": "Active", "name": ["!=", self.name]},
		)
		if other:
			frappe.throw(
				_("Project {0} already has an Active Maintenance Contract ({1}). Expire or cancel it first.").format(
					self.project, other
				)
			)


def get_active_contract(project):
	"""Return the Active Sapphire Maintenance Contract doc for a Project, or None."""
	if not project:
		return None
	name = frappe.db.get_value(
		"Sapphire Maintenance Contract", {"project": project, "status": "Active"}, "name"
	)
	return frappe.get_doc("Sapphire Maintenance Contract", name) if name else None


def _month_or_default(value, default):
	"""Normalise a free-text month (Project Contract stores Data) to a Select option."""
	value = (value or "").strip().capitalize()
	return value if value in MONTHS else default


def _append_features_from_sales_order(doc, so_name):
	"""Add one covered-feature row per Sales Order Item carrying a water feature."""
	existing = {row.serial_no for row in doc.get("covered_features", [])}
	items = frappe.get_all(
		"Sales Order Item",
		filters={"parent": so_name, "custom_serial_no": ["is", "set"]},
		fields=["name", "custom_serial_no", "custom_maintenance_frequency", "custom_next_predictive_visit"],
		order_by="idx",
	)
	for item in items:
		if item.custom_serial_no in existing:
			continue
		doc.append(
			"covered_features",
			{
				"serial_no": item.custom_serial_no,
				"frequency": item.custom_maintenance_frequency,
				"next_visit_date": item.custom_next_predictive_visit or nowdate(),
				"sales_order_item": item.name,
			},
		)
		existing.add(item.custom_serial_no)


@frappe.whitelist()
def make_contract_from_sales_order(source_name):
	"""Map a submitted Sales Order into a draft Maintenance Contract.

	Whitelisted; called by the Sales Order form's "Create > Maintenance
	Contract" button via ``frappe.model.open_mapped_doc``. Water-feature item
	rows become ``covered_features``; a Signed maintenance-type Project
	Contract for the same project is linked when one exists.
	"""
	so = frappe.get_doc("Sales Order", source_name)
	doc = frappe.new_doc("Sapphire Maintenance Contract")
	doc.customer = so.customer
	doc.project = so.project
	doc.sales_order = so.name
	doc.start_date = so.transaction_date
	_append_features_from_sales_order(doc, so.name)

	if so.project:
		project_contract = frappe.db.get_value(
			"Project Contract",
			{"project": so.project, "template_key": "maintenance", "status": "Signed", "docstatus": 1},
			"name",
		)
		if project_contract:
			_apply_project_contract(doc, frappe.get_doc("Project Contract", project_contract))

	return doc


@frappe.whitelist()
def make_contract_from_project_contract(source_name):
	"""Map a Signed Maintenance Services Agreement into a draft Maintenance Contract.

	Whitelisted; called by the Project Contract form's "Create > Maintenance
	Contract" button via ``frappe.model.open_mapped_doc``. Pulls the legal
	terms (frequency, invoicing cadence, start date, seasonal options) and, if
	the project has a submitted Maintenance Sales Order, links it and maps its
	water-feature items into ``covered_features``.
	"""
	contract = frappe.get_doc("Project Contract", source_name)
	if contract.template_key != "maintenance":
		frappe.throw(_("{0} is not a Maintenance Services Agreement.").format(source_name))

	doc = frappe.new_doc("Sapphire Maintenance Contract")
	if contract.party_type == "Customer":
		doc.customer = contract.party
	doc.project = contract.project
	_apply_project_contract(doc, contract)

	if contract.project:
		so_name = frappe.db.get_value(
			"Sales Order",
			{"project": contract.project, "order_type": "Maintenance", "docstatus": 1},
			"name",
		)
		if so_name:
			doc.sales_order = so_name
			_append_features_from_sales_order(doc, so_name)

	return doc


def _apply_project_contract(doc, contract):
	"""Copy the legal agreement's operational terms onto the contract doc."""
	doc.project_contract = contract.name
	doc.start_date = doc.start_date or contract.agreement_start_date or contract.contract_date
	doc.invoicing_frequency = contract.invoicing_frequency or "Per Visit"

	default_frequency = PROJECT_CONTRACT_FREQUENCY_MAP.get(contract.visit_frequency)
	if default_frequency:
		for row in doc.get("covered_features", []):
			if not row.frequency:
				row.frequency = default_frequency

	included = {row.option_key for row in contract.get("service_options", []) if row.included}
	existing = {row.visit_label for row in doc.get("seasonal_visits", [])}
	if ("startup" in included or "package" in included) and "Seasonal Startup" not in existing:
		doc.append(
			"seasonal_visits",
			{
				"visit_label": "Seasonal Startup",
				"target_month": _month_or_default(contract.startup_month, "April"),
			},
		)
	if ("winterization" in included or "package" in included) and "Winterization" not in existing:
		doc.append(
			"seasonal_visits",
			{
				"visit_label": "Winterization",
				"target_month": _month_or_default(contract.winterization_month, "October"),
			},
		)
