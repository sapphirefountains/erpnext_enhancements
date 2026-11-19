# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the printable rack QR sheet at ``/rack-qr-sheet`` (v1.831.0).

One printed page per rack: each shelf's QR code with its position (Shelf 1 is the top shelf), meant
to be laminated and hung low beside a rack whose shelves are too high, or their edges too thin, for a
sticker (the north-facing Atrium racks, from the 2026-11-19 operations review). The codes are the
location labels' own (``<site>/stock-scan?w=<warehouse>``); what goes on the page is
``inventory_enhancements/rack_sheet_rules.py`` and the reads are ``warehouse_labels.rack_sheets``.

**The filename matters.** Frappe imports a ``www`` controller by the template's basename with hyphens
turned into underscores, so ``rack-qr-sheet.html`` needs ``rack_qr_sheet.py``
(``scripts/check_www_controllers.py`` guards it in CI).

Server-rendered like ``/warehouse-labels``: every code is an inline SVG drawn here, so what prints is
what the preview shows. The page's only script is the Print button and the rack picker's submit.

Query parameters:

* ``w``: one or more group warehouses (repeatable). The Warehouse form's **Rack QR Sheet** button
  sends its own. A group whose Warehouse Map box is a Room prints one page per rack inside it.

Who: the labels page's roles (``stock_scan_rules.LABEL_ROLES``). A signed-out visitor is sent to log
in and comes back to the same address.
"""

import frappe
from frappe import _
from frappe.utils import format_date, nowdate

from erpnext_enhancements.inventory_enhancements import rack_sheet_rules as sheet_rules
from erpnext_enhancements.inventory_enhancements import stock_scan_rules as rules
from erpnext_enhancements.inventory_enhancements.warehouse_labels import group_options, rack_sheets

# Warehouse names for a signed-in user; never serve one visitor's render to another.
no_cache = 1


def get_context(context):
	"""Render the rack sheets, or send a signed-out visitor to log in and come back."""
	if frappe.session.user in ("", None, "Guest"):
		frappe.local.flags.redirect_location = rules.login_redirect(
			frappe.request.full_path if frappe.request else "/rack-qr-sheet"
		)
		raise frappe.Redirect

	if not rules.LABEL_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You do not have permission to print warehouse labels."), frappe.PermissionError)

	args = frappe.request.args if frappe.request else {}
	names = [
		name.strip()
		for name in (args.getlist("w") if hasattr(args, "getlist") else [])
		if name and name.strip()
	]

	context.no_cache = 1
	context.names = names
	context.pages = rack_sheets(names) if names else []
	context.groups = group_options()
	context.printed_on = format_date(nowdate())
	context.page_width = sheet_rules.PAGE_WIDTH
	context.page_height = sheet_rules.PAGE_HEIGHT
	context.margin = sheet_rules.MARGIN
	context.content_width = round(sheet_rules.PAGE_WIDTH - 2 * sheet_rules.MARGIN, 3)
	context.position_width = sheet_rules.POSITION_WIDTH
	context.caption_height = sheet_rules.CAPTION_HEIGHT
	context.gap = sheet_rules.GAP
	return context
