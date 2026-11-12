// "Show on map" on the Item form (v1.815.0): opens the Warehouse Map page
// (/desk/warehouse-map) with this item lit up wherever it is on hand or assigned. A Desk route,
// so Back returns to the Item.
//
// Targets: the "Item" doctype form. Loaded via: hooks.py `doctype_js["Item"]`.
//
// Shown for a saved stock item, to the roles that may open the page
// (stock_scan_rules.SCAN_ROLES); the page and its endpoints check again on the server. The
// Warehouse form's own button is in warehouse_stock_scan.js.

(() => {
	const ROLES = ["Stock User", "Stock Manager", "Inventory Clerk", "System Manager"];

	frappe.ui.form.on("Item", {
		refresh(frm) {
			if (frm.is_new() || !frm.doc.is_stock_item) return;
			if (!ROLES.some((role) => frappe.user.has_role(role))) return;
			frm.add_custom_button(__("Show on map"), () => frappe.set_route("warehouse-map", { item: frm.doc.name }));
		},
	});
})();
