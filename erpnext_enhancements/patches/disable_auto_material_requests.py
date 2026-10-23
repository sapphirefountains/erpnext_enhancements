"""Stop ERPNext raising Material Requests on its own (v1.730.0, ER-2026-1323810, TASK-2026-02922).

Parker Bailey, approved by Nik on 2026-10-22: "Right now when an item is below the reorder point it
would automatically create a material request for that item ... I think having more control over
requests being sent and made instead of just automatically clogging the material request table
would be a lot more helpful." The Reorder Board (``/desk/reorder-board``) lists what is below its
point and the buyer chooses what to request; the daily digest says what newly fell below.

**What it writes, and only that:** ``Stock Settings.auto_indent`` = 0, one ``tabSingles`` value,
through ``frappe.db.set_single_value``, and only when it is on. ``reorder_email_notify`` is left
as it is: it is read only inside the job this switches off, so it no longer does anything.

**What that stops.** ERPNext's ``erpnext.stock.reorder_item.reorder_item`` (``daily_long``) is

    if cint(frappe.db.get_single_value("Stock Settings", "auto_indent")):
        return _reorder_item()

so with 0 it returns before reading a rule: no Material Request is created and
``send_email_notification`` ("Auto Material Requests Generated") is never reached. Checked against
``erpnext/stock/reorder_item.py`` at the v16.50.0 tag (production) and on the ``version-16`` branch.
The six requests auto-indent already made on production (3 Pending, 3 Stopped, owner
Administrator) are not touched; the Pending ones show on the board's On order list like any other
request.

**The Singles traps (CLAUDE.md).** Writing one field of a Single that was never saved would make
it load from ``tabSingles`` with every other field blank. Stock Settings is saved by ERPNext's
setup wizard and has rows on production, but the patch checks: with no rows at all it writes
nothing (and a never-saved Stock Settings loads ``auto_indent`` as its declared default, which is
0, so there is nothing to switch off). ``tabSingles`` is read with a plain ``select``, never
``db.get_value("Singles", ...)``.

Never raises: a patch that raises aborts ``bench migrate``, which here is the deploy. Safe twice:
the second run finds 0 and writes nothing.
"""

import frappe

SETTINGS = "Stock Settings"
FIELD = "auto_indent"


def execute():
	try:
		disable_auto_indent()
	except Exception:
		frappe.log_error(title="disable_auto_material_requests failed", message=frappe.get_traceback())


def disable_auto_indent():
	"""Switch auto-indent off where it is on. Returns True when it wrote."""
	rows = frappe.db.sql(
		"select field, value from `tabSingles` where doctype = %s",
		(SETTINGS,),
	)
	if not rows:
		print("disable_auto_material_requests: Stock Settings has never been saved; nothing to do")
		return False
	stored = {field: value for field, value in rows}
	if not frappe.utils.cint(stored.get(FIELD)):
		print("disable_auto_material_requests: auto_indent is already off")
		return False
	frappe.db.set_single_value(SETTINGS, FIELD, 0)
	frappe.clear_document_cache(SETTINGS, SETTINGS)
	print("disable_auto_material_requests: Stock Settings.auto_indent 1 -> 0")
	return True
