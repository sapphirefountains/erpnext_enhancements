"""Fleet Vehicle maintenance-status engine.

One place computes a vehicle's due dates and headline ``maintenance_status``:

* ``compute_derived(vehicle)`` — pure: sets each ``*_due_date`` from the matching
  ``last_*`` date + the cadence interval, and the overall status (No Data / OK /
  Due Soon / Overdue). Called from ``FleetVehicle.validate`` so the form reflects
  a manually seeded baseline immediately.
* ``recompute_vehicle_status(vehicle, notify=...)`` — refreshes the ``last_*``
  dates + odometer from submitted Vehicle Maintenance Logs, saves (which runs
  ``compute_derived`` via validate), and (when asked) notifies fleet managers if
  the vehicle has newly slipped to Due Soon / Overdue. Called from the log on
  submit/cancel and the daily job.

Cadence intervals come from ERPNext Enhancements Settings (with the schedule's
defaults as a fallback), so they are tunable without a deploy.
"""

import frappe
from frappe.utils import add_days, add_months, cint, getdate, nowdate

# Single source of truth for the maintenance cadences. Everything cadence-related
# (the log type that satisfies it, the vehicle's last/due fields, the interval
# setting + default) is derived from this so the structures can't drift apart.
# (log_type, last_field, due_field, interval_setting, unit, default_interval)
CADENCES = [
	(
		"Weekly",
		"last_weekly_service_date",
		"weekly_service_due_date",
		"fleet_weekly_interval_days",
		"days",
		7,
	),
	(
		"Oil Change (3-Month)",
		"last_oil_change_date",
		"oil_change_due_date",
		"fleet_oil_change_interval_months",
		"months",
		3,
	),
	(
		"Dealership Check-Up (6-Month)",
		"last_dealership_checkup_date",
		"dealership_checkup_due_date",
		"fleet_dealership_interval_months",
		"months",
		6,
	),
	(
		"Windshield Wipers (6-Month)",
		"last_wiper_change_date",
		"wiper_change_due_date",
		"fleet_wiper_interval_months",
		"months",
		6,
	),
]

# Which Vehicle Maintenance Log type rolls which "last done" date forward (derived).
# "Other / Repair" is deliberately absent — it logs work without a cadence.
LOG_TYPE_TO_LAST = {log_type: last_f for log_type, last_f, *_ in CADENCES}

DUE_SOON_SETTING = "fleet_due_soon_window_days"
DEFAULT_DUE_SOON_DAYS = 7

# Human labels for the due-date fields, used in reminder bodies.
DUE_LABELS = {
	"weekly_service_due_date": "Weekly service",
	"oil_change_due_date": "Oil change",
	"dealership_checkup_due_date": "Dealership check-up",
	"wiper_change_due_date": "Windshield wipers",
}


def get_intervals():
	"""Cadence intervals from settings, falling back to the schedule defaults.

	An explicitly entered value (including 0 — e.g. a 0-day Due Soon window means
	"only flag Overdue") is honoured; only a blank/unset field uses the default.
	"""
	settings = frappe.get_cached_doc("ERPNext Enhancements Settings")

	def value(field, default):
		raw = settings.get(field)
		return cint(raw) if raw not in (None, "") else default

	intervals = {setting: value(setting, default) for _t, _l, _d, setting, _u, default in CADENCES}
	intervals[DUE_SOON_SETTING] = value(DUE_SOON_SETTING, DEFAULT_DUE_SOON_DAYS)
	return intervals


def compute_derived(vehicle, intervals=None):
	"""Set each ``*_due_date`` and ``maintenance_status`` on the vehicle in place."""
	if intervals is None:
		intervals = get_intervals()

	today = getdate(nowdate())
	due_dates = []

	for _log_type, last_f, due_f, setting, unit, default in CADENCES:
		last = vehicle.get(last_f)
		if not last:
			vehicle.set(due_f, None)
			continue
		last = getdate(last)
		step = intervals.get(setting, default)
		due = add_days(last, step) if unit == "days" else add_months(last, step)
		vehicle.set(due_f, due)
		due_dates.append(getdate(due))

	if vehicle.get("status") == "Retired" or not due_dates:
		vehicle.maintenance_status = "No Data"
		return

	soon_cutoff = add_days(today, intervals.get(DUE_SOON_SETTING, DEFAULT_DUE_SOON_DAYS))
	if any(due < today for due in due_dates):
		vehicle.maintenance_status = "Overdue"
	elif any(today <= due <= soon_cutoff for due in due_dates):
		vehicle.maintenance_status = "Due Soon"
	else:
		vehicle.maintenance_status = "OK"


