"""Backend for the Maintenance Planner desk page (``/app/maintenance-planner``).

The planner is a month/week calendar of maintenance visits that people move by
dragging cards. :func:`get_planner` feeds it three kinds of card:

* **Visit records** (Sapphire Maintenance Record). An open draft sits on its
  ``scheduled_visit_date``; a visit pending review or submitted sits on the day
  the work was done. Only a draft that nobody has started (no ``visit_date``)
  can be moved, and :func:`move_visit` moves it by saving the record under the
  mover's own permissions, so the workflow and the validate hooks all apply.
* **Projected visits**: the ones the nightly scheduler
  (``tasks.generate_predictive_maintenance_records``) will draft later. They are
  worked out from each Active contract's feature rows, ``next_visit_date`` then
  ``frequency``, through the same winter pause as the roll-forward
  (``maintenance_scheduling.defer_for_winter``). Without them the calendar would
  go blank a week out, because drafts only exist for the next seven days. The
  first card of each series is a stored date, the contract's next visit, so
  :func:`move_projected` can move it. The cards after it are arithmetic and stay
  put: they follow wherever the visit before them actually happens.
* **Seasonal visits** that are not drafted yet, on the 1st of their month (the
  scheduler drafts them that day). Not movable.

Moving a projected visit into the drafting window drafts it right away, on the
day it was dropped. Otherwise a visit dragged onto today would only be drafted
by tomorrow's run, which clamps an overdue date forward to tomorrow.

The pure pieces (series, site names, which feature rows a move touches) take
plain values so ``tests/test_maintenance_planner.py`` runs them without a bench.
"""

import datetime
import re

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, formatdate, getdate, nowdate

# Who may open the planner: exactly the roles that read every maintenance record
# (sapphire_maintenance.permissions.VIEW_ALL_ROLES), because the record query below
# runs as raw SQL. Kept as a literal so the bench-free tests need no permissions stub;
# test_maintenance_planner asserts the two sets agree.
PLANNER_ROLES = {"System Manager", "Projects Manager", "Maintenance Supervisor", "Maintenance User"}

# A month view shows six weeks; anything much longer is a mistake or a scrape.
MAX_RANGE_DAYS = 100
# A daily series over a long range is the worst case. Far more than one range needs.
MAX_SERIES_LENGTH = 400

PENDING_STATE = "Pending Review"

# Employee designations of the people who do visits ("Junior Technician", "Senior Technician").
TECHNICIAN_DESIGNATION = "Technician"

MONTHS = [
	"January",
	"February",
	"March",
	"April",
	"May",
	"June",
	"July",
	"August",
	"September",
	"October",
	"November",
	"December",
]

# Project titles carry the contract boilerplate: "Highlands Maintenance Contract",
# "The Charles - Fountain Maintenance Contract", "Red Butte Garden Maintenance 2026".
# On a calendar card only the site name earns its space.
_SITE_SUFFIX = re.compile(
	r"\s*[-–—:]?\s*(?:fountain\s+)?(?:preventative\s+|preventive\s+)?maintenance(?:\s+contract)?(?:\s+\d{4})?\s*$",
	re.IGNORECASE,
)


# ---------------------------------------------------------------------- pure helpers


def short_site_name(title):
	"""A project title without the maintenance-contract boilerplate."""
	if not title:
		return title
	short = _SITE_SUFFIX.sub("", str(title)).strip(" -–—:")
	return short or title


def feature_label(serial_no):
	"""``MAINT-HARDWARE-East-Courtyard`` → ``East Courtyard``."""
	if not serial_no:
		return ""
	text = re.sub(r"^MAINT-[^-]+-", "", str(serial_no)).replace("-", " ").strip()
	return text or serial_no


def project_series(first, step, start, end, limit=MAX_SERIES_LENGTH):
	"""The dates of a visit series that fall between ``start`` and ``end``.

	``first`` is the next visit; ``step(date)`` returns the one after it, or
	None when the series stops. A step that fails to move forward also stops
	it, so a blank frequency can never spin.
	"""
	dates = []
	current = getdate(first) if first else None
	start, end = getdate(start), getdate(end)
	for _step in range(limit):
		if not current or current > end:
			break
		if current >= start:
			dates.append(current)
		following = step(current)
		following = getdate(following) if following else None
		if not following or following <= current:
			break
		current = following
	return dates


