"""Planner Phase 6B, faster scheduling (TASK-2026-02468): the Project Planner's new writes, and the
search both planners share.

Nik picked four things on 2026-10-09: a right-click menu, drag-to-resize with double-click quick add,
moving several tasks at once, and search with keyboard shortcuts. Most of that is page work; what
needs the server is here:

* :func:`duplicate_task` copies one task as a new Task, on the same days or from another day. The
  copy is Copy week's (``project_planner._copy_values``: crew, hours, crew size, qualifications,
  pencil flag, location, equipment, the outdoor and customer-facing flags), so the two can never
  disagree about what a copy carries.
* :func:`split_task` cuts one task in two at a day: the first keeps the days before it, the second
  (a new Task that ``depends_on`` the first) the rest. Hours, and any crew member's own hours, are
  shared pro rata by working days. A one-day task splits in two halves, the second on a day the page
  picked (the person's next free day). All or nothing, inside one savepoint.
* :func:`quick_add_task` creates a task for one person on one day from a double-click.
* :func:`move_many` moves several tasks (and/or pencils or firms them) in one call: one conflict
  check for the lot, one reason, all or nothing, as ``shift_successors`` and ``copy_week`` do.
* :func:`remove_created_task` is the Undo of a task the planner just created (a duplicate, a quick
  add, the second half of a split): it deletes it only when nothing has been attached since, and
  never with ``force``. For a split it also puts the first half back.
* :func:`search_planner` finds tasks, projects and people for the search box, customer jobs only;
  ``planner="maintenance"`` finds visits, sites and technicians for the Maintenance Planner.

The rules every write here keeps, because the page relies on them:

* **Overbooking warns and never blocks.** A change that creates a conflict answers
  ``{"needs_reason": True, "conflicts": {person: [..]}}`` and writes nothing; sent again with a
  ``reason`` it saves and the reason goes on the timeline. The page's own ``send`` asks.
* **A person is never picked for anyone.** Nothing here chooses who: a duplicate keeps the crew it
  copied, a quick add books only the person the planner named, and a split copies the crew to both
  halves ("suggest a crew" was declined).
* **Each planner moves only its own records.** Every write here is a project Task's. A rental crew
  task is refused: its dates follow its Rental Booking.
* **A new task cannot be a draft.** Draft mode (Phase 3B) holds back changes to tasks that exist,
  as a ``Planner Draft Change`` of that task; a task that does not exist yet has nothing to hang one
  on. So duplicate, split and quick add refuse in draft mode with a sentence that says so, and the
  page refuses before it asks. :func:`move_many` changes tasks that exist, so it drafts properly.
* **Writes are as the caller.** ``check_permission`` on every task, ``create`` for a new one, and
  ``doc.save()`` / ``doc.insert()`` so ERPNext's own checks and the crew-to-assignment sync run.
"""

import datetime
import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from erpnext_enhancements.api import project_planner as pp
from erpnext_enhancements.project_enhancements import crew_availability as engine
from erpnext_enhancements.project_enhancements import planner_tracking

#: The search box: at most this many results, people and projects first, at most this many of each.
SEARCH_LIMIT = 20
SEARCH_PEOPLE = 5
SEARCH_PROJECTS = 5
#: Rows read per kind before the Python re-check; the re-check only ever drops rows.
SEARCH_SCAN = 60
SEARCH_MIN_CHARS = 2
SEARCH_MAX_CHARS = 80
PLANNERS = ("project", "maintenance")
#: A task subject longer than this is refused (ERPNext's own field is Data, 140 characters).
SUBJECT_MAX = 140
#: Most hours a quick add may book (a typing slip of 800 for 8 should not make a 100-day task).
QUICK_ADD_MAX_HOURS = 400
#: The timeline reason when an Undo puts the first half of a split back over a conflict.
UNDO_REASON = "Undo on the Project Planner"
DRAFT_REFUSAL = (
	"Draft mode holds back changes to tasks that already exist; a new task cannot be a draft. "
	"Turn Draft mode off to add it."
)
INTERVAL_DOCTYPE = planner_tracking.INTERVAL_DOCTYPE
DRAFT_DOCTYPE = "Planner Draft Change"


# ---------------------------------------------------------------------- pure helpers


def working_days(first, last):
	"""Monday-to-Friday days from ``first`` to ``last`` inclusive (0 when ``last`` is earlier)."""
	return sum(1 for day in engine.daterange(first, last) if day.weekday() < 5)


def split_weights(span, split_day):
	"""``(first, second)``: how a multi-day task's hours are shared when it is cut at ``split_day``.

	The working days (Monday to Friday) of each part. When either part has none (a part that is all
	weekend), by calendar days instead: a part with no hours would read as "no estimate", which the
	engine books as a **full day** for each of its days, the opposite of what a split means.
	"""
	first_span = (span[0], split_day - datetime.timedelta(days=1))
	second_span = (split_day, span[1])
	first, second = working_days(*first_span), working_days(*second_span)
	if first and second:
		return first, second
	return (first_span[1] - first_span[0]).days + 1, (second_span[1] - second_span[0]).days + 1


def split_hours(total, first_weight, second_weight):
	"""``(first, second)`` hours: ``total`` shared pro rata, to the hundredth; they always sum to it."""
	total = round(flt(total), 2)
	weight = flt(first_weight) + flt(second_weight)
	if total <= 0 or weight <= 0:
		return 0.0, 0.0
	first = round(total * flt(first_weight) / weight, 2)
	return first, round(total - first, 2)


