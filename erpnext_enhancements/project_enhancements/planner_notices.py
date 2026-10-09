# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Telling people what the Project Planner changed (Phase 3B): publish notices and 48-hour alerts.

Two messages, two triggers:

* **Publish notice.** When a planner publishes their drafts (``api.project_planner.publish_drafts``)
  every person whose bookings moved gets **one** notice summarizing their changed days: a bell
  notification (Notification Log), an email in the shared shell, and a text when they have a cell
  number. Always on: draft mode is the deliberate "tell people when I'm done" path, so pressing
  Publish *is* the decision to tell them.
* **Change alert** (Settings ``change_alerts``, **off** by default). When a saved Task change moves
  someone's bookings inside the next 48 hours (their dates, time slot, or whether they are on the
  crew), they get one alert, "Your Thursday changed: Highlands pump set moved to Friday 8:00",
  with a link to that day's route. A bell notification, and a text (an email when they have no
  cell number). It hangs off ``Task.on_update`` (:func:`queue_task_change`), so a drag on the
  planner and an edit on the Task form both count.

Things this module is careful about, some of which look like bugs:

* **One alert per person per request.** A drag can save a task twice and a publish saves many; the
  changes are collected on ``frappe.local`` (the *first* "before" and the *latest* "after" of each
  task) and turned into one alert per person when the transaction commits
  (``frappe.db.after_commit``). A rolled-back save alerts nobody.
* **A publish does not also alert.** ``publish_drafts`` sets ``frappe.flags.planner_publishing``
  while it saves, and folds the 48-hour wording into each person's single publish notice instead.
* **Alerts are best effort and not re-drivable.** They go out from a background job after commit.
  The production deploy ``FLUSHDB``s redis, so an alert queued in the seconds before a merge to
  main is lost, silently. Nothing re-sends it: an alert about a change that is already in the past
  is worse than none, and the 6 AM digest tells everyone their day anyway. The publish notice's
  bell entry and email are written in the publishing request itself (both are database rows that
  survive a flush); only its text goes through a job.
* **Finishing a task alerts nobody.** Marking a job Completed or Canceled is not news to the crew
  who did it, so finished tasks are skipped.
* **Texts reuse the technician digest's rules**: the number is the person's Employee
  ``cell_number`` (by ``user_id``), sent through ``api.telephony.send_system_sms``; a person with
  no number is not texted. Emails go only through ``email_style.wrap`` (the shared shell).
* **Never raises into a save.** :func:`queue_task_change` is a doc_event; a failure is logged and
  the task saves.

The wording helpers (:func:`when_text`, :func:`change_lines`, :func:`alert_headline`) take plain
values so ``tests/test_planner_phase3b.py`` runs them without a bench.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import cint, flt

from erpnext_enhancements.project_enhancements import crew_availability as engine

SETTINGS = "Project Planner Settings"
#: Same tuple as the engine's: a finished task books nobody, so changing one tells nobody.
FINISHED_STATUSES = engine.FINISHED_STATUSES
ALERT_HOURS = 48
SMS_LINES = 10
ALERT_JOB = "erpnext_enhancements.project_enhancements.planner_notices.send_change_alerts"
TEXT_JOB = "erpnext_enhancements.project_enhancements.planner_notices.send_texts"
#: Where a request's task changes wait for the commit (``frappe.local`` attribute).
LOCAL_KEY = "planner_change_alerts"
PLANNER_ROUTE = "/app/project-planner"

# The Task fields a person's booking is read from (the engine's task_span / task_slot).
_STATE_FIELDS = (
	"name",
	"subject",
	"status",
	"exp_start_date",
	"exp_end_date",
	"expected_time",
	"custom_start_datetime",
	"custom_end_datetime",
)


# ---------------------------------------------------------------------- settings and people


def setting(fieldname):
	"""A Project Planner Settings switch as 0 or 1. A field with no ``tabSingles`` row reads None,
	which is off, the same as its declared default."""
	try:
		return cint(frappe.get_cached_doc(SETTINGS).get(fieldname))
	except Exception:
		return 0


def cell_number(user):
	"""The person's cell number: their Employee ``cell_number``, matched on ``user_id``. The same
	rule as the maintenance technician digest (``api.maintenance_dispatch._send_tech_digest``)."""
	if not user:
		return None
	return frappe.db.get_value("Employee", {"user_id": user}, "cell_number") or None


def email_of(user):
	return frappe.db.get_value("User", user, "email") or user


def resource_of(user):
	"""The person's active Planner Resource name, or None."""
	if not user:
		return None
	rows = frappe.get_all(
		"Planner Resource",
		filters={"user": user, "is_active": 1},
		pluck="name",
		order_by="creation asc",
		limit_page_length=1,
	)
	return rows[0] if rows else None


