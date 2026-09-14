# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Paging three people about a Critical non-conformance, and proving it landed — sub-phase G.

The build spec is unusually specific here: *Critical severity alerts the PM, Production Manager
and President, with acknowledgment timestamped per recipient.* Not "notify the team" — a named
list, and a record of who actually saw it.

The companion paper explains why the President is on that list, and it is the same argument
that put them on the Scope of Work: they should see every project at the one moment ambiguity
is highest, and be alerted immediately the one time a failure is serious enough to matter.
Everything in between stays at the PM level by design, not by oversight.

Why this file is thin
---------------------

Every judgement — who is due, who is a duplicate, when a nag becomes noise — is in
:mod:`erpnext_enhancements.quality.alerting`, which imports no ``frappe`` and is covered by
bench-free tests. This module is the half that talks to the database and the mail queue, and is
deliberately boring, because CI has no Frappe integration-test job and anything decided in here
is decided untested.

Durability, which is the whole design
--------------------------------------

Merging to ``main`` ``FLUSHDB``s the queue redis and destroys every pending background job,
silently. So an enqueue is **never** evidence anybody was told, and three things follow:

1. The dispatch worker writes its acknowledgement rows **first** and stamps ``notified_on``
   only after a send succeeds. A row with no ``notified_on`` is a person who was not reached.
2. :func:`sweep` re-drives on two separate conditions — a Critical NCR that was never
   dispatched at all (the enqueue died before it ran), and a row that was never notified or is
   past its SLA. Neither depends on the other having worked.
3. Nothing here is the only copy of anything. Re-running the sweep an hour later reaches the
   same conclusion from the database rather than from memory.

What it will not do
-------------------

It never raises into a save. A Critical NCR whose alert could not be sent is still a Critical
NCR, and losing the record to protect its notification would be exactly backwards. Failures go
to the Error Log with the NCR named, and the sweep picks them up on the hour.

