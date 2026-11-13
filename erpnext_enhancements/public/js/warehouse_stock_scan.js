/**
 * Warehouse — the doors to the QR labels and the Stock Scan page.
 *
 * Targets: the "Warehouse" doctype form.
 * Loaded via: hooks.py `doctype_js["Warehouse"]`.
 *
 * A location that can hold stock (not a group) gets "QR Label", which opens the print page
 * with just this warehouse, and "Open Stock Scan", which opens the page a technician lands
 * on when they scan that label — the quickest way to check a label before printing a sheet.
 * A group gets "Print QR Labels", every location beneath it.
 *
 * window.open rather than frappe.set_route: both are website routes (www/), not Desk
 * pages, and they print or run full-screen on their own.
 *
 * "Show on Map" (v1.818.0), on a location and on a group alike, opens the Warehouse Map page
 * on the box that shows this warehouse. That one is a Desk page, so it is a frappe.set_route
 * and Back returns to this form. Shown to the roles that may open the page
 * (stock_scan_rules.SCAN_ROLES) once a map exists (frappe.boot.ee_warehouse_map); the page is
 * gated on the server too.
 */
const WAREHOUSE_MAP_ROLES = ["Stock User", "Stock Manager", "Inventory Clerk", "System Manager"];

frappe.ui.form.on("Warehouse", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.disabled) return;
		const group = __("Stock Scan");
		const name = encodeURIComponent(frm.doc.name);
		if (frappe.boot.ee_warehouse_map && WAREHOUSE_MAP_ROLES.some((role) => frappe.user.has_role(role))) {
			frm.add_custom_button(__("Show on Map"), () => frappe.set_route("warehouse-map", { w: frm.doc.name }), group);
		}
		if (frm.doc.is_group) {
			frm.add_custom_button(
				__("Print QR Labels"),
				() => window.open(`/warehouse-labels?under=${name}`, "_blank", "noopener"),
				group
			);
			return;
		}
		if (frm.doc.warehouse_type === "Transit") return;
		frm.add_custom_button(
			__("QR Label"),
			() => window.open(`/warehouse-labels?w=${name}`, "_blank", "noopener"),
			group
		);
		frm.add_custom_button(
			__("Open Stock Scan"),
			() => window.open(`/stock-scan?w=${name}`, "_blank", "noopener"),
			group
		);
	},
});
