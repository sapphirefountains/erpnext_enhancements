"""Purchase Order approval-threshold escalation (WI-013).

A Purchase Order whose grand total exceeds the approval threshold can only be
*submitted* by a user holding the "PO Approver" role (the CEO). Everyone else
must save the draft and hand it to the approver — no custom approval doctype,
just a submit-time gate (native "PM saves, CEO submits" flow).

The threshold is configurable in **ERPNext Enhancements Settings**
(`po_approval_threshold`, default 500; set 0 to disable). ``get_effective_threshold``
is the single resolution point, deliberately structured so a future **per-project**
override — a fixed amount OR a percentage of the project budget — can slot in
without touching the enforcement (WI-058; the percentage rule is deferred until
project budgets exist, since every project's ``estimated_costing`` is 0/NULL today).
"""

import frappe
from frappe import _
from frappe.utils import flt, fmt_money, now_datetime

APPROVING_ROLE = "PO Approver"


def enforce_threshold(doc, method=None):
	"""`before_submit` hook on Purchase Order: block submission above the effective
	threshold unless the submitting user holds the approving role."""
	_enforce_threshold(doc, after_submit=False)


def enforce_threshold_after_submit(doc, method=None):
	"""`before_update_after_submit` hook on Purchase Order: re-run the WI-013 gate on
	every post-submit change.

	The submit-time gate alone is bypassable. ERPNext's "Update Items" button calls the
	whitelisted ``erpnext.controllers.accounts_controller.update_child_qty_rate``, which
	edits qty/rate/rows on a *submitted* PO, recalculates ``grand_total`` and calls
	``parent.save()`` — an update-after-submit that fires this event but never re-runs
	``before_submit``. Without this a PO submitted at $400 (under the $500 threshold) can be
	edited up to any amount by anyone with write access, and neither WI-013 nor WI-066
	fires. Its only native backstops (Authorization Rule, Budget) are inert on this site.
	"""
	_enforce_threshold(doc, after_submit=True)


def _threshold_amount(doc):
	"""The amount compared against the threshold, in company (base) currency.

	The threshold is a single company-wide figure, but ``grand_total`` is in the PO's
	transaction currency — so a foreign-currency PO was gated against the wrong number
	(a EUR total compared to a USD threshold). ``base_grand_total`` is
	``grand_total * conversion_rate``; fall back to ``grand_total`` only if it is unset
	(e.g. mid-bootstrap), where conversion_rate is 1 anyway.
	"""
	return flt(doc.get("base_grand_total")) or flt(doc.grand_total)


def _enforce_threshold(doc, after_submit):
	threshold = get_effective_threshold(doc)
	if not threshold:
		return
	amount = _threshold_amount(doc)
	if amount <= flt(threshold):
		return
	if has_approval_authority():
		return
	currency = _company_currency(doc)
	if after_submit:
		message = _(
			"This change would leave the Purchase Order total ({0}) above the approval "
			"threshold of {1}. Only a {2} (the CEO) can put an order over that amount — "
			"revert the change, or ask the approver to make it."
		)
	else:
		message = _(
			"This Purchase Order total ({0}) exceeds the approval threshold of {1}. "
			"Only a {2} (the CEO) can submit it — save the draft and ask the approver to submit."
		)
	frappe.throw(
		message.format(
			fmt_money(amount, currency=currency),
			fmt_money(threshold, currency=currency),
			APPROVING_ROLE,
		),
		title=_("Approval Required"),
	)


def _company_currency(doc):
	"""The company's base currency — the unit the threshold is expressed in.

	Falls back to the PO's own currency if the company (or its default currency) cannot
	be resolved, so the error message always names *some* currency rather than none.
	"""
	company = doc.get("company")
	if company:
		currency = frappe.get_cached_value("Company", company, "default_currency")
		if currency:
			return currency
	return doc.get("currency")


def stamp_approval(doc, method=None):
	"""`before_submit` hook on Purchase Order: record who submitted it, and when.

	Runs **last** in the ``before_submit`` chain, so it only fires once
	``enforce_requester_separation`` and ``enforce_threshold`` have both passed — the
	stamp means "this order cleared both gates in this person's hands", not merely
	"somebody pressed submit".

	It exists because the supplier-facing print format shows an approver, and there was
	nowhere truthful to read one from: Purchase Order has no approver field, and
	``modified_by`` is whoever touched the document *last*, which after any post-submit
	edit is not the approver at all.

	Guarded with ``has_field``: the fields are created by
	``patches.add_po_approval_stamp_fields``, and this hook fires during ERPNext's own
	test bootstrap, before that patch has run on a fresh database.
	"""
	meta = doc.meta
	if meta.has_field("custom_approved_by"):
		doc.custom_approved_by = frappe.session.user
	if meta.has_field("custom_approved_on"):
		doc.custom_approved_on = now_datetime()


def has_approval_authority(user=None):
	"""True if `user` (default: session user) may submit an over-threshold PO."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return APPROVING_ROLE in frappe.get_roles(user)


def get_effective_threshold(doc):
	"""Resolve the approval-threshold amount that applies to this Purchase Order.

	Resolution order (only the last is active today):
	  1. Per-project override (WI-058) — a fixed amount, or a percentage resolved
	     against the project budget. Deferred: project budgets are 0/NULL today.
	  2. Global amount from ERPNext Enhancements Settings.

	Returns a positive amount, or 0 when no threshold applies (check disabled).
	"""
	project_threshold = _project_override(doc)
	if project_threshold is not None:
		return flt(project_threshold)
	return flt(_settings().get("po_approval_threshold"))


def _project_override(doc):
	"""Placeholder for the per-project threshold (WI-058).

	When per-project thresholds land, resolve the PO's project (`doc.project`, or
	the line-item projects) here and return either the fixed amount, or
	``percent / 100 * project_budget`` for a percentage rule. Returns None today,
	so the global Settings amount applies to every PO.
	"""
	return None


def _settings():
	return frappe.get_cached_doc("ERPNext Enhancements Settings")
