// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Triton Assistant Settings form client script.
 *
 * Auto-loaded by Frappe as the doctype's form script (it lives alongside the
 * doctype; no doctype_js hooks.py entry is needed).
 *
 * Adds a "Test Connection" custom button that calls the whitelisted server
 * method `test_connection` (which mints a bridge token against the shared
 * Triton Settings connection) and shows the result in a green/red msgprint.
 */
frappe.ui.form.on("Triton Assistant Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			frm.call({
				method: "erpnext_enhancements.ai_governance.doctype.triton_assistant_settings.triton_assistant_settings.test_connection",
				freeze: true,
				freeze_message: __("Contacting Triton…"),
			}).then((r) => {
				const res = r.message || {};
				frappe.msgprint({
					title: res.ok ? __("Connection OK") : __("Connection Failed"),
					message: res.message || (res.ok ? __("Success") : __("Unknown error")),
					indicator: res.ok ? "green" : "red",
				});
			});
		});
	},
});
