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
	threshold = get_effective_threshold(doc)
	if not threshold or flt(doc.grand_total) <= flt(threshold):
		return
	if has_approval_authority():
		return
	frappe.throw(
		_(
			"This Purchase Order total ({0}) exceeds the approval threshold of {1}. "
			"Only a {2} (the CEO) can submit it — save the draft and ask the approver to submit."
		).format(
			fmt_money(doc.grand_total, currency=doc.get("currency")),
			fmt_money(threshold, currency=doc.get("currency")),
			APPROVING_ROLE,
		),
		title=_("Approval Required"),
	)


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
