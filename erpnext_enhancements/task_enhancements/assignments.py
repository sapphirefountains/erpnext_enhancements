"""The rules behind My Work: when an assignment is due, what it asks, and when its record is done.

My Work (WI-081 Phase 1) is a better *view of Frappe's own assignments*, not a second system. An
assignment is still a ToDo row made by the sidebar's Assign To, an Assignment Rule, the crew
mirrors, Triton or the AI follow-up tool, and closing one still goes through the ToDo controller,
which rebuilds the record's ``_assign`` list. This module holds the decisions every surface shares
(the My Work page, the banner on a record, the sidebar count, the email, auto-close and the backlog
triage), so they cannot disagree with each other.

It imports nothing from frappe, so ``tests/test_my_work_rules.py`` runs it bench-free.

Things it is careful about, including the ones that look like bugs:

* **A ToDo's ``date`` is usually not a due date.** ``assign_to.add`` writes
  ``args.get("date", nowdate())``, so an assignment made without a "Complete By" date is stamped
  with the day it was made. On production 1,141 of 1,310 open ToDos carried their creation day
  (2026-10-20), which is why "97% overdue" was mostly an artefact. :func:`due_date` therefore reads
  a date equal to the creation day as *no due date*. The one case that costs: a person who picks
  today's date on purpose and misses it sees it under "No due date" the next morning rather than
  "Overdue". Every date set later -- a snooze, a drag to another day, any date other than the
  creation day -- counts.
* **Task has two spellings of cancelled.** The site's Property Setter offers "Canceled", ERPNext's
  own code writes "Cancelled", and ERPNext's ``unassign_todo`` only clears on the second, so 35
  open ToDos sat on "Canceled" Tasks. :data:`TASK_FINISHED` is the tuple ``task.py`` and
  ``crew_availability.py`` already use (tested equal).
* **"Closed Won" is not finished.** Winning is when the next piece of work starts; an open
  assignment on a won Opportunity on production read "Will you please create a new project for
  this?". The same reasoning keeps Task "Pending Review" open: it is waiting on the reviewer.
* **Auto-close fires on the change into a finished state, never on every save of a finished
  record** (:func:`just_finished`). Someone assigned to a Completed Project afterwards ("send the
  final invoice") keeps the assignment through every later save of that Project.
"""

import datetime
import html
import re

#: Statuses a Task never leaves by itself. Same literal as
#: ``task_enhancements/doctype/task/task.py`` and ``project_enhancements/crew_availability.py``;
#: redefined rather than imported because both of those import frappe (the first ERPNext too).
TASK_FINISHED = ("Completed", "Canceled", "Cancelled", "Invoiced", "Template")

#: Same literal as ``crew_availability.CLOSED_PROJECT_STATUSES``. Invoiced and Paid come after
#: Completed; a Project that jumps straight to one of them is finished too.
PROJECT_FINISHED = ("Completed", "Invoiced", "Paid", "Cancelled", "Canceled")

#: doctype -> (field, values that mean the record's work is over). Read by auto-close (on the
#: change into one of them) and by the backlog triage (on the state as it stands). A doctype that
#: is not here is closed only when it is cancelled (docstatus 2), which every doctype shares.
FINISHED_STATES = {
	"Task": ("status", TASK_FINISHED),
	# Stock statuses. The site's own pipeline adds "Closed Won", deliberately left out (above).
	"Opportunity": ("status", ("Lost", "Closed", "Converted")),
	"Project": ("status", PROJECT_FINISHED),
	"Lead": ("status", ("Converted", "Do Not Contact", "Lost Quotation")),
	"Customer": ("disabled", (1,)),
	# "Ordered" stays open: the material is not here yet, and whoever holds it is chasing it.
	"Material Request": (
		"status",
		("Stopped", "Cancelled", "Received", "Transferred", "Issued", "Manufactured"),
	),
	"Purchase Order": ("status", ("Completed", "Closed", "Cancelled", "Delivered")),
	"Quotation": ("status", ("Lost", "Cancelled")),
	"Issue": ("status", ("Resolved", "Closed")),
	# A visit's assignment is the visit itself; submitting it is doing it.
	"Sapphire Maintenance Record": ("docstatus", (1, 2)),
}

#: Fields read as integers (a Check, docstatus); everything else compares as stored.
_INT_FIELDS = ("docstatus", "disabled")

#: Accounts that are nobody's inbox. The customer follow-up reminder fell back to a Customer's
#: owner, which is Administrator for every imported customer: 286 open ToDos on production.
NOBODY = ("Administrator", "Guest")