def split_crew(crew, first_weight, second_weight):
	"""The crew of each part. A member's own hours (over the whole task) are shared like the task's;
	a member with none ("an even share", or a full day) keeps none on both parts."""
	first, second = [], []
	for member in crew or []:
		if not member.get("resource"):
			continue
		row = {"resource": member.get("resource"), "is_lead": 1 if member.get("is_lead") else 0}
		own = flt(member.get("hours"))
		if own > 0:
			mine, theirs = split_hours(own, first_weight, second_weight)
			first.append(dict(row, hours=mine or None))
			second.append(dict(row, hours=theirs or None))
		else:
			first.append(dict(row, hours=None))
			second.append(dict(row, hours=None))
	return first, second


def like_pattern(text):
	"""``%text%`` for a SQL LIKE, with LIKE's own wildcards (and the escape character) escaped."""
	escaped = str(text or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
	return f"%{escaped}%"


def merge_results(people, projects, records, limit=SEARCH_LIMIT):
	"""``(results, truncated)``: up to ``SEARCH_PEOPLE`` people and ``SEARCH_PROJECTS`` projects (or
	sites), then the records (tasks or visits) up to ``limit`` in all."""
	head = list(people or [])[:SEARCH_PEOPLE] + list(projects or [])[:SEARCH_PROJECTS]
	room = max(limit - len(head), 0)
	tail = list(records or [])[:room]
	truncated = (
		len(people or []) > SEARCH_PEOPLE
		or len(projects or []) > SEARCH_PROJECTS
		or len(records or []) > room
	)
	return head + tail, truncated


def nearest_first(rows, anchor, day_of):
	"""``rows`` with a date first, the nearest to ``anchor`` first; undated ones last, by name."""

	def key(row):
		day = engine._as_date(day_of(row))
		if not day:
			return (1, 0, str(row.get("name") or ""))
		return (0, abs((day - anchor).days), str(row.get("name") or ""))

	return sorted(rows or [], key=key)


def hypothetical(name, what):
	"""The name a task that does not exist yet goes by in a conflict check ("TASK-1 (copy)")."""
	return f"{name} ({what})"


# ---------------------------------------------------------------------- shared checks


def _refuse_draft(draft, message=DRAFT_REFUSAL):
	if cint(pp.given(draft)):
		frappe.throw(_(message), title=_("Not in Draft mode"))


def _require_create():
	if not frappe.has_permission("Task", "create"):
		frappe.throw(_("You cannot create Tasks."), frappe.PermissionError)


def _require_customer_job(project):
	if not project:
		frappe.throw(_("The task has no project, so it is not on the planner."))
	if project not in engine.planner_projects({project}):
		frappe.throw(
			_("{0} is not a customer job, so its tasks are not on the planner.").format(
				frappe.utils.escape_html(project)
			)
		)


def _refuse_rental(doc, what):
	if doc.get("custom_rental_booking"):
		frappe.throw(
			_(
				"{0} is a rental crew task: its dates follow Rental Booking {1}, so it cannot be {2} here."
			).format(doc.name, doc.custom_rental_booking, what)
		)


def _installed(doctype):
	try:
		return bool(frappe.db.exists("DocType", doctype))
	except Exception:
		return False


def _has_timesheet(name):
	return bool(frappe.db.exists("Timesheet Detail", {"task": name, "docstatus": ["<", 2]}))


def _has_intervals(name):
	return _installed(INTERVAL_DOCTYPE) and bool(frappe.db.exists(INTERVAL_DOCTYPE, {"task": name}))


def _insert(doc):
	"""``doc.insert()`` as the caller; what it said through msgprint comes back as warnings."""
	log = pp._message_log()
	mark = len(log) if log is not None else 0
	doc.insert()
	return pp._take_messages(log, mark)


def _copy_credentials(target, source):
	target.set(
		"custom_required_credentials",
		[
			{"credential_type": row.get("credential_type")}
			for row in (source.get("custom_required_credentials") or [])
			if row.get("credential_type")
		],
	)


def _comment_reason(doc, text, conflicts, reason):
	doc.add_comment(
		"Comment",
		text.format(
			frappe.utils.escape_html(pp.conflict_summary(conflicts)), frappe.utils.escape_html(reason)
		),
	)


def _created(doc, warnings, conflicts, **extra):
	return {
		"name": doc.name,
		"modified": str(doc.modified),
		"created": True,
		"warnings": warnings,
		"conflicts": conflicts,
		"card": pp._card(doc),
		**extra,
	}


# ---------------------------------------------------------------------- duplicate


@frappe.whitelist(methods=["POST"])
def duplicate_task(task, date=None, reason=None, draft=0):
	"""Copy ``task`` as a new Task: on the same days, or starting on ``date`` (keeping its length).

	The copy is Copy week's (``project_planner._copy_values``): subject, project, parent task, crew
	rows, hours, crew size, qualifications, pencil flag, location, equipment, the *Outdoor work* and
	*Customer-facing visit* flags, status Open and "Copied from TASK-X on the Project Planner" in its
	description. An undated task's copy is undated, unless ``date`` is given (then it gets enough days
	for its estimate, as a drop from the Unscheduled tray does).

	Refused for a rental crew task, a group or template task, a task that is not a customer job, in
	draft mode, and without create permission. A copy that would overbook someone answers
	``{"needs_reason": True, "conflicts"}`` and creates nothing. Returns ``{"name", "modified",
	"created": True, "source", "card", "warnings", "conflicts"}``.
	"""
	pp._require_planner()
	_refuse_draft(draft)
	_require_create()
	doc = frappe.get_doc("Task", task)
	doc.check_permission("read")
	_refuse_rental(doc, _("duplicated"))
	if cint(doc.get("is_group")) or cint(doc.get("is_template")):
		frappe.throw(_("{0} is a group or template task, so it is not duplicated here.").format(doc.name))
	_require_customer_job(doc.get("project"))

	state = pp._state(doc)
	span = engine.task_span(state)
	day = getdate(date) if pp.given(date) else None
	crew = pp._current_crew(doc)
	values = pp._copy_values(doc, state, (day - span[0]).days if (day and span) else 0)
	if day and not span:
		people = max(len(crew), cint(doc.get("custom_crew_size")), 1)
		first, last = engine.span_for_estimate(
			day, doc.get("expected_time"), people, engine.get_settings()["default_day_hours"]
		)
		values["exp_start_date"], values["exp_end_date"] = str(first), str(last)

	preview = dict(
		state,
		name=hypothetical(doc.name, "copy"),
		status="Open",
		**{field: values.get(field) for field in pp.DATE_FIELDS},
	)
	conflicts = engine.preview_batch([(preview, crew)]) if engine.task_span(preview) else {}
	reason = (pp.given(reason) or "").strip()
	if conflicts and not reason:
		return {"needs_reason": True, "conflicts": conflicts}

	new = frappe.get_doc(values)
	pp._set_crew(new, crew, crew)
	_copy_credentials(new, doc)
	if doc.get("custom_equipment"):
		pp._set_equipment(new, pp.equipment_payload(doc.get("custom_equipment")))
	new.check_permission("create")
	warnings = _insert(new)
	if conflicts:
		_comment_reason(
			new, _("Duplicated on the Project Planner over a conflict: {0}. Reason: {1}"), conflicts, reason
		)
	return _created(new, warnings, conflicts, source=doc.name)


# ---------------------------------------------------------------------- split


def _refuse_split(doc):
	"""Why ``doc`` cannot be split, as a refusal: the planner is told exactly what is in the way."""
	_refuse_rental(doc, _("split"))
	if cint(doc.get("is_group")) or cint(doc.get("is_template")):
		frappe.throw(_("{0} is a group or template task, so it is not split here.").format(doc.name))
	if doc.get("status") in engine.FINISHED_STATUSES:
		frappe.throw(_("{0} is {1}, so it is not split.").format(doc.name, _(doc.get("status"))))
	if _has_timesheet(doc.name):
		frappe.throw(
			_(
				"Time is logged against {0} on a timesheet, so it cannot be split: the logged hours would no "
				"longer match the work. Add a new task for the rest instead."
			).format(doc.name)
		)
	if _has_intervals(doc.name):
		frappe.throw(
			_(
				"Someone has clocked time on {0} at the kiosk, so it cannot be split: the clocked hours would "
				"no longer match the work. Add a new task for the rest instead."
			).format(doc.name)
		)


def _clamp_slot_fields(target, first_day, last_day, was_slot):
	"""Keep a task's from-to datetimes inside its new days (their times of day kept). A part that a
	split leaves on one day would turn into a time slot, which books the slot's length instead of its
	share of the hours, so a pair that was not a slot before is cleared rather than made one."""
	for field in ("custom_start_datetime", "custom_end_datetime"):
		value = engine._as_datetime(target.get(field))
		if not value:
			continue
		day = min(max(value.date(), first_day), last_day)
		if day != value.date():
			target[field] = pp.on_date(target.get(field), day)
	if not was_slot and engine.task_slot(target):
		target["custom_start_datetime"] = None
		target["custom_end_datetime"] = None


@frappe.whitelist(methods=["POST"])
def split_task(task, split_date, modified, reason=None, draft=0):
	"""Cut ``task`` in two at ``split_date``.

	* A **multi-day** task: ``split_date`` is one of its days after the first. The task keeps the days
	  before it; a new Task gets ``split_date`` to the old last day.
	* A **one-day** task ("Split in two"): ``split_date`` is a later day (the page offers the person's
	  next free day). The task keeps its day, the new one goes on ``split_date``, each with half.

	Hours are shared pro rata by working days (:func:`split_weights`; halves for a one-day task, and a
	one-day task with no estimate gets half a day each, from Settings' full-day hours per person), and
	so is any crew member's own hours. The new Task copies the crew, qualifications, equipment, the
	pencil flag and the two Phase 5 flags (``project_planner._copy_values``), ``depends_on`` the first,
	and both get a timeline note. All or nothing, in one savepoint.

	Refused for a rental crew task, a task with time on a timesheet or clocked at the kiosk (Job
	Interval), a finished, group or template task, in draft mode, and without create permission;
	``modified`` is the optimistic lock, as for ``save_task``. A split that would overbook someone
	answers ``needs_reason`` and writes nothing. Returns ``{"name", "modified", "card", "first":
	{start, end, expected_time}, "second": {name, modified, start, end, expected_time, card},
	"warnings", "conflicts"}``.
	"""
	pp._require_planner()
	_refuse_draft(
		draft,
		"Draft mode holds back changes to tasks that already exist; a split makes a new task, which "
		"cannot be a draft. Turn Draft mode off to split it.",
	)
	_require_create()
	doc = pp._load(task, modified)
	_refuse_split(doc)
	state = pp._state(doc)
	span = engine.task_span(state)
	if not span:
		frappe.throw(_("{0} has no dates, so there is nothing to split.").format(doc.name))
	day = getdate(split_date) if pp.given(split_date) else None
	if not day:
		frappe.throw(_("Pick the day the second part starts."))
	one_day = span[0] == span[1]
	if one_day:
		if day <= span[0]:
			frappe.throw(_("Pick a day after {0} for the second half.").format(span[0]))
		weights = (1, 1)
	else:
		if not span[0] < day <= span[1]:
			frappe.throw(
				_("Pick a day from {0} to {1}: the first part keeps the days before it.").format(
					span[0] + datetime.timedelta(days=1), span[1]
				)
			)
		weights = split_weights(span, day)

	crew = pp._current_crew(doc)
	total = flt(doc.get("expected_time"))
	if one_day and total <= 0:
		total = flt(engine.get_settings()["default_day_hours"]) * max(len(crew), 1)
	first_hours, second_hours = split_hours(total, *weights)
	first_crew, second_crew = split_crew(crew, *weights)
	was_slot = bool(engine.task_slot(state))

	# The second part, as a new Task's values.
	if one_day:
		values = pp._copy_values(doc, state, (day - span[0]).days)
	else:
		values = pp._copy_values(doc, state, 0)
		values["exp_start_date"] = pp.on_date(state.get("exp_start_date"), day)
		values["exp_end_date"] = state.get("exp_end_date")
		_clamp_slot_fields(values, day, span[1], was_slot)
	values["expected_time"] = second_hours
	note = _("Split from {0} on the Project Planner").format(frappe.utils.escape_html(doc.name))
	values["description"] = f"{doc.get('description') or ''}<p>{note}</p>"

	# The first part, edited in memory.
	before = (dict(state), [dict(member) for member in crew])
	if not one_day:
		pp._move(doc, None, str(day - datetime.timedelta(days=1)))
		fields = {f: doc.get(f) for f in ("custom_start_datetime", "custom_end_datetime")}
		_clamp_slot_fields(fields, span[0], day - datetime.timedelta(days=1), was_slot)
		for field, value in fields.items():
			doc.set(field, value)
	doc.expected_time = first_hours
	if any(flt(member.get("hours")) > 0 for member in crew):
		pp._set_crew(doc, first_crew, crew)
	first_state = pp._state(doc)
	second_preview = dict(
		state,
		name=hypothetical(doc.name, "split"),
		status="Open",
		expected_time=second_hours,
		**{field: values.get(field) for field in pp.DATE_FIELDS},
	)
	conflicts = pp.conflict_delta(
		engine.preview_batch([before]),
		engine.preview_batch([(first_state, first_crew), (second_preview, second_crew)]),
	)
	reason = (pp.given(reason) or "").strip()
	if conflicts and not reason:
		return {"needs_reason": True, "conflicts": conflicts}

	point = "planner_split"
	saved_point = pp._savepoint(point)
	try:
		warnings = pp._save(doc)
		new = frappe.get_doc(values)
		pp._set_crew(new, second_crew, crew)
		_copy_credentials(new, doc)
		if doc.get("custom_equipment"):
			pp._set_equipment(new, pp.equipment_payload(doc.get("custom_equipment")))
		new.append("depends_on", {"task": doc.name})
		new.check_permission("create")
		warnings.extend(_insert(new))
		second_span = pp._span_text(pp._state(new))
		doc.add_comment(
			"Comment",
			_("Split on the Project Planner: {0} now does the work from {1}.").format(
				frappe.utils.escape_html(new.name), second_span[0]
			),
		)
		new.add_comment("Comment", note + ".")
		if conflicts:
			_comment_reason(
				new, _("Split on the Project Planner over a conflict: {0}. Reason: {1}"), conflicts, reason
			)
	except Exception:
		if saved_point:
			pp._rollback_to(point)
		raise
	first_span = pp._span_text(pp._state(doc))
	return {
		"name": doc.name,
		"modified": str(doc.modified),
		"card": pp._card(doc),
		"first": {"start": first_span[0], "end": first_span[1], "expected_time": first_hours},
		"second": {
			"name": new.name,
			"modified": str(new.modified),
			"start": second_span[0],
			"end": second_span[1],
			"expected_time": second_hours,
			"card": pp._card(new),
		},
		"warnings": warnings,
		"conflicts": conflicts,
	}


# ---------------------------------------------------------------------- quick add


@frappe.whitelist(methods=["POST"])
def quick_add_task(project, subject, date, hours=None, resource=None, tentative=0, reason=None, draft=0):
	"""A new Task from a double-click on a person's day (or a day): ``subject`` on ``project`` on
	``date``, for ``resource`` when one is given (the planner named them; nobody is picked here).

	``hours`` is the estimate (blank: none, which books the person a full day); more hours than a day
	holds spread over the next weekdays, as a drop from the Unscheduled tray does
	(``crew_availability.span_for_estimate``). ``tentative`` makes it a pencil. Created as the caller
	(``check_permission("create")``), on a customer job only. A task that would overbook the person
	answers ``{"needs_reason": True, "conflicts"}`` and creates nothing. Refused in draft mode.
	Returns ``{"name", "modified", "created": True, "card", "warnings", "conflicts"}``.
	"""
	pp._require_planner()
	_refuse_draft(draft)
	_require_create()
	subject = " ".join(str(pp.given(subject) or "").split())
	if not subject:
		frappe.throw(_("Give the task a subject."))
	if len(subject) > SUBJECT_MAX:
		frappe.throw(_("Keep the subject to {0} characters.").format(SUBJECT_MAX))
	project = pp.given(project)
	if not project or not frappe.db.exists("Project", project):
		frappe.throw(_("Pick the project the task is for."))
	_require_customer_job(project)
	day = getdate(date) if pp.given(date) else None
	if not day:
		frappe.throw(_("Pick the day."))
	estimate = flt(pp.given(hours)) if pp.given(hours) is not None else 0.0
	if estimate < 0 or estimate > QUICK_ADD_MAX_HOURS:
		frappe.throw(_("Hours must be between 0 and {0}.").format(QUICK_ADD_MAX_HOURS))
	resource = pp.given(resource)
	crew = [{"resource": resource, "hours": None, "is_lead": 0}] if resource else []
	first, last = engine.span_for_estimate(day, estimate, 1, engine.get_settings()["default_day_hours"])
	pencil = 1 if pp.as_bool(pp.given(tentative) or 0) else 0
	values = {
		"doctype": "Task",
		"subject": subject,
		"project": project,
		"status": "Open",
		"exp_start_date": str(first),
		"exp_end_date": str(last),
		"expected_time": estimate,
		"custom_tentative": pencil,
	}
	new = frappe.get_doc(values)
	pp._set_crew(new, crew, [])  # an unknown or inactive person is refused here

	preview = dict(
		values,
		name=hypothetical(subject, "new"),
		custom_start_datetime=None,
		custom_end_datetime=None,
		custom_rental_booking=None,
		custom_rental_task_kind=None,
		custom_crew_size=0,
		equipment=[],
	)
	conflicts = engine.preview_batch([(preview, crew)]) if crew else {}
	reason = (pp.given(reason) or "").strip()
	if conflicts and not reason:
		return {"needs_reason": True, "conflicts": conflicts}

	new.check_permission("create")
	warnings = _insert(new)
	if conflicts:
		_comment_reason(
			new, _("Added on the Project Planner over a conflict: {0}. Reason: {1}"), conflicts, reason
		)
	return _created(new, warnings, conflicts)


# ---------------------------------------------------------------------- move many


def _parse_moves(moves):
	"""The ``moves`` argument as ``[{task, modified, start, end, tentative}]``, checked."""
	rows = pp.parse_list(pp.given(moves)) or []
	if not rows:
		frappe.throw(_("Pick at least one task to move."))
	if len(rows) > pp.BATCH_MAX:
		frappe.throw(_("Move {0} tasks or fewer at once.").format(pp.BATCH_MAX))
	out, seen = [], set()
	for row in rows:
		if not isinstance(row, dict) or not pp.given(row.get("task")):
			frappe.throw(_("Each move names a task."))
		task = row["task"]
		if task in seen:
			frappe.throw(_("{0} is in the list twice.").format(task))
		seen.add(task)
		tentative = pp.given(row.get("tentative"))
		entry = {
			"task": task,
			"modified": row.get("modified"),
			"start": pp.given(row.get("start")),
			"end": pp.given(row.get("end")),
			"tentative": None if tentative is None else pp.as_bool(tentative),
		}
		if not (entry["start"] or entry["end"]) and entry["tentative"] is None:
			frappe.throw(_("The move of {0} changes nothing.").format(task))
		out.append(entry)
	return out


def _move_report(doc, old, new):
	return {
		"task": doc.name,
		"subject": doc.get("subject") or doc.name,
		"from": old[0],
		"to": new[0],
		"from_end": old[1],
		"to_end": new[1],
		"tentative": engine.is_tentative(doc),
	}


def _draft_many(entries):
	"""``move_many`` in draft mode: each move into the caller's Draft row of that task (Phase 3B),
	never the Task. A stale task or a rental one fails the whole request, which rolls back."""
	moved, warnings, conflicts = [], [], {}
	for entry in entries:
		context = pp._draft_context(entry["task"], entry["modified"])
		doc = context["doc"]
		if entry["start"] or entry["end"]:
			_refuse_rental(doc, _("moved"))
		old = pp._span_text(pp._state(doc))
		result = pp._store_draft(
			context, start=entry["start"], end=entry["end"], tentative=entry["tentative"]
		)
		report = _move_report(doc, old, pp._span_text(pp._state(doc)))
		report["modified"] = result.get("modified")
		moved.append(report)
		warnings.extend(result.get("warnings") or [])
		for label, lines in (result.get("conflicts") or {}).items():
			known = conflicts.setdefault(label, [])
			for line in lines:
				if line not in known:
					known.append(line)
	return {"drafted": True, "moved": moved, "warnings": warnings, "conflicts": conflicts}


@frappe.whitelist(methods=["POST"])
def move_many(moves, reason=None, draft=0):
	"""Move several tasks in one go: the multi-select drag, and the selection bar's Move and Pencil.

	``moves`` is a JSON list of ``{"task", "modified", "start", "end", "tentative"}``: ``start`` and
	``end`` as ``save_task`` takes them (times of day and same-day slots kept), ``tentative`` 1 or 0 to
	pencil or firm up, either or both. At most 100 tasks; each one once.

	All or nothing: every task is loaded and permission-checked (``write``, ``modified``) before any
	is changed, and the moves are checked **together** (``crew_availability.preview_batch``, as
	``shift_successors`` and ``copy_week`` do), against the same tasks as they are now: only conflicts
	the batch creates count. With any and no ``reason``, nothing is saved: ``{"needs_reason": True,
	"conflicts", "moves"}``. Otherwise every task is saved through ``doc.save()``, furthest first when
	moving later (so ERPNext's own ``reschedule_dependent_tasks`` finds nothing to push), with the
	reason on each timeline; a save that fails fails the request, which Frappe rolls back. A rental
	crew task's dates are refused. ``draft=1``: each move goes into the caller's drafts instead.
	Returns ``{"moved": [{task, subject, from, to, from_end, to_end, tentative, modified}],
	"warnings", "conflicts"}``.
	"""
	pp._require_planner()
	entries = _parse_moves(moves)
	if cint(pp.given(draft)):
		return _draft_many(entries)

	docs = []
	for entry in entries:
		doc = pp._load(entry["task"], entry["modified"])
		if entry["start"] or entry["end"]:
			_refuse_rental(doc, _("moved"))
		docs.append(doc)

	before, after, reports = [], [], []
	for doc, entry in zip(docs, entries, strict=True):
		state, crew = pp._state(doc), pp._current_crew(doc)
		before.append((state, crew))
		old = pp._span_text(state)
		if entry["start"] or entry["end"]:
			pp._move(doc, entry["start"], entry["end"])
		if entry["tentative"] is not None:
			doc.custom_tentative = 1 if entry["tentative"] else 0
		new_state = pp._state(doc)
		after.append((new_state, crew))
		reports.append(_move_report(doc, old, pp._span_text(new_state)))

	conflicts = pp.conflict_delta(engine.preview_batch(before), engine.preview_batch(after))
	reason = (pp.given(reason) or "").strip()
	if conflicts and not reason:
		return {"needs_reason": True, "conflicts": conflicts, "moves": reports}

	by_name = {report["task"]: report for report in reports}

	def shift(report):
		old, new = engine._as_date(report["from"]), engine._as_date(report["to"])
		return (new - old).days if old and new else 0

	later = sum(shift(report) for report in reports) > 0
	order = sorted(docs, key=lambda d: by_name[d.name]["from"] or "", reverse=later)
	warnings, moved = [], []
	for doc in order:
		warnings.extend(pp._save(doc))
		if conflicts:
			_comment_reason(
				doc,
				_("Moved with other tasks on the Project Planner over a conflict: {0}. Reason: {1}"),
				conflicts,
				reason,
			)
		moved.append(dict(by_name[doc.name], modified=str(doc.modified)))
	return {"moved": moved, "warnings": warnings, "conflicts": conflicts}


# ---------------------------------------------------------------------- undo of a created task


def _removal_problem(doc, modified):
	"""Why the task just created cannot simply be deleted again, as a phrase, or None.

	Anything that has happened to it since is a reason to stop: it was changed, time was logged or
	clocked on it, it has sub-tasks, another task depends on it, someone else commented, a file is
	attached, or someone holds a draft change of it. Only its creator may undo it from here.
	"""
	user = frappe.session.user
	name = doc.name
	if doc.get("owner") and doc.get("owner") != user:
		return _("only the person who added it can undo it from the planner")
	if not pp.same_moment(doc.get("modified"), modified):
		return _("it was changed after it was added")
	if _has_timesheet(name):
		return _("time is logged against it on a timesheet")
	if _has_intervals(name):
		return _("someone has clocked time on it")
	if frappe.db.exists("Task", {"parent_task": name}):
		return _("it has sub-tasks")
	if frappe.db.exists("Task Depends On", {"parenttype": "Task", "task": name}):
		return _("another task depends on it")
	if frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Task",
			"reference_name": name,
			"comment_type": "Comment",
			"owner": ["!=", user],
		},
		pluck="name",
		limit_page_length=1,
	):
		return _("someone else has commented on it")
	if frappe.db.exists("File", {"attached_to_doctype": "Task", "attached_to_name": name}):
		return _("a file is attached to it")
	if _installed(DRAFT_DOCTYPE) and frappe.db.exists(DRAFT_DOCTYPE, {"task": name, "status": pp.DRAFT}):
		return _("someone has a draft change of it")
	return None


