# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the public Service Call approval page (``/service-call-approval``, v1.778.0).

**The filename matters.** Frappe imports a page's controller by the template's basename with
hyphens turned into underscores, so ``service-call-approval.html`` needs
``service_call_approval.py`` (``scripts/check_www_controllers.py`` fails the build on a hyphen).

The client opens this from the email or text ``sapphire_maintenance/service_call_approval.py``
sends: a plain-language summary of the job, the service type, the rate, short terms, a typed
name and Approve, or Decline with a reason. The link's token is the only authority. An unknown
and a malformed token render identically; expired, replaced and already-answered links, which
the visitor already holds, are told apart as a kindness.

The page adds no history entries: one screen, and answering swaps the form for a thank-you in
place, so the browser's Back and Forward keep doing what they did.
"""

import frappe

from erpnext_enhancements.sapphire_maintenance import service_call_approval as approval
from erpnext_enhancements.utils.deploy import get_deploy_version

no_cache = 1


def get_context(context):
	context.no_cache = 1
	context.deploy_version = get_deploy_version()

	ref = frappe.form_dict.get("ref")
	request = approval.resolve(ref)
	call = _call(request)
	state = _state_for(request, call)

	context.state = state
	context.summary = approval.page_summary(call) if state == "open" else None
	context.boot = {
		# A Guest session carries no CSRF token and frappe does not check one for it; a signed-in
		# staff member opening the link does, so the session's existing token is emitted (never
		# frappe.sessions.get_csrf_token(), which mints one a Guest session never persists).
		"csrf_token": (getattr(frappe.session, "data", None) or {}).get("csrf_token") or "",
		"state": state,
		"ref": ref or "",
	}
	context.expires_on = frappe.utils.format_datetime(request.expires_on) if request and request.expires_on else ""
	context.contact_phone = _contact_phone()

	if state == "open":
		frappe.enqueue(
			"erpnext_enhancements.sapphire_maintenance.service_call_approval.mark_viewed",
			queue="short",
			request_name=request.name,
		)
	return context


def _call(request):
	if not request:
		return None
	try:
		return frappe.get_doc(approval.CALL, request.service_call)
	except Exception:
		return None


def _state_for(request, call):
	if not request or not call:
		return "invalid"
	if request.status == "Approved" or call.get("approved_on"):
		return "approved"
	if request.status == "Declined":
		return "declined"
	if request.status == "Superseded":
		return "replaced"
	if approval.effective_status(request) == "Expired":
		return "expired"
	if call.get("status") not in ("Sent for Approval", "Requested") or not approval.is_live(request):
		return "invalid"
	return "open"


def _contact_phone():
	try:
		from erpnext_enhancements.crm_enhancements.fountain_move import get_contact_phone

		return get_contact_phone()
	except Exception:
		return ""
