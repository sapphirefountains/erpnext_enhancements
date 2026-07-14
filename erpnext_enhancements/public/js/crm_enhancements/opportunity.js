/**
 * Opportunity form script (CRM enhancements).
 *
 * Targets: the Opportunity DocType form.
 * Loaded via: hooks.py `doctype_js["Opportunity"]`.
 *
 * Responsibilities:
 *  - Mirror the `custom_value_stream` child-table rows into the document's
 *    `_user_tags` so each value stream (Build/Design/Events/Service) shows up as a
 *    tag; for already-saved docs missing those tags it persists them directly via
 *    the `sync_opportunity_tags_for_existing` API instead of waiting for a save.
 *  - Reopen-on-load: an Opportunity already "Closed Won" with no project yet gets
 *    the "Create project now?" prompt once (here "No" just dismisses). Fresh
 *    transitions into Closed Won are handled by the global realtime prompt in
 *    create_project_prompt.js — there is no longer a manual "Create Project"
 *    button (project creation is gated entirely behind that prompt).
 *  - Listen on the `project_creation_status` realtime channel to report
 *    background project-creation success/failure (incl. Google Drive folder
 *    provisioning results).
 */
function sync_tags_from_child_table(frm) {
	let value_streams = (frm.doc.custom_value_stream || [])
		.map((row) => row.value_stream)
		.filter(Boolean);
	let current_tags_str = frm.doc._user_tags || "";
	let current_tags = current_tags_str
		.split(",")
		.map((t) => t.trim())
		.filter(Boolean);

	let core_tags = ["Build", "Design", "Events", "Service", "Delivery", "Products"];
	let other_tags = current_tags.filter((t) => !core_tags.includes(t));

	let desired_tags = [...new Set([...other_tags, ...value_streams])];
	let new_tags_str = desired_tags.length ? "," + desired_tags.sort().join(",") + "," : null;

	if (frm.doc._user_tags !== new_tags_str) {
		let has_no_core_tags = !current_tags.some((t) => core_tags.includes(t));

		// If it's an existing document with missing tags, save it directly to the database via API.
		// Skipped while live collab applies a remote change: the originating
		// client performs this write; receivers only mirror tags locally.
		let applying_remote = frm._live_sync && frm._live_sync.applying_remote;
		if (
			!frm.is_new() &&
			!frm.is_dirty() &&
			has_no_core_tags &&
			value_streams.length > 0 &&
			!applying_remote
		) {
			frappe.call({
				method: "erpnext_enhancements.crm_enhancements.api.sync_opportunity_tags_for_existing",
				args: { opportunity_name: frm.doc.name },
				callback: function (r) {
					if (r.message !== undefined) {
						frm.doc._user_tags = r.message;
						if (frm.tags && frm.tags.refresh) frm.tags.refresh(frm.doc._user_tags);
					}
				},
			});
		} else {
			// Update locally for real-time preview (will be saved when the form is saved)
			frm.doc._user_tags = new_tags_str;
			if (frm.tags && frm.tags.refresh) {
				frm.tags.refresh(frm.doc._user_tags);
			}
		}
	}
}

frappe.ui.form.on("Opportunity", {
	setup: function (frm) {
		// Dynamically bind to the child table's doctype for real-time updates when a row changes
		let child_df = frappe.meta.get_docfield("Opportunity", "custom_value_stream");
		if (child_df && child_df.options) {
			frappe.ui.form.on(child_df.options, {
				value_stream: function (child_frm, cdt, cdn) {
					sync_tags_from_child_table(child_frm);
				},
			});
		}
	},
	onload: function (frm) {
		// Capture the status the form loaded with, so the reopen-on-load prompt
		// only fires for opportunities that were ALREADY Closed Won when opened —
		// a fresh transition (handled by the realtime prompt) loads as non-won.
		frm._ee_loaded_status = frm.doc.status;
	},
	custom_value_stream_add: function (frm) {
		sync_tags_from_child_table(frm);
	},
	custom_value_stream_remove: function (frm) {
		sync_tags_from_child_table(frm);
	},
	refresh: function (frm) {
		// Sync tags on load
		sync_tags_from_child_table(frm);

		// Project creation is triggered only by the "Create project now?" prompt
		// (create_project_prompt.js), never a manual button. Defensively drop any
		// standard "Create > Project" entry if a future ERPNext / site adds one.
		frm.remove_custom_button("Project", "Create");

		// Reopen-on-load: an opportunity already Closed Won with no project yet
		// gets the same prompt once ("No" just dismisses — it was won intentionally
		// earlier). Guarded to fire at most once per load and never for a status
		// change made during this session (the realtime prompt covers transitions).
		if (
			!frm._ee_reopen_checked &&
			!frm.is_new() &&
			frm._ee_loaded_status === "Closed Won" &&
			frm.doc.status === "Closed Won" &&
			!frm.doc.custom_created_project &&
			erpnext_enhancements.crm &&
			erpnext_enhancements.crm.confirm_create_project
		) {
			frm._ee_reopen_checked = true;
			erpnext_enhancements.crm.confirm_create_project(frm.doc.name, {
				mode: "reopen",
				frm: frm,
			});
		}

		// The real-time listener for completion remains exactly the same.
		frappe.realtime.on("project_creation_status", function (data) {
			if (data.opportunity_name === frm.doc.name) {
				if (data.status === "success") {
					let success_message = __("Project {0} created successfully.", [
						`<a href="/app/project/${data.project_doc.name}">${data.project_doc.name}</a>`,
					]);

					if (data.drive_success) {
						success_message = __(
							"Project {0} created and Google Drive directory provisioned successfully.",
							[
								`<a href="/app/project/${data.project_doc.name}">${data.project_doc.name}</a>`,
							]
						);
					}

					frappe.show_alert(
						{
							message: success_message,
							indicator: "green",
						},
						10
					);

					if (data.drive_error) {
						let msg = `
							<p>Project created, but folder provisioning failed.</p>
							<details>
								<summary>Technical Error Details</summary>
								<pre style="margin-top: 10px; padding: 10px; background-color: var(--subtle-fg); border: 1px solid var(--border-color);">${data.drive_error}</pre>
							</details>
							<p><a href="/app/error-log">Check Error Log</a></p>
						`;
						frappe.msgprint({
							title: __("Google Drive Integration Failed"),
							indicator: "orange",
							message: msg,
						});
					}

					frm.reload_doc();
				} else {
					frappe.show_alert(
						{
							message: __(
								"Project creation failed. Please check the Error Log for details."
							),
							indicator: "red",
						},
						10
					);
				}
			}
		});
	},
});
