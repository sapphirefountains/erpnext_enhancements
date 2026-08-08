# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Rename the UOM ``Gallon (UK)`` to ``Gallon`` (TASK-2026-01238).

We buy and sell in gallons and nobody here means the US gallon, so the
disambiguating suffix ERPNext ships is noise on a purchase order.

**Exact match only.** Eight other seeded UOMs contain the word "Gallon" —
``Gallon Dry (US)``, ``Gallon Liquid (US)``, ``Pound/Gallon (UK)``,
``Ounce/Gallon (UK)``, ``Grain/Gallon (UK)`` and their US counterparts. The
``/Gallon`` ones are *densities*, not volumes; a substring rename would turn
``Pound/Gallon (UK)`` into ``Pound/Gallon`` and quietly merge two different units
in a system that prices by them. This renames one record, by name.

``frappe.rename_doc`` is what cascades: it rewrites every Link column pointing at
the old name across the whole database. At the time of writing that is three
business rows (two on Material Request Item, one on Purchase Order Item) plus one
``UOM Conversion Factor`` row — but the count is not the point, the cascade is,
and hand-updating the columns instead would miss whichever one gets added next.

UOM is ``autoname: field:uom_name``, so the field has to move with the name.
Leaving ``uom_name`` at the old value would rename the record back to
``Gallon (UK)`` the next time anybody saved it.

Runs before ``disable_unused_uoms`` so that patch sees the renamed record in use
and leaves it enabled.
"""

import frappe

OLD = "Gallon (UK)"
NEW = "Gallon"


def execute():
	if not frappe.db.exists("DocType", "UOM"):
		return
	if not frappe.db.exists("UOM", OLD):
		# Already renamed, or a site that never had it. Idempotent.
		return

	if frappe.db.exists("UOM", NEW):
		# Both present. Merging is a data decision with a conversion factor
		# attached, not something a migrate should take unilaterally.
		frappe.log_error(
			f"Cannot rename '{OLD}' to '{NEW}': a UOM named '{NEW}' already exists. "
			"Merge them by hand and decide which conversion factors survive.",
			"Gallon UOM rename",
		)
		return

	# Rewrites every Link column that points at the old name, database-wide.
	#
	# NO `ignore_permissions` kwarg. `frappe.rename_doc` on Frappe 16.30 takes
	# (doctype, old, new, force, merge, ignore_if_exists, show_alert,
	# rebuild_search) and nothing else, so passing it raised TypeError — which,
	# because a raising patch aborts the whole `bench migrate`, wedged production
	# migrations from 2026-08-07 13:09 until this fix. Every patch queued behind
	# this one silently never ran. `force=True` already does what was wanted here:
	# it skips the permission and link checks that a migrate has no user to answer.
	frappe.rename_doc("UOM", OLD, NEW, force=True, show_alert=False)

	# autoname is `field:uom_name`, so the field has to agree with the new name or
	# the next save renames it straight back.
	frappe.db.set_value("UOM", NEW, "uom_name", NEW, update_modified=False)
	frappe.db.commit()
	frappe.logger().info(f"UOM renamed: '{OLD}' -> '{NEW}'")
