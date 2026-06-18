// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Client script for the Stripe Payments Settings form. Adds:
//  - "Test Connection": verifies the API key against Stripe and reports config gaps.
//  - "Dashboard": routes to the Stripe Payments dashboard page.
//  - "Copy Webhook URL": the endpoint to register in the Stripe Dashboard.
// All actions delegate to whitelisted methods under
// erpnext_enhancements.stripe_payments.core.api.
frappe.ui.form.on("Stripe Payments Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			frappe.call({
				method: "erpnext_enhancements.stripe_payments.core.api.test_connection",
				freeze: true,
				freeze_message: __("Contacting Stripe…"),
				callback(r) {
					if (r.exc || !r.message) return;
					const m = r.message;
					const gaps = [];
					if (!m.deposit_account_set) gaps.push(__("Deposit / Clearing Account"));
					if (!m.card_mode_set) gaps.push(__("Card Mode of Payment"));
					if (!m.ach_mode_set) gaps.push(__("ACH Mode of Payment"));
					let msg = __("Connected to Stripe account {0} ({1}).", [m.account_id, m.environment]);
					if (gaps.length) {
						msg += "<br><b>" + __("Still to configure:") + "</b> " + gaps.join(", ");
					}
					frappe.msgprint({ title: __("Stripe"), message: msg, indicator: "green" });
					frm.reload_doc();
				},
			});
		}).addClass("btn-primary");

		frm.add_custom_button(__("Dashboard"), () => frappe.set_route("stripe-payments-dashboard"));

		if (frm.doc.webhook_url) {
			frm.add_custom_button(__("Copy Webhook URL"), () => {
				frappe.utils.copy_to_clipboard(frm.doc.webhook_url);
			});
		}
	},
});
