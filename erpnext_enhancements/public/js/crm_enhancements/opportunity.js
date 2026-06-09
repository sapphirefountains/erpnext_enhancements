/**
 * Opportunity form script (CRM enhancements).
 *
 * Targets: the Opportunity DocType form.
 * Loaded via: hooks.py `doctype_js["Opportunity"]`.
 *
 * Responsibilities:
 *  - Mirror the `custom_value_stream` child-table rows into the document's
 *    `_user_tags` so each value stream (Build/Design/Rent/Service) shows up as a
 *    tag; for already-saved docs missing those tags it persists them directly via
 *    the `sync_opportunity_tags_for_existing` API instead of waiting for a save.
 *  - Add a "Create Project" button (Closed Won opportunities only, for Employees /
 *    System Managers) that prompts for a Project Template + users to notify and
 *    enqueues background project creation, then listens on the
 *    `project_creation_status` realtime channel to report success/failure
 *    (including Google Drive folder provisioning results).
 */
function sync_tags_from_child_table(frm) {
	let value_streams = (frm.doc.custom_value_stream || []).map(row => row.value_stream).filter(Boolean);
	let current_tags_str = frm.doc._user_tags || "";
	let current_tags = current_tags_str.split(",").map(t => t.trim()).filter(Boolean);

	let core_tags = ["Build", "Design", "Rent", "Service"];
	let other_tags = current_tags.filter(t => !core_tags.includes(t));

	let desired_tags = [...new Set([...other_tags, ...value_streams])];
	let new_tags_str = desired_tags.length ? "," + desired_tags.sort().join(",") + "," : null;

	if (frm.doc._user_tags !== new_tags_str) {
		let has_no_core_tags = !current_tags.some(t => core_tags.includes(t));

		// If it's an existing document with missing tags, save it directly to the database via API
		if (!frm.is_new() && !frm.is_dirty() && has_no_core_tags && value_streams.length > 0) {
			frappe.call({
				method: "erpnext_enhancements.crm_enhancements.api.sync_opportunity_tags_for_existing",
				args: { opportunity_name: frm.doc.name },
				callback: function(r) {
					if (r.message !== undefined) {
						frm.doc._user_tags = r.message;
						if (frm.tags && frm.tags.refresh) frm.tags.refresh(frm.doc._user_tags);
					}
				}
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
	setup: function(frm) {
		// Dynamically bind to the child table's doctype for real-time updates when a row changes
		let child_df = frappe.meta.get_docfield("Opportunity", "custom_value_stream");
		if (child_df && child_df.options) {
			frappe.ui.form.on(child_df.options, {
				value_stream: function(child_frm, cdt, cdn) {
					sync_tags_from_child_table(child_frm);
				}
			});
		}
	},
	custom_value_stream_add: function(frm) {
		sync_tags_from_child_table(frm);
	},
	custom_value_stream_remove: function(frm) {
		sync_tags_from_child_table(frm);
	},
	refresh: function (frm) {
		// Sync tags on load
		sync_tags_from_child_table(frm);

		function toggle_project_button() {
			if (
				frm.doc.status === "Closed Won" &&
				!frm.doc.custom_created_project &&
				(frappe.user.has_role("Employee") || frappe.user.has_role("System Manager"))
			) {
				frm.add_custom_button(__("Create Project"), function () {
					// 1. Fetch the list of users first.
					frappe.call({
						method: "frappe.client.get_list",
						args: {
							doctype: "User",
							filters: {
								enabled: 1,
								user_type: "System User",
							},
							fields: ["name"],
							limit_page_length: 0,
						},
						callback: function (r) {
							let users = r.message || [];
							let user_options = users.map((u) => u.name);

							// 2. Create a dialog to ask for the Project Template.
							let dialog = new frappe.ui.Dialog({
								title: "Select Project Template",
								fields: [
									{
										label: "Project Template",
										fieldname: "project_template",
										fieldtype: "Link",
										options: "Project Template",
										reqd: 1, // Make the selection mandatory
									},
									{
										label: "Users to Notify",
										fieldname: "users_to_notify",
										fieldtype: "MultiSelect",
										options: user_options,
										default: [frappe.session.user],
										reqd: 1, // Make the selection mandatory
										description:
											"Select users to be notified when the project is created.",
									},
								],
								primary_action_label: "Create Project",
								// 3. This code runs when the user clicks "Create Project".
								primary_action: function (values) {
									// Change the dialog to show a progress bar.
									dialog
										.get_primary_btn()
										.prop("disabled", true)
										.html("Queuing...");
									dialog.body.innerHTML = `
                                        <div class="progress">
                                            <div class="progress-bar progress-bar-striped progress-bar-animated" style="width: 100%"></div>
                                        </div>
                                        <div class="text-center" style="margin-top: 10px;">
                                            Adding job to the queue...
                                        </div>`;

									// 4. Call the backend with the selected template.
									frappe.call({
										method: "erpnext_enhancements.crm_enhancements.api.enqueue_project_creation",
										args: {
											opportunity_name: frm.doc.name,
											users: values.users_to_notify, // Pass the selected users
											project_template: values.project_template, // Pass the selected template
										},
										callback: function (r) {
											dialog.hide(); // Close the dialog.
											if (r.message && r.message.status === "queued") {
												frappe.show_alert({
													message: __(
														"Project creation started in the background. Awaiting completion..."
													),
													indicator: "blue",
												});
												frm.remove_custom_button("Create Project");
											}
										},
									});
								},
							});

							dialog.show();
						},
					});
				}).addClass("btn-primary");
			} else {
				frm.remove_custom_button("Create Project");
			}
		}
		toggle_project_button();
		frm.fields_dict["status"].$input.on("change", toggle_project_button);

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
								<pre style="margin-top: 10px; padding: 10px; background-color: #f8f9fa; border: 1px solid #ddd;">${data.drive_error}</pre>
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
