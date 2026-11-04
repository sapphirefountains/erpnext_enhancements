"""The icon rail's data (v1.793.0; Nik, 2026-11-03): what this person opens most, and the counts the
sidebar badges show.

Serves ``public/js/global_enhancements/sidebar_rail.js``, which puts the rows a person uses most at
the top of each module's sidebar and draws a count on the Task and Pending Confirmations rows. One
GET, read-only, scoped to the session user:

* ``usage`` -- this person's own ``tabRoute History`` over the last :data:`DAYS`, as ``{route: n}``,
  the busiest :data:`MAX_ROUTES` routes. frappe's ``router_history.js`` records lists, workspaces,
  pages and reports and skips forms, so a form never counts here; the client keeps its own tally
  for the rest of the day and seeds from this once a day.
* ``badges`` -- ``tasks`` (the open Tasks assigned to this person, and how many of them are past
  their expected end), ``pending_actions`` (the AI Pending Actions still Pending that this person
  may see), each only where the person may read the doctype. A count that fails is left out rather
  than failing the answer: a badge is a convenience.

``usage=0`` skips the Route History tally. The client seeds from it once a day and then polls the
badges once a minute, and a 90-day ``GROUP BY`` has no business running on every poll.

Nothing here writes. Tab-indented.
"""

import frappe
from frappe.utils import add_days, cint, nowdate, today

DAYS = 90
MAX_ROUTES = 500
# The badge reads "99+" past this, so a permission-aware count never fetches more than one page.
BADGE_CAP = 99
# ERPNext 16.52.1's Task statuses that are still work: everything but Completed and Cancelled, plus
# Template, which is never assigned.
OPEN_TASK_STATUSES = ("Open", "Working", "Pending Review", "Overdue")


@frappe.whitelist(methods=["GET"])
def get_rail_data(usage=1):
	"""This person's route counts and badge numbers, for the sidebar rail.

	``usage=0`` (the client's minute poll) answers the badges only; ``usage`` is then ``None``."""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(frappe._("Not permitted"), frappe.PermissionError)
	want_usage = cint(usage) if usage not in (None, "") else 1
	return {"days": DAYS, "usage": route_usage(user) if want_usage else None, "badges": badges(user)}


def route_usage(user, days=DAYS, limit=MAX_ROUTES):
	"""``{route: visits}`` for one person over the last ``days`` days, busiest first, at most ``limit``."""
	try:
		if not frappe.db.table_exists("Route History"):
			return {}
		rows = frappe.db.sql(
			"""
			select route, count(*) as n
			from `tabRoute History`
			where user = %(user)s and creation >= %(since)s
			group by route
			order by n desc
			limit %(limit)s
			""",
			{"user": user, "since": add_days(nowdate(), -int(days)), "limit": int(limit)},
			as_dict=True,
		)
	except Exception:
		frappe.log_error(title="Sidebar rail: route usage not read", message=frappe.get_traceback())
		return {}
	return {row.route: int(row.n) for row in rows if row.get("route")}


def badges(user):
	"""The numbers the badged rows show, by key; a doctype the person may not read is left out."""
	out = {}
	try:
		if frappe.has_permission("Task", "read"):
			filters = {"status": ["in", list(OPEN_TASK_STATUSES)], "_assign": ["like", f"%{user}%"]}
			count = int(frappe.db.count("Task", filters) or 0)
			overdue = 0
			if count:
				overdue = int(frappe.db.count("Task", dict(filters, exp_end_date=["<", today()])) or 0)
			out["tasks"] = {"count": count, "overdue": overdue}
	except Exception:
		frappe.log_error(title="Sidebar rail: task badge not read", message=frappe.get_traceback())
	try:
		if frappe.db.exists("DocType", "AI Pending Action") and frappe.has_permission("AI Pending Action", "read"):
			# Through the permission layer, never `db.count`: the doctype grants role All read with
			# `if_owner`, which `db.count` knows nothing about, so a plain count handed every person
			# the whole site's pending actions while their list showed only their own. `get_list`
			# applies the owner constraint (and user permissions); one page past the cap is enough.
			names = frappe.get_list(
				"AI Pending Action", filters={"status": "Pending"}, pluck="name", limit_page_length=BADGE_CAP + 1
			)
			out["pending_actions"] = len(names or [])
	except Exception:
		frappe.log_error(title="Sidebar rail: pending actions badge not read", message=frappe.get_traceback())
	return out
