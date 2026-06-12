/**
 * "Open Drive Folder" button for documents linked to a Google Drive folder.
 *
 * Targets: Project, Customer and Opportunity forms (the doctypes whose
 *   `custom_drive_folder_id` is set by crm_enhancements.drive_utils
 *   provisioning or the settings-page backfill).
 * Loaded via: hooks.py `doctype_js` for each of those doctypes.
 *
 * The folder ID field itself stays hidden; this renders the human affordance.
 */
(function () {
	const DOCTYPES = ["Project", "Customer", "Opportunity"];
	DOCTYPES.forEach((doctype) => {
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				const folder_id = frm.doc.custom_drive_folder_id;
				if (!folder_id || frm.is_new()) return;
				frm.add_custom_button(__("Open Drive Folder"), () => {
					window.open(
						`https://drive.google.com/drive/folders/${encodeURIComponent(folder_id)}`,
						"_blank"
					);
				});
			},
		});
	});
})();
