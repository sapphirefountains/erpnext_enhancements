// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Stock Scan Log list view: one pill per row saying the thing a Stock Manager looks for.
 *
 * Auto-loaded by Frappe as the doctype's list script (same folder; no hooks.py entry).
 * Undone first, because an undone row moved nothing and should read as history. Then the
 * review queue — stock added without a purchase order that nobody has confirmed yet — in
 * orange, since that is the one state that asks for someone's attention (a store run recorded a
 * day late says so, v1.535.0). The rest say which way the stock moved.
 */
frappe.listview_settings["Stock Scan Log"] = {
	add_fields: ["status", "action", "needs_review", "reviewed", "recorded_late"],
	get_indicator(doc) {
		if (doc.status === "Undone") return [__("Undone"), "gray", "status,=,Undone"];
		// A store run recorded the day after it was bought (POL-0602 §4.7 asks for the same day).
		if (doc.needs_review && !doc.reviewed && doc.recorded_late) {
			return [__("Needs Review: Recorded Late"), "orange", "needs_review,=,1|reviewed,=,0|recorded_late,=,1"];
		}
		if (doc.needs_review && !doc.reviewed) return [__("Needs Review"), "orange", "needs_review,=,1|reviewed,=,0"];
		const colors = { Take: "red", Receive: "green", "Add Without PO": "blue", Move: "purple", "Store Run": "orange" };
		return [__(doc.action), colors[doc.action] || "gray", `action,=,${doc.action}`];
	},
};
