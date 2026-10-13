# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Leave-by pings (v1.606.0, calendar sync C3): "Leave by 7:35 am for Riverwalk".

The shared Google Calendar events of C3 say "Leave by 7:35 am" on the first stop of a day, but
Google's reminders cannot carry it to the crew: the Calendar API's reminders belong to the account
that writes the events (Nik's), so they would only ever ping him. So ERPNext sends it itself.

**Who gets one.** Each person who ticks *Ping me at leave-by time* on their Planner Resource (off by
default), while Project Planner Settings **Leave-by pings** is on (off by default). One notice, at
*Ping this many minutes before leave-by* (default 10) before the moment they must leave the shop to
reach the **first stop of their day** on time, through the change alerts' channels
(``planner_notices``): a bell notification, and a text, or an email when they have no cell number.

**It does not depend on the change alerts switch** (``change_alerts``). That switch is about telling
people things they did not ask for; this one is asked for, person by person, so the opt-in is the
consent. Nor does it need Google Calendar sync to be on: the times come from the planner.

**The times are the planner's** (``route_slots``, the route view's arithmetic): leave the shop at the
day start, drive, wait for a slotted stop. Leave-by is the first stop's arrival less its drive. The
engine call asks Google nothing (``google=False``: the drive-time cache the planner and the calendar
fill, else the straight-line estimate), because a five-minute cron is not where the planner itself
would pay for a route.

Never pinged: a pencil (pencils are never routed, and a pencil stop is skipped anyway), a day with
no hours (a day off, a holiday, time off or an all-day block), a travel day (no route), a first stop
with no location (no drive, so no leave-by), a person with no login, and anybody not opted in.

**At most one ping per person per stop per day, and it survives a deploy.** Each ping is claimed by
inserting a ``Planner Ping Log`` row keyed ``user|date|stop`` (unique) and committing it *before*
anything is sent, the ``Planner Digest Log`` pattern. A cache marker would not do: the production
deploy ``FLUSHDB``s redis. A send that fails after the claim is not retried. A late tick still sends
up to :data:`GRACE_MINUTES` after the leave-by time; later than that, a ping is worse than none.

:func:`select`, :func:`due`, :func:`message` and :func:`log_key` take plain values so
``tests/test_calendar_route_timing.py`` runs them without a bench.
"""

import hashlib

import frappe
from frappe import _

from erpnext_enhancements.project_enhancements import planner_notices as notices
from erpnext_enhancements.project_enhancements import route_slots

SETTINGS = "Project Planner Settings"
LOG_DOCTYPE = "Planner Ping Log"
RESOURCE = "Planner Resource"
OPT_IN_FIELD = "ping_at_leave_by"
DEFAULT_MINUTES = 10
MAX_MINUTES = 120
#: A tick this many minutes after the leave-by time still sends (the scheduler can run late).
GRACE_MINUTES = 10
FAILED_FLAG = "ee_leave_by_routes_failed"


# ---------------------------------------------------------------------- pure


def lead_minutes(value):
	"""Settings ``leave_by_ping_minutes``: None (no ``tabSingles`` row yet) is the default 10."""
	if value in (None, ""):
		return DEFAULT_MINUTES
	try:
		return max(min(int(float(value)), MAX_MINUTES), 0)
	except (TypeError, ValueError):
		return DEFAULT_MINUTES


def due(leave_by, now_minutes, lead):
	"""True from ``lead`` minutes before ``leave_by`` until :data:`GRACE_MINUTES` after it."""
	if leave_by is None:
		return False
	return leave_by - lead <= now_minutes <= leave_by + GRACE_MINUTES


def stop_ref(key):
	"""``("task", "TASK-1")`` → ``"task:TASK-1"``."""
	return f"{key[0]}:{key[1]}"


def log_key(user, day, ref):
	"""``user|date|<hash of the stop>``: a projected visit's key is long, the column is not."""
	digest = hashlib.sha1(str(ref).encode("utf-8")).hexdigest()[:16]
	return f"{user}|{day}|{digest}"


def select(people, days, day, now_minutes, lead):
	"""The pings due now: ``[{"user", "resource", "key", "slot"}]``.

	``people`` are opted-in Planner Resources (``name``, ``user``); ``days`` is
	``route_slots.compute``'s ``days``. Only the first stop of each person's day, and only when it
	is firm, driven to, on a day with hours, and due (see the module docstring).
	"""
	out = []
	for person in people or []:
		user, resource = person.get("user"), person.get("name")
		if not user or not resource:
			continue
		entry = (days or {}).get((resource, day))
		if not entry:
			continue
		try:
			capacity = float(entry.get("capacity") or 0)
		except (TypeError, ValueError):
			capacity = 0.0
		if capacity <= 0:
			continue
		key, slot = route_slots.first_slot(entry.get("slots"))
		if not slot or slot.get("tentative") or slot.get("leave_by") is None:
			continue
		if not due(slot["leave_by"], now_minutes, lead):
			continue
		out.append({"user": user, "resource": resource, "key": key, "slot": slot})
	return out


