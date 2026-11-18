# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Serial numbers, builders and warranty on product units (TASK-2026-03230).

* ``prepare_configured_item`` (Item ``before_validate``): a configured product kept in stock gets
  serial numbers, a serial number series, a warranty period and the pre-shipment test template,
  each only where it is blank. ``before_validate`` rather than ``validate`` so ERPNext's own Item
  checks see the values: it refuses to change Has Serial No once stock has moved, and this never
  asks it to (it only turns it on for an Item with no stock ledger yet).
* ``stamp_builders`` (Stock Entry ``on_submit``): when a unit's Manufacture entry is submitted, each
  serial number it made records who built it (``custom_built_by``, from the Work Order) and which
  Work Order made it (ERPNext's own ``Serial No.work_order``, which it fills itself only when
  Manufacturing Settings makes serials at Work Order submit, off on prod).
* ``set_unit_builder`` (whitelisted, System Manager): records the builder of a unit that came from
  no Work Order (opening stock, a unit received some other way), where the gate would otherwise
  refuse it for ever. Only on a blank.

Warranty is ERPNext's: delivering a serial number sets its ``warranty_expiry_date`` to the posting
date plus the Item's ``warranty_period`` and copies the period onto the serial
(``serial_batch_bundle.update_serial_no_status_warehouse``, read at v16.54.0); a return clears both.
So the 365 days only has to be on the Item.

Why this lives in a hook and not in ``product_configurator/erp_integration.py``, which makes the
Item: that file is being rewritten by quote to order (#1465) at the same time, and the rule holds
for any configured Item however it was saved, including one created before this release that has
not moved stock yet.
"""

import frappe
from frappe import _

from erpnext_enhancements.product_fulfillment import rules
from erpnext_enhancements.product_fulfillment.settings import get_settings, has_column


def serials_of_row(row):
	"""The serial numbers on one stock line: its Serial No text field, else its bundle."""
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	serials = get_serial_nos(row.get("serial_no")) if row.get("serial_no") else []
	if not serials and row.get("serial_and_batch_bundle"):
		from erpnext.stock.serial_batch_bundle import get_serial_nos_from_bundle

		serials = get_serial_nos_from_bundle(row.get("serial_and_batch_bundle")) or []
	return [s for s in serials if s]


def prepare_configured_item(doc, method=None):
	"""Item ``before_validate``: serial numbers, warranty and the test template on configured stock
	Items. Fills blanks only; never raises."""
	try:
		if not doc.get("custom_source_configuration") or not int(doc.get("is_stock_item") or 0):
			return
		settings = get_settings()
		if not int(doc.get("has_serial_no") or 0) and (doc.is_new() or not _has_stock_ledger(doc.name)):
			doc.has_serial_no = 1
		if int(doc.get("has_serial_no") or 0) and not doc.get("serial_no_series"):
			series = settings.serial_no_series
			doc.serial_no_series = (
				series if rules.valid_serial_series(series) else rules.DEFAULT_SERIAL_SERIES
			)
		if not str(doc.get("warranty_period") or "").strip() and int(settings.warranty_days or 0) > 0:
			doc.warranty_period = str(int(settings.warranty_days))
		template = settings.test_template
		if (
			not doc.get("quality_inspection_template")
			and template
			and frappe.db.exists("Quality Inspection Template", template)
		):
			doc.quality_inspection_template = template
	except Exception:
		frappe.log_error(title="Product Fulfillment: configured Item", message=frappe.get_traceback())


def _has_stock_ledger(item_code):
	return bool(frappe.db.exists("Stock Ledger Entry", {"item_code": item_code, "is_cancelled": 0}))


def stamp_builders(doc, method=None):
	"""Stock Entry ``on_submit``: a Manufacture entry's serial numbers record their builder.

	The builder is the Work Order's ``custom_built_by``; when nobody recorded one, the person
	submitting the Manufacture entry, who is then written back on the Work Order too. Never raises:
	a missing stamp shows up at the gate, which refuses a unit with no builder.
	"""
	try:
		if doc.get("purpose") != "Manufacture" or not doc.get("work_order"):
			return
		if not has_column("Serial No", "custom_built_by") or not has_column("Work Order", "custom_built_by"):
			return
		builder = frappe.db.get_value("Work Order", doc.work_order, "custom_built_by")
		if not builder:
			builder = frappe.session.user
			frappe.db.set_value(
				"Work Order", doc.work_order, "custom_built_by", builder, update_modified=False
			)
		for row in doc.get("items") or ():
			if not int(row.get("is_finished_item") or 0):
				continue
			for serial in serials_of_row(row):
				values = {"custom_built_by": builder}
				if not frappe.db.get_value("Serial No", serial, "work_order"):
					values["work_order"] = doc.work_order
				frappe.db.set_value("Serial No", serial, values, update_modified=False)
		_warn_unfinished_checklist(doc.work_order)
	except Exception:
		frappe.log_error(title="Product Fulfillment: builder stamp", message=frappe.get_traceback())


def _warn_unfinished_checklist(work_order):
	if not frappe.db.exists("DocType", "Work Order Build Step"):
		return
	rows = frappe.get_all(
		"Work Order Build Step",
		filters={"parent": work_order, "parenttype": "Work Order"},
		fields=["step_type", "done"],
	)
	progress = rules.checklist_progress(rows)
	if progress["total"] and not progress["complete"]:
		frappe.msgprint(
			_(
				"{0} of {1} build steps are ticked on Work Order {2}. The unit is recorded as built anyway."
			).format(progress["done"], progress["total"], work_order),
			indicator="orange",
			alert=True,
		)


@frappe.whitelist(methods=["POST"])
def set_unit_builder(serial_no, user):
	"""Record who built a unit that came from no Work Order. System Manager only; blanks only."""
	frappe.only_for("System Manager")
	if not has_column("Serial No", "custom_built_by"):
		frappe.throw(_("Serial numbers have no Built By field on this site yet."))
	if not frappe.db.exists("User", user):
		frappe.throw(_("User {0} not found.").format(user))
	current = frappe.db.get_value("Serial No", serial_no, "custom_built_by")
	if current:
		frappe.throw(_("{0} already records its builder ({1}).").format(serial_no, current))
	frappe.db.set_value("Serial No", serial_no, "custom_built_by", user, update_modified=False)
	frappe.get_doc("Serial No", serial_no).add_comment(
		"Info", _("Built By recorded as {0} by {1}.").format(user, frappe.session.user)
	)
	return {"serial_no": serial_no, "built_by": user}
