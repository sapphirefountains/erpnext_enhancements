"""Seed Call Intelligence supporting records (v1.11.0).

Creates the "Call Center Supervisor" role (recipient of the High Escalation
Risk / Compliance Flag notifications), grants it read/write/report access to
Call Log via Custom DocPerm, and seeds the Telephony Call Type intents the
Triton IVR produces (Rental / Design / Build / Service / General / Outbound).

**Insert-only and idempotent** (model: ``seed_maintenance_sections``): every
record is existence-guarded, so site-side edits survive re-migrations.
"""

import frappe
from frappe.permissions import add_permission, update_permission_property

CALL_TYPES = ["Rental", "Design", "Build", "Service", "General", "Outbound"]


def execute():
	_seed_role()
	_seed_call_log_perms()
	_seed_call_types()


def _seed_role():
	if not frappe.db.exists("Role", "Call Center Supervisor"):
		role = frappe.new_doc("Role")
		role.role_name = "Call Center Supervisor"
		role.desk_access = 1
		role.insert(ignore_permissions=True)


def _seed_call_log_perms():
	if frappe.db.exists("Custom DocPerm", {"parent": "Call Log", "role": "Call Center Supervisor"}):
		return
	add_permission("Call Log", "Call Center Supervisor", permlevel=0)
	for ptype in ("write", "report", "export", "email", "share"):
		update_permission_property("Call Log", "Call Center Supervisor", 0, ptype, 1)


def _seed_call_types():
	if not frappe.db.exists("DocType", "Telephony Call Type"):
		return
	for label in CALL_TYPES:
		if frappe.db.exists("Telephony Call Type", {"call_type": label}):
			continue
		doc = frappe.get_doc({"doctype": "Telephony Call Type", "call_type": label})
		doc.insert(ignore_permissions=True)
		doc.submit()
