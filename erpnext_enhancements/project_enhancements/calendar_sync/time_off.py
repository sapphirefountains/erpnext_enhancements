# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Time off read IN from the Company Holidays & Vacations calendar (C4).

People already put their time off on that shared Google calendar by hand ("Austin Off", "Clegg -
ooo"). This reads those events and turns each into **Unavailable Planner Blocks**, so both planners
know who is out. It is the one place ERPNext takes anything from Google: C2 pushes everything else
one way, out.

**Which person, which days** is ``time_off_rules`` (pure; read its docstring for the rules). What
this module adds is the reading and the writing:

* **Reading.** C2's client and account, ``events.list`` with ``singleEvents=true`` so recurring
  events arrive as their instances (a multi-day event is one event with a span either way).
  :func:`incremental_read` every 10 minutes from a stored ``syncToken`` (its own token: the query
  differs from C2's snap-back, and Google ties a token to its query); :func:`nightly_read` re-reads
  the whole window (Calendar Sync Settings' days back / days ahead, the same window as C2's repair)
  and removes blocks whose event has gone. The nightly read is what makes the deploy's ``FLUSHDB``
  harmless here too: a lost job, a lost token or a review decision whose job was flushed is redone
  by the next one.
* **Writing.** One all-day or timed Planner Block per person per day, marked ``source`` "Google" with
  the event's id, and a note "From Google Calendar: <title>" (a block's note is private to the
  person and the planners). Each read compares what the event wants with the blocks it has, and
  creates, updates or deletes only the difference. **Blocks made in ERPNext are never read, changed
  or deleted here**: every query filters on ``source = "Google"``. Everything happens inside the
  window: a block on a day before it is history and stays.
* **Imported blocks are read-only** (``PlannerBlock.validate`` refuses an edit, ``on_trash`` a
  delete by anyone but a System Manager) and **never go back out**: ``sources.block_item`` reads an
  imported block as gone, so C2 never writes an event for one, and ``triggers.on_change`` does not
  even queue it. Without that, every hand-made Google event would come back as a duplicate made by
  ERPNext.
* **Unmatched events go to the review list** (``Calendar Time Off Review``): a planner picks the
  person, "Everyone", or "Ignore"; the choice is kept for that event id and, when ticked, for the
  title. The people in Calendar Sync Settings' *Time off alerts* (else whoever authorized the
  account) get one bell when new rows arrive.
* **"Company holiday"** makes a block for every active staff resource (not subcontractors: the
  availability engine gives them no holidays either), except on a day their Holiday List already has
  as a holiday. The Holiday List is HR's and is never written from a calendar title.
