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
  picked (the person's next free day). All or nothing, inside one savepoint. Phase 6E: the open tasks
  of the same project that waited on the original now wait on the second part, the half that ends
  where the work ends (:func:`_repoint_dependents`); both parts keep the same name.
* :func:`quick_add_task` creates a task for one person on one day from a double-click; since
  v1.602.0 it also takes a phase, dependencies, a time slot, several crew, an address, the outdoor
  and customer-visit flags, a description and a crew size.
* :func:`build_out_tasks` creates MANY undated tasks on one project in one request (the Build out
  grid: subject, hours, phase, depends-on, crew size), new phases (group tasks) first; they land in
  the Unscheduled tray. :func:`remove_built_out_tasks` is its Undo, and
  :func:`get_build_out_context` / :func:`get_projects_needing_tasks` are the reads behind the grid
  and the Needs tasks list. :func:`get_copy_source` (v1.603.0) reads another project's leaf tasks as
  grid rows, the starting plan of "Copy from project...". "Start from template..." (T3, v1.608.0)
  reads its rows from ``api/planner_templates.py`` and sends each new phase's procurement checklist
  as ``phase_checklists``.
* :func:`move_many` moves several tasks (and/or pencils or firms them) in one call: one conflict
  check for the lot, one reason, all or nothing, as ``shift_successors`` and ``copy_week`` do.
* :func:`remove_created_task` is the Undo of a task the planner just created (a duplicate, a quick
  add, the second half of a split): it deletes it only when nothing has been attached since, and
  never with ``force``. For a split it also puts the first half back, and the tasks the split had
  moved onto the second half go back onto the first (Phase 6E).
* :func:`add_dependency` / :func:`remove_dependency` (P6, the Gantt tab's link-by-drag) make one task
  wait on another, or stop: through ``_load``'s checks, with the "starts before" line asking for a
  reason as a save does, and with ERPNext's calendar-day push held back, because no date changed.
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
* **A new task can be a draft, except a split's (v1.603.0).** Draft mode (Phase 3B) holds a change
  back as a ``Planner Draft Change``. Quick add, duplicate and build out with ``draft=1`` make the
  same checks as the live call and then keep the task-to-be as a ``New task`` Draft row
  (``project_planner.store_new_draft``): no Task exists, so no assignment, digest, conflict-centre
  line or calendar event can come from it, until Publish creates it through the same code path
  (``project_planner.task_from_spec``). Its card is named ``draft-new:<row>``, and
  :func:`remove_created_task` / :func:`remove_built_out_tasks` discard such rows. Split stays refused
  in draft mode (it changes a task that exists and creates another at once), with a sentence that
  says so, and the page refuses before it asks. :func:`move_many` changes tasks that exist, so it
  drafts properly.
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
from erpnext_enhancements.project_enhancements import planner_templates as templates
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
	"Draft mode holds back changes to tasks that already exist, and a new task can be drafted; but a split "
	"changes a task and creates another at once, which cannot be a draft. Turn Draft mode off to split it."
)
INTERVAL_DOCTYPE = planner_tracking.INTERVAL_DOCTYPE
DRAFT_DOCTYPE = "Planner Draft Change"
#: Build-out (v1.602.0): most rows one grid may create, most new phases among them, and the cap on
#: a crew size typed into a cell (a typing slip of 400 for 4).
BUILD_OUT_MAX_ROWS = 100
BUILD_OUT_MAX_PHASES = 25
CREW_SIZE_MAX = 50
#: The longest note a quick add keeps on the task (Task.description is a Text Editor).
DESCRIPTION_MAX = 4000
CLOCK_FORMATS = ("%H:%M:%S", "%H:%M")
#: How many projects the Needs tasks list answers with at most.
NEEDS_TASKS_LIMIT = 200
#: Task statuses that are not a task for the Needs tasks list: canceled work is no work. (Finished
#: work is deliberately NOT here: a project whose tasks are all finished has had tasks.)
NOT_A_TASK = ("Canceled", "Cancelled", "Template")
#: The statuses of a canceled task (a copy from another project leaves them out).
CANCELED_STATUSES = ("Canceled", "Cancelled")
#: How many open tasks the build-out grid offers as things to depend on.
BUILD_OUT_CONTEXT_TASKS = 300
#: THE reading of a phase name's number ("Phase 300", "300 Rough-in", "phase 300 - design" -> 300), shared
#: with the templates (project_enhancements/planner_templates.py, pure): the build-out matches a
#: project's open phase by it. Decision 2026-10-12: a project's phases are "Phase 100" ... "Phase 700".
phase_number = templates.phase_number


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


def clean_text(value):
	"""``value`` with its whitespace collapsed ("" for nothing): a subject typed with a stray tab."""
	return " ".join(str(value if value is not None else "").split())


def as_names(value):
	"""A list argument of names as sent by ``frappe.call``: a JSON list, a list, or a comma list.

	Blank entries and repeats are dropped, order kept. ``None`` and ``""`` are an empty list.
	"""
	value = pp.given(value)
	if value is None:
		return []
	if isinstance(value, str):
		text = value.strip()
		try:
			value = json.loads(text) if text.startswith("[") else text.split(",")
		except ValueError:
			value = text.split(",")  # not JSON after all: read as a comma list; the names are then checked
	if not isinstance(value, list | tuple):
		value = [value]
	out = []
	for item in value:
		name = clean_text(item)
		if name and name not in out:
			out.append(name)
	return out


def parse_clock(value):
	"""A time of day typed as ``HH:MM`` or ``HH:MM:SS`` as a ``datetime.time``; None when blank.

	Raises ``ValueError`` for anything else (the Time field of a form sends ``HH:MM:SS``).
	"""
	text = clean_text(pp.given(value))
	if not text:
		return None
	for fmt in CLOCK_FORMATS:
		try:
			return datetime.datetime.strptime(text, fmt).time()
		except ValueError:
			continue
	raise ValueError(text)


def slot_for(day, start_time, end_time):
	"""``(start, end, hours)`` for a same-day time slot: ``start``/``end`` as ``YYYY-MM-DD HH:MM:SS``
	and the length in hours; None when neither time is given. Raises ``ValueError`` for a half-given
	slot, a time that is not a time, or an end that is not after the start."""
	first, last = parse_clock(start_time), parse_clock(end_time)
	if first is None and last is None:
		return None
	if first is None or last is None or last <= first:
		raise ValueError("slot")
	span = datetime.datetime.combine(day, last) - datetime.datetime.combine(day, first)
	return (
		f"{day} {first.strftime('%H:%M:%S')}",
		f"{day} {last.strftime('%H:%M:%S')}",
		round(span.total_seconds() / 3600, 2),
	)


def build_order(count, depends):
	"""``(order, cycle)``: row numbers ``0..count-1`` in an order where every row comes after the rows
	it depends on, otherwise the grid's own order; ``cycle`` is the loop of rows when there is one.

	``depends`` maps a row to the set of rows it waits on. A stable topological sort: the lowest row
	number that is ready goes first, so a grid whose rows only depend on rows above them comes back
	untouched. With a cycle ``order`` is None and ``cycle`` lists the rows going round (a row that
	merely waits on the loop is not in it).
	"""
	waiting = {row: set(depends.get(row) or ()) for row in range(count)}
	done, order = set(), []
	while len(order) < count:
		ready = [row for row in range(count) if row not in done and waiting[row] <= done]
		if not ready:
			remaining = [row for row in range(count) if row not in done]
			row, seen = remaining[0], []
			while row not in seen:
				seen.append(row)
				row = min(waiting[row] - done)
			return None, seen[seen.index(row) :]
		done.add(ready[0])
		order.append(ready[0])
	return order, []


def deletion_order(names, depends):
	"""``names`` reordered so a task comes before every task it depends on (the Undo of a build-out
	deletes the waiting task first; Frappe refuses to delete one that something still links to).

	``depends`` maps a task to the set of tasks it waits on. Anything left in a loop keeps its place.
	"""
	remaining = list(names)
	out = []
	while remaining:
		free = [
			name
			for name in remaining
			if not any(name in (depends.get(other) or ()) for other in remaining if other != name)
		]
		pick = free[0] if free else remaining[0]
		out.append(pick)
		remaining.remove(pick)
	return out


def needs_tasks_query(has_owner, limit=NEEDS_TASKS_LIMIT):
	"""``(sql, values)``: the Active customer-job projects with no task at all, in ONE statement.

	"No task at all" is no task that is a leaf (not a phase), not a template and not canceled
	(:data:`NOT_A_TASK`): a project whose phases exist but whose work does not is listed too. Projects
	whose tasks are all finished are deliberately NOT listed (Nik, 2026-10-12, "Only no-task
	projects"): on production that was 304 Active customer jobs against 17 with no task, mostly stale
	jobs nobody closed, and the 304 buried the 17.
	The customer-job rule is the planners' own (``crew_availability.planner_job_condition``), and
	the page's PM filter reads ``pm`` (the project owner's Employee name, NULL before that custom
	field exists). Newest project first. Everything bound; nothing client-supplied reaches it.
	"""
	type_expr, stream = engine.planner_job_sql("p")
	owner = "e.employee_name" if has_owner else "NULL"
	join = "LEFT JOIN `tabEmployee` e ON e.name = p.custom_project_owner" if has_owner else ""
	sql = f"""
		SELECT p.name, p.project_name AS title, {owner} AS pm, p.customer,
			p.expected_start_date, p.expected_end_date,
			{type_expr} AS project_type, {stream} AS planner_stream
		FROM `tabProject` p
		{join}
		WHERE p.status = %(active)s
			AND {engine.planner_job_condition("p")}
			AND NOT EXISTS (
				SELECT 1 FROM `tabTask` t
				WHERE t.project = p.name
					AND IFNULL(t.is_group, 0) = 0
					AND IFNULL(t.is_template, 0) = 0
					AND IFNULL(t.status, '') NOT IN %(not_a_task)s
			)
		ORDER BY p.creation DESC
		LIMIT %(limit)s
	"""
	values = {
		"active": pp.ACTIVE_PROJECT,
		"not_a_task": NOT_A_TASK,
		"limit": int(limit),
		**engine.PLANNER_SQL_VALUES,
	}
	return sql, values


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
	"""``doc.insert()`` as the caller; what it said through msgprint comes back as warnings. The one
	insert behind every new Task, which Publish (``project_planner._create_new_tasks``) shares."""
	return pp._insert_doc(doc)


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

	Refused for a rental crew task, a group or template task, a task that is not a customer job, and
	without create permission. A copy that would overbook someone answers ``{"needs_reason": True,
	"conflicts"}`` and creates nothing. Returns ``{"name", "modified", "created": True, "source",
	"card", "warnings", "conflicts"}``.

	``draft=1`` (v1.603.0): every check above is made, then the copy is kept as a ``New task`` Draft
	row instead of a Task (``project_planner.store_new_draft``): it returns ``{"drafted": True, "name":
	"draft-new:<row>", "source", "modified", "card", "warnings", "conflicts"}`` and the conflicts are
	only information, since drafting never asks for a reason.
	"""
	pp._require_planner()
	drafting = bool(cint(pp.given(draft)))
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
	if conflicts and not reason and not drafting:
		return {"needs_reason": True, "conflicts": conflicts}

	# The same spec builds the live copy and the drafted one (project_planner.task_from_spec).
	spec = {
		"op": "duplicate",
		"source": doc.name,
		"values": {key: value for key, value in values.items() if key != "doctype"},
		"crew": pp.crew_payload(crew),
		"keep_crew": [member.get("resource") for member in crew if member.get("resource")],
		"credentials": [
			row.get("credential_type")
			for row in (doc.get("custom_required_credentials") or [])
			if row.get("credential_type")
		],
		"equipment": pp.equipment_payload(doc.get("custom_equipment"))
		if doc.get("custom_equipment")
		else None,
		"depends_on": [],
	}
	new = pp.task_from_spec(spec)
	new.check_permission("create")
	if drafting:
		return dict(pp.store_new_draft(spec, new, conflicts), source=doc.name)
	warnings = _insert(new)
	if conflicts:
		_comment_reason(
			new, _("Duplicated on the Project Planner over a conflict: {0}. Reason: {1}"), conflicts, reason
		)
	return _created(new, warnings, conflicts, source=doc.name)


# ---------------------------------------------------------------------- dependents (Phase 6E)


def _dependency_rows(task):
	"""``[{"name", "parent"}]``: the ``Task Depends On`` rows that make another task wait on ``task``."""
	return frappe.get_all(
		"Task Depends On",
		filters={"parenttype": "Task", "task": task},
		fields=["name", "parent"],
		limit_page_length=0,
	)


def _open_same_project(names, project):
	"""Of ``names``, the tasks of ``project`` that are not finished and not templates."""
	if not names:
		return []
	rows = frappe.get_all(
		"Task",
		filters={"name": ["in", sorted(names)]},
		fields=["name", "project", "status", "is_template"],
		limit_page_length=0,
	)
	return sorted(
		row.get("name")
		for row in rows
		if row.get("project") == project
		and row.get("status") not in engine.FINISHED_STATUSES
		and row.get("status") != "Template"
		and not cint(row.get("is_template"))
	)


def _swap_dependency(dependents, old, new, new_subject, note):
	"""Make each of ``dependents`` wait on ``new`` instead of ``old``. Returns the names changed.

	**Written straight to the child table, not through ``Task.save()``**, on purpose: a save runs
	ERPNext's ``on_update`` -> ``reschedule_dependent_tasks``, which pushes the dependent's own
	Open successors by calendar days if any of them starts before it ends. Re-pointing a
	dependency must move no dates, so it touches only the ``task`` of the row and the parent's
	``depends_on_tasks`` text (ERPNext's ``update_depends_on`` rebuilds that same text on a save),
	then leaves a timeline note. Write permission is checked first, as the caller; a task the
	caller cannot write fails the request, and with it the whole split. Never a duplicate row: a
	task that already waits on ``new`` simply loses its row for ``old``.
	"""
	changed = []
	for name in dependents:
		if not frappe.has_permission("Task", "write", doc=name):
			frappe.throw(
				_("You cannot change {0}, which waits on {1}, so nothing was changed.").format(name, old),
				frappe.PermissionError,
			)
		rows = frappe.get_all(
			"Task Depends On",
			filters={"parenttype": "Task", "parent": name},
			fields=["name", "task"],
			order_by="idx asc",
			limit_page_length=0,
		)
		mine = [row for row in rows if row.get("task") == old]
		if not mine:
			continue
		keep_row = mine[0].get("name")
		if any(row.get("task") == new for row in rows):
			drop = [row.get("name") for row in mine]
			keep_row = None
		else:
			frappe.db.set_value(
				"Task Depends On", keep_row, {"task": new, "subject": new_subject}, update_modified=False
			)
			drop = [row.get("name") for row in mine[1:]]
		for row_name in drop:
			frappe.db.delete("Task Depends On", {"name": row_name})
		kept = []
		for row in rows:
			if row.get("name") in drop:
				continue
			task = new if row.get("name") == keep_row else row.get("task")
			if task and task not in kept:
				kept.append(task)
		frappe.db.set_value("Task", name, "depends_on_tasks", "".join(f"{task}," for task in kept))
		frappe.get_doc("Task", name).add_comment("Comment", note)
		changed.append(name)
	return changed


def _repoint_dependents(first, second):
	"""Phase 6E: after a split, every open task of the same project that waited on ``first`` waits
	on ``second`` (the part that ends where the original ended). ``second`` itself is left alone
	(it waits on ``first``). Returns the tasks moved. Part of the split's savepoint."""
	rows = [row for row in _dependency_rows(first.name) if row.get("parent") not in (first.name, second.name)]
	names = _open_same_project({row.get("parent") for row in rows}, first.get("project"))
	return _swap_dependency(
		names,
		first.name,
		second.name,
		second.get("subject"),
		_("Now waits on {0} (second part of a split)").format(frappe.utils.escape_html(second.name)),
	)


def _restore_dependents(second, first, allowed):
	"""The Undo of :func:`_repoint_dependents`: the tasks the split moved (``allowed``, the names the
	page was told) that still wait on ``second`` wait on ``first`` again. Returns the names put
	back."""
	waiting = {row.get("parent") for row in _dependency_rows(second.name)}
	names = sorted(set(allowed or ()) & waiting)
	if not names:
		return []
	return _swap_dependency(
		names,
		second.name,
		first.name,
		first.get("subject"),
		_("Waits on {0} again (the split was undone)").format(frappe.utils.escape_html(first.name)),
	)


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
	Interval), a finished, group or template task, in draft mode (a split changes a task that exists
	and creates another at once, which no single draft row can hold: v1.603.0 drafts the other
	three ways to add a task, not this one), and without create permission;
	``modified`` is the optimistic lock, as for ``save_task``. A split that would overbook someone
	answers ``needs_reason`` and writes nothing. Returns ``{"name", "modified", "card", "first":
	{start, end, expected_time}, "second": {name, modified, start, end, expected_time, card},
	"repointed": [task names], "warnings", "conflicts"}``.

	**Phase 6E (Nik, 2026-10-09): both parts keep the same name, and the tasks that waited on the
	original wait on the second part.** Every open task of the same project whose ``depends_on``
	lists the original is pointed at the second part instead (:func:`_repoint_dependents`), inside
	the same savepoint, so a failure there rolls the whole split back. ``repointed`` names them; the
	page hands the list back to :func:`remove_created_task` so the Undo can put them back.
	"""
	pp._require_planner()
	_refuse_draft(draft)
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
		# Phase 6E: what waited on the whole task now waits on its last part.
		repointed = _repoint_dependents(doc, new)
		second_span = pp._span_text(pp._state(new))
		doc.add_comment(
			"Comment",
			_("Split on the Project Planner: {0} now does the work from {1}.").format(
				frappe.utils.escape_html(new.name), second_span[0]
			),
		)
		new.add_comment("Comment", note + ".")
		if repointed:
			new.add_comment(
				"Comment",
				_("Tasks that waited on {0} now wait on this one: {1}.").format(
					frappe.utils.escape_html(doc.name),
					", ".join(frappe.utils.escape_html(n) for n in repointed),
				),
			)
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
		"repointed": repointed,
		"warnings": warnings,
		"conflicts": conflicts,
	}


# ---------------------------------------------------------------------- quick add


def _project_task_rows(project, names=None, draft=False):
	"""``{name: row}`` of the project's Tasks (``name``, ``subject``, ``status``, ``is_group``,
	``is_template``, ``exp_end_date``), or just ``names`` of them, in ONE read. A name that is not on
	this project is simply absent, which is how every caller tells "not here" from "here".

	``draft`` (v1.603.0, a drafting build-out) adds the caller's own drafted new tasks of the project
	under their ``draft-new:`` names, so a second grid can file a task under a phase, or make it wait
	on a task, the first one drafted. Without it a ``draft-new:`` name is simply "not a task of"."""
	filters = {"project": project}
	if names is not None:
		filters["name"] = ["in", sorted(names)]
	rows = frappe.get_all(
		"Task",
		filters=filters,
		fields=["name", "subject", "status", "is_group", "is_template", "exp_end_date"],
		limit_page_length=0,
	)
	out = {row.get("name"): row for row in rows}
	if draft:
		for row in _drafted_task_rows(project):
			if names is None or row["name"] in names:
				out[row["name"]] = row
	return out


def _drafted_task_rows(project):
	"""The caller's drafted new tasks of ``project``, in the shape of :func:`_project_task_rows`."""
	rows, _duplicates = pp._draft_rows()
	out = []
	for row in rows:
		if not pp.is_new_row(row) or row.get("project") != project:
			continue
		values = pp.load_spec(row.get("payload"))["values"]
		out.append(
			{
				"name": pp.draft_ref(row.get("name")),
				"subject": values.get("subject") or row.get("subject"),
				"status": "Open",
				"is_group": cint(values.get("is_group")),
				"is_template": 0,
				"exp_end_date": values.get("exp_end_date"),
			}
		)
	return out


