/**
 * @file Hand-off process bar on the Project form (PRO-0204 tracker UI).
 * @description
 * Renders the `custom_process_steps` child table as a step progress bar in
 * the `custom_process_progress` HTML field (Hand-Off Process tab):
 *
 *  - ✓ completed steps (with who/when), every *actionable* step highlighted
 *    with its due date and a one-click "Mark Complete" action, upcoming steps
 *    muted, skipped steps struck through.
 *  - **A step's buttons render inside that step's box**, under its due date,
 *    rather than in a shared row beneath the bar. Several steps can be
 *    actionable at once, so the old row had to label every button with its
 *    step title to say which one it advanced; in the box that is self-evident.
 *  - Steps 6 (Outline Tasks) and 7 (Hold Launch Meeting) don't wait on step 5
 *    (Initial Payment From Customer) — payment can take weeks, and neither is
 *    money-dependent — so 5 and 6 (and, once 6 is done, 7) can all be
 *    actionable at once, each with its own highlight and button. Mirrors
 *    `_actionable_steps` in `process_steps.py`; keep the two in sync.
 *  - Overdue actionable steps glow red (matches the Sales Pipeline lights).
 *  - The "Outline Tasks" step shows a soft signal — the project's open task
 *    count — per the meeting: visibility, never a blocker.
 *  - Projects without a tracker (in-flight before v1.3.0, or created without
 *    an Opportunity) get a "Start Hand-Off Process" button — explicit opt-in,
 *    per the meeting decision not to back-fill active projects.
 *
 * All writes are click-driven (collab-safe: nothing here reacts to field
 * changes, so remote live-sync values can't re-fire side effects). Styling
 * is inline in the rendered HTML using Frappe CSS variables, so both Frappe
 * Light and Timeless Night work without a bundle change.
 */