* **Time off over firm work alerts once.** A newly imported block on a day its person has firm
  bookings (tasks or visits) sends the same alert people see in the Conflict center
  (``api/planner_blocks._conflicts_made``) to the *Time off alerts* people as a bell, once per block
  (``time_off_alerted``; a block is one person's one day).

**Switches.** Nothing is read unless Calendar Sync Settings has the master switch on, an account
authorized, a Company Holidays & Vacations calendar ID, and **Read time off from Google** ticked
(default off). :func:`preview` is the dry run: it reads Google (a read is the only way to know) and
writes nothing at all, to ERPNext or to Google.
"""

import datetime
import json
import time

import frappe

from erpnext_enhancements.project_enhancements.calendar_sync import engine, google_api
from erpnext_enhancements.project_enhancements.calendar_sync import time_off_rules as rules

BLOCK = "Planner Block"
REVIEW = "Calendar Time Off Review"
RESOURCE = "Planner Resource"
SOURCE_GOOGLE = "Google"
SWITCH = "read_time_off"
TOKEN_FIELD = "time_off_sync_token"
ALERT_TABLE = "time_off_alert_recipients"

#: A global flag the import sets while it writes, so the Planner Block controller lets it through.
IMPORT_FLAG = "calendar_time_off_import"
LOCK_KEY = "ee_calendar_time_off_lock"
LOCK_SECONDS = 25 * 60
PAGES = 100
RUN_SECONDS = 15 * 60
PREVIEW_MAX_DAYS = 400
REVIEW_ROUTE = "/app/calendar-time-off-review?status=Open"
#: What the event snapshot on a review row keeps: enough to apply a decision without Google.
SNAPSHOT_KEYS = ("id", "status", "summary", "start", "end", "creator", "organizer", "eventType")


# ---------------------------------------------------------------------- switches and setup


def _has_column(doctype, column):
	try:
		return bool(frappe.db.has_column(doctype, column))
	except Exception:
		return False


def _table_exists(doctype):
	try:
		return bool(frappe.db.table_exists(doctype))
	except Exception:
		return False


def installed():
	"""True once the migrate that brings C4 has run (the block columns and the review table)."""
	return _has_column(BLOCK, "source") and _has_column(BLOCK, "google_event_id") and _table_exists(REVIEW)


def calendar_id(doc=None):
	return (((doc or engine.settings()).get("holidays_calendar_id")) or "").strip()


def is_on(doc=None):
	"""Master switch, an account, the holidays calendar and *Read time off from Google*. Never raises."""
	try:
		doc = doc or engine.settings()
		return (
			bool(int(doc.get(SWITCH) or 0)) and bool(calendar_id(doc)) and engine.is_on(doc) and installed()
		)
	except Exception:
		return False


def _today():
	from frappe.utils import getdate, nowdate

	return getdate(nowdate())


def _zone():
	try:
		from frappe.utils import get_system_timezone

		return rules.zone_for(get_system_timezone())
	except Exception:
		return None


def _lock():
	"""True when this run may go ahead (no other read holds the lock). Redis ``SET NX``; on any doubt
	it answers True: ``diff_blocks`` heals a race, a skipped run is only a delay."""
	try:
		return bool(frappe.cache.set(frappe.cache.make_key(LOCK_KEY), 1, ex=LOCK_SECONDS, nx=True))
	except Exception:
		return True


def _unlock():
	try:
		frappe.cache.delete(frappe.cache.make_key(LOCK_KEY))
	except Exception:
		pass


def _log_failure(title):
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


# ---------------------------------------------------------------------- what ERPNext knows


def people():
	"""Active staff on the planner: ``[{"resource", "full_name", "aliases", "user"}]``.

	Subcontractors are left out: their names are company names, and their time off is not on our
	calendar."""
	fields = ["name", "resource_name", "resource_type", "user", "employee"]
	if _has_column(RESOURCE, "calendar_aliases"):
		fields.append("calendar_aliases")
	out = []
	for row in frappe.get_all(RESOURCE, filters={"is_active": 1}, fields=fields, limit_page_length=0):
		if row.get("resource_type") == "Subcontractor":
			continue
		out.append(
			{
				"resource": row.get("name"),
				"full_name": row.get("resource_name") or row.get("name"),
				"aliases": row.get("calendar_aliases") or "",
				"user": row.get("user"),
				"employee": row.get("employee"),
				"resource_type": row.get("resource_type"),
			}
		)
	return out


def emails_to_resources(staff):
	"""``{lower-case address: resource}`` from each person's User (its name and its email)."""
	users = sorted({p["user"] for p in staff if p.get("user")})
	if not users:
		return {}
	by_user = {p["user"]: p["resource"] for p in staff if p.get("user")}
	out = {}
	for row in frappe.get_all(
		"User", filters={"name": ["in", users]}, fields=["name", "email"], limit_page_length=0
	):
		resource = by_user.get(row.get("name"))
		for address in (row.get("name"), row.get("email")):
			if resource and address and "@" in str(address):
				out.setdefault(str(address).strip().lower(), resource)
	return out


def review_rows(filters=None):
	if not _table_exists(REVIEW):
		return []
	return frappe.get_all(
		REVIEW,
		filters=filters or {},
		fields=["name", "event_id", "status", "resource", "remember_title", "title_key", "event_json"],
		limit_page_length=0,
	)


def decisions():
	"""``(by event id, by title key)`` from the review list's decided rows."""
	by_event, by_title = {}, {}
	for row in review_rows({"status": ["in", list(rules.DECIDED)]}):
		if row.get("event_id"):
			by_event[row["event_id"]] = row
		if int(row.get("remember_title") or 0) and row.get("title_key"):
			by_title.setdefault(row["title_key"], row)
	return by_event, by_title


BLOCK_READ = (
	"name",
	"resource",
	"date",
	"all_day",
	"from_time",
	"to_time",
	"note",
	"source",
	"google_event_id",
)


def imported_blocks(event_ids, start, end):
	"""``{event id: [block rows]}``: imported blocks of these events inside the window. Only ever
	``source = "Google"``: a hand-made block is never read here."""
	ids = sorted({i for i in event_ids if i})
	if not ids:
		return {}
	fields = list(BLOCK_READ)
	if _has_column(BLOCK, "time_off_alerted"):
		fields.append("time_off_alerted")
	out = {}
	for row in frappe.get_all(
		BLOCK,
		filters={
			"source": SOURCE_GOOGLE,
			"google_event_id": ["in", ids],
			"date": ["between", [start, end]],
		},
		fields=fields,
		limit_page_length=0,
	):
		if row.get("source") != SOURCE_GOOGLE:
			continue  # belt and braces: a hand-made block is never touched
		out.setdefault(row.get("google_event_id"), []).append(row)
	return out


def _holidays_for(staff, start, end):
	"""``{resource: {date: ...}}`` from the Holiday Lists the availability engine reads (each
	employee's own, else the company's)."""
	from erpnext_enhancements.project_enhancements import crew_availability

	rows = [
		{"name": p["resource"], "resource_type": p.get("resource_type"), "employee": p.get("employee")}
		for p in staff
	]
	return crew_availability._read_holidays(rows, start, end) or {}


def _conflicts_for(resource, day, name):
	"""The conflicts a block makes with firm work on its day: the Conflict center's own check."""
	from erpnext_enhancements.api import planner_blocks

	return planner_blocks._conflicts_made(resource, day, name)


# ---------------------------------------------------------------------- one run


class Run:
	"""One read: what it knows, what it did (or would do, in a dry run)."""

	def __init__(self, doc, start, end, dry_run=False):
		self.doc = doc
		self.start, self.end = start, end
		self.dry_run = dry_run
		self.zone = _zone()
		self.today = _today()
		self.calendar = calendar_id(doc)
		self.staff = people()
		self.names = {p["resource"]: p["full_name"] for p in self.staff}
		self.index = rules.name_index(self.staff)
		self.by_email = emails_to_resources(self.staff)
		self.by_event, self.by_title = decisions()
		self.open_reviews = {
			r["event_id"]: r["name"] for r in review_rows({"status": rules.STATUS_OPEN}) if r.get("event_id")
		}
		self._holidays = None
		self.counts = {}
		self.rows = []
		self.review = []
		self.new_reviews = []
		self.alerts = []
		self.seen = set()

	def count(self, key, n=1):
		self.counts[key] = self.counts.get(key, 0) + n

	def holidays(self):
		if self._holidays is None:
			try:
				self._holidays = _holidays_for(self.staff, self.start, self.end)
			except Exception:
				_log_failure("Calendar time off: Holiday Lists unreadable")
				self._holidays = {}
		return self._holidays

	def everyone(self, days):
		"""``{resource: [days]}`` for a company holiday: each staff member, minus their holidays."""
		holidays = self.holidays()
		out = {}
		for person in self.staff:
			off = holidays.get(person["resource"]) or {}
			mine = [d for d in days if d["date"] not in off]
			if mine:
				out[person["resource"]] = mine
		return out

	def wanted(self, answer, event, days):
		note = rules.note_for(event.get("summary"))
		if answer["everyone"]:
			out = []
			for resource, mine in self.everyone(days).items():
				out.extend(rules.wanted_blocks([resource], mine, note))
			return out
		active = [r for r in answer["resources"] if r in self.names]
		return rules.wanted_blocks(active, days, note)


def _snapshot(event):
	return json.dumps(
		{key: event.get(key) for key in SNAPSHOT_KEYS if key in event}, sort_keys=True, default=str
	)


def _person_label(run, resource):
	return run.names.get(resource) or resource or ""


def handle_event(run, event, existing):
	"""Apply one event: its blocks (``existing`` are the ones it has in the window) and its review
	row. Returns the outcome."""
	event_id = event.get("id")
	if not event_id:
		return rules.SKIP
	answer = rules.classify(
		event, run.staff, run.by_email, run.by_event, run.by_title, run.calendar, run.index
	)
	run.count(answer["how"])
	if answer["how"] == rules.HOW_OURS:
		return rules.SKIP  # ERPNext's own event: not even looked at.
	days = []
	if answer["outcome"] in (rules.IMPORT, rules.REVIEW):
		days = rules.in_window(rules.event_days(event, run.zone), run.start, run.end)
	wanted = run.wanted(answer, event, days) if answer["outcome"] == rules.IMPORT else []
	create, update, delete = rules.diff_blocks(wanted, existing)
	title = event.get("summary") or ""
	for row in create:
		run.rows.append(_row("create", run, row, title, answer["how"]))
	for _name, row in update:
		run.rows.append(_row("update", run, row, title, answer["how"]))
	by_name = {b.get("name"): b for b in existing or []}
	for name in delete:
		run.rows.append(_row("delete", run, by_name.get(name) or {}, title, answer["how"]))
	run.count("created", len(create))
	run.count("updated", len(update))
	run.count("deleted", len(delete))

	if answer["outcome"] == rules.REVIEW and days:
		_review(run, event, answer, days)
	elif not run.dry_run and event_id in run.open_reviews:
		_close_review(run, event_id)
	if run.dry_run:
		return answer["outcome"]

	touched = []
	for row in create:
		touched.append(_create_block(event_id, row))
	for name, row in update:
		touched.append(_update_block(name, row))
	for name in delete:
		_delete_block(name)
	for doc in touched:
		if doc is not None:
			_check_conflicts(run, doc, title)
	return answer["outcome"]


def _row(action, run, block, title, how):
	date = block.get("date")
	timed = not int(block.get("all_day") or 0) and block.get("from_time")
	return {
		"action": action,
		"person": _person_label(run, block.get("resource")),
		"date": str(rules._date(date)) if date else "",
		"time": f"{rules._twelve(block.get('from_time'))}–{rules._twelve(block.get('to_time'))}"
		if timed
		else "",
		"title": title,
		"how": how,
	}


# ---------------------------------------------------------------------- writes


def _create_block(event_id, row):
	doc = frappe.new_doc(BLOCK)
	doc.update(
		{
			"resource": row["resource"],
			"date": str(row["date"]),
			"all_day": row["all_day"],
			"from_time": row.get("from_time"),
			"to_time": row.get("to_time"),
			"note": row.get("note"),
			"source": SOURCE_GOOGLE,
			"google_event_id": event_id,
		}
	)
	doc.flags.calendar_import = True
	try:
		doc.insert(ignore_permissions=True)
	except Exception:
		# One person (made inactive between the read and the write, say) must not stop the rest.
		_log_failure("Calendar time off: a block could not be created")
		return None
	return doc


def _update_block(name, row):
	try:
		doc = frappe.get_doc(BLOCK, name)
		if doc.get("source") != SOURCE_GOOGLE:
			return None  # never a hand-made block, whatever the query said
		for field in ("all_day", "from_time", "to_time", "note"):
			doc.set(field, row.get(field))
		doc.flags.calendar_import = True
		doc.save(ignore_permissions=True)
		return doc
	except Exception:
		_log_failure("Calendar time off: a block could not be updated")
		return None


def _delete_block(name):
	try:
		if frappe.db.get_value(BLOCK, name, "source") != SOURCE_GOOGLE:
			return False
		frappe.delete_doc(BLOCK, name, ignore_permissions=True)
		return True
	except Exception:
		_log_failure("Calendar time off: a block could not be deleted")
		return False


def _review(run, event, answer, days):
	"""Add or refresh the event's review row (Open), unless a planner already decided it."""
	entry = {
		"event_id": event.get("id"),
		"title": (event.get("summary") or "(no title)")[:140],
		"title_key": rules.title_key(event.get("summary"))[:140],
		"dates": rules.when_text(days),
		"start_date": str(days[0]["date"]),
		"end_date": str(days[-1]["date"]),
		"reason": answer["reason"],
		"suggested_resource": answer.get("suggested") if answer.get("suggested") in run.names else None,
		"creator": rules.email_of(event.get("creator"))[:140],
		"calendar_id": run.calendar,
		"event_json": _snapshot(event),
	}
	run.review.append(
		{
			"title": entry["title"],
			"dates": entry["dates"],
			"reason": entry["reason"],
			"suggested": _person_label(run, entry["suggested_resource"]),
		}
	)
	if run.dry_run:
		return
	name = frappe.db.get_value(REVIEW, {"event_id": entry["event_id"]}, "name")
	if name:
		doc = frappe.get_doc(REVIEW, name)
		if doc.get("status") != rules.STATUS_OPEN:
			return
		changed = any(str(doc.get(k) or "") != str(v or "") for k, v in entry.items())
		if not changed:
			return
		doc.update(entry)
		doc.flags.calendar_import = True
		doc.save(ignore_permissions=True)
		return
	doc = frappe.new_doc(REVIEW)
	doc.update({**entry, "status": rules.STATUS_OPEN})
	doc.flags.calendar_import = True
	doc.insert(ignore_permissions=True)
	run.new_reviews.append(doc)


def _close_review(run, event_id):
	"""An event that no longer needs a person: drop its review row while it is still Open. A decided
	row stays, because it is the decision."""
	name = run.open_reviews.pop(event_id, None)
	if name and frappe.db.get_value(REVIEW, name, "status") == rules.STATUS_OPEN:
		frappe.delete_doc(REVIEW, name, ignore_permissions=True)


def _check_conflicts(run, doc, title):
	"""A block that lands on firm work alerts the planners, once per block (one person, one day)."""
	try:
		if int(doc.get("time_off_alerted") or 0):
			return
		day = rules._date(doc.get("date"))
		if not day or day < run.today:
			return
		conflicts = _conflicts_for(doc.get("resource"), day, doc.get("name")) or []
		if not conflicts:
			return
		work = []
		for conflict in conflicts:
			for item in conflict.get("items") or []:
				if item.get("title") and item["title"] not in work:
					work.append(item["title"])
		run.alerts.append(
			{
				"block": doc.get("name"),
				"person": _person_label(run, doc.get("resource")),
				"date": day,
				"title": title,
				"work": work,
			}
		)
		frappe.db.set_value(BLOCK, doc.get("name"), "time_off_alerted", 1, update_modified=False)
	except Exception:
		_log_failure("Calendar time off: conflict check failed")


# ---------------------------------------------------------------------- telling the planners


def recipients(doc=None):
	"""The *Time off alerts* people, else whoever authorized the Google account."""
	doc = doc or engine.settings()
	users = []
	for row in doc.get(ALERT_TABLE) or []:
		user = row.get("user")
		if user and user not in users:
			users.append(user)
	if not users and doc.get("authorized_by"):
		users.append(doc.get("authorized_by"))
	return users


def _bell(user, subject, lines, doctype=None, name=None):
	"""A bell notification (type Alert, which Frappe never emails), the planners' own channel
	(``planner_notices._bell``)."""
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": user,
			"type": "Alert",
			"document_type": doctype if name else None,
			"document_name": name,
			"subject": frappe.utils.escape_html(subject),
			"email_content": "<br>".join(frappe.utils.escape_html(line) for line in lines or []),
		}
	).insert(ignore_permissions=True)