def _is_finished(row):
	return row.get("status") in engine.FINISHED_STATUSES or cint(row.get("is_template"))


def _where(number):
	"""``Row 3: `` for a message about a grid row; nothing for a quick add."""
	return _("Row {0}: ").format(number) if number else ""


def _check_phase(name, rows, project, number=None):
	"""``name`` as a phase to file a new task under: a group Task of ``project`` that is still open."""
	label = _where(number) + frappe.utils.escape_html(name)
	row = rows.get(name)
	if not row:
		frappe.throw(_("{0} is not a task of {1}.").format(label, project))
	if not cint(row.get("is_group")):
		frappe.throw(
			_("{0} is not a phase. A phase is a group task; pick one, or type a new phase name.").format(
				label
			)
		)
	if _is_finished(row):
		frappe.throw(_("{0} is finished, so nothing new can go under it.").format(label))
	if not pp.is_draft_ref(name) and not frappe.has_permission("Task", "read", doc=name):
		frappe.throw(_("You cannot read {0}.").format(label), frappe.PermissionError)
	return row


def _check_predecessor(name, rows, project, number=None):
	"""``name`` as something a new task waits on: a plain Task of ``project``, not a phase or template."""
	label = _where(number) + frappe.utils.escape_html(name)
	row = rows.get(name)
	if not row or cint(row.get("is_template")):
		frappe.throw(_("{0} is not a task of {1}.").format(label, project))
	if cint(row.get("is_group")):
		frappe.throw(_("{0} is a phase. A task can wait on another task, not on a phase.").format(label))
	if not pp.is_draft_ref(name) and not frappe.has_permission("Task", "read", doc=name):
		frappe.throw(_("You cannot read {0}.").format(label), frappe.PermissionError)
	return row


