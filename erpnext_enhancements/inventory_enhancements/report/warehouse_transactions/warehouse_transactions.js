// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Warehouse Transactions: every stock movement and the person who made it
 * (v1.797.0, TASK-2026-03136). The reasoning lives in warehouse_transactions.py.
 *
 * Only the dates are required. Item, Warehouse and User may be left blank, which
 * is why this is a Script Report: the report view sends only the filters that
 * hold a value, and a Query Report's SQL fails on the missing ones.
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
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			// A group warehouse includes every bin under it.
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
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
	],
};
