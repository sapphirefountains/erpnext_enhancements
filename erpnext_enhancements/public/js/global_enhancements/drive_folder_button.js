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
				// What the button does lives in record_actions.js since v1.686.0
				// (erpnext_enhancements.record_actions), shared with the guided page's
				// Actions menu. A tab still holding the bundle from before then has no
				// record_actions: no button until it reloads, rather than one that throws.
				const actions = window.erpnext_enhancements && erpnext_enhancements.record_actions;
				if (!actions) return;
				// Still a button rather than nothing: the link being dead is
				// information the person wants, and silently hiding it just makes
				// them hunt for a button that used to be there.
				const missing = !!frm.doc.custom_drive_folder_missing;
				frm.add_custom_button(
					missing ? __("Drive Folder Missing") : __("Open Drive Folder"),
					() => actions.open_drive_folder(folder_id, missing),
					group ? __(group) : undefined
				);
			},
		});
	});
})();
