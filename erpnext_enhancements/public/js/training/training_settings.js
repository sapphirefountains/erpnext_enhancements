// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Training Settings — pasting the GCS signing key, and checking it works.
//
// The key field is a `Password`, which Frappe renders as a single-line masked
// input. That control cannot take a 2 KB multi-line service-account JSON by
// paste without mangling or truncating it, and being masked it then hides the
// damage — you get a saved-looking field and an opaque 403 on the first video.
// So the field is read-only and the key goes in through the dialog below, which
// is a real multi-line box and validates server-side before storing.

frappe.ui.form.on("Training Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test GCS Connection"), () => test_connection(frm), __("Training Media"));
	},

	set_service_account_key(frm) {
		paste_key(frm);
	},
});

function paste_key(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Set Service Account Key"),
		size: "large",
		fields: [
			{
				fieldname: "help",
				fieldtype: "HTML",
				options: `<p class="text-muted small">${__(
					"Paste the whole contents of the key file created with <code>gcloud iam service-accounts keys create</code> — including the opening and closing braces. It is checked before it is stored, and it is never shown again afterwards."
				)}</p>`,
			},
			{
				fieldname: "key_json",
				fieldtype: "Code",
				label: __("Service Account JSON"),
				reqd: 1,
				// Not "JSON": the JSON editor tries to reformat and lint as you
				// type, which fights a paste of this size.
				options: "Text",
			},
		],
		primary_action_label: __("Store Key"),
		primary_action(values) {
			frappe.call({
				method: "erpnext_enhancements.training.gcs_media.set_service_account_key",
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
					if (out.warning) {
						frappe.msgprint({
							title: __("Stored, with a caveat"),
							indicator: "orange",
							message: out.warning,
						});
					}
					frm.reload_doc();
				},
			});
		},
	});

	dialog.show();
	// The paste is the entire point of this dialog; put the cursor in it.
	setTimeout(() => dialog.fields_dict.key_json.$input?.focus(), 300);
}

function test_connection(frm) {
	frappe.call({
		method: "erpnext_enhancements.training.gcs_media.test_connection",
		freeze: true,
		freeze_message: __("Writing a probe object, signing it, fetching it…"),
		callback(r) {
			const out = r.message || {};
			frappe.msgprint({
				title: out.ok ? __("Connection OK") : __("Connection failed"),
				indicator: out.ok ? "green" : "red",
				message: out.message,
			});
		},
	});
}
