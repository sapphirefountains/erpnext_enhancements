# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Lead triage: an inbound Lead gets an owner, a first-response deadline, and a chase.

TASK-2026-01473. Between 2026-08-01 and 2026-09-22 this site created **one** Lead
against 29 Opportunities: sales works Opportunities directly, so lead-to-opportunity
conversion and contact rate cannot be computed, and attribution that hangs off a
Lead has nothing to hang on. The decision (2026-08-13) was to keep the Lead stage and
fix the process. This module is the part of the process software can hold:

* **An owner, always.** A website Lead goes to ``web_lead_default_owner`` -- the one
  named person who triages -- or, when that is blank or disabled, round-robin across
  enabled users holding ``lead_triage_role`` (default ``Sales Team``). Not ``Sales
  User``: 16 of 19 enabled users hold it, so as a pool it means "everybody".
* **A deadline.** ``custom_first_response_due`` is the arrival time plus
  ``lead_sla_response_minutes`` of *working* time (Mon-Fri, business hours, the
  company Holiday List), so an enquiry at 16:30 on Friday is due Monday morning.
* **A queue.** An open ToDo for the owner, dated to the deadline, plus the Sales
  Dashboard's Speed-to-Lead widget, which now shows the deadline too.
* **A chase.** ``sweep_first_response_sla`` (every ten minutes) reminds the owner
  once the deadline passes, and escalates once ``lead_sla_escalation_minutes`` of
  working time have gone by. Each fires once per Lead: ``custom_sla_alert`` records
  that it did.

## What counts as a response

A **Sent** Communication of type **Communication** referencing the Lead: an email sent
from the Lead form, an SMS through the telephony gateway, or a Triton call log for an
outbound call. It is the rule Frappe itself uses to stamp ``first_responded_on``
(``communication.update_first_response_time``), and ``RESPONSE_EXISTS_SQL`` is the
only copy of it, shared with the dashboard widget -- a widget that disagrees with the
alert about whether a Lead has been answered is worse than no widget. Automated
messages do not count: an auto-acknowledgement is not a salesperson.

``custom_first_response_at`` is stamped from the same rule by
``stamp_first_response`` (a Communication hook), not by Frappe, because Frappe's
version keys on the bare fieldnames ``first_responded_on`` / ``first_response_time``
and every Custom Field in this app is ``custom_``-prefixed so it can never collide
with a field erpnext adds later.

## Scope

Only Leads that carry a deadline are chased, and only inbound channels stamp one --
today, the website ingress. A Lead a salesperson types in after a phone call already
has its response, and chasing it would train people to ignore the alert. Another
inbound channel (the fountain-move conversion, Triton inbound calls) opts in by
calling :func:`prepare_inbound_lead` and :func:`assign_inbound_lead`.

Everything that can fail here is logged and swallowed: the Lead already exists and is
the thing that mattered. The SLA ships dormant behind ``lead_sla_enabled``; ownership
and the ToDo do not, because a website Lead with no owner is the failure this module
exists to end.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, get_time, get_url_to_form, now_datetime

from erpnext_enhancements.utils import business_hours

SETTINGS_DOCTYPE = "ERPNext Enhancements Settings"

DEFAULT_TRIAGE_ROLE = "Sales Team"
ESCALATION_ROLE = "Sales Manager"
DEFAULT_RESPONSE_MINUTES = 60
DEFAULT_ESCALATION_MINUTES = 240
DEFAULT_DAY_START = datetime.time(8, 0)
DEFAULT_DAY_END = datetime.time(17, 0)

#: Never in a rotation, never an escalation recipient.
SERVICE_USERS = frozenset({"Administrator", "Guest"})

#: DefaultValue key holding the last user the rotation picked.
ROTATION_KEY = "lead_triage_last_owner"

#: Lead statuses that mean "no longer waiting on us for a first touch". An exclusion
#: list on purpose: the Lead status Select is site-configurable, and a new status
#: should default to "still needs a response" rather than silently drop out.
LEAD_CLOSED_STATUSES = ("Converted", "Opportunity", "Quotation", "Lost Quotation", "Do Not Contact")

#: The one definition of "somebody here has answered this Lead". Format with the Lead
#: table's alias. See the module docstring.
RESPONSE_EXISTS_SQL = (
	"exists (select 1 from `tabCommunication` c"
	" where c.reference_doctype = 'Lead' and c.reference_name = {alias}.name"
	" and c.sent_or_received = 'Sent' and c.communication_type = 'Communication')"
)

ALERT_REMINDED = "Reminded"
ALERT_ESCALATED = "Escalated"

