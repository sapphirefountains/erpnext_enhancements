# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Audit-log helper for the Accounting Document Intake pipeline. Mirrors
``google_drive.drive_sync.log_sync``: writes one ``Accounting Intake Log`` row
and never raises (a logging failure must not break the action being logged)."""

import json

import frappe


def log_intake(
	action,
	status,
	*,
	accounting_document=None,
	reference_doctype=None,
	reference_name=None,
	detail=None,
	error=None,
	payload=None,
	attempts=1,
):
	"""Write one Accounting Intake Log row. Never raises.

	``payload`` (a dict of ``{"method": ..., "kwargs": {...}}``) is stored as
	JSON on Failed rows so the nightly retry job can re-enqueue the work."""
	try:
		frappe.get_doc(
			{
				"doctype": "Accounting Intake Log",
				"action": action,
				"status": status,
				"accounting_document": accounting_document,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"detail": (detail or "")[:140] or None,
				"error": (error or "")[:1000] or None,
				"payload": json.dumps(payload) if payload else None,
				"attempts": attempts,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Accounting Intake Log write failed")
