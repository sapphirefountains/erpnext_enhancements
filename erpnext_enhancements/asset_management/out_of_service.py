# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Taking rental fountains out of service automatically, and putting them back.

Two sources, both only for Assets marked Available for Event Rental:

* **A Return inspection that found damage** (``Rental Inspection.on_submit``) takes the
  fountain out until someone returns it to service. Only Return: damage found while packing
  is caught before the fountain leaves, and the crew decides there and then.
* **An ERPNext Asset Repair** (``doc_events`` in hooks.py). Pending takes the fountain out
  from the failure date; Completed puts it back on the completion date; Cancelled puts it
  back now.

A fountain already out of service is not taken out twice (the doctype allows one open record
per fountain); the new source is added as a comment on the open record instead.

The Asset Repair hook must never stop a repair being saved, so it runs inside a savepoint and
logs rather than raises. The inspection path raises: an inspection that could not record its
own finding should not be signed off as if it had.
"""

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

from erpnext_enhancements.asset_management.rental_availability import RENTABLE_FIELD

OPEN = "Out of Service"


def is_rentable(asset):
	if not asset or not frappe.db.has_column("Asset", RENTABLE_FIELD):
		return False
	return bool(cint(frappe.db.get_value("Asset", asset, RENTABLE_FIELD)))


def ensure_out_of_service(asset, reason, description, source_doctype, source_name, out_from=None):
	"""Open an out-of-service record for ``asset``, or note the new source on the open one.

	Returns the record's name, or None for a fountain that is not in the rental fleet.
	"""
	if not is_rentable(asset):
		return None
	existing = frappe.db.get_value("Asset Out of Service", {"asset": asset, "status": OPEN}, "name")
	if existing:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "Asset Out of Service",
				"reference_name": existing,
				"content": _("Also reported by {0} {1}: {2}").format(
					_(source_doctype), source_name, frappe.utils.escape_html(description or "")
				),
			}
		).insert(ignore_permissions=True)
		return existing
	doc = frappe.get_doc(
		{
			"doctype": "Asset Out of Service",
			"asset": asset,
			"reason": reason,
			"description": description,
			"out_from": out_from or now_datetime(),
			"source_doctype": source_doctype,
			"source_name": source_name,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def return_for_source(source_doctype, source_name, returned_on=None):
	"""Return to service every open record that ``source`` opened."""
	for name in frappe.get_all(
		"Asset Out of Service",
		filters={"source_doctype": source_doctype, "source_name": source_name, "status": OPEN},
		pluck="name",
	):
		doc = frappe.get_doc("Asset Out of Service", name)
		doc.mark_returned(returned_on)
		doc.flags.ignore_permissions = True
		doc.save()


def on_asset_repair_change(doc, method=None):
	"""doc_events for Asset Repair: after_insert, on_update, on_submit, on_update_after_submit, on_cancel."""
	asset = getattr(doc, "asset", None)
	if not asset:
		return
	savepoint = "rental_asset_repair"
	frappe.db.savepoint(savepoint)
	try:
		status = getattr(doc, "repair_status", None) or ""
		if method == "on_cancel" or status == "Cancelled":
			return_for_source("Asset Repair", doc.name)
		elif status == "Completed":
			completed = getattr(doc, "completion_date", None)
			return_for_source("Asset Repair", doc.name, get_datetime(completed) if completed else None)
		elif status == "Pending" and cint(doc.docstatus) < 2:
			# Already out — from this repair on an earlier save, or from anything else. A
			# repair is saved many times while Pending; re-noting it each time is noise.
			if frappe.db.exists("Asset Out of Service", {"asset": asset, "status": OPEN}):
				return
			failed = getattr(doc, "failure_date", None)
			ensure_out_of_service(
				asset,
				"Repair",
				getattr(doc, "description", None) or _("Asset Repair {0}").format(doc.name),
				"Asset Repair",
				doc.name,
				out_from=get_datetime(failed) if failed else None,
			)
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(title="Rental out-of-service sync failed", message=frappe.get_traceback())