def route_url(user, day):
	"""A link to the person's route for ``day``, else to their week."""
	resource = resource_of(user)
	if resource and day:
		return frappe.utils.get_url(f"{PLANNER_ROUTE}/route/{resource}/{day}")
	return frappe.utils.get_url(f"{PLANNER_ROUTE}/my-week/{day}" if day else f"{PLANNER_ROUTE}/my-week")


# ---------------------------------------------------------------------- wording (pure)


def _day(value, long=False):
	value = engine._as_date(value)
	if long:
		return value.strftime("%A")
	return f"{value:%a %b} {value.day}"


def _clock(text):
	"""``"08:00"`` → ``"8:00"``; the 24-hour clock the route view prints, without the leading zero."""
	parts = str(text or "").split(":")
	if len(parts) < 2:
		return str(text or "")
	try:
		return f"{int(parts[0])}:{parts[1][:2]}"
	except ValueError:
		return str(text)


def when_text(span, slot=None, long=False):
	"""When a booking is, as people say it: ``"Thu Oct 15"``, ``"Thu Oct 15 – Fri Oct 16"``, and
	with ``long`` the weekday alone, ``"Thursday 8:00"``. A slot adds its start time."""
	if not span:
		return _("no date")
	first, last = engine._as_date(span[0]), engine._as_date(span[1])
	text = _day(first, long)
	if last and last != first:
		text += " – " + _day(last, long)
	if slot:
		text += " " + _clock(slot[0])
	return text


def snapshot(state, users, hours=None):
	"""What a person reads off one task: subject, days, time slot, who is on it and their hours.

	``state`` is a Task row or doc (``engine.task_span`` / ``task_slot`` read it); ``users`` the
	logins on its crew; ``hours`` ``{user: crew-row hours}``.
	"""
	state = state or {}
	return {
		"task": state.get("name"),
		"subject": state.get("subject") or state.get("name") or "",
		"span": engine.task_span(state),
		"slot": engine._slot_text(engine.task_slot(state)),
		"users": sorted({user for user in users or () if user}),
		"hours": {user: round(flt(value), 2) for user, value in (hours or {}).items() if user},
		"expected": round(flt(state.get("expected_time")), 2),
	}


def _days(snap):
	span = (snap or {}).get("span")
	return engine.daterange(*span) if span else []


def _hours_changed(before, after, user):
	return before.get("expected") != after.get("expected") or (before.get("hours") or {}).get(user) != (
		after.get("hours") or {}
	).get(user)


def change_lines(before, after, window=None, long=False):
	"""``{user: [{"task", "text", "day"}]}``: what changed for each person between two snapshots.

	* on the crew before and after, and the days or time moved → "X moved from Thu Oct 15 to Fri
	  Oct 16" (with ``long``, the alert's form: "X moved to Friday 8:00");
	* on it before and after, same days, different hours → "X (Thu Oct 15): your hours changed";
	* newly on it → "You're on X: Thu Oct 15"; taken off → "You're off X (Thu Oct 15)".

	``before`` is None for a new task. With ``window`` (``(first_day, last_day)``) only changes that
	touch a day inside it are kept, and ``day`` is the first such day; without, ``day`` is the first
	day the change touches. A person with nothing changed is left out.
	"""
	before, after = before or None, after or None
	if not before and not after:
		return {}
	was_on = set((before or {}).get("users") or ())
	now_on = set((after or {}).get("users") or ())
	subject = (after or before).get("subject") or ""
	task = (after or before).get("task")
	moved = bool(
		before
		and after
		and (before.get("span"), before.get("slot")) != (after.get("span"), after.get("slot"))
	)
	out = {}
	for user in sorted(was_on | now_on):
		if user in was_on and user in now_on:
			if moved:
				if long:
					text = _("{0} moved to {1}").format(
						subject, when_text(after["span"], after["slot"], True)
					)
				else:
					text = _("{0} moved from {1} to {2}").format(
						subject,
						when_text(before["span"], before["slot"]),
						when_text(after["span"], after["slot"]),
					)
				days = _days(before) + _days(after)
			elif _hours_changed(before, after, user):
				text = _("{0} ({1}): your hours changed").format(
					subject, when_text(after["span"], after["slot"], long)
				)
				days = _days(after)
			else:
				continue
		elif user in now_on:
			text = _("You're on {0}: {1}").format(subject, when_text(after["span"], after["slot"], long))
			days = _days(after)
		else:
			text = _("You're off {0} ({1})").format(subject, when_text(before["span"], before["slot"], long))
			days = _days(before)
		if window:
			days = [day for day in days if window[0] <= day <= window[1]]
			if not days:
				continue
		out.setdefault(user, []).append({"task": task, "text": text, "day": str(min(days)) if days else None})
	return out