(function () {
	const STYLE = `
		<style>
			.ee-process-bar { display: flex; flex-wrap: wrap; gap: 8px; padding: 8px 0; }
			.ee-process-step {
				flex: 1 1 110px; min-width: 110px; border: 1px solid var(--border-color);
				border-radius: 8px; padding: 8px 10px; background: var(--fg-color);
			}
			.ee-process-step .ee-step-no {
				display: inline-flex; align-items: center; justify-content: center;
				width: 20px; height: 20px; border-radius: 50%; font-size: 11px; font-weight: 700;
				background: var(--bg-color); border: 1px solid var(--border-color); color: var(--text-muted);
			}
			.ee-process-step .ee-step-title { font-size: 12px; font-weight: 600; color: var(--heading-color); margin-top: 4px; }
			.ee-process-step .ee-step-meta { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
			.ee-process-step.done { opacity: 0.85; }
			.ee-process-step.done .ee-step-no { background: #16a34a; border-color: #16a34a; color: #fff; }
			.ee-process-step.skipped .ee-step-title { text-decoration: line-through; }
			.ee-process-step.current { border-color: var(--primary, #2490ef); box-shadow: 0 0 0 1px var(--primary, #2490ef); }
			.ee-process-step.current.overdue { border-color: #dc2626; box-shadow: 0 0 0 1px #dc2626, 0 0 8px rgba(220, 38, 38, 0.4); }
			.ee-process-step.current .ee-step-no { background: var(--primary, #2490ef); border-color: var(--primary, #2490ef); color: #fff; }
			.ee-process-step.current.overdue .ee-step-no { background: #dc2626; border-color: #dc2626; }
			/* A step's buttons live in the step's own box. Wider basis so the
			   labels fit, stacked and full-width so they stay readable when the
			   bar is squeezed down to seven cards on a laptop. */
			.ee-process-step.has-actions { flex-basis: 150px; min-width: 132px; }
			.ee-process-step .ee-step-actions { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; }
			.ee-process-step .ee-step-actions .btn { width: 100%; white-space: normal; }
		</style>
	`;

	// -----------------------------------------------------------------------
	// Inline step actions.
	//
	// The 2026-08-06 meeting's central complaint: the steps were pure
	// record-keeping, a row you tick, with no help doing the work — and manual
	// steps completed 5% of the time against 100% for the automated ones. So the
	// current step gets a button that starts the actual work, next to the one
	// that records it. Keyed by step_number, because the titles are editable
	// site-side on Process Step Template but the numbering is the process.
	// -----------------------------------------------------------------------

	const STEP_INVOICE = 4;
	const STEP_TASKS = 6;
	const STEP_LAUNCH = 7;
	const STEP_PAYMENT = 5;

	/**
	 * Every Pending step whose predecessors are all resolved — not just the
	 * single lowest-numbered one. Mirrors `_actionable_steps` in
	 * process_steps.py: a step blocks later ones only while it is itself
	 * Pending AND isn't STEP_PAYMENT, so 6 and 7 don't stall behind payment
	 * while 6 still has to finish before 7 opens up.
	 */
	function actionable_steps(steps) {
		const blocking_pending = new Set(
			steps
				.filter((s) => s.status === "Pending" && Number(s.step_number) !== STEP_PAYMENT)
				.map((s) => Number(s.step_number))
		);
		return steps.filter((s) => {
			if (s.status !== "Pending") return false;
			for (const n of blocking_pending) {
				if (n < Number(s.step_number)) return false;
			}
			return true;
		});
	}

	function open_billing_email(frm) {
		// The "hack period" flow: ERPNext is not the accounting system yet, so
		// the compliant action is still a note to Billing, not an invoice. The
		// server-side `handoff_invoice_flow` setting decides which of these two
		// this button is; see erpnext_enhancements_settings.json.
		frappe
			.xcall("erpnext_enhancements.crm_enhancements.handoff.resolve_attendees", {
				opportunity: frm.doc.custom_opportunity || null,
			})
			.then((attendees) => {
				new frappe.views.CommunicationComposer({
					doc: frm.doc,
					frm: frm,
					subject: __("Accounting project & invoice — {0}", [frm.doc.project_name || frm.doc.name]),
					recipients: ((attendees && attendees.Billing) || []).join(", "),
					message: __(
						"Please set up the accounting project and send the invoice for {0} ({1}).",
						[frm.doc.project_name || frm.doc.name, frm.doc.customer || ""]
					),
				});
			});
	}

	function open_draft_invoice(frm) {
		frappe.new_doc("Sales Invoice", {
			customer: frm.doc.customer,
			project: frm.doc.name,
		});
	}

	function open_tasks(frm) {
		// Jump to what already exists rather than always creating: the step is
		// "outline tasks", and someone part-way through needs the list, not a
		// blank Task. A project with nothing on it gets the new-Task form.
		frappe.db
			.count("Task", { filters: { project: frm.doc.name } })
			.then((n) => {
				if (n) {
					frappe.set_route("List", "Task", { project: frm.doc.name });
				} else {
					frappe.new_doc("Task", { project: frm.doc.name });
				}
			});
	}

	function open_launch_meeting(frm) {
		frappe
			.xcall("erpnext_enhancements.crm_enhancements.handoff.project_meeting_attendees", {
				project: frm.doc.name,
			})
			.then((attendees) => {
				erpnext_enhancements.handoff_meeting_dialog.open({
					title: __("Hold Project Launch Meeting"),
					description: __(
						"The project team is invited by email and the meeting is added to the calendar."
					),
					attendees: attendees,
					on_submit(values) {
						return frappe
							.xcall(
								"erpnext_enhancements.crm_enhancements.handoff.schedule_project_meeting",
								{
									project: frm.doc.name,
									starts_on: values.starts_on,
									duration_minutes: values.duration_minutes,
									attendees: values.attendees,
								}
							)
							.then((result) => {
								frappe.show_alert({
									message: result.invited
										? __("Launch meeting created and invites sent.")
										: __("Meeting created, but the invite email failed — check the Error Log."),
									indicator: result.invited ? "green" : "orange",
								});
								frm.reload_doc();
							});
					},
				});
			});
	}

	const STEP_ACTIONS = {
		[STEP_INVOICE]: {
			label: "Send to Billing",
			run(frm) {
				const ctx = frm.__handoff_ctx || {};
				if (ctx.invoice_flow === "Draft Sales Invoice") {
					open_draft_invoice(frm);
				} else {
					open_billing_email(frm);
				}
			},
		},
		[STEP_TASKS]: { label: "Open Tasks", run: open_tasks },
		[STEP_LAUNCH]: { label: "Schedule Launch Meeting", run: open_launch_meeting },
		[STEP_PAYMENT]: { label: "Set Follow-Up Reminder", run: open_payment_followup },
	};

	/**
	 * Schedule the next chase on an unpaid initial payment.
	 *
	 * Payment is the one step with no due date and no escalation, deliberately —
	 * the customer's cheque is not ours to hurry, and nagging about it only
	 * teaches people to filter the emails. That left it the only actionable step
	 * with nothing to *do*. This is the replacement: not a clock, a manual chain.
	 * Each reminder that fires brings you back here to set the next one.
	 *
	 * The date and the recipient both come from the server — business-day maths
	 * honours the hand-off Holiday List, and the reminder is addressed to the
	 * Finance & Accounting Manager for this project, who is usually not whoever
	 * is clicking. Defaulting either of them client-side would let the two drift.
	 */
	function open_payment_followup(frm) {
		frappe
			.xcall("erpnext_enhancements.process_steps.get_payment_followup", {
				project: frm.doc.name,
			})
			.then((info) => {
				const d = new frappe.ui.Dialog({
					title: __("Follow Up On Initial Payment"),
					fields: [
						{
							fieldtype: "HTML",
							fieldname: "who",
							options: `<p class="text-muted" style="margin-bottom:8px;">${
								info.employee_name
									? __("Reminds {0}.", [frappe.utils.escape_html(info.employee_name)])
									: __("No Finance & Accounting Manager is configured.")
							}${
								info.remind_at
									? " " +
										__("Replaces the reminder currently set for {0}.", [
											frappe.datetime.str_to_user(info.remind_at),
										])
									: ""
							}</p>`,
						},
						{
							label: __("Remind At"),
							fieldname: "remind_at",
							fieldtype: "Datetime",
							reqd: 1,
							default: info.default_remind_at,
							description: __("{0} business days out by default, skipping weekends and holidays.", [
								info.business_days,
							]),
						},
					],
					primary_action_label: __("Set Reminder"),
					primary_action(values) {
						frappe
							.xcall("erpnext_enhancements.process_steps.schedule_payment_followup", {
								project: frm.doc.name,
								remind_at: values.remind_at,
							})
							.then((res) => {
								d.hide();
								frappe.show_alert({
									message: __("{0} will be reminded on {1}", [
										res.employee_name,
										frappe.datetime.str_to_user(res.remind_at),
									]),
									indicator: "green",
								});
								render(frm);
							});
					},
				});
				d.show();
			});
	}

	/**
	 * The buttons for one actionable step, rendered inside its own box: the
	 * action that starts the work (where the step has one) and the one that
	 * records it. "Mark Complete" rather than "Mark Step N Complete" — the
	 * number is in the same box, an arm's length above the button.
	 */
	function step_actions_html(step) {
		const esc = frappe.utils.escape_html;
		const action = STEP_ACTIONS[step.step_number];
		return `
			<div class="ee-step-actions">
				${
					action
						? `<button class="btn btn-xs btn-default ee-step-action" data-step="${esc(step.name)}">${__(action.label)}</button>`
						: ""
				}
				<button class="btn btn-xs btn-primary ee-complete-step" data-step="${esc(step.name)}">
					${__("Mark Complete")}
				</button>
			</div>
		`;
	}

	/**
	 * The 7-business-day Closed-Won -> Launch goal, shown beside step 7.
	 *
	 * Deliberately separate from the step's own due date: a step can be on time
	 * against the step before it and still blow the launch goal, because the goal
	 * measures the whole chain. The meeting asked for both to be visible.
	 */
	function launch_deadline_block(frm, steps) {
		const launch = steps.find((s) => Number(s.step_number) === STEP_LAUNCH);
		const deadline = (frm.__handoff_ctx || {}).launch_deadline;
		if (!launch || !deadline) return "";
		const done = launch.status === "Completed";
		const missed = done
			? launch.completed_on && launch.completed_on > deadline
			: frappe.datetime.now_datetime() > deadline;
		const label = done
			? missed
				? __("Launch goal MISSED")
				: __("Launch goal met")
			: missed
				? __("Launch goal MISSED")
				: __("Launch goal");
		return `
			<div class="ee-step-meta" style="margin-top:6px; ${missed ? "color: #dc2626; font-weight:600;" : ""}">
				${label}: ${__("launch within 7 business days of Closed Won")} —
				${frappe.datetime.str_to_user(deadline)}
			</div>`;
	}

	function render(frm) {
		const field = frm.get_field("custom_process_progress");
		if (!field || !field.$wrapper) return;
		// master switch: hide the hand-off UI entirely while the suite is
		// dormant (ERPNext Enhancements Settings; server guards are authority)
		if (!frappe.boot.ee_process_automation) {
			field.$wrapper.html("");
			return;
		}
		const esc = frappe.utils.escape_html;
		const steps = (frm.doc.custom_process_steps || [])
			.slice()
			.sort((a, b) => (a.step_number || 0) - (b.step_number || 0));

		if (!steps.length) {
			if (frm.is_new()) {
				field.$wrapper.html("");
				return;
			}
			field.$wrapper.html(`
				<div class="text-muted" style="padding: 6px 0;">
					${__("No hand-off process on this project.")}
					<button class="btn btn-xs btn-default ee-start-process" style="margin-left:8px;">
						${__("Start Hand-Off Process")}
					</button>
				</div>
			`);
			field.$wrapper.find(".ee-start-process").on("click", () => {
				frappe
					.call("erpnext_enhancements.process_steps.start_process", { project: frm.doc.name })
					.then(() => frm.reload_doc());
			});
			return;
		}

		// Plural now: step 5 (payment) doesn't block 6/7, so several steps can
		// be actionable at the same time. See actionable_steps() above.
		const actionable = actionable_steps(steps);
		const actionable_names = new Set(actionable.map((s) => s.name));
		const done = steps.filter((s) => s.status === "Completed").length;
		const now = frappe.datetime.now_datetime();

		let html = `${STYLE}<div class="ee-process-bar">`;
		steps.forEach((step) => {
			const is_actionable = actionable_names.has(step.name);
			const overdue = is_actionable && step.due_by && step.due_by < now;
			const cls =
				step.status === "Completed"
					? "done"
					: step.status === "Skipped"
						? "skipped"
						: is_actionable
							? `current${overdue ? " overdue" : ""}`
							: "";
			let meta = esc(step.responsible_role || "");
			if (step.status === "Completed" && step.completed_on) {
				meta = `${__("Done")} ${frappe.datetime.str_to_user(step.completed_on)}`;
			} else if (is_actionable && step.due_by) {
				meta = `${esc(step.responsible_role || "")} · ${overdue ? __("OVERDUE") : __("due")} ${frappe.datetime.str_to_user(step.due_by)}`;
			}
			// The buttons for a step go inside that step's box — several steps can
			// be actionable at once (5 and 6, then 7), and a shared row underneath
			// had to re-print the step title on every button just to say which box
			// it belonged to. In the box, the box says it.
			const actions = is_actionable ? step_actions_html(step) : "";
			html += `
				<div class="ee-process-step ${cls}${actions ? " has-actions" : ""}" data-step="${esc(step.name)}">
					<span class="ee-step-no">${step.status === "Completed" ? "✓" : esc(String(step.step_number || ""))}</span>
					<div class="ee-step-title">${esc(step.step_title || "")}</div>
					<div class="ee-step-meta">${meta}</div>
					<div class="ee-step-extra" data-extra="${esc(step.name)}"></div>
					${actions}
				</div>
			`;
		});
		html += "</div>";

		if (actionable.length) {
			html += `
				<div class="text-muted" style="font-size:11px; margin-top:4px;">
					${__("{0} of {1} done", [done, steps.length])}
				</div>
			`;
		}

		const launch_deadline_html = launch_deadline_block(frm, steps);
		if (launch_deadline_html) html += launch_deadline_html;

		field.$wrapper.html(html);

		// Completion goes through a whitelisted server method, NOT
		// frappe.model.set_value + save. The 2026-08-06 audit found
		// retroactive box-checking, and the old client path let the browser
		// propose completed_on; the server now stamps it from its own clock
		// and checks the responsible role. data-step disambiguates which of
		// possibly several actionable steps a click belongs to.
		field.$wrapper.find(".ee-complete-step").on("click", function () {
			const step_name = $(this).data("step");
			frappe
				.xcall("erpnext_enhancements.process_steps.complete_step", {
					project: frm.doc.name,
					step_name: step_name,
				})
				.then(() => frm.reload_doc());
		});

		field.$wrapper.find(".ee-step-action").on("click", function () {
			const step_name = $(this).data("step");
			const step = actionable.find((s) => s.name === step_name);
			const action = step && STEP_ACTIONS[step.step_number];
			if (action) action.run(frm, step);
		});

		// Soft signal on the payment step: when the next chase is set. Display
		// only, and best-effort — the step has no due date of its own, so this
		// is the only place the follow-up date is visible.
		const payment_step = steps.find((s) => Number(s.step_number) === STEP_PAYMENT);
		if (payment_step && payment_step.status === "Pending" && !frm.is_new()) {
			frappe
				.xcall("erpnext_enhancements.process_steps.get_payment_followup", {
					project: frm.doc.name,
				})
				.then((info) => {
					if (!info || !info.remind_at) return;
					field.$wrapper
						.find(`[data-extra="${CSS.escape(payment_step.name)}"]`)
						.html(
							`<span class="text-muted" style="font-size:11px;">${__("next follow-up {0}", [
								frappe.datetime.str_to_user(info.remind_at),
							])}</span>`
						);
				})
				.catch(() => {
					// Decoration only — never blank the bar over it.
				});
		}

		// Soft signal on the task-outlining step: open task count (display only).
		const tasks_step = steps.find((s) => /task/i.test(s.step_title || ""));
		if (tasks_step && !frm.is_new()) {
			frappe.db
				.count("Task", {
					filters: { project: frm.doc.name, status: ["not in", ["Completed", "Cancelled"]] },
				})
				.then((n) => {
					field.$wrapper
						.find(`[data-extra="${CSS.escape(tasks_step.name)}"]`)
						.html(`<span class="text-muted" style="font-size:11px;">${__("{0} open tasks", [n])}</span>`);
				});
		}
	}

	frappe.ui.form.on("Project", {
		refresh(frm) {
			// Paint immediately from the child rows we already have, then repaint
			// once the launch deadline and invoice flow arrive. The bar is the
			// primary content of this tab; making it wait on a round trip would
			// show an empty panel on every load for data that only decorates it.
			render(frm);
			if (frm.is_new() || !frappe.boot.ee_process_automation) return;
			frappe
				.xcall("erpnext_enhancements.crm_enhancements.handoff.project_handoff_context", {
					project: frm.doc.name,
				})
				.then((ctx) => {
					frm.__handoff_ctx = ctx || {};
					render(frm);
				})
				.catch(() => {
					// Decoration only — a failed context call must not blank the bar.
					frm.__handoff_ctx = {};
				});
		},
	});
})();
