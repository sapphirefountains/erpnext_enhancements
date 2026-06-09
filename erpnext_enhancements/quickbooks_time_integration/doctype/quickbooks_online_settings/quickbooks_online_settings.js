/**
 * Client script for the QuickBooks Online Settings form.
 *
 * Adds the integration's action buttons to the Settings form toolbar:
 *  - "Dashboard" / "Link Existing Records": route to the QBO dashboard page.
 *  - "Connect QuickBooks": calls the `start_oauth` RPC and redirects the browser
 *    to the returned Intuit authorization URL to begin the OAuth2 connect flow.
 *  - "Import All": calls the `import_all` RPC (freezing the UI) and reports the
 *    resulting sync log.
 *  - "Preview Resync": calls `preview_resync` and opens the generated preview
 *    Sync Log so the user can review changes before running an overwrite resync.
 *
 * All buttons delegate to whitelisted methods under
 * erpnext_enhancements.quickbooks_time_integration.quickbooks_online.api.
 */
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
