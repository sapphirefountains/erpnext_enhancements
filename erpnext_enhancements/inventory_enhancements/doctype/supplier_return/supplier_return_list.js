// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Supplier Return list (v1.807.0): a color per step, days at the step, and one click to the
 * returns that are waiting on money.
 *
 * The sidebar's group-by already offers Status (it is a standard filter). The Kanban view by
 * Status is the list's own "Kanban" switch; no board is seeded, because a board is a record a
 * person may rearrange and a migrate must not put it back.
 *
 * "Waiting on money" sets an ordinary list filter (`waiting_on_money = 1`, a stored Check the
 * server keeps true while a return is Sent Back and a refund or credit is still owed), so it
 * lands in the URL like any filter and Back takes it off again.
 */
frappe.listview_settings["Supplier Return"] = {
	add_fields: ["status", "status_changed_on", "requested_resolution", "money_source"],
	get_indicator(doc) {
		const colors = {
			Draft: "gray",
			Requested: "orange",
			"Approved by Supplier": "blue",
			Denied: "red",
			"Sent Back": "yellow",
			"Refund Received": "green",
			"Credit Received": "green",
			"Replacement Received": "green",
			"Closed Without Credit": "darkgrey",
			Canceled: "darkgrey",
		};
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
	formatters: {
		status(value, df, doc) {
			const open = ["Draft", "Requested", "Approved by Supplier", "Sent Back"].includes(doc.status);
			if (!open || !doc.status_changed_on) return frappe.utils.escape_html(value || "");
			const days = Math.max(frappe.datetime.get_day_diff(frappe.datetime.get_today(), doc.status_changed_on), 0);
			const waited = days === 1 ? __("1 day") : __("{0} days", [days]);
			return `${frappe.utils.escape_html(value || "")} <span class="text-muted">· ${waited}</span>`;
		},
	},
	onload(listview) {
		listview.page.add_inner_button(__("Waiting on Money"), () => {
			listview.filter_area.add([["Supplier Return", "waiting_on_money", "=", 1]]);
		});
		listview.page.add_inner_button(__("Money Outstanding Report"), () => {
			frappe.set_route("query-report", "Supplier Returns Money Outstanding");
		});
	},
};
