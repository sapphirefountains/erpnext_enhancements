# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Filing an incident from where it happened, and making a fix into real work.

Two endpoints, and both exist to close a gap that a doctype alone leaves open.

**`report_incident`** is the phone path. A technician standing at a pump vault
with a cut hand is not going to open the Desk, find a list and fill in fourteen
fields — and if filing is that expensive it does not happen, which is how a log
ends up looking clean. So this takes the five things somebody can actually answer
in a minute, writes the record immediately, and leaves the rest for whoever picks
it up. The record exists from the moment they press send, which is the property
that matters: **an incomplete record filed today beats a complete one filed
never**, and it beats a phone call that leaves nothing at all.

It is reachable from the visit wizard, the surface technicians already use on
their phones — because incidents happen on visits, and a feature reachable only
from its own list view is one nobody finds. This app has shipped three correct
server sides with no caller; that is not becoming four.

**`raise_action`** turns a corrective action into a Training Assignment or a Task.
A corrective action that stays a sentence on a form is a corrective action nobody
does, and "retrain him on confined space" written in a box next to an injury is
the exact sentence that gets read once at the review meeting and never again.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

DOCTYPE = "Safety Incident"


def _me():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	return user


@frappe.whitelist()
def report_incident(
	what_happened,
	location_text,
	incident_type="Injury",
	doing_before=None,
	employee=None,
	maintenance_record=None,
	occurred_on=None,
	photo=None,
):
	"""File one, now, with what somebody can answer standing up.

	**Nothing here is optional that the law needs and nothing is required that a
	person in pain cannot answer.** `what_happened` and `location_text` are the two
	that cannot be reconstructed later; everything else — the treatment, the
	classification, the body part, the root cause — is filled in by whoever reviews
	it, because those are judgements and this is a report.

	`employee` defaults to the caller, which is right here and wrong almost
	everywhere else in this app: on an intake queue the person pressing the button
	is a reviewer, but somebody filing an injury report from a phone is, in the
	overwhelming case, reporting their own. A colleague filing on their behalf
	passes it explicitly.
	"""
	me = _me()
	if not (what_happened or "").strip():
		frappe.throw(_("Say what happened."))
	if not (location_text or "").strip():
		frappe.throw(_("Say where."))

	if not employee:
		employee = frappe.db.get_value("Employee", {"user_id": me, "status": "Active"}, "name")

	doc = frappe.new_doc(DOCTYPE)
	doc.incident_type = incident_type or "Injury"
	doc.what_happened = what_happened
	# The form asks what they were doing just before, and it is a required field on
	# the record because OSHA 301 asks it. From a phone it is often blank, and
	# refusing the whole report over it would be choosing the form over the fact.
	doc.doing_before = (doing_before or "").strip() or _("Not recorded at the time")
	doc.location_text = location_text
	doc.occurred_on = occurred_on or now_datetime()
	if employee and doc.incident_type in ("Injury", "Illness"):
		doc.employee = employee
	if maintenance_record and frappe.db.exists("Sapphire Maintenance Record", maintenance_record):
		doc.maintenance_record = maintenance_record
	if photo:
		doc.photo = photo

	doc.insert(ignore_permissions=True)

	# Told to somebody immediately. A record nobody knows about is the same as a
	# phone call nobody answered -- and for a reportable case the clock is already
	# running.
	_notify_new(doc)
	return {
		"name": doc.name,
		"recordable": cint(doc.is_recordable),
		"reportable": doc.reportable_to_uosh,
		"due_by": str(doc.uosh_due_by) if doc.uosh_due_by else None,
	}


