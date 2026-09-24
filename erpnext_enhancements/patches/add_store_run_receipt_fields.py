"""Three Purchase Receipt fields for "Bought on a store run" on the Stock Scan page (v1.535.0).

Every line a technician records on a store run posts one submitted Purchase Receipt with no
purchase order (``api.stock_scan.store_run``). One trip is usually several lines, so the
receipts of one trip need something in common, and Accounting needs to see the paper:

* ``custom_store_run`` -- the run id the page mints (``sr-<time>-<random>``). Every receipt from
  one trip carries the same value; the store-run KPI groups receipts into trips by it, and
  Accounting filters on it to bill a trip on one Purchase Invoice. ``no_copy``, so a Duplicate
  does not claim to belong to the trip. (An Amend still copies it in v16, which is right: the
  amendment is the same purchase.)
* ``custom_receipt_photo`` -- the photo of the paper receipt, an ``Attach Image``, so Frappe's
  own ``attach_files_to_document`` (an ``on_update`` hook on every doctype) attaches the
  unattached upload to the first receipt of the run and a copy to each later one.
* ``custom_receipt_total`` -- the receipt's total, tax included, as the technician entered it.
  The receipt carries no tax (the site's default template is a setup-wizard placeholder), so
  this is where the tax is visible, and what the KPI matches a card charge on.

Created with ``create_custom_fields`` (``is_system_generated = 1``, so the fixture export skips
them), the way the procurement fields on Purchase Order and the Supplier's store-run flag are.
All three are new columns on a normal doctype with no default, so existing receipts read empty,
which is right: none of them came from a store run.

Also named in ``hooks.after_install``: ``bench install-app`` writes all of patches.txt to the
Patch Log as already run, so a fresh site would otherwise never get the fields, and the page
would say "Store runs are being set up" for ever. Cannot raise; safe twice.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

RUN_FIELD = "custom_store_run"
PHOTO_FIELD = "custom_receipt_photo"
TOTAL_FIELD = "custom_receipt_total"


def execute():
	try:
		create_custom_fields(
			{
				"Purchase Receipt": [
					{
						"fieldname": RUN_FIELD,
						"fieldtype": "Data",
						"label": "Store Run",
						"insert_after": "supplier_delivery_note",
						"read_only": 1,
						"no_copy": 1,
						"search_index": 1,
						"in_standard_filter": 1,
						"description": (
							"Set by the Stock Scan page. Every receipt from one trip carries the same value."
						),
					},
					{
						"fieldname": PHOTO_FIELD,
						"fieldtype": "Attach Image",
						"label": "Receipt Photo",
						"insert_after": RUN_FIELD,
						"read_only": 1,
						"no_copy": 1,
						"depends_on": RUN_FIELD,
					},
					{
						"fieldname": TOTAL_FIELD,
						"fieldtype": "Currency",
						"label": "Receipt Total (Tax Included)",
						"insert_after": PHOTO_FIELD,
						"options": "currency",
						"read_only": 1,
						"no_copy": 1,
						"depends_on": RUN_FIELD,
						"description": (
							"The paper receipt's total, tax included, as the technician entered it. "
							"Every receipt of one trip carries the same total."
						),
					},
				]
			},
			update=True,
		)
	except Exception:
		# Never raises: a patch that raises aborts `bench migrate`, which on this repo is the
		# deploy. Without the fields the page keeps the door shut and says why.
		frappe.log_error(
			f"add_store_run_receipt_fields failed\n{frappe.get_traceback()}", "Stock Scan store runs"
		)
