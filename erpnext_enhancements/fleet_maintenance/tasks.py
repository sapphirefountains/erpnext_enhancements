"""Fleet Maintenance scheduled jobs."""

import frappe

from erpnext_enhancements.feature_flags import fleet_maintenance_enabled, fleet_reminders_enabled
from erpnext_enhancements.fleet_maintenance.status import recompute_vehicle_status


def refresh_fleet_status():
	"""Daily: recompute every non-retired vehicle's maintenance status so it slips
	to Due Soon / Overdue as dates pass, and (when reminders are on) notify fleet
	managers the day a vehicle newly crosses that line.

	Dormant unless Fleet Maintenance is enabled in ERPNext Enhancements Settings.
	"""
	if not fleet_maintenance_enabled():
		return

	notify = fleet_reminders_enabled()
	for name in frappe.get_all("Fleet Vehicle", filters={"status": ["!=", "Retired"]}, pluck="name"):
		try:
			recompute_vehicle_status(name, notify=notify)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Fleet status refresh")
