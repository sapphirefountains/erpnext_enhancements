"""Default the "Send Fleet Reminders" toggle ON for existing installs (v1.138.0).

A Single doctype applies a field ``default`` only when the doc is first created;
existing sites have no row in ``tabSingles`` for the new field, and
``get_single_value`` casts a missing Check to 0 (not None) — so a default-on
field would read OFF on every upgraded site. Write 1 once, but only if the field
was never set, so a user who later unchecks it is respected. New sites get it
from the field default at doc creation and skip this patch.

The parent ``fleet_maintenance_enabled`` switch ships OFF, so this only takes
effect once an operator enables the Fleet Maintenance suite.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "ERPNext Enhancements Settings"):
		return

	already_set = frappe.db.sql(
		"select value from tabSingles where doctype = %s and field = %s",
		("ERPNext Enhancements Settings", "fleet_reminders_enabled"),
	)
	if already_set:
		return

	frappe.db.set_single_value("ERPNext Enhancements Settings", "fleet_reminders_enabled", 1)
