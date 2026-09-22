// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Connect / Test / Disconnect per ad platform, plus Sync now. Every call is a POST to
// erpnext_enhancements.marketing.api.*; the only GET in the flow is the platform sending
// the browser back to oauth_callback, which redirects here.

const MARKETING_PLATFORMS = [
	{ name: "Google Ads", prefix: "google_ads" },
	{ name: "Meta Ads", prefix: "meta" },
	{ name: "LinkedIn Ads", prefix: "linkedin" },
];

const MARKETING_API = "erpnext_enhancements.marketing.api";

frappe.ui.form.on("Marketing Credentials", {
	refresh(frm) {
		const redirect = `${window.location.origin}/api/method/${MARKETING_API}.oauth_callback`;
		frm.set_intro(
			__("Register this redirect URI, exactly, in each platform's app: {0}", [`<code>${frappe.utils.escape_html(redirect)}</code>`]),
			"blue"
		);

		MARKETING_PLATFORMS.forEach((platform) => {
			const status = frm.doc[`${platform.prefix}_connection_status`];
			const group = __(platform.name);

			frm.add_custom_button(
				status === "Connected" ? __("Reconnect") : __("Connect"),
				() => {
					if (frm.is_dirty()) {
						frappe.msgprint(__("Save first: the Connect flow reads the saved client ID and secret."));
						return;
					}
					frappe.call({
						method: `${MARKETING_API}.start_oauth`,
						type: "POST",
						args: { platform: platform.name },
						callback: (r) => {
							if (r.message && r.message.authorization_url) {
								window.location.href = r.message.authorization_url;
							}
						},
					});
				},
				group
			);

			if (status === "Connected" || status === "Auth Failed") {
				frm.add_custom_button(
					__("Test"),
					() =>
						frappe.call({
							method: `${MARKETING_API}.test_connection`,
							type: "POST",
							args: { platform: platform.name },
							freeze: true,
							callback: (r) => {
								const m = r.message || {};
								frappe.msgprint({
									title: __(platform.name),
									indicator: m.ok ? "green" : "red",
									message: m.ok
										? __("Readable accounts:") + "<br>" + (m.accounts || []).map(frappe.utils.escape_html).join("<br>")
										: frappe.utils.escape_html(m.message || __("Failed")),
								});
								frm.reload_doc();
							},
						}),
					group
				);
				frm.add_custom_button(
					__("Disconnect"),
					() =>
						frappe.confirm(__("Forget the stored tokens for {0}?", [__(platform.name)]), () =>
							frappe.call({
								method: `${MARKETING_API}.disconnect`,
								type: "POST",
								args: { platform: platform.name },
								callback: () => frm.reload_doc(),
							})
						),
					group
				);
			}
		});

		frm.add_custom_button(__("Sync now"), () =>
			frappe.call({
				method: `${MARKETING_API}.sync_now`,
				type: "POST",
				callback: (r) => {
					const queued = (r.message && r.message.queued) || [];
					frappe.show_alert({ message: __("Queued: {0}", [queued.join(", ")]), indicator: "green" });
				},
			})
		);

		const metaExpiry = frm.doc.meta_access_token_expires_on;
		if (metaExpiry && frappe.datetime.get_day_diff(metaExpiry, frappe.datetime.now_date()) <= 10) {
			frm.dashboard.set_headline_alert(
				__("The Meta token expires on {0}. Click Reconnect under Meta Ads before then.", [frappe.datetime.str_to_user(metaExpiry)]),
				"orange"
			);
		}
	},
});
