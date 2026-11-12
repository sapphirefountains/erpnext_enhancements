// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Location Contents: what is at a location, and what should be (v1.805.0).
 * The reasoning lives in location_contents.py; the statuses are decided in
 * inventory_enhancements/inventory_report_rules.py.
 *
 * Every filter is optional. A blank Location is every location. "Only problems"
 * starts ticked, so the report opens on the rows somebody should act on; the
 * cards over the table still count every row at the chosen locations.
 */
frappe.query_reports["Location Contents"] = {
	filters: [
		{
			// A bin, or a group: a shelf, a bay, a row, the whole inventory.
			fieldname: "location",
			label: __("Location"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			// A group includes the groups under it.
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{
			// The same words inventory_report_rules.STATUSES holds, in its order.
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: [
				"",
				"Here, assigned to another bin",
				"Here, not assigned",
				"Assigned here, none on hand",
				"Empty, not decided",
				"On order to here",
				"As expected",
				"Held here",
				"Nothing here",
			].join("\n"),
		},
		{
			fieldname: "only_problems",
			label: __("Only problems"),
			fieldtype: "Check",
			default: 1,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const face = window.erpnext_enhancements?.report_face;
		const formatted = face?.format
			? face.format(value, row, column, data, default_formatter)
			: default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data) {
			// is_problem comes from the server, so the list of problems has one home.
			if (data.is_problem) {
				return `<span style="color: var(--red-500); font-weight: 600;">${formatted}</span>`;
			}
			if (data.status === "As expected") {
				return `<span style="color: var(--green-600);">${formatted}</span>`;
			}
		}
		return formatted;
	},
};