def _quick_add_people(resource, crew):
	"""The names a quick add books, in order: ``resource`` (the cell's person) first, then ``crew``."""
	people = []
	for name in [pp.given(resource), *as_names(crew)]:
		name = clean_text(name)
		if name and name not in people:
			people.append(name)
	return people


def _flag(value):
	return 1 if pp.as_bool(pp.given(value) or 0) else 0


def _plain_note(text):
	"""A typed note as the HTML Task.description holds: escaped, line breaks kept."""
	escaped = frappe.utils.escape_html(text.strip())
	return "<p>" + escaped.replace("\r\n", "\n").replace("\n", "<br>") + "</p>"


@frappe.whitelist(methods=["POST"])
def quick_add_task(
	project,
	subject,
	date,
	hours=None,
	resource=None,
	tentative=0,
	reason=None,
	draft=0,
	parent_task=None,
	depends_on=None,
	start_time=None,
	end_time=None,
	crew=None,
	address=None,
	outdoor=0,
	customer_visit=0,
	description=None,
	crew_size=None,
):
	"""A new Task from a double-click on a person's day (or a day): ``subject`` on ``project`` on
	``date``, for ``resource`` when one is given (the planner named them; nobody is picked here).

	``hours`` is the estimate (blank: none, which books the person a full day); more hours than a day
	holds spread over the next weekdays, as a drop from the Unscheduled tray does
	(``crew_availability.span_for_estimate``). ``tentative`` makes it a pencil. Created as the caller
	(``check_permission("create")``), on a customer job only. A task that would overbook the person
	answers ``{"needs_reason": True, "conflicts"}`` and creates nothing.

	``draft=1`` (v1.603.0): every check is made, then the task is kept as a ``New task`` Draft row
	instead of a Task (``project_planner.store_new_draft``). It returns ``{"drafted": True, "name":
	"draft-new:<row>", "modified", "card", "warnings", "conflicts"}``; conflicts are information, and
	nothing asks for a reason until Publish.

	Every argument after ``draft`` is optional and new in v1.602.0 (the richer quick add); leave them
	all out and the task is exactly what it always was:

	* ``parent_task``: a phase (an open group Task of the same project) to file it under.
	* ``depends_on``: tasks of the same project it waits on (a JSON list or comma list). One that is
	  still running when this task starts is a "Dependencies" line in the needs_reason answer, as on
	  ``save_task``; a pencil needs no reason.
	* ``start_time`` and ``end_time`` (``HH:MM``): a time slot on ``date``, which becomes the task's
	  ``custom_start_datetime``/``custom_end_datetime`` and its one day (the slot decides the day,
	  ``crew_availability.task_span``). With no ``hours`` the estimate is the slot's length.
	* ``crew``: more people (a JSON list or comma list of Planner Resources) on top of ``resource``.
	  Nobody is picked: the first person named is the lead when there is more than one, and each has
	  an even share. Every one of them is conflict-checked together.
	* ``address`` (an Address), ``outdoor`` and ``customer_visit`` (the Phase 5 flags),
	  ``description`` (plain text, kept escaped) and ``crew_size``.

	Returns ``{"name", "modified", "created": True, "card", "warnings", "conflicts"}``.
	"""
	pp._require_planner()
	drafting = bool(cint(pp.given(draft)))
	_require_create()
	subject = clean_text(pp.given(subject))
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
	people = _quick_add_people(resource, crew)
	# The first person is the lead once there is a crew; a person alone is not "the lead of" anyone.
	crew = [
		{"resource": name, "hours": None, "is_lead": 1 if (len(people) > 1 and index == 0) else 0}
		for index, name in enumerate(people)
	]
	size = pp.given(crew_size)
	size = cint(size) if size is not None else 0
	if size < 0 or size > CREW_SIZE_MAX:
		frappe.throw(_("Crew size must be between 0 and {0}.").format(CREW_SIZE_MAX))

	try:
		slot = slot_for(day, start_time, end_time)
	except ValueError:
		frappe.throw(_("Give the time slot a start and an end, as HH:MM, with the end after the start."))
	if slot:
		first = last = day
		estimate = estimate or slot[2]
	else:
		first, last = engine.span_for_estimate(
			day, estimate, max(len(people), size, 1), engine.get_settings()["default_day_hours"]
		)
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
	if slot:
		values["custom_start_datetime"], values["custom_end_datetime"] = slot[0], slot[1]
	if size:
		values["custom_crew_size"] = size
	if _flag(outdoor):
		values["custom_outdoor"] = 1
	if _flag(customer_visit):
		values["custom_customer_visit"] = 1
	note = pp.given(description)
	if note is not None and str(note).strip():
		if len(str(note)) > DESCRIPTION_MAX:
			frappe.throw(_("Keep the description to {0} characters.").format(DESCRIPTION_MAX))
		values["description"] = _plain_note(str(note))
	place = clean_text(pp.given(address))
	if place:
		if not frappe.db.exists("Address", place):
			frappe.throw(_("{0} is not an Address.").format(frappe.utils.escape_html(place)))
		values["custom_locationaddress_of_task"] = place

	parent = clean_text(pp.given(parent_task))
	needs = as_names(depends_on)
	rows = _project_task_rows(project, ([parent] if parent else []) + needs) if (parent or needs) else {}
	if parent:
		phase = _check_phase(parent, rows, project)
		ends = engine._as_date(phase.get("exp_end_date"))
		if ends and last > ends:
			frappe.throw(
				_("{0} ends on {1}, so a task under it cannot run to {2}. Pick an earlier day.").format(
					frappe.utils.escape_html(parent), ends, last
				)
			)
		values["parent_task"] = parent
	for name in needs:
		_check_predecessor(name, rows, project)

	spec = {
		"op": "quick_add",
		"values": {key: value for key, value in values.items() if key in pp.SPEC_VALUE_KEYS},
		"crew": crew,
		"depends_on": [{"task": name, "subject": rows[name].get("subject")} for name in needs],
	}
	new = pp.task_from_spec(spec)  # an unknown or inactive person is refused here

	preview = dict(
		values,
		name=hypothetical(subject, "new"),
		custom_start_datetime=values.get("custom_start_datetime"),
		custom_end_datetime=values.get("custom_end_datetime"),
		custom_rental_booking=None,
		custom_rental_task_kind=None,
		custom_crew_size=size,
		equipment=[],
	)
	conflicts = engine.preview_batch([(preview, crew)]) if crew else {}
	if needs and not pencil:
		waiting = pp.dependency_conflicts(preview, list(pp._task_rows(needs).values()))
		if waiting:
			conflicts = dict(conflicts, **{pp.DEPENDENCY_KEY: waiting})
	if drafting:
		new.check_permission("create")
		return pp.store_new_draft(spec, new, conflicts)
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


# ---------------------------------------------------------------------- build out (v1.602.0)

# Nik, 2026-10-12: "I definitely want to be able to easily create tasks for projects in the project
# planner screen so I don't have to leave it to build out tasks for a project." The Build out grid
# types many tasks at once; they are created UNDATED, so they land in the Unscheduled tray, ready to
# drag onto days. Nothing here picks a person or a day for anyone.


