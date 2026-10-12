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

So saving the switch **folds** the next trip's drafts into one site visit, and is careful
about everything else, because deleting a draft cannot be undone:

* **Only the next trip's bare drafts.** A draft folds only when it is dated today or later, has
  never been opened (no section rows: the wizard and the form build them on first open, and
  an opened draft may have answers still on a phone, which the server cannot see), has
  nothing on it (an answer, note, clock, signature, photo, date chosen, an attachment or a
  comment) and is still Draft. The earliest becomes the site visit; the others fold into it
  only when planned the same way (same day, technician and crew), so a date or crew somebody
  set in the Maintenance Planner is not thrown away.
* **Everything else is left and named**: overdue drafts stay missed visits (the Missed
  Maintenance Visits report lists them), opened or started ones, ones planned differently,
  and ones another record links to. Labelled drafts (a Winterization, a follow-up) are
  separate visits and are never looked at.
* **Never a second site visit.** A contract that already has an open site visit folds nothing.
* **The kept draft is converted in place** (its water feature cleared, one field written), so
  no save hook runs on it; a full save would stamp a kiosk clock-in from whatever interval
  the technician is on when the office saves the contract.
* **ToDos go at once.** ``delete_doc`` removes a deleted draft's ToDos only in a job queued
  after commit, which a deploy's Redis flush destroys, so the open ones are canceled here, in
  the same transaction.

