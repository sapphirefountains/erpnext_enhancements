"""Separation of duties on Purchase Order submission (WI-066).

Whoever raised the Material Request must not be the one who commits the money
against it. A Purchase Order that draws on one or more Material Requests can only
be *submitted* by someone who owns none of them — two humans on every
requisition-to-commitment chain.

Scope is deliberately narrow: the check is **MR-linked only**. A PO carrying no
``Purchase Order Item.material_request`` value is never blocked, because nothing
ties it to a requester. On this site only 7 of 127 historical POs carried that
link, so the gate's reach today is small and grows only as the MR-first
discipline in the WI-012 SOP is actually adopted. It is traceability hygiene, not
a spend control — the spend controls are WI-013's threshold and the Purchase
Invoice approval workflow (WI-044).

``Material Request`` has no ``requested_by`` field on this site, so ``owner`` is
the only requester identity available. An MR typed up on someone else's behalf
therefore binds the person who typed it, not the person who wanted it.

There is **no role-based bypass** — not "Purchase Manager", not "PO Approver",
not the CEO. Only ``Administrator``, which is break-glass and also bypasses
WI-013's threshold, so it voids both controls at once. The identity checked is
``frappe.session.user`` — whoever presses Submit, not ``doc.owner``: a requester
may draft the PO and hand the draft over, which is the intended workflow (it
mirrors WI-013's "save the draft, let someone else submit" shape) and makes
amended POs behave correctly with no special case.

Registered ahead of ``po_approval.enforce_threshold`` on ``before_submit``; see
hooks.py for why that order matters. Killable without a deploy via ERPNext
Enhancements Settings → Purchasing Controls → Enforce PO Separation of Duties.
"""

import frappe
from frappe import _

CREATOR_ROLE = "PO Creator"
APPROVING_ROLE = "PO Approver"


def enforce_requester_separation(doc, method=None):
	"""`before_submit` hook on Purchase Order: block the MR owner from submitting."""
	if not is_enforcement_enabled():
		return
	if is_separation_exempt():
		return
	conflicts = get_conflicting_material_requests(doc)
	if not conflicts:
		return
	subject = _("Material Request {0}") if len(conflicts) == 1 else _("Material Requests {0}")
	frappe.throw(
		_(
			"Separation of duties: you raised {0}, so you cannot also submit the "
			"Purchase Order that fills it. Save the draft and ask another {1} to review "
			"and submit it — if the total is over the approval threshold, ask a {2}. "
			"No role overrides this check."
		).format(subject.format(", ".join(conflicts)), CREATOR_ROLE, APPROVING_ROLE),
		title=_("Separation of Duties"),
	)


def is_enforcement_enabled():
	"""The Settings kill switch. Defaults ON; see patches/default_po_sod_on.py."""
	return bool(
		frappe.db.get_single_value("ERPNext Enhancements Settings", "po_sod_enforcement_enabled")
	)


def is_separation_exempt(user=None):
	"""Only Administrator is exempt — there is no role-based override by design."""
	return (user or frappe.session.user) == "Administrator"


def get_linked_material_requests(doc):
	"""Unique, non-blank Material Request names this PO draws from, sorted.

	Defensive per the app's hook convention: ``doc.get("items")`` may be absent
	during ERPNext's own test bootstrap, and a line's ``material_request`` may be
	None, "" or whitespace. Duplicates across lines collapse — a two-line PO off
	one Material Request must name it once in the error, not twice.
	"""
	names = set()
	for row in doc.get("items") or []:
		name = (row.get("material_request") or "").strip()
		if name:
			names.add(name)
	return sorted(names)


def get_conflicting_material_requests(doc, user=None):
	"""The linked Material Requests owned by `user` (default: the session user).

	``frappe.get_all`` ignores permissions by design, which is what we want: the
	gate must fire for a PO Creator who holds no Material Request read access.

	Names that no longer resolve — an MR deleted since the PO was drafted — simply
	do not match, so a dangling link can never make a Purchase Order permanently
	unsubmittable. The failure mode here is open, not stuck.
	"""
	names = get_linked_material_requests(doc)
	if not names:
		return []
	return sorted(
		frappe.get_all(
			"Material Request",
			filters={"name": ("in", names), "owner": user or frappe.session.user},
			pluck="name",
		)
	)
