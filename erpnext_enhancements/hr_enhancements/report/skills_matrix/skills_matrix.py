# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Skills Matrix — people down the side, qualifications across the top.

The question this answers is **"who can I send?"**, and until now nothing in the
app could. `Training Completion Matrix` reports what has already happened, one
course at a time, and `training/compliance.py` warns about an individual at the
moment of dispatch — and, as it happens, could never fire, because it only ever
looked at people who already had an assignment.

This is the forward-looking version and it is deliberately one grid rather than
two. A technician is qualified by a mixture of **internal courses** and
**external credentials**, and a manager scheduling a basin drain does not care
which system a ticket came out of. Splitting them into two reports would mean
every scheduling decision needs two screens and a mental join.

Each cell is one of four words, and the wording matters more than the colour:

* ``Current`` — held and not close to lapsing.
* ``Expiring`` — held, inside the ninety-day horizon. **Still qualified today.**
  Reading this as "cannot go" would make the horizon do the opposite of its job.
* ``Lapsed`` — held once, not now. Different from never, and worth seeing as
  different: a lapsed forklift ticket is a renewal, a missing one is a course.
* ``Never`` — no record either way.

Deliberately not a compliance verdict. It does not say who *may* be dispatched —
that is `training/compliance.py`'s advisory, and it stays warn-only. This is the
picture you look at on Friday when you are planning next week.
"""

import frappe
from frappe import _
from frappe.utils import add_days, getdate, today

CURRENT = "Current"
EXPIRING = "Expiring"
LAPSED = "Lapsed"
NEVER = "Never"

HORIZON_DAYS = 90


def execute(filters=None):
	filters = frappe._dict(filters or {})
	employees = _employees(filters)
	if not employees:
		return _columns([]), [], None

	qualifications = _qualifications(filters)
	held = _held(employees, qualifications)

	columns = _columns(qualifications)
	rows = []
	scoped = 0
	for employee in employees:
		row = {
			"employee": employee.name,
			"employee_name": employee.employee_name,
			"position": employee.get("custom_position") or employee.get("designation") or "",
			"department": employee.department or "",
		}
		# Counted against what this person's RUNG asks for, not against every
		# qualification in the company. Every column is still drawn -- the grid is
		# the point -- but a cell outside their requirements is not a gap.
		#
		# Before WI-073 this counted all of them, so a Junior Technician's gap
		# number included the Finance & Accounting Manager's courses and every
		# credential any role has ever needed. The number was roughly "how many
		# qualifications exist", which is nearly the same for everybody, so the
		# "who needs the most work" ordering below was noise wearing the shape of a
		# priority list. Nobody would have noticed: it sorted, it just sorted on
		# nothing.
		required = required_keys(employee.get("custom_position"))
		gaps = 0
		for key, _label in qualifications:
			state = held.get((employee.name, key), NEVER)
			row[_fieldname(key)] = state
			if state in (LAPSED, NEVER) and (required is None or key in required):
				gaps += 1
		# Sorted on later, so a manager opens this already looking at whoever needs
		# the most work rather than at whoever is alphabetically first.
		row["gaps"] = gaps
		if required is not None:
			scoped += 1
		rows.append(row)

	rows.sort(key=lambda r: (-r["gaps"], r["employee_name"] or ""))
	return columns, rows, _scope_message(scoped, len(rows))


def _scope_message(scoped, total):
	"""Say out loud when the gap column is not scoped to anybody's rung.

	Without this the report looks identical either way: a priority order computed
	from what each rung asks for, and one computed from "how many qualifications
	exist in the company", draw the same screen. The second is noise, and silent
	noise that sorts is worse than an empty table — somebody plans a week on it.

	Reported rather than refused, because the grid itself is still true and useful
	with no requirements configured; it is only the ordering that is meaningless.
	"""
	if not total:
		return None
	if scoped == 0:
		return _(
			"<b>Gap counts are company-wide.</b> No Position on this site lists what it "
			"requires, so the <i>Gaps</i> column counts every qualification in the company "
			"for everybody and the row order means little. Fill in <i>What this rung asks "
			"for</i> on a Position to scope it."
		)
	if scoped < total:
		return _(
			"Gap counts are scoped to each person's rung for {0} of {1} people. The rest "
			"have no Position, or their Position lists no requirements, so their gaps are "
			"counted company-wide."
		).format(scoped, total)
	return None


# ---------------------------------------------------------------------- pieces


def _employees(filters):
	where = {"status": "Active"}
	if filters.get("department"):
		where["department"] = filters.department
	fields = ["name", "employee_name", "department", "designation"]
	if frappe.db.has_column("Employee", "custom_position"):
		fields.append("custom_position")
	return frappe.get_all("Employee", filters=where, fields=fields, order_by="employee_name asc")


def _qualifications(filters):
	"""``(key, label)`` for every column, in a stable order.

	Keys are namespaced by source (``course:``/``cred:``) rather than by bare name,
	because a Credential Type and a Training Course are allowed to share a title
	and a collision here would silently merge two columns into one — which reads as
	everybody suddenly being qualified.
	"""
	out = []
	if not filters.get("credentials_only"):
		for row in frappe.get_all(
			"Training Course",
			filters={"status": "Published", "weight": "Required"},
			fields=["name", "course_title"],
			order_by="course_title asc",
		):
			out.append((f"course:{row.name}", row.course_title or row.name))

	if not filters.get("courses_only") and frappe.db.exists("DocType", "Credential Type"):
		for row in frappe.get_all(
			"Credential Type", filters={"is_active": 1}, pluck="name", order_by="name asc"
		):
			out.append((f"cred:{row}", row))
	return out


def _held(employees, qualifications):
	"""``{(employee, key): state}``, built from two queries rather than N×M.

	Sixteen people times twenty qualifications is 320 cells; asking the database
	once per cell would be 320 round trips for a screen somebody opens on a Friday
	afternoon.
	"""
	by_name = {e.name: e for e in employees}
	users = {
		e.name: frappe.db.get_value("Employee", e.name, "user_id") for e in employees
	}
	user_to_employee = {u: name for name, u in users.items() if u}
	wanted = {key for key, _ in qualifications}
	held = {}

	# --- internal courses, from the completion record
	if any(k.startswith("course:") for k in wanted):
		horizon = getdate(add_days(today(), HORIZON_DAYS))
		for row in frappe.get_all(
			"Training Completion",
			filters={"docstatus": 1, "user": ["in", list(user_to_employee)] or [""]},
			fields=["user", "course", "status", "expires_on"],
			order_by="completed_on asc",
		):
			employee = user_to_employee.get(row.user)
			key = f"course:{row.course}"
			if not employee or key not in wanted:
				continue
			held[(employee, key)] = _course_state(row, horizon)

	# --- external credentials
	if any(k.startswith("cred:") for k in wanted) and frappe.db.exists(
		"DocType", "Employee Credential"
	):
		for row in frappe.get_all(
			"Employee Credential",
			filters={"employee": ["in", list(by_name)] or [""]},
			fields=["employee", "credential_type", "status"],
			order_by="expires_on asc",
		):
			key = f"cred:{row.credential_type}"
			if key not in wanted:
				continue
			state = {
				"Valid": CURRENT,
				"Expiring": EXPIRING,
				"Expired": LAPSED,
				"Revoked": LAPSED,
			}.get(row.status, NEVER)
			# Best wins: somebody who renewed keeps two rows, and the current one is
			# the true answer to "can they do this".
			existing = held.get((row.employee, key))
			if existing is None or _rank(state) > _rank(existing):
				held[(row.employee, key)] = state

	return held


def _course_state(row, horizon):
	if row.status in ("Revoked", "Expired", "Superseded"):
		return LAPSED
	if row.expires_on and getdate(row.expires_on) < getdate(today()):
		return LAPSED
	if row.expires_on and getdate(row.expires_on) <= horizon:
		return EXPIRING
	return CURRENT


def _rank(state):
	return {NEVER: 0, LAPSED: 1, EXPIRING: 2, CURRENT: 3}.get(state, 0)


def _fieldname(key):
	"""A column fieldname Frappe will accept, derived from the namespaced key."""
	return frappe.scrub(key.replace(":", "_"))[:140]


def _columns(qualifications):
	columns = [
		{"fieldname": "employee_name", "label": _("Person"), "fieldtype": "Data", "width": 180},
		{"fieldname": "position", "label": _("Position"), "fieldtype": "Data", "width": 150},
		{"fieldname": "department", "label": _("Department"), "fieldtype": "Data", "width": 130},
		{"fieldname": "gaps", "label": _("Gaps"), "fieldtype": "Int", "width": 70},
	]
	for key, label in qualifications:
		columns.append(
			{
				"fieldname": _fieldname(key),
				"label": label,
				"fieldtype": "Data",
				"width": 130,
			}
		)
	return columns


def required_keys(position):
	"""The column keys this rung actually asks for, or None for "not configured".

	**None and empty-set mean opposite things and the distinction is the whole
	point.** None is "nobody has written down what this rung requires", and the
	honest response to that is to count every column as before rather than to
	report the person as having no gaps -- which is what an empty set would do, and
	which would turn an unconfigured ladder into a clean bill of health for the
	entire company.

	That is the failure this release keeps meeting: a check that reports all-clear
	because it is looking at nothing. Here it fails the other way on purpose.

	Keys are namespaced exactly as ``_qualifications`` builds them, so a Credential
	Type and a Training Course that share a title cannot collide.
	"""
	if not position:
		return None
	try:
		rows = frappe.get_cached_doc("Position", position).get("requirements") or []
	except frappe.DoesNotExistError:
		return None
	if not rows:
		return None

	keys = set()
	for row in rows:
		if row.requirement_type == "Credential":
			if row.credential_type:
				keys.add(f"cred:{row.credential_type}")
		elif row.training_course:
			# A Sign-off requirement names a course too, and the matrix has one
			# column per course -- so both kinds land on the same key rather than
			# inventing a column the grid does not draw.
			keys.add(f"course:{row.training_course}")
	return keys or None