def _restore_plan(restore):
	"""The first half of a split to put back: ``{task, modified, start, end, expected_time, crew}``."""
	value = pp.given(restore)
	if value is None:
		return None
	if isinstance(value, str):
		value = json.loads(value)
	if not isinstance(value, dict) or not value.get("task"):
		frappe.throw(_("Nothing to put back."))
	return value


@frappe.whitelist(methods=["POST"])
def remove_created_task(task, modified, restore=None):
	"""Undo a task the planner just created (a duplicate, a quick add, a split's second half).

	Deletes ``task`` through ``frappe.delete_doc`` as the caller (delete permission; never ``force``,
	so a link to it still refuses, and it goes to Deleted Documents), and only when nothing has been
	attached since (:func:`_removal_problem`); otherwise it refuses with the reason and changes
	nothing. ``restore`` (JSON, for a split) puts the first half back through ``save_task``'s own
	path: ``{"task", "modified", "start", "end", "expected_time", "crew"}``, any conflict that brings
	back recorded with the Undo reason. Returns ``{"removed", "name", "modified", "card",
	"warnings"}`` (the last four for the task put back).
	"""
	pp._require_planner()
	doc = frappe.get_doc("Task", task)
	problem = _removal_problem(doc, modified)
	if problem:
		frappe.throw(
			_("{0} was not removed: {1}. Delete it on the task form if it should go.").format(
				doc.name, problem
			),
			title=_("Not undone"),
		)
	if not frappe.has_permission("Task", "delete", doc=doc):
		frappe.throw(
			_("You cannot delete Tasks, so {0} was not removed.").format(doc.name), frappe.PermissionError
		)
	plan = _restore_plan(restore)
	first = pp._load(plan["task"], plan.get("modified")) if plan else None

	frappe.delete_doc("Task", doc.name)
	result = {"removed": doc.name, "warnings": []}
	if first is not None:
		restored = pp._apply(
			first,
			start=pp.given(plan.get("start")),
			end=pp.given(plan.get("end")),
			expected_time=pp.given(plan.get("expected_time")),
			crew=pp.parse_list(pp.given(plan.get("crew"))),
			reason=_(UNDO_REASON),
		)
		first.add_comment(
			"Comment",
			_("The split into {0} was undone on the Project Planner.").format(
				frappe.utils.escape_html(doc.name)
			),
		)
		result.update(
			name=restored.get("name"),
			modified=restored.get("modified"),
			card=restored.get("card"),
			warnings=restored.get("warnings") or [],
		)
	return result


