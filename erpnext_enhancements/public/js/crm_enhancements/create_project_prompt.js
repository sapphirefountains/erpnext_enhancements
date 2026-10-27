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
 *     billing, operations, production and sales inboxes) and then the same
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
 *
 * Once queued, the creation is watched (v1.749.0): a progress dialog lists the
 * background job's steps and fills its bar as each one finishes, from the
 * job's own `ee_project_creation_progress` events
 * (crm_enhancements/project_creation_progress.py). When it is over the
 * dialog closes, the record reloads wherever it is showing (the Desk form,
 * and the guided page through `sf-record-changed`), and a short alert links
 * the new Project. Every entry point shares this, so it works on whichever
 * surface started the creation. While it watches, the surfaces' own
 * `project_creation_status` listeners stand aside (`is_tracking_project_creation`)
 * so nothing is said or reloaded twice; once it is closed, they take over.
 */
frappe.provide("erpnext_enhancements.crm");

(function () {
	const EVENT = "ee_prompt_create_project";
	const open_prompts = {}; // opportunity_name -> true while a prompt/dialog is showing

	const PROGRESS_EVENT = "ee_project_creation_progress";
	const STATUS_EVENT = "project_creation_status";
	const STATE_METHOD =
		"erpnext_enhancements.crm_enhancements.project_creation_progress.project_creation_state";
	// This long without an event and the dialog asks the server where things stand: a deploy
	// empties the job queue without a word, and a socket that drops misses an event for good.
	const QUIET_MS = 15000;
	const QUIET_TIMEOUT_MS = 10000;
	const trackers = {}; // opportunity_name -> the tracker watching its creation

	// A read that says nothing when it fails (v1.749.0 review). frappe.call's own handlers put up a
	// message for a 502 or a 504, which during a deploy is every poll, and on a QueryTimeoutError
	// frappe.xcall never settles at all. The creation state and the Build out offer are advisory
	// reads, so they go through fetch (as feedback/transport.js does), settle on every outcome, a
	// timeout included, and leave the words to the dialog.
	function quiet_call(method, args) {
		const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
		const timer = controller ? setTimeout(() => controller.abort(), QUIET_TIMEOUT_MS) : null;
		return fetch(`/api/method/${method}`, {
			method: "POST",
			credentials: "same-origin",
			headers: {
				"Content-Type": "application/json",
				Accept: "application/json",
				"X-Frappe-CSRF-Token": frappe.csrf_token,
			},
			body: JSON.stringify(args || {}),
			signal: controller ? controller.signal : undefined,
		})
			.then(function (res) {
				if (!res.ok) throw new Error(`HTTP ${res.status}`);
				return res.json();
			})
			.then(function (body) {
				return body ? body.message : undefined;
			})
			.finally(function () {
				if (timer) clearTimeout(timer);
			});
	}

	// The "Create Project" dialog (template + users to notify), defaulting the
	// notify list to the group inboxes. Mirrors the dialog the old "Create
	// Project" button used to show.
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
	 *
	 * Asks first whether a creation is already under way (v1.749.0): one is then
	 * shown, rather than a form that would start a second. This is what makes a
	 * reload mid-creation, or a colleague on the same deal, safe.
	 */
	function open_create_project_dialog(opportunity_name, opts) {
		opts = opts || {};
		if (trackers[opportunity_name]) {
			trackers[opportunity_name].show();
			return;
		}
		quiet_call(STATE_METHOD, { opportunity_name: opportunity_name })
			.catch(function () {
				return null; // advisory: the server refuses a second job anyway
			})
			.then(function (state) {
				if (state && (state.phase === "queued" || state.phase === "running")) {
					make_tracker(opportunity_name, opts).begin(state);
					return;
				}
				if (state && state.project) {
					report(opportunity_name, { status: "exists", project: state.project }, opts);
					return;
				}
				check_gate_then_open(opportunity_name, opts);
			});
	}

	function check_gate_then_open(opportunity_name, opts) {
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
							`<p><a href="/desk/opportunity/${encodeURIComponent(opportunity_name)}">${__(
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
			// Fountain Move jobs: an Opportunity from the /fountain-move intake preselects the
			// Fountain Move template (the server ticks the project as a move either way). Advisory,
			// so a failure only leaves the field empty.
			frappe
				.xcall("erpnext_enhancements.project_enhancements.fountain_move_jobs.opportunity_defaults", {
					opportunity: opportunity_name,
				})
				.catch(function () {
					return {};
				}),
		]).then(function (results) {
			const default_users = results[0] || [];
			const move_defaults = results[2] || {};
			// The inboxes are not Users, so they are offered alongside the desk
			// users rather than found among them.
			const user_options = default_users.concat(
				(results[1] || [])
					.map(function (u) {
						return u.name;
					})
					.filter(function (name) {
						return (
							!default_users.includes(name) && !["Administrator", "Guest"].includes(name)
						);
					})
			);

			const dialog = new frappe.ui.Dialog({
				title: __("Create Project"),
				fields: [
					{
						label: __("Project Template"),
						fieldname: "project_template",
						fieldtype: "Link",
						options: "Project Template",
						reqd: 1,
						// The Project Planner's phase templates and bundles (v1.608.0) are not
						// project templates to create a job from: ERPNext would copy every task
						// in, all dated the project's first day. They are loaded per job from
						// the planner's Build out grid instead.
						get_query: () => ({ filters: { custom_template_kind: ["is", "not set"] } }),
						default: move_defaults.template || undefined,
						description: move_defaults.is_fountain_move
							? __("This came from the fountain move form, so the project will be a Fountain Move.")
							: undefined,
					},
					{
						label: __("Users to Notify"),
						fieldname: "users_to_notify",
						fieldtype: "MultiSelect",
						options: user_options,
						// A string, not the array: v16's MultiSelect.validate calls
						// value.replace() on the default, so an array threw and the
						// field opened empty. ignore_validation because that validate
						// returns "" for any entry outside `options` or after a ", ";
						// the text stays in the box (and is what gets submitted), but
						// the control's own value goes blank, and on a reqd field
						// that can outline it red while it plainly has addresses in it.
						default: default_users.join(", "),
						ignore_validation: 1,
						reqd: 1,
						description: __(
							"Defaults to the billing, operations, production and sales inboxes. Pick from the list or type any address. Everyone listed is emailed when the project is created."
						),
					},
				],
				primary_action_label: __("Create Project"),
				primary_action: function (values) {
					// The field takes free text, so the common typos are caught here
					// while the dialog can still be corrected. This is the browser's
					// email regex, which is looser than the server's in a few odd
					// shapes (accented or quoted local parts, IP literals, a hyphen
					// at the edge of a domain label), so the server check stays the
					// authority, and the error handler below keeps its refusal
					// recoverable as well.
					const addresses = frappe.utils.split_emails(values.users_to_notify || "");
					const invalid = addresses.filter(function (address) {
						return !frappe.utils.validate_type(address, "email");
					});
					if (invalid.length) {
						frappe.msgprint({
							title: __("Not an email address"),
							indicator: "orange",
							message: __("Fix or remove: {0}", [
								frappe.utils.escape_html(invalid.join(", ")),
							]),
						});
						return;
					}

					const primary_btn = dialog.get_primary_btn();
					const primary_label = primary_btn.html();
					primary_btn.prop("disabled", true).html(__("Queuing..."));
					// set_message hides the fields rather than replacing them (this
					// used to overwrite dialog.body), so a refusal from the server
					// can hand the form back exactly as it was.
					dialog.set_message(__("Adding job to the queue..."));

					// Watching starts before the request, not after it: a quick worker
					// can report the first step, or even finish, before the reply
					// arrives, and those events must reach the tracker rather than
					// the surfaces' own listeners.
					const tracker = make_tracker(opportunity_name, opts);
					// Settled on the request's own promise, not its callbacks: v16 runs
					// neither `callback` nor `error` on a QueryTimeoutError or a
					// QueryDeadlockError (request.js shows its own message and returns),
					// and a tracker left waiting there swallowed every later click until
					// a reload. The jqXHR frappe.call returns always settles. A refusal
					// (frappe.throw, HTTP 417) still shows its message as before.
					Promise.resolve(
						frappe.call({
							method: "erpnext_enhancements.crm_enhancements.api.enqueue_project_creation",
							args: {
								opportunity_name: opportunity_name,
								// The list that was checked, not the raw field, so the
								// server validates exactly what passed here.
								users: addresses.join(", "),
								project_template: values.project_template,
							},
						})
					)
						.then(function (r) {
							dialog.hide();
							const result = (r && r.message) || {};
							if (result.status === "exists") {
								tracker.cancel();
								report(opportunity_name, { status: "exists", project: result.project }, opts);
								return;
							}
							// "running": someone (or another tab) had already started it,
							// and that creation is the one shown.
							if (result.status === "queued" || result.status === "running") {
								tracker.begin(result);
								return;
							}
							tracker.cancel();
						})
						.catch(function () {
							// Refused or failed: the form comes back exactly as it was, and
							// nothing is left watching.
							tracker.cancel();
							dialog.clear_message();
							primary_btn.prop("disabled", false).html(primary_label);
						});
				},
			});

			dialog.show();
		});
	}

	// -----------------------------------------------------------------------
	// Progress (v1.749.0)
	// -----------------------------------------------------------------------

	const PROGRESS_STYLE = `
		<style>
			.ee-pc-text { font-weight: 600; color: var(--heading-color); margin-bottom: 8px; }
			.ee-pc-bar { height: 10px; margin-bottom: 12px; }
			.ee-pc-steps { list-style: none; padding: 0; margin: 0 0 8px; }
			.ee-pc-step { display: flex; align-items: center; gap: 8px; padding: 3px 0; color: var(--text-muted); }
			.ee-pc-step.done { color: var(--text-color); }
			.ee-pc-step.done .ee-pc-mark { color: #16a34a; font-weight: 700; }
			.ee-pc-step.current { color: var(--heading-color); font-weight: 600; }
			.ee-pc-mark { display: inline-flex; justify-content: center; width: 16px; flex: none; }
		</style>
	`;

	// What the bar shows for `state` (the job's ee_project_creation_progress payload): how many
	// of `steps` are finished, never an estimate of time. The steps are not equal in length (the
	// project with its template's tasks, and the Drive folder, are the slow ones), so the text
	// names the step it is on as well as the share done.
	function progress_view(steps, state, reconnecting) {
		state = state || {};
		const total = steps.length;
		const done = Math.max(0, Math.min(total, Number(state.done) || 0));
		const running = state.phase === "running";
		const current = running
			? steps.findIndex(function (s) {
					return s.key === state.step;
				})
			: -1;
		let text = __("Waiting for a background worker...");
		if (running) {
			text =
				current >= 0
					? __("Step {0} of {1}: {2}", [current + 1, total, steps[current].label])
					: __("Working...");
		}
		// The server did not answer the last check (a deploy restarts it): one calm line, the bar as
		// it was, and the next check in QUIET_MS. Never a popup per poll.
		if (reconnecting) text = __("Reconnecting to the server...");
		return {
			percent: total ? Math.round((done * 100) / total) : 0,
			text: text,
			rows: steps.map(function (s, i) {
				return { label: s.label, state: i < done ? "done" : i === current ? "current" : "pending" };
			}),
		};
	}

	function progress_html(view) {
		const esc = frappe.utils.escape_html;
		const rows = view.rows
			.map(function (row) {
				const mark =
					row.state === "done"
						? "✓"
						: row.state === "current"
							? '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>'
							: "";
				return `<li class="ee-pc-step ${row.state}"><span class="ee-pc-mark">${mark}</span><span>${esc(
					row.label
				)}</span></li>`;
			})
			.join("");
		const percent = Number(view.percent) || 0;
		return `${PROGRESS_STYLE}
			<div class="ee-pc">
				<div class="ee-pc-text" aria-live="polite">${esc(view.text)}</div>
				<div class="progress ee-pc-bar">
					<div class="progress-bar progress-bar-striped progress-bar-animated" role="progressbar"
						style="width: ${percent}%" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100"
						aria-label="${esc(__("Project creation progress"))}"></div>
				</div>
				<ol class="ee-pc-steps">${rows}</ol>
				<p class="text-muted small">${esc(
					__("You can close this: the project keeps being made, and this page refreshes when it is done.")
				)}</p>
			</div>`;
	}

	// The job's project_creation_status payload, and the server's recorded state, as one shape.
	function outcome_from_status(data) {
		data = data || {};
		return {
			status: data.status,
			project: data.project || (data.project_doc && data.project_doc.name) || "",
			adopted: !!data.adopted,
			blocked_reason: data.blocked_reason || "",
			reason: data.reason || "",
			error_log: data.error_log || "",
			// The traceback text from the event; the recorded state keeps only that it happened.
			drive_error: data.drive_error || "",
		};
	}

	function outcome_from_state(state) {
		if (state.phase === "finished") return outcome_from_status((state.state && state.state.result) || {});
		// Linked, with nothing recorded about how (the record expired): it has its project.
		if (state.project) return { status: "exists", project: state.project };
		// "lost" or "idle" while something was being watched: the job is gone, and with it
		// anything it would have said. A deploy empties both Redis instances at once.
		return { status: "lost" };
	}

	function project_link(project) {
		return `<a href="/desk/project/${encodeURIComponent(project)}">${frappe.utils.escape_html(project)}</a>`;
	}

	// -----------------------------------------------------------------------
	// The next step: Build out from a planner template (v1.749.0, TASK-2026-02984)
	//
	// Nik kept this dialog's template list to job templates: the Project Planner's phase templates
	// and bundles (Project Template custom_template_kind) would have every task dated on the
	// project's first day if a project were created from them. They go onto a job that exists,
	// through the planner's Build out > Start from template…. So once the Project is made, the
	// success message offers that, and one click opens the new Project's Gantt in the planner with
	// Start from template… open. It is offered only where it would work: to someone whose roles
	// open the planner (boot.page_info), and only when the server says they may build out and a
	// planner template exists (api/planner_templates.planner_template_offer). Otherwise the
	// message is the plain one, with nothing extra.
	// -----------------------------------------------------------------------

	const OFFER_METHOD = "erpnext_enhancements.api.planner_templates.planner_template_offer";
	const BUILD_OUT_ACTION = "ee_build_out_from_template";

	// Resolves true when the link would work for this person, false otherwise (a failed ask included).
	function build_out_offer() {
		const pages = (frappe.boot && frappe.boot.page_info) || {};
		if (!pages["project-planner"]) return Promise.resolve(false);
		return quiet_call(OFFER_METHOD)
			.then(function (r) {
				return !!(r && r.offer);
			})
			.catch(function () {
				return false;
			});
	}

	// set_route with an options object, as the Project form's Open in Project Planner does: on v16 it
	// fills route_options and writes the query, and it is a history entry of its own, so Back comes
	// back here. The planner takes build_out off the address as it lands (ppg_take_build_out), so
	// Back, Forward and a reload of that entry show the Gantt and never open the dialog again.
	function open_planner_build_out(project) {
		frappe.set_route("project-planner", "gantt", project, { project: project, build_out: "template" });
	}

	function announce_created(project, message) {
		build_out_offer().then(function (offer) {
			if (!offer) {
				frappe.show_alert({ message: message, indicator: "green" }, 10);
				return;
			}
			const action = `<a href="#" data-action="${BUILD_OUT_ACTION}">${frappe.utils.escape_html(
				__("Build out from a planner template")
			)}</a>`;
			frappe.show_alert({ message: `${message} ${action}`, indicator: "green" }, 15, {
				[BUILD_OUT_ACTION]: function (e) {
					if (e && e.preventDefault) e.preventDefault();
					open_planner_build_out(project);
				},
			});
		});
	}

	// The record as the server now has it, wherever it is showing. The guided page reloads on
	// sf-record-changed and keeps any edit it has not sent (guided_entry.js). A Desk form with
	// unsaved edits is not reloaded under them: the job saved the opportunity when it linked the
	// project, so a save of those edits would be refused anyway, and the person is asked first.
	function refresh_record(opportunity_name, opts) {
		opts = opts || {};
		if (!opts.form_only) $(document).trigger("sf-record-changed", ["Opportunity", opportunity_name]);
		const route = (frappe.get_route && frappe.get_route()) || [];
		if (!opts.form_only && route[0] === "List" && typeof cur_list !== "undefined" && cur_list && cur_list.refresh) {
			cur_list.refresh(); // the Kanban board / list the prompt was answered on
		}
		const form = [opts.frm, typeof cur_frm !== "undefined" ? cur_frm : null].find(function (f) {
			return f && f.doctype === "Opportunity" && f.doc && f.doc.name === opportunity_name;
		});
		if (!form) return;
		if (!form.is_dirty()) {
			form.reload_doc();
			return;
		}
		// Off screen, its own "changed on the server" notice is waiting for when it comes back.
		if (route[0] !== "Form" || route[2] !== opportunity_name) return;
		frappe.msgprint({
			title: __("Reload to see the project"),
			indicator: "orange",
			message: frappe.utils.escape_html(
				__(
					"The project was made and this opportunity was updated on the server, but this form has unsaved changes. Copy anything you need from it, then reload."
				)
			),
			primary_action: {
				label: __("Reload"),
				action: function () {
					frappe.hide_msgprint();
					form.reload_doc();
				},
			},
		});
	}

	// How a creation ended, said once: the progress dialog says it while it watches, and the
	// surfaces' listeners (opportunity.js, the guided page) when it does not.
	function report(opportunity_name, outcome, opts) {
		opts = opts || {};
		const esc = frappe.utils.escape_html;
		const status = outcome.status;
		if ((status === "success" || status === "exists") && outcome.project) {
			refresh_record(opportunity_name, opts);
			const link = project_link(outcome.project);
			let message = __("Project {0} created.", [link]);
			if (status === "exists") {
				message = outcome.adopted
					? __("Linked project {0}: it already pointed at this opportunity, so no second project was made.", [
							link,
						])
					: __("This opportunity has project {0}.", [link]);
			}
			// A project just made offers the next step; one that was already there does not.
			if (status === "success") announce_created(outcome.project, message);
			else frappe.show_alert({ message: message, indicator: "green" }, 10);
			if (outcome.drive_error) {
				const details =
					typeof outcome.drive_error === "string"
						? `<details><summary>${esc(__("Technical details"))}</summary><pre>${esc(
								outcome.drive_error
							)}</pre></details>`
						: "";
				frappe.msgprint({
					title: __("Google Drive Integration Failed"),
					indicator: "orange",
					message: `<p>${__("Project {0} was created, but its Google Drive folder was not.", [
						link,
					])}</p>${details}<p><a href="/desk/error-log">${esc(__("Check the Error Log"))}</a></p>`,
				});
			}
			if (status === "success" && typeof opts.on_success === "function") opts.on_success(outcome.project);
			return;
		}
		if (status === "blocked") {
			frappe.msgprint({
				title: __("Hand-Off Required"),
				indicator: "orange",
				message: esc(outcome.blocked_reason || ""),
			});
			return;
		}
		// Failed, or lost to a deploy or a worker restart. Nothing is left half-made either way:
		// the job rolls a failure back until the link is committed, and on a retry it links a
		// Project that already points here rather than making a second.
		const lost = status !== "failed";
		let message = lost
			? esc(
					__(
						"The background job stopped before it finished: a deploy or a server restart clears the queue. No project was linked to this opportunity, so it is safe to start again."
					)
				)
			: esc(outcome.reason || __("The project could not be created."));
		if (!lost) {
			const log = outcome.error_log
				? `/desk/error-log/${encodeURIComponent(outcome.error_log)}`
				: "/desk/error-log";
			message += ` <a href="${log}">${esc(__("The Error Log has the details."))}</a>`;
		}
		frappe.msgprint({
			title: lost ? __("Project creation stopped") : __("Project creation failed"),
			indicator: "red",
			message: `<p>${message}</p>`,
			primary_action: {
				label: __("Try again"),
				action: function () {
					frappe.hide_msgprint();
					open_create_project_dialog(opportunity_name, { frm: opts.frm, on_success: opts.on_success });
				},
			},
		});
	}

	// Who reports a creation whose dialog was closed early: the Desk form's listener while that
	// Opportunity's form is loaded (opportunity.js), the guided page while it shows it
	// (guided_entry.js). Anywhere else, the Kanban board or the list, nobody would.
	function surface_reports(opportunity_name) {
		const view = frappe.views && frappe.views.formview && frappe.views.formview.Opportunity;
		if (view && view.frm && view.frm.doc && view.frm.doc.name === opportunity_name) return true;
		const route = (frappe.get_route && frappe.get_route()) || [];
		return route[0] === "guided-entry" && route[1] === "Opportunity" && route[2] === opportunity_name;
	}

	// Closed early: when the outcome arrives, say it here if no surface will, once. A dialog opened
	// again for it (a new tracker) says it instead.
	function report_when_done(opportunity_name, opts) {
		const on_status = function (data) {
			if (!data || data.opportunity_name !== opportunity_name) return;
			frappe.realtime.off(STATUS_EVENT, on_status);
			if (trackers[opportunity_name] || surface_reports(opportunity_name)) return;
			report(opportunity_name, outcome_from_status(data), opts);
		};
		frappe.realtime.on(STATUS_EVENT, on_status);
	}

	/**
	 * Watch one creation: listen for its progress and its outcome, and once `begin` has the
	 * steps, show them in a dialog. Made before the enqueue request goes, so nothing the job
	 * says while the request is in flight is missed (or heard by the surfaces instead).
	 *
	 * When the events go quiet for QUIET_MS it asks project_creation_state, which is how a job a
	 * deploy flushed out of the queue is noticed rather than waited on forever. The ask is quiet
	 * (quiet_call): while the server is away the dialog says "Reconnecting" and asks again.
	 * Closing the dialog stops the watching only; the job carries on, and the surfaces report it
	 * (report_when_done where none does).
	 */
	function make_tracker(opportunity_name, opts) {
		if (trackers[opportunity_name]) return trackers[opportunity_name];
		const t = {
			steps: [],
			state: { phase: "queued", done: 0 },
			heard: false,
			over: false,
			timer: null,
			dialog: null,
			reconnecting: false,
		};

		function paint() {
			if (!t.dialog) return;
			t.dialog.fields_dict.progress.$wrapper.html(progress_html(progress_view(t.steps, t.state, t.reconnecting)));
		}
		function arm() {
			clearTimeout(t.timer);
			t.timer = setTimeout(check, QUIET_MS);
		}
		function check() {
			quiet_call(STATE_METHOD, { opportunity_name: opportunity_name })
				.then(function (state) {
					if (t.over) return;
					if (!state) throw new Error("no answer");
					t.reconnecting = false;
					if (state.phase === "queued" || state.phase === "running") {
						if (state.state) t.state = state.state;
						paint();
						arm();
						return;
					}
					finish(outcome_from_state(state));
				})
				.catch(function () {
					if (t.over) return;
					t.reconnecting = true;
					paint();
					arm();
				});
		}
		function on_progress(data) {
			if (t.over || !data || data.opportunity !== opportunity_name) return;
			t.heard = true;
			t.reconnecting = false;
			t.state = data;
			paint();
			if (t.dialog) arm();
		}
		function on_status(data) {
			if (t.over || !data || data.opportunity_name !== opportunity_name) return;
			finish(outcome_from_status(data));
		}
		function release() {
			clearTimeout(t.timer);
			frappe.realtime.off(PROGRESS_EVENT, on_progress);
			frappe.realtime.off(STATUS_EVENT, on_status);
			if (trackers[opportunity_name] === t) delete trackers[opportunity_name];
		}
		function finish(outcome) {
			t.over = true;
			release();
			if (t.dialog) t.dialog.hide();
			report(opportunity_name, outcome, opts);
		}

		t.begin = function (start) {
			if (t.over) return; // it finished while the request was in flight, and has said so
			start = start || {};
			if (start.steps && start.steps.length) t.steps = start.steps;
			if (!t.heard && start.state) t.state = start.state;
			t.dialog = new frappe.ui.Dialog({
				title: __("Creating Project"),
				fields: [{ fieldtype: "HTML", fieldname: "progress" }],
			});
			t.dialog.onhide = function () {
				if (t.over) return;
				t.over = true;
				release();
				frappe.show_alert({
					message: __("The project is still being created. This page refreshes when it is done."),
					indicator: "blue",
				});
				report_when_done(opportunity_name, opts);
			};
			paint();
			t.dialog.show();
			arm();
		};
		t.show = function () {
			if (t.dialog) t.dialog.show();
		};
		t.cancel = function () {
			if (t.over) return;
			t.over = true;
			release();
			if (t.dialog) t.dialog.hide();
		};

		frappe.realtime.on(PROGRESS_EVENT, on_progress);
		frappe.realtime.on(STATUS_EVENT, on_status);
		trackers[opportunity_name] = t;
		return t;
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
			// The guided entry page (form kit P4.5) reloads the record when it is the one showing.
			$(document).trigger("sf-record-changed", ["Opportunity", opportunity_name]);
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

		// Set from handoff_state before asking: a deal still on a Lead gets a warning above the
		// question (Nik, 2026-10-13), since its Project would take the Lead as its customer.
		let lead_warning = "";

		const ask = function (question, on_yes) {
			const body =
				opts.mode === "transition"
					? `${question}<p class="text-muted" style="margin-top:8px;">${__(
							"No returns this opportunity to its previous status."
						)}</p>`
					: question;
			const message = lead_warning + body;
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
				// No on_success: the progress dialog reloads the record when the project exists
				// (refresh_record), which is when there is something new to show.
				open_create_project_dialog(opportunity_name, { frm: opts.frm });
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
				// A historical deal (pre-ERPNext, untouched for 90 days —
				// crm_enhancements/historical.py) is excluded from hand-off warnings, so
				// opening one asks nothing. A person changing its status un-flags it in
				// that same save, so a fresh transition is never skipped by this.
				if (state && state.is_historical) {
					done();
					return;
				}
				if (state && state.on_lead) {
					lead_warning = `<p class="text-warning" style="margin-bottom:8px;"><b>${__(
						"This deal is still on a Lead."
					)}</b> ${__(
						"Convert the Lead to an Account and set Opportunity From to Account before the hand-off: a Project made from a Lead gets the Lead as its customer, which fails, and no Google Drive folder is made."
					)}</p>`;
				}
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
	// While true, the progress dialog reports and reloads for this opportunity, and the
	// surfaces' own project_creation_status listeners stand aside.
	erpnext_enhancements.crm.is_tracking_project_creation = function (opportunity_name) {
		return !!trackers[opportunity_name];
	};
	// The Desk form's listener (opportunity.js) when nothing is watching: the same words and the
	// same reload rule as the dialog. form_only, because the guided page has its own listener.
	erpnext_enhancements.crm.report_project_creation_status = function (data, frm) {
		if (!data || !data.opportunity_name) return;
		report(data.opportunity_name, outcome_from_status(data), { frm: frm, form_only: true });
	};
	// The guided page's own report (when the dialog is not watching) offers the same next step.
	erpnext_enhancements.crm.build_out_offer = build_out_offer;
	erpnext_enhancements.crm.open_planner_build_out = open_planner_build_out;
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
