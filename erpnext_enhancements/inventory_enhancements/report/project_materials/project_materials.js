// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Project Materials: what was bought for a job, and where each item stands
 * (v1.805.0). The reasoning lives in project_materials.py.
 *
 * The Project filter picks the view. With a Project: one row per item ordered
 * or received for it. Without one: one row per job that holds stock now, on the
 * Project Shelf or at its job site. Item and Show apply to the first view,
 * Customer and "Include finished jobs" to the second.
 */

// The three columns that hold document names ("PUR-ORD-0001, PUR-ORD-0002"), and
// the doctype each names. The cell's value stays the plain names, so export and
// the column search read them as written; only the screen turns them into links.
const SF_PROJECT_MATERIALS_DOCS = {
	purchase_orders: "Purchase Order",
	receipts: "Purchase Receipt",
	supplier_returns: "Supplier Return",
};

frappe.query_reports["Project Materials"] = {
	filters: [
		{
			fieldname: "project",
			label: __("Project"),
			fieldtype: "Link",
			options: "Project",
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			// The same words inventory_report_rules.SHOW_OPTIONS holds.
			fieldname: "show",
			label: __("Show"),
			fieldtype: "Select",
			options: ["Everything", "Still to come", "On the shelf or at the site", "Received, not located"].join(
				"\n"
			),
			default: "Everything",
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			// Stock left at a finished job is the stock most worth finding.
			fieldname: "include_finished",
			label: __("Include finished jobs"),
			fieldtype: "Check",
			default: 1,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const doctype = SF_PROJECT_MATERIALS_DOCS[column.fieldname];
		if (doctype && value) {
			return String(value)
				.split(", ")
				.filter(Boolean)
				.map((name) => {
					const href = frappe.utils.get_form_link(doctype, name);
					return `<a href="${frappe.utils.escape_html(href)}">${frappe.utils.escape_html(name)}</a>`;
				})
				.join(", ");
		}
		const face = window.erpnext_enhancements?.report_face;
		const formatted = face?.format
			? face.format(value, row, column, data, default_formatter)
			: default_formatter(value, row, column, data);
		if (!data) return formatted;
		if (column.fieldname === "still_to_come" && data.still_to_come > 0) {
			return `<span style="color: var(--orange-600); font-weight: 600;">${formatted}</span>`;
		}
		if (column.fieldname === "not_located" && data.not_located > 0) {
			return `<span style="color: var(--red-500);">${formatted}</span>`;
		}
		return formatted;
	},
};