def recompute_vehicle_status(vehicle_name, notify=False):
	"""Refresh ``last_*`` dates + odometer from submitted logs, save (which
	recomputes the status via validate), and optionally notify on a newly Due Soon
	/ Overdue vehicle."""
	if not vehicle_name or not frappe.db.exists("Fleet Vehicle", vehicle_name):
		return

	vehicle = frappe.get_doc("Fleet Vehicle", vehicle_name)
	old_status = vehicle.maintenance_status

	# Latest submitted service date per cadence becomes that cadence's last-done.
	# A cadence with no log keeps its existing (possibly hand-seeded) value.
	for log_type, last_f in LOG_TYPE_TO_LAST.items():
		last_date = frappe.db.get_value(
			"Vehicle Maintenance Log",
			{"vehicle": vehicle_name, "maintenance_type": log_type, "docstatus": 1},
			"service_date",
			order_by="service_date desc",
		)
		if last_date:
			vehicle.set(last_f, last_date)

	# Odometer only ever moves forward.
	row = frappe.db.get_all(
		"Vehicle Maintenance Log",
		filters={"vehicle": vehicle_name, "docstatus": 1},
		fields=["max(odometer) as max_odo"],
	)
	max_odo = cint(row[0].max_odo) if row and row[0].get("max_odo") else 0
	if max_odo > cint(vehicle.current_odometer):
		vehicle.current_odometer = max_odo

	# save() runs FleetVehicle.validate() → compute_derived(), so the due dates and
	# status are recomputed exactly once here.
	vehicle.save(ignore_permissions=True)
	new_status = vehicle.maintenance_status

	if notify and new_status != old_status and new_status in ("Due Soon", "Overdue"):
		_notify_fleet_managers(vehicle, new_status)


# ---------------------------------------------------------------------- reminders


def _fleet_manager_users():
	"""Enabled desk users who should receive fleet reminders: anyone with the
	Fleet Manager or Maintenance Manager role, falling back to System Managers
	when none are assigned."""

	def users_with_roles(roles):
		owners = set()
		for role in roles:
			owners.update(
				frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent")
			)
		if not owners:
			return []
		return frappe.get_all(
			"User",
			filters={"name": ["in", list(owners)], "enabled": 1, "user_type": "System User"},
			pluck="name",
		)

	return users_with_roles(["Fleet Manager", "Maintenance Manager"]) or users_with_roles(["System Manager"])


def _due_summary(vehicle):
	"""HTML lines describing which cadences are overdue / due soon."""
	today = getdate(nowdate())
	lines = []
	for due_f, label in DUE_LABELS.items():
		due = vehicle.get(due_f)
		if not due:
			continue
		due = getdate(due)
		if due < today:
			lines.append(f"• {label} — <b>overdue</b> (was due {due})")
		else:
			lines.append(f"• {label} — due {due}")
	return lines


def _notify_fleet_managers(vehicle, status):
	users = _fleet_manager_users()
	if not users:
		return

	subject = f"{vehicle.name}: vehicle maintenance {status.lower()}"
	body = f"<p><b>{frappe.utils.escape_html(vehicle.name)}</b> maintenance is now <b>{status}</b>.</p>"
	lines = _due_summary(vehicle)
	if lines:
		body += "<p>" + "<br>".join(lines) + "</p>"

	for user in users:
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"for_user": user,
					"type": "Alert",
					"subject": subject,
					"email_content": body,
					"document_type": "Fleet Vehicle",
					"document_name": vehicle.name,
				}
			).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Fleet reminder notification")
