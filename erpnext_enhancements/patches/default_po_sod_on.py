import frappe


def execute():
	"""Default the WI-066 separation-of-duties gate ON for existing installs.

	The Check field on ERPNext Enhancements Settings ships with ``default "1"``,
	but Frappe only applies a Single doctype's field default when the doc is first
	created. Existing sites already have that Single, so the new field has no row
	in ``tabSingles`` and reads as 0 — the guard would ship permanently dormant and
	silently enforce nothing, which is the worst outcome for a control: it reads as
	shipped in git and does nothing in production.

	Writes 1 once, and only when the value was never explicitly stored, so an
	operator who later switches it off is respected (patches run exactly once, per
	the Patch Log). New sites skip this — the field default covers them at doc
	creation.
	"""
	if not frappe.db.exists("DocType", "ERPNext Enhancements Settings"):
		return

	already_set = frappe.db.sql(
		"select value from tabSingles where doctype = %s and field = %s",
		("ERPNext Enhancements Settings", "po_sod_enforcement_enabled"),
	)
	if already_set:
		return

	frappe.db.set_single_value("ERPNext Enhancements Settings", "po_sod_enforcement_enabled", 1)