#: The buckets My Work draws, in the order it draws them. "No due date" sits after Today: it is
#: work to pick up whenever, and before what has been put off to later in the week.
BUCKETS = ("overdue", "today", "undated", "week", "later")

#: Buckets that count toward the sidebar number: everything not put off to a later day. Snoozing
#: an item is therefore how you take it off the count.
ACTIONABLE = ("overdue", "today", "undated")

#: The description ``assign_to.add`` writes when the Assign To dialog's comment is left empty.
_AUTO_DESCRIPTION = "Assignment for {0} {1}"

#: A ToDo the backlog triage may close, and what it becomes. "record_finished" is Closed: the work
#: happened. Everything else is Cancelled: nobody did it, and nobody is going to.
TRIAGE_STATUS = {
	"record_missing": "Cancelled",
	"record_finished": "Closed",
	"nobody_owns": "Cancelled",
	"not_a_prospect": "Cancelled",
	"stale": "Cancelled",
}

#: How long an assignment and its record must both have sat untouched to count as stale.
STALE_DAYS = 90

#: The description the customer inactivity reminder writes (script_migrations/customer.py).
FOLLOW_UP_PREFIX = "Follow up with "


def as_date(value):
	"""A ``datetime.date`` from a date, a datetime or an ISO string; ``None`` for anything else."""
	if value is None or value == "":
		return None
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	text = str(value).strip()
	try:
		return datetime.date.fromisoformat(text[:10])
	except ValueError:
		return None


def as_datetime(value):
	"""A ``datetime.datetime`` from a datetime, a date (midnight) or an ISO string; else ``None``."""
	if value is None or value == "":
		return None
	if isinstance(value, datetime.datetime):
		return value
	if isinstance(value, datetime.date):
		return datetime.datetime.combine(value, datetime.time())
	text = str(value).strip().replace("T", " ")
	try:
		return datetime.datetime.fromisoformat(text)
	except ValueError:
		day = as_date(text)
		return datetime.datetime.combine(day, datetime.time()) if day else None


def due_date(date, created):
	"""The day an assignment is due, or ``None`` when it has none.

	A date equal to the day the ToDo was made is the stamp ``assign_to.add`` writes when nobody
	chose a date, so it is read as none (module docstring).
	"""
	due = as_date(date)
	if due is None:
		return None
	made = as_date(created)
	if made is not None and due == made:
		return None
	return due


def bucket(due, today):
	"""Which of :data:`BUCKETS` an assignment due on ``due`` belongs in, seen on ``today``."""
	if due is None:
		return "undated"
	if due < today:
		return "overdue"
	if due == today:
		return "today"
	if (due - today).days <= 7:
		return "week"
	return "later"


def plain_text(value):
	"""A ToDo description as one line of text: tags out, entities decoded, whitespace collapsed.

	Descriptions are Text Editor HTML (``<div class="ql-editor read-mode"><p>...``). The result
	is escaped again wherever it is drawn; this is for reading, not for markup.
	"""
	if not value:
		return ""
	text = re.sub(r"<[^>]*>", " ", str(value))
	text = html.unescape(text)
	return re.sub(r"\s+", " ", text).strip()


def paragraphs(value):
	"""A ToDo description as plain text that keeps its line and paragraph breaks.

	For the email body, whose ``prose`` macro escapes what it is given and keeps newlines: handed
	the raw Text Editor HTML, as it was until v1.696.0, it printed the tags.
	"""
	if not value:
		return ""
	text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6])\s*>", "\n", str(value))
	text = html.unescape(re.sub(r"<[^>]*>", "", text))
	lines = [re.sub(r"[ \t\r\f\v ]+", " ", line).strip() for line in text.split("\n")]
	return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def the_ask(description, reference_type=None, reference_name=None):
	"""What the assignment asks for, as plain text, or ``""`` when it asks nothing of its own.

	The Assign To dialog's empty comment becomes "Assignment for Task TASK-0001", which only
	repeats the record's name, so it reads as no ask at all.
	"""
	text = plain_text(description)
	if reference_type and reference_name:
		if text == _AUTO_DESCRIPTION.format(reference_type, reference_name):
			return ""
	return text


def truncate(text, limit):
	"""``text`` cut to ``limit`` characters at a word boundary, with an ellipsis when cut."""
	text = text or ""
	if len(text) <= limit:
		return text
	cut = text[: max(limit - 1, 1)].rsplit(" ", 1)[0].rstrip(" ,;:.-")
	return (cut or text[: limit - 1]) + "…"


