"""One-time migration patch (post_model_sync; listed in patches.txt).

Respells Sapphire Maintenance Contract's terminal status **Cancelled →
Canceled**, matching Project and Task (which already use the single-l spelling
via their status property setters) so the same state does not read two
different ways across the app.

The Select option itself moves with the doctype JSON; this carries the stored
rows over, because Frappe validates a Select's value on save and a contract
left on the old spelling would refuse to save with "Cancelled is not a valid
value" the next time anyone touched it.
"""

import frappe

OLD = "Cancelled"
NEW = "Canceled"


def execute():
	if not frappe.db.exists("DocType", "Sapphire Maintenance Contract"):
		return

	stale = frappe.get_all(
		"Sapphire Maintenance Contract", filters={"status": OLD}, pluck="name"
	)
	if not stale:
		return

	# update_modified stays off: this is a spelling correction, not a change to
	# any contract, and it must not look like someone edited the agreement.
	for name in stale:
		frappe.db.set_value(
			"Sapphire Maintenance Contract", name, "status", NEW, update_modified=False
		)
	frappe.db.commit()
