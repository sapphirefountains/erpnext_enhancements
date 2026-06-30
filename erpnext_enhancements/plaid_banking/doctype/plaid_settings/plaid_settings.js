/**
 * Client script for the Plaid Settings form.
 *
 * Drives the Plaid Link connect flow from the desk form (a real document, NOT a
 * shadow-DOM block — so window.Plaid is defined on the real window and Link mounts
 * its own top-level iframe). Buttons:
 *  - "Test Connection": item_get round-trip; lifts the auth pause on success.
 *  - "Connect Bank" / "Reconnect Bank": create_link_token -> open Plaid Link ->
 *    exchange_public_token (stores the encrypted access token).
 *  - "Disconnect Bank" (only once linked): revokes the Item at Plaid + clears tokens.
 *  - "Refresh Balances Now" (only once linked): pulls /accounts/balance/get now.
 *
 * All buttons delegate to whitelisted methods under
 * erpnext_enhancements.plaid_banking.core.api.
 */
const PLAID_API = "erpnext_enhancements.plaid_banking.core.api";
const PLAID_LINK_SRC = "https://cdn.plaid.com/link/v2/stable/link-initialize.js";

function ensurePlaidScript() {
	if (window.Plaid || document.getElementById("plaid-link-js")) {
		return;
	}
	const s = document.createElement("script");
	s.id = "plaid-link-js";
	s.src = PLAID_LINK_SRC;
	document.head.appendChild(s);
}

function openPlaidLink(frm, linkToken) {
	if (!window.Plaid) {
		frappe.msgprint(__("Plaid Link is still loading — try again in a moment."));
		return;
	}
	const handler = window.Plaid.create({
		token: linkToken,
		onSuccess(public_token) {
			frappe.call({
				method: `${PLAID_API}.exchange_public_token`,
				args: { public_token },
				freeze: true,
				freeze_message: __("Linking your bank…"),
				callback() {
					frappe.show_alert({ message: __("Bank connected."), indicator: "green" });
					frm.reload_doc();
				},
			});
		},
		onExit(err) {
			if (err) {
				frappe.msgprint(__("Plaid Link was cancelled or did not complete."));
			}
		},
	});
	handler.open();
}

function connectBank(frm) {
	frappe.call({
		method: `${PLAID_API}.create_link_token`,
		freeze: true,
		freeze_message: __("Contacting Plaid…"),
		callback(r) {
			const token = r.message && r.message.link_token;
			if (token) {
				openPlaidLink(frm, token);
			}
		},
	});
}

frappe.ui.form.on("Plaid Settings", {
	refresh(frm) {
		ensurePlaidScript();

		frm.add_custom_button(__("Test Connection"), () => {
			frappe.call({
				method: `${PLAID_API}.test_connection`,
				freeze: true,
				freeze_message: __("Testing the Plaid connection…"),
				callback(r) {
					const m = r.message || {};
					frappe.show_alert({
						message: m.message || (m.ok ? __("Connection OK.") : __("Connection failed.")),
						indicator: m.ok ? "green" : "red",
					});
					frm.reload_doc();
				},
			});
		});

		frm.add_custom_button(
			frm.doc.plaid_item_id ? __("Reconnect Bank") : __("Connect Bank"),
			() => connectBank(frm),
		);

		if (frm.doc.plaid_item_id) {
			frm.add_custom_button(__("Refresh Balances Now"), () => {
				frappe.call({
					method: `${PLAID_API}.refresh_now`,
					freeze: true,
					freeze_message: __("Refreshing balances…"),
					callback(r) {
						const m = r.message || {};
						frappe.show_alert({
							message: m.ok ? __("Balances refreshed.") : m.message || __("Could not refresh."),
							indicator: m.ok ? "green" : "orange",
						});
						frm.reload_doc();
					},
				});
			});

			frm.add_custom_button(__("Disconnect Bank"), () => {
				frappe.confirm(
					__(
						"Disconnect this bank from Plaid? This revokes the connection at Plaid and clears the stored token. You can reconnect at any time.",
					),
					() => {
						frappe.call({
							method: `${PLAID_API}.disconnect`,
							freeze: true,
							freeze_message: __("Disconnecting…"),
							callback() {
								frappe.show_alert({ message: __("Bank disconnected."), indicator: "orange" });
								frm.reload_doc();
							},
						});
					},
				);
			});
		}
	},
});
