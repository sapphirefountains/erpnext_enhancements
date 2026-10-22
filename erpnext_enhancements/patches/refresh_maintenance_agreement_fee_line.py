"""Give the live Maintenance Services Agreement its per-period Service Fee line (v1.724.0).

Nik, 2026-10-22: "add a monthly (per-period) fee line to the maintenance agreement template, filled
from the linked maintenance contract's Recurring Amount when set". Three changes to Service Plan
Selection, nothing else in the body:

* a **Service Fee** line ("Service Fee: $1,490.00 per month, billed May through September"), printed
  only when the render context has one (``project_enhancements.maintenance_fee``);
* the **Annual Service Fee Total** line is left out when a Service Fee prints and no annual total is
  set, instead of a blank line beside a stated fee; with neither, the paper form keeps its blank;
* a ticked **Standard Maintenance** with no per-visit price reads "base rate in the Service Fee below"
  when there is one, instead of a blank rate.

An agreement with no Recurring Amount on its maintenance contract and no Annual Fee renders exactly as
before.

Same discipline as ``refresh_maintenance_agreement_layout``, whose helpers it shares:

* the body is replaced only when it is exactly one this repo shipped (SHA-256): the v1.611.1 body prod
  carried when this was written (checked 2026-10-22), or the v1.611.0 body should the layout refresh
  still be waiting on a signing link; a body edited on the site is logged and left alone;
* contracts signed on paper are frozen first, so their reprint keeps the agreement they were signed on
  (on prod the one signed maintenance agreement, SF-MAINT-2026-0001, was frozen in v1.611.0 already);
* nothing changes while a signing link for the template is out, since the executed copy is re-rendered
  at signing.

**A wait is retried, not forgotten.** Patch Log records this patch as done even when it waited, so
``retry_after_migrate`` (``hooks.after_migrate``) runs the same guarded swap after every migrate. It is
silent: a body already current, a body edited on the site and a link still out each return without
logging, since the patch's own run logged them once. It only ever installs the body this release shipped
(``FEE_LINE_BODY_SHA256``), so once a later release changes the template file, the retry does nothing
and that release's own patch owns the swap. Until the body is swapped, agreements print no Service Fee
and no summary row: the render context gives a fee only to a body with the line
(``maintenance_fee.template_prints_fee``).

Resets no terms on any contract. Never raises, and a second run finds the body current and does nothing.
"""

import frappe

from erpnext_enhancements.patches.update_maintenance_agreement_terms import (
	TEMPLATE_KEY,
	body_sha256,
	freeze_contract_copies,
	live_requests,
	shipped_body,
)

#: Shipped maintenance bodies this patch may replace. Not v1.609.0: that body predates the 2026-10-13
#: terms, whose patch resets issued agreements before it swaps, so a site still on it is left to that patch.
REPLACEABLE_BODY_SHA256 = {
	# v1.611.0, in case the layout refresh found a signing link out and waited.
	"123c83c50d44a747c44817c12b132a33b83992d1753c6b8f1822dc2b079c3e40",
	# v1.611.1, the Word-copy layout: prod's body byte for byte on 2026-10-22.
	"c2baa1612d4d819f76e6becbdbde50b26d85318a62874440e6a2f3cea2609ba6",
}

#: The body this release ships (v1.724.0), the only one ``retry_after_migrate`` installs.
FEE_LINE_BODY_SHA256 = "01106661762f53bac604b1671576822e9ddcdef94a79b30a9434e3a9d87a2569"


def decide(live_body, new_body):
	"""``"replace"``, ``"current"`` or ``"diverged"``: what to do with the live body."""
	if live_body == new_body:
		return "current"
	if body_sha256(live_body) in REPLACEABLE_BODY_SHA256:
		return "replace"
	return "diverged"


def execute():
	try:
		_execute()
	except Exception:
		# A patch that raises aborts `bench migrate`, which is the deploy.
		frappe.log_error(title="Maintenance agreement fee line patch failed", message=frappe.get_traceback())


def retry_after_migrate():
	"""``after_migrate``: finish a swap the patch had to leave while a signing link was out. Never raises."""
	try:
		_execute(quiet=True)
	except Exception:
		frappe.log_error(title="Maintenance agreement fee line retry failed", message=frappe.get_traceback())


def _execute(quiet=False):
	live = frappe.db.get_value("Contract Template", TEMPLATE_KEY, "body")
	if live is None:
		return  # no maintenance template on this site; the seed inserts the new one
	new_body = shipped_body()
	if quiet and body_sha256(new_body) != FEE_LINE_BODY_SHA256:
		return  # a later release changed the template; its own patch owns the swap
	verdict = decide(live, new_body)
	if verdict == "current":
		return
	if verdict == "diverged":
		if quiet:
			return
		frappe.log_error(
			title="Maintenance agreement fee line: template not updated",
			message=(
				"Contract Template 'maintenance' has been edited on this site, so the per-period Service Fee "
				"line was not added. Merge the Service Plan Selection section of "
				"templates/contracts/maintenance_services_agreement.html into it by hand."
			),
		)
		return
	freeze_contract_copies(TEMPLATE_KEY)
	pending = live_requests(TEMPLATE_KEY)
	if pending:
		if quiet:
			return
		frappe.log_error(
			title="Maintenance agreement fee line: template not updated yet",
			message=(
				"These signing links are still out: " + ", ".join(pending) + ". The Service Fee line is "
				"added by the first migrate after they are signed or voided (retry_after_migrate), or now "
				"by running erpnext_enhancements.patches.refresh_maintenance_agreement_fee_line.execute."
			),
		)
		return
	frappe.db.set_value("Contract Template", TEMPLATE_KEY, "body", new_body, update_modified=False)
	frappe.db.commit()
