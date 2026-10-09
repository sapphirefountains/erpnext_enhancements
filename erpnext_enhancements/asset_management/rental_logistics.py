# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Rental operations: the crew, their four tasks, their morning digest, and inspections (v1.566.0).

Nik's calls, 2026-09-29: the crew is picked **per booking** (a Crew table, pre-filled with Rental
Settings' default lead), and a firm booking gives them four ERPNext Tasks.

* :func:`sync_tasks` — Delivery, Setup (only when a setup time is set), Take-down and Cleaning,
  one of each per booking (``Task.custom_rental_booking`` + ``custom_rental_task_kind``), dated from
  the booking's schedule and assigned to every crew member with a ToDo. Kept in step on every save of
  a firm booking: dates and descriptions follow the schedule, a crew member taken off loses their
  open ToDos, and a canceled or expired booking cancels its open tasks. A task someone has marked
  Completed is never touched.
* **The Project's expected dates are widened, never narrowed,** to cover the tasks. ERPNext refuses a
  Task dated outside its Project's expected window (``Task.validate_parent_project_dates``), and
  cleaning always falls after take-down — the date the Rental Planner's new projects end on.
* :func:`send_crew_digests` — 6am: each crew member's rental tasks for today, by email and text, at
  most once a day (``Task.custom_rental_digest_sent_on``), like the maintenance route digest.
* :func:`generate_due_inspections` — 6am: pre-shipping checklists for every fountain delivering today
  or tomorrow; :func:`generate_return_inspections` — when a booking is marked Returned. Each uses the
  existing ``api.booking.generate_inspection`` on the fountain's Rental calendar leg, which returns
  the existing sheet rather than making a second.

Everything called from a booking save runs in a savepoint and reports failure as a comment, so the
paperwork never stops a booking being saved. The scheduled sweeps re-derive their work from the
tables each morning, because the deploy's Redis flush kills queued jobs.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import add_days, cint, format_datetime, get_datetime, getdate, nowdate

from erpnext_enhancements.asset_management import rental_rules as rules

DELIVERY, SETUP, TAKEDOWN, CLEANING = "Delivery", "Setup", "Take-down", "Cleaning"
TASK_KINDS = (DELIVERY, SETUP, TAKEDOWN, CLEANING)
DONE_TASK_STATUSES = ("Completed", "Cancelled")


def settings():
	return frappe.get_cached_doc("Rental Settings")


def default_crew(booking):
	"""A new booking's crew starts with Rental Settings' default lead."""
	if booking.get("crew"):
		return
	lead = settings().get("default_crew_lead")
	if lead and frappe.db.get_value("User", lead, "enabled"):
		booking.append("crew", {"user": lead, "crew_role": "Lead"})


def crew_users(booking):
	users = []
	for row in booking.get("crew") or []:
		if row.user and row.user not in users:
			users.append(row.user)
	return users


# ---------------------------------------------------------------- tasks


def planned_tasks(booking):
	"""``{kind: (date, time_text)}`` for the tasks a firm booking needs."""
	delivery = get_datetime(booking.delivery_datetime)
	takedown = get_datetime(booking.takedown_datetime)
	turnaround = max((rules.whole(r.turnaround_hours) for r in booking.get("fountains") or []), default=0)
	cleaning = takedown + datetime.timedelta(hours=turnaround)
	plan = {
		DELIVERY: (delivery.date(), format_datetime(delivery)),
		TAKEDOWN: (takedown.date(), format_datetime(takedown)),
		CLEANING: (cleaning.date(), _("after take-down, done by {0}").format(format_datetime(cleaning))),
	}
	if booking.setup_datetime:
		setup = get_datetime(booking.setup_datetime)
		plan[SETUP] = (setup.date(), format_datetime(setup))
	return plan


def _subject(kind, booking):
	who = booking.customer_name or booking.customer
	what = booking.event_name or booking.name
	return {
		DELIVERY: _("Deliver fountains: {0} ({1})"),
		SETUP: _("Set up fountains: {0} ({1})"),
		TAKEDOWN: _("Take down fountains: {0} ({1})"),
		CLEANING: _("Clean returned fountains: {0} ({1})"),
	}[kind].format(what, who)[:140]


