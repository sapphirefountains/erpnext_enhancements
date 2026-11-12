"""Seed the Email Template "Supplier Return Request" (Supplier Returns, v1.807.0).

The return request emailed to a supplier (``inventory_enhancements/supplier_returns.send_request``)
is worded by this Email Template, so the buyer can change the message in the Desk without a
release. This patch creates it once with the built-in wording, and **only when it does not
exist**: once it is there it belongs to whoever edits it, and a later migrate must never put the
default back over their words. Renaming or deleting it is safe: the Send dialog then shows the
same built-in wording and says the template is missing.

The template is HTML (``use_html``), because a Text Editor field rewrites what it is given and
would mangle the Jinja ``{% if %}`` blocks. It is content only -- paragraphs and the
``{{ lines_table }}`` slot, no container, no width, no colors: the shared email shell
(``email_style.wrap``) adds the letterhead, as for every email this app sends. The slot is where
the table of items goes; the server builds that table from the return's own lines.

**Nothing is sent by seeding it.** A return request goes only when a person clicks Send in the
Supplier Return form's dialog. Insert-only, cannot raise (a failure is logged), safe twice, and it
prints what it did.
"""

import frappe

from erpnext_enhancements.inventory_enhancements.supplier_return_rules import (
	DEFAULT_BODY,
	DEFAULT_SUBJECT,
	EMAIL_TEMPLATE,
)


def execute():
	try:
		if not frappe.db.exists("DocType", "Email Template"):
			print("seed_supplier_return_request_template: no Email Template doctype, nothing done")
			return
		if frappe.db.exists("Email Template", EMAIL_TEMPLATE):
			print(f"seed_supplier_return_request_template: {EMAIL_TEMPLATE!r} already exists, left as it is")
			return
		frappe.get_doc(
			{
				"doctype": "Email Template",
				"subject": DEFAULT_SUBJECT,
				"use_html": 1,
				"response_html": DEFAULT_BODY,
			}
		).insert(ignore_permissions=True, set_name=EMAIL_TEMPLATE)
		print(f"seed_supplier_return_request_template: created {EMAIL_TEMPLATE!r}")
	except Exception:
		frappe.log_error(
			title="Seed Supplier Return Request template",
			message=(
				f"Could not create the Email Template {EMAIL_TEMPLATE!r}. Return requests use the "
				"built-in wording until it exists.\n\n" + frappe.get_traceback()
			),
		)
		print("seed_supplier_return_request_template: failed, logged; built-in wording is used")