def make_step(contract, frequencies):
	"""``step`` for :func:`project_series`: the soonest next visit over ``frequencies``.

	One frequency for a Per Feature row. Every row's frequency for a Per Site
	Visit contract, because one site visit rolls every feature forward from the
	same day (``update_next_visit_dates``) and the site is due again as soon as
	the first of them is.
	"""
	from erpnext_enhancements.api.maintenance_scheduling import calculate_next_date, defer_for_winter

	frequencies = [f for f in frequencies if f]

	def step(day):
		candidates = [defer_for_winter(contract, calculate_next_date(day, f)) for f in frequencies]
		candidates = [getdate(c) for c in candidates if c]
		return min(candidates) if candidates else None

	return step


def series_end(contract, end):
	"""``end``, cut short at the contract's end date when it will not renew."""
	end = getdate(end)
	if contract.get("end_date") and (not contract.get("auto_renew") or contract.get("non_renewal_notice")):
		return min(end, getdate(contract.get("end_date")))
	return end


def build_projections(contract, open_drafts, seasonal, start, end, today):
	"""The projected (not yet drafted) visits for one Active contract.

	Args:
		contract: the contract doc (``covered_features``, ``visit_shape``, dates).
		open_drafts: the scheduler's dedup keys for open *regular* drafts mapped to
			that draft's date (or None): the contract name for a Per Site Visit
			contract, ``(project, serial_no)`` for a Per Feature one. An open draft
			stands for the next visit, so the series continues from it.
		seasonal: ``[{label, target_month, last_generated_year, drafted}]``.
		start, end, today: dates.

	Returns:
		list[dict] in date order. ``movable`` marks the card that is the
		contract's stored next visit; ``from_date`` is that stored date, which
		:func:`move_projected` checks before it writes.
	"""
	start, end, today = getdate(start), getdate(end), getdate(today)
	last = series_end(contract, end)
	starts_on = getdate(contract.get("start_date")) if contract.get("start_date") else None
	rows = [
		row
		for row in (contract.get("covered_features") or [])
		if row.get("frequency") or row.get("next_visit_date")
	]
	entries = []

	def add_series(rows_in_series, key, serial_no):
		dated = [getdate(row.get("next_visit_date")) for row in rows_in_series if row.get("next_visit_date")]
		if not dated:
			return
		step = make_step(contract, [row.get("frequency") for row in rows_in_series])
		if key in open_drafts:
			# The draft is the next visit; what follows it rolls from the day it is done.
			drafted_on = open_drafts.get(key)
			base = max(getdate(drafted_on), today) if drafted_on else today
			first, stored = step(base), None
		else:
			first = stored = min(dated)
			if starts_on and first < starts_on:
				first = starts_on
		for index, day in enumerate(project_series(first, step, start, last)):
			movable = bool(stored) and index == 0 and day == first
			entries.append(
				{
					"kind": "projected",
					"key": f"{contract.name}|{serial_no or ''}|{day}",
					"contract": contract.name,
					"project": contract.get("project"),
					"serial_no": serial_no,
					"frequency": ", ".join(
						sorted({row.get("frequency") for row in rows_in_series if row.get("frequency")})
					),
					"date": str(day),
					"movable": movable,
					"from_date": str(stored) if movable else None,
					"overdue": day < today,
				}
			)

	if contract.get("visit_shape") == "Per Site Visit":
		add_series(rows, contract.name, None)
	else:
		for row in rows:
			add_series([row], (contract.get("project"), row.get("serial_no")), row.get("serial_no"))

	for visit in seasonal or []:
		if visit.get("drafted") or visit.get("target_month") not in MONTHS:
			continue
		month = MONTHS.index(visit["target_month"]) + 1
		for year in range(start.year, end.year + 1):
			if (visit.get("last_generated_year") or 0) >= year:
				continue
			# The scheduler drafts it on its first run in the target month. In the
			# current month that run is the next one; a month already gone is
			# skipped for the year, so nothing would be drafted.
			day = datetime.date(year, month, 1)
			if day <= today:
				if (year, month) != (today.year, today.month):
					continue
				day = today
			if not (start <= day <= last) or (starts_on and day < starts_on):
				continue
			entries.append(
				{
					"kind": "projected",
					"key": f"{contract.name}|{visit['label']}|{day}",
					"contract": contract.name,
					"project": contract.get("project"),
					"serial_no": None,
					"label": visit["label"],
					"frequency": _("Once a year"),
					"date": str(day),
					"movable": False,
					"from_date": None,
					"overdue": False,
				}
			)

	entries.sort(key=lambda entry: entry["date"])
	return entries


