# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the printable warehouse QR labels at ``/warehouse-labels``.

**The filename matters.** Frappe derives this module from the template's basename with
hyphens turned into underscores, so ``warehouse-labels.html`` needs ``warehouse_labels.py``.
A hyphenated controller is never imported and ``get_context`` silently never runs
(``scripts/check_www_controllers.py`` enforces it in CI).

The page is server-rendered: every label's QR code is an inline SVG drawn here, so what
prints is exactly what the preview shows, with no script needed to put a code on paper.
The small inline script in the template only paginates (skip used labels on a part sheet,
untick labels you do not want) and prints.

Query parameters, all optional:

* ``under`` — a group warehouse; print only the locations beneath it.
* ``w`` — one or more warehouse names (repeatable); print exactly these. The Warehouse
  form's **QR Label** button links here this way. Wins over ``under``.
* ``size`` — a key of ``stock_scan_rules.LABEL_PRESETS``; defaults to Avery 5160.
* ``skip`` — how many labels on the first sheet are already used.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements.inventory_enhancements import stock_scan_rules as rules
from erpnext_enhancements.inventory_enhancements.warehouse_labels import group_options, label_rows

# The page shows warehouse names to a signed-in user; never serve one visitor's render to another.
no_cache = 1


def get_context(context):
	"""Render the label sheets, or send a signed-out visitor to log in and come back."""
	if frappe.session.user in ("", None, "Guest"):
		# Encoded whole, so every query parameter comes back after login (stock_scan_rules).
		frappe.local.flags.redirect_location = rules.login_redirect(
			frappe.request.full_path if frappe.request else "/warehouse-labels"
		)
		raise frappe.Redirect

	if not rules.LABEL_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You do not have permission to print warehouse labels."), frappe.PermissionError)

	args = frappe.request.args if frappe.request else {}
	names = [name for name in (args.getlist("w") if hasattr(args, "getlist") else []) if name]
	under = (frappe.form_dict.get("under") or "").strip() or None
	size, preset = rules.label_preset(frappe.form_dict.get("size"))
	per_sheet = preset["columns"] * preset["rows"]
	skip = min(max(cint(frappe.form_dict.get("skip")), 0), per_sheet - 1)

	labels = label_rows(under=None if names else under, names=names)

	context.no_cache = 1
	context.labels = labels
	context.names = names
	context.under = under
	context.groups = group_options()
	context.size = size
	context.preset = preset
	context.presets = [{"value": key, "label": value["title"]} for key, value in rules.LABEL_PRESETS.items()]
	context.per_sheet = per_sheet
	context.skip = skip
	# Geometry for the paginating script. Numbers only, so json is safe inside <script>.
	context.layout_json = json.dumps(
		{"size": size, "perSheet": per_sheet, "skip": skip, "columns": preset["columns"]}
	)
	return context
