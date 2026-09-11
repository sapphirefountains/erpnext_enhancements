# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Signing a policy, and the emergency contact nobody has filled in.

Two unrelated things in one module because both are "a field on somebody's record
that has to get filled in by that person, and nobody else can do it for them".

**Signing.** The typed name is compared against their own, because a signature
that does not match the signer is not one — and the mismatch is almost always a
manager helpfully signing on somebody's behalf, which is exactly the thing this
record exists to prevent. The IP is stamped, because an acknowledgement with no
trace of where it came from is easy to dispute and hard to defend.

**Emergency contacts.** Verified on prod 2026-09-11: `emergency_phone_number` is
empty for **all sixteen** active employees. The field has existed the whole time.
That is the shape of this whole release in miniature — the mechanism was there and
nothing ever asked anybody to use it — so this asks, weekly, and only of the
people who are missing one.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

DOCTYPE = "Policy Acknowledgement"
REQUESTED = "Requested"
SIGNED = "Signed"
DECLINED = "Declined"


def _me():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	return user


@frappe.whitelist()
def request_acknowledgement(policy, employees, version_label=None, document_url=None):
	"""Ask a list of people to acknowledge something. HR's action.

	Skips anybody who already has an outstanding or signed row for the same policy
	**and version**, so re-running it after adding one person does not produce
	fifteen duplicate requests — and so a new version genuinely does ask everybody
	again, which is the case that matters.
	"""
	me = _me()
	if not ({"HR Manager", "System Manager"} & set(frappe.get_roles(me))):
		frappe.throw(_("Only HR can ask for an acknowledgement."), frappe.PermissionError)

	if isinstance(employees, str):
		import json as _json

		try:
			employees = _json.loads(employees)
		except (TypeError, ValueError):
			employees = [employees]

	made = []
	for employee in employees or []:
		existing = frappe.db.exists(
			DOCTYPE,
			{
				"policy": policy,
				"version_label": version_label or "",
				"employee": employee,
				"status": ["in", (REQUESTED, SIGNED)],
			},
		)
		if existing:
			continue
		doc = frappe.new_doc(DOCTYPE)
		doc.policy = policy
		doc.version_label = version_label
		doc.document_url = document_url
		doc.employee = employee
		doc.status = REQUESTED
		doc.flags.policy_transition = True
		doc.insert(ignore_permissions=True)
		made.append(doc.name)
		_notify(doc)
	return {"created": made}


@frappe.whitelist()
def sign(acknowledgement, typed_name, signature=None):
	""""I have read it and I agree."

	The typed name has to match theirs. Not as a security control — anybody logged
	in as them could type it — but because the failure it catches is somebody
	else's name being typed, which is a manager signing on their behalf. That is a
	real and common thing, it is the one thing that makes the register worthless,
	and it is invisible afterwards.
	"""
	me = _me()
	doc = frappe.get_doc(DOCTYPE, acknowledgement)
	if doc.user != me:
		frappe.throw(
			_("Only {0} can sign this. An acknowledgement somebody else signed is not one.").format(
				doc.employee_name or doc.employee
			),
			frappe.PermissionError,
		)
	if doc.status == SIGNED:
		frappe.throw(_("You have already signed this."))

	typed = (typed_name or "").strip()
	if not typed:
		frappe.throw(_("Type your name to sign."))
	if not _names_match(typed, doc.employee_name):
		frappe.throw(
			_("That is not your name. Type <b>{0}</b>.").format(doc.employee_name or ""),
			title=_("Name does not match"),
		)

	doc.status = SIGNED
	doc.signed_name = typed
	doc.signature = signature
	doc.signed_on = now_datetime()
	doc.signed_from_ip = frappe.local.request_ip if hasattr(frappe.local, "request_ip") else None
	doc.flags.policy_transition = True
	doc.save(ignore_permissions=True)
	return {"status": doc.status, "signed_on": str(doc.signed_on)}