def merge_lines(*groups):
	"""Several ``change_lines`` answers as one, per person, in the order given."""
	out = {}
	for group in groups:
		for user, entries in (group or {}).items():
			out.setdefault(user, []).extend(entries)
	return out


def alert_headline(entries):
	"""``"Your Thursday changed: Highlands pump set moved to Friday 8:00"``, from one person's
	alert entries; the weekday is the first day the changes touch."""
	entries = [e for e in entries or [] if e.get("text")]
	if not entries:
		return ""
	days = sorted(e["day"] for e in entries if e.get("day"))
	texts = "; ".join(e["text"] for e in entries)
	if not days:
		return _("Your schedule changed: {0}").format(texts)
	return _("Your {0} changed: {1}").format(_day(days[0], long=True), texts)


def alert_window(now=None):
	"""``(today, the date 48 hours from now)``: the days a change alert cares about."""
	now = now or frappe.utils.now_datetime()
	return now.date(), (now + datetime.timedelta(hours=ALERT_HOURS)).date()


def sms_text(headline, lines, link):
	"""A text: the headline, at most ten lines, and a link."""
	body = [f"Sapphire Fountains — {headline}"]
	shown = list(lines or [])
	if len(shown) > SMS_LINES:
		shown = shown[:SMS_LINES] + [_("…and {0} more").format(len(lines) - SMS_LINES)]
	body.extend(f"• {line}" for line in shown)
	if link:
		body.append(link)
	return "\n".join(body)


# ---------------------------------------------------------------------- sending


def _bell(user, subject, lines, task=None, from_user=None):
	"""The in-app notification. Type "Alert", which Frappe never emails, so the email below is the
	only one the person gets (see ``notification_skip_email_types`` in hooks.py)."""
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": user,
			"from_user": from_user,
			"type": "Alert",
			"document_type": "Task" if task else None,
			"document_name": task,
			"subject": frappe.utils.escape_html(subject),
			"email_content": "<br>".join(frappe.utils.escape_html(line) for line in lines or []),
		}
	).insert(ignore_permissions=True)


def _email(user, subject, intro, lines, link, link_label):
	from erpnext_enhancements import email_style

	body = email_style.p(intro) + email_style.bullets(list(lines or []))
	if link:
		body += email_style.button(link, link_label)
	frappe.sendmail(
		recipients=[email_of(user)],
		subject=subject,
		message=email_style.wrap(body, title=_("Your schedule changed"), eyebrow=_("Project Planner")),
	)


def _text(user, message):
	"""Text ``user`` when they have a cell number. True when a text went."""
	number = cell_number(user)
	if not number:
		return False
	from erpnext_enhancements.api.telephony import send_system_sms

	send_system_sms(number, message)
	return True


def send_publish_notices(notices, publisher=None):
	"""One notice per person after a publish. ``notices``: ``{user: {"lines": [...], "urgent":
	[alert entries], "task": first task, "day": first changed day}}``.

	The bell entry and the email are written now, in the publishing transaction; the texts go in one
	job after the commit (a slow gateway must not hold the planner's request). Returns how many
	people were told. One person failing never stops the next.
	"""
	told = 0
	texts = {}
	name = frappe.utils.get_fullname(publisher) if publisher else _("A planner")
	for user, notice in (notices or {}).items():
		lines = [entry["text"] for entry in notice.get("lines") or []]
		if not user or not lines:
			continue
		urgent = notice.get("urgent") or []
		subject = alert_headline(urgent) if urgent else _("Your schedule changed")
		link = route_url(user, notice.get("day"))
		try:
			_bell(user, subject, lines, notice.get("task"), publisher)
			_email(
				user,
				subject,
				_("{0} published changes to your schedule on the Project Planner:").format(name),
				lines,
				link,
				_("Open your route"),
			)
			texts[user] = sms_text(subject if urgent else _("your schedule changed:"), lines, link)
			told += 1
		except Exception:
			frappe.log_error(title="Project Planner: publish notice failed", message=frappe.get_traceback())
	if texts:
		frappe.enqueue(TEXT_JOB, queue="short", enqueue_after_commit=True, texts=texts)
	return told


def send_texts(texts):
	"""Background job: ``{user: message}``. A person with no cell number is skipped."""
	for user, message in (texts or {}).items():
		try:
			_text(user, message)
		except Exception:
			frappe.log_error(title="Project Planner: text failed", message=frappe.get_traceback())


