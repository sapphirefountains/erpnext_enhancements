"""Personal blocks and day notes for both planners (Project Planner Phase 6D, TASK-2026-02470).

* **Personal blocks** (``Planner Block``): "unavailable 2–4 pm", "shop day". One person, one day,
  all day or from a time to a time, with a private note. Both planners count them against hours
  through the shared engine (``project_enhancements/crew_availability``): an all-day block is a day
  off ("Unavailable"), a timed one books its hours, and work dragged onto either asks for a reason
  like any other conflict.
* **Day notes** (``Planner Day Note``): "Shop meeting 7 am", "City inspection at Riverwalk", for
  everyone or for one Planner Resource group, on both calendars, in My week and in the 6 AM digest.

Nik's rules (2026-10-09), and how each is kept here:

* **Planners can block anyone; each person can block their own time.** The planners are
  ``crew_availability.SCHEDULER_ROLES`` (System Manager, Projects Manager, Projects User,
  Maintenance Supervisor). Everyone else with planner access, the technicians (Maintenance User),
  may create, edit and delete only blocks whose Planner Resource's ``user`` is them, an existing
  block included. Every endpoint first checks the union of both planners' roles
  (:data:`ACCESS_ROLES`). Technicians have no Desk permission on the doctype, so these checks are the
  rule, and the writes use ``ignore_permissions`` after them.
* **Others see "Unavailable"; planners see the note; a person always sees their own.** Notes are
  attached only through ``crew_availability.block_notes(names, viewer)``, so an answer that forgets
  to attach them carries none. The engine's bookings and conflict sentences never contain a note.
* **Saving a block never refuses because of a conflict.** It answers with the conflicts the block
  makes with firm work already booked (``[{date, label, items}]``) so the page can say "This
  overlaps Dig at Riverwalk; your PM will see it in the Conflict center".
* **No timeline comment on the tasks or visits a block overlaps.** Decided deliberately: the block
  is the person's own business and its existence is already on every planner; a comment would put a
  technician's private appointment on a customer job's timeline, would go stale the moment the block
  is moved or deleted, and the Conflict center is where a PM deals with it. A kept conflict
  (``api/planner_conflicts.acknowledge_conflict``) is what writes on the timeline.

Hooks into the existing reads are one line each: ``project_planner.get_planner`` and
``maintenance_planner.get_planner`` add :func:`payload_extras`, ``project_planner.get_my_week`` adds
:func:`my_week_extras`, and the three morning digests read :func:`digest_extras` /
:func:`digest_note_lines`.
"""

import frappe
from frappe import _
from frappe.utils import date_diff, getdate

from erpnext_enhancements.api import maintenance_planner as mp
from erpnext_enhancements.api import project_planner as pp
from erpnext_enhancements.project_enhancements import crew_availability as engine

BLOCK_DOCTYPE = engine.BLOCK_DOCTYPE
NOTE_DOCTYPE = "Planner Day Note"
#: Nik's "planners": they block anyone, read every note and write day notes. Defined in the engine.
SCHEDULER_ROLES = engine.SCHEDULER_ROLES
#: Everyone who can open either planner may read blocks and notes and block their own time.
ACCESS_ROLES = frozenset(pp.PLANNER_ROLES | mp.PLANNER_ROLES)
#: The longest range one read answers for, the planners' own limit.
MAX_RANGE_DAYS = 100
#: A day note's audience: blank (everyone) or one Planner Resource group.
AUDIENCES = ("Field", "PM", "Design", "Subcontractor")


# ---------------------------------------------------------------------- pure helpers


def time_text(value):
	"""A Time argument as ``"HH:MM:00"``, or None when it is blank or unreadable."""
	minutes = engine.time_minutes(pp.given(value))
	if minutes is None:
		return None
	return f"{minutes // 60:02d}:{minutes % 60:02d}:00"


