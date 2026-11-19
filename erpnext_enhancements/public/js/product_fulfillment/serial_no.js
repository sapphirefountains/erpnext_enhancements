// Serial No: a product unit's label and test (v1.828.0, product_fulfillment, TASK-2026-03230).
//
// For a unit with a recorded builder (custom_built_by, stamped when its Work Order was finished):
// Print Label opens the Product Unit Label (serial number and part number) in a new tab, and Start
// Test makes a draft Pre-Shipment Functional Test for this serial as you and opens it. The server
// refuses Start Test to the person who built the unit (product_fulfillment.qc_gate.start_test).

frappe.ui.form.on("Serial No", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.custom_built_by) return;
		frm.add_custom_button(__("Print Label"), () => {
			const url =
				`/printview?doctype=${encodeURIComponent("Serial No")}&name=${encodeURIComponent(frm.doc.name)}` +
				`&format=${encodeURIComponent("Product Unit Label")}&no_letterhead=1&trigger_print=1`;
			window.open(url, "_blank", "noopener");
		});
		const me = String(frappe.session.user || "").toLowerCase();
		if (String(frm.doc.custom_built_by).toLowerCase() === me) {
			frm.dashboard.set_headline_alert(__("You built this unit, so someone else tests it."), "blue");
			return;
		}
		if (frappe.model.can_create("Quality Inspection")) {
			frm.add_custom_button(__("Start Test"), () =>
				frappe
					.call({
						method: "erpnext_enhancements.product_fulfillment.qc_gate.start_test",
						args: { serial_no: frm.doc.name },
					})
					.then((r) => r && r.message && frappe.set_route("Form", "Quality Inspection", r.message.name))
			);
		}
	},
});