@frappe.whitelist()
def decline(acknowledgement, reason):
	"""Declining is a real answer and it is recorded.

	A register that only accepts yes is a register that gets a yes. A refusal with
	a reason on it is far more use to whoever has to deal with it than a row that
	simply never got filled in — which is what refusing to accept a no actually
	produces.
	"""
	me = _me()
	doc = frappe.get_doc(DOCTYPE, acknowledgement)
	if doc.user != me:
		frappe.throw(_("That is not yours to decline."), frappe.PermissionError)
	if not (reason or "").strip():
		frappe.throw(_("Say why. A decline with no reason leaves nothing to resolve."))

	doc.status = DECLINED
	doc.declined_reason = reason
	doc.flags.policy_transition = True
	doc.save(ignore_permissions=True)
	_notify_declined(doc)
	return {"status": doc.status}


def _names_match(typed, actual):
	"""Casefolded, punctuation-stripped, whitespace-collapsed. Nothing fuzzier.

	Deliberately not a partial or token-subset match: the failure being caught is
	*a different person's name*, and every leniency that lets "J Griffin" through
	also lets a colleague's surname through.
	"""
	import re

	def norm(value):
		return " ".join(re.sub(r"[^\w\s]", " ", str(value or "")).split()).casefold()

	return bool(norm(typed)) and norm(typed) == norm(actual)


def _notify(doc):
	try:
		from erpnext_enhancements.training import notifications

		recipient = notifications._recipient(doc.user)
		if not recipient:
			return False
		return notifications._send(
			recipient,
			_("Please read and acknowledge: {0}").format(doc.policy),
			_("<p>Please read <b>{0}</b>{1} and confirm you agree.</p><p><a href='{2}'>Open it</a></p>").format(
				frappe.utils.escape_html(doc.policy or ""),
				_(" (version {0})").format(doc.version_label) if doc.version_label else "",
				frappe.utils.get_url_to_form(DOCTYPE, doc.name),
			),
		)
	except Exception:
		frappe.log_error(
			f"Could not send the acknowledgement request {doc.name}\n{frappe.get_traceback()}",
			"Policy acknowledgement",
		)
		return False


def _notify_declined(doc):
	"""A decline goes to HR immediately. It is the only outcome that needs somebody."""
	try:
		from erpnext_enhancements.training import notifications

		for user in _hr_users():
			recipient = notifications._recipient(user)
			if recipient:
				notifications._send(
					recipient,
					_("Declined: {0}").format(doc.policy),
					_("<p><b>{0}</b> declined <b>{1}</b>.</p><p>{2}</p>").format(
						doc.employee_name or doc.employee,
						frappe.utils.escape_html(doc.policy or ""),
						frappe.utils.escape_html(doc.declined_reason or ""),
					),
				)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Policy acknowledgement")


def _hr_users():
	users = set()
	for role in ("HR Manager", "System Manager"):
		users.update(
			frappe.get_all("Has Role", filters={"parenttype": "User", "role": role}, pluck="parent")
			or []
		)
	return [u for u in users if u and u not in ("Administrator", "Guest")]


# ------------------------------------------------------------ emergency contacts


def nudge_missing_emergency_contacts():
	"""Weekly, and only to the people who are missing one.

	All sixteen were missing one when this was written, and the field has existed
	the whole time — nothing had ever asked. So it asks, and it asks *them* rather
	than reporting a number to HR, because the only person who can fill this in is
	the person whose contact it is.

	Never raises: a nudge that fails must not take the scheduler down.
	"""
	try:
		if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
			return 0
		from erpnext_enhancements.training import notifications

		sent = 0
		for row in frappe.get_all(
			"Employee",
			filters={"status": "Active", "user_id": ["is", "set"]},
			fields=["name", "employee_name", "user_id", "emergency_phone_number"],
		):
			if (row.emergency_phone_number or "").strip():
				continue
			recipient = notifications._recipient(row.user_id)
			if not recipient:
				continue
			if notifications._send(
				recipient,
				_("We have no emergency contact for you"),
				_(
					"<p>There is nobody listed to call if something happens to you at work.</p>"
					"<p>Add a name and a number to <a href='{0}'>your employee record</a> — it "
					"takes a minute and it is the one thing on there that only matters on the "
					"worst day.</p>"
				).format(frappe.utils.get_url_to_form("Employee", row.name)),
			):
				sent += 1
		return sent
	except Exception:
		frappe.log_error(
			f"Emergency-contact nudge failed\n{frappe.get_traceback()}", "HR emergency contacts"
		)
		return 0
