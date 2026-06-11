"""AI Governance scheduler jobs (wired in hooks.py ``scheduler_events``)."""

import frappe
from frappe.utils import add_days, cint, now_datetime, nowdate


def expire_stale_pending_actions():
	"""Hourly: flip Pending actions past their TTL to Expired.

	Hourly (not daily) because the default TTL is one hour — a stale-but-
	Pending card someone confirms six hours later is exactly the race the TTL
	exists to prevent. The endpoint-side expiry check in gating_api is the
	hard guarantee; this sweep keeps the list view honest.
	"""
	stale = frappe.get_all(
		"AI Pending Action",
		filters={"status": "Pending", "expires_at": ("<", now_datetime())},
		pluck="name",
	)
	for name in stale:
		frappe.flags.ai_action_transition = True
		try:
			doc = frappe.get_doc("AI Pending Action", name)
			doc.status = "Expired"
			doc.save(ignore_permissions=True)
		except Exception:
			frappe.log_error(
				f"Failed to expire AI Pending Action {name}\n{frappe.get_traceback()}",
				"AI Governance",
			)
		finally:
			frappe.flags.ai_action_transition = False


def purge_old_action_logs():
	"""Daily: apply the optional retention window (Settings, 0 = keep forever)
	to AI Action Log rows and *terminal* AI Pending Actions."""
	days = cint(
		frappe.db.get_single_value("ERPNext Enhancements Settings", "ai_action_log_retention_days")
	)
	if not days:
		return
	cutoff = add_days(nowdate(), -days)

	frappe.flags.ai_log_purge = True
	try:
		for name in frappe.get_all(
			"AI Action Log", filters={"creation": ("<", cutoff)}, pluck="name"
		):
			frappe.delete_doc("AI Action Log", name, force=True, ignore_permissions=True)
		for name in frappe.get_all(
			"AI Pending Action",
			filters={
				"status": ("in", ("Executed", "Failed", "Cancelled", "Expired")),
				"modified": ("<", cutoff),
			},
			pluck="name",
		):
			frappe.delete_doc("AI Pending Action", name, force=True, ignore_permissions=True)
	finally:
		frappe.flags.ai_log_purge = False
