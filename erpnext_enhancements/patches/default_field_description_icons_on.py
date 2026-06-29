import frappe


def execute():
	"""Default the new "Field Description Info Icons" toggle ON for existing installs.

	The Check field on ERPNext Enhancements Settings ships with ``default "1"``,
	but Frappe only applies a Single doctype's field default when the doc is
	first created. Existing sites already have that Single doc, so the new field
	has no row in ``tabSingles`` and reads as 0 (Frappe casts a missing Check to
	0, not None). This writes 1 once — and only when the value was never
	explicitly stored — so the feature is on by default after upgrade. It runs
	exactly once (recorded in Patch Log), so a user who later unchecks it is
	respected. New sites skip this (the field default covers them at doc
	creation).
	"""
	if not frappe.db.exists("DocType", "ERPNext Enhancements Settings"):
		return

	already_set = frappe.db.sql(
		"select value from tabSingles where doctype = %s and field = %s",
		("ERPNext Enhancements Settings", "field_description_icons_enabled"),
	)
	if already_set:
		return

	frappe.db.set_single_value(
		"ERPNext Enhancements Settings", "field_description_icons_enabled", 1
	)
