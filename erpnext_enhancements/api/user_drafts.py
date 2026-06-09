"""Per-user form autosave drafts.

Whitelisted API behind the Desk form autosave in
``public/js/erpnext_enhancements.js`` (``save_draft`` / ``delete_draft``),
which persists unsaved edits into the "User Form Draft" DocType so they survive
reloads/crashes.

Security: drafts are keyed by ``frappe.session.user``; Guest sessions are
ignored. Writes use ``ignore_permissions=True`` (the draft store is the user's
own scratch space, not the underlying document).

Scheduler: ``cleanup_stale_drafts`` runs daily (hooks.py) to bulk-purge drafts
older than ``DRAFT_RETENTION_DAYS`` so the table doesn't grow unbounded.
"""

import frappe
import json

@frappe.whitelist()
def save_draft(ref_doctype, ref_name, form_data):
	"""
	Upsert a User Form Draft for the current user.
	"""
	if not ref_doctype or not ref_name:
		return

	user = frappe.session.user
	if user == "Guest":
		return

	# Check for existing draft
	existing_draft = frappe.db.get_value(
		"User Form Draft",
		{"ref_doctype": ref_doctype, "ref_name": ref_name, "user": user},
		"name"
	)

	if existing_draft:
		# Update
		doc = frappe.get_doc("User Form Draft", existing_draft)
		doc.form_data = form_data
		doc.save(ignore_permissions=True)
	else:
		# Create
		doc = frappe.get_doc({
			"doctype": "User Form Draft",
			"ref_doctype": ref_doctype,
			"ref_name": ref_name,
			"user": user,
			"form_data": form_data
		})
		doc.insert(ignore_permissions=True)

	return doc.name

@frappe.whitelist()
def delete_draft(ref_doctype, ref_name):
	"""
	Delete a User Form Draft for the current user.
	"""
	if not ref_doctype or not ref_name:
		return

	user = frappe.session.user
	if user == "Guest":
		return

	existing_draft = frappe.db.get_value(
		"User Form Draft",
		{"ref_doctype": ref_doctype, "ref_name": ref_name, "user": user},
		"name"
	)

	if existing_draft:
		frappe.delete_doc("User Form Draft", existing_draft, ignore_permissions=True)


# Drafts untouched for this many days are treated as stale and purged by the
# daily scheduled job below. They are an autosave safety net, not a record of
# intent — once a form has been left alone for a month the draft is almost
# certainly obsolete (saved, abandoned, or its parent deleted).
DRAFT_RETENTION_DAYS = 30


def cleanup_stale_drafts():
	"""Scheduled (daily): delete User Form Drafts not modified in DRAFT_RETENTION_DAYS.

	Without this the "User Form Draft" table grows without bound — autosave keeps
	inserting/refreshing rows that nothing ever removes once the work is finished.
	"""
	cutoff = frappe.utils.add_days(frappe.utils.nowdate(), -DRAFT_RETENTION_DAYS)
	stale = frappe.get_all(
		"User Form Draft", filters={"modified": ["<", cutoff]}, pluck="name"
	)
	if not stale:
		return

	# User Form Draft is a flat container (no child tables, no on_trash hooks), so a
	# bulk delete is safe and far cheaper than deleting documents one at a time.
	frappe.db.delete("User Form Draft", {"name": ["in", stale]})
	frappe.db.commit()
	frappe.logger().info(f"cleanup_stale_drafts: removed {len(stale)} stale User Form Draft(s)")