def alert_lines(alerts):
	"""``["Austin is off Thu Oct 15 (“Austin Off”): booked on Highlands pump set"]``."""
	out = []
	for alert in alerts:
		day = alert["date"]
		when = f"{day.strftime('%a %b')} {day.day}"
		work = ", ".join(alert.get("work") or []) or "firm work"
		out.append(f"{alert['person']} is off {when} (“{alert['title']}”): booked on {work}")
	return out


def notify(run):
	"""One bell per person for this run's conflicts, one for its new review rows. Never raises."""
	try:
		users = recipients(run.doc)
		if not users:
			return
		if run.alerts:
			people_off = sorted({a["person"] for a in run.alerts})
			subject = f"Time off from Google lands on booked work: {', '.join(people_off)}"
			lines = [*alert_lines(run.alerts), "Open the Conflict center on either planner to move the work."]
			for user in users:
				_bell(user, subject, lines, BLOCK, run.alerts[0]["block"])
		if run.new_reviews:
			n = len(run.new_reviews)
			subject = (
				"1 event on the vacations calendar needs a person"
				if n == 1
				else f"{n} events on the vacations calendar need a person"
			)
			lines = [
				f"{doc.get('title')} ({doc.get('dates')}): {doc.get('reason')}"
				for doc in run.new_reviews[:10]
			]
			lines.append("Pick the person, Everyone or Ignore in Calendar Time Off Review.")
			for user in users:
				_bell(user, subject, lines, REVIEW, run.new_reviews[0].get("name"))
	except Exception:
		_log_failure("Calendar time off: planners not told")