#: Bound on one sweep. A backlog bigger than this is a staffing problem, and the next
#: sweep ten minutes later picks up the rest.
SWEEP_LIMIT = 200


# ------------------------------------------------------------------ settings


def _settings():
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def _time_or(value, default):
	try:
		return get_time(value) if value else default
	except Exception:
		return default


def sla_config(settings=None):
	"""The SLA dials, with the declared defaults standing in for a blank or zero.

	Belt and braces with the backfill patch: a Single's defaults never reach the row
	that already exists, so on a site that has not migrated the patch these read None.
	"""
	settings = settings or _settings()
	day_start = _time_or(settings.get("lead_sla_business_start"), DEFAULT_DAY_START)
	day_end = _time_or(settings.get("lead_sla_business_end"), DEFAULT_DAY_END)
	if day_end <= day_start:
		day_start, day_end = DEFAULT_DAY_START, DEFAULT_DAY_END
	response = cint(settings.get("lead_sla_response_minutes")) or DEFAULT_RESPONSE_MINUTES
	escalation = cint(settings.get("lead_sla_escalation_minutes")) or DEFAULT_ESCALATION_MINUTES
	return {
		"enabled": bool(cint(settings.get("lead_sla_enabled") or 0)),
		"response_minutes": response,
		# Escalating before the reminder would skip the owner entirely.
		"escalation_minutes": max(escalation, response),
		"day_start": day_start,
		"day_end": day_end,
	}


def _holiday_checker():
	from erpnext_enhancements.utils.working_days import _holiday_checker

	company = frappe.defaults.get_global_default("company")
	holiday_list = frappe.get_cached_value("Company", company, "default_holiday_list") if company else None
	return _holiday_checker(holiday_list)


def deadline(start, minutes, cfg, is_holiday=None):
	"""``start`` plus ``minutes`` of working time, per ``cfg``."""
	return business_hours.add_business_minutes(
		get_datetime(start), minutes, cfg["day_start"], cfg["day_end"], is_holiday or (lambda _d: False)
	)


# ------------------------------------------------------------------ owner


def next_in_rotation(pool, last):
	"""The user after ``last`` in ``pool`` (sorted), wrapping; the first if ``last`` left."""
	pool = sorted(pool)
	if not pool:
		return None
	later = [user for user in pool if user > (last or "")]
	return later[0] if later else pool[0]


def _enabled_system_users(names):
	if not names:
		return set()
	rows = frappe.get_all(
		"User",
		filters={"name": ["in", list(names)], "enabled": 1, "user_type": "System User"},
		pluck="name",
	)
	return {name for name in rows if name not in SERVICE_USERS}


def users_with_roles(*roles):
	"""Enabled system users holding EVERY role in ``roles``."""
	holders = None
	for role in roles:
		members = set(
			frappe.get_all("Has Role", filters={"parenttype": "User", "role": role}, pluck="parent")
		)
		holders = members if holders is None else holders & members
	return _enabled_system_users(holders or set())


def pick_owner(settings=None):
	"""``(user, how)`` for a new inbound Lead; ``(None, None)`` when nobody qualifies.

	``how`` is "default" or "rotation". The named owner wins; the rotation is the
	backup, for the day that person is disabled or the setting was never filled in.
	"""
	settings = settings or _settings()
	named = (settings.get("web_lead_default_owner") or "").strip()
	if named and _enabled_system_users({named}):
		return named, "default"

	pool = users_with_roles((settings.get("lead_triage_role") or "").strip() or DEFAULT_TRIAGE_ROLE)
	user = next_in_rotation(pool, frappe.db.get_default(ROTATION_KEY))
	if not user:
		return None, None
	frappe.db.set_default(ROTATION_KEY, user)
	return user, "rotation"


def escalation_recipients(settings=None):
	"""Who hears about a Lead nobody has answered.

	``lead_sla_escalate_to`` when it names an enabled user. Otherwise the users holding
	BOTH ``Sales Manager`` and the triage role: ``Sales Manager`` alone is held by nine
	people on this site, and a broadcast is how an escalation becomes wallpaper.
	"""
	settings = settings or _settings()
	named = (settings.get("lead_sla_escalate_to") or "").strip()
	if named and _enabled_system_users({named}):
		return [named]
	role = (settings.get("lead_triage_role") or "").strip() or DEFAULT_TRIAGE_ROLE
	return sorted(users_with_roles(ESCALATION_ROLE, role))


# ------------------------------------------------------------------ inbound


