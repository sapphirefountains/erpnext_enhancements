// Delivery Note: the QC gate's verdict, before Submit is pressed (v1.828.0, product_fulfillment).
//
// A draft that ships configured product units asks the server what the gate would say
// (qc_gate.delivery_gate_status, the same check before_submit makes) and shows it as the headline:
// green when every unit has a passed test signed by someone other than its builder, otherwise the
// list of what is missing. Asked once per refresh and on save; it never writes.

frappe.ui.form.on("Delivery Note", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.docstatus !== 0 || frm.doc.is_return) return;
		frappe
			.call({
				method: "erpnext_enhancements.product_fulfillment.qc_gate.delivery_gate_status",
				args: { delivery_note: frm.doc.name },
				type: "GET", // the endpoint takes GET only
			})
			.then((r) => {
				const answer = r && r.message;
				if (!answer || !answer.rows) return;
				if (!answer.problems.length) {
					frm.dashboard.set_headline_alert(__("Every product unit on this note has passed its test."), "green");
					return;
				}
				const items = answer.problems.map((p) => `<li>${frappe.utils.escape_html(p)}</li>`).join("");
				frm.dashboard.set_headline_alert(
					`${__("Not ready to ship:")}<ul style="margin:4px 0 0 18px; padding:0">${items}</ul>`,
					"orange"
				);
			});
	},
});
