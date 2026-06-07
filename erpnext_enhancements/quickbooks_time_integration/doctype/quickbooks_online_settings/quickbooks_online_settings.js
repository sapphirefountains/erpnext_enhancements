frappe.ui.form.on("QuickBooks Online Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Dashboard"), () => frappe.set_route("quickbooks-online-dashboard"));
		frm.add_custom_button(__("Link Existing Records"), () => frappe.set_route("quickbooks-online-dashboard"));
		frm.add_custom_button(__("Connect QuickBooks"), () => {
			frappe.call({
				method: "erpnext_enhancements.quickbooks_time_integration.quickbooks_online.api.start_oauth",
				args: { environment: frm.doc.environment },
				callback(response) {
					const url = response.message && response.message.authorization_url;
					if (url) {
						window.location.href = url;
					}
				},
			});
		});
		frm.add_custom_button(__("Import All"), () => {
			frappe.call({
				method: "erpnext_enhancements.quickbooks_time_integration.quickbooks_online.api.import_all",
				freeze: true,
				freeze_message: __("Importing QuickBooks Online data..."),
				callback(response) {
					frappe.msgprint(__("Import completed in log {0}", [response.message]));
				},
			});
		});
		frm.add_custom_button(__("Preview Resync"), () => {
			frappe.call({
				method: "erpnext_enhancements.quickbooks_time_integration.quickbooks_online.api.preview_resync",
				freeze: true,
				freeze_message: __("Building resync preview..."),
				callback(response) {
					const result = response.message || {};
					frappe.set_route("Form", "QuickBooks Sync Log", result.preview_id);
				},
			});
		});
	},
});