# ---------------------------------------------------------------------- change alerts


def task_snapshot(doc, todo_users=None):
	"""A :func:`snapshot` of a Task doc: its crew rows' users, else ``todo_users``."""
	rows = (getattr(doc, "custom_crew", None) or []) if doc is not None else []
	users = [row.get("user") for row in rows if row.get("user")]
	hours = {row.get("user"): row.get("hours") for row in rows if row.get("user")}
	if not rows and todo_users:
		users = list(todo_users)
	return snapshot({key: doc.get(key) for key in _STATE_FIELDS}, users, hours)


def queue_task_change(doc, method=None):
	"""``Task.on_update``: remember a change for the 48-hour alert, sent once after commit.

	Off unless Settings ``change_alerts`` is on. Skipped during import, patch, install and migrate,
	during a publish (which tells people itself), and for finished tasks. Never raises.
	"""
	try:
		flags = frappe.flags
		for flag in ("in_import", "in_patch", "in_install", "in_migrate", "planner_publishing"):
			if getattr(flags, flag, False):
				return
		if not setting("change_alerts"):
			return
		if doc.get("status") in FINISHED_STATUSES:
			return
		before_doc = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
		todo_users = None
		has_rows = bool(getattr(doc, "custom_crew", None)) or bool(
			before_doc is not None and getattr(before_doc, "custom_crew", None)
		)
		if not has_rows:
			# A task nobody has crewed on the planner: its people are its open assignees.
			todo_users = frappe.get_all(
				"ToDo",
				filters={"reference_type": "Task", "reference_name": doc.name, "status": "Open"},
				pluck="allocated_to",
			)
		after = task_snapshot(doc, todo_users)
		before = task_snapshot(before_doc, todo_users) if before_doc is not None else None
		# Any change is remembered, not only one inside the window: a second save in the same
		# request is measured from this save's "before", and the window is applied at the commit.
		if not change_lines(before, after):
			return
		_remember(doc.name, before, after)
	except Exception:
		frappe.log_error(title="Project Planner: change alert not queued", message=frappe.get_traceback())


def _remember(task, before, after):
	store = getattr(frappe.local, LOCAL_KEY, None)
	if store is None:
		store = {}
		setattr(frappe.local, LOCAL_KEY, store)
		try:
			frappe.db.after_commit.add(flush_alerts)
			frappe.db.after_rollback.add(_forget)
		except AttributeError:
			# No commit hooks (an old framework or a test stub): send at the end of this request's
			# commit the plain way. Coalescing still holds for this one task.
			setattr(frappe.local, LOCAL_KEY, None)
			alerts = collect_alerts([{"before": before, "after": after}], alert_window())
			if alerts:
				frappe.enqueue(ALERT_JOB, queue="short", enqueue_after_commit=True, alerts=alerts)
			return
	if task in store:
		store[task]["after"] = after
	else:
		store[task] = {"before": before, "after": after}


def _forget():
	setattr(frappe.local, LOCAL_KEY, None)


def collect_alerts(changes, window):
	"""``{user: [entries]}`` from ``[{"before", "after"}]``, one list per person (long wording)."""
	return merge_lines(
		*(change_lines(c.get("before"), c.get("after"), window, long=True) for c in changes or [])
	)


def flush_alerts():
	"""After commit: one background job carrying every person's alert from this request."""
	store = getattr(frappe.local, LOCAL_KEY, None) or {}
	_forget()
	if not store:
		return
	try:
		alerts = collect_alerts(store.values(), alert_window())
		if alerts:
			frappe.enqueue(ALERT_JOB, queue="short", alerts=alerts)
	except Exception:
		frappe.log_error(title="Project Planner: change alerts not sent", message=frappe.get_traceback())


def send_change_alerts(alerts):
	"""Background job: one alert per person. ``alerts``: ``{user: [{"task", "text", "day"}]}``.

	A bell notification, and a text, or an email for someone with no cell number. Best effort.
	"""
	for user, entries in (alerts or {}).items():
		try:
			headline = alert_headline(entries)
			if not user or not headline:
				continue
			day = min((e["day"] for e in entries if e.get("day")), default=None)
			link = route_url(user, day)
			lines = [e["text"] for e in entries]
			task = next((e.get("task") for e in entries if e.get("task")), None)
			_bell(user, headline, lines, task)
			if not _text(user, sms_text(headline, [], link)):
				_email(user, headline, headline, lines, link, _("Open your route"))
		except Exception:
			frappe.log_error(title="Project Planner: change alert failed", message=frappe.get_traceback())