def block_entry(row, notes=None, viewer=None, schedule=False):
	"""One block as the pages read it. ``note`` only when ``notes`` (from ``block_notes``) has it.

	``{"name", "resource", "resource_label", "user", "date", "all_day", "from_time", "to_time",
	"slot", "window", "hours", "label", "note", "own", "can_edit", "modified"}``: ``label`` is
	always "Unavailable"; ``own`` is the viewer's own block; ``can_edit`` is own-or-scheduler.
	"""
	row = row or {}
	all_day = engine.is_all_day_block(row)
	slot = None if all_day else engine.block_slot(row)
	text = [engine._hhmm(slot[0]), engine._hhmm(slot[1])] if slot else None
	own = bool(viewer) and row.get("user") == viewer
	return {
		"name": row.get("name"),
		"resource": row.get("resource"),
		"resource_label": row.get("resource_name") or row.get("resource"),
		"user": row.get("user"),
		"date": str(getdate(row.get("date"))) if row.get("date") else None,
		"all_day": all_day,
		"from_time": text[0] if text else None,
		"to_time": text[1] if text else None,
		"slot": text,
		"window": engine.block_window(text),
		"hours": round((slot[1] - slot[0]) / 60, 2) if slot else None,
		"label": engine.UNAVAILABLE,
		"note": (notes or {}).get(row.get("name")),
		"own": own,
		"can_edit": bool(schedule or own),
		"modified": str(row.get("modified")) if row.get("modified") else None,
	}


def note_entry(row, schedule=False):
	"""One day note: ``{"name", "date", "note", "audience", "project", "project_title", "can_edit"}``."""
	row = row or {}
	return {
		"name": row.get("name"),
		"date": str(getdate(row.get("date"))) if row.get("date") else None,
		"note": str(row.get("note") or "").strip(),
		"audience": row.get("audience") or None,
		"project": row.get("project") or None,
		"project_title": row.get("project_title") or row.get("project") or None,
		"can_edit": bool(schedule),
	}


def notes_for_audience(entries, group):
	"""The notes a person in Planner Resource group ``group`` gets: everyone's, and their group's."""
	return [e for e in entries or [] if not e.get("audience") or (group and e.get("audience") == group)]


def group_by_date(entries):
	"""``{"YYYY-MM-DD": [entry, ...]}`` in date order, each day's entries in the order given."""
	out = {}
	for entry in sorted(entries or [], key=lambda e: str(e.get("date") or "")):
		if entry.get("date"):
			out.setdefault(entry["date"], []).append(entry)
	return out


def block_names_in(days):
	"""Every block name in a planner's day cells: the engine's (``bookings`` of kind ``block``) or the
	Maintenance Planner's (``blocks``). ``days`` is ``{key: {"YYYY-MM-DD": cell}}``."""
	names = set()
	for per_day in (days or {}).values():
		for cell in (per_day or {}).values():
			for booking in (cell or {}).get("bookings") or []:
				if booking.get("kind") == engine.BLOCK_KIND and booking.get("ref"):
					names.add(booking["ref"])
			for block in (cell or {}).get("blocks") or []:
				if block.get("ref") or block.get("name"):
					names.add(block.get("ref") or block.get("name"))
	return sorted(names)


def block_text(entry):
	"""A block as one digest line: ``"Unavailable 2–4 pm: Dentist"``, ``"Unavailable all day"``."""
	when = entry.get("window") or (_("all day") if entry.get("all_day") else "")
	text = " ".join(part for part in (_(engine.UNAVAILABLE), when) if part)
	return f"{text}: {entry['note']}" if entry.get("note") else text


def note_text(entry):
	"""A day note as one digest line: ``"Note: Shop meeting 7 am"``, ``"Note (Riverwalk): …"``."""
	place = entry.get("project_title")
	head = _("Note ({0})").format(place) if place else _("Note")
	return f"{head}: {entry.get('note') or ''}"


# ---------------------------------------------------------------------- gates and reads


def _viewer():
	return frappe.session.user


def can_schedule(user=None):
	"""True for Administrator and :data:`SCHEDULER_ROLES` ("block anyone", read every note)."""
	return engine.can_read_every_note(user or _viewer())