# ---------------------------------------------------------------------- search


def _mp():
	from erpnext_enhancements.api import maintenance_planner

	return maintenance_planner


def _search_people(like, need_user):
	rows = frappe.get_all(
		"Planner Resource",
		filters={"is_active": 1, "resource_name": ["like", like]},
		fields=["name", "resource_name", "resource_group", "user"],
		order_by="resource_name asc",
		limit_page_length=SEARCH_SCAN,
	)
	return [
		{
			"kind": "person",
			"resource": row.get("name"),
			"user": row.get("user"),
			"label": row.get("resource_name") or row.get("name"),
			"group": row.get("resource_group"),
		}
		for row in rows
		if not need_user or row.get("user")
	]


def _search_projects(like):
	type_expr, stream = engine.planner_job_sql("p")
	rows = frappe.db.sql(
		f"""
		SELECT p.name, p.project_name AS title, p.status,
			{type_expr} AS project_type, {stream} AS planner_stream
		FROM `tabProject` p
		WHERE (p.name LIKE %(like)s OR p.project_name LIKE %(like)s)
			AND IFNULL(p.status, '') NOT IN %(closed)s
			AND {engine.planner_job_condition("p")}
		ORDER BY (IFNULL(p.status, '') = %(active)s) DESC, p.modified DESC
		LIMIT %(scan)s
		""",
		{
			"like": like,
			"closed": engine.CLOSED_PROJECT_STATUSES,
			"active": pp.ACTIVE_PROJECT,
			"scan": SEARCH_SCAN,
			**engine.PLANNER_SQL_VALUES,
		},
		as_dict=True,
	)
	return [
		{
			"kind": "project",
			"name": row.get("name"),
			"label": row.get("title") or row.get("name"),
			"status": row.get("status"),
		}
		for row in rows or []
		if engine.is_planner_job({"project": row.get("name"), **row})
	]


