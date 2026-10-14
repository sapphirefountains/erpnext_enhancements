// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Stripe Payments dashboard: connection/config readiness, status counts and a
// recent-payments table. All data comes from the operator-gated RPC
// erpnext_enhancements.stripe_payments.core.api.get_dashboard_status. Its loading, empty, error
// and no-access panels, its toast and its server call come from sf_kit (v1.645.0), and so do its
// tiles and status chips (v1.649.0): every status shows its word, and its colour is a kit token.
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
		const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
		return { esc, call, state: { render }, chip: esc, tiles: { html: () => "" }, toast: (message) => frappe.show_alert({ message, indicator: "orange" }) };
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

	// What each payment status means; how it looks is the kit's (v1.649.0). Link Sent, Expired and
	// Refunded are neither good nor bad news, so they are neutral chips with their word.
	const STATUS_TONE = { Paid: "good", Processing: "warn", Failed: "danger" };
	const PAYMENT_STATUSES = ["Paid", "Processing", "Link Sent", "Failed", "Expired", "Refunded"];

	// A yes/no setting as a word in a status chip: the word is the meaning, the colour repeats it.
	function setting(label, on, words, off_tone) {
		return { label, value: on ? words[0] : words[1], tone: on ? "good" : off_tone };
	}

	// The connection and its settings as one tile, and a tile per payment status: sf_kit draws both,
	// escapes every value, and gives each status a chip that says its word. The recent payments
	// table's status column is a kit chip too.
	function render(sf, data) {
		const esc = sf.esc;
		const s = data.settings || {};
		const c = data.counts || {};
		const connected = s.status === "Connected";

		const connection = {
			key: "connection",
			title: __("Connection"),
			tone: connected ? "good" : "warn",
			status_label: s.status || __("Not Configured"),
			detail: s.status_message,
			metrics: [
				{ label: __("Environment"), value: s.environment || "—" },
				setting(__("Enabled"), s.enabled, [__("Yes"), __("No")], "warn"),
				setting(__("Secret key"), s.has_secret_key, [__("Set"), __("Missing")], "danger"),
				setting(__("Webhook secret"), s.has_webhook_secret, [__("Set"), __("Missing")], "danger"),
				{ label: __("Deposit account"), value: s.deposit_account || "—" },
				{ label: __("Card mode"), value: s.card_mode_of_payment || "—" },
				{ label: __("ACH mode"), value: s.ach_mode_of_payment || (s.enable_ach ? "—" : __("disabled")) },
				{ label: __("Last webhook"), value: s.last_webhook_at ? frappe.datetime.str_to_user(s.last_webhook_at) : "—" },
			],
		};

		const counts = PAYMENT_STATUSES.map((k) => ({
			key: k,
			tone: STATUS_TONE[k] || "neutral",
			status_label: __(k),
			value: c[k] || 0,
		}));

		const rows = (data.recent || [])
			.map(
				(r) => `<tr>
				<td><a href="/desk/stripe-payment/${encodeURIComponent(r.name)}">${esc(r.name)}</a></td>
				<td>${esc(r.customer)}</td>
				<td>${r.sales_invoice ? `<a href="/desk/sales-invoice/${encodeURIComponent(r.sales_invoice)}">${esc(r.sales_invoice)}</a>` : "—"}</td>
				<td class="text-right">${esc(format_currency(r.amount, r.currency))}</td>
				<td>${esc(r.payment_method_type || "—")}</td>
				<td>${sf.chip(r.status, STATUS_TONE[r.status] || "neutral")}</td>
				<td>${r.payment_entry ? `<a href="/desk/payment-entry/${encodeURIComponent(r.payment_entry)}">${esc(r.payment_entry)}</a>` : "—"}</td>
			</tr>`,
			)
			.join("");

		$body.html(`
			${sf.tiles.html([connection])}
			<h5 style="margin: var(--sf-space-20) 0 var(--sf-space-10);">${__("Payments")}</h5>
			${sf.tiles.html(counts)}
			<h5 style="margin: var(--sf-space-20) 0 var(--sf-space-10);">${__("Recent Payments")}</h5>
			<table class="table table-bordered">
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
						render(sf, message);
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
