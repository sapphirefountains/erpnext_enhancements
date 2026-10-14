"""Create the Boneyard warehouse and point Inventory Scanner Settings at it (v1.689.0).

Stock Scan's *Send to boneyard* (``inventory_enhancements.bin_assignments.send_to_boneyard``)
moves stock a bin no longer keeps to one dedicated warehouse, read from
``Inventory Scanner Settings.boneyard_warehouse``. This patch, in this order:

1. **Creates the warehouse**: one leaf Warehouse, ``warehouse_name`` "Boneyard", under
   :data:`PARENT` (``Inventory - SF``, the group that holds ``Inventory Room - SF``), with the
   parent's company and no ``warehouse_type`` -- not Transit, so Stock Scan and the QR label
   sheet treat it like any other location. ERPNext names it ``Boneyard - SF``. An existing one
   is found by ``warehouse_name`` and company, never by a hard-coded docname, and reused.
   Skipped quietly when the parent does not exist or is not a group (a fresh or test site).
2. **Fills the setting** with that warehouse, **only where ``tabSingles`` has no row for
   ``boneyard_warehouse``** -- never over a stored value, an emptied one included: a Stock
   Manager who cleared it switched the action off on purpose (the same rule as
   ``backfill_marketing_settings_defaults``). A Single that has never been saved at all is left
   alone too: writing its first row would make every other field without a row stop reading
   its declared default (CLAUDE.md, "the inverse bites too"). Production's was saved on
   2026-06-13. The value is read back through ``get_settings()``, never ``db.get_value("Singles")``.

The two warehouses already called ``Bin D2-2-1 (Boneyard) - SF`` and ``Bin D2-2-2 (Boneyard) -
SF`` on production (both empty, neither anybody's default) are not touched.

Cannot raise -- a patch that raises aborts ``bench migrate``, which on this repo is the deploy.
Each step runs in its own savepoint and logs to the Error Log if it fails. Safe twice: the
second run finds the warehouse and the setting's row.
"""

import frappe

PARENT = "Inventory - SF"
NAME = "Boneyard"
SETTINGS = "Inventory Scanner Settings"
FIELD = "boneyard_warehouse"
SAVEPOINT = "add_boneyard_warehouse"


def _say(text):
	try:
		print(text)
	except Exception:
		pass


def ensure_warehouse():
	"""The Boneyard warehouse's name, created if missing; ``None`` when there is no parent."""
	parent = frappe.db.get_value("Warehouse", PARENT, ["name", "company", "is_group"], as_dict=True)
	if not parent or not parent.is_group or not parent.company:
		return None
	existing = frappe.db.get_value("Warehouse", {"warehouse_name": NAME, "company": parent.company}, "name")
	if existing:
		return existing
	doc = frappe.get_doc(
		{
			"doctype": "Warehouse",
			"warehouse_name": NAME,
			"parent_warehouse": parent.name,
			"company": parent.company,
			"is_group": 0,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def fill_setting(warehouse):
	"""Write ``warehouse`` into the setting where it has no ``tabSingles`` row. True if written."""
	if not warehouse or not frappe.db.exists("DocType", SETTINGS):
		return False
	stored = {row[0] for row in frappe.db.sql("select field from tabSingles where doctype = %s", (SETTINGS,))}
	if not stored or FIELD in stored:
		return False
	frappe.db.set_single_value(SETTINGS, FIELD, warehouse)
	frappe.clear_document_cache(SETTINGS, SETTINGS)
	return True


def _step(label, fn, *args):
	try:
		frappe.db.savepoint(SAVEPOINT)
		return fn(*args)
	except Exception:
		try:
			frappe.db.rollback(save_point=SAVEPOINT)
		except Exception:
			pass
		try:
			frappe.log_error(
				title=f"Boneyard warehouse patch: {label} failed", message=frappe.get_traceback()
			)
		except Exception:
			pass
		return None


def execute():
	warehouse = _step("creating the warehouse", ensure_warehouse)
	if not warehouse:
		_say("add_boneyard_warehouse: no Boneyard warehouse (no parent, or it failed); nothing set.")
		return
	written = _step("filling the setting", fill_setting, warehouse)
	_say(f"add_boneyard_warehouse: {warehouse}; setting {'filled' if written else 'left as it was'}.")
