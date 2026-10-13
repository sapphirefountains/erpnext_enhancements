"""Give the live Maintenance Services Agreement the Word copy's layout (v1.611.1).

Nik, 2026-10-13: "I need the printed agreement to match the DOCX better and be formatted
with bullet points and numbering etc." The template now marks each numbered clause
(``ct-clause`` with its number in a hanging column, the titled ones in bold), bullets the
lists under §2.1-§2.4, and indents the plan options, §9.1's term options and §4.3's
lettered payment clauses; the print format styles them. The wording does not change.

Same discipline as ``update_maintenance_agreement_terms``, whose helpers it uses:

* the body is replaced only when it is exactly one this repo shipped, the v1.609.0 body
  (should the earlier patch have been held back by a live signing link) or the v1.611.0
  body; a body edited on the site is logged and left alone;
* contracts signed on paper are frozen first, so their reprint keeps the layout they were
  signed with;
* nothing changes while a signing link for the template is out.

It does **not** reset the new terms on issued agreements as the earlier patch did: an
agreement issued since v1.611.0 was issued on those terms. Never raises.
"""

import frappe

from erpnext_enhancements.patches.update_maintenance_agreement_terms import (
	TEMPLATE_KEY,
	body_sha256,
	freeze_contract_copies,
	live_requests,
	shipped_body,
)

#: Shipped maintenance bodies this patch may replace.
REPLACEABLE_BODY_SHA256 = {
	# v1.609.0, in case update_maintenance_agreement_terms found a signing link out and waited.
	"4b77c564f827ccff3ab915a8cab6b582f72bb4493ff67ec730c4b0e07fba2967",
	# v1.611.0, the 2026-10-13 terms before the layout.
	"123c83c50d44a747c44817c12b132a33b83992d1753c6b8f1822dc2b079c3e40",
}


def decide(live_body, new_body):
	if live_body == new_body:
		return "current"
	if body_sha256(live_body) in REPLACEABLE_BODY_SHA256:
		return "replace"
	return "diverged"


def execute():
	try:
		_execute()
	except Exception:
		frappe.log_error(title="Maintenance agreement layout patch failed", message=frappe.get_traceback())


def _execute():
	live = frappe.db.get_value("Contract Template", TEMPLATE_KEY, "body")
	if live is None:
		return
	new_body = shipped_body()
	verdict = decide(live, new_body)
	if verdict == "current":
		return
	if verdict == "diverged":
		frappe.log_error(
			title="Maintenance agreement layout: template not updated",
			message=(
				"Contract Template 'maintenance' has been edited on this site, so the Word-copy layout "
				"(numbered clauses, bulleted lists) was not applied. Merge "
				"templates/contracts/maintenance_services_agreement.html into it by hand."
			),
		)
		return
	freeze_contract_copies(TEMPLATE_KEY)
	pending = live_requests(TEMPLATE_KEY)
	if pending:
		frappe.log_error(
			title="Maintenance agreement layout: template not updated yet",
			message=(
				"These signing links are still out: " + ", ".join(pending) + ". Run "
				"erpnext_enhancements.patches.refresh_maintenance_agreement_layout.execute again once they "
				"are signed or voided."
			),
		)
		return
	frappe.db.set_value("Contract Template", TEMPLATE_KEY, "body", new_body, update_modified=False)
	frappe.db.commit()