def _search_tasks(like, anchor):
	cols = engine._task_columns()
	selected = ", ".join(f"{expr} AS `{column}`" for column, expr in cols.items())
	type_expr, stream = engine.planner_job_sql("p")
	job = engine.planner_job_condition("p", cols["custom_rental_booking"])
	rows = frappe.db.sql(
		f"""
		SELECT
			t.name, t.subject, t.project, t.status, t.exp_start_date, t.exp_end_date, {selected},
			p.project_name AS project_title, p.status AS project_status,
			{type_expr} AS project_type, {stream} AS planner_stream
		FROM `tabTask` t
		LEFT JOIN `tabProject` p ON p.name = t.project
		WHERE IFNULL(t.is_group, 0) = 0
			AND IFNULL(t.is_template, 0) = 0
			AND IFNULL(t.status, '') NOT IN %(finished)s
			AND (IFNULL(t.project, '') = '' OR IFNULL(p.status, '') NOT IN %(closed)s)
			AND {job}
			AND (t.subject LIKE %(like)s OR t.name LIKE %(like)s)
		ORDER BY (t.exp_start_date IS NULL), ABS(DATEDIFF(t.exp_start_date, %(anchor)s)), t.name
		LIMIT %(scan)s
		""",
		{
			"like": like,
			"anchor": str(anchor),
			"finished": engine.FINISHED_STATUSES,
			"closed": engine.CLOSED_PROJECT_STATUSES,
			"scan": SEARCH_SCAN,
			**engine.PLANNER_SQL_VALUES,
		},
		as_dict=True,
	)
	out = []
	for row in rows or []:
		if not engine._task_is_live(row):
			continue
		span = engine.task_span(row)
		out.append(
			{
				"kind": "task",
				"name": row.get("name"),
				"label": row.get("subject") or row.get("name"),
				"project": row.get("project"),
				"project_title": row.get("project_title") or row.get("project"),
				"status": row.get("status"),
				"start": str(span[0]) if span else None,
				"end": str(span[1]) if span else None,
				"rental": bool(row.get("custom_rental_booking")),
			}
		)
	return nearest_first(out, anchor, lambda row: row.get("start"))


