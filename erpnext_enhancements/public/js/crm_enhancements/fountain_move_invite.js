/*
 * Fountain Move Invite form: resend, revoke, copy the link.
 *
 * The token is attribution only — it grants no access — so "Copy Link" is not a
 * privileged action in any meaningful sense. It is here because staff routinely
 * need to paste the same link into a text or a chat.
 */

frappe.ui.form.on("Fountain Move Invite", {
	refresh(frm) {
		if (frm.is_new()) return;

		show_state(frm);

		if (["Sent", "Opened", "Expired"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Resend"), () => {
				frappe.confirm(
					__("Email this link again? The same link is reused and its expiry is extended."),
					() => {
						frappe.call({
							method:
								"erpnext_enhancements.crm_enhancements.fountain_move.invites.resend_intake_link",
							args: { invite_name: frm.doc.name },
							freeze: true,
							freeze_message: __("Resending…"),
							callback(response) {
								if (response.message) {
									frappe.show_alert(
										{
											message: __("Resent to {0}", [response.message.to]),
											indicator: "green",
										},
										7
									);
								}
								frm.reload_doc();
							},
						});
					}
				);
			});
		}

		if (frm.doc.status !== "Submitted" && frm.doc.status !== "Revoked") {
			frm.add_custom_button(
				__("Revoke"),
				() => {
					frappe.confirm(
						__(
							"Stop attributing submissions to this invite? The customer can still use the public form — they just won't be linked to this invite."
						),
						() => {
							frappe.call({
								method:
									"erpnext_enhancements.crm_enhancements.fountain_move.invites.revoke_intake_link",
								args: { invite_name: frm.doc.name },
								freeze: true,
								callback: () => frm.reload_doc(),
							});
						}
					);
				},
				__("Actions")
			);
		}

		if (frm.doc.fountain_move_request) {
			frm.add_custom_button(__("Open Request"), () =>
				frappe.set_route("Form", "Fountain Move Request", frm.doc.fountain_move_request)
			);
		}
	},
});

function show_state(frm) {
	const states = {
		Sent: [__("Sent — not opened yet."), "blue"],
		Opened: [__("The customer opened the form but hasn't submitted it yet."), "orange"],
		Submitted: [__("Submitted. See the linked request."), "green"],
		Expired: [
			__("Expired. The link still opens the form; it just no longer attributes the submission."),
			"grey",
		],
		Revoked: [__("Revoked."), "grey"],
	};
	const state = states[frm.doc.status];
	if (state) {
		frm.dashboard.clear_headline();
		frm.dashboard.set_headline(state[0], state[1]);
	}
}