def _build_rows(rows):
	"""The grid's rows, each checked on its own: ``[{"number", "subject", "hours", "parent",
	"phase", "crew_size", "needs"}]``. Numbers are the 1-based places in the list sent (the blank
	rows are the page's to drop, so ``row:3`` means the third row of what it sent)."""
	try:
		items = pp.parse_list(pp.given(rows)) or []
	except ValueError:
		frappe.throw(_("The rows could not be read. Reload the planner and try again."))
	if not items:
		frappe.throw(_("Add at least one task to build out."))
	if len(items) > BUILD_OUT_MAX_ROWS:
		frappe.throw(_("Build out {0} tasks or fewer at once.").format(BUILD_OUT_MAX_ROWS))
	out = []
	for number, item in enumerate(items, start=1):
		if not isinstance(item, dict):
			frappe.throw(_("Row {0}: expected a task.").format(number))
		subject = clean_text(pp.given(item.get("subject")))
		if not subject:
			frappe.throw(_("Row {0}: give the task a subject.").format(number))
		if len(subject) > SUBJECT_MAX:
			frappe.throw(_("Row {0}: keep the subject to {1} characters.").format(number, SUBJECT_MAX))
		hours = flt(pp.given(item.get("hours"))) if pp.given(item.get("hours")) is not None else 0.0
		if hours < 0 or hours > QUICK_ADD_MAX_HOURS:
			frappe.throw(_("Row {0}: hours must be between 0 and {1}.").format(number, QUICK_ADD_MAX_HOURS))
		size = pp.given(item.get("crew_size"))
		size = cint(size) if size is not None else 0
		if size < 0 or size > CREW_SIZE_MAX:
			frappe.throw(_("Row {0}: crew size must be between 0 and {1}.").format(number, CREW_SIZE_MAX))
		phase = clean_text(pp.given(item.get("phase")))
		if len(phase) > SUBJECT_MAX:
			frappe.throw(_("Row {0}: keep the phase name to {1} characters.").format(number, SUBJECT_MAX))
		out.append(
			{
				"number": number,
				"subject": subject,
				"hours": hours,
				"parent": clean_text(pp.given(item.get("parent_task"))),
				"phase": phase,
				"crew_size": size,
				"needs": as_names(item.get("depends_on")),
			}
		)
	return out


def _plan_build_out(project, items, rows):
	"""Resolve the phases and dependencies of ``items`` against the project's tasks ``rows``.

	Returns ``{"phases": [title, ...], "phase_keys": [...], "parents": {row_index: ("task", name) |
	("new", key)}, "waits": {row_index: {row_index, ...}}, "existing": {row_index: [task, ...]},
	"order": [...]}`` (``phases`` are the new phases' titles, ``phase_keys`` what ``("new", key)`` names).
	Refuses, naming the row, a phase that is not an open group of this project, a dependency that is
	not a task of it, a row waiting on itself or on a row that is not there, and a loop.

	A typed phase name that is the name or the subject (any capitals) of an open phase of the project
	is that phase, so typing "Pumps" twice never makes two; the same new name on several rows is ONE
	new phase, spelt the way its first row spelt it.

	T3 (v1.608.0): a phase name that starts with a three-digit phase number ("Phase 300", "300 Rough-in",
	"phase 300 - design": :func:`phase_number`) is the project's OPEN phase with that number, whatever
	its spelling (its "Phase 300" first, when it has several); with none, ONE new phase named
	``Phase 300`` is made for every row that named 300 (decision 2026-10-12: projects' phases are
	``Phase 100`` ... ``Phase 700``). A template loaded onto a project that has its phases never makes a
	second set.
	"""
	open_phases = {
		name: row for name, row in rows.items() if cint(row.get("is_group")) and not _is_finished(row)
	}
	by_subject, by_number = {}, {}
	for name in sorted(open_phases):
		subject = clean_text(open_phases[name].get("subject"))
		by_subject.setdefault(subject.casefold(), name)
		value = phase_number(subject)
		if value is None:
			continue
		# Several open phases with one number: the one named exactly "Phase 300" wins, else the first.
		canonical = subject.casefold() == templates.phase_name(value).casefold()
		if value not in by_number or (canonical and not by_number[value][1]):
			by_number[value] = (name, canonical)
	by_number = {value: name for value, (name, _canonical) in by_number.items()}
	phases, spelled, parents = [], {}, {}
	for index, item in enumerate(items):
		if item["parent"]:
			_check_phase(item["parent"], rows, project, item["number"])
			parents[index] = ("task", item["parent"])
		elif item["phase"]:
			value = phase_number(item["phase"])
			key = templates.phase_key(item["phase"])
			if item["phase"] in open_phases:
				parents[index] = ("task", item["phase"])
			elif item["phase"].casefold() in by_subject:
				parents[index] = ("task", by_subject[item["phase"].casefold()])
			elif value is not None and value in by_number:
				parents[index] = ("task", by_number[value])
			else:
				if key not in spelled:
					spelled[key] = templates.phase_name(value) if value is not None else item["phase"]
					phases.append(key)
				parents[index] = ("new", key)
	if len(phases) > BUILD_OUT_MAX_PHASES:
		frappe.throw(_("Make {0} new phases or fewer at once.").format(BUILD_OUT_MAX_PHASES))

	waits, existing = {}, {}
	for index, item in enumerate(items):
		for token in item["needs"]:
			if token.lower().startswith("row:"):
				try:
					other = int(token[4:])
				except ValueError:
					other = 0
				if not 1 <= other <= len(items):
					frappe.throw(
						_("Row {0}: it waits on row {1}, which is not in the grid.").format(
							item["number"], frappe.utils.escape_html(token[4:])
						)
					)
				if other == item["number"]:
					frappe.throw(_("Row {0}: a task cannot wait on itself.").format(item["number"]))
				waits.setdefault(index, set()).add(other - 1)
			else:
				_check_predecessor(token, rows, project, item["number"])
				existing.setdefault(index, []).append(token)
	order, cycle = build_order(len(items), waits)
	if cycle:
		frappe.throw(
			_("Rows {0} wait on each other, round and round. Take one of those dependencies out.").format(
				", ".join(str(index + 1) for index in cycle)
			),
			title=_("Circular dependency"),
		)
	return {
		"phases": [spelled[key] for key in phases],
		"phase_keys": phases,
		"parents": parents,
		"waits": waits,
		"existing": existing,
		"order": order,
	}


def _phase_checklists(value):
	"""The grid's ``phase_checklists`` (T3, v1.608.0) as ``{phase name casefolded: [items]}``.

	A JSON object of a phase name to its procurement checklist (a list of lines), as
	``planner_templates.get_template_rows`` handed the page. At most one per new phase the grid may
	make, items cleaned and capped (``planner_templates.checklist_lines``); anything else is refused.
	"""
	raw = pp.given(value)
	if raw is None:
		return {}
	try:
		data = json.loads(raw) if isinstance(raw, str) else raw
	except ValueError:
		data = None
	if not isinstance(data, dict) or len(data) > BUILD_OUT_MAX_PHASES:
		frappe.throw(_("The phase checklists could not be read. Reload the planner and try again."))
	out = {}
	for title, items in data.items():
		if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
			frappe.throw(_("The phase checklists could not be read. Reload the planner and try again."))
		lines = templates.checklist_lines("\n".join(items))
		key = templates.phase_key(title)
		if key and lines:
			out[key] = lines
	return out


def _insert_row(doc, label):
	"""``_insert`` with the row named when ERPNext refuses it. A permission error passes as it is."""
	log = pp._message_log()
	mark = len(log) if log is not None else 0
	try:
		return _insert(doc)
	except frappe.PermissionError:
		raise
	except Exception as exc:
		text = pp._failure_text(exc, pp._take_messages(log, mark))
		frappe.throw(
			_("{0} could not be created, so nothing was: {1}").format(
				frappe.utils.escape_html(label), frappe.utils.escape_html(text)
			),
			title=_("Build out stopped"),
		)