def email_subject(title, ask, doctype_label=None, limit=120):
	"""The assignment email's subject: what the record is, then what is asked of it.

	"Smith Residence rebuild: Send revised quote with the LED package". The old subject was
	"ToDo: OPP-2026-00187 [For Opportunity OPP-2026-00187]", which named the record twice and the
	work not at all. The email also goes to whoever assigned it, so the subject never says "you".
	"""
	title = (title or "").strip()
	ask = (ask or "").strip()
	if title and ask:
		subject = f"{title}: {ask}"
	elif ask:
		subject = ask
	elif title and doctype_label:
		subject = f"{title} ({doctype_label})"
	elif title:
		subject = title
	else:
		subject = "New to-do"
	return truncate(subject, limit)


def finished_value(doctype, value):
	"""True when ``value`` of :data:`FINISHED_STATES`' field for ``doctype`` means it is done."""
	spec = FINISHED_STATES.get(doctype)
	if not spec:
		return False
	field, values = spec
	if field in _INT_FIELDS:
		try:
			value = int(value or 0)
		except (TypeError, ValueError):
			return False
	return value in values


def finished_field(doctype):
	"""The field :data:`FINISHED_STATES` reads for ``doctype``, or ``None``."""
	spec = FINISHED_STATES.get(doctype)
	return spec[0] if spec else None


def just_finished(doctype, before, after):
	"""True when a save moved ``doctype``'s field from not-finished (or nothing) to finished.

	``before`` is the stored value before the save, ``None`` for a new record. A record saved
	while it is *already* finished is not "just finished", so an assignment added to it later
	survives the next save.
	"""
	if not finished_value(doctype, after):
		return False
	return before is None or not finished_value(doctype, before)


def is_generated_follow_up(row):
	"""A ToDo the customer inactivity reminder wrote: on a Customer, its text, no assigner."""
	return (
		(row.get("reference_type") or "") == "Customer"
		and plain_text(row.get("description")).startswith(FOLLOW_UP_PREFIX)
		and not row.get("assigned_by")
	)


def triage_rule(row, now):
	"""Which :data:`TRIAGE_STATUS` rule closes this open ToDo, or ``None`` to leave it open.

	``row`` is a ToDo's ``allocated_to``, ``assigned_by``, ``reference_type``, ``description``,
	``date``, ``creation`` and ``modified``, plus ``ref``: ``None`` when the ToDo names no record,
	else ``{"exists", "finished", "modified", "prospect"}`` for the record it names. ``now`` is a
	datetime. First match wins, in this order:

	1. ``record_missing``: it names a record that no longer exists.
	2. ``record_finished``: the record is finished (:data:`FINISHED_STATES`, or cancelled) and was
	   last changed after the assignment was made -- it finished with this assignment open.
	   An assignment made on an already-finished record is deliberate work and stays.
	3. ``nobody_owns``: it sits on Administrator or Guest, whose inbox nobody reads.
	4. ``not_a_prospect``: a generated "Follow up with" reminder for a Customer that is no longer
	   a prospect, which is the only thing the reminder was ever for.
	5. ``stale``: neither the assignment nor its record has been touched in :data:`STALE_DAYS`
	   days, and it is not due in the future.

	**An unassigned ToDo is not closed for being unassigned.** Triton, the phone agent, files every
	call follow-up with no assignee and no record ("Send an estimate for one Stillwater panel
	emergency shut-off unit", 2026-10-20): live customer work that My Work's Unclaimed list now
	shows to everyone. Only the staleness rule reaches one.
	"""
	ref = row.get("ref")
	created = as_datetime(row.get("creation"))
	if ref is not None and not ref.get("exists"):
		return "record_missing"
	if ref is not None and ref.get("finished"):
		changed = as_datetime(ref.get("modified"))
		if changed is not None and created is not None and changed >= created:
			return "record_finished"
	if (row.get("allocated_to") or "") in NOBODY:
		return "nobody_owns"
	if ref is not None and ref.get("prospect") is False and is_generated_follow_up(row):
		return "not_a_prospect"
	cutoff = now - datetime.timedelta(days=STALE_DAYS)
	touched = as_datetime(row.get("modified"))
	ref_touched = as_datetime(ref.get("modified")) if ref is not None else None
	due = due_date(row.get("date"), row.get("creation"))
	if (
		touched is not None
		and touched < cutoff
		and (ref is None or (ref_touched is not None and ref_touched < cutoff))
		and (due is None or due <= now.date())
	):
		return "stale"
	return None


