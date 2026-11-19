// Sales Order: Make Work Orders for configured products (v1.828.0, product_fulfillment).
//
// Submitting a product order already plans its configured lines (build.on_sales_order_submit): one
// Work Order per unit to build, or finished units from the shelf. This button runs the same plan
// again, for when that could not finish (no BOM yet, a warehouse missing) or the setting is off.
// Planning again never builds twice. Shown on a submitted, open order with a configured part
// number on it (PDT-nnnn followed by its options); the server decides what is configured.

frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || ["Closed", "Completed", "On Hold"].includes(frm.doc.status)) return;
		// Inside the handler, not at the top of the file: a form script can be evaluated twice in one
		// tab (meta reloaded after a deploy), and a second top-level const would throw.
		const configured = /^PDT-\d{4}-/i;
		if (!(frm.doc.items || []).some((row) => configured.test(row.item_code || ""))) return;
		if (!frappe.model.can_create("Work Order")) return;
		frm.add_custom_button(
			__("Product Work Orders"),
			() =>
				frappe
					.call({
						method: "erpnext_enhancements.product_fulfillment.build.make_work_orders",
						args: { sales_order: frm.doc.name },
						freeze: true,
					})
					.then(() => frm.reload_doc()),
			__("Create")
		);
	},
});
