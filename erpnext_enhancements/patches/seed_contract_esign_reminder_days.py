"""Seed the signature reminder cadence on existing sites.

Same trap as ``seed_contract_esign_defaults``, and worth stating twice because it
is silent both times: a field ``default`` only applies when the Settings Single
is first created, and that Single already exists everywhere. Without this the
field comes up blank, ``reminder_offsets`` reads "" and returns no offsets, and
the entire reminder feature ships switched off with nothing to indicate it.

**Fill-blanks-only**, so an operator who has deliberately cleared the field to
stop chasing customers keeps that choice on the next migrate.
"""

import frappe

FIELDNAME = "contract_esign_reminder_days"
DEFAULT = "3,7"


def execute():
	if not frappe.get_meta("ERPNext Enhancements Settings").has_field(FIELDNAME):
		return
	if (frappe.db.get_single_value("ERPNext Enhancements Settings", FIELDNAME) or "").strip():
		return
	frappe.db.set_single_value("ERPNext Enhancements Settings", FIELDNAME, DEFAULT)
	frappe.db.commit()
