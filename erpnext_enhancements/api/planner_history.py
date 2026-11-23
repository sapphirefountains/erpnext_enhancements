# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Done work moved onto old dates, on the Project and Maintenance Planners (v1.796.0).

Nik, 2026-11-04: "In the Maintenance Planner and Project Planner and such I need to be able to move
previously done tasks and such to old dates for historical reasons." He picked, through four questions:

* **Where.** The Project Planner's Gantt (a done task's bar moves; its Not scheduled list holds the done
  tasks that never had dates, 4,529 of 5,854 on production that day), the Calendar and Crew grids behind a
  Display switch that draws done tasks there too (``get_planner(done=1)``, :func:`add_done_work`), and the
  Maintenance Planner: a submitted or in-review visit moves to another past day with its clock times, and
  an unstarted draft (or a Fountain Move job) can go on a past day.
* **Dates.** All of them move together: a Task's planned start and end, its actual start and end and its
  Completed On (:func:`done_task_dates`), so it reads as done on the new days everywhere. A visit's day of
  visit, its scheduled day and its clock-in and clock-out (:func:`visit_dates`).
* **Who.** Managers only (:data:`BACKDATE_ROLES`). Everyone else sees done work locked, as before.
* **Note.** Every move leaves an automatic note on the record's timeline (:func:`history_note`): who moved
  it, from which dates to which, on which planner. No prompt.

What made it impossible before: the engine's ``read_tasks`` leaves finished tasks (and closed projects)
out, so the grids never drew them; the Gantt drew a Completed task with ``movable=False``; the Maintenance
Planner's ``_edit_visit`` refused any day before today and any started visit, and ``_refuse_finished`` any
visit in review or submitted; ``move_fountain_move`` refused a past day. ERPNext's own check of a Task's
dates against its Project's window (``validate_parent_project_dates``, erpnext 16.52.1) was already handled:
``project_window.before_task_validate`` widens the window first, on every Task save.

**History, not schedule.** These endpoints are their own, not ``save_task`` or ``move_visit``, because the
planners' schedule rules do not apply to a record of what already happened:

* no conflict check and no reason (overbooking a day that has passed changes nobody's day);
* no "Notify crew?" and no notice of any kind;
* never drafted, whatever Draft mode says: a draft is a schedule waiting to be published, and this is not;
* nothing that follows moves: neither the planner's auto-shift (P4) nor ERPNext's own
  ``Task.reschedule_dependent_tasks``, which ``flags.ignore_reschedule`` turns off for the save;
* done work stays in the past: the new last day is today or earlier (:data:`PAST_ONLY`).

Undo puts back exactly what was there (``restore_done_task`` / ``restore_done_visit``); it is how a done
task taken out of the Gantt's Not scheduled list goes back to having no dates, which ``save_task`` cannot
do (it reads a blank date as "unchanged"). The dates it puts back are the server's own record of the
move, kept in the cache by the record and the ``modified`` the move left (:data:`UNDO_KEY`), never dates
the browser sends (review of #1427): a record changed since, or a cache flushed by a deploy, has no Undo.

A submitted visit (and one waiting in review) is written with ``frappe.db.set_value`` after the checks
here, not saved: its date fields are not ``allow_on_submit``, making them so would let anyone with
submit edit a finished visit's dates on the form, and an in-review save runs the workflow. Its Calendar
Sync event is re-synced as ``on_update_after_submit`` would. The Timesheet, Stock Entry and invoice a
submitted visit already made stay as they are, and the contract's next visit date is not rolled again.

The pure pieces take plain values, so ``tests/test_planner_history.py`` runs them without a bench.
"""

import datetime
import html

import frappe
from frappe import _

#: Who may move done work onto old dates (Nik, 2026-11-04: "Managers only"). Administrator too.
BACKDATE_ROLES = frozenset({"System Manager", "Projects Manager", "Maintenance Supervisor"})
#: The Task statuses that are done work. A canceled task is not history to move: it never happened.
DONE_TASK_STATUSES = ("Completed",)
#: A Task's dates that move, in the order the note lists them.
TASK_FIELDS = (
	"exp_start_date",
	"exp_end_date",
	"custom_start_datetime",
	"custom_end_datetime",
	"act_start_date",
	"act_end_date",
	"completed_on",
)
#: A visit's dates that move, in the order the note lists them.
VISIT_FIELDS = ("visit_date", "scheduled_visit_date", "clock_in_time", "clock_out_time")
VISIT = "Sapphire Maintenance Record"
#: The most done tasks one range draws (a month of finished jobs is a few hundred); past it the answer
#: says ``done_truncated`` and the page says so.
DONE_LIMIT = 1000
#: How long a move can be undone (review of #1427): the dates it replaced are kept on the server, keyed
#: by the record and the ``modified`` the move left, never taken back from the browser. A deploy
#: flushes Redis, so an Undo after one is refused with a message, which is the safe direction.
UNDO_SECONDS = 12 * 60 * 60
UNDO_KEY = "planner_history:undo:{doctype}:{name}:{modified}"
#: Mirrors ``sapphire_maintenance_record.TECHNICIAN_SIGNATURE_FROM`` (tests hold them equal): a blank
#: visit made before this day and dated before it would pass the signature gate (``_signature_exempt``).
SIGNATURE_FROM = datetime.date(2026, 10, 13)

PROJECT_PLANNER = "Project Planner"
MAINTENANCE_PLANNER = "Maintenance Planner"

MANAGERS_ONLY = "Only Projects Managers, Maintenance Supervisors and System Managers can move done work."
PAST_ONLY = "Done work goes on today or an earlier day."
NOT_DONE = "{0} is not done: move it as usual."
CANCELED_VISIT = "{0} was canceled, so it has no day to move."
NO_UNDO = (
	"This move can no longer be undone from the planner: the record changed since, or the planner was "
	"restarted. Change its dates on the record."
)
BLANK_BEFORE_SIGNATURES = (
	"{0} was never filled in, and a blank visit made before {1} cannot be dated before that day: it would "
	"skip the technician's signature. Fill it in from the Visit Wizard with its date."
)


# ---------------------------------------------------------------------- pure helpers


def may_backdate(user, roles):
	"""Whether ``user`` holding ``roles`` may move done work onto old dates."""
	return user == "Administrator" or bool(BACKDATE_ROLES & set(roles or ()))


def is_done_task(row):
	return (row or {}).get("status") in DONE_TASK_STATUSES


def as_date(value):
	"""A date from a date, a datetime or ISO text; None for a blank."""
	if not value:
		return None
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	text = str(value).strip()
	return datetime.date.fromisoformat(text[:10]) if text else None


def as_datetime(value):
	"""A datetime from a datetime, a date or ISO text; None for a blank."""
	if not value:
		return None
	if isinstance(value, datetime.datetime):
		return value
	if isinstance(value, datetime.date):
		return datetime.datetime.combine(value, datetime.time())
	text = str(value).strip()
	return datetime.datetime.fromisoformat(text) if text else None


def shift_date(value, days):
	"""``value`` (a date field) ``days`` later; None stays None."""
	day = as_date(value)
	return day + datetime.timedelta(days=days) if day else None


def shift_datetime(value, days):
	"""``value`` (a datetime field) ``days`` later, at the same time of day; None stays None."""
	moment = as_datetime(value)
	return moment + datetime.timedelta(days=days) if moment else None


def done_task_dates(old_span, new_span, values, today):
	"""``{field: new value}`` for a done Task's actual start and end and its Completed On, after its
	planned days went from ``old_span`` to ``new_span`` (``(first_day, last_day)``; ``old_span`` None
	for a task that had no dates). The planned dates themselves are ``project_planner._move``'s.

	Each one moves with its planned counterpart: the actual start by as many days as the first day,
	the actual end and Completed On by as many as the last day. A task that had no planned dates is
	measured from its own done dates instead (Completed On, else the actual end; the actual start,
	else that). A done task with no Completed On is given the new last day: it is done, and now it says
	when. Completed On never goes past ``today``, which ERPNext refuses (``validate_completed_on``),
	and neither do the actual dates (review of #1427); an actual start never goes after the actual end
	(``validate_from_to_dates``).
	"""
	new_first, new_last = as_date(new_span[0]), as_date(new_span[1])
	act_start, act_end = as_date(values.get("act_start_date")), as_date(values.get("act_end_date"))
	completed = as_date(values.get("completed_on"))
	if old_span:
		start_anchor, end_anchor = as_date(old_span[0]), as_date(old_span[1])
	else:
		end_anchor = completed or act_end or act_start
		start_anchor = act_start or end_anchor
	start_days = (new_first - start_anchor).days if start_anchor else None
	end_days = (new_last - end_anchor).days if end_anchor else None

	out = {}
	if act_start and start_days is not None:
		out["act_start_date"] = shift_date(act_start, start_days)
	if act_end and end_days is not None:
		out["act_end_date"] = shift_date(act_end, end_days)
	if completed and end_days is not None:
		out["completed_on"] = shift_date(completed, end_days)
	elif not completed:
		out["completed_on"] = new_last
	today = as_date(today)
	# Nothing done is dated after today: Completed On (which ERPNext refuses), and the actual dates too.
	for field in ("act_start_date", "act_end_date", "completed_on"):
		if out.get(field) and today and out[field] > today:
			out[field] = today
	first, last = out.get("act_start_date", act_start), out.get("act_end_date", act_end)
	if first and last and first > last:
		out["act_start_date"] = last
	return {field: value for field, value in out.items() if as_date(value) != as_date(values.get(field))}


def visit_dates(values, target):
	"""``{field: new value}`` for a visit moved to ``target``: the day of the visit becomes ``target``,
	and its scheduled day and clock-in and clock-out move by as many days as the visit did (a clock time
	keeps its time of day). The visit is measured from its day of visit, else its scheduled day, else the
	day it clocked in. A scheduled day that was blank becomes ``target``: an unscheduled draft moved onto a
	past day is recorded as done that day, and the Visit Wizard then dates it there (a backfill).
	"""
	target = as_date(target)
	anchor = (
		as_date(values.get("visit_date"))
		or as_date(values.get("scheduled_visit_date"))
		or as_date(values.get("clock_in_time"))
	)
	days = (target - anchor).days if anchor else 0
	out = {"visit_date": target}
	scheduled = as_date(values.get("scheduled_visit_date"))
	out["scheduled_visit_date"] = shift_date(scheduled, days) if scheduled else target
	for field in ("clock_in_time", "clock_out_time"):
		if values.get(field) and days:
			out[field] = shift_datetime(values.get(field), days)
	return {field: value for field, value in out.items() if _text(value) != _text(values.get(field))}


def restore_problem(values, today):
	"""Why dates put back by an Undo cannot be written, or None: nothing done after ``today`` (Completed
	On, the actual dates, the day of a visit), an actual start never after its end and a clock-in never
	after its clock-out. The values come from the server's own record of the move (:data:`UNDO_KEY`),
	so this is defence in depth, not trust in the browser."""
	today = as_date(today)
	for field in ("completed_on", "act_start_date", "act_end_date", "visit_date"):
		day = as_date(values.get(field))
		if day and today and day > today:
			return PAST_ONLY
	first, last = as_date(values.get("act_start_date")), as_date(values.get("act_end_date"))
	if first and last and first > last:
		return "The actual start is after the actual end."
	clock_in, clock_out = as_datetime(values.get("clock_in_time")), as_datetime(values.get("clock_out_time"))
	if clock_in and clock_out and clock_in > clock_out:
		return "The clock-in is after the clock-out."
	return None


def blank_visit_refusal(values, created, target, name, gate=SIGNATURE_FROM):
	"""The refusal for a never-started visit (no day of visit) made before ``gate`` and moved to a day
	before it, or None (review of #1427). Such a visit would count as done before signatures existed
	(``SapphireMaintenanceRecord._signature_exempt``: dated **and** made before the gate) and slip past
	the signature check that refused the bulk Request Review of thirteen blank drafts on 2026-10-13."""
	if values.get("visit_date"):
		return None
	created, target = as_date(created), as_date(target)
	if created and target and created < gate and target < gate:
		return BLANK_BEFORE_SIGNATURES.format(name, gate.isoformat())
	return None


def _text(value):
	"""A date or time as the note shows it: ``2026-03-02``, ``2026-03-02 07:30``; ``""`` for a blank."""
	if not value:
		return ""
	if isinstance(value, datetime.datetime):
		return value.strftime("%Y-%m-%d %H:%M")
	if isinstance(value, datetime.date):
		return value.isoformat()
	text = str(value).strip()
	if len(text) > 10:
		try:
			return as_datetime(text).strftime("%Y-%m-%d %H:%M")
		except ValueError:
			return text
	return text


def changed_fields(before, after, fields):
	"""``[(field, old text, new text)]`` for each of ``fields`` whose shown value changed."""
	out = []
	for field in fields:
		old, new = _text(before.get(field)), _text(after.get(field))
		if old != new:
			out.append((field, old, new))
	return out


def history_note(planner, who, changes, labels=None, undone=False):
	"""The timeline note of a move (HTML, escaped): who, on which planner, each date from what to what.

	``changes`` is :func:`changed_fields`' list; ``labels`` maps a field to its label (the field name
	shows where none is given). A blank date reads "none"."""
	labels = labels or {}
	lines = "".join(
		f"<li>{html.escape(labels.get(field) or field)}: {html.escape(old or _NONE)} → {html.escape(new or _NONE)}</li>"
		for field, old, new in changes
	)
	if undone:
		head = "{} undid a move of this done work on the {}.".format(
			html.escape(who or ""), html.escape(planner)
		)
	else:
		head = "{} moved this done work on the {}, as a historical record.".format(
			html.escape(who or ""), html.escape(planner)
		)
	return f"<p>{head}</p><ul>{lines}</ul>"


_NONE = "none"


def done_bookings(days, cards, start, end):
	"""Put each done card on its crew's days in the Crew view (``days`` is ``get_planner``'s ``days``,
	``{resource: {"YYYY-MM-DD": cell}}``), as a ``task`` booking marked ``done`` with no hours: the day's
	capacity, booked and free hours are the engine's and stay as they were. Only cells already there are
	written. Returns how many bookings were added."""
	start, end = as_date(start), as_date(end)
	added = 0
	for card in cards or []:
		first, last = as_date(card.get("start")), as_date(card.get("end") or card.get("start"))
		if not first or not last or not start or not end:
			continue
		first, last = max(first, start), min(last, end)
		for member in card.get("crew") or []:
			per_day = (days or {}).get(member.get("resource"))
			if not per_day:
				continue
			day = first
			while day <= last:
				cell = per_day.get(str(day))
				if cell is not None:
					cell.setdefault("bookings", []).append(
						{
							"kind": "task",
							"ref": card.get("name"),
							"label": card.get("subject") or card.get("name"),
							"hours": 0,
							"done": True,
						}
					)
					added += 1
				day += datetime.timedelta(days=1)
	return added


def serialized(values):
	"""``values`` with every date and time as ISO text, for the answer's ``before``."""
	out = {}
	for field, value in (values or {}).items():
		if isinstance(value, datetime.datetime):
			out[field] = value.isoformat(sep=" ")
		elif isinstance(value, datetime.date):
			out[field] = value.isoformat()
		else:
			out[field] = str(value) if value else None
	return out


# ---------------------------------------------------------------------- gates


def can_backdate():
	"""Whether the session user may move done work onto old dates."""
	return may_backdate(frappe.session.user, frappe.get_roles())


def _require_backdate():
	if not can_backdate():
		frappe.throw(_(MANAGERS_ONLY), frappe.PermissionError)


def _today():
	return as_date(frappe.utils.nowdate())


def _who():
	user = frappe.session.user
	try:
		return frappe.utils.get_fullname(user) or user
	except Exception:
		return user


def _labels(doc, fields):
	out = {}
	for field in fields:
		try:
			out[field] = _(doc.meta.get_label(field)) or field
		except Exception:
			out[field] = field
	return out


def _undo_key(doctype, name, modified):
	return UNDO_KEY.format(doctype=doctype, name=name, modified=modified)


def _remember(doc, before):
	"""Keep the dates a move replaced, for its Undo, keyed by the ``modified`` the move left."""
	try:
		frappe.cache.set_value(
			_undo_key(doc.doctype, doc.name, str(doc.modified)),
			serialized(before),
			expires_in_sec=UNDO_SECONDS,
		)
	except Exception:
		frappe.log_error(title="Planner: a move's Undo could not be kept", message=frappe.get_traceback())


def _recall(doctype, name, modified):
	"""The dates the move that left ``modified`` replaced, or None (changed since, expired, flushed)."""
	try:
		values = frappe.cache.get_value(_undo_key(doctype, name, modified))
	except Exception:
		values = None
	return values if isinstance(values, dict) else None


def _forget(doctype, name, modified):
	try:
		frappe.cache.delete_value(_undo_key(doctype, name, modified))
	except Exception:
		pass


# ---------------------------------------------------------------------- Project Planner


def _task_values(doc):
	return {field: doc.get(field) for field in TASK_FIELDS}


def _require_done_task(doc):
	if not is_done_task(doc):
		frappe.throw(_(NOT_DONE).format(doc.get("subject") or doc.name))


def _save_task(doc, before, undone=False):
	"""Save a done task moved on the planner, note it, keep what it replaced for its Undo, and answer.
	Nothing that follows it moves."""
	from erpnext_enhancements.api import project_planner as pp

	doc.flags.ignore_reschedule = True  # ERPNext's own push of the tasks that depend on it
	warnings = pp._save(doc)
	after = _task_values(doc)
	changes = changed_fields(before, after, TASK_FIELDS)
	if changes:
		doc.add_comment(
			"Comment",
			history_note(PROJECT_PLANNER, _who(), changes, _labels(doc, TASK_FIELDS), undone=undone),
		)
	if not undone:
		_remember(doc, before)
	return {"name": doc.name, "modified": str(doc.modified), "warnings": warnings, "historical": True}


@frappe.whitelist(methods=["POST"])
def move_done_task(task, modified, start=None, end=None):
	"""Move a done Task onto other days, as the Project Planner's Gantt and grids drag it.

	``start`` alone moves it and keeps its length; ``end`` alone moves its last day; both set both (as
	``save_task`` takes them). A task with no dates is given them. Its actual dates and Completed On move
	with it (:func:`done_task_dates`). Managers only; the new last day is today or earlier; no conflict
	check, no notice, never drafted, nothing that follows moves; a note goes on its timeline.

	Returns ``{"name", "modified", "warnings", "historical": True}``, with ``unchanged: True`` when the
	days are the same. The dates it replaced stay on the server for :func:`restore_done_task`.
	"""
	from erpnext_enhancements.api import project_planner as pp
	from erpnext_enhancements.project_enhancements import crew_availability as engine

	_require_backdate()
	doc = pp._load(task, modified)
	_require_done_task(doc)
	before = _task_values(doc)
	old = engine.task_span(pp._state(doc))
	pp._move(doc, pp.given(start), pp.given(end))
	new = engine.task_span(pp._state(doc))
	if not new or new == old:
		return {
			"name": doc.name,
			"modified": str(doc.modified),
			"warnings": [],
			"historical": True,
			"unchanged": True,
		}
	today = _today()
	if new[1] > today:
		frappe.throw(_(PAST_ONLY))
	for field, value in done_task_dates(old, new, before, today).items():
		doc.set(field, value)
	return _save_task(doc, before)


@frappe.whitelist(methods=["POST"])
def restore_done_task(task, modified):
	"""Undo :func:`move_done_task`: put back exactly the dates that move replaced (a blank clears one),
	so a done task that had no dates has none again. ``modified`` is what the move left: the dates come
	from the server's record of that move (:data:`UNDO_KEY`), never from the browser, and a task changed
	since has no Undo here (:data:`NO_UNDO`). The same gates and note as a move."""
	from erpnext_enhancements.api import project_planner as pp

	_require_backdate()
	doc = pp._load(task, modified)
	_require_done_task(doc)
	values = _recall(doc.doctype, doc.name, str(modified))
	if values is None:
		frappe.throw(_(NO_UNDO))
	refusal = pp._subtask_refusal(doc)
	if refusal:
		frappe.throw(refusal)
	if doc.get("custom_rental_booking"):
		frappe.throw(_("This task's dates follow Rental Booking {0}.").format(doc.custom_rental_booking))
	problem = restore_problem(values, _today())
	if problem:
		frappe.throw(_(problem))
	was = _task_values(doc)
	for field in TASK_FIELDS:
		if field in values:
			doc.set(field, values[field] or None)
	answer = _save_task(doc, was, undone=True)
	_forget(doc.doctype, doc.name, str(modified))
	return answer


def _done_assignees(names):
	"""``{task: [user]}``: who was assigned each done task. ERPNext closes the assignments when a task
	is completed, so Closed counts as well as Open: they are who did it."""
	out = {}
	if not names:
		return out
	for row in frappe.get_all(
		"ToDo",
		filters={
			"reference_type": "Task",
			"reference_name": ["in", list(names)],
			"status": ["in", ["Open", "Closed"]],
		},
		fields=["reference_name", "allocated_to"],
		limit_page_length=0,
	):
		users = out.setdefault(row.get("reference_name"), [])
		if row.get("allocated_to") and row.get("allocated_to") not in users:
			users.append(row.get("allocated_to"))
	return out


def read_done_tasks(start, end):
	"""``(rows, truncated)``: done leaf Tasks on customer jobs whose planned days overlap
	``start``..``end``, finished jobs included (the engine's ``read_tasks`` leaves both out), at most
	:data:`DONE_LIMIT` of them; ``truncated`` says there were more. Rental crew tasks are not here: their
	days follow the booking. Raw SQL after the planner's role gate, for the COALESCE over nullable dates,
	as ``read_tasks``; re-checked against ``task_span`` and ``is_planner_job``, which are the definitions."""
	from erpnext_enhancements.project_enhancements import crew_availability as engine

	cols = engine._task_columns()
	selected = ", ".join(f"{expr} AS `{column}`" for column, expr in cols.items())
	slot_start, slot_end = cols["custom_start_datetime"], cols["custom_end_datetime"]
	rental = cols["custom_rental_booking"]
	type_expr, stream = engine.planner_job_sql("p")
	job = engine.planner_job_condition("p", rental)
	rows = frappe.db.sql(
		f"""
		SELECT
			t.name, t.subject, t.project, t.status, t.exp_start_date, t.exp_end_date,
			t.expected_time, t.color, t.modified, t.completed_on, {selected},
			p.project_name AS project_title, p.status AS project_status,
			{type_expr} AS project_type, {stream} AS planner_stream
		FROM `tabTask` t
		LEFT JOIN `tabProject` p ON p.name = t.project
		WHERE IFNULL(t.is_group, 0) = 0
			AND IFNULL(t.is_template, 0) = 0
			AND t.status IN %(done)s
			AND IFNULL({rental}, '') = ''
			AND {job}
			AND (
				(
					COALESCE(DATE(t.exp_start_date), DATE(t.exp_end_date), DATE({slot_start})) <= %(end)s
					AND COALESCE(DATE(t.exp_end_date), DATE(t.exp_start_date), DATE({slot_end}), DATE({slot_start})) >= %(start)s
				)
				OR DATE({slot_start}) BETWEEN %(start)s AND %(end)s
			)
		ORDER BY t.exp_start_date, t.name
		LIMIT %(limit)s
		""",
		{
			"start": start,
			"end": end,
			"done": DONE_TASK_STATUSES,
			"limit": DONE_LIMIT + 1,  # one more than is drawn, to know whether there were more
			**engine.PLANNER_SQL_VALUES,
		},
		as_dict=True,
	)
	truncated = len(rows) > DONE_LIMIT
	out = []
	for row in rows[:DONE_LIMIT]:
		span = engine.task_span(row)
		if span and engine.is_planner_job(row) and span[0] <= as_date(end) and span[1] >= as_date(start):
			out.append(row)
	return out, truncated


def done_cards(start, end, data, all_tasks=False, may=False):
	"""``(cards, truncated)``: the done tasks of ``start``..``end`` as Project Planner cards (``truncated``
	from :func:`read_done_tasks`): ``finished``, never ``movable``,
	and ``backdate`` (a manager may move them by their dates, here and on the Gantt). The display rule
	is the schedule's (D6): phase and crew work, unless ``all_tasks``. The crew is the task's crew rows,
	else who it was assigned to (:func:`_done_assignees`)."""
	from erpnext_enhancements.api import project_planner as pp
	from erpnext_enhancements.project_enhancements import crew_availability as engine
	from erpnext_enhancements.project_enhancements import fountain_move_jobs

	rows, truncated = read_done_tasks(start, end)
	if not rows:
		return [], truncated
	names = [row.get("name") for row in rows]
	crew_rows, _open = engine.read_crews(names)
	assignees = _done_assignees(names)
	user_to_resource = data.get("user_to_resource") or {}
	labels = {r.get("name"): r.get("label") for r in data.get("resources") or []}
	crews = {
		name: engine.resolve_crew(crew_rows.get(name), assignees.get(name), user_to_resource)
		for name in names
	}
	if not all_tasks:
		crewless = [name for name in names if not crews[name]]
		under = engine.tasks_under_a_phase(crewless) if crewless else set()
		rows = [row for row in rows if crews[row.get("name")] or row.get("name") in under]
	today = _today()
	cards = []
	for row in rows:
		crew = [
			dict(m, label=m.get("label") or labels.get(m["resource"]) or m["resource"])
			for m in crews[row["name"]]
		]
		card = pp.build_card(row, crew, {}, [], today, False)
		for member in card["crew"]:
			member["booked"] = None
		card.update(
			finished=True,
			movable=False,
			backdate=bool(may),
			overdue=False,
			short=0,
			completed_on=str(as_date(row.get("completed_on"))) if row.get("completed_on") else None,
		)
		cards.append(card)
	fountain_move_jobs.mark_read_only(cards)  # moved on the Maintenance Planner, never here
	for card in cards:
		if card.get("fountain_move"):
			card["backdate"] = False
	return cards, truncated


def add_done_work(result, start, end, data, all_tasks=False):
	"""``get_planner(done=1)``: the range's done tasks join ``tasks`` (``finished`` cards), sit on their
	crew's days in the Crew view (:func:`done_bookings`), and their projects join the Project filter.
	``can_backdate`` says whether this person may move them, and ``done_truncated`` that the range held
	more than :data:`DONE_LIMIT` (the page says so). Nothing the engine computed changes."""
	from erpnext_enhancements.api import project_planner as pp

	may = can_backdate()
	cards, truncated = done_cards(start, end, data, all_tasks=all_tasks, may=may)
	result["done"] = True
	result["can_backdate"] = may
	result["done_tasks"] = len(cards)
	result["done_truncated"] = bool(truncated)
	if not cards:
		return result
	shown = {card.get("name") for card in result.get("tasks") or []}
	cards = [card for card in cards if card.get("name") not in shown]
	result["tasks"] = list(result.get("tasks") or []) + cards
	done_bookings(result.get("days"), cards, start, end)
	listed = {row.get("name") for row in result.get("projects") or []}
	missing = {
		card.get("project") for card in cards if card.get("project") and card.get("project") not in listed
	}
	if missing:
		# _projects answers every Active customer job plus the names it is given: keep only the missing.
		found = [row for row in pp._projects(missing) if row.get("name") in missing]
		result["projects"] = sorted(
			list(result.get("projects") or []) + found,
			key=lambda row: str(row.get("title") or row.get("name") or "").casefold(),
		)
	return result


# ---------------------------------------------------------------------- Maintenance Planner


def _visit_values(doc):
	return {field: doc.get(field) for field in VISIT_FIELDS}


def _visit_access(doc):
	"""The caller may change this visit and it is not canceled.

	``write``, not ``submit`` (review of #1427): frappe 16.53.0's ``has_permission`` applies no docstatus
	rule, so ``write`` on a submitted visit is the role's write right, which all three managers hold;
	Maintenance Supervisors hold no ``submit`` on visits, and Nik named them. A submitted or in-review
	visit is then written straight to its row, past the save's own check (:func:`_write_visit`), so this
	is that write's permission check."""
	doc.check_permission("write")
	if doc.docstatus == 2:
		frappe.throw(_(CANCELED_VISIT).format(doc.name))


def _write_visit(doc, before, changes, undone=False):
	"""Write a visit's moved dates, note it, keep what it replaced for its Undo, and answer. An open
	draft is saved under the caller's permissions, so its hooks run; a visit in review or submitted is
	written straight to its row (see the module docstring) and its calendar event re-synced."""
	from erpnext_enhancements.api import maintenance_planner as mp

	warnings = []
	if doc.docstatus == 0 and (doc.get("workflow_state") or "") != mp.PENDING_STATE:
		for field, value in changes.items():
			doc.set(field, value)
		warnings = mp._save_with_warnings(doc)
	else:
		frappe.db.set_value(VISIT, doc.name, changes)
		doc.reload()
		try:
			from erpnext_enhancements.project_enhancements.calendar_sync import triggers

			triggers.on_change(doc, "on_update_after_submit")
		except Exception:
			frappe.log_error(
				title="Maintenance Planner: a moved visit's calendar event", message=frappe.get_traceback()
			)
	after = _visit_values(doc)
	moved = changed_fields(before, after, VISIT_FIELDS)
	if moved:
		doc.add_comment(
			"Comment",
			history_note(MAINTENANCE_PLANNER, _who(), moved, _labels(doc, VISIT_FIELDS), undone=undone),
		)
	if not undone:
		_remember(doc, before)
	return {
		"name": doc.name,
		"modified": str(doc.modified),
		"date": str(as_date(doc.get("visit_date"))) if doc.get("visit_date") else None,
		"warnings": warnings,
		"historical": True,
	}


@frappe.whitelist(methods=["POST"])
def move_done_visit(record, modified=None, date=None):
	"""Move a visit onto a past day, as the Maintenance Planner drags it: a submitted or in-review
	visit, a started draft, or an unstarted draft (which is then recorded as done that day). Its day of
	visit, scheduled day and clock times move together (:func:`visit_dates`). Managers only; ``date`` is
	today or earlier; a blank visit made before signatures is not dated before them
	(:func:`blank_visit_refusal`); nothing else about the visit changes; a note goes on its timeline.

	Returns ``{"name", "modified", "date", "warnings", "historical": True}``, with ``unchanged: True``
	when nothing moved. The dates it replaced stay on the server for :func:`restore_done_visit`.
	"""
	from erpnext_enhancements.api import maintenance_planner as mp

	mp._require_planner()
	_require_backdate()
	target = as_date(date)
	if not target:
		frappe.throw(_("Pick the day the visit was done."))
	if target > _today():
		frappe.throw(_(PAST_ONLY))
	doc = frappe.get_doc(VISIT, record)
	_visit_access(doc)
	mp._check_fresh(doc, modified)
	before = _visit_values(doc)
	refusal = blank_visit_refusal(before, doc.get("creation"), target, doc.name)
	if refusal:
		frappe.throw(_(refusal))
	changes = visit_dates(before, target)
	if not changes:
		return {
			"name": doc.name,
			"modified": str(doc.modified),
			"date": str(target),
			"warnings": [],
			"historical": True,
			"unchanged": True,
		}
	return _write_visit(doc, before, changes)


@frappe.whitelist(methods=["POST"])
def restore_done_visit(record, modified):
	"""Undo :func:`move_done_visit`: put back exactly the dates that move replaced (a blank clears one).
	``modified`` is what the move left: the dates come from the server's record of that move
	(:data:`UNDO_KEY`), never from the browser, and a visit changed since has no Undo here. The same
	gates and note as a move."""
	from erpnext_enhancements.api import maintenance_planner as mp

	mp._require_planner()
	_require_backdate()
	doc = frappe.get_doc(VISIT, record)
	_visit_access(doc)
	mp._check_fresh(doc, modified)
	values = _recall(doc.doctype, doc.name, str(modified))
	if values is None:
		frappe.throw(_(NO_UNDO))
	was = _visit_values(doc)
	restored = {field: values.get(field) for field in VISIT_FIELDS if field in values}
	problem = restore_problem(restored, _today())
	if problem:
		frappe.throw(_(problem))
	changes = {
		field: value or None for field, value in restored.items() if _text(value) != _text(was.get(field))
	}
	if not changes:
		_forget(doc.doctype, doc.name, str(modified))
		return {
			"name": doc.name,
			"modified": str(doc.modified),
			"date": str(as_date(doc.get("visit_date"))) if doc.get("visit_date") else None,
			"warnings": [],
			"historical": True,
			"unchanged": True,
		}
	answer = _write_visit(doc, was, changes, undone=True)
	_forget(doc.doctype, doc.name, str(modified))
	return answer