def _description(kind, booking, when):
	esc = frappe.utils.escape_html
	lines = [f"<p><b>{esc(kind)}</b>: {esc(when)}</p>"]
	lines.append(
		"<p>"
		+ _("Rental {0}").format(frappe.utils.get_link_to_form("Rental Booking", booking.name))
		+ "</p>"
	)
	items = [esc(r.asset_name or r.asset) for r in booking.get("fountains") or []]
	items += [esc(f"{cint(r.qty)} × {r.pool}") for r in booking.get("accessories") or []]
	if items:
		lines.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
	if kind != CLEANING:
		if booking.venue_address:
			lines.append("<p>" + _("Venue: {0}").format(esc(booking.venue_address)) + "</p>")
		for label, field in (
			(_("On-site contact"), "site_contact_name"),
			(_("Phone"), "site_contact_phone"),
			(_("Surface"), "surface"),
			(_("Access"), "site_access"),
			(_("Power"), "power_source"),
			(_("Water"), "water_source"),
			(_("Notes"), "site_prep_notes"),
		):
			if booking.get(field):
				lines.append(f"<p>{esc(label)}: {esc(booking.get(field))}</p>")
	return "".join(lines)


def _existing_tasks(booking_name):
	rows = frappe.get_all(
		"Task",
		filters={"custom_rental_booking": booking_name},
		fields=["name", "custom_rental_task_kind", "status", "exp_start_date"],
	)
	return {r.custom_rental_task_kind: r for r in rows if r.custom_rental_task_kind}


def sync_tasks(booking):
	"""Make the booking's tasks and their assignments match it. See the module docstring."""
	existing = _existing_tasks(booking.name)
	if booking.status in rules.RELEASED_STATUSES:
		for row in existing.values():
			if row.status not in DONE_TASK_STATUSES:
				frappe.db.set_value("Task", row.name, "status", "Cancelled")
				_close_todos(row.name)
		return
	if booking.status not in rules.FIRM_STATUSES:
		return

	plan = planned_tasks(booking)
	if booking.project:
		_widen_project(booking.project, min(d for d, _t in plan.values()), max(d for d, _t in plan.values()))
	crew = crew_users(booking)
	for kind in TASK_KINDS:
		row = existing.get(kind)
		if kind not in plan:
			if row and row.status not in DONE_TASK_STATUSES:
				frappe.db.set_value("Task", row.name, "status", "Cancelled")
				_close_todos(row.name)
			continue
		date, when = plan[kind]
		if row and row.status == "Completed":
			continue
		task = frappe.get_doc("Task", row.name) if row else frappe.new_doc("Task")
		task.update(
			{
				"subject": _subject(kind, booking),
				"project": booking.project,
				"exp_start_date": date,
				"exp_end_date": date,
				"description": _description(kind, booking, when),
				"custom_rental_booking": booking.name,
				"custom_rental_task_kind": kind,
			}
		)
		if task.get("status") in (None, "", "Cancelled"):
			task.status = "Open"
		if row and getdate(row.exp_start_date) != getdate(date):
			# A moved task is a new day's work: let the digest announce it again.
			task.custom_rental_digest_sent_on = None
		task.flags.ignore_permissions = True
		task.save()
		_assign(task.name, crew)


def _widen_project(project, start, end):
	current = frappe.db.get_value("Project", project, ["expected_start_date", "expected_end_date"], as_dict=True)
	if not current:
		return
	values = {}
	if current.expected_start_date and getdate(current.expected_start_date) > start:
		values["expected_start_date"] = start
	if current.expected_end_date and getdate(current.expected_end_date) < end:
		values["expected_end_date"] = end
	if values:
		frappe.db.set_value("Project", project, values, update_modified=False)


