import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_unified_tabs():
	# Matrix for field injection:
	# Customer: Contacts (Y), Addresses (Y)
	# Supplier: Contacts (Y), Addresses (Y)
	# Contact: Contacts (N), Addresses (Y)
	# Opportunity: Contacts (Y), Addresses (Y)
	# Project: Contacts (Y), Addresses (Y)
	# Master Project: Contacts (Y), Addresses (Y)

	matrix = {
		"Customer": {"contacts": True, "addresses": True},
		"Supplier": {"contacts": True, "addresses": True},
		"Contact": {"contacts": False, "addresses": True},
		"Opportunity": {"contacts": True, "addresses": True},
		"Project": {"contacts": True, "addresses": True},
		"Master Project": {"contacts": True, "addresses": True},
	}

	tab_map = {
		"Project": "custom_contacts__addresses",
		"Master Project": "custom_contacts__addresses",
		"Opportunity": "contact_info",
		"Customer": "contacts_and_addresses_tab",
		"Supplier": "contacts_and_addresses_tab",
	}

	for doctype, config in matrix.items():
		if not frappe.db.exists("DocType", doctype):
			continue

		meta = frappe.get_meta(doctype)
		target_tab = tab_map.get(doctype)

		if not target_tab and doctype == "Contact":
			# Try to find the 'Details' tab for Contact
			target_tab = next((f.fieldname for f in meta.fields if f.fieldtype == "Tab Break" and f.label == "Details"), None)

		fields = []
		insert_after_contacts = None

		# Handle Tab Injection
		if target_tab:
			if not meta.has_field(target_tab):
				if doctype == "Master Project":
					last_tab = "tasks_html"
				else:
					last_tab = get_last_tab_fieldname(doctype)
				fields.append({
					"fieldname": target_tab,
					"label": "Contacts & Addresses",
					"fieldtype": "Tab Break",
					"insert_after": last_tab
				})
				insert_after_contacts = target_tab
			else:
				insert_after_contacts = target_tab
		else:
			insert_after_contacts = get_last_tab_fieldname(doctype)
		prev_field = insert_after_contacts

		# IF Contacts (Y)
		if config["contacts"]:
			fields.extend([
				{
					"fieldname": "primary_contact",
					"label": "Primary Contact",
					"fieldtype": "Link",
					"options": "Contact",
					"insert_after": prev_field
				},
				{
					"fieldname": "section_break_contacts",
					"label": "Contact Directory",
					"fieldtype": "Section Break",
					"insert_after": "primary_contact"
				},
				{
					"fieldname": "contact_list_html",
					"label": "Contact List HTML",
					"fieldtype": "HTML",
					"insert_after": "section_break_contacts"
				}
			])
			prev_field = "contact_list_html"

		# IF Addresses (Y)
		if config["addresses"]:
			fields.extend([
				{
					"fieldname": "section_break_map",
					"label": "Location",
					"fieldtype": "Section Break",
					"insert_after": prev_field
				},
				{
					"fieldname": "primary_address",
					"label": "Primary Address",
					"fieldtype": "Link",
					"options": "Address",
					"insert_after": "section_break_map"
				},
				{
					"fieldname": "location_map_col_break",
					"fieldtype": "Column Break",
					"insert_after": "primary_address"
				},
				{
					"fieldname": "location_map_html",
					"label": "Location Map HTML",
					"fieldtype": "HTML",
					"insert_after": "location_map_col_break"
				},
				{
					"fieldname": "section_break_address_list",
					"label": "Address Directory",
					"fieldtype": "Section Break",
					"insert_after": "location_map_html"
				},
				{
					"fieldname": "address_list_html",
					"label": "Address List HTML",
					"fieldtype": "HTML",
					"insert_after": "section_break_address_list"
				}
			])

		# Skip creation if the fieldname already exists (Standard or Custom)
		# But update insert_after for specific fields if they are custom
		fields_to_create = []
		for field in fields:
			if meta.has_field(field["fieldname"]):
				if frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": field["fieldname"]}):
					frappe.db.set_value("Custom Field", {"dt": doctype, "fieldname": field["fieldname"]}, "insert_after", field["insert_after"])
				continue
			fields_to_create.append(field)

		if fields_to_create:
			create_custom_fields({doctype: fields_to_create}, update=True)


def get_last_tab_fieldname(doctype):
	meta = frappe.get_meta(doctype)
	tabs = [f.fieldname for f in meta.fields if f.fieldtype == "Tab Break"]
	if tabs:
		return tabs[-1]
	return None


def create_primary_contact_fields():
	create_unified_tabs()
	create_comments_tab("Project")
	create_comments_tab("Master Project")


def create_comments_tab(doctype):
	if not frappe.db.exists("DocType", doctype):
		return

	fields = [
		{
			"fieldname": "custom_comments_tab",
			"label": "Comments",
			"fieldtype": "Tab Break",
			"insert_after": get_last_field_before_tabs(doctype)
		},
		{
			"fieldname": "custom_comments_field",
			"label": "Comments App",
			"fieldtype": "HTML",
			"insert_after": "custom_comments_tab"
		}
	]

	create_custom_fields({doctype: fields}, update=True)


def get_last_field_before_tabs(doctype):
	if doctype == "Master Project":
		return "address_list_html"

	meta = frappe.get_meta(doctype)
	for field in meta.fields:
		if field.fieldtype == "Tab Break":
			# Return the field before the first tab if possible, 
			# but usually we just want to insert before other tabs or at the end of the first section.
			# For simplicity, we'll return the last field that is NOT a tab.
			pass

	non_tab_fields = [f.fieldname for f in meta.fields if f.fieldtype != "Tab Break"]
	if non_tab_fields:
		return non_tab_fields[-1]
	return None