# ---------------------------------------------------------------------- reading Google


def _iso(day, zone, days=0):
	"""Midnight at the start of ``day`` (+ ``days``) in the site's zone, RFC 3339."""
	value = datetime.datetime.combine(day + datetime.timedelta(days=days), datetime.time())
	if zone is not None:
		value = value.replace(tzinfo=zone)
		return value.isoformat()
	return value.isoformat() + "Z"


def _apply_page(run, items, budget):
	"""Apply one page of events. False when the run ran out of time (the caller stops)."""
	ids = [e.get("id") for e in items if e.get("id") and not rules.is_erpnext_event(e)]
	existing = imported_blocks(ids, run.start, run.end) if ids else {}
	for event in items:
		if time.monotonic() > budget:
			return False
		if event.get("id") and not rules.is_erpnext_event(event):
			# Seen before it is handled: an event that fails to apply keeps its blocks.
			run.seen.add(event["id"])
		try:
			handle_event(run, event, existing.get(event.get("id")) or [])
		except google_api.GoogleAuthError:
			raise
		except Exception:
			_log_failure("Calendar time off: an event could not be read in")
		if not run.dry_run:
			frappe.db.commit()
	return True


def read(run, client, sync_token=None, full=False):
	"""List and apply. ``full`` reads the window (no token); otherwise from ``sync_token`` (or the
	whole calendar, to get a first token). Returns ``(complete, next sync token or None)``."""
	budget = time.monotonic() + RUN_SECONDS
	page = None
	for _ in range(PAGES):
		if full:
			answer = client.list_instances(
				run.calendar,
				page_token=page,
				time_min=_iso(run.start, run.zone),
				time_max=_iso(run.end, run.zone, days=1),
				show_deleted=False,
			)
		else:
			answer = client.list_instances(run.calendar, sync_token=sync_token, page_token=page)
		if not _apply_page(run, answer.get("items") or [], budget):
			return False, None
		page = answer.get("nextPageToken")
		if not page:
			return True, answer.get("nextSyncToken")
	return False, None


