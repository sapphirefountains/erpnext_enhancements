# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Switching a contract to one visit for the whole site (v1.599.0, D4 of Phase D, TASK-2026-02489).

A contract's ``visit_shape`` is **Per Feature** (the scheduler drafts one visit per water
feature) or **Per Site Visit** (one visit covers every feature, its rows tagged with theirs).
Hardware Apartments (MNT-CON-2026-00008) has four fountains on one invoice and was Per Feature,
so each trip made four forms and four reports. Lisa asked how that would work; Nik chose one
visit for all four (2026-10-12).

Switching the shape alone is not enough. The scheduler drafts a site visit only while the
contract has **no** open regular draft (``tasks.generate_predictive_maintenance_records``,
the same filter as ``api.maintenance_visit.open_regular_draft``), and the per-feature drafts
already drafted for the next trip are exactly that. So the site visit would not appear until
all four were finished, as four visits, and "Log a visit" would open one of them.

So saving the switch **folds** them: the contract's open per-feature regular drafts nobody has
started become one site visit. The earliest is kept and turned into it (no water feature,
section tables emptied so the wizard builds them for every feature on first open, as it does
for any scheduler draft), and the rest are deleted with their ToDos. A draft somebody has
started (:func:`started`) is left as it is and named, as is one something links to. Labelled
drafts (a Winterization, a follow-up) are separate visits and never touched.

This is a code change, not a one-off fix for Hardware, so it works for any contract switched
later. Only this direction is handled: switching back to Per Feature leaves an open site
visit as it is.
"""

import frappe
from frappe import _
from frappe.utils import flt, formatdate

from erpnext_enhancements.sapphire_maintenance.task_rules import task_answered

DOCTYPE = "Sapphire Maintenance Record"
PER_SITE = "Per Site Visit"
TABLES = ("maintenance_results", "chemistry_readings", "cleaning_tasks", "consumables")
#: Header fields that mean somebody has started the visit.
STARTED_FIELDS = (
	"visit_notes",
	"visit_date",
	"clock_in_time",
	"clock_out_time",
	"safety_acknowledged",
	"client_sign_off",
	"fountain_photo",
)


def on_contract_update(contract):
	"""The contract controller's ``on_update``: fold when this save switched it to one visit
	for the site. Says what it did; says nothing when there was nothing to fold."""
	if contract.get("visit_shape") != PER_SITE:
		return
	before = contract.get_doc_before_save()
	if not before or before.get("visit_shape") == PER_SITE:
		return
	result = fold_feature_drafts(contract.name)
	message = describe(result)
	if message:
		frappe.msgprint(message, title=_("One visit for the whole site"), indicator="blue")


def fold_feature_drafts(contract_name):
	"""Fold the contract's open per-feature regular drafts into one site visit.

	Returns ``{"kept": name or None, "removed": [names], "left": [names]}``.
	"""
	names = frappe.get_all(
		DOCTYPE,
		filters={
			"maintenance_contract": contract_name,
			"docstatus": 0,
			"visit_label": ["is", "not set"],
			"serial_no": ["is", "set"],
		},
		pluck="name",
		order_by="scheduled_visit_date asc, creation asc",
	)
	drafts = [frappe.get_doc(DOCTYPE, name) for name in names]
	left = [doc.name for doc in drafts if started(doc)]
	untouched = [doc for doc in drafts if not started(doc)]
	if not untouched:
		return {"kept": None, "removed": [], "left": left}

	# The earliest becomes the site visit, and is saved before anything is deleted, so a save
	# that fails leaves every draft where it was (the whole contract save rolls back).
	keep, rest = untouched[0], untouched[1:]
	keep.serial_no = None
	for table in TABLES:
		keep.set(table, [])
	keep.save(ignore_permissions=True)

	removed = []
	for doc in rest:
		if _delete(doc.name):
			removed.append(doc.name)
		else:
			left.append(doc.name)
	return {"kept": keep.name, "date": keep.get("scheduled_visit_date"), "removed": removed, "left": left}


def started(doc):
	"""Somebody has begun the visit: an answer, a photo, a note, a clock, a signature, safety
	acknowledged, a date chosen, or it has gone further than Draft. Such a draft holds work
	that a fold must not throw away."""
	if (doc.get("workflow_state") or "Draft") != "Draft":
		return True
	if any(doc.get(field) for field in STARTED_FIELDS):
		return True
	for row in doc.get("maintenance_results") or []:
		if row.get("selection") or row.get("answer") or row.get("other_details") or row.get("photo"):
			return True
	for row in doc.get("chemistry_readings") or []:
		if flt(row.get("reading_value")) or row.get("notes") or row.get("photo"):
			return True
	for row in doc.get("cleaning_tasks") or []:
		if task_answered(row) or row.get("photo"):
			return True
	return any(flt(row.get("qty")) for row in doc.get("consumables") or [])


def _delete(name):
	"""Delete an untouched draft and its ToDos (``delete_doc`` removes those). False when
	something links to it, which ``delete_doc`` refuses with a message; that message is muted
	here because the draft is named in the fold's own summary instead."""
	muted = frappe.flags.mute_messages
	frappe.flags.mute_messages = True
	try:
		frappe.delete_doc(DOCTYPE, name, ignore_permissions=True)
		return True
	except frappe.LinkExistsError:
		return False
	finally:
		frappe.flags.mute_messages = muted


def describe(result):
	"""What the fold did, for the person who saved the contract. Empty when nothing changed."""
	lines = []
	if result.get("kept"):
		when = formatdate(result["date"]) if result.get("date") else _("its next date")
		lines.append(
			_("Visit {0} on {1} now covers every water feature on this contract.").format(
				result["kept"], when
			)
		)
	if result.get("removed"):
		lines.append(
			_("Removed {0} per-feature drafts nobody had started: {1}.").format(
				len(result["removed"]), ", ".join(result["removed"])
			)
		)
	if result.get("left"):
		lines.append(
			_(
				"Left as they are, because someone has started them: {0}. Finish or delete them; "
				"until then they stand beside the site visit."
			).format(", ".join(result["left"]))
		)
	return "<br>".join(frappe.utils.escape_html(line) for line in lines)
