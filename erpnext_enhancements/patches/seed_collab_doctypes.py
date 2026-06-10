"""One-time migration patch (post_model_sync; listed in patches.txt).

Seeds the live-collab settings on ERPNext Enhancements Settings with the
launch allowlist (``api.collab.DEFAULT_COLLAB_DOCTYPES``) and switches the
feature on, so moving the allowlist from code constants to settings is
behavior-neutral for existing sites. Idempotent: skips entirely if any
``collab_doctypes`` rows already exist (an admin-curated list is never
overwritten), and skips doctypes missing on the site.
"""

import frappe

from erpnext_enhancements.api.collab import DEFAULT_COLLAB_DOCTYPES


def execute():
	"""Seed collab_enabled + collab_doctypes with the launch allowlist."""
	settings = frappe.get_single("ERPNext Enhancements Settings")
	if settings.get("collab_doctypes"):
		return

	for doctype in DEFAULT_COLLAB_DOCTYPES:
		if frappe.db.exists("DocType", doctype):
			settings.append("collab_doctypes", {"document_type": doctype})
	settings.collab_enabled = 1
	settings.save(ignore_permissions=True)
