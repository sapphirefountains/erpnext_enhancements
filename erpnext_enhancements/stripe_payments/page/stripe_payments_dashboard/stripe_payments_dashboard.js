// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Stripe Payments dashboard: connection/config readiness, status counts and a
// recent-payments table. All data comes from the operator-gated RPC
// erpnext_enhancements.stripe_payments.core.api.get_dashboard_status. Its loading, empty, error
// and no-access panels, its toast and its server call come from sf_kit (v1.645.0).
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

	// sf_kit from the shared loader (public/js/sf_kit_loader.js, in the global bundle). It never
	// rejects: a kit that never arrived resolves to a plain stand-in (frappe.call and a line of
	// plain text), so the dashboard carries on. A Desk opened before the deploy that added the
	// loader has no sf_kit_ready yet, and asks frappe.require for the kit itself.
	function kit() {
		if (typeof window.sf_kit_ready === "function") return window.sf_kit_ready();
		return new Promise((resolve) =>
			frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css"], () =>
				resolve(window.sf_kit && window.sf_kit.state ? window.sf_kit : bareKit())
			),
		);
	}

	// Neither the loader nor the kit: a Desk opened before this deploy whose old kit file is gone too.
	// A line of text and a button, so the page still says something instead of throwing.
	function bareKit() {
		const failed = (x) => ({ status: (x && x.status) || 0, message: "", exc_type: "", session_expired: (x && x.status) === 401 });
		const render = (el, kind, o) => {
			const $el = $(el).empty().append($('<div class="text-muted"></div>').text((o && o.title) || ""));
			if (o && o.on_action) $('<button class="btn btn-default btn-sm"></button>').text(o.action_label || __("Try again")).on("click", o.on_action).appendTo($el);
		};
		const call = (method, args) => Promise.resolve(frappe.call({ method, args: args || {} })).then((r) => r && r.message, (x) => Promise.reject(failed(x)));
		return { esc: (v) => frappe.utils.escape_html(v == null ? "" : String(v)), call, state: { render }, toast: (message) => frappe.show_alert({ message, indicator: "orange" }) };
	}

	// A dashboard that never loaded: no access on a 403, a way back in when the session has
	// lapsed (the quiet path puts up no sign-in dialog of frappe's), else the error with Try again.
	function failed(sf, err) {
		err = err || {};
		if (err.session_expired) {
			sf.state.render($body, "error", {
				title: __("You have been signed out."),
				body: __("Reload the page to sign in again."),
				action_label: __("Reload"),
				on_action: () => window.location.reload(),
			});
		} else if (err.status === 403) {
			sf.state.render($body, "no-access", {
				title: __("You do not have access to the Stripe Payments dashboard."),
				body: err.message,
			});
		} else {
			sf.state.render($body, "error", {
				title: __("Could not load the Stripe Payments dashboard."),
				body: err.message,
				on_action: () => load(),
			});
		}
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
				<td><a href="/desk/stripe-payment/${encodeURIComponent(r.name)}">${esc(r.name)}</a></td>
				<td>${esc(r.customer)}</td>
				<td>${r.sales_invoice ? `<a href="/desk/sales-invoice/${encodeURIComponent(r.sales_invoice)}">${esc(r.sales_invoice)}</a>` : "—"}</td>
				<td class="text-right">${format_currency(r.amount, r.currency)}</td>
				<td>${esc(r.payment_method_type || "—")}</td>
				<td><span class="indicator-pill ${statusColor(r.status)}">${esc(r.status)}</span></td>
				<td>${r.payment_entry ? `<a href="/desk/payment-entry/${encodeURIComponent(r.payment_entry)}">${esc(r.payment_entry)}</a>` : "—"}</td>
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

	// Quiet (v1.645.0): the dashboard says what went wrong in its own panel. A refresh that fails
	// while the dashboard is showing (Refresh, or after Test Connection) keeps it and says so in a
	// toast; only a dashboard that never loaded is replaced by the panel.
	let loaded = false;
	function load() {
		kit().then((sf) => {
			if (!loaded) sf.state.render($body, "loading", { title: __("Loading the Stripe Payments dashboard…") });
			sf.call("erpnext_enhancements.stripe_payments.core.api.get_dashboard_status", {}, { quiet: true }).then(
				(message) => {
					if (message) {
						loaded = true;
						render(message);
					} else if (!loaded) {
						sf.state.render($body, "empty", {
							title: __("No dashboard data came back."),
							body: __("Check Stripe Payments Settings, then Refresh."),
						});
					}
				},
				(err) => {
					if (!loaded) return failed(sf, err);
					sf.toast(
						err && err.session_expired
							? __("You have been signed out. Reload the page to sign in again.")
							: __("Could not refresh the dashboard. Showing what was last loaded."),
						{ tone: "warning" },
					);
				},
			);
		});
	}

	load();
};