def rows_to_move(contract, source, target, serial_no=None):
	"""The feature rows whose ``next_visit_date`` a projected move rewrites.

	Per Feature: the one row, and only while it still holds ``source``.
	Per Site Visit: one visit covers every feature, so the card is the site's
	soonest next date. Moving it carries every row due on that day, plus any
	row due before the new day: that visit will see to those features too.
	An empty list means the card is stale (someone moved it, or it got drafted).
	"""
	source, target = getdate(source), getdate(target)
	rows = [row for row in (contract.get("covered_features") or []) if row.get("next_visit_date")]
	if contract.get("visit_shape") == "Per Site Visit":
		if not rows or min(getdate(row.get("next_visit_date")) for row in rows) != source:
			return []
		return [
			row
			for row in rows
			if getdate(row.get("next_visit_date")) == source or getdate(row.get("next_visit_date")) < target
		]
	return [
		row
		for row in rows
		if row.get("serial_no") == serial_no and getdate(row.get("next_visit_date")) == source
	]


# ---------------------------------------------------------------------- endpoints


def _require_planner():
	if frappe.session.user == "Administrator":
		return
	if not PLANNER_ROLES & set(frappe.get_roles()):
		frappe.throw(_("Only maintenance staff can use the planner."), frappe.PermissionError)


@frappe.whitelist()
def get_planner(start, end):
	"""Every card between ``start`` and ``end`` (inclusive), plus the unscheduled drafts.

	Technician filtering happens in the browser: the whole schedule is a few
	hundred cards at most, and switching people should not cost a round trip.
	"""
	_require_planner()
	start, end = getdate(start), getdate(end)
	if end < start:
		frappe.throw(_("The end date is before the start date."))
	if date_diff(end, start) > MAX_RANGE_DAYS:
		frappe.throw(_("Pick a range of {0} days or less.").format(MAX_RANGE_DAYS))
	today = getdate(nowdate())

	can_move_visits = bool(frappe.has_permission("Sapphire Maintenance Record", "write"))
	can_move_projected = bool(frappe.has_permission("Sapphire Maintenance Contract", "write"))

	visits = [_visit_card(row, today, can_move_visits) for row in _records_between(start, end)]
	unscheduled = [_visit_card(row, today, can_move_visits) for row in _unscheduled_drafts()]
	projected = _projections(start, end, today)
	for card in projected:
		card["movable"] = card["movable"] and can_move_projected

	_decorate(visits + unscheduled + projected)
	technicians = _technicians(visits + unscheduled + projected)
	return {
		"start": str(start),
		"end": str(end),
		"today": str(today),
		"visits": visits,
		"unscheduled": unscheduled,
		"projected": projected,
		"technicians": technicians,
		"bookings": _project_bookings(technicians, start, end),
		"can_move_visits": can_move_visits,
		"can_move_projected": can_move_projected,
	}


# Booking kinds the Maintenance Planner shows read-only. Visits are its own cards.
FOREIGN_BOOKING_KINDS = ("task", "rental", "travel")


def _project_bookings(technicians, start, end):
	"""Each technician's free hours and non-visit bookings, from the shared availability engine.

	``{user: {"YYYY-MM-DD": {"capacity", "booked", "free", "off", "conflicts", "items"}}}``
	where ``items`` are the project tasks, rental crew tasks and travel days that use the
	technician's hours (``{kind, ref, label, project, hours, slot}``). Maintenance and
	Projects share technicians, and a technician's free hours must read the same in both
	planners, so the numbers come from ``project_enhancements.crew_availability`` and are
	never computed here.

	A technician with no active Planner Resource is simply absent: the planner then shows no
	free hours for them rather than a wrong number. A failure in the engine returns ``{}``
	(logged), because a bug on the project side must never blank the maintenance calendar.
	The engine is imported lazily: it imports this module back for the visit projections.
	"""
	users = [t["user"] for t in technicians or [] if t.get("user")]
	if not users:
		return {}
	try:
		from erpnext_enhancements.project_enhancements.crew_availability import availability

		data = availability(start, end)
		user_to_resource = data.get("user_to_resource") or {}
		out = {}
		for user in users:
			resource = user_to_resource.get(user)
			per_day = (data.get("days") or {}).get(resource) if resource else None
			if not per_day:
				continue
			out[user] = {
				day: {
					"capacity": cell.get("capacity"),
					"booked": cell.get("booked"),
					"free": cell.get("free"),
					"off": cell.get("off"),
					"conflicts": list(cell.get("conflicts") or []),
					"items": [
						{
							"kind": b.get("kind"),
							"ref": b.get("ref"),
							"label": b.get("label"),
							"project": b.get("project"),
							"hours": b.get("hours"),
							"slot": b.get("slot"),
						}
						for b in cell.get("bookings") or []
						if b.get("kind") in FOREIGN_BOOKING_KINDS
					],
				}
				for day, cell in per_day.items()
			}
		return out
	except Exception:
		frappe.log_error(title="Maintenance Planner: project bookings failed", message=frappe.get_traceback())
		return {}