def remove_vanished(run):
	"""After a complete full read: imported blocks in the window whose event was not listed, and
	Open review rows for events no longer there."""
	gone_blocks = 0
	for row in frappe.get_all(
		BLOCK,
		filters={"source": SOURCE_GOOGLE, "date": ["between", [run.start, run.end]]},
		fields=["name", "google_event_id", "resource", "date", "all_day", "from_time", "to_time"],
		limit_page_length=0,
	):
		if row.get("google_event_id") in run.seen:
			continue
		run.rows.append(_row("delete", run, row, "(event no longer on the calendar)", rules.HOW_CANCELLED))
		gone_blocks += 1
		if not run.dry_run:
			_delete_block(row["name"])
	run.count("deleted", gone_blocks)
	for row in review_rows({"status": rules.STATUS_OPEN}):
		if row.get("event_id") in run.seen:
			continue
		run.count("review_closed")
		if not run.dry_run:
			frappe.delete_doc(REVIEW, row["name"], ignore_permissions=True)
	if not run.dry_run:
		frappe.db.commit()


def _summary(run):
	keys = ("created", "updated", "deleted", "title", "creator", "company", "decision", "pattern")
	parts = [f"{run.counts[k]} {k}" for k in keys if run.counts.get(k)]
	open_rows = len(review_rows({"status": rules.STATUS_OPEN}))
	parts.append(f"{open_rows} waiting for review")
	return ", ".join(parts)