# ---------------------------------------------------------------------------
# Phase 2: alerts (task_enhancements/assignment_alerts.py)

#: Days past a real due date before whoever assigned it is told, once.
ESCALATE_AFTER_DAYS = 2

#: The longest text message the High-priority alert sends: two SMS segments.
SMS_LIMIT = 300


def escalation_due(row, today, after_days=ESCALATE_AFTER_DAYS):
	"""True when an open assignment is ``after_days`` past a real due date, someone else assigned
	it, and they have not been told yet (``custom_escalated_on`` empty).

	Only a *real* due date counts (:func:`due_date`): the creation-day stamp is not a promise
	anyone made, so escalating on it would tell every assigner about every assignment two days
	after making it.
	"""
	if row.get("custom_escalated_on"):
		return False
	assigner, assignee = row.get("assigned_by"), row.get("allocated_to")
	if not assigner or not assignee or assigner == assignee:
		return False
	due = due_date(row.get("date"), row.get("creation"))
	return due is not None and (today - due).days >= after_days


def due_today(row, today):
	"""True when an open assignment's real due date is ``today``: the morning nudge."""
	return due_date(row.get("date"), row.get("creation")) == today


def digest_worth_sending(sections):
	"""Whether a morning summary has anything to say. ``sections`` maps a section to its rows.

	"No due date" alone is not news: it is the standing pile, which the page and the sidebar
	count already show. A summary goes when something is overdue, due today, new since yesterday,
	or late with someone you assigned it to.
	"""
	return any(sections.get(key) for key in ("overdue", "today", "new", "waiting"))


def sms_text(assigner_name, title, ask, url, limit=SMS_LIMIT):
	"""The High-priority assignment text: who, what, and the link, cut to fit two segments.

	"High priority from James Harris: Smith Residence rebuild - Send revised quote. <url>". The
	link is never cut; the ask gives way first.
	"""
	head = f"High priority from {assigner_name}: " if assigner_name else "High priority: "
	what = (title or "").strip()
	ask = (ask or "").strip()
	if ask and ask != what:
		what = f"{what} - {ask}" if what else ask
	tail = f" {url}" if url else ""
	room = max(limit - len(head) - len(tail), 20)
	return f"{head}{truncate(what, room)}{tail}"


# ---------------------------------------------------------------------------
# Phase 3: teams, repeats, checklists

#: A team is a Role whose name ends with this. Production already has nine (2026-10-20): Support,
#: Production, Sales, Design, Finance, Operations, Executive, HR and Marketing Team. A queue item is
#: a ToDo with that Role in ToDo's native ``role`` field and nobody in ``allocated_to``.
TEAM_SUFFIX = " Team"

#: ``ToDo.custom_repeat`` options -> (unit, size). Months are counted from the first due date, not
#: chained, so a monthly on the 31st comes back on the 31st (or the month's last day), not the 28th
#: forever after February.
REPEATS = {
	"Daily": ("days", 1),
	"Weekly": ("days", 7),
	"Every 2 weeks": ("days", 14),
	"Monthly": ("months", 1),
	"Quarterly": ("months", 3),
	"Yearly": ("months", 12),
}


def is_team_role(role):
	return bool(role) and str(role).endswith(TEAM_SUFFIX) and len(str(role)) > len(TEAM_SUFFIX)


def team_label(role):
	""" "Sales Team" -> "Sales"."""
	return str(role)[: -len(TEAM_SUFFIX)] if is_team_role(role) else str(role or "")


def add_months(day, months):
	"""``day`` plus ``months`` calendar months, the day clamped to the month's last."""
	import calendar

	month_index = day.month - 1 + months
	year = day.year + month_index // 12
	month = month_index % 12 + 1
	return datetime.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def next_occurrence(due, today, repeat):
	"""The next due date of a repeating assignment finished on ``today``, or ``None``.

	Counted from its own due date, in whole steps, to the first one after today: a weekly due on
	Mondays and finished on a Wednesday comes back next Monday, not next Wednesday, and one that
	fell weeks behind does not come back already overdue. With no due date, one step from today.
	"""
	spec = REPEATS.get(repeat or "")
	if not spec:
		return None
	unit, size = spec
	base = due or today
	step = 1
	while True:
		if unit == "days":
			nxt = base + datetime.timedelta(days=size * step)
		else:
			nxt = add_months(base, size * step)
		if nxt > today:
			return nxt
		step += 1


