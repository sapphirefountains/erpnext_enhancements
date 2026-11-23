// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Where Is This Item: every place an item is, and whose it is (v1.805.0).
 * The reasoning lives in where_is_this_item.py.
 *
 * Every filter is optional. With nothing chosen the report lists every item
 * that is on hand or on order anywhere. An Item wins over a search.
 */
frappe.query_reports["Where Is This Item"] = {
	filters: [
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			// Words from the item's code or name; every word has to match.
			fieldname: "search",
			label: __("Or search"),
			fieldtype: "Data",
		},
		{
			// A group includes the groups under it.
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{
			// An item's empty bin is still one of its places.
			fieldname: "include_empty",
			label: __("Assigned places with none on hand"),
			fieldtype: "Check",
			default: 1,
		},
	],

	// "Show on map" (v1.818.0): the Warehouse Map page with the chosen Item lit up. A Desk route,
	// so Back returns to this report with its filters.
	onload(report) {
		// Offered once a map exists (frappe.boot.ee_warehouse_map, boot.py).
		if (!frappe.boot.ee_warehouse_map) return;
		report.page.add_inner_button(__("Show on map"), () => {
			const item = report.get_filter_value("item_code");
			if (!item) {
				frappe.show_alert({ message: __("Choose an Item first, then Show on map."), indicator: "orange" }, 6);
				return;
			}
			frappe.set_route("warehouse-map", { item });
		});
	},

	formatter(value, row, column, data, default_formatter) {
		const face = window.erpnext_enhancements?.report_face;
		const formatted = face?.format
			? face.format(value, row, column, data, default_formatter)
			: default_formatter(value, row, column, data);
		// A place with none on hand reads lighter than one that holds stock.
		if (data && !data.on_hand && column.fieldname !== "on_order") {
			return `<span style="opacity: .6;">${formatted}</span>`;
		}
		return formatted;
	},
};
