// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Manual-upload channel for the Accounting Document Intake queue: a one-click
// "Upload Document" button that uploads a file and creates a Document Intake
// row (status Received) via the whitelisted ingest_upload endpoint.
frappe.listview_settings["Document Intake"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Upload Document"), () => {
			new frappe.ui.FileUploader({
				folder: "Home/Attachments",
				on_success: (file_doc) => {
					frappe.call({
						method: "erpnext_enhancements.accounting_intake.intake.ingest_upload",
						args: { file_url: file_doc.file_url },
						freeze: true,
						freeze_message: __("Adding to the intake queue…"),
						callback: (r) => {
							if (!r.exc && r.message && r.message.name) {
								frappe.show_alert(
									{ message: __("Queued: {0}", [r.message.name]), indicator: "green" },
									5,
								);
								listview.refresh();
							}
						},
					});
				},
			});
		});
	},
};
