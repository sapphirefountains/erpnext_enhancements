"""Seed the Email Template "Planner Date Confirmation" (Project Planner Phase 5, P5.4).

The customer date confirmation (``project_enhancements/customer_confirmations.py``) is worded by
this Email Template so Nik can design the message in the Desk without a release. This patch
creates it once with the module's built-in default, and **only when it does not exist**: once it
is there it is Nik's, and a later migrate must never put the default wording back over his edits.
Renaming or deleting it is safe too: the module then falls back to the same built-in default, and
the preview says so.

The template is HTML (``use_html``), because a Text Editor field rewrites what it is given and
would mangle the Jinja ``{% if %}`` blocks. It is content only -- paragraphs, no container, no
width, no colors: the shared email shell (``email_style.wrap``) adds the letterhead, as for every
email this app sends (``tests/test_email_design.py``).

Nothing is sent by seeding it: Project Planner Settings ``customer_date_confirmations`` stays off.
Insert-only, cannot raise (a failure is logged), safe twice.
"""

import frappe

from erpnext_enhancements.project_enhancements.customer_confirmations import (
	DEFAULT_RESPONSE,
	DEFAULT_SUBJECT,
	TEMPLATE,
)


def execute():
	try:
		if not frappe.db.exists("DocType", "Email Template") or frappe.db.exists("Email Template", TEMPLATE):
			return
		frappe.get_doc(
			{
				"doctype": "Email Template",
				"subject": DEFAULT_SUBJECT,
				"use_html": 1,
				"response_html": DEFAULT_RESPONSE,
			}
		).insert(ignore_permissions=True, set_name=TEMPLATE)
	except Exception:
		frappe.log_error(
			title="Seed Planner Date Confirmation template",
			message=(
				"Could not create the Email Template 'Planner Date Confirmation'. Customer date "
				"confirmations use the built-in wording until it exists.\n\n" + frappe.get_traceback()
			),
		)
