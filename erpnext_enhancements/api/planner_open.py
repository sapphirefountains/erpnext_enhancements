# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What a tap on a planner item may open, for the person asking (v1.752.0).

A tap on anything a planner draws opens its record (Nik, 2026-10-27; ``planner_kit/records.js``). The
browser's ``frappe.model.can_read`` only knows which *doctypes* a user may read (``boot.user.can_read``);
it cannot see User Permissions or a doctype's own row rules. On production that matters:

* **Employee.** 14 of the 17 planner users read Employee through a Custom DocPerm and each holds one
  Employee User Permission, their own record, applied to all doctypes. Every other Employee is "Not
  permitted" for them, whatever their role.
* **Planner Resource** links to Employee and does not ignore user permissions, so the same people are
  refused every Planner Resource that names someone else.
* **Travel Trip** shows a non-coordinator only the trips they are on (``travel_management/permissions``).
* **Sapphire Maintenance Record** shows the Projects User role, which the Project Planner admits, only its
  customers' submitted visits (``sapphire_maintenance/permissions``).

So the planners ask here, once per doctype per answer, which of the records they are about to draw this
person may open, and mark each item: a person's ``open_as`` (``"Employee"``, ``"Planner Resource"`` or
None: the page shows their week, as a tap did before), a booking's ``open`` (False: the page keeps its old
answer). ``frappe.get_list`` applies role permissions, User Permissions and ``permission_query_conditions``
together, which is the rule the form itself enforces.

Not asked: Task, Project and Asset. Checked on production (2026-10-27, read-only): their only links to
Employee ignore user permissions (Asset.custodian; Project's owner and technical lead through Property
Setters), there is one Company, the two Project User Permissions are outside users (applied to Task) who
hold no planner role, and strict user permissions are off. A role that cannot read one of them at all is
already answered in the browser by ``frappe.model.can_read``.
"""

import frappe

#: The record behind each kind of booking the planners draw (a projected visit is its contract's).
BOOKING_DOCTYPES = {
	"visit": "Sapphire Maintenance Record",
	"travel": "Travel Trip",
}
PROJECTED_DOCTYPE = "Sapphire Maintenance Contract"


def openable(doctype, names):
	"""The subset of ``names`` this user may open, as a set. One query; none for no names.

	A role with no read on the doctype (or only select) opens none of it, and is answered before the
	query: ``frappe.get_list`` refuses through ``frappe.throw``, which writes "Insufficient Permission"
	into the message log before it raises, so the Desk would show it as a red dialog on every planner
	load even though the error is caught here. Whatever a refused or failed query still left in the log
	is cut back off (``_drop_messages``): the planner asked this on its own, nobody pressed anything."""
	wanted = sorted({str(n) for n in names or () if n})
	if not wanted:
		return set()
	if not frappe.has_permission(doctype, "read"):
		return set()
	mark = _message_count()
	try:
		rows = frappe.get_list(doctype, filters={"name": ["in", wanted]}, pluck="name", limit_page_length=0)
	except frappe.PermissionError:
		_drop_messages(mark)
		return set()
	except Exception:
		# Deciding what a tap opens must never cost a planner its answer: nothing opens (a tap does what
		# it did before), and the error goes to the Error Log.
		_drop_messages(mark)
		frappe.log_error(title=f"Planner: which {doctype} records open", message=frappe.get_traceback())
		return set()
	return {str(name) for name in rows or ()}


def _message_count():
	log = getattr(getattr(frappe, "local", None), "message_log", None)
	return len(log) if isinstance(log, list) else None


def _drop_messages(mark):
	"""Cut the message log back to ``mark`` entries: what the refused query wrote into it."""
	log = getattr(getattr(frappe, "local", None), "message_log", None)
	if mark is not None and isinstance(log, list) and len(log) > mark:
		frappe.local.message_log = log[:mark]


def open_as(person, employees, resources, resource_key="name"):
	"""What a tap on ``person`` opens: their Employee, else their Planner Resource, else None (their week)."""
	if person.get("employee") and person["employee"] in employees:
		return "Employee"
	if person.get(resource_key) and person[resource_key] in resources:
		return "Planner Resource"
	return None


def mark_people(people, resource_key="name"):
	"""Set ``open_as`` on each person dict (``employee`` and ``resource_key`` are read). Returns them."""
	people = [p for p in people or () if isinstance(p, dict)]
	employees = openable("Employee", [p.get("employee") for p in people])
	resources = openable("Planner Resource", [p.get(resource_key) for p in people])
	for person in people:
		person["open_as"] = open_as(person, employees, resources, resource_key)
	return people


def booking_doctype(booking):
	"""The doctype ``open`` is decided for, or None when the browser's own check is enough."""
	kind = booking.get("kind")
	if kind == "visit" and booking.get("projected"):
		return PROJECTED_DOCTYPE
	return BOOKING_DOCTYPES.get(kind)


def mark_bookings(bookings):
	"""Set ``open`` on each booking whose record is decided per row (a visit, a contract, a trip).

	Everything else is left as it is: ``records.js`` reads a missing ``open`` as "ask the browser"."""
	bookings = [b for b in bookings or () if isinstance(b, dict)]
	wanted = {}
	for booking in bookings:
		doctype = booking_doctype(booking)
		if doctype and booking.get("ref"):
			wanted.setdefault(doctype, set()).add(booking["ref"])
	allowed = {doctype: openable(doctype, names) for doctype, names in wanted.items()}
	for booking in bookings:
		doctype = booking_doctype(booking)
		if doctype:
			booking["open"] = bool(booking.get("ref")) and booking["ref"] in allowed.get(doctype, set())
	return bookings


def day_bookings(days):
	"""Every booking in an engine ``days`` map (``{resource: {day: {"bookings": [...]}}}``)."""
	return [
		booking
		for per_day in (days or {}).values()
		for cell in (per_day or {}).values()
		for booking in (cell or {}).get("bookings") or []
	]