def _visit_day(row):
	"""The day a visit is drawn on, as the Maintenance Planner draws it (``_records_between``)."""
	if cint(row.get("docstatus")) == 1 or row.get("workflow_state") == _mp().PENDING_STATE:
		return row.get("visit_date") or row.get("scheduled_visit_date")
	return row.get("scheduled_visit_date") or row.get("visit_date")


def _search_visits(like, anchor):
	mp = _mp()
	rows = frappe.db.sql(
		"""
		SELECT r.name, r.project, r.customer, r.visit_label, r.technician, r.docstatus,
			r.workflow_state, r.scheduled_visit_date, r.visit_date, p.project_name AS project_title
		FROM `tabSapphire Maintenance Record` r
		LEFT JOIN `tabProject` p ON p.name = r.project
		WHERE r.docstatus < 2
			AND (r.name LIKE %(like)s OR r.project LIKE %(like)s OR r.customer LIKE %(like)s
				OR p.project_name LIKE %(like)s)
		ORDER BY (COALESCE(r.scheduled_visit_date, r.visit_date) IS NULL),
			ABS(DATEDIFF(COALESCE(r.scheduled_visit_date, r.visit_date), %(anchor)s)), r.name
		LIMIT %(scan)s
		""",
		{"like": like, "anchor": str(anchor), "scan": SEARCH_SCAN},
		as_dict=True,
	)
	out = []
	for row in rows or []:
		if cint(row.get("docstatus")) == 1:
			status = "done"
		elif row.get("workflow_state") == mp.PENDING_STATE:
			status = "pending"
		else:
			status = "draft"
		day = engine._as_date(_visit_day(row))
		title = row.get("project_title") or row.get("project") or row.get("name")
		out.append(
			{
				"kind": "visit",
				"name": row.get("name"),
				"label": mp.short_site_name(title) or title,
				"project": row.get("project"),
				"visit_label": row.get("visit_label"),
				"date": str(day) if day else None,
				"status": status,
			}
		)
	return nearest_first(out, anchor, lambda row: row.get("date"))


