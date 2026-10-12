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
  nothing on it (an answer, note, clock, signature, photo, date chosen or an attachment) and
  is still Draft. The earliest becomes the site visit; the others fold into it only when
  planned the same way (same day, technician and crew), so a date or crew somebody set in the
  Maintenance Planner is not thrown away.
* **Comments move, they do not block** (v1.599.1). A folded draft's comments go onto the site
  visit, one of each: the scheduler's training warning and the Planner's conflict notes are
  on almost every draft, so counting a comment as work left every Hardware draft unfolded the
  first time it ran (2026-10-12).
* **Everything else is left and named**: overdue drafts stay missed visits (the Missed
  Maintenance Visits report lists them), opened or started ones, ones planned differently,
  and ones another record links to. Labelled drafts (a Winterization, a follow-up) are
  separate visits and are never looked at.
* **Never a second site visit.** A contract that already has an open site visit folds its
  per-feature drafts into that one when it is bare and upcoming (v1.599.2: a fold left half
  done, or a switch back and forth), and folds nothing when somebody has started it.
* **A draft's own workflow actions are not links** (v1.599.2). Every draft under the maintenance
  workflow has an open Workflow Action, and ``delete_doc`` clears it in ``on_trash`` before its
  own link check; checking links before calling it counted that as a link, so the first run
  kept Hardware's site visit and left the other three drafts.
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
	# Not started, but out in the world: an invoice made from it, or a morning digest that texted
	# its link (the crew's digest stamps are checked in started()). The Planner's own undo
	# (api.maintenance_actions._removal_problem) refuses to delete a visit for the same reasons.
	"sales_invoice",
	"dispatch_digest_sent_on",
)
#: The training warning's marker (training/compliance.py): its text changes with the reason
#: ("Not completed", "Overdue") while the marker names the same people and courses.
TRAINING_MARKER = "<!-- training-compliance:"
REGULAR = {"docstatus": 0, "visit_label": ["is", "not set"]}
#: Dynamic links ``delete_doc`` removes in ``on_trash`` (frappe's ``process_workflow_actions``)
#: before its own link check, so they never stop a delete.
CLEARED_ON_TRASH = frozenset({"Workflow Action"})


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
	left because the contract already has a site visit open that somebody has started.
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
		"comments": 0,
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
	today = getdate(nowdate())

	def overdue(doc):
		return not doc.get("scheduled_visit_date") or getdate(doc.scheduled_visit_date) < today

	existing = frappe.get_doc(DOCTYPE, site_visit[0]) if site_visit else None
	if existing and (overdue(existing) or started(existing)):
		result["site_visit"] = existing.name
		result["beside"] = list(names)
		return result

	bare = []
	for doc in (frappe.get_doc(DOCTYPE, name) for name in names):
		if overdue(doc):
			result["overdue"].append(doc.name)
		elif started(doc):
			result["started"].append(doc.name)
		else:
			bare.append(doc)
	if existing:
		# The contract's site visit is already there and bare: the drafts fold into it.
		keep, rest = existing, bare
	elif bare:
		keep, rest = bare[0], bare[1:]
		# In place, one field: no save hooks on the kept draft (module docstring).
		frappe.db.set_value(DOCTYPE, keep.name, "serial_no", None)
	else:
		return result
	result["kept"], result["date"] = keep.name, keep.get("scheduled_visit_date")
	for doc in rest:
		if _plan(doc) != _plan(keep):
			result["different"].append(doc.name)
		elif _linked(doc):
			result["linked"].append(doc.name)
		else:
			result["comments"] += _carry_comments(doc.name, keep.name)
			_cancel_todos(doc.name)
			frappe.delete_doc(DOCTYPE, doc.name, ignore_permissions=True)
			result["removed"].append(doc.name)
	return result


