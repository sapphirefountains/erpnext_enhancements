# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The safety brief a technician reads before starting, assembled at the moment.

One endpoint, three sources, and every one of them **derived from the record
rather than typed**:

* **the safety data sheets** for the chemicals on *this visit*, taken from the
  visit's own consumables. A hand-kept list of "what is at this site" is wrong
  within a month, and a wrong SDS list is worse than none because it reads as
  authoritative — somebody checks it, finds the product they are holding is not on
  it, and concludes the product is harmless;
* **the PPE** for this kind of work, from the written hazard assessment OSHA asks
  for and almost nobody keeps. Keeping it is worth doing for its own sake here,
  because the list is read before every visit rather than filed;
* **open hazards somebody already found at this site**, which is the half that
  makes reporting one worth thirty seconds. A hazard report that only files a
  ticket protects nobody standing at that hatch tomorrow.

It attaches to the visit wizard's existing safety step rather than adding a screen.
That step already exists, technicians already cannot proceed past it, and a second
safety screen is one people learn to click through twice as fast.

Never raises. A brief that cannot be assembled must not stop somebody starting
work — the red banner and the acknowledgement tick are already there and are the
load-bearing part; this is what the app can add to them.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

VISIT = "Sapphire Maintenance Record"
HAZARD = "Site Hazard"
ASSESSMENT = "PPE Hazard Assessment"


def _me():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	return user


@frappe.whitelist()
def safety_brief(maintenance_record=None, customer=None, work_type=None):
	"""Everything worth knowing before this visit starts.

	Every section is independently best-effort: a missing doctype or a bad row
	costs that section and nothing else. A brief with two of three parts is still
	worth reading, and an exception here would take the safety step down with it.
	"""
	_me()
	visit = None
	if maintenance_record and frappe.db.exists(VISIT, maintenance_record):
		visit = frappe.get_doc(VISIT, maintenance_record)
		customer = customer or visit.customer

	return {
		"sheets": _sheets_for(visit),
		"ppe": _ppe_for(work_type),
		"hazards": _hazards_at(customer, visit),
	}


def _sheets_for(visit):
	"""SDS for the chemicals on this visit's consumables.

	**Derived from the visit, not from a site list.** The consumables child table
	is what the crew actually takes and uses, maintained because the stock has to
	balance — so it is right for the same reason a hand-kept list is wrong: it is
	kept up for a reason other than safety, which is the only kind of list that
	stays accurate.
	"""
	if not visit:
		return []
	try:
		items = [row.item for row in (visit.get("consumables") or []) if row.item]
		if not items:
			return []
		rows = frappe.get_all(
			"Item",
			filters={"name": ["in", list(dict.fromkeys(items))], "custom_is_chemical": 1},
			fields=[
				"name",
				"item_name",
				"custom_sds_document",
				"custom_hazard_summary",
				"custom_sds_reviewed_on",
			],
		)
		return [
			{
				"item": row.name,
				"label": row.item_name or row.name,
				"summary": row.custom_hazard_summary or "",
				"document": row.custom_sds_document or "",
				# Said out loud rather than hidden: a sheet with no document attached
				# is the common case at first, and pretending otherwise makes the
				# whole panel untrustworthy.
				"missing_document": not row.custom_sds_document,
			}
			for row in rows
		]
	except Exception:
		return []


def _ppe_for(work_type):
	"""The PPE list for this kind of work, from the written assessment."""
	if not work_type or not frappe.db.exists("DocType", ASSESSMENT):
		return []
	try:
		if not frappe.db.exists(ASSESSMENT, {"work_type": work_type, "is_active": 1}):
			return []
		doc = frappe.get_cached_doc(ASSESSMENT, work_type)
		return [
			{"protection": row.protection, "because": row.because or "", "required": cint(row.is_mandatory)}
			for row in (doc.get("requirements") or [])
		]
	except Exception:
		return []