def _search_sites(like):
	mp = _mp()
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT p.name, p.project_name AS title
		FROM `tabProject` p
		INNER JOIN `tabSapphire Maintenance Contract` c ON c.project = p.name
		WHERE (p.name LIKE %(like)s OR p.project_name LIKE %(like)s)
		ORDER BY p.project_name
		LIMIT %(scan)s
		""",
		{"like": like, "scan": SEARCH_SCAN},
		as_dict=True,
	)
	return [
		{
			"kind": "site",
			"project": row.get("name"),
			"label": mp.short_site_name(row.get("title") or row.get("name")) or row.get("name"),
		}
		for row in rows or []
	]


@frappe.whitelist()
def search_planner(q, start=None, planner="project"):
	"""The search box. ``q`` (2 to 80 characters) against the Project Planner's tasks (subject,
	name), projects (name, title) and people (name), customer jobs only and open work only, the tasks
	nearest ``start`` (default today) first. ``planner="maintenance"`` (the Maintenance Planner's
	roles) searches visits (record, site, customer), sites with a maintenance contract, and the people
	who have a user. At most 20 results: up to 5 people, up to 5 projects or sites, then the records.

	Returns ``{"q", "planner", "results": [...], "truncated"}``, each result ``{"kind": "person",
	resource, user, label, group}``, ``{"kind": "project", name, label, status}``, ``{"kind": "task",
	name, label, project, project_title, status, start, end, rental}``, ``{"kind": "site", project,
	label}`` or ``{"kind": "visit", name, label, project, visit_label, date, status}``. Read-only and
	Google-free; the page searches what it has loaded first and asks this only for the rest.
	"""
	planner = str(pp.given(planner) or "project").strip().lower()
	if planner not in PLANNERS:
		frappe.throw(_("Unknown planner {0}.").format(frappe.utils.escape_html(planner)))
	if planner == "maintenance":
		_mp()._require_planner()
	else:
		pp._require_planner()
	text = " ".join(str(pp.given(q) or "").split())[:SEARCH_MAX_CHARS]
	anchor = getdate(start) if pp.given(start) else getdate(nowdate())
	answer = {"q": text, "planner": planner, "results": [], "truncated": False}
	if len(text) < SEARCH_MIN_CHARS:
		return answer
	like = like_pattern(text)
	if planner == "maintenance":
		people = _search_people(like, need_user=True)
		places, records = _search_sites(like), _search_visits(like, anchor)
	else:
		people = _search_people(like, need_user=False)
		places, records = _search_projects(like), _search_tasks(like, anchor)
	answer["results"], answer["truncated"] = merge_results(people, places, records)
	return answer