def _require_access():
	if _viewer() == "Administrator":
		return
	if not ACCESS_ROLES & set(frappe.get_roles()):
		frappe.throw(_("Only projects and maintenance staff can use the planner."), frappe.PermissionError)


def _require_scheduler():
	if not can_schedule():
		frappe.throw(
			_(
				"Only a System Manager, Projects Manager, Projects User or Maintenance Supervisor can do that."
			),
			frappe.PermissionError,
		)


def _range(start, end):
	start, end = getdate(start), getdate(end)
	if end < start:
		frappe.throw(_("The end date is before the start date."))
	if date_diff(end, start) > MAX_RANGE_DAYS:
		frappe.throw(_("Pick a range of {0} days or less.").format(MAX_RANGE_DAYS))
	return start, end


def _installed(doctype):
	check = getattr(frappe.db, "table_exists", None)
	if not callable(check):
		return False
	try:
		return bool(check(doctype))
	except Exception:
		return False


def _resource_row(name):
	if not name:
		return None
	return frappe.db.get_value(
		"Planner Resource",
		name,
		["name", "resource_name", "user", "is_active", "resource_group"],
		as_dict=True,
	)


def _check_owner(owner):
	"""Own-or-scheduler: a technician may touch only a block on their own Planner Resource."""
	if can_schedule():
		return
	if owner and owner == _viewer():
		return
	frappe.throw(_("You can only block your own time."), frappe.PermissionError)


def _block_rows(start, end, resources=None):
	"""Planner Block rows in ``start``..``end`` (optionally of ``resources``), without the note."""
	if not _installed(BLOCK_DOCTYPE):
		return []
	filters = {"date": ["between", [start, end]]}
	if resources is not None:
		filters["resource"] = ["in", sorted({r for r in resources if r}) or ["__none__"]]
	return frappe.get_all(
		BLOCK_DOCTYPE,
		filters=filters,
		fields=[*engine.BLOCK_FIELDS, "resource_name"],
		order_by="date asc, name asc",
		limit_page_length=0,
	)


def _note_rows(start, end):
	if not _installed(NOTE_DOCTYPE):
		return []
	return frappe.get_all(
		NOTE_DOCTYPE,
		filters={"date": ["between", [start, end]]},
		fields=["name", "date", "note", "audience", "project", "project_title", "modified"],
		order_by="date asc, creation asc",
		limit_page_length=0,
	)


def _write(doc):
	"""Save ``doc`` (insert when new) past Desk permissions, after this module's own checks; what
	``validate`` said through msgprint (a day that has passed) comes back as warnings."""
	log = pp._message_log()
	mark = len(log) if log is not None else 0
	doc.flags.ignore_permissions = True
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)
	return pp._take_messages(log, mark)


# ---------------------------------------------------------------------- blocks: endpoints


@frappe.whitelist()
def get_blocks(start, end, resource=None, user=None):
	"""Personal blocks from ``start`` to ``end`` (at most 100 days), optionally for one ``resource``
	or one ``user``. Any planner role.

	``{"start", "end", "can_schedule", "blocks": [block_entry]}``; ``note`` is filled only where the
	caller may read it (their own block, or any block for a scheduler).
	"""
	_require_access()
	start, end = _range(start, end)
	resource, user = pp.given(resource), pp.given(user)
	resources = None
	if resource:
		resources = [resource]
	elif user:
		resources = frappe.get_all(
			"Planner Resource", filters={"user": user}, pluck="name", limit_page_length=0
		)
	rows = _block_rows(start, end, resources)
	notes = engine.block_notes([row.get("name") for row in rows])
	schedule = can_schedule()
	return {
		"start": str(start),
		"end": str(end),
		"can_schedule": schedule,
		"blocks": [block_entry(row, notes, _viewer(), schedule) for row in rows],
	}