def _run(full):
	"""The scheduled read (incremental, or the nightly full one). A no-op while switched off."""
	doc = engine.settings()
	if not is_on(doc) or not _lock():
		return None
	flags = frappe.flags
	try:
		setattr(flags, IMPORT_FLAG, True)
		start, end = engine.repair_window(doc)
		run = Run(doc, start, end)
		client = google_api.Client()
		token = doc.get(TOKEN_FIELD) or None
		try:
			if full:
				complete, _fresh = read(run, client, full=True)
				if complete:
					remove_vanished(run)
				fresh = None
			else:
				try:
					complete, fresh = read(run, client, sync_token=token)
				except google_api.GoogleCalendarError as exc:
					if exc.status != 410:
						raise
					# The token expired: list in full from the start and take a new one.
					complete, fresh = read(run, client, sync_token=None)
		except google_api.GoogleAuthError as exc:
			engine._auth_failed(exc)
			return None
		notify(run)
		state = {"last_time_off_summary": f"{start} to {end}: {_summary(run)}"}
		if full:
			state["last_time_off_full_read"] = engine._now()
		else:
			state["last_time_off_sync"] = engine._now()
			if fresh:
				state[TOKEN_FIELD] = fresh
		engine.write_state(**state)
		frappe.db.commit()
		return run.counts
	except Exception:
		_log_failure("Calendar time off: the read failed")
		return None
	finally:
		setattr(flags, IMPORT_FLAG, False)
		_unlock()