def _records_between(start, end):
	# Raw SQL for the date expression, after the role gate in _require_planner: every
	# planner role sees every record (sapphire_maintenance.permissions).
	return frappe.db.sql(
		"""
		SELECT * FROM (
			SELECT
				name, project, customer, maintenance_contract, serial_no, visit_label,
				technician, scheduled_visit_date, visit_date, docstatus, workflow_state,
				completion_percent, has_out_of_range_readings, modified,
				CASE
					WHEN docstatus = 1 OR workflow_state = %(pending)s
						THEN COALESCE(visit_date, scheduled_visit_date, DATE(modified))
					ELSE COALESCE(scheduled_visit_date, visit_date)
				END AS plan_date
			FROM `tabSapphire Maintenance Record`
			WHERE docstatus < 2
		) visits
		WHERE plan_date BETWEEN %(start)s AND %(end)s
		ORDER BY plan_date, name
		LIMIT 2000
		""",
		{"start": start, "end": end, "pending": PENDING_STATE},
		as_dict=True,
	)


def _unscheduled_drafts():
	return frappe.db.sql(
		"""
		SELECT
			name, project, customer, maintenance_contract, serial_no, visit_label,
			technician, scheduled_visit_date, visit_date, docstatus, workflow_state,
			completion_percent, has_out_of_range_readings, modified, NULL AS plan_date
		FROM `tabSapphire Maintenance Record`
		WHERE docstatus = 0
			AND IFNULL(workflow_state, '') != %(pending)s
			AND scheduled_visit_date IS NULL
			AND visit_date IS NULL
		ORDER BY creation
		LIMIT 200
		""",
		{"pending": PENDING_STATE},
		as_dict=True,
	)


def _visit_card(row, today, can_move):
	if row.docstatus == 1:
		status = "done"
	elif row.workflow_state == PENDING_STATE:
		status = "pending"
	else:
		status = "draft"
	day = getdate(row.plan_date) if row.plan_date else None
	return {
		"kind": "visit",
		"name": row.name,
		"key": row.name,
		"date": str(day) if day else None,
		"project": row.project,
		"contract": row.maintenance_contract,
		"customer": row.customer,
		"serial_no": row.serial_no,
		"label": row.visit_label,
		"technician": row.technician,
		"status": status,
		"completion": row.completion_percent or 0,
		"flagged": bool(row.has_out_of_range_readings),
		# Started visits (a visit_date) record work already done or underway.
		"movable": bool(can_move and status == "draft" and not row.visit_date),
		"started": bool(row.visit_date),
		"modified": str(row.modified),
		"overdue": bool(status == "draft" and day and day < today),
	}


def _projections(start, end, today):
	from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract import (
		iter_seasonal_visits,
	)

	contracts = frappe.get_all("Sapphire Maintenance Contract", filters={"status": "Active"}, pluck="name")
	if not contracts:
		return []

	open_drafts = {}
	open_labels = set()
	for draft in frappe.get_all(
		"Sapphire Maintenance Record",
		filters={"docstatus": 0},
		fields=["maintenance_contract", "project", "serial_no", "visit_label", "scheduled_visit_date"],
	):
		if draft.visit_label:
			open_labels.add((draft.maintenance_contract, draft.visit_label))
			continue
		# The scheduler's two dedup keys (tasks.generate_predictive_maintenance_records).
		for key in (draft.maintenance_contract, (draft.project, draft.serial_no)):
			if key and (key not in open_drafts or not open_drafts[key]):
				open_drafts[key] = draft.scheduled_visit_date

	held = set(
		frappe.get_all("Customer", filters={"custom_service_hold": 1}, pluck="name")
		if frappe.db.has_column("Customer", "custom_service_hold")
		else []
	)

	entries = []
	for name in contracts:
		contract = frappe.get_doc("Sapphire Maintenance Contract", name)
		seasonal = [
			{
				"label": visit["label"],
				"target_month": visit["target_month"],
				"last_generated_year": visit["last_generated_year"],
				"drafted": (contract.name, visit["label"]) in open_labels,
			}
			for visit in iter_seasonal_visits(contract)
		]
		for entry in build_projections(contract, open_drafts, seasonal, start, end, today):
			entry["on_hold"] = contract.customer in held
			entry["customer"] = contract.customer
			entries.append(entry)
	return entries