@frappe.whitelist(methods=["POST"])
def build_out_tasks(project, rows, draft=0, phase_checklists=None):
	"""Create many undated Tasks on one customer job, in ONE request: the Build out grid.

	``rows`` is a JSON list (at most 100) of ``{"subject", "hours", "parent_task", "phase",
	"depends_on", "crew_size"}``. ``subject`` is required; ``hours`` is the estimate (blank: none).

	* ``parent_task`` files the task under an existing phase (an open group Task of the project);
	  ``phase`` is a name instead: an existing phase of that name, else a NEW phase (a group Task),
	  made first and shared by every row that typed it. ``parent_task`` wins when both are sent.
	* ``depends_on`` lists what the task waits on: ``"row:3"`` for the third row of ``rows``, or the
	  name of an existing task of the project. A row waiting on itself, or a loop of rows, is refused
	  with the rows named (ERPNext would refuse it later, with only a "Circular Reference Error").

	Every row is checked before anything is written (a row's mistake is named by its number), then the
	phases are made, then the tasks in an order where each comes after what it waits on (the grid's
	own order when it already is). Created as the caller (``check_permission("create")``), on a
	customer job only, and all or nothing: a task ERPNext refuses fails the request, which Frappe
	rolls back. No person and no day is picked: the tasks are undated and crewless, so they wait in
	the Unscheduled tray. Returns ``{"project", "count", "phases": [{name, subject, modified}],
	"tasks": [{name, row, subject, modified, parent_task, depends_on}], "warnings"}`` (``phases`` only
	the new ones, the tasks in the grid's order). ``remove_built_out_tasks`` undoes it with those
	names and stamps.

	``draft=1`` (v1.603.0): the same checks, row by row with the rows numbered, then every phase and
	task is kept as a ``New task`` Draft row instead of a Task (``project_planner.insert_new_draft``),
	in the same order, so a row that waits on another waits on its ``draft-new:<row>`` name, and a
	drafted task is filed under a drafted phase the same way. The answer has the same shape with
	``draft-new:`` names and ``"drafted": True``; nothing is a Task until Publish. A grid may also
	name, as a phase or as something to wait on, a task an earlier drafted build-out made
	(``get_build_out_context(draft=1)`` offers them).

	T3 (v1.608.0): a ``phase`` starting with a phase number ("Phase 300", "300 Rough-in") is the
	project's open phase with that number, else a new ``Phase 300`` (:func:`_plan_build_out`).

	``phase_checklists`` (T3, v1.608.0): a JSON object of a phase name to its procurement checklist
	(lines), from "Start from template...". A NEW phase of that name (any capitals, or the same phase
	number) gets it as a tick
	list in its description (``planner_templates.checklist_html``, every item escaped); an existing
	phase is never written to, and a name no row uses is ignored.
	"""
	pp._require_planner()
	drafting = bool(cint(pp.given(draft)))
	_require_create()
	project = pp.given(project)
	if not project or not frappe.db.exists("Project", project):
		frappe.throw(_("Pick the project to build out."))
	_require_customer_job(project)
	items = _build_rows(rows)
	checklists = _phase_checklists(phase_checklists)
	known = _project_task_rows(project, draft=drafting)
	plan = _plan_build_out(project, items, known)
	if drafting:
		pp.require_draft_room(len(items) + len(plan["phases"]))

	# Each phase and task is a spec, built into a document by the one function that builds a Task
	# (project_planner.task_from_spec) for the live insert and for a drafted row alike.
	warnings, phase_names, made_phases = [], {}, []
	for key, title in zip(plan["phase_keys"], plan["phases"], strict=True):
		spec = {"op": "build_out", "values": {"subject": title, "project": project, "is_group": 1}}
		if checklists.get(key):
			spec["values"]["description"] = templates.checklist_html(checklists[key])
		doc = pp.task_from_spec(spec)
		doc.check_permission("create")
		if drafting:
			doc = pp.insert_new_draft(spec)
			phase_names[key] = pp.draft_ref(doc.get("name"))
		else:
			warnings.extend(_insert_row(doc, _("Phase {0}").format(title)))
			phase_names[key] = doc.name
		made_phases.append(doc)

	names = {}
	made = {}
	specs = {}
	for index in plan["order"]:
		item = items[index]
		values = {
			"subject": item["subject"],
			"project": project,
			"expected_time": item["hours"],
		}
		if item["crew_size"]:
			values["custom_crew_size"] = item["crew_size"]
		kind, ref = plan["parents"].get(index, (None, None))
		if kind == "task":
			values["parent_task"] = ref
		elif kind == "new":
			values["parent_task"] = phase_names[ref]
		waiting = [
			{"task": names[other], "subject": items[other]["subject"]}
			for other in sorted(plan["waits"].get(index) or ())
		]
		waiting += [
			{"task": task, "subject": known[task].get("subject")}
			for task in plan["existing"].get(index) or ()
		]
		spec = {"op": "build_out", "row": item["number"], "values": values, "depends_on": waiting}
		doc = pp.task_from_spec(spec)
		doc.check_permission("create")
		if drafting:
			doc = pp.insert_new_draft(spec)
			names[index] = pp.draft_ref(doc.get("name"))
		else:
			warnings.extend(_insert_row(doc, _("Row {0} ({1})").format(item["number"], item["subject"])))
			names[index] = doc.name
		made[index] = doc
		specs[index] = spec

	# The phases were saved again as each child was added (ERPNext lists a child among its parent's
	# dependencies), so the stamp the Undo compares against is read last. A drafted phase is not a
	# Task: its stamp is its Draft row's own.
	phases = []
	for doc in made_phases:
		if drafting:
			phases.append(
				{
					"name": pp.draft_ref(doc.get("name")),
					"subject": doc.get("subject"),
					"modified": str(doc.get("modified")),
				}
			)
			continue
		stamp = frappe.db.get_value("Task", doc.name, "modified")
		phases.append(
			{"name": doc.name, "subject": doc.get("subject"), "modified": str(stamp or doc.modified)}
		)
	tasks = [
		{
			"name": names[index],
			"row": items[index]["number"],
			"subject": items[index]["subject"],
			"modified": str(made[index].modified),
			"parent_task": specs[index]["values"].get("parent_task") or None,
			"depends_on": [row.get("task") for row in specs[index]["depends_on"]],
		}
		for index in range(len(items))
	]
	answer = {"project": project, "count": len(tasks), "phases": phases, "tasks": tasks, "warnings": warnings}
	if drafting:
		answer["drafted"] = True
	return answer


@frappe.whitelist()
def get_build_out_context(project, draft=0):
	"""What the Build out grid offers for ``project``: ``{"project", "title", "phases": [{name,
	subject}], "tasks": [{name, subject, status}], "truncated", "can_create"}``.

	``phases`` are the project's open group Tasks (what a row can be filed under); ``tasks`` its open
	plain Tasks (what a row can wait on), at most 300, one read for both. A project that is not a
	customer job is refused, as everywhere on the planner. ``draft=1`` (v1.603.0) also lists the
	caller's drafted new phases and tasks of the project, under their ``draft-new:`` names and marked
	``"draft": True``, so a drafting grid can build on what an earlier one drafted.
	"""
	pp._require_planner()
	project = pp.given(project)
	if not project or not frappe.db.exists("Project", project):
		frappe.throw(_("Pick the project to build out."))
	_require_customer_job(project)
	rows = [
		row
		for row in _project_task_rows(project, draft=bool(cint(pp.given(draft)))).values()
		if not _is_finished(row)
	]
	phases = sorted(
		(
			dict(
				{"name": row.get("name"), "subject": row.get("subject") or row.get("name")},
				**({"draft": True} if pp.is_draft_ref(row.get("name")) else {}),
			)
			for row in rows
			if cint(row.get("is_group"))
		),
		key=lambda entry: (entry["subject"].lower(), entry["name"]),
	)
	tasks = sorted(
		(
			dict(
				{
					"name": row.get("name"),
					"subject": row.get("subject") or row.get("name"),
					"status": row.get("status"),
				},
				**({"draft": True} if pp.is_draft_ref(row.get("name")) else {}),
			)
			for row in rows
			if not cint(row.get("is_group"))
		),
		key=lambda entry: entry["name"],
	)
	return {
		"project": project,
		"title": frappe.db.get_value("Project", project, "project_name") or project,
		"phases": phases,
		"tasks": tasks[:BUILD_OUT_CONTEXT_TASKS],
		"truncated": len(tasks) > BUILD_OUT_CONTEXT_TASKS,
		"can_create": bool(frappe.has_permission("Task", "create")),
	}


def _copy_order(task):
	return (cint(task.get("lft")), cint(task.get("idx")), str(task.get("creation") or ""), task.get("name"))


def copy_plan(tasks, depends, cap=BUILD_OUT_MAX_ROWS):
	"""A project's tasks as the starting rows of a build-out grid: ``{"rows", "count", "total",
	"truncated", "dropped_dependencies", "skipped": {"templates", "canceled"}}``. Pure.

	``tasks`` are Task rows (``name``, ``subject``, ``status``, ``is_group``, ``is_template``,
	``parent_task``, ``expected_time``, ``custom_crew_size``, ``lft``, ``idx``, ``creation``) and
	``depends`` the ``Task Depends On`` rows (``parent`` waits on ``task``) of the same project.

	* Only the **leaf** tasks are rows; a group is a phase, and its subject is the ``phase`` of the
	  leaves directly under it. Templates and canceled tasks are skipped (and counted); finished tasks
	  are copied, since a finished job is the best template there is.
	* Order is the project's own: the phases in tree order, the tasks of each in tree order
	  (``lft``, then ``idx``, then creation), tasks under no phase first. At most ``cap`` rows.
	* ``needs`` are ``row:N`` (the N-th row returned, from 1) for each dependency whose task is among
	  the rows; a dependency on anything else (a task not copied, a phase) is dropped and counted.
	* Only subject, hours, phase and crew size are copied. Never dates, people, crew rows, assignments,
	  status, timesheets, notes, addresses or anything else of the customer's.
	"""
	groups = sorted((t for t in tasks if cint(t.get("is_group"))), key=_copy_order)
	rank = {group.get("name"): index for index, group in enumerate(groups)}
	subjects = {group.get("name"): clean_text(group.get("subject")) for group in groups}
	skipped = {"templates": 0, "canceled": 0}
	leaves = []
	for task in tasks:
		if cint(task.get("is_group")):
			continue
		if cint(task.get("is_template")) or task.get("status") == "Template":
			skipped["templates"] += 1
		elif task.get("status") in CANCELED_STATUSES:
			skipped["canceled"] += 1
		elif clean_text(task.get("subject")):
			leaves.append(task)
	leaves.sort(key=lambda task: (rank.get(task.get("parent_task"), -1), *_copy_order(task)))
	total = len(leaves)
	leaves = leaves[:cap]
	place = {task.get("name"): number for number, task in enumerate(leaves, start=1)}
	waits, dropped = {}, set()
	for row in depends or []:
		child, needed = row.get("parent"), row.get("task")
		if child not in place or not needed:
			continue
		if needed in place and needed != child:
			waits.setdefault(child, set()).add(place[needed])
		else:
			dropped.add((child, needed))
	rows = [
		{
			"subject": clean_text(task.get("subject"))[:SUBJECT_MAX],
			"hours": round(flt(task.get("expected_time")), 2),
			"phase": subjects.get(task.get("parent_task"), "")[:SUBJECT_MAX],
			"crew_size": cint(task.get("custom_crew_size")),
			"needs": [f"row:{number}" for number in sorted(waits.get(task.get("name"), ()))],
		}
		for task in leaves
	]
	return {
		"rows": rows,
		"count": len(rows),
		"total": total,
		"truncated": total > len(rows),
		"dropped_dependencies": len(dropped),
		"skipped": skipped,
	}


def _copy_note(plan):
	"""The sentence under a loaded copy: what came across and what did not."""
	parts = [
		_("{0} tasks copied. Dates, people, notes and addresses are never copied.").format(plan["count"])
	]
	if plan["dropped_dependencies"]:
		parts.append(
			_("{0} dependencies on tasks that were not copied were left out.").format(
				plan["dropped_dependencies"]
			)
		)
	skipped = plan["skipped"]
	if skipped["templates"] or skipped["canceled"]:
		parts.append(
			_("Left out: {0} template and {1} canceled tasks.").format(
				skipped["templates"], skipped["canceled"]
			)
		)
	if plan["truncated"]:
		parts.append(_("Only the first {0} of {1} tasks were copied.").format(plan["count"], plan["total"]))
	return " ".join(parts)


