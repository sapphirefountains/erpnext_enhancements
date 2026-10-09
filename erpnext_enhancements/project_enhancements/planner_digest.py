# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One combined 6 AM message per person (Project Planner Phase 3B, P3.8).

Before this, someone on both a project crew and a maintenance route could get the technician
digest, the rental crew digest, and nothing at all about their project tasks. The availability
engine already knows everything booked on a person (project tasks, maintenance visits including
projected ones and crew places, rental crew tasks, travel), in the order their route drives it, so
:func:`send_daily_digests` texts and emails each person that one list: time (the slot, else the
route's arrival time), site, address, who they are with, hours, and a link to their route view.

**Behind Settings ``combined_morning_digest``, off by default.** While it is on, the maintenance
technician digest (``api.maintenance_dispatch.send_morning_digests``) and the rental crew digest
(``asset_management.rental_logistics.send_crew_digests``) skip every person this one covers
(:func:`covered_users`: each active Planner Resource with an enabled login), so nobody gets three
texts. While it is off nothing changes anywhere. :func:`send_preview` ("Send me a preview" on the
Settings form) emails the caller their own message without texting or recording anything.

Things this module is careful about, some of which look like bugs:

* **At most once per person per day, through a database row, not redis.** Each person's day is
  claimed by inserting a ``Planner Digest Log`` row keyed ``user|date`` (unique), committed
  *before* anything is sent. A second run, a second worker or a scheduler catch-up finds the row
  (or fails on the unique key) and sends nothing. The production deploy ``FLUSHDB``s redis, so a
  cache marker would have re-texted everyone after every merge. A send that fails after the claim
  is not retried: a missed digest is better than two.
* **Nothing booked, nothing sent**, and no claim either, so switching the digest on mid-morning
  and running it by hand still reaches the people who have work.
* **One person failing never stops the next**; each is logged on its own.
* **Texts follow the technician digest's rules** (``planner_notices.cell_number``: the Employee's
  ``cell_number`` by ``user_id``, through ``send_system_sms``), and email goes only through the
  shared shell (``email_style.wrap``).

``digest_lines`` and ``digest_text`` take plain values so ``tests/test_planner_phase3b.py`` runs
them without a bench.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from erpnext_enhancements.project_enhancements import crew_availability as engine
from erpnext_enhancements.project_enhancements import planner_notices as notices

LOG_DOCTYPE = "Planner Digest Log"
SMS_LINES = 10


def covered_users():
	"""The logins the combined digest speaks for, or an empty set while it is off.

	The other two morning digests call this and skip these people. Never raises: on any failure it
	answers the empty set, which leaves the older digests exactly as they were.
	"""
	try:
		if not notices.setting("combined_morning_digest"):
			return set()
		return {person.get("user") for person in _people()}
	except Exception:
		frappe.log_error(title="Project Planner: digest coverage", message=frappe.get_traceback())
		return set()


def _people():
	"""Active Planner Resources whose login is enabled, one per login."""
	rows = frappe.get_all(
		"Planner Resource",
		filters={"is_active": 1, "user": ["is", "set"]},
		fields=["name", "resource_name", "user"],
		order_by="resource_name asc",
		limit_page_length=0,
	)
	users = sorted({row.get("user") for row in rows if row.get("user")})
	enabled = (
		set(frappe.get_all("User", filters={"name": ["in", users], "enabled": 1}, pluck="name"))
		if users
		else set()
	)
	out, seen = [], set()
	for row in rows:
		user = row.get("user")
		if user in enabled and user not in seen:
			seen.add(user)
			out.append(row)
	return out


# ---------------------------------------------------------------------- wording (pure)


def digest_lines(entry):
	"""``[{"time", "what", "where", "with", "hours"}]`` for one day entry (``api.project_planner.
	day_entry``), in route order; a travel day is one line."""
	out = []
	if entry.get("travel"):
		out.append({"time": "", "what": entry["travel"], "where": "", "with": "", "hours": None})
	for item in entry.get("items") or []:
		what = item.get("label") or item.get("ref") or ""
		place = item.get("project_title")
		if place and place != what:
			what = f"{place}: {what}"
		out.append(
			{
				"time": notices._clock(item.get("time")) if item.get("time") else "",
				"what": what,
				"where": item.get("address") or "",
				"with": ", ".join(item.get("crew") or []),
				"hours": flt(item.get("hours")) or None,
			}
		)
	return out


def digest_text(day, lines, link):
	"""The text message: a count, at most ten numbered lines, and the route link."""
	day = getdate(day)
	when = f"{day:%a} {day.month}/{day.day}"
	head = _("Sapphire Fountains — your day, {0} ({1}):").format(when, len(lines))
	body = []
	for index, line in enumerate(lines[:SMS_LINES], 1):
		text = f"{index}. " + " ".join(part for part in (line["time"], line["what"]) if part)
		if line["where"]:
			text += f" — {line['where']}"
		if line["with"]:
			text += " " + _("(with {0})").format(line["with"])
		body.append(text)
	if len(lines) > SMS_LINES:
		body.append(_("…and {0} more — see email").format(len(lines) - SMS_LINES))
	if link:
		body.append(_("Route: {0}").format(link))
	return "\n".join([head, *body])


