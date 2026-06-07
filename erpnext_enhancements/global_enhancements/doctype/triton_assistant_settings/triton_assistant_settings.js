// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.ui.form.on("Triton Assistant Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			frm.call({
				method: "erpnext_enhancements.global_enhancements.doctype.triton_assistant_settings.triton_assistant_settings.test_connection",
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
