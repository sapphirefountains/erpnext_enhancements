# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Recover attestation and transition dates from `tabVersion`, only where provable.

Two backfills that share one rule and one warning.

**The rule.** A date is written only when a `Version` row *proves* it. Everything
else is left NULL and marked, because an empty date means "not reconstructible" and
the roster renders it that way. Filling it with today's, or with a plausible
neighbour, converts an unanswerable question into a confidently wrong answer — which
is the failure this whole release is about.

**The warning, and why the yield here is low.** `track_changes = 1` is set on
essentially every doctype in both modules and reads like a safety net. It is not one
for status, because every derived-status write in this app goes through
``db_set(..., update_modified=False)``, which never reaches ``save_version()``. So
the transitions an auditor actually asks about left no trace at all. The one class
that *did* is the docstatus flip, because ``cancel()`` and ``submit()`` route through
``save()`` — which is exactly why sign-off attestation is recoverable and work
restriction status is mostly not.

Expect this to report a lot of "not reconstructible". That is the honest number.

Never aborts a migrate. Safe twice: both passes only ever fill a blank.
"""

import json

import frappe
from frappe.utils import getdate

SIGNOFF = "Training Signoff"
RESTRICTION = "Work Restriction"

RESTRICTION_UNKNOWN = (
	"Status moved before v1.396.0, when no transition date was recorded. Not "
	"reconstructible, so deliberately left empty rather than guessed."
)


def execute():
	_signoff_attested_on()
	_restriction_transitions()


def _signoff_attested_on():
	"""``attested_on`` from the Version row recording the docstatus 0 -> 1 flip.

	`signed_on` is NOT a fallback and must never become one: it is when the learner
	*raised the request*, and the gap between the two is sometimes days. Answering an
	audit with it reports somebody as competent from the moment they asked to be.
	"""
	if not frappe.db.exists("DocType", SIGNOFF):
		return
	if not frappe.get_meta(SIGNOFF).has_field("attested_on"):
		return

	rows = frappe.get_all(SIGNOFF, filters={"docstatus": 1}, pluck="name")
	found = 0
	for name in rows:
		if frappe.db.get_value(SIGNOFF, name, "attested_on"):
			continue
		when = _submitted_at(SIGNOFF, name)
		if not when:
			continue
		frappe.db.set_value(SIGNOFF, name, "attested_on", when, update_modified=False)
		found += 1

	print(
		f"[erpnext_enhancements] sign-off attestation dates: {found} recovered of "
		f"{len(rows)} submitted; the rest are not reconstructible and stay empty"
	)


def _restriction_transitions():
	"""``ended_on`` / ``canceled_on`` from a Version row, else ``to_date``, else NULL.

	``to_date`` is accepted as the end date only when it is set and in the past —
	that is the date the restriction was *written to* run until, so a row that
	reached it and was then tidied to Ended genuinely stopped applying then. A
	``to_date`` in the future proves nothing about a status that has already moved.
	"""
	if not frappe.db.exists("DocType", RESTRICTION):
		return
	if not frappe.get_meta(RESTRICTION).has_field("ended_on"):
		return

	today = getdate(frappe.utils.today())
	recovered = inferred = unknown = 0

	for row in frappe.get_all(
		RESTRICTION,
		filters={"status": ["in", ("Ended", "Canceled")]},
		fields=["name", "status", "to_date", "ended_on", "canceled_on"],
	):
		field = "ended_on" if row.status == "Ended" else "canceled_on"
		if row.get(field):
			continue

		when = _status_changed_at(RESTRICTION, row.name, row.status)
		if when:
			frappe.db.set_value(RESTRICTION, row.name, field, getdate(when), update_modified=False)
			recovered += 1
			continue

		if row.status == "Ended" and row.to_date and getdate(row.to_date) <= today:
			frappe.db.set_value(RESTRICTION, row.name, field, row.to_date, update_modified=False)
			inferred += 1
			continue

		frappe.db.set_value(
			RESTRICTION, row.name, "history_note", RESTRICTION_UNKNOWN, update_modified=False
		)
		unknown += 1

	print(
		f"[erpnext_enhancements] work restriction transitions: {recovered} from Version, "
		f"{inferred} from to_date, {unknown} not reconstructible"
	)


def _submitted_at(doctype, name):
	"""``creation`` of the Version row that recorded this document being submitted."""
	for version in frappe.get_all(
		"Version",
		filters={"ref_doctype": doctype, "docname": name},
		fields=["creation", "data"],
		order_by="creation asc",
	):
		for _field, before, after in _changed(version.get("data")):
			if _field == "docstatus" and str(after) == "1" and str(before) == "0":
				return version.creation
	return None


def _status_changed_at(doctype, name, to_status):
	"""``creation`` of the Version row that moved ``status`` to ``to_status``."""
	for version in frappe.get_all(
		"Version",
		filters={"ref_doctype": doctype, "docname": name},
		fields=["creation", "data"],
		order_by="creation asc",
	):
		for field, _before, after in _changed(version.get("data")):
			if field == "status" and after == to_status:
				return version.creation
	return None


def _changed(raw):
	"""Yield ``(fieldname, before, after)`` from a Version row's ``data`` blob.

	Tolerant on purpose: a Version row whose shape has drifted across upgrades must
	cost this patch that row, not the migrate.
	"""
	try:
		payload = json.loads(raw or "{}")
	except (TypeError, ValueError):
		return
	for entry in payload.get("changed") or []:
		if isinstance(entry, (list, tuple)) and len(entry) >= 3:
			yield entry[0], entry[1], entry[2]
