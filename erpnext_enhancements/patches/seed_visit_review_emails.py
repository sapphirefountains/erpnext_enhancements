"""Seed ERPNext Enhancements Settings → Visit Review Emails with ``service_repair@`` (v1.591.0).

The visit review digest (``sapphire_maintenance/review_digest.py``) emails every answer on a
visit to this list when the visit is sent for review. Nik asked for the list to be a setting
rather than a fixed address. ``service_repair@sapphirefountains.com`` is where Lisa reviews
visits before billing, so the list starts with it.

A Table field has no ``default``, so a new one is empty on every existing site. An empty list
means "send the review copy to nobody", which is a choice somebody may make on purpose later.
So this seeds the address once, here, and the empty-means-nobody rule is never second-guessed
in code.

**It writes only when the table has no rows at all.** Once anybody has saved the table, empty
or not, that is their answer: ``tabPatch Log`` records this patch, so it never runs again.

**It inserts the child row directly and never saves the Single.** Saving ERPNext Enhancements
Settings runs its controller's ``validate`` over 189 fields this patch does not touch. A patch
that raises aborts ``bench migrate``, which on this repo is the deploy (CLAUDE.md).
"""

import frappe

SETTINGS = "ERPNext Enhancements Settings"
CHILD = "Project Reminder Email"
FIELD = "visit_review_emails"
DEFAULT = "service_repair@sapphirefountains.com"


def execute():
	if not (frappe.db.exists("DocType", SETTINGS) and frappe.db.exists("DocType", CHILD)):
		return
	try:
		if frappe.db.count(CHILD, {"parent": SETTINGS, "parenttype": SETTINGS, "parentfield": FIELD}):
			return
		row = frappe.new_doc(CHILD)
		row.update(
			{"parent": SETTINGS, "parenttype": SETTINGS, "parentfield": FIELD, "idx": 1, "email": DEFAULT}
		)
		row.db_insert()
		frappe.clear_document_cache(SETTINGS, SETTINGS)
	except Exception:
		# A missing seed costs one email list somebody can fill in by hand; an aborted
		# migrate costs the deploy.
		frappe.log_error(title="Visit review emails not seeded", message=frappe.get_traceback())
		return
	print(f"[erpnext_enhancements] visit review emails seeded with {DEFAULT}")