def message(slot):
	"""``(headline, lines)`` for one ping."""
	from erpnext_enhancements.project_enhancements.calendar_sync import payload

	label = slot.get("label") or slot.get("ref") or _("your first stop")
	headline = _("Leave by {0} for {1}").format(payload.minutes_clock(slot["leave_by"]), label)
	line = _("First stop: on site at {0}").format(payload.minutes_clock(slot["arrive"]))
	drive = slot.get("drive_minutes")
	if drive:
		line += " " + _("({0} min drive)").format(int(round(float(drive))))
	return headline, [line]


def bell_target(key, slot):
	"""``(doctype, name)`` the bell opens: the task, the visit record, or nothing (projected)."""
	if key[0] == route_slots.TASK_FAMILY:
		return "Task", key[1]
	if key[0] == route_slots.VISIT_FAMILY and slot.get("ref") == key[1]:
		return "Sapphire Maintenance Record", key[1]
	return None, None


# ---------------------------------------------------------------------- reads and writes


def is_on():
	return bool(notices.setting("leave_by_pings"))


def _lead():
	try:
		return lead_minutes(frappe.get_cached_doc(SETTINGS).get("leave_by_ping_minutes"))
	except Exception:
		return DEFAULT_MINUTES


def opted_in():
	"""Active Planner Resources that asked for pings and have an enabled login."""
	try:
		if not frappe.db.has_column(RESOURCE, OPT_IN_FIELD):
			return []
	except Exception:
		return []
	rows = frappe.get_all(
		RESOURCE,
		filters={"is_active": 1, OPT_IN_FIELD: 1},
		fields=["name", "user", "resource_name"],
		limit_page_length=0,
	)
	users = sorted({row.get("user") for row in rows if row.get("user")})
	if not users:
		return []
	enabled = set(
		frappe.get_all(
			"User", filters={"name": ["in", users], "enabled": 1}, pluck="name", limit_page_length=0
		)
	)
	return [row for row in rows if row.get("user") in enabled]


def _claim(user, day, ref, leave_by_text):
	"""Record the ping before sending it. False when it is already recorded (another run, or this
	stop was pinged earlier today). Committed at once, so a crash while sending does not send again."""
	key = log_key(user, day, ref)
	if frappe.db.exists(LOG_DOCTYPE, key):
		return False
	try:
		frappe.get_doc(
			{
				"doctype": LOG_DOCTYPE,
				"log_key": key,
				"user": user,
				"ping_date": str(day),
				"stop_ref": str(ref)[:255],
				"leave_by": leave_by_text,
				"sent_on": frappe.utils.now_datetime(),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		return True
	except Exception:
		# Almost always the unique key: another run claimed it between exists() and insert.
		frappe.db.rollback()
		return False


def _send(user, day, key, slot):
	"""Bell, then a text, else an email: the change alerts' rule. Returns the channels that went."""
	headline, lines = message(slot)
	link = notices.route_url(user, day)
	doctype, name = bell_target(key, slot)
	channels = []
	notices._bell(user, headline, lines, name, None, doctype or "Task")
	channels.append("bell")
	if notices._text(user, notices.sms_text(headline, lines, link)):
		channels.append("text")
	else:
		notices._email(user, headline, headline, lines, link, _("Open your route"), title=_("Time to leave"))
		channels.append("email")
	return channels


def _routes_failed():
	try:
		if frappe.cache.get_value(FAILED_FLAG):
			return
		frappe.cache.set_value(FAILED_FLAG, 1, expires_in_sec=3600)
		frappe.log_error(
			title="Project Planner: leave-by pings could not work out routes",
			message="No leave-by ping was sent this time; the next tick tries again. Logged once an hour.",
		)
	except Exception:
		pass


def send_leave_by_pings(now=None):
	"""Cron, every five minutes: each opted-in person's leave-by ping, at most once per stop per day.

	Does nothing while Project Planner Settings ``leave_by_pings`` is off or nobody has opted in.
	Returns how many were sent.
	"""
	if not is_on():
		return 0
	people = opted_in()
	if not people:
		return 0
	now = now or frappe.utils.now_datetime()
	day = now.date()
	minutes = now.hour * 60 + now.minute
	try:
		data = route_slots.compute(day, day, [p.get("name") for p in people], google=False)
	except Exception:
		_routes_failed()
		return 0
	sent = 0
	from erpnext_enhancements.project_enhancements.calendar_sync import payload

	for ping in select(people, data.get("days") or {}, day, minutes, _lead()):
		user, slot = ping["user"], ping["slot"]
		ref = stop_ref(ping["key"])
		if not _claim(user, day, ref, payload.minutes_clock(slot["leave_by"])):
			continue
		try:
			channels = _send(user, day, ping["key"], slot)
			frappe.db.set_value(
				LOG_DOCTYPE, log_key(user, day, ref), "channels", ", ".join(channels), update_modified=False
			)
			frappe.db.commit()
			sent += 1
		except Exception:
			frappe.log_error(title="Project Planner: leave-by ping failed", message=f"{user} {day} {ref}")
	return sent