def _assign(task_name, crew):
	"""One open ToDo per crew member; crew taken off lose theirs. Inserted directly, because
	``assign_to.add`` checks the CALLER's permission on the Task and a salesperson saving the
	booking may have none."""
	todos = frappe.get_all(
		"ToDo",
		filters={"reference_type": "Task", "reference_name": task_name, "status": "Open"},
		fields=["name", "allocated_to"],
	)
	have = {t.allocated_to for t in todos}
	for todo in todos:
		if todo.allocated_to not in crew:
			frappe.db.set_value("ToDo", todo.name, "status", "Cancelled")
	subject = frappe.db.get_value("Task", task_name, "subject")
	for user in crew:
		if user not in have:
			frappe.get_doc(
				{
					"doctype": "ToDo",
					"allocated_to": user,
					"reference_type": "Task",
					"reference_name": task_name,
					"description": subject,
					"date": frappe.db.get_value("Task", task_name, "exp_start_date"),
				}
			).insert(ignore_permissions=True)


def _close_todos(task_name):
	for name in frappe.get_all(
		"ToDo", filters={"reference_type": "Task", "reference_name": task_name, "status": "Open"}, pluck="name"
	):
		frappe.db.set_value("ToDo", name, "status", "Cancelled")


def after_booking_save(booking):
	"""Rental Booking on_update: tasks, and return inspections on the save that makes it Returned.

	In a savepoint: a failure here is a comment on the booking, never a refused save.
	"""
	savepoint = "rental_logistics"
	frappe.db.savepoint(savepoint)
	try:
		sync_tasks(booking)
		before = booking.get_doc_before_save()
		if booking.status == "Returned" and (before is None or before.status != "Returned"):
			generate_return_inspections(booking)
	except Exception as exc:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(title=f"Rental logistics failed for {booking.name}", message=frappe.get_traceback())
		booking.add_comment(
			"Comment",
			_("The crew tasks or checklists could not be updated: {0}").format(frappe.utils.strip_html(str(exc))[:300]),
		)


# ---------------------------------------------------------------- digest


def send_crew_digests():
	"""Cron, 6am: each crew member's rental tasks for today, by email and text. At most once a day."""
	if not cint(settings().get("crew_digest")):
		return
	today = getdate(nowdate())
	tasks = frappe.get_all(
		"Task",
		filters=[
			["custom_rental_booking", "is", "set"],
			["exp_start_date", "=", today],
			["status", "not in", list(DONE_TASK_STATUSES)],
		],
		# Not already digested today: never, or on an earlier day (a task moved to today).
		or_filters=[
			["custom_rental_digest_sent_on", "is", "not set"],
			["custom_rental_digest_sent_on", "<", today],
		],
		fields=["name", "subject", "custom_rental_booking", "custom_rental_task_kind"],
	)
	by_user = {}
	for task in tasks:
		for user in frappe.get_all(
			"ToDo",
			filters={"reference_type": "Task", "reference_name": task.name, "status": "Open"},
			pluck="allocated_to",
		):
			by_user.setdefault(user, []).append(task)
	for task in tasks:
		frappe.db.set_value("Task", task.name, "custom_rental_digest_sent_on", today, update_modified=False)
	frappe.db.commit()
	covered = _combined_digest_covers()
	for user, items in by_user.items():
		if user in covered:
			# The Project Planner's combined 6 AM digest lists this person's rental jobs with the
			# rest of their day (Phase 3B), so this one would be a second text about the same work.
			continue
		try:
			_send_digest(user, items, today)
		except Exception:
			frappe.log_error(title=f"Rental crew digest failed: {user}", message=frappe.get_traceback())


def _combined_digest_covers():
	"""Users the Project Planner's combined morning digest covers; empty while it is off (Project
	Planner Settings ``combined_morning_digest``). Any failure answers the empty set, which leaves
	this digest exactly as it was."""
	try:
		from erpnext_enhancements.project_enhancements.planner_digest import covered_users

		return covered_users()
	except Exception:
		return set()