This is a code change, not a one-off fix for Hardware, so it works for any contract switched
later. Only this direction is handled: switching back to Per Feature leaves an open site
visit as it is.
"""

import frappe
from frappe import _
from frappe.utils import formatdate, getdate, nowdate

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
REGULAR = {"docstatus": 0, "visit_label": ["is", "not set"]}


def on_contract_update(contract):
	"""The contract controller's ``on_update``: fold when this save switched it to one visit
	for the site. Says what it did; says nothing when there was nothing to fold."""
	if contract.get("visit_shape") != PER_SITE:
		return
	before = contract.get_doc_before_save()
	if not before or before.get("visit_shape") == PER_SITE:
		return
	message = describe(fold_feature_drafts(contract.name))
	if message:
		frappe.msgprint(message, title=_("One visit for the whole site"), indicator="blue")


def fold_feature_drafts(contract_name):
	"""Fold the contract's next-trip per-feature drafts into one site visit (module docstring).

	Returns ``{"kept", "date", "removed", "started", "different", "linked", "overdue",
	"site_visit", "beside"}``: names, except ``date``. ``beside`` is every per-feature draft,
	left because the contract already has a site visit open.
	"""
	result = {
		"kept": None,
		"date": None,
		"removed": [],
		"started": [],
		"different": [],
		"linked": [],
		"overdue": [],
		"site_visit": None,
		"beside": [],
	}
	names = frappe.get_all(
		DOCTYPE,
		filters={"maintenance_contract": contract_name, **REGULAR, "serial_no": ["is", "set"]},
		pluck="name",
		order_by="scheduled_visit_date asc, creation asc",
	)
	if not names:
		return result
	site_visit = frappe.get_all(
		DOCTYPE,
		filters={"maintenance_contract": contract_name, **REGULAR, "serial_no": ["is", "not set"]},
		pluck="name",
		limit=1,
	)
	if site_visit:
		result["site_visit"] = site_visit[0]
		result["beside"] = list(names)
		return result

	today = getdate(nowdate())
	bare = []
	for doc in (frappe.get_doc(DOCTYPE, name) for name in names):
		if not doc.get("scheduled_visit_date") or getdate(doc.scheduled_visit_date) < today:
			result["overdue"].append(doc.name)
		elif started(doc):
			result["started"].append(doc.name)
		else:
			bare.append(doc)
	if not bare:
		return result

	keep, rest = bare[0], bare[1:]
	# In place, one field: no save hooks on the kept draft (module docstring).
	frappe.db.set_value(DOCTYPE, keep.name, "serial_no", None)
	result["kept"], result["date"] = keep.name, keep.get("scheduled_visit_date")
	for doc in rest:
		if _plan(doc) != _plan(keep):
			result["different"].append(doc.name)
		elif _linked(doc):
			result["linked"].append(doc.name)
		else:
			_cancel_todos(doc.name)
			frappe.delete_doc(DOCTYPE, doc.name, ignore_permissions=True)
			result["removed"].append(doc.name)
	return result


def started(doc):
	"""Somebody has opened or begun the visit, so folding it could lose work: section rows (the
	wizard builds them on first open, and answers may still be on a phone), any header field
	of :data:`STARTED_FIELDS`, a state past Draft, an attachment or a comment."""
	if (doc.get("workflow_state") or "Draft") != "Draft":
		return True
	if any(doc.get(table) for table in TABLES):
		return True
	if any(doc.get(field) for field in STARTED_FIELDS):
		return True
	if frappe.db.exists("File", {"attached_to_doctype": DOCTYPE, "attached_to_name": doc.name}):
		return True
	return bool(
		frappe.db.exists(
			"Comment", {"reference_doctype": DOCTYPE, "reference_name": doc.name, "comment_type": "Comment"}
		)
	)


def _plan(doc):
	"""How the visit is planned: the day, the technician and the crew."""
	crew = sorted(row.get("user") or "" for row in doc.get("crew") or [])
	return (str(doc.get("scheduled_visit_date") or ""), doc.get("technician") or "", crew)


def _linked(doc):
	"""Another record links to the draft, so ``delete_doc`` would refuse it. Checked first, with
	its message muted (the draft is named in the summary instead), so that a real error in the
	delete itself is still shown."""
	from frappe.model.delete_doc import check_if_doc_is_dynamically_linked, check_if_doc_is_linked

	muted = frappe.flags.mute_messages
	frappe.flags.mute_messages = True
	try:
		check_if_doc_is_linked(doc)
		check_if_doc_is_dynamically_linked(doc)
		return False
	except frappe.LinkExistsError:
		return True
	finally:
		frappe.flags.mute_messages = muted


def _cancel_todos(name):
	for todo in frappe.get_all(
		"ToDo", filters={"reference_type": DOCTYPE, "reference_name": name, "status": "Open"}, pluck="name"
	):
		frappe.db.set_value("ToDo", todo, "status", "Cancelled")


def describe(result):
	"""What the fold did, for the person who saved the contract. Empty when nothing changed."""

	def names(key):
		return ", ".join(result.get(key) or [])

	lines = []
	if result.get("site_visit"):
		lines.append(
			_(
				"This contract already has a site visit open ({0}), so its per-feature drafts were left "
				"as they are: {1}."
			).format(result["site_visit"], names("beside"))
		)
		return "<br>".join(frappe.utils.escape_html(line) for line in lines)
	if result.get("kept"):
		when = formatdate(result["date"]) if result.get("date") else _("its next date")
		lines.append(
			_("Visit {0} on {1} now covers every water feature on this contract.").format(
				result["kept"], when
			)
		)
	if result.get("removed"):
		lines.append(
			_("Removed {0} per-feature drafts for the same trip that nobody had opened: {1}.").format(
				len(result["removed"]), names("removed")
			)
		)
	for key, text in (
		(
			"started",
			_(
				"Left, because someone has opened or started them: {0}. Finish or delete them; until "
				"then they stand beside the site visit."
			),
		),
		("different", _("Left, because they are planned for another day, technician or crew: {0}.")),
		("linked", _("Left, because other records link to them: {0}.")),
		("overdue", _("Left as missed visits, because their date has passed: {0}.")),
	):
		if result.get(key):
			lines.append(text.format(names(key)))
	return "<br>".join(frappe.utils.escape_html(line) for line in lines)