def _hazards_at(customer, visit):
	"""Open hazards somebody already found here.

	`Accepted risk` is included alongside `Open`, deliberately: a hazard nobody is
	going to fix is still a hazard the next person needs to know about, and
	dropping it from the banner is how "accepted" quietly becomes "forgotten".
	"""
	if not customer or not frappe.db.exists("DocType", HAZARD):
		return []
	try:
		filters = {"customer": customer, "status": ["in", ("Open", "Accepted risk")]}
		if visit and visit.get("serial_no"):
			# Narrowed to this feature when the visit names one, plus site-wide rows
			# that name no feature -- a hazard on the north basin is not a reason to
			# warn somebody working on the south one, but a hazard at the gate is.
			# A LIST of triples, not a dict. `{"serial_no": a, "serial_no": b}` is a
			# dict literal with a repeated key -- the second silently replaces the
			# first, so the site-wide arm would have been the only one and the
			# feature-specific hazard would never have shown. Exactly the bug
			# `test_hooks_integrity` caught in the scheduler an hour earlier in this
			# release; Python does not warn about it either time.
			rows = frappe.get_all(
				HAZARD,
				filters=filters,
				or_filters=[
					["serial_no", "=", visit.serial_no],
					["serial_no", "in", ("", None)],
				],
				fields=["name", "what", "category", "where_exactly", "status", "photo"],
				order_by="reported_on desc",
			)
		else:
			rows = frappe.get_all(
				HAZARD,
				filters=filters,
				fields=["name", "what", "category", "where_exactly", "status", "photo"],
				order_by="reported_on desc",
			)
		return rows
	except Exception:
		return []


@frappe.whitelist()
def report_hazard(what, category, customer=None, where_exactly=None, maintenance_record=None, photo=None):
	"""Thirty seconds, standing at the thing.

	Four fields, and `what` is the only one that cannot be reconstructed later.
	The record exists the moment they send it — same rule as an incident report,
	for the same reason: if filing is expensive it does not happen, and a site that
	looks hazard-free because nobody could face the form is the worst outcome.
	"""
	me = _me()
	if not (what or "").strip():
		frappe.throw(_("Say what you found."))

	visit = None
	if maintenance_record and frappe.db.exists(VISIT, maintenance_record):
		visit = frappe.get_doc(VISIT, maintenance_record)
		customer = customer or visit.customer

	doc = frappe.new_doc(HAZARD)
	doc.what = what
	doc.category = category or "Other"
	doc.customer = customer
	doc.where_exactly = where_exactly
	doc.photo = photo
	if visit:
		doc.maintenance_record = visit.name
		doc.serial_no = visit.get("serial_no")
	doc.insert(ignore_permissions=True)

	_notify_hazard(doc, me)
	return {"name": doc.name}


def _notify_hazard(doc, reporter):
	"""Somebody who can fix it hears about it. Never raises.

	The banner warns the next technician either way — that is the half that does
	not depend on anybody reading an email — so a failure here costs the fix, not
	the warning.
	"""
	try:
		from erpnext_enhancements.training import notifications

		recipients = set()
		for role in ("Maintenance Manager", "System Manager"):
			recipients.update(
				frappe.get_all(
					"Has Role", filters={"parenttype": "User", "role": role}, pluck="parent"
				)
				or []
			)
		recipients.discard("Administrator")
		recipients.discard("Guest")

		body = _("<p><b>{0}</b> at {1}{2}.</p><p>{3}</p><p><a href='{4}'>Open it</a></p>").format(
			doc.category,
			frappe.utils.escape_html(doc.customer or _("an unnamed site")),
			_(" — {0}").format(frappe.utils.escape_html(doc.where_exactly)) if doc.where_exactly else "",
			frappe.utils.escape_html(doc.what or ""),
			frappe.utils.get_url_to_form(HAZARD, doc.name),
		)
		for user in recipients:
			recipient = notifications._recipient(user)
			if recipient:
				notifications._send(
					recipient, _("Hazard reported: {0}").format(doc.customer or doc.category), body
				)
		return True
	except Exception:
		frappe.log_error(
			f"Could not notify anyone about hazard {doc.name}\n{frappe.get_traceback()}",
			"Site hazard",
		)
		return False
