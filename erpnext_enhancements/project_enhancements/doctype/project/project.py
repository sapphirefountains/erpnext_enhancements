"""Custom scripts for the Project doctype to enhance list view functionality."""

import frappe
from frappe.utils import nowdate, strip_html


@frappe.whitelist()
def get_project_brief_data(project_name):
	"""Collect the data shown on the printable Project Brief.

	The brief mirrors the paper template Sapphire uses (the scanned
	"Project Brief" form). This is a *display-only* feature: every value is
	pulled from fields that already exist on the Project (and, where helpful,
	the linked Customer's default Address/Contact). Fields the template asks
	for but which have no source in the system (kick-off date, lien notice
	date, contract type, fee/contingency, etc.) are returned blank so the
	brief renders as a fillable form, just like the printed original.

	Args:
	    project_name (str): The Project document name (e.g. "PROJ-0567").

	Returns:
	    dict: Brief fields keyed for the client-side renderer.
	"""
	doc = frappe.get_doc("Project", project_name)

	contract_value = doc.get("custom_project_dollar_amount") or 0

	# Project notes is a Text Editor (HTML); strip tags for a clean brief.
	description = doc.get("custom_project_description") or strip_html(doc.get("notes") or "")

	data = {
		"project_number": doc.name,
		"project_title": doc.get("project_name") or doc.name,
		"brief_date": nowdate(),
		"contract_value": contract_value,
		"contract_amount": contract_value,
		"start_date": doc.get("expected_start_date"),
		"completion_date": doc.get("expected_end_date"),
		"description": (description or "").strip(),
		"pm": doc.get("custom_project_owner") or "",
		"tech_lead": doc.get("custom_technical_lead") or "",
		# No native Project source — left blank to be filled in on the printout.
		"kickoff_meeting_date": "",
		"prelim_lien_notice_date": "",
		"owner": doc.get("customer") or "",
		"owner_contact": "",
		"general_contractor": "",
		"gc_contact": "",
		"address_lines": [],
	}

	customer = doc.get("customer")
	if customer:
		data["address_lines"] = _customer_address_lines(customer)
		data["owner_contact"] = _customer_primary_contact(customer)

	return data


def _customer_address_lines(customer):
	"""Return the customer's default address as a list of display lines."""
	try:
		from frappe.contacts.doctype.address.address import get_default_address

		address_name = get_default_address("Customer", customer)
		if not address_name:
			return []
		addr = frappe.get_doc("Address", address_name)
		city_line = ", ".join([p for p in [addr.city, addr.state] if p])
		if addr.pincode:
			city_line = (city_line + " " + addr.pincode).strip()
		lines = [addr.address_line1, addr.address_line2, city_line, addr.country]
		return [line for line in lines if line]
	except Exception:
		# Address resolution is best-effort; never block the brief over it.
		return []


def _customer_primary_contact(customer):
	"""Return the customer's default contact as a display name, if any."""
	try:
		from frappe.contacts.doctype.contact.contact import get_default_contact

		contact_name = get_default_contact("Customer", customer)
		if not contact_name:
			return ""
		contact = frappe.get_doc("Contact", contact_name)
		full = " ".join([p for p in [contact.first_name, contact.last_name] if p]).strip()
		return full or contact_name
	except Exception:
		return ""


@frappe.whitelist()
def get_project_grouping_option():
	"""Provides list view settings to group projects by project type.

	This function is called from a client-side script to dynamically configure
	the list view settings for the Project doctype. It is used to set the
	default grouping behavior.

	Returns:
	    dict: A dictionary specifying that projects should be grouped by the
	        `project_type` field. Example: `{'group_by': 'project_type'}`.
	"""
	# These print() statements are for debugging. They will appear in your
	# terminal window where the `bench start` command is running.
	print("--- SERVER SCRIPT DEBUG ---")
	print("Python function 'get_project_grouping_option' was called successfully.")

	settings = {"group_by": "project_type"}

	# This log shows the exact data being sent back to the browser.
	print(f"Returning settings: {settings}")
	print("--------------------------")

	# This sends the `settings` dictionary back to the browser as the response.
	return settings
