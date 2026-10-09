# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Multi-person maintenance visits: the crew table and its assignment mirror (P1.8).

Some visits take more than one person, and some take several people all day (Nik, 2026-10-09).
A Sapphire Maintenance Record therefore carries, besides ``technician``, a ``crew`` table (rows of
Sapphire Visit Crew Member) plus ``planned_hours`` and ``full_day``. ``technician`` keeps its
meaning: **the person filling in the visit**, the one clock autofill, the timesheet, the
consumable warehouse and kiosk attribution key on. The crew is everyone else booked on it.

This module holds what every caller shares:

* :func:`validate_crew` tidies a crew table before it is stored (record and profile alike).
* :func:`on_record_update` mirrors crew changes into ordinary assignments (ToDos), exactly as
  ``project_enhancements/crew_sync.py`` does for a Task's crew, so the sidebar, "assigned to me",
  the ToDo list and everything else that reads assignments keeps working unchanged.
* :func:`crew_users` / :func:`crew_record_names` answer "who is on this visit" and "which visits
  is this person on" for the kiosk, the Visit Wizard and the digest.

The mirror is one-sided and conservative, like the Task one:

* **Add** an assignment only for a user newly on the crew who holds no open ToDo on the visit yet.
* **Remove** one only for a user who was on the crew *before this save* and is not now, and who
  is not now the technician. A Visit Wizard claim moves a crew member into ``technician`` and the
  displaced technician into the crew; neither of them may lose their ToDo over it. A user assigned
  from the sidebar who was never on the crew is never touched.
* Submitted and cancelled visits are skipped, and so are imports, patches, installs and migrates.
* It never raises: a visit must stay saveable even if an assignee is disabled or a share fails.

``doc_events`` fire during ERPNext's own test bootstrap, before this app's fields exist, so every
read of ``crew`` goes through ``getattr(doc, "crew", None) or []``.
"""

import frappe
from frappe import _

RECORD = "Sapphire Maintenance Record"
PROFILE = "Sapphire Maintenance Profile"
CREW_DOCTYPE = "Sapphire Visit Crew Member"


def flt(value):
	"""``frappe.utils.flt`` without the import: bench-free suites stub ``frappe.utils`` sparsely."""
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def cint(value):
	try:
		return int(float(value or 0))
	except (TypeError, ValueError):
		return 0


def _rows(doc, table="crew"):
	if doc is None:
		return []
	if isinstance(doc, dict):
		return doc.get(table) or []
	return getattr(doc, table, None) or []


def _get(row, field):
	if isinstance(row, dict):
		return row.get(field)
	return getattr(row, field, None)


def crew_users(doc, table="crew", lead_field="technician"):
	"""The users on a visit's crew, in row order, without repeats or the technician."""
	lead = _get(doc, lead_field) if doc is not None else None
	out = []
	for row in _rows(doc, table):
		user = _get(row, "user")
		if user and user != lead and user not in out:
			out.append(user)
	return out


def visit_people(doc):
	"""Everyone booked on a visit: the technician first, then the crew."""
	lead = _get(doc, "technician")
	return ([lead] if lead else []) + crew_users(doc)


def explicit_hours(value):
	"""A crew row's / visit's hours when set: a Float reads 0 for blank, so only > 0 counts."""
	hours = flt(value) if value not in (None, "") else 0.0
	return round(hours, 2) if hours > 0 else None


def validate_crew(doc, method=None, table="crew", lead_field="technician", hours_field="planned_hours"):
	"""Tidy a crew table before it is stored.

	* A row for the technician (or the profile's default technician) is dropped: they are on the
	  visit already, and a second booking would double their hours.
	* The same person twice keeps their first row.
	* Hours round to two decimals; a negative value is an error, since it would *subtract* from a
	  day's booked hours. Blank stays blank (0): "the visit's length".
	"""
	lead = _get(doc, lead_field)
	seen = set()
	for row in list(_rows(doc, table)):
		user = _get(row, "user")
		if user and (user == lead or user in seen):
			doc.remove(row)
			continue
		if user:
			seen.add(user)
		hours = _get(row, "hours")
		if hours not in (None, ""):
			hours = flt(hours)
			if hours < 0:
				frappe.throw(_("Crew row {0}: hours cannot be negative.").format(_get(row, "idx")))
			row.hours = round(hours, 2)

	if hours_field:
		hours = _get(doc, hours_field)
		if hours not in (None, "") and flt(hours) < 0:
			frappe.throw(_("A visit's hours cannot be negative."))


def _skipped_by_flags():
	flags = frappe.flags
	return bool(
		getattr(flags, "in_import", False)
		or getattr(flags, "in_patch", False)
		or getattr(flags, "in_install", False)
		or getattr(flags, "in_migrate", False)
	)