def _digest_email(label, day, lines, link, week_link, preview_text=None):
	"""``(subject, html)`` for the email, through the shared shell."""
	from erpnext_enhancements import email_style

	day = getdate(day)
	when = frappe.utils.formatdate(day)
	rows = []
	for line in lines:
		what = line["what"]
		if line["hours"]:
			what += f" ({engine.fmt_hours(line['hours'])}h)"
		rows.append([line["time"], what, line["where"], line["with"]])
	body = email_style.p(_("Good morning {0}. Here is your day, {1}, in route order:").format(label, when))
	body += email_style.table([_("Time"), _("What"), _("Where"), _("With")], rows)
	if link:
		body += email_style.button(link, _("Open your route"))
	if week_link:
		body += email_style.links([(week_link, _("See your whole week"))])
	if preview_text:
		body += email_style.note(
			_("This is a preview. Nothing was texted, and the 6 AM digest is unaffected.")
		)
		body += email_style.p(_("The text message would read:")) + email_style.code(preview_text)
	subject = _("Your day — {0} ({1})").format(when, len(lines))
	html = email_style.wrap(body, title=_("Your day"), eyebrow=_("Schedule") + " · " + str(when))
	return subject, html


# ---------------------------------------------------------------------- sending


def _links(resource, day):
	route = frappe.utils.get_url(f"{notices.PLANNER_ROUTE}/route/{resource}/{day}")
	week = frappe.utils.get_url(f"{notices.PLANNER_ROUTE}/my-week/{day}")
	return route, week


def _entries(day, resources):
	from erpnext_enhancements.api.project_planner import _people_days

	data, days = _people_days(day, day, resources)
	labels = {r["name"]: r["label"] for r in data["resources"]}
	return {name: (labels.get(name), (entries or [None])[0]) for name, entries in days.items()}


def _claim(user, day, bookings):
	"""Record ``user``'s digest for ``day`` before sending it. False when it is already recorded
	(another run got there first). The row is committed at once, so a crash while sending does not
	send again."""
	key = f"{user}|{day}"
	if frappe.db.exists(LOG_DOCTYPE, key):
		return False
	try:
		frappe.get_doc(
			{
				"doctype": LOG_DOCTYPE,
				"log_key": key,
				"user": user,
				"digest_date": str(day),
				"sent_on": frappe.utils.now_datetime(),
				"bookings": bookings,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		return True
	except Exception:
		# Almost always the unique key: a second run claimed the day between the exists() and the
		# insert. Either way the safe answer is "do not send".
		frappe.db.rollback()
		return False


def send_daily_digests():
	"""Cron, 6 AM: each covered person's bookings for today, by text and email, at most once a day.

	Does nothing while Settings ``combined_morning_digest`` is off.
	"""
	if not notices.setting("combined_morning_digest"):
		return
	day = getdate(nowdate())
	people = _people()
	if not people:
		return
	entries = _entries(day, [person.get("name") for person in people])
	for person in people:
		user, resource = person.get("user"), person.get("name")
		label, entry = entries.get(resource) or (None, None)
		lines = digest_lines(entry or {})
		if not lines:
			continue
		if not _claim(user, day, len(lines)):
			continue
		try:
			channels = _send(user, resource, label or person.get("resource_name") or user, day, lines)
			frappe.db.set_value(
				LOG_DOCTYPE, f"{user}|{day}", "channels", ", ".join(channels), update_modified=False
			)
			frappe.db.commit()
		except Exception:
			frappe.log_error(title=f"Project Planner digest failed: {user}", message=frappe.get_traceback())


def _send(user, resource, label, day, lines):
	"""Text and email one person their day. Returns the channels that went."""
	link, week = _links(resource, day)
	channels = []
	try:
		if notices._text(user, digest_text(day, lines, link)):
			channels.append("text")
	except Exception:
		frappe.log_error(title=f"Project Planner digest text failed: {user}", message=frappe.get_traceback())
	subject, html = _digest_email(label, day, lines, link, week)
	frappe.sendmail(recipients=[notices.email_of(user)], subject=subject, message=html)
	channels.append("email")
	return channels


def send_preview(user, day=None):
	"""Email ``user`` what their combined digest would say for ``day`` (default today).

	No text, no ``Planner Digest Log`` row, and the Settings switch is not consulted, so the
	message can be read before anybody else gets one. ``{"sent": bool, "message": str}``.
	"""
	day = getdate(day or nowdate())
	resource = notices.resource_of(user)
	if not resource:
		return {
			"sent": False,
			"message": _("You are not on the Planner Resources list, so there is no digest to preview."),
		}
	label, entry = _entries(day, [resource]).get(resource) or (None, None)
	lines = digest_lines(entry or {})
	if not lines:
		return {
			"sent": False,
			"message": _(
				"Nothing is booked on you for {0}, so the digest would not message you that day."
			).format(frappe.utils.formatdate(day)),
		}
	link, week = _links(resource, day)
	subject, html = _digest_email(label or user, day, lines, link, week, digest_text(day, lines, link))
	frappe.sendmail(
		recipients=[notices.email_of(user)], subject=_("Preview: {0}").format(subject), message=html
	)
	return {
		"sent": True,
		"message": _("Sent to {0}: {1} booking(s) for {2}.").format(
			notices.email_of(user), len(lines), frappe.utils.formatdate(day)
		),
	}