def _send_digest(user, tasks, today):
	from erpnext_enhancements import email_style

	order = {kind: i for i, kind in enumerate(TASK_KINDS)}
	tasks = sorted(tasks, key=lambda t: order.get(t.custom_rental_task_kind, 9))
	when = frappe.utils.formatdate(today)
	count = len(tasks)

	cell = frappe.db.get_value("Employee", {"user_id": user}, "cell_number")
	if cell:
		lines = [f"{i}. {t.subject}" for i, t in enumerate(tasks[:10], 1)]
		if count > 10:
			lines.append(_("…and {0} more — see email").format(count - 10))
		try:
			from erpnext_enhancements.api.telephony import send_system_sms

			send_system_sms(cell, f"Sapphire Fountains — {count} rental job(s) today ({when}):\n" + "\n".join(lines))
		except Exception:
			frappe.log_error(title=f"Rental crew digest SMS failed: {user}", message=frappe.get_traceback())

	email = frappe.db.get_value("User", user, "email") or user
	rows = [
		[str(i), t.custom_rental_task_kind or "", t.subject, t.custom_rental_booking]
		for i, t in enumerate(tasks, 1)
	]
	frappe.sendmail(
		recipients=[email],
		subject=_("Your rental jobs — {0} ({1})").format(when, count),
		message=email_style.wrap(
			email_style.p(_("Your rental jobs for {0}:").format(when))
			+ email_style.table(["#", _("Job"), _("What"), _("Rental")], rows),
			title=_("Your rental jobs"),
			eyebrow=_("Rentals") + " · " + str(when),
			pillar="rent",
		),
	)


# ---------------------------------------------------------------- inspections


def _generate(booking, direction):
	"""A checklist per fountain, assigned to the crew. Created as the system (``make_inspection``):
	whoever's save triggered it need not be allowed to create inspections."""
	from erpnext_enhancements.api.booking import make_inspection

	crew = crew_users(booking)
	lead = next((r.user for r in booking.get("crew") or [] if r.crew_role == "Lead"), None) or (crew[0] if crew else None)
	made, problems = [], []
	for row in booking.get("fountains") or []:
		if not row.asset_booking:
			continue
		try:
			result = make_inspection(row.asset_booking, direction, inspected_by=lead, ignore_permissions=True)
			if result and result.get("created"):
				made.append(result.get("inspection"))
				for user in crew:
					frappe.get_doc(
						{
							"doctype": "ToDo",
							"allocated_to": user,
							"reference_type": "Rental Inspection",
							"reference_name": result.get("inspection"),
							"description": _("{0} checklist: {1}").format(_(direction), row.asset_name or row.asset),
						}
					).insert(ignore_permissions=True)
		except Exception as exc:
			frappe.clear_messages()
			problems.append(f"{row.asset_name or row.asset}: {frappe.utils.strip_html(str(exc))[:200]}")
	if made:
		booking.add_comment("Comment", _("{0} checklists created: {1}").format(_(direction), ", ".join(made)))
	if problems:
		booking.add_comment(
			"Comment",
			_("{0} checklists could not be created for: {1}").format(_(direction), "; ".join(problems)),
		)
	return made


def generate_return_inspections(booking):
	if cint(settings().get("generate_inspections")):
		_generate(booking, "Return")


def generate_due_inspections():
	"""Cron, 6am: pre-shipping checklists for every confirmed rental delivering today or tomorrow."""
	if not cint(settings().get("generate_inspections")):
		return
	start = get_datetime(nowdate())
	end = get_datetime(add_days(nowdate(), 2))
	for name in frappe.get_all(
		"Rental Booking",
		filters=[
			["status", "=", "Confirmed"],
			["delivery_datetime", "is", "set"],
			["delivery_datetime", ">=", start],
			["delivery_datetime", "<", end],
		],
		pluck="name",
	):
		savepoint = "rental_preship"
		frappe.db.savepoint(savepoint)
		try:
			_generate(frappe.get_doc("Rental Booking", name), "Pre-shipping")
			frappe.db.commit()
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			frappe.log_error(title=f"Rental pre-shipping checklists failed for {name}", message=frappe.get_traceback())
