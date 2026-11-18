// Delivery Note: the QC gate's verdict and getting the order to the customer (product_fulfillment).
//
// v1.826.0 -- a draft that ships configured product units asks the server what the gate would say
// (qc_gate.delivery_gate_status, the same check before_submit makes) and shows it as the headline:
// green when every unit has a passed test signed by someone other than its builder, otherwise the
// list of what is missing.
//
// v1.827.0 (TASK-2026-03231) -- the fulfillment method's buttons:
//   * Pickup at shop, draft: Email Ready for Pickup (refused by the server until every unit passed).
//   * Carrier shipping: Track Package (the tracking link, a new tab), and once submitted, Carrier
//     Status (FedEx and UPS, through Carrier Tracking) and Resend Shipped Email.
// Nothing here writes the note; the server does, and the form reloads.

frappe.ui.form.on("Delivery Note", {
	refresh(frm) {
		if (frm.is_new()) return;
		const method = frm.doc.custom_fulfillment_method;
		const api = "erpnext_enhancements.product_fulfillment.";
		const group = __("Fulfillment");

		if (frm.doc.docstatus === 0 && !frm.doc.is_return) {
			frappe.call({ method: api + "qc_gate.delivery_gate_status", args: { delivery_note: frm.doc.name } }).then((r) => {
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
		}

		if (method === "Pickup at shop" && frm.doc.docstatus === 0) {
			frm.add_custom_button(
				frm.doc.custom_ready_email_sent_on ? __("Email Ready for Pickup Again") : __("Email Ready for Pickup"),
				() =>
					frappe
						.call({ method: api + "shipping.send_ready_for_pickup", args: { delivery_note: frm.doc.name }, freeze: true })
						.then((r) => {
							if (r && r.message && r.message.sent_to) {
								frappe.show_alert({ message: __("Sent to {0}", [r.message.sent_to]), indicator: "green" });
								frm.reload_doc();
							}
						}),
				group
			);
		}

		if (method === "Carrier shipping") {
			if (frm.doc.custom_tracking_url) {
				frm.add_custom_button(__("Track Package"), () => window.open(frm.doc.custom_tracking_url, "_blank", "noopener"), group);
			}
			if (frm.doc.docstatus === 1) {
				frm.add_custom_button(
					__("Carrier Status"),
					() =>
						frappe.call({ method: api + "shipping.carrier_status", args: { delivery_note: frm.doc.name } }).then((r) => {
							const s = (r && r.message) || {};
							const esc = frappe.utils.escape_html;
							let text;
							if (s.outcome === "off") text = __("Package status checks are switched off (Carrier Tracking Settings).");
							else if (s.outcome === "unsupported") text = __("Only FedEx and UPS can be asked; use Track Package.");
							else if (s.outcome === "no_number") text = __("There is no tracking number on this note.");
							else if (s.status || s.description) {
								text = [s.description || s.status, s.estimated_delivery ? __("Expected {0}", [s.estimated_delivery]) : "", s.last_location]
									.filter(Boolean)
									.map(esc)
									.join("<br>");
							} else text = esc(s.error || __("The carrier gave no answer ({0}).", [s.outcome || "?"]));
							frappe.msgprint({ title: __("Carrier Status"), message: text, indicator: "blue" });
						}),
					group
				);
				frm.add_custom_button(
					__("Resend Shipped Email"),
					() =>
						frappe
							.call({ method: api + "shipping.resend_shipped", args: { delivery_note: frm.doc.name }, freeze: true })
							.then((r) => {
								if (r && r.message && r.message.sent_to) {
									frappe.show_alert({ message: __("Sent to {0}", [r.message.sent_to]), indicator: "green" });
									frm.reload_doc();
								}
							}),
					group
				);
			}
		}
	},
});