def _notify_new(doc):
	"""One email to whoever keeps the log. Never raises."""
	try:
		from erpnext_enhancements.training import notifications

		recipients = _log_keepers()
		if not recipients:
			return False
		urgent = doc.reportable_to_uosh and doc.reportable_to_uosh != _("No")
		subject = (
			_("REPORTABLE incident: {0}").format(doc.location_text)
			if urgent
			else _("Incident reported: {0}").format(doc.location_text)
		)
		body = _("<p><b>{0}</b> at {1}.</p><p>{2}</p>").format(
			doc.incident_type, frappe.utils.escape_html(doc.location_text or ""),
			frappe.utils.escape_html(doc.what_happened or ""),
		)
		if urgent:
			body += _(
				"<p><b>{0}</b> — call UOSH by <b>{1}</b>. Do not move the equipment until they "
				"release the scene.</p>"
			).format(doc.reportable_to_uosh, doc.uosh_due_by)
		body += _("<p><a href='{0}'>Open the report</a></p>").format(
			frappe.utils.get_url_to_form(DOCTYPE, doc.name)
		)
		sent = False
		for user in recipients:
			recipient = notifications._recipient(user)
			if recipient:
				sent = notifications._send(recipient, subject, body) or sent
		return sent
	except Exception:
		frappe.log_error(
			f"Could not notify anyone about {doc.name}\n{frappe.get_traceback()}", "Safety incident"
		)
		return False


def _log_keepers():
	"""Who hears about a new incident.

	`HR Manager` plus `System Manager`, read live rather than from a settings field.
	A settings field would be one more thing to fill in before this works at all,
	and the failure of an unset one is silence — which is the failure mode this
	whole module exists to remove.
	"""
	users = set()
	for role in ("HR Manager", "System Manager"):
		users.update(
			frappe.get_all("Has Role", filters={"parenttype": "User", "role": role}, pluck="parent")
			or []
		)
	return [u for u in users if u and u not in ("Administrator", "Guest")]


@frappe.whitelist()
def raise_action(incident, row, kind="Task"):
	"""Turn one corrective action into work that exists outside this form.

	`kind` is ``Task`` or ``Training Assignment``. Both are deliberate: "fix the
	ladder" is a task, "retrain him on confined space" is an assignment, and
	collapsing them into one would make one of the two a bad fit for the thing it
	most often is.

	Refuses to raise the same row twice — `raised_reference` is the guard — because
	a corrective action raised three times is three people doing it once between
	them.
	"""
	me = _me()
	doc = frappe.get_doc(DOCTYPE, incident)
	doc.check_permission("write")

	line = next((r for r in doc.actions if r.name == row), None)
	if not line:
		frappe.throw(_("That action is not on this incident."))
	if line.raised_reference:
		frappe.throw(_("That action has already been raised as {0}.").format(line.raised_reference))

	if kind == "Training Assignment":
		reference = _raise_training(doc, line)
	else:
		reference = _raise_task(doc, line)

	line.db_set("raised_reference", reference, update_modified=False)
	doc.add_comment("Comment", _("Corrective action raised as {0} by {1}.").format(reference, me))
	return {"reference": reference}


def _raise_task(doc, line):
	task = frappe.new_doc("Task")
	task.subject = (line.action or _("Corrective action"))[:140]
	task.description = _(
		"<p>Corrective action from incident <b>{0}</b> ({1} at {2}).</p><p>{3}</p>"
	).format(doc.name, doc.incident_type, doc.location_text, frappe.utils.escape_html(line.action or ""))
	if line.due_on:
		task.exp_end_date = line.due_on
	if doc.project:
		task.project = doc.project
	task.insert(ignore_permissions=True)
	return task.name


def _raise_training(doc, line):
	"""An assignment needs a course, and the action text is not one.

	So this refuses rather than guessing. Picking "the course whose title looks
	closest" is the same class of mistake as matching a reimbursement Supplier by
	name — it succeeds confidently and assigns the wrong thing, and a wrongly
	assigned safety course is worse than none because it reads as done.
	"""
	course = frappe.db.get_value("Training Course", {"course_title": line.action}, "name")
	if not course:
		frappe.throw(
			_(
				"Name the course exactly in the action text to raise a Training Assignment, or "
				"raise this as a Task instead. Guessing which course was meant is how somebody "
				"ends up marked as retrained on the wrong thing."
			),
			title=_("Which course?"),
		)
	if not doc.employee:
		frappe.throw(_("This incident names nobody, so there is nobody to assign a course to."))

	user = frappe.db.get_value("Employee", doc.employee, "user_id")
	if not user:
		frappe.throw(_("That employee has no login, so they cannot be assigned a course."))

	assignment = frappe.new_doc("Training Assignment")
	assignment.course = course
	assignment.user = user
	if line.due_on:
		assignment.due_date = line.due_on
	assignment.insert(ignore_permissions=True)
	return assignment.name
