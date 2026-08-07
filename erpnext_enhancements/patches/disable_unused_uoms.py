# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Hide the UOMs nobody uses, so the picker is short (TASK-2026-01238).

ERPNext seeds 240 UOMs. Five are used by business data on this site — ``Unit``,
``Nos``, ``FT``, ``Square Foot`` and ``Gallon`` (renamed from ``Gallon (UK)`` by
:mod:`rename_gallon_uk_uom`, which runs first) — and the rest are noise in every
UOM field on every form.

**Disabled, not deleted, and that is a considered choice.**

The task says "purge". Deleting looked right at first: UOMs are seeded by two
*patches* (``v11_0.uom_conversion_data``, ``v12_0.update_uom_conversion_factor``),
both already in ``tabPatch Log``, so unlike a standard Print Format they do not
come back on migrate. But counting references turned that over:

* **235 of the 240 are referenced by ``UOM Conversion Factor``**, ERPNext's seeded
  conversion matrix. Counting that as "in use" leaves nothing deletable; ignoring
  it and deleting anyway means tearing 235 rows out of ERPNext's own reference
  data and orphaning whatever else points at them.
* ``enabled = 0`` already does the job. ``frappe/desk/search.py`` filters
  ``enabled = 1`` for any doctype with that Check field, so a disabled UOM
  disappears from every link picker — which is exactly what the task asked for
  ("so the list is small and grows as needed"). Growing it again is one tick.
* It is reversible, and it cannot orphan a Link.

Proof the mechanism is safe on a *referenced* record: ``Nos`` was already
``enabled = 0`` on this site while 281 Items used it as their stock UOM, with no
ill effect. Disabling hides a UOM from new entry; it does not invalidate the
documents that already carry it.

Usage is computed at run time from ``INFORMATION_SCHEMA`` rather than hard-coded,
because there are 119 UOM-bearing columns across core, ERPNext and this app, and a
list written today is wrong after the next app install.
"""

import frappe

# Reference data, not usage. `UOM Conversion Factor` is ERPNext's seeded
# from/to matrix and names nearly every UOM; treating it as evidence of use
# would keep all 240. `tabUOM` is the records themselves.
SEEDED_TABLES = {"tabUOM", "tabUOM Conversion Factor"}

# Never disabled whatever the scan says. `Unit` is the site default (see
# `set_default_stock_uom_unit`), and disabling the default would leave every new
# Item pointing at a UOM its own picker refuses to offer.
ALWAYS_KEEP = {"Unit", "Nos"}


def _uom_columns():
	"""Every column on this database that can name a UOM."""
	rows = frappe.db.sql(
		"""
		SELECT TABLE_NAME, COLUMN_NAME
		FROM INFORMATION_SCHEMA.COLUMNS
		WHERE TABLE_SCHEMA = DATABASE()
		  AND (COLUMN_NAME = 'uom' OR COLUMN_NAME LIKE %s OR COLUMN_NAME LIKE %s)
		""",
		("%\\_uom", "uom\\_%"),
	)
	return [(table, column) for table, column in rows if table not in SEEDED_TABLES]


def _in_use():
	names = set()
	for table, column in _uom_columns():
		try:
			rows = frappe.db.sql(
				f"SELECT DISTINCT `{column}` FROM `{table}` "
				f"WHERE `{column}` IS NOT NULL AND `{column}` != ''"
			)
		except Exception:
			# A table that vanishes between the catalogue read and the query (an
			# app uninstalled mid-migrate) must not fail the patch.
			continue
		names.update(value for (value,) in rows)
	return names


def execute():
	if not frappe.db.exists("DocType", "UOM"):
		return
	if not frappe.db.has_column("UOM", "enabled"):
		return

	keep = _in_use() | ALWAYS_KEEP
	default_uom = frappe.db.get_single_value("Stock Settings", "stock_uom")
	if default_uom:
		keep.add(default_uom)

	disabled = 0
	for name in frappe.get_all("UOM", filters={"enabled": 1}, pluck="name"):
		if name in keep:
			continue
		# db.set_value, not doc.save(): 235 saves fire 235 rounds of validation and
		# document events for a one-field flag, and UOM has no controller logic that
		# needs to run.
		frappe.db.set_value("UOM", name, "enabled", 0, update_modified=False)
		disabled += 1

	frappe.db.commit()
	frappe.logger().info(
		f"UOM cleanup: disabled {disabled}, kept {len(keep & set(frappe.get_all('UOM', pluck='name')))} in use"
	)
