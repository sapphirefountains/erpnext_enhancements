/*
 * Fountain Move Request list view: the "Send Intake Link" flow.
 *
 * Mirrors public/js/stripe_payments/sales_invoice_pay_button.js — dialog, freeze
 * while sending, green toast naming the channel and recipient.
 *
 * Registered BOTH as listview_settings.primary_action and as an inner button.
 * The primary action alone is not reliable: frappe's list view calls
 * toggle_actions_menu_button() on selection change, which clears and re-sets the
 * primary action, so anything assigned imperatively in onload() gets wiped the
 * first time someone ticks a checkbox. The inner button is the one with
 * precedent elsewhere in this repo and always survives.
 */

frappe.listview_settings["Fountain Move Request"] = {
	add_fields: ["status", "first_name", "last_name", "city", "created_opportunity"],

	get_indicator: function (doc) {
		const map = {
			New: ["New", "blue", "status,=,New"],
			Queued: ["Queued", "orange", "status,=,Queued"],
			Converting: ["Converting", "orange", "status,=,Converting"],
			Converted: ["Converted", "green", "status,=,Converted"],
			"Duplicate Review": ["Needs a decision", "yellow", "status,=,Duplicate Review"],
			Failed: ["Failed", "red", "status,=,Failed"],
			Spam: ["Spam", "gray", "status,=,Spam"],
			Rejected: ["Rejected", "gray", "status,=,Rejected"],
		};
		return map[doc.status] || [doc.status, "gray", "status,=," + doc.status];
	},

	onload: function (listview) {
		if (!frappe.boot.ee_fountain_move) return;

		listview.page.add_inner_button(__("Send Intake Link"), function () {
			open_send_dialog();
		});

		listview.page.add_inner_button(__("Copy Public Link"), function () {
			copy_public_link();
		});
	},

	primary_action: function () {
		if (frappe.boot.ee_fountain_move) {
			open_send_dialog();
		} else {
			frappe.new_doc("Fountain Move Request");
		}
	},
};

function open_send_dialog() {
	const dialog = new frappe.ui.Dialog({
		title: __("Send the fountain move form"),
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "intro",
				options: `<p class="text-muted">${__(
					"Emails the customer a link to the intake form. Whoever sends it owns the opportunity their submission creates."
				)}</p>`,
			},
			{
				fieldtype: "Data",
				fieldname: "recipient_email",
				label: __("Customer email"),
				options: "Email",
				reqd: 1,
			},
			{
				fieldtype: "Data",
				fieldname: "recipient_name",
				label: __("Customer name"),
				description: __("Used to greet them in the email."),
			},
			{ fieldtype: "Column Break" },
			{
				fieldtype: "Select",
				fieldname: "ct_location",
				label: __("Purchased at"),
				description: __("Pre-selects the store on the form. Optional."),
			},
			{
				fieldtype: "Data",
				fieldname: "recipient_phone",
				label: __("Mobile number"),
				options: "Phone",
			},
			{
				fieldtype: "Check",
				fieldname: "also_text",
				label: __("Also text them the link"),
				depends_on: "recipient_phone",
			},
			{ fieldtype: "Section Break" },
			{
				fieldtype: "Small Text",
				fieldname: "message",
				label: __("Personal note (optional)"),
				description: __("Added to the email. Plain text only."),
			},
		],
		primary_action_label: __("Send"),
		primary_action(values) {
			dialog.disable_primary_action();
			frappe.call({
				method:
					"erpnext_enhancements.crm_enhancements.fountain_move.invites.send_intake_link",
				args: values,
				freeze: true,
				freeze_message: __("Sending the link…"),
				callback(response) {
					const result = response.message;
					if (!result) {
						dialog.enable_primary_action();
						return;
					}
					dialog.hide();
					report_sent(result);
				},
				error() {
					dialog.enable_primary_action();
				},
			});
		},
	});

	populate_locations(dialog);
	dialog.show();
}

function populate_locations(dialog) {
	// The store list is operator-editable in settings, so read it rather than
	// hardcoding three options that will eventually be wrong.
	frappe.call({
		method: "frappe.client.get",
		args: { doctype: "ERPNext Enhancements Settings" },
		callback(response) {
			const rows = (response.message || {}).fountain_move_locations || [];
			const names = rows.filter((row) => !row.disabled).map((row) => row.location_name);
			dialog.set_df_property("ct_location", "options", [""].concat(names).join("\n"));
		},
	});
}

function report_sent(result) {
	frappe.show_alert(
		{
			message: __("Link emailed to {0}", [result.to]),
			indicator: "green",
		},
		7
	);

	// SMS is best-effort and reports its own outcome. Say so plainly rather than
	// letting a silent failure look like success.
	const sms_messages = {
		sent: [__("Also texted."), "green"],
		failed: [__("The text message could not be sent — the email did go out."), "orange"],
		no_number: [__("No valid mobile number, so no text was sent."), "orange"],
		no_employee: [
			__("Texting needs an active Employee record linked to your user."),
			"orange",
		],
		not_permitted: [__("You do not have permission to send text messages."), "orange"],
	};
	if (result.sms && sms_messages[result.sms]) {
		const [message, indicator] = sms_messages[result.sms];
		frappe.show_alert({ message, indicator }, 7);
	}
}

function copy_public_link() {
	frappe.call({
		method: "erpnext_enhancements.crm_enhancements.fountain_move.invites.get_public_form_url",
		callback(response) {
			const result = response.message || {};
			if (!result.url) return;
			frappe.utils.copy_to_clipboard(result.url);
			if (result.live) {
				frappe.show_alert(
					{ message: __("Public form link copied. Safe to print or share."), indicator: "green" },
					7
				);
			} else {
				// Copying a link that 404s is worse than not copying one.
				frappe.msgprint({
					title: __("Link copied, but the form is not live"),
					indicator: "orange",
					message: __(
						"The public form is switched off, so this link currently shows a 'not found' page. Turn on <b>Publish Public Intake Form</b> in ERPNext Enhancements Settings before sharing it."
					),
				});
			}
		},
	});
}
