/**
 * Closed Won -> "what's next?" prompt (shared, global).
 *
 * Loaded globally via erpnext_enhancements.bundle.js (app_include_js), so it
 * works on the Opportunity form, the Kanban board, and the list view — anywhere
 * a status change to "Closed Won" lands. The server half
 * (crm_enhancements/project_prompt.py) detects the transition on Opportunity
 * on_update and publishes the "ee_prompt_create_project" realtime event to the
 * acting user; the global listener below shows the popup.
 *
 * The prompt asks for whichever step is actually next (v1.263.0), because on a
 * fresh transition the answer is never "create the project" — the hand-off gate
 * wants a meeting booked first, and offering a dialog that is guaranteed to be
 * refused was the worst of both:
 *
 *   - hand-off still unbooked -> "Schedule the hand-off meeting now?", Yes opens
 *     the booking dialog (shared with the Hand-Off Process tab).
 *   - otherwise               -> "Create project now?", Yes opens the "Create
 *     Project" dialog (Project Template + Users to Notify, defaulting to the
 *     Account Executive + Project Manager role holders) and then the same
 *     background creation the old button used.
 *
 * No is unchanged and means the same thing for both questions — mode
 * "transition": roll the opportunity back out of Closed Won and clear the
 * won-date stamp; mode "reopen": just dismiss. That consequence is spelled out
 * in the prompt itself, since "not right now" is an easy thing to read into a
 * No that actually un-wins the deal.
 *
 * Project creation has no manual button on the form: this prompt, the form's
 * reopen-on-load check (mode "reopen"; see opportunity.js) and the Hand-Off
 * Process tab are the entry points.
 */
frappe.provide("erpnext_enhancements.crm");

(function () {
	const EVENT = "ee_prompt_create_project";
	const open_prompts = {}; // opportunity_name -> true while a prompt/dialog is showing

	// The "Create Project" dialog (template + users to notify), defaulting the
	// notify list to the Account Executive + Project Manager role holders. Mirrors
	// the dialog the old "Create Project" button used to show.
	/**
	 * Gate check before the create-project dialog opens (PRO-0204, 2026-08-06).
	 *
	 * A hand-off meeting must be booked on the Opportunity first. This is
	 * signposting, not enforcement — `process_steps.enforce_handoff_gate` refuses
	 * on Project `before_insert` regardless — but offering a dialog that is
	 * guaranteed to fail is a worse experience than saying why up front, and this
	 * prompt is the path most people take.
	 *
	 * Reads the server's own `gate_open` rather than re-deriving the predicate
	 * from the pieces: this check and the insert have to agree, and there are now
	 * two ways to open the gate.
	 */
	function open_create_project_dialog(opportunity_name, opts) {
		frappe
			.xcall("erpnext_enhancements.crm_enhancements.handoff.handoff_state", {
				opportunity: opportunity_name,
			})
			.then(function (state) {
				if (state && state.available && state.enabled && !state.gate_open) {
					frappe.msgprint({
						title: __("Hand-Off Required"),
						indicator: "orange",
						message:
							frappe.utils.escape_html(state.gate_message) +
							`<p><a href="/app/opportunity/${encodeURIComponent(opportunity_name)}">${__(
								"Open the opportunity"
							)}</a></p>`,
					});
					return;
				}
				_open_create_project_dialog(opportunity_name, opts);
			})
			.catch(function () {
				// A transient failure of the advisory check must not block the
				// dialog: the server-side gate is still there to catch it.
				_open_create_project_dialog(opportunity_name, opts);
			});
	}

	function _open_create_project_dialog(opportunity_name, opts) {
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

	// Ask for whatever step is next, and route Yes/No.
	//  - mode "transition": No rolls the opportunity back out of Closed Won.
	//  - mode "reopen": No just dismisses (it was intentionally won earlier).
	function confirm_won_next_step(opportunity_name, opts) {
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

		// The No branch is the same for both questions, and its consequence is
		// worth spelling out: on a fresh transition, No un-wins the deal.
		const on_no = function () {
			done();
			if (opts.mode !== "transition") return; // reopen mode: just dismiss.
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
		};

		const ask = function (question, on_yes) {
			const message =
				opts.mode === "transition"
					? `${question}<p class="text-muted" style="margin-top:8px;">${__(
							"No returns this opportunity to its previous status."
						)}</p>`
					: question;
			const d = frappe.confirm(message, on_yes, on_no);
			// Also release the guard if the dialog is closed via Esc/X (no button).
			if (d) {
				const prev_onhide = d.onhide;
				d.onhide = function () {
					done();
					if (typeof prev_onhide === "function") prev_onhide.call(d);
				};
			}
		};

		const ask_create = function () {
			ask(__("Create project now?"), function () {
				done();
				open_create_project_dialog(opportunity_name, {
					frm: opts.frm,
					on_success: refresh_view,
				});
			});
		};

		const ask_schedule = function () {
			ask(__("Schedule the hand-off meeting now?"), function () {
				done();
				erpnext_enhancements.handoff_meeting_dialog.schedule_for_opportunity(opportunity_name, {
					on_success: refresh_view,
				});
			});
		};

		frappe
			.xcall("erpnext_enhancements.crm_enhancements.handoff.handoff_state", {
				opportunity: opportunity_name,
			})
			.then(function (state) {
				const needs_meeting =
					state && state.available && state.enabled && state.gate_applies && !state.held && !state.event;
				if (needs_meeting) ask_schedule();
				else ask_create();
			})
			// A transient failure of the state read must not swallow the prompt
			// entirely: fall back to the older question, which the gate check
			// inside the create dialog will correct if it turns out to be wrong.
			.catch(ask_create);
	}

	erpnext_enhancements.crm.open_create_project_dialog = open_create_project_dialog;
	erpnext_enhancements.crm.confirm_won_next_step = confirm_won_next_step;
	// Kept as an alias: the name it went out under, and a site Client Script may
	// well be calling it.
	erpnext_enhancements.crm.confirm_create_project = confirm_won_next_step;

	// Global listener: the server fires this to the acting user on the transition
	// into Closed Won (form save, Kanban drag, list edit, API — all covered).
	if (!frappe._ee_prompt_listener_registered) {
		frappe._ee_prompt_listener_registered = true;
		frappe.realtime.on(EVENT, function (data) {
			if (!data || !data.opportunity_name) return;
			confirm_won_next_step(data.opportunity_name, {
				mode: "transition",
				previous_status: data.previous_status,
			});
		});
	}
})();
