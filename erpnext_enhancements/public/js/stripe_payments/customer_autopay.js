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
					// Charge an invoice, not an amount. With an invoice the server runs the rule
					// every payment path shares (one invoice is never paid twice: refused while a
					// payment for it is settling, an emailed link closed first) and charges the
					// invoice's outstanding balance. A bare amount is allocated to no invoice —
					// the invoice stays due, and autopay, dunning or the customer could pay it
					// again — so it is only for a charge that is genuinely not for an invoice.
					const charge = (args) =>
						frappe.call({
							method: "erpnext_enhancements.stripe_payments.core.api.charge_saved_method",
							args: Object.assign({ customer: frm.doc.name }, args),
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
					frappe.prompt(
						[
							{
								fieldname: "sales_invoice",
								label: __("Sales Invoice"),
								fieldtype: "Link",
								options: "Sales Invoice",
								description: __("Charges the invoice's outstanding balance. Leave empty only for a charge that is not for an invoice."),
								get_query: () => ({
									filters: { customer: frm.doc.name, docstatus: 1, outstanding_amount: [">", 0] },
								}),
							},
							{
								fieldname: "amount",
								label: __("Amount (no invoice)"),
								fieldtype: "Currency",
								depends_on: "eval:!doc.sales_invoice",
							},
						],
						(v) => {
							if (v.sales_invoice) {
								charge({ sales_invoice: v.sales_invoice });
								return;
							}
							if (!(v.amount > 0)) {
								frappe.msgprint(__("Choose the invoice to charge, or enter an amount for a charge that is not for an invoice."));
								return;
							}
							frappe.confirm(
								__("This charge is not applied to any invoice, so every invoice stays due. Charge {0} anyway?", [
									format_currency(v.amount),
								]),
								() => charge({ amount: v.amount }),
							);
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
