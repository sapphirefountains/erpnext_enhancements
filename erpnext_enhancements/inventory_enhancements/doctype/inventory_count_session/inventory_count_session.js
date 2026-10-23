// Inventory Count Session form (v1.736.0): a door to the Count Desk, where a count is typed,
// reviewed and posted. The form shows the raw lines; the Count Desk shows the sheet.

frappe.ui.form.on("Inventory Count Session", {
	refresh(frm) {
		if (frm.is_new()) return;
		const label = frm.doc.status === "Open" ? __("Open in the Count Desk") : __("View in the Count Desk");
		frm.add_custom_button(label, () => frappe.set_route("count-desk", frm.doc.name));
	},
});