def started(doc):
	"""Somebody has opened or begun the visit, so folding it could lose work: section rows (the
	wizard builds them on first open, and answers may still be on a phone), any header field
	of :data:`STARTED_FIELDS` (an invoice or a texted digest among them), a crew member's digest
	sent, a state past Draft, or an attachment. Not a comment: those are carried to the site
	visit (:func:`_carry_comments`)."""
	if (doc.get("workflow_state") or "Draft") != "Draft":
		return True
	if any(doc.get(table) for table in TABLES):
		return True
	if any(doc.get(field) for field in STARTED_FIELDS):
		return True
	if any(row.get("digest_sent_on") for row in doc.get("crew") or []):
		return True
	return bool(frappe.db.exists("File", {"attached_to_doctype": DOCTYPE, "attached_to_name": doc.name}))


def _carry_comments(source, target):
	"""A folded draft's comments onto the site visit, so nothing written on it is lost: a note to
	the technician, the Planner's conflict notes. Returns how many were carried.

	* **As written.** Each keeps its author and time: ``insert()`` would make whoever saved the
	  contract their author (the timeline shows ``owner``, and only the owner may edit or
	  delete a comment) and, through ``after_insert``, send every @mention in them again in that
	  person's name. So each is written with ``db_insert`` and the visit's comment cache updated,
	  as ``insert`` would have.
	* **Once.** One the site visit already has is not repeated: by its text, or a training
	  warning by its marker, whose text changes with the reason.
	* **Threaded.** A reply (``custom_parent_comment``, ``api/comments.py``) points at its parent's
	  copy, or at the site visit's own copy of it.
	"""
	from frappe.core.doctype.comment.comment import update_comment_in_doc

	threaded = frappe.db.has_column("Comment", "custom_parent_comment")
	filters = {"reference_doctype": DOCTYPE, "comment_type": "Comment"}
	have = {
		_comment_key(row.content): row.name
		for row in frappe.get_all(
			"Comment", filters={**filters, "reference_name": target}, fields=["name", "content"]
		)
	}
	copies = {}
	carried = 0
	for row in frappe.get_all(
		"Comment",
		filters={**filters, "reference_name": source},
		fields=["name", "content", "comment_email", "comment_by", "owner", "creation"]
		+ (["custom_parent_comment"] if threaded else []),
		order_by="creation asc",
	):
		key = _comment_key(row.content)
		if key in have:
			copies[row.name] = have[key]
			continue
		comment = frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": DOCTYPE,
				"reference_name": target,
				"content": row.content,
				"comment_email": row.comment_email,
				"comment_by": row.comment_by,
				"owner": row.owner,
				"modified_by": row.owner,
				"creation": row.creation,
				"modified": row.creation,
			}
		)
		if threaded and row.get("custom_parent_comment"):
			comment.custom_parent_comment = copies.get(row.custom_parent_comment)
		comment.db_insert()
		update_comment_in_doc(comment)
		have[key] = copies[row.name] = comment.name
		carried += 1
	return carried


def _comment_key(content):
	"""What makes two comments the same: a training warning's marker, else the text."""
	content = content or ""
	if TRAINING_MARKER in content:
		return content[content.index(TRAINING_MARKER) :].split("-->", 1)[0]
	return content


def _plan(doc):
	"""How the visit is planned: the day, the technician and the crew."""
	crew = sorted(row.get("user") or "" for row in doc.get("crew") or [])
	return (str(doc.get("scheduled_visit_date") or ""), doc.get("technician") or "", crew)


def _linked(doc):
	"""Another record links to the draft, so ``delete_doc`` would refuse it. Checked first, with
	its message muted (the draft is named in the summary instead), so that a real error in the
	delete itself is still shown.

	The check is ``delete_doc``'s own, less what ``delete_doc`` clears in ``on_trash`` before it
	checks: the draft's Workflow Actions (:data:`CLEARED_ON_TRASH`). Every draft under the
	maintenance workflow has one, so without this every draft read as linked."""
	from frappe.model.delete_doc import check_if_doc_is_linked, get_dynamic_linked_docs

	muted = frappe.flags.mute_messages
	frappe.flags.mute_messages = True
	try:
		check_if_doc_is_linked(doc)
	except frappe.LinkExistsError:
		return True
	finally:
		frappe.flags.mute_messages = muted
	return any(link.get("reference_doctype") not in CLEARED_ON_TRASH for link in get_dynamic_linked_docs(doc))


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
	if result.get("comments"):
		lines.append(_("Their comments are on {0} now.").format(result["kept"]))
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
