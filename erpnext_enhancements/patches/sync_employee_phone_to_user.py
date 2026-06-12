"""One-time backfill: copy each active Employee's Cell Number onto the linked
User's ``phone`` field. Going forward the Employee ``on_update`` doc_event
(``sync_contact.sync_employee_phone_to_user``) keeps them in sync — this just
covers Employees whose record won't be re-saved any time soon, so "Call via
Triton" can resolve every rep's number immediately after deploy.
"""

import frappe


def execute():
	employees = frappe.get_all(
		"Employee",
		filters={"user_id": ["is", "set"], "cell_number": ["is", "set"]},
		fields=["name", "user_id", "cell_number"],
	)
	for emp in employees:
		cell = (emp.cell_number or "").strip()
		if not cell or not frappe.db.exists("User", emp.user_id):
			continue
		if (frappe.db.get_value("User", emp.user_id, "phone") or "") != cell:
			frappe.db.set_value("User", emp.user_id, "phone", cell, update_modified=False)