def incremental_read():
	"""``scheduler_events`` every 10 minutes: what changed on the calendar since the last read."""
	return _run(full=False)


def nightly_read():
	"""``scheduler_events.daily_long``: the whole window again, and removal of vanished events."""
	return _run(full=True)


def apply_review(name):
	"""Queued after a planner decides a review row: apply it (and, when it is remembered for the
	title, every Open row with that title) from the stored event, with no Google call."""
	doc = engine.settings()
	if not is_on(doc) or not _table_exists(REVIEW):
		return None
	row = frappe.db.get_value(
		REVIEW, name, ["name", "event_id", "title_key", "remember_title", "event_json"], as_dict=True
	)
	if not row:
		return None
	targets = [row]
	if int(row.get("remember_title") or 0) and row.get("title_key"):
		targets += [
			r
			for r in review_rows({"status": rules.STATUS_OPEN, "title_key": row["title_key"]})
			if r.get("name") != name
		]
	flags = frappe.flags
	try:
		setattr(flags, IMPORT_FLAG, True)
		start, end = engine.repair_window(doc)
		run = Run(doc, start, end)
		for target in targets:
			try:
				event = json.loads(target.get("event_json") or "{}")
			except Exception:
				continue
			if not event.get("id"):
				continue
			existing = imported_blocks([event["id"]], start, end).get(event["id"]) or []
			handle_event(run, event, existing)
			frappe.db.commit()
		notify(run)
		return run.counts
	except Exception:
		_log_failure("Calendar time off: a review decision could not be applied")
		return None
	finally:
		setattr(flags, IMPORT_FLAG, False)


