"""Seed the hand-off gate settings on existing installs (v1.250.0).

The new knobs on **ERPNext Enhancements Settings → Hand-Off Process** carry field
defaults, but a ``default`` only applies at doc *creation* — the Settings Single
already exists on every live site, so the fields come up blank there and the gate
would ship switched off by accident. Same trap as
``default_po_approval_threshold``: fill only when unset, so an operator who
deliberately unticks the gate (or points the escalation somewhere else) is never
clobbered.

The attendee rows are seeded insert-only and only when the table is completely
empty, so a hand-edited list survives a re-run. Defaults are the three
distribution addresses agreed in the 2026-08-06 process meeting; they are rows in
a settings table precisely so changing them is never a deploy.
"""

import frappe

#: Blank-fill defaults for the scalar knobs. Checks are included deliberately —
#: an unsaved Single reports None, which is not the same as a deliberate 0.
SCALAR_DEFAULTS = {
	"handoff_gate_enabled": 1,
	"handoff_invoice_flow": "Manual Billing Email",
	"handoff_escalation_fallback": "operations@sapphirefountains.com",
}

#: (function, email) — seeded only into an empty table.
ATTENDEE_DEFAULTS = (
	("Sales", "sales@sapphirefountains.com"),
	("Production", "production@sapphirefountains.com"),
	("Billing", "billing@sapphirefountains.com"),
)


def execute():
	if not frappe.db.exists("DocType", "ERPNext Enhancements Settings"):
		return

	# Single doctypes keep their values in `tabSingles`, so the usual has_column
	# guard does not apply — check the meta instead, or an unsynced field would be
	# silently created as a stray Singles row.
	meta = frappe.get_meta("ERPNext Enhancements Settings")
	for field, default in SCALAR_DEFAULTS.items():
		if not meta.get_field(field):
			continue
		if frappe.db.get_single_value("ERPNext Enhancements Settings", field) in (None, ""):
			frappe.db.set_single_value("ERPNext Enhancements Settings", field, default)

	if not frappe.db.exists("DocType", "Hand-Off Attendee Role"):
		return
	existing = frappe.db.count(
		"Hand-Off Attendee Role",
		{"parenttype": "ERPNext Enhancements Settings", "parentfield": "handoff_attendees"},
	)
	if existing:
		return

	settings = frappe.get_single("ERPNext Enhancements Settings")
	for idx, (function, email) in enumerate(ATTENDEE_DEFAULTS, start=1):
		settings.append("handoff_attendees", {"function": function, "email": email, "idx": idx})
	settings.save(ignore_permissions=True)