@frappe.whitelist()
def get_copy_source(project):
	"""Another project's tasks as the starting rows of the Build out grid ("Copy from project...").

	``project`` is any Project the caller can read, not only a customer job: it is a source of a
	plan, nothing is written to it. Returns :func:`copy_plan`'s answer plus ``"project"``, ``"title"``
	and a ``"note"`` sentence; each row is ``{"subject", "hours", "phase", "crew_size", "needs":
	["row:N", ...]}``, ready for the grid, which creates them on ITS project (and, in Draft mode, as
	drafts). Read-only, and Task read permission applies (``get_list``): a task the caller cannot read
	is not copied.
	"""
	pp._require_planner()
	project = clean_text(pp.given(project))
	if not project or not frappe.db.exists("Project", project):
		frappe.throw(_("Pick the project to copy from."))
	if not frappe.has_permission("Project", "read", doc=project):
		frappe.throw(
			_("You cannot read {0}.").format(frappe.utils.escape_html(project)), frappe.PermissionError
		)
	fields = [
		"name",
		"subject",
		"status",
		"is_group",
		"is_template",
		"parent_task",
		"expected_time",
		"lft",
		"idx",
		"creation",
	]
	if pp._task_has_column("custom_crew_size"):
		fields.append("custom_crew_size")
	tasks = frappe.get_list("Task", filters={"project": project}, fields=fields, limit_page_length=0)
	names = [t.get("name") for t in tasks if not cint(t.get("is_group"))]
	depends = (
		frappe.get_all(
			"Task Depends On",
			filters={"parenttype": "Task", "parent": ["in", names]},
			fields=["parent", "task"],
			limit_page_length=0,
		)
		if names
		else []
	)
	plan = copy_plan(tasks, depends)
	plan.update(
		project=project,
		title=frappe.db.get_value("Project", project, "project_name") or project,
		note=_copy_note(plan),
	)
	return plan


@frappe.whitelist()
def get_projects_needing_tasks():
	"""The Needs tasks list: Active customer jobs with no task at all, newest first.

	``{"projects": [{name, title, pm, expected_start, expected_end}], "limit", "can_create"}``.
	ONE query for all of them (:func:`needs_tasks_query`), however many projects there are; the page
	applies its own project and PM filters. A project whose tasks are all finished is not listed.
	"""
	pp._require_planner()
	try:
		has_owner = bool(frappe.db.has_column("Project", "custom_project_owner"))
	except Exception:
		has_owner = False
	query, values = needs_tasks_query(has_owner)
	rows = frappe.db.sql(query, values, as_dict=True)
	projects = [
		{
			"name": row.get("name"),
			"title": row.get("title") or row.get("name"),
			"pm": row.get("pm"),
			"expected_start": str(row["expected_start_date"]) if row.get("expected_start_date") else None,
			"expected_end": str(row["expected_end_date"]) if row.get("expected_end_date") else None,
		}
		for row in rows or []
		if engine.is_planner_job({"project": row.get("name"), **row})
	]
	return {
		"projects": projects,
		"limit": NEEDS_TASKS_LIMIT,
		"can_create": bool(frappe.has_permission("Task", "create")),
	}


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


def _draft_many(entries, follow=False, auto=False, exclude=()):
	"""``move_many`` in draft mode: each move into the caller's Draft row of that task (Phase 3B),
	never the Task. A stale task or a rental one fails the whole request, which rolls back.

	``follow`` (P4): the tasks that follow each moved one are drafted along by the working days its
	end moved (``pp._draft_followers``), each once, marked ``"dependent": True``. ``auto``: a
	follower in a published project is left alone (``shift_skipped``). ``exclude``: tasks the same
	request moves another way. What the lot creates together comes back as warnings
	(``pp._draft_checks``), and when two moved tasks reach the same follower, the one nearer it, the
	downstream one, carries it (``pp._downstream_first``)."""
	moved, warnings, conflicts, skipped = [], [], {}, []
	taken = {entry["task"] for entry in entries} | set(exclude or ())
	roots, holidays = {}, pp._shift_holidays()
	for entry in entries:
		context = pp._draft_context(entry["task"], entry["modified"])
		doc = context["doc"]
		if entry["start"] or entry["end"]:
			_refuse_rental(doc, _("moved"))
		before_state = pp._state(doc)
		old = pp._span_text(before_state)
		result = pp._store_draft(
			context, start=entry["start"], end=entry["end"], tentative=entry["tentative"]
		)
		new = pp._span_text(pp._state(doc))
		report = _move_report(doc, old, new)
		report["modified"] = result.get("modified")
		moved.append(report)
		warnings.extend(result.get("warnings") or [])
		for label, lines in (result.get("conflicts") or {}).items():
			known = conflicts.setdefault(label, [])
			for line in lines:
				if line not in known:
					known.append(line)
		roots[doc.name] = {
			"doc": doc,
			"stored": context["stored"],
			"before": before_state,
			"after": (pp._state(doc), pp._current_crew(doc)),
			"days": pp.dependent_days(old, new, holidays) if follow and old[0] and new[0] else 0,
		}
	answer = {"drafted": True, "moved": moved, "warnings": warnings, "conflicts": conflicts}
	if not follow:
		return answer
	followers = []
	for name in pp._downstream_first(list(roots)):
		if not roots[name]["days"]:
			continue
		shifted, left, found = pp._draft_followers(name, roots[name]["days"], auto=auto, exclude=taken)
		taken.update(move["task"] for move in shifted)
		moved.extend(dict(move, dependent=True, drafted=True) for move in shifted)
		skipped.extend(left)
		followers.extend(found)
	pp._merge_info(answer, pp._draft_checks(list(roots.values()), followers))
	answer["shift_skipped"] = skipped
	return answer


@frappe.whitelist(methods=["POST"])
def move_many(moves, reason=None, dependents=None, notify=None, draft=0):
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

	``notify`` (P3, the "Notify crew?" answer, as ``project_planner.save_task`` takes it): one notice
	per person whose bookings the moves changed (``"notified"``); ``auto`` notifies when any move
	touches the next 14 days. Ignored with ``draft=1``.

	``draft="auto"`` (P4): each move is drafted when its task's project schedule is Draft and saved
	otherwise, in one request (the saves first, all or nothing; a drafted move is marked
	``"drafted": True``). ``dependents=1`` (P4, D10 and D15): the tasks that follow each moved task
	move with it by the working days its end moved, later or earlier, checked with the batch and
	marked ``"dependent": True`` in ``moved``; ``shift_skipped`` lists the ones left where they were.
	A task the request moves itself is never moved again as a follower.
	"""
	pp._require_planner()
	entries = _parse_moves(moves)
	follow = pp.as_bool(dependents) if pp.given(dependents) is not None else False
	choice = pp.draft_choice(draft)
	if choice is True:
		return _draft_many(entries, follow=follow)
	auto = choice == pp.DRAFT_AUTO
	held = [entry for entry in entries if auto and pp._drafting(draft, entry["task"])]
	live = [entry for entry in entries if entry not in held]
	if not live:
		return _draft_many(held, follow=follow, auto=True)
	result = _move_live(live, reason, notify, follow, auto, exclude={entry["task"] for entry in held})
	if result.get("needs_reason") or not held:
		return result
	taken = {move["task"] for move in result["moved"]}
	drafts = _draft_many(held, follow=follow, auto=True, exclude=taken)
	result["moved"].extend(dict(move, drafted=True) for move in drafts["moved"])
	result["warnings"].extend(drafts["warnings"])
	if follow:
		result["shift_skipped"] = result.get("shift_skipped", []) + drafts.get("shift_skipped", [])
	return result


def _move_live(entries, reason, notify, follow, auto, exclude=()):
	""":func:`move_many`'s saving half: the moves (and with ``follow`` the tasks that follow them)
	checked together and saved, or ``needs_reason``."""
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

	skipped = []
	if follow:
		taken = {doc.name for doc in docs} | set(exclude or ())
		holidays = pp._shift_holidays()
		by_task = {report["task"]: report for report in reports}
		# Downstream first: a follower two moved tasks reach takes the nearer one's shift.
		for name in pp._downstream_first([report["task"] for report in reports]):
			report = by_task[name]
			days = (
				pp.dependent_days(
					(report["from"], report["from_end"]), (report["to"], report["to_end"]), holidays
				)
				if report["from"] and report["to"]
				else 0
			)
			followers, left = pp._follower_docs(report["task"], days, exclude=taken, auto=auto)
			skipped.extend(left)
			if not followers:
				continue
			was, now, moves = pp._shift_docs(followers, days)
			taken.update(doc.name for doc in followers)
			docs.extend(followers)
			before.extend(was)
			after.extend(now)
			reports.extend(
				dict(move, tentative=engine.is_tentative(doc), dependent=True)
				for doc, move in zip(followers, moves, strict=True)
			)

	conflicts = pp.conflict_delta(engine.preview_batch(before), engine.preview_batch(after))
	# P4: each task the batch moves against every task it waits on, where those go ("Starts before").
	lines = pp._follower_dependency_lines(
		[(doc, was[0], now[0]) for doc, was, now in zip(docs, before, after, strict=True)],
		{doc.name: now[0] for doc, now in zip(docs, after, strict=True)},
	)
	if lines:
		conflicts[pp.DEPENDENCY_KEY] = lines
	reason = (pp.given(reason) or "").strip()
	if conflicts and not reason:
		return {"needs_reason": True, "conflicts": conflicts, "moves": reports}

	by_name = {report["task"]: report for report in reports}

	def shift(report):
		old, new = engine._as_date(report["from"]), engine._as_date(report["to"])
		return (new - old).days if old and new else 0

	later = sum(shift(report) for report in reports) > 0
	order = sorted(docs, key=lambda d: by_name[d.name]["from"] or "", reverse=later)
	pairs = [
		{"name": doc.name, "before": was, "after": now}
		for doc, was, now in zip(docs, before, after, strict=True)
	]
	warnings, moved = [], []
	# P4: a task ERPNext's own reschedule pushed during the batch is reloaded and put back on its
	# planned dates (pp._save_planned / pp._settle), rather than failing as "changed since".
	planned = {doc.name: pp._planned(doc) for doc in docs}
	with pp._quiet_alerts(notify):
		for doc in order:
			warnings.extend(pp._save_planned(doc, planned[doc.name]))
			if conflicts:
				_comment_reason(
					doc,
					_("Moved with other tasks on the Project Planner over a conflict: {0}. Reason: {1}"),
					conflicts,
					reason,
				)
			moved.append(dict(by_name[doc.name], modified=str(doc.modified)))
		warnings.extend(pp._settle(docs, planned, moved))
	result = {"moved": moved, "warnings": warnings, "conflicts": conflicts}
	if follow:
		result["shift_skipped"] = skipped
	return pp._with_notice(result, notify, pairs)


# ---------------------------------------------------------------------- undo of a created task


def _removal_problem(doc, modified, moved_dependents=(), created_children=None):
	"""Why the task just created cannot simply be deleted again, as a phrase, or None.

	Anything that has happened to it since is a reason to stop: it was changed, time was logged or
	clocked on it, it has sub-tasks, another task depends on it, someone else commented, a file is
	attached, or someone holds a draft change of it. Only its creator may undo it from here.

	``moved_dependents`` (Phase 6E) are the tasks a split pointed at this one, which its Undo puts
	back before it deletes: they are not "another task depends on it". Any other task that waits on
	it, one added since, still stops the Undo.

	``created_children`` (v1.602.0, the Undo of a build-out) are the tasks filed under this one that
	the same Undo removes too: a phase made by the build-out has them by design, and any other
	sub-task, one somebody added to the phase since, still stops the Undo. ``None`` (every other
	caller) means no sub-task is allowed.
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
	if created_children is None:
		if frappe.db.exists("Task", {"parent_task": name}):
			return _("it has sub-tasks")
	elif set(frappe.get_all("Task", filters={"parent_task": name}, pluck="name")) - set(created_children):
		return _("it has sub-tasks")
	if {row.get("parent") for row in _dependency_rows(name)} - set(moved_dependents or ()):
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


