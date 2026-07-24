"""Rename the "Accounts" Role Profile to "Accounting" (v1.169.2).

This site UI-relabels the Customer DocType to "Accounts", so a Role Profile
literally named "Accounts" reads as customer management when it actually bundles
the accounting roles (Accounts Manager + Accounts User — GL / AP / AR authority).
The rename removes the ambiguity (WI-011 finding). Idempotent; `rename_doc` also
repoints any `User.role_profile_name` links, and we sync the `role_profile` field
to the new name (autoname = field:role_profile). Runs in post_model_sync, i.e.
before fixture sync, so `role_profile.json` then updates the already-renamed doc
instead of re-creating "Accounts".
"""

import frappe


def execute():
	if not frappe.db.exists("Role Profile", "Accounts"):
		return
	if frappe.db.exists("Role Profile", "Accounting"):
		return
	frappe.rename_doc("Role Profile", "Accounts", "Accounting", force=True)
	frappe.db.set_value("Role Profile", "Accounting", "role_profile", "Accounting")
