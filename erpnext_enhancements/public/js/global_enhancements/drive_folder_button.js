/**
 * "Open Drive Folder" button for documents linked to a Google Drive folder.
 *
 * Targets: Project, Customer and Opportunity forms (the doctypes whose
 *   `custom_drive_folder_id` is set by google_drive.drive_utils
 *   provisioning or the settings-page backfill).
 * Loaded via: hooks.py `doctype_js` for each of those doctypes.
 *
 * The folder ID field itself stays hidden; this renders the human affordance.
 *
 * A folder ID outlives the folder. Delete or move one in Drive and the ID sits
 * here pointing at nothing, so the button hands the user a bare Google 404 that
 * reads like ERPNext is broken — PRJ-00706 and PRJ-00695 were both in that state.
 * `custom_drive_folder_missing`, set by the daily reconcile in
 * `google_drive.drive_sync`, is the form's only cheap way to know: probing Drive
 * on click would cost a network round-trip before every open.
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
				// Still a button rather than nothing: the link being dead is
				// information the person wants, and silently hiding it just makes
				// them hunt for a button that used to be there.
				if (frm.doc.custom_drive_folder_missing) {
					frm.add_custom_button(
						__("Drive Folder Missing"),
						() => {
							frappe.msgprint({
								title: __("Drive Folder Missing"),
								indicator: "orange",
								message: __(
									"The Google Drive folder linked to this record no longer exists, or is no longer shared with the Drive service account. Opening it would show a Google 404.<br><br>Folder ID: <code>{0}</code><br><br>Either re-link this record in the <a href='/app/drive-link-manager'>Drive Link Manager</a>, or restore the folder from the Shared Drive trash and press <b>Check Drive Links</b> on Project Folder Google Drive Settings.",
									[frappe.utils.escape_html(folder_id)]
								),
							});
						},
						group ? __(group) : undefined
					);
					return;
				}
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
