// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Inventory Scanner Settings form client script.
 *
 * Auto-loaded by Frappe as the doctype's form script (it lives alongside the doctype; no
 * doctype_js hooks.py entry, and test_hooks_integrity refuses one).
 *
 * The Stock Scan page's two accounts are the other side of a Stock Entry row, which ERPNext
 * refuses when it is a group or a Stock-type account ("the Difference Account must not be a
 * Stock type account"), so the pickers offer only accounts a row can post to. A settings
 * page that lets you choose an account every save will then reject is a trap you find on
 * the warehouse floor, one technician at a time.
 *
 * Also links to the page itself, the label print page, the review queue, and (v1.535.0) the
 * Items technicians created while recording a store run.
 */
frappe.ui.form.on("Inventory Scanner Settings", {
	setup(frm) {
		const postable = () => ({
			filters: { is_group: 0, disabled: 0, account_type: ["!=", "Stock"] },
		});
		frm.set_query("take_expense_account", postable);
		frm.set_query("add_offset_account", postable);
		frm.set_query("scan_cost_center", () => ({ filters: { is_group: 0, disabled: 0 } }));
	},

	refresh(frm) {
		const group = __("Stock Scan");
		frm.add_custom_button(__("Open Stock Scan"), () => window.open("/stock-scan", "_blank", "noopener"), group);
		frm.add_custom_button(__("Print QR Labels"), () => window.open("/warehouse-labels", "_blank", "noopener"), group);
		// Every save that added stock without a purchase order: Add Without PO and, since
		// v1.535.0, every store-run line. Posted only -- an undone save needs no review.
		frm.add_custom_button(
			__("Stock Scan Saves to Review"),
			() =>
				frappe.set_route("List", "Stock Scan Log", {
					needs_review: 1,
					reviewed: 0,
					status: "Posted",
				}),
			group
		);
		// POL-0602 §4.8: the Purchasing Agent reviews each Item a technician created on a store
		// run, weekly. No status filter: an Item created with a line that was then undone still
		// exists, and still needs looking at.
		frm.add_custom_button(
			__("New Items From Store Runs"),
			() =>
				frappe.set_route("List", "Stock Scan Log", {
					created_item: 1,
					reviewed: 0,
				}),
			group
		);
	},
});
