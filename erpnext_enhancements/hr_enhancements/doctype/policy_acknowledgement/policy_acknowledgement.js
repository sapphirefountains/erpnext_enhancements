// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// Signing, and declining. Both are one button, and declining is offered as
// plainly as signing — a register that only offers yes is a register that gets a
// yes, and a refusal with a reason on it is far more use than a row that never
// got filled in.

frappe.ui.form.on("Policy Acknowledgement", {
	refresh(frm) {
		frm.trigger("ee_state");
		frm.trigger("ee_actions");
	},

	ee_state(frm) {
		if (frm.is_new()) return;
		const tone = { Requested: "orange", Signed: "green", Declined: "red" };
		frm.page.set_indicator(__(frm.doc.status), tone[frm.doc.status] || "gray");
		if (frm.doc.status === "Signed" && frm.doc.signed_on) {
			frm.dashboard.add_comment(
				__("Signed by {0} on {1}.", [
					frm.doc.signed_name,
					frappe.datetime.str_to_user(frm.doc.signed_on),
				]),
				"green",
				true
			);
		}
	},

	ee_actions(frm) {
		if (frm.is_new() || frm.doc.status !== "Requested") return;
		// Only the person it belongs to. The server refuses anybody else, and a
		// button that only fails is worse than no button.
		if (frm.doc.user !== frappe.session.user) return;
		frm.add_custom_button(__("I have read it and I agree"), () => frm.trigger("ee_sign")).addClass(
			"btn-primary"
		);
		frm.add_custom_button(__("I do not agree"), () => frm.trigger("ee_decline"));
	},

	ee_sign(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Sign"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "what",
					options: `<p>${__("You are confirming that you have read <b>{0}</b>{1} and agree to it.", [
						frappe.utils.escape_html(frm.doc.policy || ""),
						frm.doc.version_label ? ` (${frappe.utils.escape_html(frm.doc.version_label)})` : "",
					])}</p>`,
				},
				{
					fieldtype: "Data",
					fieldname: "typed_name",
					label: __("Type your full name"),
					reqd: 1,
					// Not prefilled, deliberately. A prefilled name is a name nobody
					// typed, and typing it is the act being recorded.
					description: __("Exactly as it appears on your employee record."),
				},
				{ fieldtype: "Signature", fieldname: "signature", label: __("Signature (optional)") },
			],
			primary_action_label: __("Sign"),
			primary_action: (values) => {
				dialog.hide();
				frm.call({
					method: "erpnext_enhancements.hr_enhancements.policies.sign",
					args: {
						acknowledgement: frm.doc.name,
						typed_name: values.typed_name,
						signature: values.signature,
					},
					freeze: true,
				}).then(() => {
					frappe.show_alert({ message: __("Signed."), indicator: "green" });
					frm.reload_doc();
				});
			},
		});
		dialog.show();
	},

	ee_decline(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("You do not agree"),
			fields: [
				{
					fieldtype: "Small Text",
					fieldname: "reason",
					label: __("Why"),
					reqd: 1,
					description: __("HR is told. This is a normal thing to do and it gets read."),
				},
			],
			primary_action_label: __("Record it"),
			primary_action: (values) => {
				dialog.hide();
				frm.call({
					method: "erpnext_enhancements.hr_enhancements.policies.decline",
					args: { acknowledgement: frm.doc.name, reason: values.reason },
					freeze: true,
				}).then(() => frm.reload_doc());
			},
		});
		dialog.show();
	},
});
