# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Closed-Won hand-off gate, and the buttons that get you through it.

Step 2 of the PRO-0204 tracker ("Hold Hand-Off Meeting") used to live on the
**Project**, which meant it only existed *after* the project had been created —
the gate sat downstream of the thing it was supposed to gate. The 2026-08-06
compliance audit found the predictable result: automated steps completed 100% of
the time, manual ones 5%, all 17 pending hand-off meetings were past SLA, and 8
of 28 new projects had been created outside the process entirely.

So the hand-off moves onto the **Opportunity**, in front of project creation:

* :func:`handoff_block_reason` — the gate predicate. Enforced from
  ``Project.before_insert`` (:func:`erpnext_enhancements.process_steps.enforce_handoff_gate`),
  from the desk mapper, and from the background creator. Three layers because
  the audit found side paths; ``before_insert`` is the authoritative one,
  because it is the only hook that survives both ``ignore_permissions`` and
  ``ignore_validate`` (the app's own creation path sets the latter).
* :func:`schedule_handoff_meeting` — the button that *does the step*: resolves
  attendees, creates a calendar Event, and emails the invite. The design intent
  from the meeting is that the compliant path is the easiest path, so the button
  holds the meeting rather than merely recording that you held it.
* :func:`mark_handoff_complete` — one click, server-stamped who/when. Closes
  step 2.
* :func:`skip_handoff` — the escape hatch, System Manager only and requiring a
  written reason. Silence must be impossible; a *visible* skip is fine.

**What the gate opens on (changed in v1.263.0).** It used to be
:func:`mark_handoff_complete` — the project waited until somebody recorded that
the meeting had *happened*. In practice the meeting is routinely booked for a
day or two out while the project needs to start now, so the gate was inviting
exactly the lie the audit was about: tick "held" for a meeting still in the
diary. The gate now opens as soon as a meeting is **booked**
(``custom_handoff_event``), and recording it afterwards is a separate,
non-blocking act. Booking is the part that cannot be faked into existence
without inviting three functions and putting it on a calendar, so it is the
better thing to gate on.

Attendees are configured, never hard-coded: **ERPNext Enhancements Settings →
Hand-Off Attendee Roles** maps each function (Sales / Production / Billing) to an
explicit address or a Role whose holders are invited.

**Step 4's note to Billing (v1.327.0).** The other communication this module
routes is the "Create Accounting Project & Send Invoice" note, composed from the
Project's step-4 button. It used to take the configured Billing list and say
nothing about money, which left Billing to go and find out how much of the deal
to invoice — so the deal now carries the answer. **First Invoice Percentage** and
**Billing Email** are Custom Fields on *both* Opportunity and Project;
:func:`billing_terms` resolves them field by field, Project over Opportunity, and
:func:`billing_notice_context` hands the composer the recipients, the percentage
and the money it works out to. A blank Billing Email means the configured Billing
route, which is still ``billing@sapphirefountains.com`` — the field is an
override, not a redirect that had to be filled in for the old behaviour to keep
working.
"""

import re

import frappe
from frappe import _
from frappe.utils import (
	add_to_date,
	cint,
	flt,
	get_datetime,
	get_url_to_form,
	getdate,
	now_datetime,
	validate_email_address,
)

from erpnext_enhancements.feature_flags import handoff_gate_enabled, process_automation_enabled
from erpnext_enhancements.utils.working_days import add_working_days

WON_STATUS = "Closed Won"

#: The one message the whole gate speaks with. The meeting asked for an error
#: that says what to do, not what went wrong. Reworded in v1.263.0 with the gate
#: itself: what it now asks for is a *booked* meeting, and an error that asks for
#: something other than what unblocks it is worse than no error.
GATE_MESSAGE = "Schedule the Hand-Off Meeting on the Opportunity first."

#: Business days from Closed Won to the hand-off meeting (meeting decision).
HANDOFF_SLA_BUSINESS_DAYS = 2

#: Business days from Closed Won to the project launch meeting — the headline
#: goal. Tracked separately from step 7's own SLA, because a step can be on time
#: against the step before it and still blow this.
LAUNCH_GOAL_BUSINESS_DAYS = 7

#: Default meeting length. Short on purpose: the meeting is a handover, and a
#: 15-minute default is far likelier to actually get booked than an hour.
DEFAULT_DURATION_MINUTES = 15

#: First and last hour (site time) a default slot may be proposed in.
WORKDAY_START_HOUR = 9
WORKDAY_END_HOUR = 16

FUNCTIONS = ("Sales", "Production", "Billing")

#: Used when the settings table has never been filled in. These mirror the
#: patch-seeded defaults so a site that cleared the table still gets somewhere
#: useful rather than an empty invite.
FALLBACK_ATTENDEES = {
	"Sales": "sales@sapphirefountains.com",
	"Production": "production@sapphirefountains.com",
	"Billing": "billing@sapphirefountains.com",
}


# ---------------------------------------------------------------------------
# Schema guards
# ---------------------------------------------------------------------------


def _has_gate_fields():
	"""True once the hand-off Custom Fields exist.

	``doc_events`` fire during ERPNext's own test bootstrap, before this app's
	custom fields are created — the gate must be inert there rather than turning
	a fresh-DB install into a crash.
	"""
	try:
		return bool(frappe.db.has_column("Opportunity", "custom_handoff_gate_applies"))
	except Exception:
		return False


def _settings():
	return frappe.get_cached_doc("ERPNext Enhancements Settings")


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def handoff_block_reason(opportunity):
	"""Why ``opportunity`` may not produce a Project yet — or ``None`` if it may.

	Returns a message rather than throwing so every caller can decide how to
	refuse: the ``before_insert`` hook throws, the desk mapper throws earlier for
	a nicer message, and the background creator logs and reports instead of
	dying in a worker.

	Blocks only when *all* of these hold, so the gate can never strand work it
	was not meant to cover:

	* both switches are on (the suite master switch and the gate's own);
	* the schema has landed;
	* the Opportunity is Closed Won;
	* ``custom_handoff_gate_applies`` is set — i.e. it transitioned into Closed
	  Won *after* this feature shipped. The pre-existing backlog (227 deals at
	  the time of writing) is deliberately exempt; see
	  ``patches/ensure_handoff_gate_fields``;
	* and no hand-off meeting has been booked, recorded or skipped. Booked is
	  enough (v1.263.0) — see the module docstring for why the gate moved off
	  "recorded".
	"""
	if not opportunity:
		return None

	# Schema guard FIRST, then the switches. This function is the first thing on
	# Project before_insert, so anything it raises blocks every project creation
	# on the site — and it is a process control, not a security control. Letting
	# one project through while the schema or the Settings Single is momentarily
	# unreadable is much cheaper than stopping all of them, so it fails open.
	try:
		if not _has_gate_fields():
			return None
		if not process_automation_enabled() or not handoff_gate_enabled():
			return None
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Hand-off gate: switch read failed")
		return None

	row = frappe.db.get_value(
		"Opportunity",
		opportunity,
		[
			"name",
			"status",
			"custom_handoff_gate_applies",
			"custom_handoff_meeting_held",
			"custom_handoff_event",
		],
		as_dict=True,
	)
	if not row:
		return None
	if row.status != WON_STATUS:
		return None
	if not cint(row.custom_handoff_gate_applies):
		return None
	if cint(row.custom_handoff_meeting_held):
		return None
	if row.custom_handoff_event:
		# A meeting is on the calendar. Keying on the stored link rather than on
		# the Event still existing is deliberate: deleting the Event is not a way
		# to un-book a hand-off, and a gate that re-closes behind a project that
		# already exists would strand the deal with no button that helps.
		return None
	return _(GATE_MESSAGE)


def throw_if_handoff_missing(opportunity):
	"""Refuse project creation when the hand-off is unrecorded. No-op otherwise."""
	reason = handoff_block_reason(opportunity)
	if reason:
		frappe.throw(reason, title=_("Hand-Off Required"))


# ---------------------------------------------------------------------------
# Attendee resolution
# ---------------------------------------------------------------------------


def _role_holder_emails(role):
	"""Enabled System Users holding ``role``, as addresses.

	A Role that does not exist is skipped rather than raising — "Account
	Executive" is a Select value on Process Step Template, not a real Role on
	this site, and configuration is allowed to name one that has not been created
	yet.
	"""
	if not role or not frappe.db.exists("Role", role):
		return []
	holders = frappe.get_all(
		"Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent"
	)
	if not holders:
		return []
	users = frappe.get_all(
		"User",
		filters={"name": ("in", holders), "enabled": 1, "user_type": "System User"},
		pluck="name",
	)
	return [user for user in users if user not in ("Administrator", "Guest")]


def _configured_rows():
	"""``{function: [row, ...]}`` from the settings table."""
	rows = {function: [] for function in FUNCTIONS}
	try:
		configured = _settings().get("handoff_attendees") or []
	except Exception:
		configured = []
	for row in configured:
		if row.get("function") in rows:
			rows[row.get("function")].append(row)
	return rows


def _dedupe(addresses):
	"""Order-preserving de-dupe of valid addresses (case-insensitive)."""
	seen = set()
	out = []
	for address in addresses:
		address = (address or "").strip()
		if not address:
			continue
		key = address.lower()
		if key in seen:
			continue
		try:
			validate_email_address(address, throw=True)
		except Exception:
			continue
		seen.add(key)
		out.append(address)
	return out


@frappe.whitelist()
def resolve_attendees(opportunity=None):
	"""Suggested hand-off attendees by function — Sales, Production, Billing.

	Resolution order per configured row: an explicit ``email`` wins, otherwise
	every holder of ``role``. Sales additionally picks up the deal's own owner,
	which is the whole point of inviting "Sales" rather than a distribution list.
	Everything returned is a suggestion: the dialog prefills it and the user can
	edit all three before sending.
	"""
	if opportunity and not frappe.has_permission("Opportunity", "read", doc=opportunity):
		frappe.throw(_("Not permitted to read this Opportunity."), frappe.PermissionError)

	configured = _configured_rows()
	resolved = {}
	for function in FUNCTIONS:
		addresses = []
		if function == "Sales" and opportunity:
			# The AE who owns the deal, ahead of any list — they are the one
			# person who cannot usefully be represented by a group address.
			owner = frappe.db.get_value("Opportunity", opportunity, "opportunity_owner")
			if owner:
				addresses.append(owner)
		for row in configured[function]:
			if row.get("email"):
				addresses.append(row.get("email"))
			else:
				addresses.extend(_role_holder_emails(row.get("role")))
		if not configured[function]:
			addresses.append(FALLBACK_ATTENDEES[function])
		resolved[function] = _dedupe(addresses)
	return resolved


@frappe.whitelist()
def attendee_options():
	"""Addresses the meeting dialog offers in its attendee pickers.

	Enabled desk users, plus every address the hand-off configuration names
	(explicit rows, the holders of the configured Roles, and the fallbacks). These
	are *suggestions only* — the dialog's attendee fields stay free text, so an
	address nobody has ever entered before is still perfectly valid to type. The
	list exists because typing ``firstname.lastname@sapphirefountains.com`` from
	memory is how an invite quietly goes to nobody.
	"""
	if not frappe.has_permission("Opportunity", "read"):
		frappe.throw(_("Not permitted."), frappe.PermissionError)

	addresses = frappe.get_all(
		"User",
		filters={"enabled": 1, "user_type": "System User"},
		pluck="name",
	)
	addresses = [user for user in addresses if user not in ("Administrator", "Guest")]

	for function, rows in _configured_rows().items():
		for row in rows:
			if row.get("email"):
				addresses.append(row.get("email"))
			else:
				addresses.extend(_role_holder_emails(row.get("role")))
		if not rows:
			addresses.append(FALLBACK_ATTENDEES[function])

	return sorted(_dedupe(addresses), key=str.lower)


@frappe.whitelist()
def project_meeting_attendees(project):
	"""Attendees for the step-7 launch meeting (the dialog's prefill)."""
	if not frappe.has_permission("Project", "read", doc=project):
		frappe.throw(_("Not permitted to read this Project."), frappe.PermissionError)
	return resolve_project_attendees(project)


@frappe.whitelist()
def schedule_project_meeting(project, starts_on=None, attendees=None, duration_minutes=None):
	"""Create the project launch meeting Event and send the invite.

	The same act as :func:`schedule_handoff_meeting`, one step later and hung off
	a Project instead of an Opportunity — so it shares the dialog, the Event
	shape and the invite. Kept as its own endpoint rather than overloading the
	first with a doctype argument: the permission check differs (Project write,
	not Opportunity write), and collapsing them would make that easy to get wrong.
	"""
	doc = frappe.get_doc("Project", project)
	if not doc.has_permission("write"):
		frappe.throw(_("Not permitted to modify this Project."), frappe.PermissionError)

	addresses = _parse_attendees(attendees)
	if not addresses:
		addresses = _parse_attendees(resolve_project_attendees(project))
	if not addresses:
		frappe.throw(_("No attendees could be resolved for this project."))

	start = get_datetime(starts_on) if starts_on else next_business_slot()
	minutes = cint(duration_minutes) or DEFAULT_DURATION_MINUTES
	end = add_to_date(start, minutes=minutes)

	label = doc.get("project_name") or doc.name
	link = get_url_to_form("Project", doc.name)
	subject = _("Project Launch Meeting — {0}").format(label)

	event = _create_event(
		subject=subject,
		starts_on=start,
		ends_on=end,
		description=_meeting_description(label, link, addresses),
		attendees=addresses,
		reference_doctype="Project",
		reference_docname=doc.name,
	)
	sent = _send_invite(event, addresses, subject, label, link)
	doc.add_comment(
		"Comment",
		_("Project launch meeting scheduled for {0} with {1}.").format(
			frappe.utils.format_datetime(start), ", ".join(addresses)
		),
	)
	return {"event": event.name, "starts_on": str(start), "attendees": addresses, "invited": sent}


@frappe.whitelist()
def project_handoff_context(project):
	"""What the Project's progress bar needs beyond its own child rows.

	The 7-business-day launch deadline (which lives on the Opportunity, not the
	Project) and which flow the invoice step's button should run. One call rather
	than a boot flag for the latter: an operator flipping the setting should see
	the button change on their next page load, not their next login.
	"""
	if not frappe.has_permission("Project", "read", doc=project):
		frappe.throw(_("Not permitted to read this Project."), frappe.PermissionError)

	row = frappe.db.get_value("Project", project, ["name", "custom_opportunity"], as_dict=True)
	deadline = launch_deadline_for_project(row) if row else None
	try:
		flow = _settings().get("handoff_invoice_flow")
	except Exception:
		flow = None
	return {
		"launch_deadline": str(deadline) if deadline else None,
		"invoice_flow": flow or "Manual Billing Email",
	}


def resolve_project_attendees(project):
	"""Attendees for the step-7 project launch meeting: the project team.

	The PM (``custom_project_owner``) plus everyone with an open assignment on the
	project, then the configured Production/Billing lists so the launch meeting
	reaches the same functions the hand-off did.
	"""
	doc = frappe.db.get_value(
		"Project", project, ["name", "custom_project_owner", "custom_opportunity"], as_dict=True
	)
	if not doc:
		return {function: [] for function in FUNCTIONS}

	team = []
	if doc.custom_project_owner:
		user_id = frappe.db.get_value("Employee", doc.custom_project_owner, "user_id")
		if user_id:
			team.append(user_id)
	team.extend(
		frappe.get_all(
			"ToDo",
			filters={"reference_type": "Project", "reference_name": project, "status": "Open"},
			pluck="allocated_to",
		)
	)

	resolved = resolve_attendees(doc.custom_opportunity if doc.custom_opportunity else None)
	resolved["Sales"] = _dedupe(team + resolved.get("Sales", []))
	return resolved


# ---------------------------------------------------------------------------
# Billing routing and invoice terms (step 4)
# ---------------------------------------------------------------------------

#: The invoice terms, carried on **both** Opportunity and Project under the same
#: fieldnames. The Opportunity is where Sales agrees them; the Project's copy is
#: seeded from it at creation and, once edited, wins — the PM is the one holding
#: the deal by the time step 4 comes round.
BILLING_EMAIL_FIELD = "custom_billing_email"
FIRST_INVOICE_PERCENTAGE_FIELD = "custom_first_invoice_percentage"

#: The Currency field the first-invoice percentage is a percentage *of*, per
#: doctype. Both are the sell price, not the cost.
AMOUNT_FIELD = {"Project": "custom_project_dollar_amount", "Opportunity": "opportunity_amount"}


def _billing_terms_row(doctype, name):
	"""``(billing_email, first_invoice_percentage, amount)`` off one record.

	All three blank when the record or the columns are missing. Guarded on the
	columns rather than on the values because ``doc_events`` and the desk mapper
	both run during ERPNext's own test bootstrap, before this app's Custom Fields
	exist — a missing column has to read as "not configured", not raise.
	"""
	if not name:
		return None, None, None
	fields = [BILLING_EMAIL_FIELD, FIRST_INVOICE_PERCENTAGE_FIELD]
	try:
		if not all(frappe.db.has_column(doctype, field) for field in fields):
			return None, None, None
		amount_field = AMOUNT_FIELD.get(doctype)
		if amount_field and frappe.db.has_column(doctype, amount_field):
			fields = [*fields, amount_field]
		else:
			amount_field = None
		row = frappe.db.get_value(doctype, name, fields, as_dict=True)
	except Exception:
		return None, None, None
	if not row:
		return None, None, None
	return (
		row.get(BILLING_EMAIL_FIELD),
		row.get(FIRST_INVOICE_PERCENTAGE_FIELD),
		row.get(amount_field) if amount_field else None,
	)


def billing_terms(project=None, opportunity=None):
	"""The step-4 invoice terms for a project: address, percentage, and base.

	Resolved field by field, Project first and Opportunity as the fallback —
	*not* whole-record, because a Project created through the desk mapper carries
	neither field (ERPNext's own mapping table does not know about them) while its
	Opportunity carries both, and a Project whose PM has set only the percentage
	must still inherit the address.

	``opportunity`` is looked up from ``Project.custom_opportunity`` when not
	given; pass it to save the read, or on its own to resolve terms for a deal
	that has no project yet.
	"""
	email = percentage = amount = None
	if project:
		email, percentage, amount = _billing_terms_row("Project", project)
		if opportunity is None:
			try:
				opportunity = frappe.db.get_value("Project", project, "custom_opportunity")
			except Exception:
				opportunity = None
	# Falsy, not ``is None``: frappe declares Percent and Currency columns NOT NULL
	# DEFAULT 0, so an unfilled percentage reads 0.0 off a real site and None only
	# off a dict. Keying the fallback on None would make it fire in tests and
	# never in production — the Opportunity's terms would be silently unreachable
	# on every project that has an amount and an address.
	if opportunity and not (email and percentage and amount):
		opp_email, opp_percentage, opp_amount = _billing_terms_row("Opportunity", opportunity)
		email = email or opp_email
		percentage = percentage or opp_percentage
		amount = amount or opp_amount
	return {
		"billing_email": (email or "").strip() or None,
		"first_invoice_percentage": flt(percentage) or None,
		"amount": flt(amount) or None,
	}


def billing_recipients(project=None, opportunity=None, billing_email=None):
	"""Where the step-4 "create the accounting project & invoice" note is sent.

	An explicit **Billing Email** on the Project (or, failing that, the
	Opportunity) is an override and replaces the list — that is the whole point
	of the field, and the composer shows the addresses for editing before
	anything is sent. With the field blank it falls through to the configured
	**Billing** attendee rows, and then to ``billing@sapphirefountains.com``, so
	the default route is unchanged from before the field existed.

	Accepts several addresses in the one field, comma- or semicolon-separated.

	The fallback deliberately reads the settings directly rather than going
	through :func:`resolve_attendees`, which permission-checks the Opportunity:
	the PM clicking this button on a Project need not be able to read the deal,
	and the Billing list does not depend on it anyway.
	"""
	if billing_email is None:
		billing_email = billing_terms(project=project, opportunity=opportunity)["billing_email"]
	if billing_email:
		explicit = _dedupe(_split_addresses(billing_email))
		if explicit:
			return explicit
		# A typo'd address is worse than no override: it would silently send the
		# hand-off note nowhere. Fall through to the configured route instead.
	return _configured_billing()


def _split_addresses(value):
	"""One free-text address field -> a list. Commas, semicolons, newlines."""
	return [part for part in re.split(r"[,;\n]", str(value or "")) if part.strip()]


def _configured_billing():
	"""The Billing attendee rows, or the fallback address when none are set."""
	rows = _configured_rows()["Billing"]
	addresses = []
	for row in rows:
		if row.get("email"):
			addresses.append(row.get("email"))
		else:
			addresses.extend(_role_holder_emails(row.get("role")))
	if not rows:
		addresses.append(FALLBACK_ATTENDEES["Billing"])
	return _dedupe(addresses)


@frappe.whitelist()
def billing_notice_context(project):
	"""Everything the step-4 composer prefills itself with, in one call.

	The button opens a dialog on a click, so the round trips are visible: this
	returns the recipients *and* the invoice terms rather than making the client
	resolve attendees and then read two fields off two doctypes.

	``first_invoice_amount`` is the money the percentage works out to, computed
	here so the note carries a figure Billing can invoice against rather than a
	percentage they have to apply themselves. It is ``None`` when either half is
	missing — a percentage of an unknown amount is worse than silence.
	"""
	if not frappe.has_permission("Project", "read", doc=project):
		frappe.throw(_("Not permitted to read this Project."), frappe.PermissionError)

	terms = billing_terms(project=project)
	opportunity = None
	try:
		opportunity = frappe.db.get_value("Project", project, "custom_opportunity")
	except Exception:
		opportunity = None

	percentage = terms["first_invoice_percentage"]
	amount = terms["amount"]
	return {
		"recipients": billing_recipients(opportunity=opportunity, billing_email=terms["billing_email"]),
		"first_invoice_percentage": percentage,
		"project_amount": amount,
		"first_invoice_amount": (
			flt(flt(amount) * flt(percentage) / 100.0, 2) if percentage and amount else None
		),
	}


# ---------------------------------------------------------------------------
# Slot suggestion
# ---------------------------------------------------------------------------


def next_business_slot(reference=None):
	"""The default proposed meeting time: next business day, 09:00 site time.

	Uses the same working-day helper as the SLA maths (so the configured Holiday
	List is honoured), then pins the hour into the working day. Deliberately a
	*suggestion* — the dialog lets the user move it.
	"""
	base = get_datetime(reference) if reference else now_datetime()
	slot = add_working_days(base, 1, holiday_list=_handoff_holiday_list())
	return slot.replace(hour=WORKDAY_START_HOUR, minute=0, second=0, microsecond=0)


def _handoff_holiday_list():
	return frappe.db.get_single_value("ERPNext Enhancements Settings", "handoff_holiday_list")


# ---------------------------------------------------------------------------
# The meeting
# ---------------------------------------------------------------------------


def _participant_reference(address):
	"""``(reference_doctype, reference_docname)`` for an Event Participants row.

	``Event Participants`` marks *both* reference fields ``reqd`` — ``email`` is
	only an optional extra — so a group address like ``production@`` simply
	cannot be expressed as a participant row. Those attendees still receive the
	invite (the email is what actually matters); they just do not appear in the
	Event's participants grid. Returns ``None`` when nothing links.
	"""
	employee = frappe.db.get_value("Employee", {"user_id": address, "status": "Active"}, "name")
	if employee:
		return "Employee", employee
	contact = frappe.db.get_value("Contact", {"email_id": address}, "name")
	if contact:
		return "Contact", contact
	return None


def _meeting_description(label, link, attendees):
	lines = [
		f"<p>{frappe.utils.escape_html(_('Closed-Won hand-off for {0}.').format(label))}</p>",
		f"<p><a href='{link}'>{frappe.utils.escape_html(link)}</a></p>",
		f"<p><b>{frappe.utils.escape_html(_('Attendees'))}:</b> "
		f"{frappe.utils.escape_html(', '.join(attendees))}</p>",
	]
	return "".join(lines)


def _create_event(subject, starts_on, ends_on, description, attendees, reference_doctype, reference_docname):
	"""Insert the calendar Event and link it back to its source document.

	Inserted with ``ignore_permissions`` on purpose: ``Event`` on this site grants
	create only to System Manager and Finance Team, so the Sales user clicking the
	button cannot insert one under their own permissions. The caller has already
	checked *write on the source document*, which is the permission that actually
	means "you may run this process step".
	"""
	event = frappe.new_doc("Event")
	event.subject = subject
	event.event_category = "Meeting"
	event.event_type = "Private"
	event.status = "Open"
	event.starts_on = starts_on
	event.ends_on = ends_on
	event.description = description
	event.reference_doctype = reference_doctype
	event.reference_docname = reference_docname

	for address in attendees:
		reference = _participant_reference(address)
		if not reference:
			continue
		event.append(
			"event_participants",
			{"reference_doctype": reference[0], "reference_docname": reference[1], "email": address},
		)

	event.insert(ignore_permissions=True)
	return event


def _send_invite(event, attendees, subject, label, link):
	"""Email the invite, with a calendar attachment.

	Note the attachment is ``METHOD:PUBLISH`` — an "add to calendar" file, not a
	``METHOD:REQUEST`` invite with RSVP tracking. ``travel_management.ics``
	deliberately stops short of REQUEST semantics; extending it is a separate
	piece of work. The email is the invite; the attachment is the convenience.

	Failures are logged and swallowed: the Event exists and the step is recorded,
	and losing the email must not roll that back.
	"""
	if not attendees:
		return False
	from erpnext_enhancements.travel_management.ics import build_ics

	try:
		ics = build_ics(
			[
				{
					"uid": f"{frappe.scrub(event.name)}@{frappe.local.site}",
					"summary": event.subject,
					"start": event.starts_on,
					"end": event.ends_on,
					"description": _("Closed-Won hand-off for {0}.").format(label),
					"url": link,
				}
			]
		)
		frappe.sendmail(
			recipients=attendees,
			subject=subject,
			message=(
				f"<p>{frappe.utils.escape_html(_('You are invited to the Closed-Won hand-off meeting for {0}.').format(label))}</p>"
				f"<p><b>{frappe.utils.escape_html(_('When'))}:</b> "
				f"{frappe.utils.escape_html(frappe.utils.format_datetime(event.starts_on))}</p>"
				f"<p><a href='{link}'>{frappe.utils.escape_html(link)}</a></p>"
			),
			attachments=[{"fname": f"{frappe.scrub(event.name)}.ics", "fcontent": ics}],
			reference_doctype=event.reference_doctype,
			reference_name=event.reference_docname,
		)
		return True
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Hand-off meeting invite failed")
		return False


@frappe.whitelist()
def schedule_handoff_meeting(opportunity, starts_on=None, attendees=None, duration_minutes=None):
	"""Create the hand-off meeting Event and send the invite.

	``attendees`` is whatever the dialog ended up with (a list, or a
	comma/newline-separated string over HTTP); when omitted the configured
	defaults are resolved server-side, so the endpoint is usable without the
	dialog. Does **not** mark the step complete — holding the meeting and having
	held it are different facts, and the audit was about the difference.
	"""
	doc = frappe.get_doc("Opportunity", opportunity)
	if not doc.has_permission("write"):
		frappe.throw(_("Not permitted to modify this Opportunity."), frappe.PermissionError)

	addresses = _parse_attendees(attendees)
	if not addresses:
		resolved = resolve_attendees(opportunity)
		addresses = _dedupe([a for function in FUNCTIONS for a in resolved.get(function, [])])
	if not addresses:
		frappe.throw(
			_("No hand-off attendees could be resolved. Set them in ERPNext Enhancements Settings.")
		)

	start = get_datetime(starts_on) if starts_on else next_business_slot()
	minutes = cint(duration_minutes) or DEFAULT_DURATION_MINUTES
	end = add_to_date(start, minutes=minutes)

	label = doc.get("customer_name") or doc.get("party_name") or doc.name
	link = get_url_to_form("Opportunity", doc.name)
	subject = _("Hand-Off Meeting — {0}").format(label)

	event = _create_event(
		subject=subject,
		starts_on=start,
		ends_on=end,
		description=_meeting_description(label, link, addresses),
		attendees=addresses,
		reference_doctype="Opportunity",
		reference_docname=doc.name,
	)

	sent = _send_invite(event, addresses, subject, label, link)

	doc.db_set("custom_handoff_event", event.name, update_modified=False)
	doc.add_comment(
		"Comment",
		_("Hand-off meeting scheduled for {0} with {1}.").format(
			frappe.utils.format_datetime(start), ", ".join(addresses)
		),
	)
	return {"event": event.name, "starts_on": str(start), "attendees": addresses, "invited": sent}


def _parse_attendees(attendees):
	"""Accept a list, a JSON array, or a comma/newline-separated string."""
	if not attendees:
		return []
	if isinstance(attendees, str):
		try:
			attendees = frappe.parse_json(attendees)
		except Exception:
			attendees = attendees.replace("\n", ",").split(",")
	if isinstance(attendees, dict):
		attendees = [a for value in attendees.values() for a in (value or [])]
	return _dedupe(list(attendees))


# ---------------------------------------------------------------------------
# Recording the step
# ---------------------------------------------------------------------------


def _stamp_handoff(doc, skip_reason=None):
	"""Write the completion stamps. Server clock and server session, always.

	``db_set`` rather than ``save``: these are read-only fields on a document the
	user may not otherwise be allowed to write, and going through ``save`` would
	re-run the whole Opportunity validation chain (including the rank gate) for
	what is a single-fact stamp.
	"""
	now = now_datetime()
	doc.db_set(
		{
			"custom_handoff_meeting_held": 1,
			"custom_handoff_meeting_on": now,
			"custom_handoff_meeting_by": frappe.session.user,
			"custom_handoff_skip_reason": skip_reason,
		},
		update_modified=False,
	)
	_mirror_onto_project(doc.name, now, frappe.session.user, skip_reason)
	return now


def _mirror_onto_project(opportunity, when, by, skip_reason):
	"""Carry the recorded hand-off onto a Project that already exists.

	Before v1.263.0 this could not happen: no project could exist until the
	hand-off was recorded, so ``_append_steps`` always found the completion
	waiting for it and seeded step 2 done. Now that booking the meeting opens the
	gate, the project is usually created *first* and its step 2 row sits Pending —
	so recording the meeting here has to reach across and close it, or the tracker
	nags forever about a meeting that already happened.

	Failures are logged, never raised: the stamp on the Opportunity is the record
	of the hand-off, and losing the mirror must not undo it.
	"""
	try:
		from erpnext_enhancements.process_steps import record_handoff_on_project

		record_handoff_on_project(opportunity, when=when, by=by, skip_reason=skip_reason)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Hand-off: mirror onto project failed")


@frappe.whitelist()
def mark_handoff_complete(opportunity):
	"""Record that the hand-off meeting happened. This is what opens the gate."""
	doc = frappe.get_doc("Opportunity", opportunity)
	if not doc.has_permission("write"):
		frappe.throw(_("Not permitted to modify this Opportunity."), frappe.PermissionError)
	if not _has_gate_fields():
		frappe.throw(_("The hand-off fields are not available yet — run migrations first."))
	if cint(doc.get("custom_handoff_meeting_held")):
		return {"already": True, "on": str(doc.get("custom_handoff_meeting_on") or "")}

	stamped = _stamp_handoff(doc)
	doc.add_comment("Comment", _("Hand-off meeting recorded (PRO-0204 step 2)."))
	return {"already": False, "on": str(stamped), "by": frappe.session.user}


@frappe.whitelist()
def skip_handoff(opportunity, reason):
	"""Skip the hand-off meeting, on the record, with a reason.

	System Manager only, and the reason is mandatory. The point is not to make
	skipping hard — sometimes a deal genuinely does not need a meeting — but to
	make it *impossible to do silently*. The reason is carried onto the Project's
	step 2 row prefixed ``[SKIPPED]``, so it shows up in the tracker and the
	compliance report rather than looking like a completion.
	"""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(
			_("Only a System Manager can skip the hand-off meeting."), frappe.PermissionError
		)
	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("A reason is required to skip the hand-off meeting."))

	doc = frappe.get_doc("Opportunity", opportunity)
	if not _has_gate_fields():
		frappe.throw(_("The hand-off fields are not available yet — run migrations first."))
	if cint(doc.get("custom_handoff_meeting_held")):
		return {"already": True}

	_stamp_handoff(doc, skip_reason=reason)
	doc.add_comment("Comment", _("Hand-off meeting SKIPPED by {0}: {1}").format(frappe.session.user, reason))
	return {"already": False, "reason": reason}


# ---------------------------------------------------------------------------
# Form state (client)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def handoff_state(opportunity):
	"""Everything the Opportunity form needs to decide which button to show."""
	if not frappe.has_permission("Opportunity", "read", doc=opportunity):
		frappe.throw(_("Not permitted to read this Opportunity."), frappe.PermissionError)
	if not _has_gate_fields():
		return {"available": False}

	row = frappe.db.get_value(
		"Opportunity",
		opportunity,
		[
			"status",
			"custom_handoff_gate_applies",
			"custom_handoff_meeting_held",
			"custom_handoff_meeting_on",
			"custom_handoff_meeting_by",
			"custom_handoff_skip_reason",
			"custom_handoff_event",
			"custom_handoff_due_by",
			"custom_launch_deadline",
			"custom_created_project",
		],
		as_dict=True,
	) or frappe._dict()

	overdue = bool(
		row.get("custom_handoff_due_by")
		and not cint(row.get("custom_handoff_meeting_held"))
		and get_datetime(row.get("custom_handoff_due_by")) < now_datetime()
	)
	# When the booked meeting is, for the tab. Read from the Event rather than
	# stored on the Opportunity so a re-scheduled meeting reads correctly without
	# a second field to keep in sync; ``None`` if somebody deleted the Event.
	event = row.get("custom_handoff_event")
	event_on = frappe.db.get_value("Event", event, "starts_on") if event else None
	return {
		"available": True,
		"enabled": process_automation_enabled() and handoff_gate_enabled(),
		"is_won": row.get("status") == WON_STATUS,
		"gate_applies": cint(row.get("custom_handoff_gate_applies")),
		"held": cint(row.get("custom_handoff_meeting_held")),
		"held_on": str(row.get("custom_handoff_meeting_on") or ""),
		"held_by": row.get("custom_handoff_meeting_by"),
		"skip_reason": row.get("custom_handoff_skip_reason"),
		"event": event,
		"event_on": str(event_on or ""),
		"due_by": str(row.get("custom_handoff_due_by") or ""),
		"launch_deadline": str(row.get("custom_launch_deadline") or ""),
		"project": row.get("custom_created_project"),
		"overdue": overdue,
		"can_skip": "System Manager" in frappe.get_roles(),
		# Whether a Project may be created *right now* — the same predicate the
		# three server-side enforcement layers use, so the tab's Create Project
		# button can never be offered for something the insert will refuse.
		"gate_open": not handoff_block_reason(opportunity),
		"gate_message": _(GATE_MESSAGE),
	}


# ---------------------------------------------------------------------------
# SLA stamping (called from the Opportunity save chain)
# ---------------------------------------------------------------------------


def stamp_handoff_gate(doc, method=None):
	"""Opportunity ``before_save`` — stamp the gate flag and the two deadlines.

	Fires only on a genuine *transition* into Closed Won, detected the same way
	``project_prompt.prompt_create_project_on_won`` does it. That is what keeps
	the pre-existing Closed-Won backlog exempt: those records never transition
	again, so they never acquire the flag, and merely re-saving one cannot drag
	it into the gate.

	Deliberately **not** gated on ``process_automation_enabled`` — this is a
	silent data stamp, the same category as ``custom_stage_changed_on``, and
	``feature_flags`` documents why those stay ungated: the data has to already
	be meaningful on the day somebody flips a switch, not start from blank.

	Runs after ``stamp_won_date`` in the ``before_save`` chain, which fills
	``custom_date_closed_won`` when empty — the deadlines are measured from the
	transition itself, so both stamps describe the same moment.
	"""
	if doc.status != WON_STATUS:
		return
	if not doc.meta.get_field("custom_handoff_gate_applies"):
		return

	before = doc.get_doc_before_save()
	if before is not None and before.status == WON_STATUS:
		return  # already won before this save — not a transition
	if cint(doc.get("custom_handoff_meeting_held")):
		return  # re-won after a recorded hand-off; leave the record alone

	doc.custom_handoff_gate_applies = 1

	now = now_datetime()
	holiday_list = _handoff_holiday_list()
	if not doc.get("custom_handoff_due_by"):
		doc.custom_handoff_due_by = add_working_days(
			now, HANDOFF_SLA_BUSINESS_DAYS, holiday_list=holiday_list
		)
	if not doc.get("custom_launch_deadline"):
		doc.custom_launch_deadline = add_working_days(
			now, LAUNCH_GOAL_BUSINESS_DAYS, holiday_list=holiday_list
		)


def launch_deadline_for_project(project_row):
	"""The 7-business-day launch deadline for a project, via its Opportunity.

	Stored on the Opportunity at the transition rather than recomputed here, so a
	later edit to the Holiday List cannot silently move a deadline that has
	already been reported on. Falls back to computing from
	``custom_date_closed_won`` for projects that predate the field.
	"""
	opportunity = project_row.get("custom_opportunity")
	if not opportunity:
		return None
	row = frappe.db.get_value(
		"Opportunity", opportunity, ["custom_launch_deadline", "custom_date_closed_won"], as_dict=True
	)
	if not row:
		return None
	if row.custom_launch_deadline:
		return get_datetime(row.custom_launch_deadline)
	if row.custom_date_closed_won:
		return add_working_days(
			get_datetime(getdate(row.custom_date_closed_won)),
			LAUNCH_GOAL_BUSINESS_DAYS,
			holiday_list=_handoff_holiday_list(),
		)
	return None
