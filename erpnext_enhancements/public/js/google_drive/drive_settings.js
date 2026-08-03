// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Project Folder Google Drive Settings — pasting the service-account key.
//
// The key field is a `Password`, which Frappe renders as a single-line masked
// input. That control cannot take a 2 KB multi-line service-account JSON by
// paste without mangling it, and being masked it then hides the damage. So the
// field is read-only and the key goes in through the dialog below.
//
// It was a `Code` field until v1.211.0, which meant the private key sat in
// cleartext and was rendered straight back onto this form.

frappe.ui.form.on("Project Folder Google Drive Settings", {
	set_service_account_key(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Set Service Account Key"),
			size: "large",
			fields: [
				{
					fieldname: "help",
					fieldtype: "HTML",
					options: `<p class="text-muted small">${__(
						"Paste the whole contents of the service-account key file, including the opening and closing braces. It is validated before it is stored, and never shown again afterwards."
					)}</p>`,
				},
				{
					fieldname: "key_json",
					fieldtype: "Code",
					label: __("Service Account JSON"),
					reqd: 1,
					// Not "JSON": that editor reformats and lints as you type, which
					// fights a paste of this size.
					options: "Text",
				},
			],
			primary_action_label: __("Store Key"),
			primary_action(values) {
				frappe.call({
					method: "erpnext_enhancements.google_drive.drive_utils.set_service_account_key",
					args: { key_json: values.key_json },
					freeze: true,
					freeze_message: __("Checking the key…"),
					callback(r) {
						const out = r.message || {};
						if (!out.ok) return;
						dialog.hide();
						frappe.show_alert({
							message: __("Key stored for {0}", [out.client_email]),
							indicator: "green",
						});
						frm.reload_doc();
					},
				});
			},
		});
		dialog.show();
		setTimeout(() => dialog.fields_dict.key_json.$input?.focus(), 300);
	},
});
