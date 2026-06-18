// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Stripe Payments dashboard: connection/config readiness, status counts and a
// recent-payments table. All data comes from the operator-gated RPC
// erpnext_enhancements.stripe_payments.core.api.get_dashboard_status.
frappe.pages["stripe-payments-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Stripe Payments"),
		single_column: true,
	});

	const $body = $('<div class="stripe-dashboard" style="padding:12px 0;"></div>').appendTo(page.body);

	page.set_primary_action(__("Refresh"), () => load(), "refresh");
	page.add_menu_item(__("Settings"), () =>
		frappe.set_route("Form", "Stripe Payments Settings", "Stripe Payments Settings"),
	);
	page.add_menu_item(__("All Stripe Payments"), () => frappe.set_route("List", "Stripe Payment"));
	page.add_menu_item(__("Test Connection"), () => {
		frappe.call({
			method: "erpnext_enhancements.stripe_payments.core.api.test_connection",
			freeze: true,
			callback() {
				load();
			},
		});
	});

	function esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}

	function statusColor(status) {
		return (
			{ Paid: "green", Processing: "orange", "Link Sent": "blue", Failed: "red", Expired: "gray", Refunded: "purple" }[
				status
			] || "gray"
		);
	}

	function render(data) {
		const s = data.settings || {};
		const c = data.counts || {};
		const connected = s.status === "Connected";

		const config = [
			[__("Environment"), s.environment],
			[__("Enabled"), s.enabled ? __("Yes") : __("No")],
			[__("Secret key"), s.has_secret_key ? "✓" : "✗"],
			[__("Webhook secret"), s.has_webhook_secret ? "✓" : "✗"],
			[__("Deposit account"), s.deposit_account || "—"],
			[__("Card mode"), s.card_mode_of_payment || "—"],
			[__("ACH mode"), s.ach_mode_of_payment || (s.enable_ach ? "—" : __("disabled"))],
			[__("Last webhook"), s.last_webhook_at ? frappe.datetime.str_to_user(s.last_webhook_at) : "—"],
		]
			.map(
				([k, v]) =>
					`<div class="col-sm-3" style="margin-bottom:8px;"><div class="text-muted small">${k}</div><div>${esc(v)}</div></div>`,
			)
			.join("");

		const tiles = ["Paid", "Processing", "Link Sent", "Failed", "Expired", "Refunded"]
			.map(
				(k) =>
					`<div class="col-sm-2"><div class="card" style="padding:10px;text-align:center;">
						<div style="font-size:22px;font-weight:600;">${c[k] || 0}</div>
						<div class="indicator-pill ${statusColor(k)}">${__(k)}</div>
					</div></div>`,
			)
			.join("");

		const rows = (data.recent || [])
			.map(
				(r) => `<tr>
				<td><a href="/app/stripe-payment/${encodeURIComponent(r.name)}">${esc(r.name)}</a></td>
				<td>${esc(r.customer)}</td>
				<td>${r.sales_invoice ? `<a href="/app/sales-invoice/${encodeURIComponent(r.sales_invoice)}">${esc(r.sales_invoice)}</a>` : "—"}</td>
				<td class="text-right">${format_currency(r.amount, r.currency)}</td>
				<td>${esc(r.payment_method_type || "—")}</td>
				<td><span class="indicator-pill ${statusColor(r.status)}">${esc(r.status)}</span></td>
				<td>${r.payment_entry ? `<a href="/app/payment-entry/${encodeURIComponent(r.payment_entry)}">${esc(r.payment_entry)}</a>` : "—"}</td>
			</tr>`,
			)
			.join("");

		$body.html(`
			<div class="alert ${connected ? "alert-success" : "alert-warning"}" style="margin-bottom:16px;">
				<b>${esc(s.status || "Not Configured")}</b>${s.status_message ? " — " + esc(s.status_message) : ""}
			</div>
			<div class="row">${config}</div>
			<h5 style="margin-top:16px;">${__("Payments")}</h5>
			<div class="row">${tiles}</div>
			<h5 style="margin-top:16px;">${__("Recent Payments")}</h5>
			<table class="table table-bordered" style="background:var(--card-bg);">
				<thead><tr>
					<th>${__("Payment")}</th><th>${__("Customer")}</th><th>${__("Invoice")}</th>
					<th class="text-right">${__("Amount")}</th><th>${__("Method")}</th><th>${__("Status")}</th><th>${__("Payment Entry")}</th>
				</tr></thead>
				<tbody>${rows || `<tr><td colspan="7" class="text-muted text-center">${__("No payments yet.")}</td></tr>`}</tbody>
			</table>
		`);
	}

	function load() {
		frappe.call({
			method: "erpnext_enhancements.stripe_payments.core.api.get_dashboard_status",
			callback(r) {
				if (!r.exc && r.message) render(r.message);
			},
		});
	}

	load();
};
