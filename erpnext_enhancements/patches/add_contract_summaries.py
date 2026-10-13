"""Put the plain-language summary at the top of the live Rental and Owner agreements.

Nik, 2026-10-13: "a summary that summarizes what they've agreed to in simple terms and
language would be great." The summary is ``{{ summary() }}`` (built by
``project_enhancements/contract_summary.py`` from each contract's own figures), placed
right after the title. The maintenance agreement gets it with the rest of its 2026-10-13
terms (``update_maintenance_agreement_terms``); this does the Rental Agreement and the
Owner Contract.

``seed_contract_templates`` is insert-only, so the line has to be added to the live record.
The same never-guess rule as the other template patches: the line goes in only before the
template's first section heading, and only when that heading appears exactly once; a body
that already calls ``summary(`` is left alone, and anything else is logged and skipped.
Never raises: a patch that raises aborts ``bench migrate``, which is the deploy.
"""

import frappe

MARKER = "{{ summary() }}\n"

#: template key -> the first section heading, which the summary goes immediately before.
ANCHORS = {
	"rental": "<h1>RENTER &amp; RENTAL INFORMATION</h1>",
	"owner": "<h1>PROJECT &amp; OWNER INFORMATION</h1>",
}


def insert_summary(body, anchor):
	"""``(new_body, changed)``; unchanged when already there or when the heading is not unique."""
	if not body or "summary(" in body or body.count(anchor) != 1:
		return body, False
	return body.replace(anchor, MARKER + anchor, 1), True


def execute():
	from erpnext_enhancements.patches.update_maintenance_agreement_terms import (
		freeze_contract_copies,
		live_requests,
	)

	for key, anchor in ANCHORS.items():
		try:
			body = frappe.db.get_value("Contract Template", key, "body")
			if body is None:
				continue  # no such template on this site; the seed inserts the repo copy
			# The same two guards as the maintenance terms patch: a contract signed on paper
			# keeps what it said, and nobody is mid-signature when the text changes.
			freeze_contract_copies(key)
			pending = live_requests(key)
			if pending and "summary(" not in body:
				frappe.log_error(
					title="Contract summary not added yet",
					message=(
						f"Contract Template '{key}' has signing links out ({', '.join(pending)}); the summary "
						"was not added so nobody signs text they were not shown. Run "
						"erpnext_enhancements.patches.add_contract_summaries.execute again once they are "
						"signed or voided."
					),
				)
				continue
			new_body, changed = insert_summary(body, anchor)
			if not changed:
				if "summary(" not in (body or ""):
					frappe.log_error(
						title="Contract summary not added",
						message=(
							f"Contract Template '{key}' has been edited on this site, so the plain-language "
							"summary was not added. Put {{ summary() }} after the agreement's title by hand."
						),
					)
				continue
			frappe.db.set_value("Contract Template", key, "body", new_body, update_modified=False)
		except Exception:
			frappe.log_error(title=f"Contract summary patch failed: {key}", message=frappe.get_traceback())
	frappe.db.commit()
