"""Scheduled nudges for Device Management (registered ``daily`` in hooks.py).

Both follow the app's stamp-first / at-most-once reminder pattern
(``tasks.nudge_unsubmitted_maintenance_forms``): they stamp the guard field
before notifying so a record is acted on at most once per window, and read their
cadence from Device Compliance Settings.

* :func:`send_device_warranty_reminders` — warn Device Managers as each device
  enters its warranty-expiry lead window (once per warranty period).
* :func:`nudge_stale_device_attestations` — remind a device's holder to re-attest
  its security posture when the last check is older than the attestation
  interval (at most once per interval).
"""

import frappe
from frappe.utils import add_days, getdate, today

from erpnext_enhancements.device_management.doctype.device_compliance_settings.device_compliance_settings import (
	get_settings,
)


def send_device_warranty_reminders():
	"""Daily: notify Device Managers of devices nearing/at warranty expiry."""
	from erpnext_enhancements.api.device_management import _notify_device_managers

	settings = get_settings()
	lead = settings.get("warranty_reminder_lead_days") or 30
	soon = add_days(today(), lead)

	devices = frappe.get_all(
		"Managed Device",
		filters={
			"warranty_expiry_date": ["<=", soon],
			"status": ["!=", "Retired"],
		},
		fields=["name", "device_name", "warranty_expiry_date", "last_warranty_reminder_on"],
	)
	for device in devices:
		if not device.warranty_expiry_date:
			continue
		window_start = add_days(getdate(device.warranty_expiry_date), -lead)
		# Already reminded within this warranty period? skip.
		if device.last_warranty_reminder_on and getdate(device.last_warranty_reminder_on) >= window_start:
			continue

		# Stamp first (at-most-once), then notify.
		frappe.db.set_value("Managed Device", device.name, "last_warranty_reminder_on", today(), update_modified=False)
		expiry = getdate(device.warranty_expiry_date)
		when = "expired" if expiry < getdate(today()) else f"expires {expiry}"
		_notify_device_managers(
			subject=f"Device warranty {when}: {device.device_name or device.name}",
			message=f"The warranty for {device.device_name or device.name} {when}. Review repair/replacement.",
			device=device.name,
		)


def nudge_stale_device_attestations():
	"""Daily: remind holders to re-attest devices past the attestation interval."""
	settings = get_settings()
	interval = settings.get("attestation_interval_days") or 90
	cutoff = add_days(today(), -interval)
	require_byod = settings.get("require_attestation_for_byod")

	filters = {"status": "Assigned", "assigned_to_user": ["is", "set"]}
	if not require_byod:
		filters["ownership"] = "Company"

	devices = frappe.get_all(
		"Managed Device",
		filters=filters,
		fields=["name", "device_name", "assigned_to_user", "last_checked_on", "last_attestation_nudge_on"],
	)
	for device in devices:
		# Only nudge when the posture check is stale...
		if device.last_checked_on and getdate(device.last_checked_on) >= getdate(cutoff):
			continue
		# ...and we have not already nudged within this interval.
		if device.last_attestation_nudge_on and getdate(device.last_attestation_nudge_on) >= getdate(cutoff):
			continue

		frappe.db.set_value("Managed Device", device.name, "last_attestation_nudge_on", today(), update_modified=False)
		_notify_assignee(device)


def _notify_assignee(device):
	"""Desk Notification Log + realtime ping to a device's current holder."""
	label = device.device_name or device.name
	try:
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"subject": f"Please confirm your device is secure: {label}",
				"email_content": (
					f"It's time to re-confirm the security of {label} (screen lock, encryption, OS version). "
					"Open My Devices and tap Attest."
				),
				"document_type": "Managed Device",
				"document_name": device.name,
				"for_user": device.assigned_to_user,
				"type": "Alert",
			}
		).insert(ignore_permissions=True)
		frappe.publish_realtime(
			"device_attestation_due",
			{"device": device.name, "label": label},
			user=device.assigned_to_user,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Device attestation nudge failed")
