// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Customer form: Stripe autopay enrollment (save a method) + manual off-session
// charge of the saved method. Backend: erpnext_enhancements.stripe_payments.core.api.
frappe.ui.form.on("Customer", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.custom_stripe_payment_method_label) {
			frm.dashboard.add_indicator(
				__("Autopay: {0}", [frm.doc.custom_stripe_payment_method_label]),
				frm.doc.custom_stripe_autopay_enabled ? "green" : "orange",
			);
		}

		frm.add_custom_button(
			__("Set up Autopay"),
			() => {
				frappe.call({
					method: "erpnext_enhancements.stripe_payments.core.api.enroll_autopay",
					args: { customer: frm.doc.name },
					freeze: true,
					freeze_message: __("Starting autopay setup…"),
					callback(r) {
						if (r.exc || !r.message || !r.message.checkout_url) return;
						window.open(r.message.checkout_url, "_blank");
						frappe.show_alert({
							message: __("Open/send the setup link to save a payment method."),
							indicator: "blue",
						});
					},
				});
			},
			__("Stripe"),
		);

		if (frm.doc.custom_stripe_default_payment_method) {
			frm.add_custom_button(
				__("Charge Saved Method"),
				() => {
					frappe.prompt(
						[{ fieldname: "amount", label: __("Amount"), fieldtype: "Currency", reqd: 1 }],
						(v) => {
							frappe.call({
								method: "erpnext_enhancements.stripe_payments.core.api.charge_saved_method",
								args: { customer: frm.doc.name, amount: v.amount },
								freeze: true,
								freeze_message: __("Charging saved method…"),
								callback(r) {
									if (r.exc || !r.message) return;
									frappe.show_alert({
										message: __("Charge {0} ({1})", [r.message.status, r.message.stripe_payment]),
										indicator: r.message.status === "Paid" ? "green" : "orange",
									});
								},
							});
						},
						__("Charge Saved Method"),
						__("Charge"),
					);
				},
				__("Stripe"),
			);

			frm.add_custom_button(
				__("Revoke Autopay"),
				() => {
					frappe.confirm(
						__("Cancel autopay and remove the saved payment method for this customer?"),
						() => {
							frappe.call({
								method: "erpnext_enhancements.stripe_payments.core.api.revoke_autopay",
								args: { customer: frm.doc.name },
								freeze: true,
								freeze_message: __("Revoking autopay…"),
								callback(r) {
									if (r.exc) return;
									frappe.show_alert({ message: __("Autopay revoked."), indicator: "orange" });
									frm.reload_doc();
								},
							});
						},
					);
				},
				__("Stripe"),
			);
		}
	},
});