def _decorate(cards):
	"""Site names, feature names and default technicians, one query each."""
	from erpnext_enhancements.api.maintenance_dispatch import default_technician_for

	projects = {card["project"] for card in cards if card.get("project")}
	titles = (
		dict(
			frappe.get_all(
				"Project",
				filters={"name": ["in", list(projects)]},
				fields=["name", "project_name"],
				as_list=True,
			)
		)
		if projects
		else {}
	)
	defaults = {project: default_technician_for(project) for project in projects}
	for card in cards:
		title = titles.get(card.get("project")) or card.get("project") or card.get("customer") or ""
		card["site_full"] = title
		card["site"] = short_site_name(title)
		card["feature"] = feature_label(card.get("serial_no"))
		if card["kind"] == "projected":
			card["technician"] = defaults.get(card.get("project"))


def _technicians(cards):
	"""The people visits go to, plus anyone already on a card.

	Holding the Maintenance User role is not enough: on this site the office
	holds it too (Lisa reviews visits before billing, she does not do them), as
	do service accounts. A technician is an active employee whose designation
	says so (Junior/Senior Technician) and who holds the role.
	"""
	holders = frappe.get_all(
		"Has Role", filters={"role": "Maintenance User", "parenttype": "User"}, pluck="parent"
	)
	staff = set(
		frappe.get_all(
			"Employee",
			filters={
				"user_id": ["in", holders or [""]],
				"status": "Active",
				"designation": ["like", f"%{TECHNICIAN_DESIGNATION}%"],
			},
			pluck="user_id",
		)
	)
	staff |= {card["technician"] for card in cards if card.get("technician")}
	if not staff:
		return []
	users = frappe.get_all(
		"User",
		filters={"name": ["in", list(staff)]},
		fields=["name", "full_name", "enabled"],
		order_by="full_name asc",
	)
	return [{"user": u.name, "name": u.full_name or u.name, "enabled": bool(u.enabled)} for u in users]


@frappe.whitelist()
def move_visit(record, date=None, technician=None, modified=None):
	"""Reschedule an open draft visit and/or hand it to another technician.

	Saved through the record under the caller's permissions, so the workflow
	and both validate warnings run (time off, training). Their messages come
	back as ``warnings`` for the planner to show beside the card, rather than
	as a dialog after every drag.
	"""
	_require_planner()
	doc = frappe.get_doc("Sapphire Maintenance Record", record)
	doc.check_permission("write")
	state = doc.get("workflow_state") or "Draft"
	if doc.docstatus != 0 or state == PENDING_STATE:
		frappe.throw(
			_("{0} is already finished ({1}), so it stays on the day it was done.").format(doc.name, _(state))
		)
	if modified and str(doc.modified) != str(modified):
		frappe.throw(
			_("This visit was changed by someone else since the planner loaded. Refresh and try again."),
			title=_("Visit Out of Date"),
		)

	changed = False
	if date:
		target = getdate(date)
		if doc.get("visit_date"):
			frappe.throw(
				_("This visit was started for {0}. Change its date on the visit form.").format(
					formatdate(doc.visit_date)
				)
			)
		if target < getdate(nowdate()):
			frappe.throw(_("Pick today or a later day."))
		if not doc.scheduled_visit_date or getdate(doc.scheduled_visit_date) != target:
			doc.scheduled_visit_date = target
			changed = True

	prior = None
	if technician is not None and (technician or None) != (doc.technician or None):
		if technician and not frappe.db.get_value("User", technician, "enabled"):
			frappe.throw(_("{0} is not an active user.").format(technician))
		prior = doc.technician or ""
		doc.technician = technician or None
		changed = True

	warnings = []
	if changed:
		log = (
			frappe.local.message_log if isinstance(getattr(frappe.local, "message_log", None), list) else None
		)
		before = len(log) if log is not None else 0
		doc.save()
		if log is not None:
			warnings = [_message_text(message) for message in log[before:]]
			del log[before:]
		if prior is not None:
			_move_assignment(doc, prior)

	return {
		"name": doc.name,
		"date": str(doc.scheduled_visit_date) if doc.scheduled_visit_date else None,
		"technician": doc.technician,
		"modified": str(doc.modified),
		"warnings": [w for w in warnings if w],
	}