def prepare_inbound_lead(lead, settings=None):
	"""Before insert: give ``lead`` an owner and, when the SLA is on, a deadline.

	Never raises -- a Lead must not be refused because triage could not be planned.
	"""
	try:
		settings = settings or _settings()
		if not lead.get("lead_owner"):
			owner, _how = pick_owner(settings)
			if owner:
				lead.lead_owner = owner
		cfg = sla_config(settings)
		if cfg["enabled"] and hasattr(lead, "custom_first_response_due"):
			lead.custom_first_response_due = deadline(
				now_datetime(), cfg["response_minutes"], cfg, _holiday_checker()
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Lead triage: prepare failed")


def assign_inbound_lead(lead):
	"""After insert: put the Lead in its owner's ToDo list and tell them.

	The ToDo is inserted directly rather than through ``assign_to.add``, which words
	its notification with the session user's name -- on a website submission, "Guest
	assigned a new task to you". The ToDo still sets ``_assign`` on the Lead through
	its own ``on_update``, so the list view's Assigned To filter works as usual.
	"""
	owner = lead.get("lead_owner")
	if not owner:
		return None
	try:
		due = lead.get("custom_first_response_due")
		title = lead.get("lead_name") or lead.name
		if not frappe.db.exists(
			"ToDo",
			{"reference_type": "Lead", "reference_name": lead.name, "allocated_to": owner, "status": "Open"},
		):
			frappe.get_doc(
				{
					"doctype": "ToDo",
					"allocated_to": owner,
					"reference_type": "Lead",
					"reference_name": lead.name,
					"description": _("First response to new Lead {0}").format(title),
					"priority": "High",
					"status": "Open",
					"date": get_datetime(due).date() if due else now_datetime().date(),
					"assigned_by": "Administrator",
				}
			).insert(ignore_permissions=True)

		subject = (
			_("New Lead {0}: first response due {1}").format(title, _format_due(due))
			if due
			else _("New Lead {0} assigned to you").format(title)
		)
		_notify([owner], subject, _lead_message(lead, due), lead.name, kind="Assignment")
		return owner
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Lead triage: assignment failed")
		return None


# ------------------------------------------------------------------ response


def is_response(communication):
	"""The rule in the module docstring, for one Communication."""
	return (
		communication.get("reference_doctype") == "Lead"
		and bool(communication.get("reference_name"))
		and communication.get("sent_or_received") == "Sent"
		and communication.get("communication_type") == "Communication"
	)


def stamp_first_response(doc, method=None):
	"""``Communication.on_update``: stamp the Lead's first response, once.

	on_update rather than after_insert so a Communication linked to the Lead after the
	fact still counts. Never raises: a failure here must not cost somebody their email.
	"""
	if not is_response(doc):
		return
	try:
		if not frappe.db.has_column("Lead", "custom_first_response_at"):
			return
		if frappe.db.get_value("Lead", doc.reference_name, "custom_first_response_at"):
			return
		frappe.db.set_value(
			"Lead",
			doc.reference_name,
			"custom_first_response_at",
			doc.get("communication_date") or doc.get("creation") or now_datetime(),
			update_modified=False,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Lead triage: first-response stamp failed")


def backfill_first_responses():
	"""``after_migrate``: stamp ``custom_first_response_at`` from existing Communications.

	Here rather than in patches.txt because the column is a fixture Custom Field and
	``sync_fixtures`` runs after the post-model-sync patches -- a patch would find no
	column, do nothing, and record itself as done. Idempotent: fills blanks only, so it
	is one cheap statement on every later migrate. Returns rows written.
	"""
	if not frappe.db.has_column("Lead", "custom_first_response_at"):
		return 0
	responses = """
		select c.reference_name as lead, min(c.creation) as first_sent
		from `tabCommunication` c
		where c.reference_doctype = 'Lead'
		  and c.sent_or_received = 'Sent'
		  and c.communication_type = 'Communication'
		group by c.reference_name
	"""
	pending = frappe.db.sql(
		f"select count(*) from `tabLead` l join ({responses}) r on r.lead = l.name"
		" where l.custom_first_response_at is null"
	)[0][0]
	if pending:
		frappe.db.sql(
			f"update `tabLead` l join ({responses}) r on r.lead = l.name"
			" set l.custom_first_response_at = r.first_sent"
			" where l.custom_first_response_at is null"
		)
	return pending or 0


# ------------------------------------------------------------------ the chase


def sla_action(now, due, escalate_at, alert):
	"""``"remind"``, ``"escalate"`` or None for one unanswered Lead. Pure.

	Escalation wins when both are due, which happens after an outage or when the
	feature is switched on over a backlog -- the manager hears once, not the owner
	and then the manager ten minutes apart.
	"""
	if alert == ALERT_ESCALATED or not due:
		return None
	if escalate_at and now >= escalate_at:
		return "escalate"
	if now >= due and not alert:
		return "remind"
	return None


def awaiting_first_response(limit=SWEEP_LIMIT):
	"""Unanswered Leads that carry a deadline and have not been escalated."""
	if not frappe.db.has_column("Lead", "custom_first_response_due"):
		return []
	placeholders = ", ".join(["%s"] * len(LEAD_CLOSED_STATUSES))
	return frappe.db.sql(
		f"""
		select l.name, l.lead_name, l.company_name, l.lead_owner, l.creation,
		       l.custom_first_response_due as due, l.custom_sla_alert as alert
		from `tabLead` l
		where l.custom_first_response_due is not null
		  and l.custom_first_response_at is null
		  and l.status not in ({placeholders})
		  and coalesce(l.custom_sla_alert, '') != %s
		  and not {RESPONSE_EXISTS_SQL.format(alias="l")}
		order by l.custom_first_response_due asc
		limit %s
		""",
		(*LEAD_CLOSED_STATUSES, ALERT_ESCALATED, limit),
		as_dict=True,
	)


def sweep_first_response_sla():
	"""Scheduler, every ten minutes: remind, then escalate, each once per Lead."""
	if _in_maintenance_context():
		return None
	settings = _settings()
	cfg = sla_config(settings)
	if not cfg["enabled"]:
		return None

	is_holiday = _holiday_checker()
	now = now_datetime()
	counts = {"remind": 0, "escalate": 0}
	for row in awaiting_first_response():
		try:
			escalate_at = deadline(row.creation, cfg["escalation_minutes"], cfg, is_holiday)
			action = sla_action(now, get_datetime(row.due), escalate_at, row.alert)
			if not action:
				continue
			if action == "remind":
				recipients = [row.lead_owner] if row.lead_owner else escalation_recipients(settings)
				subject = _("Lead {0} is past its first-response time").format(row.lead_name or row.name)
				_notify(recipients, subject, _lead_message(row, row.due), row.name)
				_set_alert(row.name, ALERT_REMINDED)
			else:
				recipients = sorted(
					set(escalation_recipients(settings)) | ({row.lead_owner} if row.lead_owner else set())
				)
				subject = _("Escalation: Lead {0} has had no response").format(row.lead_name or row.name)
				_notify(recipients, subject, _lead_message(row, row.due, escalated=True), row.name)
				_set_alert(row.name, ALERT_ESCALATED)
			counts[action] += 1
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Lead triage: SLA sweep failed on {row.name}")
	return counts


def _set_alert(lead, value):
	frappe.db.set_value("Lead", lead, "custom_sla_alert", value, update_modified=False)


def _in_maintenance_context():
	flags = frappe.flags
	return bool(flags.in_migrate or flags.in_install or flags.in_patch or flags.in_import)


# ------------------------------------------------------------------ delivery


def _format_due(due):
	"""``Mon Sep 22, 9:30 AM``. Built by hand: ``%-d`` is glibc-only."""
	if not due:
		return ""
	dt = get_datetime(due)
	return f"{dt:%a %b} {dt.day}, {dt.hour % 12 or 12}:{dt:%M %p}"


def _lead_message(lead, due, escalated=False):
	title = lead.get("lead_name") or lead.get("name")
	company = lead.get("company_name")
	lines = [_("Lead: {0}{1}").format(title, f" ({company})" if company else "")]
	if lead.get("lead_owner"):
		lines.append(_("Owner: {0}").format(lead.get("lead_owner")))
	if due:
		lines.append(_("First response was due: {0}").format(_format_due(due)))
	if escalated:
		lines.append(_("Nobody has emailed, texted or called this Lead from ERPNext yet."))
	lines.append(_("A reply sent from the Lead form, or a logged call, clears this."))
	lines.append(get_url_to_form("Lead", lead.get("name")))
	return "\n".join(lines)


def _notify(users, subject, message, lead_name, kind="Alert"):
	"""Bell notification and email to each user. One failure never costs the rest."""
	from erpnext_enhancements import email_style

	for user in dict.fromkeys(u for u in users if u):
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": subject,
					"email_content": message,
					"for_user": user,
					"from_user": "Administrator",
					"type": kind,
					"document_type": "Lead",
					"document_name": lead_name,
				}
			).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Lead triage: notification to {user} failed")
		if kind == "Assignment":
			# Frappe emails Assignment notifications itself, per the user's
			# Notification Settings. Alerts it never emails, so those are sent here.
			continue
		try:
			frappe.sendmail(
				recipients=[user],
				subject=subject,
				message=email_style.wrap(email_style.prose(message), title=subject, eyebrow="Speed to lead"),
				reference_doctype="Lead",
				reference_name=lead_name,
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Lead triage: email to {user} failed")