def _target(resource, user, doc):
	"""The Planner Resource row a save is for: ``resource``, else ``user``'s active one, else the
	existing block's, else the caller's own."""
	from erpnext_enhancements.project_enhancements import planner_notices

	name = pp.given(resource)
	if not name and pp.given(user):
		name = planner_notices.resource_of(user)
		if not name:
			frappe.throw(_("{0} is not on the planner's list of people.").format(user))
	if not name and doc.get("resource"):
		name = doc.get("resource")
	if not name:
		name = planner_notices.resource_of(_viewer())
		if not name:
			frappe.throw(
				_(
					"You are not on the planner's list of people yet, so you cannot block time. "
					"Ask a project manager to add you under Planner Resources."
				)
			)
	row = _resource_row(name)
	if not row:
		frappe.throw(_("{0} is not a Planner Resource.").format(name))
	return row


def _conflicts_made(resource, day, name):
	"""``[{"date", "label", "items"}]``: the conflicts on ``resource``'s ``day`` that the block makes
	with firm work, by comparing the day with and without it (two one-person, one-day engine passes,
	Google switched off). ``items`` are ``[{doctype, name, title, planner}]``."""
	from erpnext_enhancements.api import planner_conflicts

	day = getdate(day)
	key = str(day)
	without = engine._compute(day, day, [resource], google=False, exclude_blocks={name})
	with_block = engine._compute(day, day, [resource], google=False)
	known = set((((without.get("days") or {}).get(resource) or {}).get(key) or {}).get("conflicts") or [])
	cell = ((with_block.get("days") or {}).get(resource) or {}).get(key) or {}
	out = []
	for detail in engine.day_conflict_details(cell.get("capacity"), cell.get("off"), cell.get("bookings")):
		if detail["message"] in known:
			continue
		items = []
		for booking in detail["bookings"]:
			item = planner_conflicts.booking_item(booking, None)
			if item:
				items.append({k: item[k] for k in ("doctype", "name", "title", "planner")})
		if items:
			out.append({"date": key, "label": detail["message"], "items": items})
	return out


def conflict_message(conflicts, schedule):
	"""The sentence the page shows after a save that overlaps work, or None."""
	titles = []
	for conflict in conflicts or []:
		for item in conflict.get("items") or []:
			if item.get("title") and item["title"] not in titles:
				titles.append(item["title"])
	if not titles:
		return None
	if schedule:
		return _("This overlaps {0}. It is listed in the Conflict center.").format(", ".join(titles))
	return _("This overlaps {0}; your PM will see it in the Conflict center.").format(", ".join(titles))


@frappe.whitelist(methods=["POST"])
def save_block(
	date, resource=None, user=None, all_day=None, from_time=None, to_time=None, note=None, name=None
):
	"""Create a block, or edit block ``name``. Own-or-scheduler, an existing block included.

	``resource`` (a Planner Resource) or ``user`` names the person; with neither, a new block is the
	caller's own and an edited one stays on its person. ``all_day`` is a Check; left out, the block
	is all day unless both ``from_time`` and ``to_time`` are given. ``note`` left out keeps the
	stored one (an empty string clears it).

	Never refused because of a conflict. Returns ``{"block": block_entry, "conflicts": [{"date",
	"label", "items": [{doctype, name, title, planner}]}], "message", "warnings"}``: the conflicts
	the block makes with firm work already booked that day, and the sentence to show for them.
	"""
	_require_access()
	name = pp.given(name)
	if name:
		doc = frappe.get_doc(BLOCK_DOCTYPE, name)
		current = _resource_row(doc.get("resource"))
		_check_owner((current or {}).get("user") or doc.get("user"))
	else:
		doc = frappe.new_doc(BLOCK_DOCTYPE)
	target = _target(resource, user, doc)
	_check_owner(target.get("user"))
	if not int(target.get("is_active") or 0):
		frappe.throw(
			_("{0} is not active on the planner.").format(target.get("resource_name") or target["name"])
		)

	doc.resource = target["name"]
	doc.date = str(getdate(date))
	first, last = time_text(from_time), time_text(to_time)
	whole = pp.as_bool(all_day) if pp.given(all_day) is not None else not (first and last)
	doc.all_day = 1 if whole else 0
	doc.from_time = None if whole else first
	doc.to_time = None if whole else last
	if note is not None and not (isinstance(note, str) and note.strip() in ("null", "undefined")):
		doc.note = str(note).strip() or None
	warnings = _write(doc)

	conflicts = []
	try:
		conflicts = _conflicts_made(doc.resource, doc.date, doc.name)
	except Exception:
		# The block is saved; a failure working out what it overlaps must not lose that.
		frappe.log_error(title="Planner block: conflict check failed", message=frappe.get_traceback())
	schedule = can_schedule()
	row = {key: doc.get(key) for key in (*engine.BLOCK_FIELDS, "resource_name")}
	notes = engine.block_notes([doc.name])
	return {
		"block": block_entry(row, notes, _viewer(), schedule),
		"conflicts": conflicts,
		"message": conflict_message(conflicts, schedule),
		"warnings": warnings,
	}


