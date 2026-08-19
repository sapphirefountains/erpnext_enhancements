// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// Giving a client's contact a login that reaches /training.
//
// `training/portal.py` has held the whole of this since v1.215.0 — creating the
// Website User, granting Training Learner durably (it knows a System User needs a
// Role Profile and a Website User does not), linking the Contact, and warning when
// the Contact has no Customer behind it — and **nothing called any of it**. The only
// way to put a client on the portal was to build the User by hand and remember the
// role, which is exactly the sequence the module exists to stop people doing.
//
// Buttons rather than an automatic grant: portal access for a client is a decision
// somebody makes, and `_require_manager` on the server says the same thing.

frappe.ui.form.on("Contact", {
	refresh(frm) {
		frm.trigger("ee_training_portal_buttons");
	},

	ee_training_portal_buttons(frm) {
		if (frm.is_new()) return;
		// Manager-only on the server; hiding it from everybody else keeps the form
		// from advertising an action that will refuse.
		if (!frappe.user.has_role(["Training Manager", "System Manager"])) return;

		frm.add_custom_button(
			__("Grant training portal access"),
			() => frm.trigger("ee_grant_portal_access"),
			__("Training")
		);

		// Offered only when there is a login to take the role away from. Without
		// `user` the endpoint has nothing to act on and returns role_removed: false,
		// which reads as a failure when it is really "there was nothing here".
		if (frm.doc.user) {
			frm.add_custom_button(
				__("Revoke training portal access"),
				() => frm.trigger("ee_revoke_portal_access"),
				__("Training")
			);
		}
	},

	ee_grant_portal_access(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Grant training portal access"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "context",
					options: `<p class="text-muted">${__(
						"Creates a Website User for this contact if they do not have one, grants Training Learner, and links the login to this Contact."
					)}</p>`,
				},
				{
					fieldtype: "Check",
					fieldname: "send_welcome_email",
					label: __("Send them a welcome email"),
					// Off by default. Access is routinely prepared days before anyone
					// wants the client to know about it, and an email that arrives
					// before the courses are published is a support call.
					default: 0,
					description: __("Off by default — turn this on when you are ready for them to log in."),
				},
			],
			primary_action_label: __("Grant"),
			primary_action(values) {
				dialog.hide();
				frappe.dom.freeze(__("Granting…"));
				frappe
					.call({
						method: "erpnext_enhancements.training.portal.grant_portal_access",
						args: {
							contact: frm.doc.name,
							send_welcome_email: values.send_welcome_email ? 1 : 0,
						},
					})
					.then((r) => {
						frappe.dom.unfreeze();
						const result = (r && r.message) || {};
						frm.reload_doc();
						// Reported rather than assumed: the endpoint is idempotent, so
						// "nothing changed" is a legitimate and common outcome, and a
						// blanket "done" would leave somebody re-granting in a loop
						// wondering why the client still cannot log in.
						if (result.created) {
							frappe.show_alert({
								message: __("Created {0} and granted access.", [result.user]),
								indicator: "green",
							});
						} else if (result.role_granted) {
							frappe.show_alert({
								message: __("{0} already had a login; granted training access.", [result.user]),
								indicator: "green",
							});
						} else {
							frappe.show_alert({
								message: __("{0} already had training access.", [result.user]),
								indicator: "blue",
							});
						}
					})
					.catch(() => frappe.dom.unfreeze());
			},
		});
		dialog.show();
	},

	ee_revoke_portal_access(frm) {
		frappe.confirm(
			__(
				"Remove the Training Learner role from {0}? Their login stays — it is often the same account they use for quotes and invoices — and only their access to training is withdrawn.",
				[frm.doc.user]
			),
			() => {
				frappe.dom.freeze(__("Revoking…"));
				frappe
					.call({
						method: "erpnext_enhancements.training.portal.revoke_portal_access",
						args: { contact: frm.doc.name },
					})
					.then((r) => {
						frappe.dom.unfreeze();
						const result = (r && r.message) || {};
						frappe.show_alert({
							message: result.role_removed
								? __("Training access removed.")
								: __("They did not have training access."),
							indicator: result.role_removed ? "orange" : "blue",
						});
					})
					.catch(() => frappe.dom.unfreeze());
			}
		);
	},
});
