# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Desk RPC for triaging fountain-move requests.

These are **role-gated only, never feature-flag gated.** Retry, Mark as Spam and
Not Spam must keep working when the public form is switched off — otherwise
turning the form off to stop an abuse wave would also freeze the backlog of
genuine requests that arrived before it, which is exactly backwards.
"""

import frappe
from frappe import _
from frappe.utils import cint

#: Who may triage. Sales User is deliberately absent: retry re-runs a job that
#: creates CRM masters, and marking something spam hides a real customer.
TRIAGE_ROLES = ("System Manager", "Sales Manager")


@frappe.whitelist()
def retry_conversion(docname):
	"""Re-run conversion for one request.

	Safe to press repeatedly: the engine records each record it creates and skips
	the steps that already produced one, so a retry resumes rather than
	duplicating. ``force`` resets the attempt counter's veto — a human pressing
	the button has decided the cause is fixed.
	"""
	frappe.only_for(TRIAGE_ROLES)

	status = frappe.db.get_value("Fountain Move Request", docname, "status")
	if status == "Converted":
		frappe.throw(_("This request has already been converted."))
	if status == "Converting":
		frappe.throw(_("This request is being converted right now. Give it a moment."))

	frappe.db.set_value("Fountain Move Request", docname, "status", "Queued", update_modified=False)
	frappe.db.commit()

	frappe.enqueue(
		"erpnext_enhancements.crm_enhancements.fountain_move.conversion.run_conversion",
		queue="long",
		enqueue_after_commit=True,
		job_id=f"fmr-convert-{docname}",
		deduplicate=True,
		docname=docname,
		force=1,
	)
	return {"queued": True}


@frappe.whitelist()
def mark_spam(docname, reason=None):
	"""Quarantine a request. Creates nothing and stops any further conversion."""
	frappe.only_for(TRIAGE_ROLES)

	request = frappe.get_doc("Fountain Move Request", docname)
	if request.status == "Converted":
		frappe.throw(
			_("This request already created CRM records. Delete or merge those instead of marking it spam.")
		)

	request.db_set("status", "Spam", update_modified=False)
	request.db_set(
		"spam_reason", (reason or "Marked as spam in the desk.")[:500], update_modified=False
	)
	frappe.db.commit()
	return {"status": "Spam"}


@frappe.whitelist()
def mark_not_spam(docname):
	"""Release a false positive back into the queue.

	The honeypot and timing checks are heuristics; a customer using an aggressive
	password manager or a very fast autofill can trip them. Clearing the flags is
	part of releasing it, or conversion would immediately re-park it as spam.
	"""
	frappe.only_for(TRIAGE_ROLES)

	request = frappe.get_doc("Fountain Move Request", docname)
	if request.status != "Spam":
		frappe.throw(_("This request is not marked as spam."))

	request.db_set("honeypot_tripped", 0, update_modified=False)
	request.db_set("spam_reason", None, update_modified=False)
	if request.turnstile_verdict == "Failed":
		request.db_set("turnstile_verdict", "Not Checked", update_modified=False)
	request.db_set("status", "New", update_modified=False)
	frappe.db.commit()
	return {"status": "New"}


@frappe.whitelist()
def get_conversion_summary(docname):
	"""What this request produced — for the form's dashboard strip."""
	frappe.only_for((*TRIAGE_ROLES, "Sales User"))

	request = frappe.db.get_value(
		"Fountain Move Request",
		docname,
		[
			"status",
			"created_customer",
			"created_address",
			"created_contact",
			"created_lead",
			"created_opportunity",
			"reused_customer",
			"reused_contact",
			"reused_lead",
			"match_basis",
			"conversion_attempts",
		],
		as_dict=True,
	)
	if not request:
		frappe.throw(_("Request not found."))

	request["can_retry"] = request["status"] not in ("Converted", "Converting")
	request["attempts"] = cint(request.get("conversion_attempts"))
	return request
