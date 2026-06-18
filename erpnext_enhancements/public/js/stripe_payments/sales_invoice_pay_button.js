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

// When surcharging is on, let the payer/staff pick a method first so the fee is
// method-correct and disclosed; otherwise create a single all-methods session.
function start_payment(frm) {
	frappe.call({
		method: "erpnext_enhancements.stripe_payments.core.api.payment_config",
		callback(r) {
			const cfg = (r && r.message) || {};
			if (cfg.surcharge_enabled) choose_method(frm, cfg);
			else create_stripe_payment(frm, null);
		},
	});
}

function fee_text(cfg, method) {
	const pct = method === "card" ? cfg.card_surcharge_percent : cfg.ach_fee_percent;
	const flat = method === "card" ? cfg.card_surcharge_flat : cfg.ach_fee_flat;
	const parts = [];
	if (pct) parts.push(`${pct}%`);
	if (flat) parts.push(format_currency(flat, cfg.currency));
	return parts.length ? ` (+${parts.join(" + ")} fee)` : "";
}

function choose_method(frm, cfg) {
	const d = new frappe.ui.Dialog({
		title: __("Choose payment method"),
		fields: [{ fieldtype: "HTML", fieldname: "buttons" }],
	});
	const $w = d.fields_dict.buttons.$wrapper;
	if (cfg.enable_card) {
		$(`<button class="btn btn-primary btn-block" style="margin-bottom:8px;">${__("Card")}${fee_text(cfg, "card")}</button>`)
			.appendTo($w)
			.on("click", () => {
				d.hide();
				create_stripe_payment(frm, "card");
			});
	}
	if (cfg.enable_ach) {
		$(`<button class="btn btn-default btn-block">${__("Bank (ACH)")}${fee_text(cfg, "ach")}</button>`)
			.appendTo($w)
			.on("click", () => {
				d.hide();
				create_stripe_payment(frm, "ach");
			});
	}
	d.show();
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
