"""Give the 157 existing Purchase Orders a truthful Order Stage (v1.289.0).

**This patch must not key on an empty `custom_order_stage`, and that is the whole point
of writing it down.** `Purchase Order` is a normal doctype, so adding a column with a
`default` is one `ALTER` and MariaDB writes the default into every existing row as part of
it. By the time this runs, all 157 orders already read `Created` — nothing is empty, and
`where coalesce(custom_order_stage, '') = ''` would match zero rows, commit, and record
itself in `tabPatch Log` as a success. That is not a hypothetical: it is exactly how
`backfill_relay_auth_identity` failed silently in v1.280.3, and the field there had a
`default` for the same reason this one does.

(The opposite storage model bites the opposite way — a `default` on a new field of a
*Single* never reaches the existing row at all. Which trap you are in depends on the
doctype, and neither answer is the one you assume. See CLAUDE.md.)

So the predicate is the writer's own rule instead: `po_order_stage.backfill_stage_for`
maps `docstatus` / `status` / `per_received` to a stage, and only rows whose computed
stage differs from what they hold are touched. The `Created` guard means a re-run cannot
stomp a stage somebody has since set by hand.

Counts are logged per stage. A backfill that quietly matches nothing looks identical to
one that worked, and the log line is the only thing that tells them apart afterwards.
"""

import frappe

from erpnext_enhancements.po_order_stage import DEFAULT_STAGE, FIELD, backfill_stage_for


def execute():
	if not frappe.db.has_column("Purchase Order", FIELD):
		# Loudly, not quietly. If add_po_order_stage_field did not run, or ran without
		# creating the column, a silent return here is indistinguishable from a backfill
		# that had nothing to do -- which is the failure this patch's docstring is about.
		frappe.log_error(
			f"Purchase Order.{FIELD} does not exist; the Order Stage backfill did nothing. "
			"Check that patches.txt runs add_po_order_stage_field first.",
			"Order stage backfill skipped",
		)
		return

	rows = frappe.get_all(
		"Purchase Order",
		fields=["name", "docstatus", "status", "per_received", FIELD],
		limit_page_length=0,
	)

	counts = {}
	for row in rows:
		target = backfill_stage_for(row.docstatus, row.status, row.per_received)
		current = row.get(FIELD)
		if target == current:
			continue
		# Only ever move a row off the default. Anything else is a human's answer and
		# outranks this one -- which matters on a re-run, not on the first pass.
		if current and current != DEFAULT_STAGE:
			continue
		frappe.db.set_value("Purchase Order", row.name, FIELD, target, update_modified=False)
		counts[target] = counts.get(target, 0) + 1

	frappe.db.commit()
	frappe.logger().info(
		f"Order stage backfill: {len(rows)} orders examined, "
		f"{sum(counts.values())} updated ({counts or 'none'})"
	)
