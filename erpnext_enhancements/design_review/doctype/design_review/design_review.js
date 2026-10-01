// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// The form is where a System Manager sets the participant list and moves the review through
// its workflow (Draft -> Open -> Closed -> Decided). Everything else happens in the Review Room.

frappe.ui.form.on("Design Review", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Open in Review Room"), () => frappe.set_route("review-room", frm.doc.name));
		if (frappe.user.has_role("System Manager")) {
			frm.add_custom_button(
				__("Import a new revision"),
				() =>
					new frappe.ui.FileUploader({
						folder: "Home",
						make_attachments_public: false,
						restrictions: { allowed_file_types: [".json"] },
						on_success: (file_doc) =>
							frappe
								.call({
									method: "erpnext_enhancements.api.design_review.import_review",
									args: { file_name: file_doc.name, review: frm.doc.name },
									freeze: true,
									freeze_message: __("Importing…"),
								})
								.then((r) => {
									const report = r.message || {};
									frappe.msgprint({
										title: __("Imported"),
										message: __("Revision {0}: {1} options, {2} screens, {3} new parts.", [
											report.revision,
											report.options,
											report.screens,
											report.parts_added,
										]),
										indicator: "green",
									});
									frm.reload_doc();
								}),
					}),
				__("Concepts")
			);
		}
	},
});
