"""Default the PO approval threshold to $500 on existing installs (v1.172.0).

`po_approval_threshold` on ERPNext Enhancements Settings carries a field default of
500, but a `default` only applies at doc creation — the Settings Single already
exists on live sites, so the new field comes up blank. Set it to 500 only when
unset, so an operator who deliberately sets 0 (disabled) or another value is never
clobbered. Idempotent (runs once; no-op if already set).
"""

import frappe


def execute():
	if frappe.db.get_single_value("ERPNext Enhancements Settings", "po_approval_threshold") is None:
		frappe.db.set_single_value("ERPNext Enhancements Settings", "po_approval_threshold", 500)
