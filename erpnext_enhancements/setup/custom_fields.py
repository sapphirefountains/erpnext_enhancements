"""Idempotent custom-field provisioning for the contact/address directory UI.

Entry point :func:`create_primary_contact_fields` is registered in
``after_migrate`` (hooks.py), so it runs on every ``bench migrate``. It injects
the "Contacts & Addresses" tab — Primary Contact link, contact/address directory
HTML widgets, primary address + location map — onto the relevant party doctypes,
plus a "Comments" tab for Project / Master Project.

All creation goes through ``create_custom_fields(..., update=True)`` and existing
fields are skipped (the unified tabs additionally reconcile ``insert_after`` for
their own system-generated widget fields), so the functions are safe to re-run.
Manual, fixture-owned records (e.g. the Project Comments tab) are never updated
here — fixtures sync earlier in the migrate pipeline and own those values.
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_unified_tabs():
	"""Inject the shared Contacts/Addresses tab + widgets per the field matrix.

	The ``matrix`` decides, per doctype, whether the Contacts widgets and/or
	Address widgets are added; ``tab_map`` picks the host Tab Break (created if
	missing). Fields that already exist are left in place — only their
	``insert_after`` is corrected — making this re-runnable on every migrate.
	"""
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

		# Skip creation if the fieldname already exists (Standard or Custom).
		# Reconcile insert_after only for our own system-generated widget fields —
		# manual records are fixture-owned and must never be rewritten here.
		fields_to_create = []
		for field in fields:
			if meta.has_field(field["fieldname"]):
				filters = {"dt": doctype, "fieldname": field["fieldname"], "is_system_generated": 1}
				if frappe.db.exists("Custom Field", filters):
					frappe.db.set_value("Custom Field", filters, "insert_after", field["insert_after"])
				continue
			fields_to_create.append(field)

		if fields_to_create:
			create_custom_fields({doctype: fields_to_create}, update=True)


def get_last_tab_fieldname(doctype):
	"""Return the fieldname of the doctype's last Tab Break (or None)."""
	meta = frappe.get_meta(doctype)
	tabs = [f.fieldname for f in meta.fields if f.fieldtype == "Tab Break"]
	if tabs:
		return tabs[-1]
	return None


def create_primary_contact_fields():
	"""``after_migrate`` entry point: provision the directory + comments tabs."""
	create_unified_tabs()
	create_comments_tab("Project")
	create_comments_tab("Master Project")


def create_opportunity_winloss_fields():
	"""``after_migrate`` entry point: loss reason capture on Opportunity.

	Two custom fields shown only when the Opportunity is Lost (depends_on
	status), feeding the Sales loss KPIs + the Loss Reasons chart. Idempotent —
	existing fields are skipped. The Select options are safe to edit later
	(they're just option lists); validation that requires a reason on close lives
	in ``script_migrations.opportunity.validate_close_reason``.

	NOTE (v1.149.0): the ``custom_won_reason`` field was removed — winning an
	Opportunity no longer captures or requires a reason. Existing sites drop the
	leftover field via ``patches.remove_opportunity_won_reason``.
	"""
	if not frappe.db.exists("DocType", "Opportunity"):
		return

	meta = frappe.get_meta("Opportunity")
	fields = [
		{
			"fieldname": "custom_lost_reason",
			"label": "Lost Reason",
			"fieldtype": "Select",
			"options": "\nPrice\nCompetitor\nNo Budget\nTiming\nNo Decision\nOther",
			"insert_after": "status",
			"depends_on": "eval:doc.status=='Lost'",
		},
		{
			"fieldname": "custom_lost_competitor",
			"label": "Lost To (Competitor)",
			"fieldtype": "Data",
			"insert_after": "custom_lost_reason",
			"depends_on": "eval:doc.status=='Lost' && doc.custom_lost_reason=='Competitor'",
		},
	]
	fields_to_create = [f for f in fields if not meta.has_field(f["fieldname"])]
	if fields_to_create:
		create_custom_fields({"Opportunity": fields_to_create}, update=True)


def create_comments_tab(doctype):
	"""Add a "Comments" tab hosting the Comments-app HTML widget to a doctype.

	Insert-only: fields that already exist are never touched. The Project pair
	is a manual customization owned by the fixtures (which sync before this
	after_migrate hook runs); rewriting it here with ``update=True`` would
	silently override the fixture on every migrate. This only provisions
	missing fields (fresh installs, Master Project).
	"""
	if not frappe.db.exists("DocType", doctype):
		return

	meta = frappe.get_meta(doctype)
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

	fields_to_create = [f for f in fields if not meta.has_field(f["fieldname"])]
	if fields_to_create:
		create_custom_fields({doctype: fields_to_create}, update=True)


def get_last_field_before_tabs(doctype):
	"""Pick the field to insert the Comments tab after (last non-tab field)."""
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