@frappe.whitelist(methods=["POST"])
def delete_block(name):
	"""Delete block ``name``. Own-or-scheduler. ``{"name", "deleted": True}``."""
	_require_access()
	doc = frappe.get_doc(BLOCK_DOCTYPE, name)
	current = _resource_row(doc.get("resource"))
	_check_owner((current or {}).get("user") or doc.get("user"))
	frappe.delete_doc(BLOCK_DOCTYPE, doc.name, ignore_permissions=True)
	return {"name": doc.name, "deleted": True}


# ---------------------------------------------------------------------- day notes: endpoints


@frappe.whitelist()
def get_day_notes(start, end, mine=0):
	"""Day notes from ``start`` to ``end`` (at most 100 days). Any planner role.

	``{"start", "end", "can_schedule", "notes": {"YYYY-MM-DD": [note_entry]}}``. With ``mine=1``
	only the notes for the caller's own Planner Resource group (and everyone's), as their digest
	and My week show them.
	"""
	_require_access()
	start, end = _range(start, end)
	schedule = can_schedule()
	entries = [note_entry(row, schedule) for row in _note_rows(start, end)]
	if pp.as_bool(pp.given(mine) or 0):
		from erpnext_enhancements.project_enhancements import planner_notices

		person = _resource_row(planner_notices.resource_of(_viewer()))
		entries = notes_for_audience(entries, (person or {}).get("resource_group"))
	return {"start": str(start), "end": str(end), "can_schedule": schedule, "notes": group_by_date(entries)}


@frappe.whitelist(methods=["POST"])
def save_day_note(date, note, audience=None, project=None, name=None):
	"""Create a day note, or edit note ``name``. Schedulers only. Returns the ``note_entry``."""
	_require_access()
	_require_scheduler()
	name = pp.given(name)
	doc = frappe.get_doc(NOTE_DOCTYPE, name) if name else frappe.new_doc(NOTE_DOCTYPE)
	doc.date = str(getdate(date))
	doc.note = str(note or "").strip()
	audience = pp.given(audience)
	if audience and audience not in AUDIENCES:
		frappe.throw(_("A note is for everyone or for one of: {0}.").format(", ".join(AUDIENCES)))
	doc.audience = audience or None
	doc.project = pp.given(project) or None
	_write(doc)
	return note_entry(
		{key: doc.get(key) for key in ("name", "date", "note", "audience", "project", "project_title")}, True
	)


@frappe.whitelist(methods=["POST"])
def delete_day_note(name):
	"""Delete day note ``name``. Schedulers only. ``{"name", "deleted": True}``."""
	_require_access()
	_require_scheduler()
	frappe.delete_doc(NOTE_DOCTYPE, name, ignore_permissions=True)
	return {"name": name, "deleted": True}


# ---------------------------------------------------------------------- hooks into existing reads


