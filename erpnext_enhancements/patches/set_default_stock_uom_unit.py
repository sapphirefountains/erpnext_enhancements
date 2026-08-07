# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Make ``Unit`` the default UOM for new Items (TASK-2026-01239).

Production already had this set by hand; the repo did not, so a fresh site got
ERPNext's ``Nos``. This makes it durable.

**Saved through the controller, not written with ``db.set_value``.** That is the
whole trick. ``Item.stock_uom`` carries no default in its own JSON — the default
arrives from ``tabDefaultValue``, and the only thing that writes it is
``Stock Settings.on_update``:

    frappe.db.set_default(key, self.get(key, ""))

A direct ``db.set_value`` on the Single updates the stored setting and never fires
``on_update``, so the Settings page would read "Unit" while every new Item still
defaulted to the old value — correct-looking and wrong, which is the failure mode
this codebase keeps meeting.

Idempotent: bails when it is already ``Unit``, so re-running costs one read.
"""

import frappe

DEFAULT_UOM = "Unit"


def execute():
	if not frappe.db.exists("DocType", "Stock Settings"):
		return
	if not frappe.db.exists("UOM", DEFAULT_UOM):
		# Nothing to point at. Better a log line than a Link validation error that
		# fails the whole migrate.
		frappe.log_error(
			f"Cannot set the default stock UOM: no UOM named '{DEFAULT_UOM}' exists.",
			"Default stock UOM",
		)
		return

	settings = frappe.get_single("Stock Settings")
	if settings.get("stock_uom") == DEFAULT_UOM:
		return

	settings.stock_uom = DEFAULT_UOM
	# save(), so on_update runs and frappe.db.set_default writes the value new
	# Items actually read.
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	frappe.logger().info(f"Stock Settings: default stock UOM set to {DEFAULT_UOM}")
