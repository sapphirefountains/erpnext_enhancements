// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Stripe Payment form: open the checkout link while pending, and refund once paid.
// Backend: erpnext_enhancements.stripe_payments.core.api.
frappe.ui.form.on("Stripe Payment", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.checkout_url && ["Link Sent", "Processing"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Open Checkout Link"), () => window.open(frm.doc.checkout_url, "_blank"));
		}

		if (["Paid", "Processing"].includes(frm.doc.status) && frm.doc.stripe_payment_intent) {
			frm.add_custom_button(
				__("Refund"),
				() => {
					frappe.prompt(
						[{ fieldname: "amount", label: __("Amount (blank = full)"), fieldtype: "Currency" }],
						(v) => {
							frappe.confirm(__("Refund this payment in Stripe?"), () => {
								frappe.call({
									method: "erpnext_enhancements.stripe_payments.core.api.refund_payment",
									args: { stripe_payment: frm.doc.name, amount: v.amount || null },
									freeze: true,
									freeze_message: __("Refunding…"),
									callback(r) {
										if (r.exc || !r.message) return;
										frappe.show_alert({
											message: __("Refund {0}", [r.message.status]),
											indicator: "orange",
										});
										frm.reload_doc();
									},
								});
							});
						},
						__("Refund"),
						__("Refund"),
					);
				},
				__("Stripe"),
			).addClass("btn-danger");
		}
	},
});