def _discard_new(refs):
	"""The Undo of drafted new tasks: mark the caller's Draft rows for ``refs`` (``draft-new:`` names)
	Discarded. ``(discarded, gone)``. Refused, discarding none, while another of the caller's drafted
	tasks (one not being discarded too) is filed under one of them or waits on one: it would be left
	pointing at nothing. A row that is not the caller's, or not a Draft any more, is "gone"."""
	rows, _duplicates = pp._draft_rows()
	mine = {pp.draft_ref(row.get("name")): row for row in rows if pp.is_new_row(row)}
	live = [ref for ref in dict.fromkeys(refs) if ref in mine]
	gone = [ref for ref in dict.fromkeys(refs) if ref not in mine]
	problems = []
	for ref, row in mine.items():
		hit = pp.spec_refs(pp.load_spec(row.get("payload"))) & set(live)
		if ref not in live and hit:
			problems.append(
				_("{0} is filed under or waits on a task you are removing").format(row.get("subject") or ref)
			)
	if problems:
		frappe.throw(
			_("Nothing was removed. {0}. Discard those first.").format(
				frappe.utils.escape_html("; ".join(problems))
			),
			title=_("Not undone"),
		)
	for ref in live:
		draft = frappe.get_doc(DRAFT_DOCTYPE, mine[ref].get("name"))
		draft.status = pp.DISCARDED
		pp._save_draft_row(draft)
	return live, gone


@frappe.whitelist(methods=["POST"])
def remove_created_task(task, modified, restore=None):
	"""Undo a task the planner just created (a duplicate, a quick add, a split's second half).

	Deletes ``task`` through ``frappe.delete_doc`` as the caller (delete permission; never ``force``,
	so a link to it still refuses, and it goes to Deleted Documents), and only when nothing has been
	attached since (:func:`_removal_problem`); otherwise it refuses with the reason and changes
	nothing. ``restore`` (JSON, for a split) puts the first half back through ``save_task``'s own
	path: ``{"task", "modified", "start", "end", "expected_time", "crew", "dependents"}``, any
	conflict that brings back recorded with the Undo reason. Returns ``{"removed", "name",
	"modified", "card", "warnings", "restored_dependents"}`` (the middle four for the task put back).

	``dependents`` (Phase 6E) are the tasks the split pointed at the second half (``split_task``'s
	``repointed``). They are put back on the first half **before** the delete, in the same request
	(so a failure leaves them where they were), and they are the only dependents that do not stop
	the Undo; a task that started waiting on the second half after the split does.
	"""
	pp._require_planner()
	if pp.is_draft_ref(task):
		# v1.603.0: the Undo of a drafted quick add or duplicate discards its Draft row. There is no Task.
		live, gone = _discard_new([task])
		return {"removed": task, "name": task, "discarded": bool(live), "gone": gone, "warnings": []}
	doc = frappe.get_doc("Task", task)
	plan = _restore_plan(restore)
	moved = [name for name in (pp.parse_list(pp.given(plan.get("dependents"))) or [])] if plan else []
	# A task filed under a phase is listed among the phase's dependencies by ERPNext itself
	# (v1.602.0): that row is not "another task depends on it", and goes before the delete.
	parent = doc.get("parent_task")
	problem = _removal_problem(doc, modified, [*moved, parent] if parent else moved)
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
	first = pp._load(plan["task"], plan.get("modified")) if plan else None

	# The tasks the split moved onto this half wait on the first half again, then it can go: the
	# delete refuses a task that something still links to (never forced).
	result = {"removed": doc.name, "warnings": []}
	if first is not None:
		result["restored_dependents"] = _restore_dependents(doc, first, moved)
	if parent:
		_unlink_from_parent(parent, doc.name)
	frappe.delete_doc("Task", doc.name)
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


def _undo_items(value):
	"""``[(task, modified)]`` from a JSON list of ``{"task", "modified"}``."""
	items = pp.parse_list(pp.given(value)) or []
	if len(items) > BUILD_OUT_MAX_ROWS + BUILD_OUT_MAX_PHASES:
		frappe.throw(_("Too many tasks to undo at once."))
	out = []
	for item in items:
		if not isinstance(item, dict) or not pp.given(item.get("task")):
			frappe.throw(_("Each task to undo needs its name."))
		out.append((item["task"], item.get("modified")))
	return out


def _unlink_from_parent(parent, task):
	"""Take ``task`` out of its phase's dependencies. ERPNext lists every sub-task there when it is
	saved (``populate_depends_on``), and Frappe will not delete a task that a row still links to.
	Written straight to the table and the parent's ``depends_on_tasks`` text, as ``_swap_dependency``
	does, so the phase is not saved (and no date is rescheduled) by an Undo."""
	frappe.db.delete("Task Depends On", {"parenttype": "Task", "parent": parent, "task": task})
	left = frappe.get_all(
		"Task Depends On",
		filters={"parenttype": "Task", "parent": parent},
		fields=["task"],
		order_by="idx asc",
		limit_page_length=0,
	)
	kept = []
	for row in left:
		if row.get("task") and row.get("task") not in kept:
			kept.append(row.get("task"))
	frappe.db.set_value(
		"Task", parent, "depends_on_tasks", "".join(f"{name}," for name in kept), update_modified=False
	)


@frappe.whitelist(methods=["POST"])
def remove_built_out_tasks(tasks, phases=None):
	"""Undo :func:`build_out_tasks`: delete the tasks it made, and the new phases it made.

	``tasks`` and ``phases`` are JSON lists of ``{"task", "modified"}``, exactly as ``build_out_tasks``
	answered. Every one is checked before anything is deleted (:func:`_removal_problem`, the check of
	a single created task's Undo): changed since, logged time or clocked time on it, another task
	waiting on it, a file, somebody else's comment or draft, or not the caller's own. A phase also
	stops the Undo if a sub-task that is not one of these has been filed under it. Tasks of the same
	build-out waiting on each other, and a phase's own list of sub-tasks, do not count. If anything
	stops it, **nothing** is removed and the answer says which and why.

	Then the tasks go (the waiting one before what it waits on, each taken off its phase's list of
	dependencies first), then the phases, each through ``frappe.delete_doc`` as the caller and never
	with ``force``. A task already deleted is skipped. Returns ``{"removed": [...], "phases": [...],
	"gone": [...]}``.
	"""
	pp._require_planner()
	leaves, groups = _undo_items(tasks), _undo_items(phases)
	if not leaves and not groups:
		frappe.throw(_("Nothing to undo."))
	drafted = [pp.is_draft_ref(name) for name, _stamp in leaves + groups]
	if any(drafted):
		# v1.603.0: the Undo of a drafting build-out discards its Draft rows (there is no Task to delete).
		if not all(drafted):
			frappe.throw(_("Drafted and saved tasks cannot be undone together."))
		live, gone = _discard_new([name for name, _stamp in leaves + groups])
		return {
			"removed": [name for name, _stamp in leaves if name in live],
			"phases": [name for name, _stamp in groups if name in live],
			"gone": gone,
			"discarded": True,
		}
	docs, gone = {}, []
	for name, _stamp in leaves + groups:
		if frappe.db.exists("Task", name):
			docs[name] = frappe.get_doc("Task", name)
		else:
			gone.append(name)
	live = set(docs)
	group_names = {name for name, _stamp in groups if name in live}
	leaf_names = [name for name, _stamp in leaves if name in live]
	children = {}
	for name in leaf_names:
		parent = docs[name].get("parent_task")
		if parent in group_names:
			children.setdefault(parent, set()).add(name)

	problems = []
	for name, stamp in leaves + groups:
		if name not in live:
			continue
		doc = docs[name]
		# Another task of this build-out waiting on it is being deleted too, and a sub-task's phase
		# lists it by ERPNext's own doing.
		allowed = live | ({doc.get("parent_task")} if doc.get("parent_task") else set())
		problem = _removal_problem(
			doc, stamp, allowed, children.get(name, set()) if name in group_names else None
		)
		if problem is None and not frappe.has_permission("Task", "delete", doc=doc):
			problem = _("you cannot delete Tasks")
		if problem:
			problems.append(f"{name}: {problem}")
	if problems:
		frappe.throw(
			_("Nothing was removed. {0}. Delete them on the task forms if they should go.").format(
				frappe.utils.escape_html("; ".join(problems))
			),
			title=_("Not undone"),
		)

	for name in leaf_names:
		parent = docs[name].get("parent_task")
		if parent:
			_unlink_from_parent(parent, name)
	waits = {}
	if leaf_names:
		for row in frappe.get_all(
			"Task Depends On",
			filters={"parenttype": "Task", "parent": ["in", leaf_names]},
			fields=["parent", "task"],
			limit_page_length=0,
		):
			if row.get("task") in leaf_names and row.get("task") != row.get("parent"):
				waits.setdefault(row.get("parent"), set()).add(row.get("task"))
	removed = deletion_order(leaf_names, waits)
	for name in removed:
		frappe.delete_doc("Task", name)
	phases_removed = [name for name, _stamp in groups if name in group_names]
	for name in phases_removed:
		frappe.delete_doc("Task", name)
	return {"removed": removed, "phases": phases_removed, "gone": gone}


