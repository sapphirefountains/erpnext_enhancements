# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Opening and closing a confined-space entry permit.

Two transitions, and the asymmetry between them is the design.

**Opening refuses.** Everywhere else in this app an advisory warns and lets the
human decide, for a reason that is written down in `training/compliance.py`: by
the time a record is being saved the truck is already moving, and blocking the
form moves the work off the books. That reasoning does not transfer here. A
confined-space permit is not a record *of* work that is happening, it is the
thing that decides whether it starts — and the atmosphere reading is the one
number the whole document exists to check. A permit that opens over a bad reading
has stopped meaning anything, and the failure it is guarding against is somebody
dying in a pit.

So: bad gas refuses. The human ticks — ventilation, isolation, rescue plan — are
listed back as a confirmation rather than refused, because those are judgements
and a form that argues with a supervisor about them gets filled in dishonestly.

**Closing is trivially easy**, on purpose. A close-out that takes effort is a
close-out that happens tomorrow, and a permit nobody closed reads exactly like a
person still down a hole.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

DOCTYPE = "Confined Space Entry Permit"

DRAFT = "Draft"
OPEN = "Open"
CLOSED = "Closed"
CANCELED = "Canceled"


def _me():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	return user


@frappe.whitelist()
def open_permit(permit, acknowledge_controls=0):
	"""Open it, or say exactly why not.

	`acknowledge_controls` is the supervisor confirming the unticked human controls
	deliberately. It cannot waive the atmosphere — there is no argument to have
	with a gas reading.
	"""
	_me()
	doc = frappe.get_doc(DOCTYPE, permit)
	if doc.status == OPEN:
		frappe.throw(_("That permit is already open."))
	if doc.status in (CLOSED, CANCELED):
		frappe.throw(_("That permit is {0} — open a new one for a new entry.").format(doc.status))

	problems = doc.atmosphere_problems()
	if problems:
		# The one refusal in the module. Not an advisory.
		frappe.throw(
			_(
				"<b>Do not enter.</b> {0}.<br><br>Ventilate, re-test, and open a new permit when "
				"the readings are in range. This is the one check that does not take an override."
			).format("; ".join(str(p) for p in problems)),
			title=_("Atmosphere out of limits"),
		)

	missing = doc.missing_controls()
	if missing and not cint(acknowledge_controls):
		# Listed back, not refused: these are judgements, and a form that argues
		# with a supervisor about them gets filled in dishonestly.
		frappe.throw(
			_("Not confirmed yet: {0}. Tick them, or confirm you are going ahead anyway.").format(
				", ".join(str(m) for m in missing)
			),
			title=_("Before anybody goes in"),
		)

	doc.status = OPEN
	doc.opened_on = now_datetime()
	doc.expires_on = doc.expiry_from(doc.opened_on)
	doc.flags.permit_transition = True
	doc.save(ignore_permissions=True)
	return {
		"status": doc.status,
		"expires_on": str(doc.expires_on),
		"acknowledged": [str(m) for m in missing],
	}


@frappe.whitelist()
def close_permit(permit, everyone_out=1, notes=None):
	"""Close it out. Deliberately one click and a confirmation.

	`everyone_out` is the only thing asked, and it is asked because it is the only
	thing that matters: a permit nobody closed reads exactly like a person still
	down a hole, and an attendant who cannot close it in ten seconds will close it
	in the truck on the way home.
	"""
	me = _me()
	doc = frappe.get_doc(DOCTYPE, permit)
	if doc.status != OPEN:
		frappe.throw(_("That permit is {0}, so there is nothing to close.").format(doc.status))
	if not cint(everyone_out):
		frappe.throw(_("Account for everybody before closing the permit."))

	doc.status = CLOSED
	doc.closed_on = now_datetime()
	doc.closed_by = me
	doc.everyone_out = 1
	if notes:
		doc.close_notes = notes
	doc.flags.permit_transition = True
	doc.save(ignore_permissions=True)
	return {"status": doc.status}


@frappe.whitelist()
def cancel_permit(permit, notes=None):
	"""Abandon it without entering — the common case when the gas is bad."""
	_me()
	doc = frappe.get_doc(DOCTYPE, permit)
	if doc.status == CLOSED:
		frappe.throw(_("That permit is already closed."))
	doc.status = CANCELED
	if notes:
		doc.close_notes = notes
	doc.flags.permit_transition = True
	doc.save(ignore_permissions=True)
	return {"status": doc.status}


@frappe.whitelist()
def open_permits():
	"""Permits still open — the "is anybody in a hole right now" question.

	Staff only, by employment. Ordered oldest first, because the oldest open permit
	is the one most likely to be a person and not a forgotten form.
	"""
	me = _me()
	if not frappe.db.exists("Employee", {"user_id": me, "status": "Active"}):
		frappe.throw(_("Only staff can see this."), frappe.PermissionError)
	rows = frappe.get_all(
		DOCTYPE,
		filters={"status": OPEN},
		fields=["name", "confined_space", "entrant_name", "attendant", "opened_on", "expires_on"],
		order_by="opened_on asc",
	)
	now = now_datetime()
	for row in rows:
		row["expired"] = bool(row.get("expires_on") and row["expires_on"] < now)
	return rows