It never re-nags more than once per calendar day. A sweep that re-sends hourly trains people to
filter it, and a filtered Critical alert is worse than no alert at all.
"""

import frappe
from frappe import _

from erpnext_enhancements import email_style
from erpnext_enhancements.quality import alerting, lifecycle
from erpnext_enhancements.quality.doctype.quality_settings.quality_settings import is_enabled

#: The child table on ``Non Conformance`` holding one row per recipient.
ACK_FIELD = "custom_acknowledgments"
SENT_FIELD = "custom_critical_alert_sent_on"

#: How many Critical NCRs one sweep will look at. A sweep that tries to drain an unbounded
#: backlog in a single job is a sweep that times out and drains none of it.
SWEEP_LIMIT = 50


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def on_ncr_update(doc, method=None):
	"""``Non Conformance`` ``on_update``: page three people when severity becomes Critical.

	Almost every NCR arrives here Minor — :mod:`erpnext_enhancements.quality.routing` raises
	them that way deliberately, because severity is a judgement about consequence that no
	checklist row carries. So the trigger is a *save*, not an insert: the alert fires when a
	person decides this one is Critical, which is the only moment that decision exists.

	Enqueued rather than done inline, and ``enqueue_after_commit`` is not optional.
	``Document.hook``'s ``compose`` increments ``frappe.db._disable_transaction_control`` around
	a ``doc_events`` handler, so this function **cannot commit**, and a worker that started
	before the commit would read the old severity.
	"""
	try:
		if not is_enabled() or not _notifications_enabled():
			return
		severity = getattr(doc, "custom_severity", None) or ""
		sent_on = getattr(doc, SENT_FIELD, None)
		if not alerting.alert_is_due(severity, lifecycle.SEVERITY_CRITICAL, sent_on):
			return
		frappe.enqueue(
			"erpnext_enhancements.quality.critical_alerts.dispatch",
			queue="short",
			enqueue_after_commit=True,
			ncr=doc.name,
		)
	except Exception:
		# A notification is never worth failing the save of the record it is about.
		frappe.log_error(
			title="Quality: could not queue a Critical NCR alert",
			message=f"ncr={getattr(doc, 'name', '?')}\n\n{frappe.get_traceback()}",
		)


def dispatch(ncr):
	"""Resolve recipients, write their rows, send, and stamp — in that order.

	The order is the durability argument. Rows are written and the document stamped **before**
	a single email goes out, so a worker killed halfway leaves rows whose ``notified_on`` is
	empty; :func:`alerting.renag_due` treats those as due immediately and the next sweep sends
	them. The reverse order would lose the fact that anybody was owed an email at all.

	Safe to run twice. The stamp makes a second run a no-op, including the re-entrant
	``on_update`` caused by the save below.
	"""
	try:
		doc = frappe.get_doc("Non Conformance", ncr)
	except frappe.DoesNotExistError:
		return 0

	severity = getattr(doc, "custom_severity", None) or ""
	if not alerting.alert_is_due(severity, lifecycle.SEVERITY_CRITICAL, doc.get(SENT_FIELD)):
		return 0

	recipients = alerting.dedupe_recipients(_candidates(doc))
	now = frappe.utils.now_datetime()

	if not recipients:
		# Stamped anyway, and said out loud on the record. The alternative is an hourly retry
		# that can never succeed, which buries a real problem under its own noise. A Critical
		# non-conformance that can reach nobody is a fact about the role assignments, and it
		# belongs where somebody opening the NCR will see it.
		_stamp(doc.name, now)
		_comment(
			doc.name,
			_(
				"Critical alert could not be sent: this project has no owner on record and "
				"nobody holds Production Manager or President."
			),
		)
		frappe.log_error(
			title="Quality: Critical NCR reached nobody",
			message=f"ncr={doc.name} project={doc.get('custom_project')}",
		)
		return 0

	doc.set(ACK_FIELD, [])
	for user, label in recipients:
		doc.append(ACK_FIELD, {"user": user, "role_label": label, "reminder_count": 0})
	doc.set(SENT_FIELD, now)
	doc.save(ignore_permissions=True)
	frappe.db.commit()

	sent = 0
	for row in doc.get(ACK_FIELD) or []:
		if not _send(doc, row.user, first_time=True):
			continue
		frappe.db.set_value(
			"NCR Acknowledgment",
			row.name,
			"notified_on",
			frappe.utils.now_datetime(),
			update_modified=False,
		)
		sent += 1
	frappe.db.commit()
	return sent


def sweep():
	"""Hourly: re-drive alerts a deploy destroyed, and chase the ones nobody has acknowledged.

	Two independent passes, because they fail independently. The first catches a Critical NCR
	whose enqueue never ran at all — the deploy case — and the second catches a recipient who
	was listed but never reached, or reached and still silent past the SLA.
	"""
	if not is_enabled() or not _notifications_enabled():
		return
	try:
		_redrive_undispatched()
		_chase_unacknowledged()
	except Exception:
		frappe.log_error(title="Quality: Critical NCR sweep failed", message=frappe.get_traceback())


def record_acknowledgement(ncr, user, note=None):
	"""Stamp one recipient's row. Returns ``(acknowledged, total)`` after the write.

	Refuses anybody not on the notified list — see :func:`alerting.may_acknowledge`. An
	acknowledgement from a person who was never told is not evidence of anything, and recording
	it would produce a reassuring row that means nothing.
	"""
	doc = frappe.get_doc("Non Conformance", ncr)
	rows = doc.get(ACK_FIELD) or []
	if not alerting.may_acknowledge(rows, user):
		frappe.throw(
			_("{0} is not on the alert list for this non-conformance.").format(user),
			title=_("Not a recipient"),
		)

	now = frappe.utils.now_datetime()
	for row in rows:
		if row.user != user or row.acknowledged_on:
			continue
		frappe.db.set_value(
			"NCR Acknowledgment",
			row.name,
			{"acknowledged_on": now, "note": (note or "").strip() or None},
			update_modified=False,
		)
		row.acknowledged_on = now

	acknowledged, total = alerting.acknowledgement_state(rows)
	_comment(
		ncr,
		_("{0} acknowledged the Critical alert. {1} of {2} recipients have now seen it.").format(
			frappe.utils.get_fullname(user), acknowledged, total
		),
	)
	return acknowledged, total


# ---------------------------------------------------------------------------
# Recipients
# ---------------------------------------------------------------------------


def _candidates(doc):
	"""``[(user, role_label), ...]`` in priority order, before deduping.

	Order is the label order too, and :func:`alerting.dedupe_recipients` keeps the first reason
	a person qualified. The PM comes first deliberately: on a site this size one person often
	holds two of these, and "you are the PM on this job" is the more useful thing to tell them.
	"""
	out = []
	pm = _project_manager(doc.get("custom_project"))
	if pm:
		out.append((pm, alerting.ROLE_PM))
	for role in (alerting.ROLE_PRODUCTION, alerting.ROLE_PRESIDENT):
		out.extend((user, role) for user in _role_users(role))
	return out


def _project_manager(project):
	"""The project owner, as a User.

	``Project.custom_project_owner`` is a Link to **Employee**, not to User, so this needs the
	``Employee.user_id`` hop. Skipping it silently addresses nobody: the Link value is an
	employee code, and ``sendmail`` would simply never deliver.
	"""
	if not project:
		return None
	try:
		employee = frappe.db.get_value("Project", project, "custom_project_owner")
		if not employee:
			return None
		return frappe.db.get_value("Employee", employee, "user_id")
	except Exception:
		return None


def _role_users(role):
	"""Enabled System Users holding ``role``. Never raises; an unknown Role is simply empty."""
	try:
		holders = frappe.get_all(
			"Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent"
		)
		if not holders:
			return []
		return frappe.get_all(
			"User",
			filters={"name": ["in", holders], "enabled": 1, "user_type": "System User"},
			pluck="name",
		)
	except Exception:
		return []


# ---------------------------------------------------------------------------
# The two sweep passes
# ---------------------------------------------------------------------------


def _redrive_undispatched():
	"""Critical NCRs never dispatched at all. Almost always a deploy that ate the enqueue."""
	for name in frappe.get_all(
		"Non Conformance",
		filters={
			"custom_severity": lifecycle.SEVERITY_CRITICAL,
			SENT_FIELD: ["is", "not set"],
		},
		pluck="name",
		order_by="creation asc",
		limit=SWEEP_LIMIT,
	):
		try:
			dispatch(name)
		except Exception:
			frappe.log_error(
				title="Quality: Critical NCR re-drive failed",
				message=f"ncr={name}\n\n{frappe.get_traceback()}",
			)


def _chase_unacknowledged():
	"""Recipients never reached, or reached and still silent past the SLA.

	The status filter uses ``not in`` rather than a ``<`` against anything: a closed NCR owes
	nobody an acknowledgement, and chasing one would be the clearest possible way to teach
	people that this alert is not worth reading.
	"""
	sla = _sla_hours()
	now = frappe.utils.now_datetime()

	for name in frappe.get_all(
		"Non Conformance",
		filters={
			"custom_severity": lifecycle.SEVERITY_CRITICAL,
			SENT_FIELD: ["is", "set"],
			"status": ["not in", (lifecycle.NCR_VERIFIED, lifecycle.NCR_CLOSED)],
		},
		pluck="name",
		order_by="creation asc",
		limit=SWEEP_LIMIT,
	):
		try:
			doc = frappe.get_doc("Non Conformance", name)
			for row in alerting.outstanding(doc.get(ACK_FIELD) or [], now, sla):
				if not _send(doc, row.user, first_time=not row.notified_on):
					continue
				stamped = frappe.utils.now_datetime()
				frappe.db.set_value(
					"NCR Acknowledgment",
					row.name,
					{
						"notified_on": row.notified_on or stamped,
						"last_reminded_on": stamped,
						"reminder_count": (row.reminder_count or 0) + 1,
					},
					update_modified=False,
				)
			frappe.db.commit()
		except Exception:
			frappe.log_error(
				title="Quality: Critical NCR chase failed",
				message=f"ncr={name}\n\n{frappe.get_traceback()}",
			)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def _send(doc, user, first_time=True):
	"""One email to one person. Returns whether it was queued; never raises."""
	try:
		email = frappe.db.get_value("User", user, "email") or user
		if not email or "@" not in email:
			return False
		link = frappe.utils.get_url_to_form("Non Conformance", doc.name)
		subject = _("Critical non-conformance: {0}").format(doc.get("subject") or doc.name)
		frappe.sendmail(
			recipients=[email],
			subject=subject if first_time else _("Still unacknowledged — {0}").format(subject),
			message=email_style.wrap(
				_body(doc, link, first_time),
				title=subject,
				eyebrow=_("Quality"),
				preheader=_("A Critical non-conformance is waiting for your acknowledgement."),
			),
			reference_doctype="Non Conformance",
			reference_name=doc.name,
		)
		return True
	except Exception:
		frappe.log_error(
			title="Quality: Critical NCR email failed",
			message=f"ncr={doc.name} user={user}\n\n{frappe.get_traceback()}",
		)
		return False


def _body(doc, link, first_time):
	"""What the email says.

	It leads with the finding rather than with the workflow, because the reader's first
	question is what broke, not what this system wants from them. The acknowledgement ask comes
	after they know what they would be acknowledging.
	"""
	rows = [
		(_("Project"), doc.get("custom_project") or _("Not linked")),
		(
			_("Raised by"),
			frappe.utils.get_fullname(doc.get("custom_raised_by"))
			if doc.get("custom_raised_by")
			else _("Unknown"),
		),
		(_("Responsible"), doc.get("custom_responsible_party") or _("Not set")),
		(_("Status"), doc.get("status") or ""),
	]
	if doc.get("custom_inspection"):
		rows.append((_("From inspection"), doc.get("custom_inspection")))

	parts = []
	if not first_time:
		parts.append(
			email_style.callout(
				_("This is still waiting for your acknowledgement."), tone="warning"
			)
		)
	parts.append(
		email_style.callout(
			_("A Critical non-conformance has been raised on this project."), tone="danger"
		)
	)
	parts.append(email_style.h(doc.get("subject") or doc.name))
	parts.append(email_style.kv(rows))
	if doc.get("details"):
		parts.append(email_style.rich(doc.get("details")))
	parts.append(
		email_style.p(
			_(
				"Open the record and press Acknowledge. That stamps your name and the time, "
				"which is the only record that this reached you."
			)
		)
	)
	parts.append(email_style.button(link, _("Open the non-conformance"), tone="danger"))
	parts.append(email_style.button_fallback(link))
	return "".join(parts)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _notifications_enabled():
	try:
		return bool(frappe.db.get_single_value("Quality Settings", "notifications_enabled"))
	except Exception:
		return False


def _sla_hours():
	try:
		return int(frappe.db.get_single_value("Quality Settings", "critical_ack_sla_hours") or 24)
	except Exception:
		return 24


def _stamp(ncr, when):
	frappe.db.set_value("Non Conformance", ncr, SENT_FIELD, when, update_modified=False)


def _comment(ncr, text):
	"""Timeline note. Never raises — a missing comment must not lose an acknowledgement."""
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "Non Conformance",
				"reference_name": ncr,
				"content": text,
			}
		).insert(ignore_permissions=True)
	except Exception:
		pass
