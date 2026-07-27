// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// "Pay with Stripe" button on submitted, outstanding Sales Invoices. Creates a
// hosted Stripe Checkout link and offers to open / copy / email / text it to the
// customer. Once paid (custom_stripe_payment_status === "Paid") it shows a green
// indicator instead. Backend: erpnext_enhancements.stripe_payments.core.api.
function stripe_pay_button(frm) {
	if (frm.doc.docstatus !== 1) return;

	if (frm.doc.custom_stripe_payment_status === "Paid") {
		frm.dashboard.add_indicator(__("Paid via Stripe"), "green");
		return;
	}
	if (!(frm.doc.outstanding_amount > 0)) return;

	frm.add_custom_button(__("Pay with Stripe"), () => start_payment(frm), __("Stripe")).addClass("btn-primary");
}

// Always a single all-methods session. The method-first dialog that used to live
// here existed only to price a per-method fee before payment; hosted Checkout can't
// tell a debit card from a credit one until after the session is priced, so it never
// surcharges and there is no longer a fee to disclose or a choice to force.
function start_payment(frm) {
	create_stripe_payment(frm, null);
}

function create_stripe_payment(frm, method) {
	frappe.call({
		method: "erpnext_enhancements.stripe_payments.core.api.create_invoice_payment",
		args: { sales_invoice: frm.doc.name, method },
		freeze: true,
		freeze_message: __("Creating Stripe checkout…"),
		callback(r) {
			if (r.exc || !r.message) return;
			show_link_dialog(frm, r.message);
			frm.reload_doc();
		},
	});
}

function show_link_dialog(frm, res) {
	const url = res.checkout_url;
	const sp = res.stripe_payment;
	const d = new frappe.ui.Dialog({
		title: __("Stripe Payment Link"),
		fields: [{ fieldtype: "HTML", fieldname: "info" }],
		primary_action_label: __("Open Link"),
		primary_action() {
			window.open(url, "_blank");
		},
	});
	d.fields_dict.info.$wrapper.html(`
		<p>${__("Send this secure payment link to the customer:")}</p>
		<div style="word-break:break-all;padding:8px;background:var(--control-bg);border-radius:6px;">
			${frappe.utils.escape_html(url)}
		</div>
	`);
	d.set_secondary_action_label(__("Copy"));
	d.set_secondary_action(() => {
		frappe.utils.copy_to_clipboard(url);
	});
	d.show();

	$(`<button class="btn btn-sm btn-default ml-2">${__("Email link")}</button>`)
		.appendTo(d.footer)
		.on("click", () => send_link(sp, "email"));
	$(`<button class="btn btn-sm btn-default ml-2">${__("Text link")}</button>`)
		.appendTo(d.footer)
		.on("click", () => send_link(sp, "sms"));
}

function send_link(stripe_payment, via) {
	frappe.call({
		method: "erpnext_enhancements.stripe_payments.core.api.send_payment_link",
		args: { stripe_payment, via },
		freeze: true,
		freeze_message: __("Sending…"),
		callback(r) {
			if (r.exc || !r.message) return;
			frappe.show_alert({
				message: __("Payment link sent via {0} to {1}", [r.message.via, r.message.to]),
				indicator: "green",
			});
		},
	});
}

frappe.ui.form.on("Sales Invoice", { refresh: stripe_pay_button });