def payload_extras(start, end, days):
	"""What both planners' ``get_planner`` add: ``{"day_notes", "block_notes", "can_schedule"}``.

	``day_notes`` is every note in range (``{date: [note_entry]}``: the calendar shows them all);
	``block_notes`` is ``{block: note}`` for the blocks in ``days`` the caller may read;
	``can_schedule`` says whether the page may offer "block anyone". Never raises: a failure is
	logged and leaves the planner as it was.
	"""
	out = {"day_notes": {}, "block_notes": {}, "can_schedule": False}
	try:
		out["can_schedule"] = can_schedule()
		out["day_notes"] = group_by_date(
			note_entry(row, out["can_schedule"]) for row in _note_rows(start, end)
		)
		out["block_notes"] = engine.block_notes(block_names_in(days))
	except Exception:
		frappe.log_error(title="Planner: day notes and block notes failed", message=frappe.get_traceback())
	return out


def my_week_extras(resource, start, end):
	"""What ``get_my_week`` adds: ``{"blocks": [block_entry], "day_notes": {date: [note_entry]}}``.

	The person's own blocks with their own notes, and the week's notes for their group. Never raises.
	"""
	out = {"blocks": [], "day_notes": {}}
	if not resource:
		return out
	try:
		person = _resource_row(resource) or {}
		rows = _block_rows(start, end, [resource])
		notes = engine.block_notes([row.get("name") for row in rows])
		schedule = can_schedule()
		out["blocks"] = [block_entry(row, notes, _viewer(), schedule) for row in rows]
		entries = [note_entry(row, schedule) for row in _note_rows(start, end)]
		out["day_notes"] = group_by_date(notes_for_audience(entries, person.get("resource_group")))
	except Exception:
		frappe.log_error(title="Planner: My week blocks and notes failed", message=frappe.get_traceback())
	return out


def digest_extras(day, resources):
	"""``{resource: {"day_notes": [note_entry], "blocks": [block_entry]}}`` for one day's digest.

	Each person gets the day's notes for everyone and for their group, and their own blocks with
	their own note (read as them, not as whoever runs the scheduler). Never raises.
	"""
	day = getdate(day)
	out = {}
	try:
		names = sorted({r for r in resources or () if r})
		if not names:
			return out
		people = {
			row.get("name"): row
			for row in frappe.get_all(
				"Planner Resource",
				filters={"name": ["in", names]},
				fields=["name", "user", "resource_group"],
				limit_page_length=0,
			)
		}
		entries = [note_entry(row) for row in _note_rows(day, day)]
		rows = _block_rows(day, day, names)
		for name in names:
			person = people.get(name) or {}
			mine = [row for row in rows if row.get("resource") == name]
			notes = (
				engine.block_notes([row.get("name") for row in mine], viewer=person.get("user"))
				if mine
				else {}
			)
			blocks = [block_entry(row, notes, person.get("user")) for row in mine]
			out[name] = {
				"day_notes": [
					dict(e, text=note_text(e))
					for e in notes_for_audience(entries, person.get("resource_group"))
				],
				"blocks": [dict(e, text=block_text(e)) for e in blocks],
			}
	except Exception:
		frappe.log_error(title="Planner digest: day notes and blocks failed", message=frappe.get_traceback())
	return out


def digest_note_lines(user, day):
	"""The day notes and own blocks for ``user``'s maintenance or rental digest, as plain lines.

	For the two older digests, which only reach people the combined digest does not cover. A user
	with no active Planner Resource gets everyone's notes and no blocks. Never raises.
	"""
	try:
		from erpnext_enhancements.project_enhancements import planner_notices

		resource = planner_notices.resource_of(user)
		if resource:
			extras = digest_extras(day, [resource]).get(resource) or {}
		else:
			day = getdate(day)
			extras = {
				"day_notes": notes_for_audience([note_entry(row) for row in _note_rows(day, day)], None),
				"blocks": [],
			}
		return [note_text(e) for e in extras.get("day_notes") or []] + [
			block_text(e) for e in extras.get("blocks") or []
		]
	except Exception:
		frappe.log_error(title="Planner digest: note lines failed", message=frappe.get_traceback())
		return []
