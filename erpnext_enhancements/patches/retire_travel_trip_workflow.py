"""Retire the legacy Travel Trip Workflow (v1.15.0 travel redesign).

Travel Trip is now a non-submittable doctype with a plain ``status`` Select —
the workflow engine is no longer involved. The fixture files no longer carry
the workflow (see fixtures/workflow.json), but per fixtures/README.md removing
a record from a fixture only stops managing it; this patch performs the actual
deletion.

Deletes the Workflow, its Workflow Action queue rows, and the four
travel-unique Workflow States (Requested / Booking in Progress /
Ready for Travel / Expense Review) — the latter only when no other workflow
still references them. Shared/stock states (Draft, Approved, Rejected,
In Progress, Closed) are left alone even if orphaned: they are harmless
masters another workflow may pick up.

Idempotent and fresh-install-safe: every deletion is existence-guarded.
"""

import frappe

TRAVEL_ONLY_STATES = (
	"Requested",
	"Booking in Progress",
	"Ready for Travel",
	"Expense Review",
)


def execute():
	if frappe.db.exists("Workflow", "Travel Trip Workflow"):
		# Workflow Action has NO `workflow` column (verified on the live site) —
		# its pending-approval rows point at documents, so clear them by
		# reference. The drop_legacy_travel_trips patch already removed the
		# trip-referencing rows; this is an idempotent belt-and-braces sweep.
		frappe.db.delete("Workflow Action", {"reference_doctype": "Travel Trip"})
		frappe.delete_doc("Workflow", "Travel Trip Workflow", force=True, ignore_permissions=True)

	for state in TRAVEL_ONLY_STATES:
		if not frappe.db.exists("Workflow State", state):
			continue
		if frappe.db.exists("Workflow Document State", {"state": state}):
			continue  # still referenced by another workflow
		frappe.delete_doc("Workflow State", state, force=True, ignore_permissions=True)
