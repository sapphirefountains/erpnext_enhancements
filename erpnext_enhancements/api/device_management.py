"""Whitelisted endpoints for the Device Management module.

Two audiences, two access models (the house split, mirroring
``api.inventory_scanner`` / ``api.time_kiosk``):

* **Device Managers** drive the fleet — barcode scan resolution + lifecycle
  (check-out / check-in / transfer / repair / lost / retire) from the Device
  Console (``device_management/page/device_console``) and the Managed Device
  form buttons. Gated to ``MANAGER_ROLES``; ordinary ``doc.save()`` (they hold
  write perm).
* **Any employee** sees and attests *their own* device via ``get_my_devices`` /
  ``attest_device``. These require only an authenticated session and enforce
  identity (``assigned_to_user == frappe.session.user``); the save then uses
  ``ignore_permissions=True`` because an employee has read-only DocPerm — the
  identity check above is the real gate.

Lifecycle helpers keep the custody history (``Device Assignment Log``)
append-only: each move closes the open row (no ``returned_on``) and/or opens a
new one. ``mark_lost`` forces Non-Compliant and is the documented seam for the
Phase-2 provider remote-wipe.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, today

from erpnext_enhancements.device_management.compliance import derive_compliance
from erpnext_enhancements.device_management.doctype.device_compliance_settings.device_compliance_settings import (
	get_settings,
)

# Roles permitted to manage devices. "Device Manager" is seeded by
# patches.create_device_manager_role; the doctype + pages also gate on these.
MANAGER_ROLES = {"System Manager", "Device Manager"}


def _check_manager():
	"""Throw unless the caller holds one of MANAGER_ROLES."""
	if not MANAGER_ROLES.intersection(set(frappe.get_roles())):
		frappe.throw(_("You are not permitted to manage devices."), frappe.PermissionError)


# ---------------------------------------------------------------------------
# Scan resolution + lookups (Device Console)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def resolve_device_scan(code):
	"""Classify a scanned/typed code as a Managed Device, or unknown.

	Looked up by ``barcode`` -> ``asset_tag`` -> ``imei`` -> ``serial_number``
	(identifiers are normalised upper-case on save, so match upper-case)."""
	_check_manager()
	code = (code or "").strip()
	if not code:
		frappe.throw(_("Empty scan."))
	upper = code.upper()

	name = None
	for field in ("barcode", "asset_tag", "imei", "serial_number"):
		# asset_tag is stored as entered; the rest are upper-cased on save.
		value = code if field == "asset_tag" else upper
		name = frappe.db.get_value("Managed Device", {field: value}, "name")
		if name:
			break

	if not name:
		return {"type": "unknown", "code": code}
	return {"type": "device", "device": _device_payload(name)}


@frappe.whitelist()
def lookup_employee(query, limit=10):
	"""Active-employee search for the assignee picker (filter values are bound)."""
	_check_manager()
	query = (query or "").strip()
	if not query:
		return []
	like = f"%{query}%"
	return frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		or_filters={"name": ["like", like], "employee_name": ["like", like]},
		fields=["name", "employee_name"],
		limit=cint(limit) or 10,
		order_by="employee_name asc",
	)


@frappe.whitelist()
def get_console_bootstrap():
	"""Initial payload for the Device Console: camera flag + fleet counters."""
	_check_manager()
	settings = get_settings()
	return {
		"user": frappe.session.user,
		"enable_camera_scan": cint(settings.get("enable_camera_scan")),
		"counts": {
			"total": frappe.db.count("Managed Device"),
			"in_stock": frappe.db.count("Managed Device", {"status": "In Stock"}),
			"assigned": frappe.db.count("Managed Device", {"status": "Assigned"}),
			"non_compliant": frappe.db.count("Managed Device", {"compliance_status": "Non-Compliant"}),
		},
	}


# ---------------------------------------------------------------------------
# Lifecycle (Device Manager)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def check_out(device, employee, condition_note=None):
	"""Assign an In-Stock (or just-repaired) device to an employee."""
	_check_manager()
	doc = frappe.get_doc("Managed Device", device)
	if doc.status not in ("In Stock", "In Repair"):
		frappe.throw(_("Device {0} is {1} — only an In Stock or In Repair device can be checked out.").format(doc.name, _(doc.status)))
	if not frappe.db.exists("Employee", employee):
		frappe.throw(_("Employee {0} not found.").format(employee))

	doc.assigned_to_employee = employee
	doc.assigned_on = today()
	doc.status = "Assigned"
	_open_assignment_row(doc, employee, "Checked Out", condition_note)
	doc.save()
	return _device_payload(doc.name)


@frappe.whitelist()
def check_in(device, condition_note=None):
	"""Release an assigned device back to stock."""
	_check_manager()
	doc = frappe.get_doc("Managed Device", device)
	if doc.status != "Assigned":
		frappe.throw(_("Device {0} is not currently checked out.").format(doc.name))
	_close_open_assignment_row(doc, "Checked In", condition_note)
	doc.assigned_to_employee = None
	doc.assigned_on = None
	doc.status = "In Stock"
	doc.save()
	return _device_payload(doc.name)


@frappe.whitelist()
def transfer(device, new_employee, condition_note=None):
	"""Hand an assigned device directly to another employee (no stock gap)."""
	_check_manager()
	doc = frappe.get_doc("Managed Device", device)
	if doc.status != "Assigned":
		frappe.throw(_("Only an Assigned device can be transferred. Check out device {0} first.").format(doc.name))
	if not frappe.db.exists("Employee", new_employee):
		frappe.throw(_("Employee {0} not found.").format(new_employee))

	_close_open_assignment_row(doc, "Transferred", condition_note)
	doc.assigned_to_employee = new_employee
	doc.assigned_on = today()
	# status stays "Assigned"
	_open_assignment_row(doc, new_employee, "Checked Out", condition_note)
	doc.save()
	return _device_payload(doc.name)


@frappe.whitelist()
def mark_repair(device, note=None):
	"""Send a device for repair (pulled from its holder if assigned)."""
	_check_manager()
	doc = frappe.get_doc("Managed Device", device)
	if doc.status not in ("Assigned", "In Stock"):
		frappe.throw(_("Device {0} is {1} and cannot be sent for repair.").format(doc.name, _(doc.status)))
	if doc.status == "Assigned":
		_close_open_assignment_row(doc, "Checked In", note)
		doc.assigned_to_employee = None
		doc.assigned_on = None
	doc.status = "In Repair"
	if note:
		doc.compliance_notes = note
	doc.save()
	return _device_payload(doc.name)


@frappe.whitelist()
def mark_lost(device, note=None):
	"""Flag a device lost/stolen — clears the holder and forces Non-Compliant.

	This is the documented seam for the Phase-2 provider remote-wipe: a lost
	device is exactly what an admin would lock/wipe through ``mdm_integration``.
	"""
	_check_manager()
	doc = frappe.get_doc("Managed Device", device)
	if doc.status == "Retired":
		frappe.throw(_("Device {0} is Retired.").format(doc.name))
	if doc.status == "Assigned":
		_close_open_assignment_row(doc, "Checked In", note)
		doc.assigned_to_employee = None
		doc.assigned_on = None
	doc.status = "Lost/Stolen"
	doc.compliance_status = "Non-Compliant"
	if note:
		doc.compliance_notes = note
	doc.save()
	return _device_payload(doc.name)


@frappe.whitelist()
def retire(device, note=None):
	"""Retire a device (terminal). Closes any open custody row and unassigns."""
	_check_manager()
	doc = frappe.get_doc("Managed Device", device)
	if doc.status == "Retired":
		return _device_payload(doc.name)
	if doc.status == "Assigned":
		_close_open_assignment_row(doc, "Checked In", note)
		doc.assigned_to_employee = None
		doc.assigned_on = None
	doc.status = "Retired"
	if note:
		doc.compliance_notes = note
	doc.save()
	return _device_payload(doc.name)


# ---------------------------------------------------------------------------
# Self-service (any employee — their own device only)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_my_devices():
	"""Return the calling user's assigned devices (shallow, BYOD-safe fields)."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Not permitted."), frappe.PermissionError)
	return frappe.get_all(
		"Managed Device",
		filters={"assigned_to_user": frappe.session.user},
		fields=[
			"name", "device_name", "device_type", "platform", "ownership", "status",
			"compliance_status", "screen_lock_enabled", "encryption_enabled",
			"os_version", "last_checked_on",
		],
		order_by="device_name asc",
	)


