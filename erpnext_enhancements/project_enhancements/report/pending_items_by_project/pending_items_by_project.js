// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Pending Items by Project — what was bought for this job and has not turned up.
 *
 * A Query Report, so the whole thing is the SQL in
 * `pending_items_by_project.json`; this file is the filter and the colouring.
 * The reasoning that would otherwise live in a report docstring lives here.
 *
 * **The project match is a union, not `Purchase Order Item.project`.** The
 * item-row project is mandatory (Property Setter `Purchase Order Item-project-reqd`,
 * WI-014) and `procurement_project.cascade_project_to_items` fills blanks from the
 * header on save — but blanks only, on save only. Rows written before that hook, or
 * through a path that bypasses it, still carry one and not the other. On production
 * today 40 of the 148 live pending lines have a blank row project and **32 of those
 * sit under an order whose header names the job**. Run against PRJ-00566, the job
 * with the most outstanding material, the union returns **63 rows and the row-only
 * match returns 37** — a report that quietly loses two fifths of a job is worse than
 * no report, because it is the same shape as good news. So the SQL reads
 * `ifnull(nullif(poi.project, ''), po.project)` — the row wins, the header is the
 * fallback. Same rule as `api/pickup_routing._project_po_names`.
 *
 * **Closed and Delivered orders are excluded.** "Not yet delivered" has to mean
 * "still coming"; `Closed` is a deliberate decision to stop chasing an order that
 * came up short, and `Delivered` is a drop-ship that never comes here. Counting
 * either as pending would tell a project manager to expect material nobody is
 * going to bring. That is the same `SETTLED_PO_STATUSES` rule the Pick Routing Map
 * and the Supplier Pickup List use — and it is load-bearing, not cosmetic: 81 of
 * the 123 submitted orders reading `per_received < 100` on this site are Closed.
 *
 * The line-level `qty - received_qty > 0` test is what makes each row true; the
 * order-level `per_received < 100` is the shared rule and lets MariaDB cut most
 * orders before the join.
 */
frappe.query_reports["Pending Items by Project"] = {
	filters: [
		{
			fieldname: "project",
			label: __("Project"),
			fieldtype: "Link",
			options: "Project",
			reqd: 1,
			// Required because a Query Report substitutes its filters straight
			// into the SQL: a blank project compares against NULL and returns
			// nothing rather than erroring, which reads as "all delivered".
			// `frappe.route_options` still pre-fills it when another page routes
			// here with a project.
		},
	],

	// Rows of a Query Report are keyed by the column *label*, not a scrubbed
	// fieldname — `prepare_columns` sets `id: column.fieldname` and
	// `report_utils.prepare_field_from_column` sets `fieldname = label` for the
	// "Label:Fieldtype/Options:Width" form. So this reads `data["Expected
	// Delivery"]`, and renaming that column in the SQL breaks the colouring.
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		const expected = data && data["Expected Delivery"];
		if (expected && frappe.datetime.get_day_diff(frappe.datetime.get_today(), expected) > 0) {
			value = `<span style="color: var(--red-500)">${value}</span>`;
		}

		return value;
	},
};
