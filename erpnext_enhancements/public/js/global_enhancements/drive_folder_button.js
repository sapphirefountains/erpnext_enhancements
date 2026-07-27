/**
 * "Open Drive Folder" button for documents linked to a Google Drive folder.
 *
 * Targets: Project, Customer and Opportunity forms (the doctypes whose
 *   `custom_drive_folder_id` is set by google_drive.drive_utils
 *   provisioning or the settings-page backfill).
 * Loaded via: hooks.py `doctype_js` for each of those doctypes.
 *
 * The folder ID field itself stays hidden; this renders the human affordance.
 */
(function () {
	// Toolbar placement per doctype. Project's toolbar is crowded and ERPNext gives
	// it a native "View" group (Gantt Chart / Kanban Board), so the button folds in
	// there. Customer and Opportunity have no such group: putting it in one would
	// mint a dropdown holding a single item, which costs a click and reads worse
	// than the plain button it replaced. They keep it top-level.
	const DOCTYPE_GROUPS = {
		Project: "View",
		Customer: null,
		Opportunity: null,
	};
	Object.entries(DOCTYPE_GROUPS).forEach(([doctype, group]) => {
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				const folder_id = frm.doc.custom_drive_folder_id;
				if (!folder_id || frm.is_new()) return;
				frm.add_custom_button(
					__("Open Drive Folder"),
					() => {
						window.open(
							`https://drive.google.com/drive/folders/${encodeURIComponent(folder_id)}`,
							"_blank"
						);
					},
					group ? __(group) : undefined
				);
			},
		});
	});
})();