@frappe.whitelist()
def attest_device(device, screen_lock, encryption, os_version=None):
	"""Record the caller's self-attested posture for a device they hold.

	Identity-gated: only the device's current assignee may attest. Derives the
	compliance status via the single ``derive_compliance`` rule and stamps the
	check as Manual so a Phase-2 provider feed can later overwrite it.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Not permitted."), frappe.PermissionError)
	doc = frappe.get_doc("Managed Device", device)
	if doc.assigned_to_user != frappe.session.user:
		frappe.throw(_("You can only attest a device assigned to you."), frappe.PermissionError)

	doc.screen_lock_enabled = cint(screen_lock)
	doc.encryption_enabled = cint(encryption)
	if os_version is not None:
		doc.os_version = (os_version or "").strip()
	doc.last_checked_on = today()
	doc.compliance_source = "Manual"
	doc.compliance_status = derive_compliance(doc.screen_lock_enabled, doc.encryption_enabled)
	doc.save(ignore_permissions=True)  # identity-gated above; employee has read-only DocPerm

	settings = get_settings()
	if doc.compliance_status == "Non-Compliant" and cint(settings.get("notify_device_manager_on_noncompliance")):
		_notify_device_managers(
			subject=_("Device {0} reported non-compliant").format(doc.device_name or doc.name),
			message=_("{0} attested {1} as non-compliant (screen lock / encryption not both enabled).").format(
				frappe.session.user, doc.device_name or doc.name
			),
			device=doc.name,
		)
	return _device_payload(doc.name)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _open_assignment_row(doc, employee, action, note=None):
	"""Append an open custody row (the new current holder)."""
	doc.append(
		"assignment_history",
		{
			"employee": employee,
			"user": frappe.db.get_value("Employee", employee, "user_id"),
			"action": action,
			"assigned_on": now_datetime(),
			"assigned_by": frappe.session.user,
			"condition_note": note,
		},
	)


def _close_open_assignment_row(doc, action, note=None):
	"""Stamp the currently-open custody row (no ``returned_on``) as returned."""
	for row in doc.assignment_history:
		if not row.returned_on:
			row.returned_on = now_datetime()
			row.action = action
			if note:
				row.condition_note = note
			return True
	return False


def _notify_device_managers(subject, message, device):
	"""Desk Notification Log to every Device Manager (best-effort)."""
	users = _device_manager_users()
	for user in users:
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": subject,
					"email_content": message,
					"document_type": "Managed Device",
					"document_name": device,
					"for_user": user,
					"type": "Alert",
				}
			).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Device Manager notification failed")


def _device_manager_users():
	"""Enabled users holding the Device Manager role."""
	rows = frappe.get_all(
		"Has Role",
		filters={"role": "Device Manager", "parenttype": "User"},
		fields=["parent"],
	)
	users = []
	for row in rows:
		if frappe.db.get_value("User", row.parent, "enabled"):
			users.append(row.parent)
	return users


def _device_payload(name):
	"""Serialize a device (header + open holder + history) for the clients."""
	doc = frappe.get_doc("Managed Device", name)
	history = [
		{
			"employee": row.employee,
			"user": row.user,
			"action": row.action,
			"assigned_on": str(row.assigned_on) if row.assigned_on else None,
			"returned_on": str(row.returned_on) if row.returned_on else None,
			"condition_note": row.condition_note,
		}
		for row in doc.assignment_history
	]
	return {
		"name": doc.name,
		"device_name": doc.device_name,
		"asset_tag": doc.asset_tag,
		"status": doc.status,
		"ownership": doc.ownership,
		"device_type": doc.device_type,
		"platform": doc.platform,
		"assigned_to_employee": doc.assigned_to_employee,
		"assigned_to_user": doc.assigned_to_user,
		"assigned_on": str(doc.assigned_on) if doc.assigned_on else None,
		"compliance_status": doc.compliance_status,
		"compliance_source": doc.compliance_source,
		"last_checked_on": str(doc.last_checked_on) if doc.last_checked_on else None,
		"history": history,
	}
