// Quality Inspection: a product unit's Pre-Shipment Functional Test (v1.828.0, product_fulfillment).
//
// A unit's test shows who built the unit (custom_built_by, fetched from its serial number) and says
// plainly when the person set as Inspected By is that builder, which the server refuses to save
// (qc_gate.quality_inspection_validate). A submitted test gets Print Certificate: the Pre-Shipment
// Test Certificate that goes in the box.

frappe.ui.form.on("Quality Inspection", {
	refresh(frm) {
		if (!frm.doc.item_serial_no || !frm.doc.custom_built_by) return;
		const same =
			String(frm.doc.custom_built_by).toLowerCase() === String(frm.doc.inspected_by || "").toLowerCase();
		if (same) {
			frm.dashboard.set_headline_alert(
				__("{0} built this unit, so someone else must test it.", [
					frappe.utils.escape_html(frappe.user.full_name(frm.doc.custom_built_by)),
				]),
				"red"
			);
		} else if (frm.doc.docstatus === 0) {
			frm.dashboard.set_headline_alert(
				__("Built by {0}. Type Pass, or N/A where the configuration has none, in each reading; submitting signs the test.", [
					frappe.utils.escape_html(frappe.user.full_name(frm.doc.custom_built_by)),
				]),
				"blue"
			);
		}
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__("Print Certificate"), () => {
				const url =
					`/printview?doctype=${encodeURIComponent("Quality Inspection")}&name=${encodeURIComponent(frm.doc.name)}` +
					`&format=${encodeURIComponent("Pre-Shipment Test Certificate")}&trigger_print=1`;
				window.open(url, "_blank", "noopener");
			});
		}
	},
	inspected_by(frm) {
		frm.trigger("refresh");
	},
});
