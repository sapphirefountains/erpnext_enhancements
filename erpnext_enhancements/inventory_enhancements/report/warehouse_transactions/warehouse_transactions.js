// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Warehouse Transactions: every stock movement and the person who made it
 * (v1.797.0, TASK-2026-03136). The reasoning lives in warehouse_transactions.py.
 *
 * Only the dates are required. Item, Location, Project, User and Kind may be
 * left blank, which is why this is a Script Report: the report view sends only
 * the filters that hold a value, and a Query Report's SQL fails on the missing
 * ones.
 *
 * v1.831.0 (the 2026-11-19 operations review): a View (the movements, or totals
 * By person or By item), a Project, a Kind, and a Location label that says a room
 * or a rack includes every shelf under it. The Bin Manager opens this report with
 * its bin or its job filled in (public/js/bin_manager/logic.js transactions_route).
 *
 * v1.841.0: Exclude my entries, a box that leaves the signed-in person's own
 * movements out (stock_movement_rules.owner_filter).
 */
frappe.query_reports["Warehouse Transactions"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			// The last 30 days; DEFAULT_DAYS in the Python is the same number.
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			// The rows, or one total per person (everyone who keeps stock, zeros
			// included) or per item. stock_movement_rules.VIEWS holds the same three.
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Movements", "By person", "By item"].join("\n"),
			default: "Movements",
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			// A room or a rack (a group warehouse) includes every shelf and bin under it.
			fieldname: "warehouse",
			label: __("Location (room, rack or bin)"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			// Stock moved for this job: rows carrying the job, and everything at its job site.
			fieldname: "project",
			label: __("Project"),
			fieldtype: "Link",
			options: "Project",
		},
		{
			// The person who made the movement (the entry's owner). Disabled
			// accounts stay pickable, so a leaver's history can still be read.
			fieldname: "user",
			label: __("User"),
			fieldtype: "Link",
			options: "User",
			get_query: () => ({ filters: { user_type: "System User" } }),
		},
		{
			// Leave out the signed-in person's own movements, to see how everyone
			// else has used the warehouse (v1.841.0, TASK-2026-03290). Off unless
			// ticked; it narrows the rows, both total views and the cards, with
			// every other filter still applied. With User set to yourself as well,
			// nothing can match and the report says so.
			fieldname: "exclude_mine",
			label: __("Exclude my entries"),
			fieldtype: "Check",
			default: 0,
		},
		{
			// What the movement was. stock_movement_rules.KINDS holds the same list.
			fieldname: "kind",
			label: __("Kind"),
			fieldtype: "Select",
			options: [
				"",
				"Take",
				"Receive",
				"Add",
				"Move",
				"Count",
				"Return to supplier",
				"Delivered",
				"Other",
			].join("\n"),
		},
	],
};
