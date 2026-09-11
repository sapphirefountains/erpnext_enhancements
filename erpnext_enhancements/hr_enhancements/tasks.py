# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Scheduled HR jobs — keeping "is this person still qualified?" true.

A credential's status is arithmetic on a date, so it is correct on the day it is
saved and wrong every day after. This sweep re-derives it nightly and, once a
week, tells people what is about to lapse.

**A horizon, not an alarm.** Until now nothing in this app warned about anything
before the fact: `certificates.expire_and_recertify` reacts *after* a training
certificate lapses, and `fixtures/notification.json` holds nineteen alerts across
Maintenance, Opportunity, Project, Material Request, Call Log, Task, Lead, Fiscal
Year, ToDo, Reminder, Error Log and Integration Request — and **zero** HR or
training ones. An expiry model with no forward view is a model that tells you
about a problem on the morning of the job.

The weekly digest goes to the holder *and* their supervisor, and it is one email
per person covering everything, not one per credential. Same reason as the
training reminder digest: that is the difference between a reminder people read
and a reminder people filter.
"""

import frappe
from frappe.utils import add_days, get_url, getdate, today

from erpnext_enhancements.hr_enhancements.doctype.employee_credential.employee_credential import (
	EXPIRY_HORIZON_DAYS,
)

CREDENTIAL = "Employee Credential"


def refresh_credential_status():
	"""Re-derive every credential's status. Idempotent, and writes only on change.

	Runs over rows that could plausibly move — anything with an expiry date that is
	not already revoked. A revoked credential is revoked whatever the calendar
	does, and one with no expiry never changes on its own.
	"""
	if not frappe.db.exists("DocType", CREDENTIAL):
		return 0

	changed = 0
	for name in frappe.get_all(
		CREDENTIAL,
		filters={"expires_on": ["is", "set"], "revoked_on": ["is", "not set"]},
		pluck="name",
	):
		try:
			doc = frappe.get_doc(CREDENTIAL, name)
			fresh = doc.derive_status()
			if fresh != doc.status:
				# db_set rather than save: nothing else about the row changed, and a
				# full save would re-run validate and re-stamp `modified` on a record
				# nobody touched.
				doc.db_set("status", fresh, update_modified=False)
				changed += 1
		except Exception:
			frappe.log_error(
				f"Could not refresh credential {name}\n{frappe.get_traceback()}", "HR credentials"
			)
	return changed


def send_expiry_digest():
	"""One email per person for everything of theirs lapsing inside the horizon.

	Sent to the holder and, separately, a roll-up to each supervisor — a technician
	needs to book the course and their supervisor needs to know not to schedule
	them past the date.
	"""
	if not frappe.db.exists("DocType", CREDENTIAL):
		return 0

	horizon = add_days(today(), EXPIRY_HORIZON_DAYS)
	rows = frappe.get_all(
		CREDENTIAL,
		filters={
			"status": ["in", ("Expiring", "Expired")],
			# Explicit rather than relying on the status alone: a filter on a
			# nullable date pushed through frappe's query builder is coalesced, and
			# a NULL expiry would land on whichever side of the comparison the
			# sentinel falls. Belt and braces, and it costs one indexed read.
			"expires_on": ["<=", horizon],
		},
		fields=["name", "user", "employee", "employee_name", "credential_type", "expires_on", "status"],
		order_by="expires_on asc",
	)
	if not rows:
		return 0

	by_person = {}
	for row in rows:
		if row.user:
			by_person.setdefault(row.user, []).append(row)

	sent = 0
	for user, items in by_person.items():
		if _notify(user, _subject(items), _body(items, own=True)):
			sent += 1

	for supervisor, items in _by_supervisor(rows).items():
		if _notify(supervisor, _subject(items, own=False), _body(items, own=False)):
			sent += 1
	return sent


def _by_supervisor(rows):
	"""Group by the holder's ``reports_to`` login. Skips anyone with no manager.

	Deliberately `reports_to` rather than the position ladder: this is "who plans
	your week and might schedule you past the date", which is exactly what the
	reporting line means and exactly what the ladder does not.
	"""
	out = {}
	for row in rows:
		manager = frappe.db.get_value("Employee", row.employee, "reports_to")
		user = frappe.db.get_value("Employee", manager, "user_id") if manager else None
		if user and user != row.user:
			out.setdefault(user, []).append(row)
	return out


def _subject(items, own=True):
	expired = [i for i in items if i.status == "Expired"]
	if own:
		return (
			f"{len(expired)} of your credentials have expired"
			if expired
			else f"{len(items)} of your credentials expire soon"
		)
	return f"{len(items)} credential(s) on your crew need renewing"


def _body(items, own=True):
	lines = []
	for item in items:
		who = "" if own else f"{frappe.utils.escape_html(item.employee_name or item.employee)} — "
		when = getdate(item.expires_on).strftime("%d %b %Y")
		verb = "expired" if item.status == "Expired" else "expires"
		lines.append(
			f"<li>{who}<b>{frappe.utils.escape_html(item.credential_type)}</b> {verb} {when}</li>"
		)
	opening = (
		"<p>These need renewing before they lapse:</p>"
		if own
		else "<p>People on your crew have credentials running out:</p>"
	)
	return (
		opening
		+ "<ul>"
		+ "".join(lines)
		+ "</ul>"
		+ f'<p><a href="{get_url("/app/employee-credential")}">Open the register</a></p>'
	)


def _notify(user, subject, body_html):
	"""Best-effort mail, gated the same way training's is. Never raises.

	Reuses ``training.notifications`` rather than growing a second mailer: it
	already resolves an Employee's preferred address the way travel does, already
	honours the notification switch, and already logs a Notification Log entry
	beside the email.
	"""
	try:
		from erpnext_enhancements.training import notifications

		if not notifications._enabled():
			return False
		recipient = notifications._recipient(user)
		return bool(recipient) and notifications._send(recipient, subject, body_html)
	except Exception:
		frappe.log_error(
			f"Could not send the credential digest to {user}\n{frappe.get_traceback()}",
			"HR credentials",
		)
		return False


def mint_work_anniversaries():
	"""Today's work anniversaries, into the team feed.

	**The feed knew how to show these and nothing kept writing them.**
	`Training Achievement` has carried a `Work Anniversary` kind since the social
	layer shipped, and `player.js` renders it — but the only thing that ever minted
	one was `patches/backfill_training_achievements.py`, which runs once. So the
	feed would have opened with sixteen anniversaries and then produced not one
	more, ever: on somebody's next anniversary the feed says nothing, and the
	record that says "5 years at Sapphire Fountains" stays frozen at whatever it
	was on install day. Found by the HR feature survey.

	Daily and exact-date, unlike the backfill, which had to catch up on history and
	so took each person's most recent completed year. Here the question is only
	"is today the day", so there is no catching up to do and no risk of a feed that
	opens with sixty rows.

	`social.record` is idempotent on ``(user, kind, title)``, so a double run in one
	day mints nothing twice. No `force` here and that is deliberate: this is an
	ordinary scheduled job, not a migrate, and it *should* stay dormant during one.
	"""
	from erpnext_enhancements.training import social

	now = getdate(today())
	made = 0
	for row in frappe.get_all(
		"Employee",
		filters={"status": "Active", "user_id": ["is", "set"], "date_of_joining": ["is", "set"]},
		fields=["user_id", "date_of_joining"],
	):
		joined = getdate(row.date_of_joining)
		if not _is_the_day(joined, now):
			continue
		years = now.year - joined.year
		if years < 1:
			continue
		label = (
			"1 year at Sapphire Fountains"
			if years == 1
			else f"{years} years at Sapphire Fountains"
		)
		if social.record(row.user_id, "Work Anniversary", label, occurred_on=now):
			made += 1
	return made


def _is_the_day(joined, now):
	"""Whether *now* is the anniversary of *joined*.

	29 February joiners are marked on the 28th in a non-leap year rather than
	skipping three years in four -- the same choice the backfill made, kept
	identical so the two cannot disagree about somebody's date.
	"""
	if (joined.month, joined.day) == (2, 29):
		is_leap = now.year % 4 == 0 and (now.year % 100 != 0 or now.year % 400 == 0)
		return (now.month, now.day) == ((2, 29) if is_leap else (2, 28))
	return (now.month, now.day) == (joined.month, joined.day)