def on_record_update(doc, method=None):
	"""``Sapphire Maintenance Record`` on_update: mirror the crew into assignments.

	See the module docstring for the rules. Best effort; never raises.
	"""
	if _skipped_by_flags():
		return
	try:
		if cint(_get(doc, "docstatus")) != 0:
			return

		now = set(crew_users(doc))
		before_doc = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
		before = set(crew_users(before_doc)) if before_doc else set()
		technician = _get(doc, "technician")

		added = sorted(now - before)
		# Someone who left the crew to become the technician keeps the ToDo they hold.
		removed = sorted(before - now - {technician})
		if not added and not removed:
			return

		from frappe.desk.form.assign_to import add as assign_add
		from frappe.desk.form.assign_to import remove as assign_remove

		open_todos = set(
			frappe.get_all(
				"ToDo",
				filters={"reference_type": RECORD, "reference_name": doc.name, "status": "Open"},
				pluck="allocated_to",
			)
		)

		for user in added:
			if user in open_todos:
				continue
			try:
				assign_add(
					{
						"assign_to": [user],
						"doctype": RECORD,
						"name": doc.name,
						"description": _("On the crew for a maintenance visit."),
					}
				)
			except Exception:
				frappe.log_error(
					title="Maintenance visit crew assignment failed",
					message=f"{doc.name} -> {user}\n{frappe.get_traceback()}",
				)

		for user in removed:
			if user not in open_todos:
				continue
			try:
				assign_remove(RECORD, doc.name, user)
			except Exception:
				frappe.log_error(
					title="Maintenance visit crew unassignment failed",
					message=f"{doc.name} -> {user}\n{frappe.get_traceback()}",
				)
	except Exception:
		frappe.log_error(title="Maintenance visit crew sync failed", message=frappe.get_traceback())


def crew_record_names(user, docstatus=None, project=None, modified_since=None, limit=100):
	"""The visit records ``user`` is on the crew of, newest first, at most ``limit``.

	One bounded join on the crew table (rows of the record, never the profile's default crew).
	Never raises: every caller is a list a technician is looking at, and a failure here must
	leave the rest of that list standing, so it is logged and answers [].
	"""
	if not user:
		return []
	conditions = ["c.parenttype = %(parenttype)s", "c.parentfield = 'crew'", "c.user = %(user)s"]
	values = {"parenttype": RECORD, "user": user, "limit": int(limit)}
	if docstatus is not None:
		conditions.append("r.docstatus = %(docstatus)s")
		values["docstatus"] = cint(docstatus)
	if project:
		conditions.append("r.project = %(project)s")
		values["project"] = project
	if modified_since:
		conditions.append("r.modified >= %(since)s")
		values["since"] = modified_since
	try:
		rows = frappe.db.sql(
			f"""
			SELECT DISTINCT r.name, r.modified
			FROM `tab{CREW_DOCTYPE}` c
			INNER JOIN `tab{RECORD}` r ON r.name = c.parent
			WHERE {" AND ".join(conditions)}
			ORDER BY r.modified DESC
			LIMIT %(limit)s
			""",
			values,
			as_dict=True,
		)
	except Exception:
		frappe.log_error(title="Maintenance visit crew lookup failed", message=frappe.get_traceback())
		return []
	return [row.get("name") for row in rows or [] if row.get("name")]


def crew_rows_for(record_names):
	"""``{record: [{"name", "user", "full_name", "hours", "digest_sent_on"}]}`` in row order.

	One query for every record asked about. The SQL deliberately names only the crew table, so a
	caller's own record query is never confused with it.
	"""
	names = sorted({name for name in record_names or [] if name})
	if not names:
		return {}
	out = {}
	for row in (
		frappe.db.sql(
			f"""
		SELECT name, parent, user, full_name, hours, digest_sent_on, idx
		FROM `tab{CREW_DOCTYPE}`
		WHERE parenttype = %(parenttype)s AND parentfield = 'crew' AND parent IN %(names)s
		ORDER BY parent, idx
		""",
			{"parenttype": RECORD, "names": tuple(names)},
			as_dict=True,
		)
		or []
	):
		parent = row.get("parent")
		if not parent or not row.get("user"):
			continue
		mine = out.setdefault(parent, [])
		if any(r["user"] == row.get("user") for r in mine):
			continue
		mine.append(
			{
				"name": row.get("name"),
				"user": row.get("user"),
				"full_name": row.get("full_name"),
				"hours": row.get("hours"),
				"digest_sent_on": row.get("digest_sent_on"),
			}
		)
	return out


def set_crew(doc, entries):
	"""Replace a record's crew with ``entries`` (``[{"user", "hours"}]``), keeping existing rows.

	A person who stays keeps their row, and with it their ``digest_sent_on``, so editing a visit's
	crew after 6 AM never re-texts somebody who already has the day's digest. Returns True when
	anything changed.
	"""
	wanted = []
	for entry in entries or []:
		user = entry.get("user")
		if user and user != _get(doc, "technician") and user not in [w["user"] for w in wanted]:
			wanted.append({"user": user, "hours": explicit_hours(entry.get("hours"))})

	current = list(_rows(doc))
	before = [(_get(r, "user"), explicit_hours(_get(r, "hours"))) for r in current]
	after = [(w["user"], w["hours"]) for w in wanted]
	if before == after:
		return False

	by_user = {}
	for row in current:
		by_user.setdefault(_get(row, "user"), row)
	keep = []
	for entry in wanted:
		row = by_user.get(entry["user"])
		if row is not None:
			row.hours = entry["hours"] or 0
			keep.append(row)
		else:
			keep.append({"user": entry["user"], "hours": entry["hours"] or 0})
	doc.set("crew", [])
	for row in keep:
		doc.append("crew", row)
	return True
