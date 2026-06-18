/**
 * Closed Won -> "Create project now?" prompt (shared, global).
 *
 * Loaded globally via erpnext_enhancements.bundle.js (app_include_js), so it
 * works on the Opportunity form, the Kanban board, and the list view — anywhere
 * a status change to "Closed Won" lands. The server half
 * (crm_enhancements/project_prompt.py) detects the transition on Opportunity
 * on_update and publishes the "ee_prompt_create_project" realtime event to the
 * acting user; the global listener below shows the popup.
 *
 * Project creation no longer has a manual button — this prompt (and the form's
 * reopen-on-load check, which calls confirm_create_project with mode "reopen";
 * see opportunity.js) is the only entry point.
 *
 *   - Yes -> the "Create Project" dialog (Project Template + Users to Notify,
 *     defaulting to the Account Executive + Project Manager role holders), then
 *     the same background creation the old button used.
 *   - No  -> mode "transition": roll the opportunity back out of Closed Won and
 *            clear the won-date stamp. mode "reopen": just dismiss.
 */
frappe.provide("erpnext_enhancements.crm");

(function () {
	const EVENT = "ee_prompt_create_project";
	const open_prompts = {}; // opportunity_name -> true while a prompt/dialog is showing

	// The "Create Project" dialog (template + users to notify), defaulting the
	// notify list to the Account Executive + Project Manager role holders. Mirrors
	// the dialog the old "Create Project" button used to show.
	function open_create_project_dialog(opportunity_name, opts) {
		opts = opts || {};
		Promise.all([
			frappe.xcall(
				"erpnext_enhancements.crm_enhancements.project_prompt.default_project_notify_users"
			),
			frappe.xcall("frappe.client.get_list", {
				doctype: "User",
				filters: { enabled: 1, user_type: "System User" },
				fields: ["name"],
				limit_page_length: 0,
			}),
		]).then(function (results) {
			const default_users = results[0] || [];
			const user_options = (results[1] || []).map(function (u) {
				return u.name;
			});

			const dialog = new frappe.ui.Dialog({
				title: __("Create Project"),
				fields: [
					{
						label: __("Project Template"),
						fieldname: "project_template",
						fieldtype: "Link",
						options: "Project Template",
						reqd: 1,
					},
					{
						label: __("Users to Notify"),
						fieldname: "users_to_notify",
						fieldtype: "MultiSelect",
						options: user_options,
						default: default_users,
						reqd: 1,
						description: __(
							"Defaults to the Account Executive and Project Manager role holders. Notified when the project is created."
						),
					},
				],
				primary_action_label: __("Create Project"),
				primary_action: function (values) {
					dialog.get_primary_btn().prop("disabled", true).html(__("Queuing..."));
					dialog.body.innerHTML = `
						<div class="progress">
							<div class="progress-bar progress-bar-striped progress-bar-animated" style="width: 100%"></div>
						</div>
						<div class="text-center" style="margin-top: 10px;">
							${__("Adding job to the queue...")}
						</div>`;

					frappe.call({
						method: "erpnext_enhancements.crm_enhancements.api.enqueue_project_creation",
						args: {
							opportunity_name: opportunity_name,
							users: values.users_to_notify,
							project_template: values.project_template,
						},
						callback: function (r) {
							dialog.hide();
							if (r.message && r.message.status === "queued") {
								frappe.show_alert({
									message: __(
										"Project creation started in the background. Awaiting completion..."
									),
									indicator: "blue",
								});
								if (typeof opts.on_success === "function") opts.on_success();
							}
						},
					});
				},
			});

			dialog.show();
		});
	}

	// Ask "Create project now?" and route Yes/No.
	//  - mode "transition": No rolls the opportunity back out of Closed Won.
	//  - mode "reopen": No just dismisses (it was intentionally won earlier).
	function confirm_create_project(opportunity_name, opts) {
		opts = opts || {};
		if (open_prompts[opportunity_name]) return; // already prompting for this opp
		open_prompts[opportunity_name] = true;

		const done = function () {
			delete open_prompts[opportunity_name];
		};

		const refresh_view = function () {
			if (opts.frm && opts.frm.doc && opts.frm.doc.name === opportunity_name) {
				opts.frm.reload_doc();
			} else if (
				typeof cur_frm !== "undefined" &&
				cur_frm &&
				cur_frm.doctype === "Opportunity" &&
				cur_frm.doc &&
				cur_frm.doc.name === opportunity_name
			) {
				cur_frm.reload_doc();
			} else if (typeof cur_list !== "undefined" && cur_list && cur_list.refresh) {
				cur_list.refresh(); // repaints the Kanban board / list (e.g. a reverted card)
			}
		};

		const d = frappe.confirm(
			__("Create project now?"),
			function () {
				// Yes
				done();
				open_create_project_dialog(opportunity_name, {
					frm: opts.frm,
					on_success: refresh_view,
				});
			},
			function () {
				// No
				done();
				if (opts.mode === "transition") {
					frappe.call({
						method: "erpnext_enhancements.crm_enhancements.project_prompt.revert_won_status",
						args: {
							opportunity_name: opportunity_name,
							previous_status: opts.previous_status || null,
						},
						callback: function () {
							frappe.show_alert({
								message: __("Reverted — opportunity is no longer Closed Won."),
								indicator: "orange",
							});
							refresh_view();
						},
					});
				}
				// reopen mode: just dismiss.
			}
		);

		// Also release the guard if the dialog is closed via Esc/X (no button).
		if (d) {
			const prev_onhide = d.onhide;
			d.onhide = function () {
				done();
				if (typeof prev_onhide === "function") prev_onhide.call(d);
			};
		}
	}

	erpnext_enhancements.crm.open_create_project_dialog = open_create_project_dialog;
	erpnext_enhancements.crm.confirm_create_project = confirm_create_project;

	// Global listener: the server fires this to the acting user on the transition
	// into Closed Won (form save, Kanban drag, list edit, API — all covered).
	if (!frappe._ee_prompt_listener_registered) {
		frappe._ee_prompt_listener_registered = true;
		frappe.realtime.on(EVENT, function (data) {
			if (!data || !data.opportunity_name) return;
			confirm_create_project(data.opportunity_name, {
				mode: "transition",
				previous_status: data.previous_status,
			});
		});
	}
})();