def queue_review(name):
	frappe.enqueue(
		"erpnext_enhancements.project_enhancements.calendar_sync.time_off.apply_review",
		queue="default",
		enqueue_after_commit=True,
		job_id=f"calendar_time_off::review::{name}",
		name=name,
	)


def queue_full_read():
	frappe.enqueue(
		"erpnext_enhancements.project_enhancements.calendar_sync.time_off.nightly_read",
		queue="long",
		timeout=3600,
		enqueue_after_commit=True,
		job_id="calendar_time_off::full_read",
	)


# ---------------------------------------------------------------------- preview and health


def preview(start=None, end=None):
	"""The dry run: what a full read of ``start``..``end`` would create, update and delete, and what
	would wait for review. Reads Google (an authorized account is needed, the switches are not) and
	**writes nothing**: no block, no review row, no alert, no sync state, nothing to Google."""
	from frappe.utils import date_diff, getdate

	doc = engine.settings()
	if not calendar_id(doc):
		frappe.throw(frappe._("Fill in the Company Holidays & Vacations calendar ID first."))
	if not google_api.stored_refresh_token():
		frappe.throw(frappe._("Authorize with Google first: the preview has to read the calendar."))
	if not installed():
		frappe.throw(frappe._("Run bench migrate first: the time-off fields are not on this site yet."))
	if not start or not end:
		first, last = engine.repair_window(doc)
		start, end = start or first, end or last
	start, end = getdate(start), getdate(end)
	if end < start:
		frappe.throw(frappe._("The end date is before the start date."))
	if date_diff(end, start) > PREVIEW_MAX_DAYS:
		frappe.throw(frappe._("Pick a range of {0} days or less.").format(PREVIEW_MAX_DAYS))
	run = Run(doc, start, end, dry_run=True)
	try:
		complete, _token = read(run, google_api.Client(), full=True)
	except google_api.GoogleCalendarError as exc:
		frappe.throw(frappe._("Could not read the calendar: {0}").format(exc.describe()))
	if complete:
		remove_vanished(run)
	run.rows.sort(key=lambda r: (r["date"], r["person"], r["action"]))
	return {
		"start": str(start),
		"end": str(end),
		"complete": complete,
		"counts": run.counts,
		"rows": run.rows[:1000],
		"review": run.review[:300],
		"enabled": is_on(doc),
	}


def health(doc=None):
	"""The Settings page's time-off lines. Never raises."""
	try:
		doc = doc or engine.settings()
		return {
			"switch": bool(int(doc.get(SWITCH) or 0)),
			"active": is_on(doc),
			"installed": installed(),
			"last_sync": str(doc.get("last_time_off_sync") or ""),
			"last_full_read": str(doc.get("last_time_off_full_read") or ""),
			"summary": doc.get("last_time_off_summary") or "",
			"open_reviews": len(review_rows({"status": rules.STATUS_OPEN})),
			"imported_blocks": frappe.db.count(BLOCK, {"source": SOURCE_GOOGLE}) if installed() else 0,
			"recipients": recipients(doc),
			"review_route": REVIEW_ROUTE,
		}
	except Exception:
		return {"switch": False, "active": False, "installed": False}