def _message_text(message):
	from frappe.utils import strip_html_tags

	if isinstance(message, str):
		try:
			message = frappe.parse_json(message)
		except Exception:
			return strip_html_tags(message)
	text = message.get("message") if isinstance(message, dict) else message
	return strip_html_tags(str(text or "")).strip()


def _move_assignment(doc, prior):
	"""Follow the technician change with the dispatch assignment (ToDo + share).

	Best-effort, like the dispatcher's own (``maintenance_dispatch``): a missing
	or duplicate assignment is logged, never raised into the move.
	"""
	from frappe.desk.form.assign_to import remove

	from erpnext_enhancements.api.maintenance_dispatch import assign_to_technician

	if prior:
		try:
			remove(doc.doctype, doc.name, prior)
		except Exception:
			frappe.log_error(
				title="Maintenance Planner: unassign failed",
				reference_doctype=doc.doctype,
				reference_name=doc.name,
			)
	if doc.technician:
		assign_to_technician(doc.name, doc.technician)


@frappe.whitelist()
def move_projected(contract, from_date, to_date, serial_no=None):
	"""Move a contract's next (not yet drafted) visit to another day.

	Rewrites ``next_visit_date`` on the feature rows :func:`rows_to_move` picks,
	leaves a note on the contract's timeline, and drafts the visit at once when
	the new day is inside the scheduler's drafting window.

	Returns:
		dict: ``{"moved": rows rewritten, "drafted": the new record or None}``.
	"""
	_require_planner()
	from erpnext_enhancements.tasks import MAINTENANCE_DRAFT_HORIZON_DAYS

	doc = frappe.get_doc("Sapphire Maintenance Contract", contract)
	doc.check_permission("write")
	if doc.status != "Active":
		frappe.throw(_("{0} is not an Active contract.").format(doc.name))
	source, target = getdate(from_date), getdate(to_date)
	today = getdate(nowdate())
	if target < today:
		frappe.throw(_("Pick today or a later day."))
	if _open_regular_draft_exists(doc, serial_no):
		frappe.throw(
			_("This visit has already been drafted. Refresh the planner and move the visit card instead.")
		)
	rows = rows_to_move(doc, source, target, serial_no)
	if not rows:
		frappe.throw(_("This visit has moved since the planner loaded. Refresh and try again."))
	if source == target:
		return {"moved": 0, "drafted": None}

	for row in rows:
		frappe.db.set_value("Sapphire Contract Feature", row.name, "next_visit_date", target)
		row.next_visit_date = target
	what = feature_label(serial_no) if serial_no else _("Next visit")
	doc.add_comment(
		"Info",
		_("{0} moved from {1} to {2} on the Maintenance Planner.").format(
			what,
			formatdate(source),
			formatdate(target),
		),
	)

	drafted = None
	if target <= getdate(add_days(today, MAINTENANCE_DRAFT_HORIZON_DAYS)) and _scheduler_would_draft(
		doc, today
	):
		from erpnext_enhancements.tasks import _draft_maintenance_record

		record = _draft_maintenance_record(
			doc,
			serial_no=None if doc.visit_shape == "Per Site Visit" else serial_no,
			scheduled_date=target,
			exact_date=True,
		)
		drafted = record.name
	return {"moved": len(rows), "drafted": drafted}


def _open_regular_draft_exists(contract, serial_no):
	"""The scheduler's own dedup checks (tasks.generate_predictive_maintenance_records)."""
	if contract.visit_shape == "Per Site Visit":
		filters = {"maintenance_contract": contract.name}
	else:
		filters = {"project": contract.project, "serial_no": serial_no}
	filters.update({"visit_label": ["is", "not set"], "docstatus": 0})
	return bool(frappe.db.exists("Sapphire Maintenance Record", filters))


def _scheduler_would_draft(contract, today):
	"""The scheduler skips held customers and contracts that have not started."""
	if contract.start_date and getdate(contract.start_date) > today:
		return False
	if (
		contract.customer
		and frappe.db.has_column("Customer", "custom_service_hold")
		and frappe.db.get_value("Customer", contract.customer, "custom_service_hold")
	):
		return False
	return True