def checklist_progress(rows):
	"""(done, total) over a checklist's rows (dicts or objects with ``done``)."""
	rows = list(rows or [])
	done = 0
	for row in rows:
		value = row.get("done") if hasattr(row, "get") else getattr(row, "done", 0)
		try:
			done += 1 if int(value or 0) else 0
		except (TypeError, ValueError):
			pass
	return done, len(rows)


# ---------------------------------------------------------------------------
# Done on a Task (Nik, 2026-10-20: "when marking a Task as Done it should also change the task's
# status to Completed, marked as completed on that day, and by the person marking it done").

#: The statuses of a Task another depends on that let that other Task be completed: ERPNext's
#: "Completed" and "Cancelled", plus this site's spelling "Canceled", which the Task override's
#: ``validate_status`` lets through too (``doctype/task/task.py`` ``DEPENDENCY_DONE``; the two are
#: held equal by ``test_my_work``). Done checks this first, so its refusal names the Task in the way.
TASK_DEPENDENCY_DONE = ("Completed", "Cancelled", "Canceled")


def done_completes_task(reference_type, repeat):
	"""Whether Done on an assignment also completes the Task it is on.

	Not for a repeating assignment: its repeat says the work comes back, and completing the Task
	would close the next occurrence as soon as it was made. A Task that is already finished is
	left as it is; that is checked against the Task when Done is pressed.
	"""
	return reference_type == "Task" and not repeat


def blocking_dependency(depends_on):
	"""The first of ``depends_on`` -- ``[(task, status)]`` -- that stops ERPNext completing the
	Task, as ``(task, status)``, or ``None``. A dependency that no longer exists has no status,
	and blocks, as it does in ERPNext."""
	for task, status in depends_on or []:
		if task and status not in TASK_DEPENDENCY_DONE:
			return task, status
	return None


# ---------------------------------------------------------------------------
# Where a link to an assignment goes (Nik, 2026-10-23: "the email for when a document is assigned to
# someone needs to link to the ToDo and or Task that is specifically that ToDo or Task, and not the
# My Work homepage by default").

#: An assignment on one of these opens the record itself; an assignment on anything else, or on
#: nothing, opens its ToDo. Nik's choice: a Task is the to-do in everyone's head and its form has the
#: "Assigned to you" banner, and a maintenance visit is where the work is done. On an Opportunity or
#: a Customer the ask is easy to lose on a long form, so the ToDo -- what was asked, by whom, by
#: when, with Done and Snooze, and the record one tap away -- is the page to land on. Until v1.739.0
#: every record opened itself and a ToDo with no record opened My Work, which marked the row only
#: when it was open, yours and in the first 40 of its section, and otherwise showed the homepage.
RECORD_LINK_DOCTYPES = ("Task", "Sapphire Maintenance Record")

#: The words on a link's button, by where it goes. Anything else says "Open <doctype>".
LINK_LABELS = {
	"Task": "Open task",
	"Sapphire Maintenance Record": "Open visit",
	"ToDo": "Open to-do",
}


def link_kind(reference_type, reference_name, can_read_record=True, can_read_todo=True):
	"""Where a link to one assignment goes, for one reader: ``"record"``, ``"todo"`` or ``"my_work"``.

	A Task or a visit opens the record, unless the reader may not read it. Everything else opens the
	ToDo, which its assignee, whoever assigned it and whoever made it can always read (v16
	``todo.has_permission``). The one reader who cannot is a team member told about a queue item,
	which nobody owns yet: they get the record when they may read it, and otherwise My Work, where
	the Unclaimed tab lets them take it.
	"""
	has_record = bool(reference_type and reference_name)
	if has_record and reference_type in RECORD_LINK_DOCTYPES and can_read_record:
		return "record"
	if can_read_todo:
		return "todo"
	if has_record and can_read_record:
		return "record"
	return "my_work"


def link_label(doctype):
	"""The button's words for a link that opens ``doctype``: "Open task", "Open to-do"."""
	return LINK_LABELS.get(doctype) or (f"Open {doctype}" if doctype else "Open My Work")


def heading(description, reference_type=None, reference_name=None, title=None, limit=100):
	"""What an assignment's ToDo form is headed with: the ask's first line, else its record's title.

	Frappe heads a ToDo with its whole description, which is "Assignment for Task TASK-2026-02302"
	on a fifth of production's Task assignments (74 of 372 in the 90 days to 2026-10-23) and a
	paragraph on Triton's call follow-ups.
	"""
	first = ""
	if the_ask(description, reference_type, reference_name):
		first = paragraphs(description).split("\n", 1)[0].strip()
	return truncate(first, limit) or (title or "").strip() or (reference_name or "") or "To-do"