# ---------------------------------------------------------------------- dependencies (P6)


def link_problem(task, depends_on, rows, waiting):
	"""Why ``task`` cannot wait on ``depends_on``, as a sentence, or None when it can.

	``rows`` maps a Task name to its row (``project``, ``is_group``, ``subject``); ``waiting`` holds the
	tasks that already wait on ``task``, at any depth (:func:`project_planner.successors`). Pure, so the
	rules are tested without a bench:

	* both ends are stored tasks: a drafted new task (``draft-new:…``) has no row to link yet;
	* not the same task, and both of the same project (ERPNext moves dependents only within one);
	* neither is a group: ERPNext lists a group's children in the group's own ``depends_on``
	  (``Task.populate_depends_on``), so a phase "waits on" its tasks already and a link to or from one
	  would be rewritten or refused on the next save;
	* no loop: the task it would wait on must not already wait on it.
	"""
	for name in (task, depends_on):
		if pp.is_draft_ref(name):
			return _("A task that is still a draft cannot be linked yet. Publish the schedule first.")
	if task == depends_on:
		return _("A task cannot wait on itself.")
	row, pred = rows.get(task), rows.get(depends_on)
	if not row or not pred:
		return _("Task {0} was not found.").format(depends_on if row else task)
	if (row.get("project") or "") != (pred.get("project") or "") or not row.get("project"):
		return _("Only tasks of the same project can be linked.")
	for name, entry in ((task, row), (depends_on, pred)):
		if cint(entry.get("is_group")):
			subject = entry.get("subject") or name
			return _("{0} is a phase or group. Link the tasks in it instead.").format(subject)
	if depends_on in set(waiting or ()):
		return _("{0} already waits on {1}, so this link would make a loop.").format(
			pred.get("subject") or depends_on, row.get("subject") or task
		)
	return None


def _link_rows(names):
	"""``{name: row}`` for :func:`link_problem` and the "starts before" check, as ``pp._task_rows`` reads
	a task plus ``is_group``."""
	rows = pp._task_rows(names)
	for row in frappe.get_all(
		"Task", filters={"name": ["in", sorted(rows)]}, fields=["name", "is_group"], limit_page_length=0
	):
		rows[row.get("name")]["is_group"] = row.get("is_group")
	return rows


def _drafted_states(names):
	"""``{name: state}`` for the tasks in ``names`` that a Draft row has moved (the caller's, or anyone's
	on a Draft schedule): a link on a draft schedule is judged against the drafted days."""
	out = {}
	rows, _duplicates = pp._draft_rows()
	for row in rows:
		if row.get("task") in names and not pp.is_new_row(row):
			doc, problem = pp._drafted_doc(row)
			if not problem:
				out[row.get("task")] = pp._state(doc)
	return out


def _rebase_drafts(task, before, after):
	"""A link changes no date, crew or anything else a draft holds, so the task's Draft rows drafted
	against the task as it was are moved on to the save, not left looking out of date."""
	for row in frappe.get_all(
		pp.DRAFT_DOCTYPE,
		filters={"task": task, "status": pp.DRAFT},
		fields=["name", "base_modified"],
		limit_page_length=0,
	):
		if pp.same_moment(row.get("base_modified"), before):
			frappe.db.set_value(
				pp.DRAFT_DOCTYPE, row.get("name"), "base_modified", after, update_modified=False
			)


def _save_links(doc):
	"""Save a change to ``doc.depends_on`` only. Its days did not change, so ERPNext's own
	``reschedule_dependent_tasks`` (calendar days, P4's merge blocker) is told not to run."""
	before = doc.modified
	doc.flags.ignore_reschedule = True
	try:
		from erpnext.projects.doctype.task.task import CircularReferenceError
	except ImportError:  # pragma: no cover - ERPNext is always installed beside this app
		CircularReferenceError = frappe.ValidationError
	try:
		warnings = pp._save(doc)
	except CircularReferenceError:
		frappe.throw(_("This link would make a loop of tasks waiting on each other."))
	_rebase_drafts(doc.name, before, doc.modified)
	return warnings


@frappe.whitelist(methods=["POST"])
def add_dependency(task, depends_on, modified, reason=None):
	"""``task`` waits on ``depends_on`` (finish to start): the Gantt tab's link drawn from one bar's
	end dot onto another (P6, §4.3).

	Through the planner's rules, as ``save_task``: the planner role and write permission on ``task``,
	the "changed since the planner loaded" check on ``modified``, no Fountain Move task (its schedule is
	the Maintenance Planner's) and no rental crew task. When ``task`` would start before ``depends_on``
	ends (on their drafted days, where a Draft row moves either), the answer is
	``{"needs_reason": True, "conflicts": {"Dependencies": [line]}}`` and nothing is saved; with a
	``reason`` it is saved and the reason goes on the task's timeline. Nothing moves: no date changes,
	so nobody is notified, and ERPNext's own calendar-day push is held back (:func:`_save_links`).

	A link is not drafted on a Draft schedule. It holds no dates and tells nobody, which is all a draft
	holds back; the task's Draft rows stay current (:func:`_rebase_drafts`).

	Returns ``{"name", "modified", "warnings", "conflicts", "linked", "task", "depends_on"}``;
	``linked`` is False when the link was already there.
	"""
	task, depends_on = pp.given(task), pp.given(depends_on)
	if pp.is_draft_ref(task) or pp.is_draft_ref(depends_on):
		frappe.throw(link_problem(task, depends_on, {}, ()))
	doc = pp._load(task, modified)
	_refuse_rental(doc, _("linked"))
	if not frappe.db.exists("Task", depends_on):
		frappe.throw(_("Task {0} was not found.").format(depends_on))
	frappe.has_permission("Task", "read", doc=depends_on, throw=True)
	rows = _link_rows([task, depends_on])
	problem = link_problem(task, depends_on, rows, pp.successors(task))
	if problem:
		frappe.throw(problem)
	answer = {"name": doc.name, "task": doc.name, "depends_on": depends_on, "warnings": [], "conflicts": {}}
	if any(row.get("task") == depends_on for row in doc.get("depends_on") or []):
		return dict(answer, modified=str(doc.modified), linked=False)
	drafted = _drafted_states({task, depends_on})
	state = drafted.get(task) or pp._state(doc)
	pred = drafted.get(depends_on) or rows[depends_on]
	lines = [] if engine.is_tentative(state) else pp.dependency_conflicts(state, [pred])
	conflicts = {pp.DEPENDENCY_KEY: lines} if lines else {}
	reason = (reason or "").strip()
	if conflicts and not reason:
		return {"needs_reason": True, "conflicts": conflicts}
	doc.append("depends_on", {"task": depends_on})
	warnings = _save_links(doc)
	if conflicts:
		text = _("Linked on the Project Planner over a conflict: {0}. Reason: {1}")
		_comment_reason(doc, text, conflicts, reason)
	return dict(answer, modified=str(doc.modified), warnings=warnings, conflicts=conflicts, linked=True)


@frappe.whitelist(methods=["POST"])
def remove_dependency(task, depends_on, modified):
	"""``task`` no longer waits on ``depends_on``: a link removed on the Gantt tab, or the Undo of
	:func:`add_dependency`. The same checks on ``task``; a group's own rows are ERPNext's (each child in
	its group's ``depends_on``) and are refused. Removing a wait never creates a conflict, so there is no
	reason to ask for. Returns ``{"name", "modified", "warnings", "removed", "task", "depends_on"}``;
	``removed`` is False when there was no such link."""
	task, depends_on = pp.given(task), pp.given(depends_on)
	if pp.is_draft_ref(task):
		frappe.throw(link_problem(task, depends_on, {}, ()))
	doc = pp._load(task, modified)
	_refuse_rental(doc, _("changed"))
	if cint(doc.get("is_group")):
		frappe.throw(
			_("{0} is a phase or group. Its tasks are ERPNext's to list.").format(doc.subject or doc.name)
		)
	answer = {"name": doc.name, "task": doc.name, "depends_on": depends_on, "warnings": []}
	keep = [row for row in doc.get("depends_on") or [] if row.get("task") != depends_on]
	if len(keep) == len(doc.get("depends_on") or []):
		return dict(answer, modified=str(doc.modified), removed=False)
	doc.set("depends_on", keep)
	warnings = _save_links(doc)
	return dict(answer, modified=str(doc.modified), warnings=warnings, removed=True)


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
