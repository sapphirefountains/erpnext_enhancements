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
 */
frappe.ui.form.on("Warehouse", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.disabled) return;
		const group = __("Stock Scan");
		const name = encodeURIComponent(frm.doc.name);
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
