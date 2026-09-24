"""Give the inventory and new-item naming KPIs the targets Nik approved (v1.532.0).

Nik, 2026-09-24 (TASK-2026-02238, "go with your recommendations"): the Operations
inventory KPIs added in v1.530.0 and the new-items naming KPI added here take the targets
proposed in the inventory plan and POL-0602. Without a ``KPI Target`` row a metric gets no
Good/Watch/Bad grading and renders as a plain grey number, which is the failure
``seed_item_naming_kpi_target`` (v1.337.0) was written to avoid for the backlog naming KPI.

These are the business's decided numbers, not thresholds invented in code, and the Process
Owner changes them by editing the rows. So a row that already exists is never touched: it
was either seeded by an earlier run or set by somebody on purpose, and either way it wins.
Keyed on the doctype's own ``format:`` name, ``TGT-{department}-{kpi_key}-{period}``.

A zero target is graded "Good at exactly zero, Bad above it" by ``metrics.compute_status``,
which is what these three mean: no stock at the $0.01 placeholder cost, no unpriced PO
lines, no stocked item out.

Each row inserts on its own, so one failure costs one target. Cannot raise; safe twice.
"""

import frappe

DECIDED = "Nik, 2026-09-24 (TASK-2026-02238); POL-0602 v1.0."

# (department, kpi_key, label, target, unit, direction, why)
TARGETS = (
	(
		"Operations",
		"store_runs_30",
		"Store Runs (30d)",
		4,
		"count",
		"Lower is better",
		"About one planned counter trip a week, to be reached by 2027-01-01. 202 in the 12 months to "
		"2026-09-24, roughly 17 a month, so it grades Bad until then.",
	),
	(
		"Operations",
		"items_below_reorder",
		"Stocked Items Below Reorder",
		5,
		"count",
		"Lower is better",
		"A few items waiting for Tuesday's buy is normal; more means the buy day is being missed.",
	),
	(
		"Operations",
		"stocked_items_out",
		"Stocked Items Out of Stock",
		0,
		"count",
		"Lower is better",
		"A stocked item at zero is the store run the kit exists to prevent.",
	),
	(
		"Operations",
		"stocked_items_counted_90",
		"Stocked Items Counted (90d)",
		100,
		"%",
		"Higher is better",
		"Every stocked item counted at least once a quarter.",
	),
	(
		"Operations",
		"placeholder_cost_stock_lines",
		"Stock at Placeholder Cost",
		0,
		"count",
		"Lower is better",
		"Opening stock went in at a $0.01 placeholder rate on 2026-09-23; every line needs a real "
		"cost, to be reached by 2026-11-01 (costing with Lisa, TASK-2026-02214).",
	),
	(
		"Operations",
		"unpriced_po_lines_90",
		"Unpriced PO Lines (90d)",
		0,
		"count",
		"Lower is better",
		"POL-0602 section 4.6: every Purchase Order line carries a price.",
	),
	(
		"Product",
		"item_naming_new_compliance_pct",
		"Item Naming Compliance (New Items)",
		100,
		"%",
		"Higher is better",
		"New Items are held to the Item Naming Schema in full; item_naming_compliance_pct stays the backlog measure.",
	),
)


def execute():
	if not frappe.db.exists("DocType", "KPI Target"):
		# The KPI package is not installed on this site. Nothing to seed, nothing to warn about.
		return
	for department, kpi_key, label, target, unit, direction, why in TARGETS:
		name = f"TGT-{department}-{kpi_key}-Daily"
		if frappe.db.exists("KPI Target", name):
			continue
		try:
			frappe.get_doc(
				{
					"doctype": "KPI Target",
					"department": department,
					"kpi_key": kpi_key,
					"label": label,
					"period": "Daily",
					"target_value": target,
					"unit": unit,
					"direction": direction,
					"notes": f"{why} Seeded by seed_inventory_kpi_targets (v1.532.0). {DECIDED}",
				}
			).insert(ignore_permissions=True)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				f"seed_inventory_kpi_targets: {name} failed\n{frappe.get_traceback()}", "KPI target seed"
			)
