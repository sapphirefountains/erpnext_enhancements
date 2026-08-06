/**
 * @file Hand-off gate and process preview on the Opportunity form (PRO-0204).
 * @description
 * Shows the first three hand-off steps — Mark Opportunity as Won, Hold Hand-Off
 * Meeting, Create Project — in the Opportunity's "Hand-Off Process" tab
 * (`custom_process_progress` HTML field), and adds the buttons that *do* step 2.
 *
 * As of the 2026-08-06 process meeting, step 2 happens HERE, before the project
 * exists: "Hold Hand-Off Meeting" books a real calendar Event and emails the
 * invite, then becomes "Mark Hand-Off Complete", and that recording is what
 * unlocks project creation. A System Manager can skip with a written reason.
 * The buttons are convenience only — `process_steps.enforce_handoff_gate` on
 * Project `before_insert` is the enforcement, because the audit found 8 of 28
 * projects created through paths no button controls.
 *
 * Steps 4-7 continue on the Project once it exists.
 *
 * Read-only and derived (collab-safe — nothing here reacts to field changes):
 *  - With a linked Project (`custom_created_project`) whose hand-off tracker was
 *    started, the three rows mirror that Project's actual step statuses (steps
 *    1-3) via a whitelisted server call.
 *  - With a linked Project that has NO steps yet (in-flight projects aren't
 *    auto-seeded — they opt in on the Project via "Start Hand-Off Process"), the
 *    rows fall back to a project-aware derived view (Create Project reads done,
 *    the meeting is the live step) plus a pointer to the project. This keeps the
 *    tab from rendering blank, which was the pre-fix behavior for such records.
 *  - Without a linked Project, they're derived from the Opportunity: "Mark Won"
 *    completes when the status is Closed Won; the others are upcoming.
 *
 * Styling matches the Project's hand-off bar (Frappe CSS vars; Light + Night).
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
			.ee-process-step.current .ee-step-no { background: var(--primary, #2490ef); border-color: var(--primary, #2490ef); color: #fff; }
		</style>
	`;

	function step_html(s) {
		const esc = frappe.utils.escape_html;
		const cls =
			s.status === "Completed"
				? "done"
				: s.status === "Skipped"
					? "skipped"
					: s.current
						? "current"
						: "";
		return `
			<div class="ee-process-step ${cls}">
				<span class="ee-step-no">${s.status === "Completed" ? "✓" : esc(String(s.no))}</span>
				<div class="ee-step-title">${esc(s.title)}</div>
				<div class="ee-step-meta">${esc(s.meta || "")}</div>
			</div>`;
	}

	function paint(field, steps, footer) {
		let html = `${STYLE}<div class="ee-process-bar">`;
		steps.forEach((s) => (html += step_html(s)));
		html += "</div>";
		if (footer) html += footer;
		field.$wrapper.html(html);
	}

	// The three opportunity->project steps derived from the Opportunity itself.
	// `project_created` is true when a Project already exists but its hand-off
	// tracker was never started (in-flight projects aren't auto-seeded — see
	// process_steps.py), so "Create Project" reads as done and the meeting is the
	// live step. When false (no project yet) this is the original pre-project view.
	function derived_steps(frm, project_created) {
		const won = frm.doc.status === "Closed Won";
		const won_meta = won
			? frm.doc.custom_date_closed_won
				? `${__("Done")} ${frappe.datetime.str_to_user(frm.doc.custom_date_closed_won)}`
				: __("Done")
			: __("Mark the opportunity Closed Won");

		// Step 2 is recorded HERE now, ahead of the project (2026-08-06 meeting),
		// so it reads from the Opportunity's own hand-off fields rather than
		// waiting for a project row to exist.
		const held = !!frm.doc.custom_handoff_meeting_held;
		const overdue =
			won &&
			!held &&
			frm.doc.custom_handoff_due_by &&
			frm.doc.custom_handoff_due_by < frappe.datetime.now_datetime();
		let handoff_meta;
		if (held) {
			handoff_meta = frm.doc.custom_handoff_skip_reason
				? __("Skipped")
				: frm.doc.custom_handoff_meeting_on
					? `${__("Done")} ${frappe.datetime.str_to_user(frm.doc.custom_handoff_meeting_on)}`
					: __("Done");
		} else if (!won) {
			handoff_meta = __("After the opportunity is won");
		} else if (frm.doc.custom_handoff_due_by) {
			handoff_meta = `${overdue ? __("OVERDUE") : __("due")} ${frappe.datetime.str_to_user(
				frm.doc.custom_handoff_due_by
			)}`;
		} else {
			handoff_meta = __("Hold the hand-off meeting");
		}

		return [
			{ no: 1, title: __("Mark Opportunity as Won"), status: won ? "Completed" : "Pending", current: !won, meta: won_meta },
			{
				no: 2,
				title: __("Hold Hand-Off Meeting"),
				status: held ? (frm.doc.custom_handoff_skip_reason ? "Skipped" : "Completed") : "Pending",
				current: won && !held,
				meta: handoff_meta,
			},
			{
				no: 3,
				title: __("Create Project in PM System"),
				status: project_created ? "Completed" : "Pending",
				// Blocked until the hand-off is recorded — the gate, shown rather
				// than merely enforced, so the order is visible before somebody
				// hits a server error.
				current: won && held && !project_created,
				meta: project_created
					? __("Done")
					: won && !held
						? __("Blocked until the hand-off is recorded")
						: won
							? __("Use the Create project prompt")
							: "",
			},
		];
	}

	function render(frm) {
		const field = frm.get_field("custom_process_progress");
		if (!field || !field.$wrapper) return;
		// Master switch (server guards are authority): hide while dormant.
		if (!frappe.boot.ee_process_automation || frm.is_new()) {
			field.$wrapper.html("");
			return;
		}

		if (frm.doc.custom_created_project) {
			// Mirror the linked Project's first three steps (live statuses).
			frappe
				.xcall(
					"erpnext_enhancements.crm_enhancements.project_prompt.opportunity_handoff_steps",
					{ opportunity_name: frm.doc.name }
				)
				.then((data) => {
					const rows = (data && data.steps) || [];
					const proj = (data && data.project) || frm.doc.custom_created_project;
					if (!rows.length) {
						// Project exists but its hand-off tracker was never started
						// (in-flight projects opt in on the Project via "Start Hand-Off
						// Process"). Show the project-aware derived view + a pointer,
						// so the tab is never blank.
						const footer = `<div class="ee-step-meta" style="margin-top:6px;">
							${__("Detailed hand-off tracker not started on")} <a href="/app/project/${encodeURIComponent(proj)}">${frappe.utils.escape_html(proj)}</a>
						</div>`;
						paint(field, derived_steps(frm, true), footer);
						return;
					}
					const current = rows.find((s) => s.status === "Pending");
					const steps = rows.map((s) => ({
						no: s.step_number,
						title: s.step_title,
						status: s.status,
						current: !!current && s.step_number === current.step_number,
						meta:
							s.status === "Completed" && s.completed_on
								? `${__("Done")} ${frappe.datetime.str_to_user(s.completed_on)}`
								: s.responsible_role || "",
					}));
					const footer = `<div class="ee-step-meta" style="margin-top:6px;">
						${__("Full hand-off continues on")} <a href="/app/project/${encodeURIComponent(proj)}">${frappe.utils.escape_html(proj)}</a>
					</div>`;
					paint(field, steps, footer);
				})
				.catch(() => {
					// Never leave the tab blank on a transient call failure.
					paint(field, derived_steps(frm, true));
				});
			return;
		}

		// No project yet — derive the three steps from the Opportunity.
		paint(field, derived_steps(frm, false));
	}

	// -----------------------------------------------------------------------
	// The hand-off gate: buttons that DO step 2, on the Opportunity.
	//
	// Per the 2026-08-06 meeting the hand-off must happen before a Project can
	// exist, so the action lives here rather than on a project that does not yet
	// exist. These buttons are convenience and signposting only — the server
	// enforces the gate on Project before_insert, so hiding a button can never be
	// the thing that stops a non-compliant project.
	// -----------------------------------------------------------------------

	function schedule_meeting(frm, state) {
		frappe
			.xcall("erpnext_enhancements.crm_enhancements.handoff.resolve_attendees", {
				opportunity: frm.doc.name,
			})
			.then((attendees) => {
				erpnext_enhancements.handoff_meeting_dialog.open({
					title: __("Hold Hand-Off Meeting"),
					description: __(
						"Sales, Production and Billing are invited by email and the meeting is added to the calendar. Recording the meeting afterwards is what unlocks project creation."
					),
					attendees: attendees,
					on_submit(values) {
						return frappe
							.xcall(
								"erpnext_enhancements.crm_enhancements.handoff.schedule_handoff_meeting",
								{
									opportunity: frm.doc.name,
									starts_on: values.starts_on,
									duration_minutes: values.duration_minutes,
									attendees: values.attendees,
								}
							)
							.then((result) => {
								frappe.show_alert({
									message: result.invited
										? __("Meeting created and invites sent.")
										: __("Meeting created, but the invite email failed — check the Error Log."),
									indicator: result.invited ? "green" : "orange",
								});
								frm.reload_doc();
							});
					},
				});
			});
	}

	function mark_complete(frm) {
		frappe
			.xcall("erpnext_enhancements.crm_enhancements.handoff.mark_handoff_complete", {
				opportunity: frm.doc.name,
			})
			.then(() => {
				frappe.show_alert({
					message: __("Hand-off recorded. You can now create the project."),
					indicator: "green",
				});
				frm.reload_doc();
			});
	}

	function skip_handoff(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Skip Hand-Off"),
			fields: [
				{
					fieldtype: "HTML",
					options: `<p class="text-muted">${__(
						"Skipping is allowed, but never silent: the reason is stored on the record and shows in the hand-off tracker and the compliance report."
					)}</p>`,
				},
				{
					fieldname: "reason",
					label: __("Reason"),
					fieldtype: "Small Text",
					reqd: 1,
				},
			],
			primary_action_label: __("Skip and Record Reason"),
			primary_action(values) {
				frappe
					.xcall("erpnext_enhancements.crm_enhancements.handoff.skip_handoff", {
						opportunity: frm.doc.name,
						reason: values.reason,
					})
					.then(() => {
						dialog.hide();
						frappe.show_alert({ message: __("Hand-off skipped and logged."), indicator: "orange" });
						frm.reload_doc();
					});
			},
		});
		dialog.show();
	}

	function add_buttons(frm) {
		if (frm.is_new() || !frappe.boot.ee_process_automation) return;

		frappe
			.xcall("erpnext_enhancements.crm_enhancements.handoff.handoff_state", {
				opportunity: frm.doc.name,
			})
			.then((state) => {
				if (!state || !state.available || !state.enabled) return;
				if (!state.is_won || !state.gate_applies) return;

				if (state.held) {
					// Already recorded — say so where the user is looking, rather
					// than leaving them to find a read-only field on a tab.
					const when = state.held_on
						? frappe.datetime.str_to_user(state.held_on)
						: __("earlier");
					frm.dashboard.add_comment(
						state.skip_reason
							? __("Hand-off SKIPPED {0} by {1}: {2}", [when, state.held_by || "", state.skip_reason])
							: __("Hand-off recorded {0} by {1}.", [when, state.held_by || ""]),
						state.skip_reason ? "orange" : "green",
						true
					);
					return;
				}

				// The gate is closed. Say what to do, in the words the server uses.
				frm.dashboard.add_comment(
					state.overdue
						? __("Hand-off meeting is OVERDUE (due {0}). {1}", [
								frappe.datetime.str_to_user(state.due_by),
								state.gate_message,
							])
						: state.gate_message,
					state.overdue ? "red" : "orange",
					true
				);

				frm.add_custom_button(
					state.event ? __("Mark Hand-Off Complete") : __("Hold Hand-Off Meeting"),
					() => (state.event ? mark_complete(frm) : schedule_meeting(frm, state))
				).addClass("btn-primary");

				// Once a meeting is booked, scheduling another is still legitimate
				// (it got moved) — just no longer the primary action.
				if (state.event) {
					frm.add_custom_button(__("Re-schedule Meeting"), () => schedule_meeting(frm, state), __("Hand-Off"));
				}
				if (state.can_skip) {
					frm.add_custom_button(
						__("Skip hand-off (requires reason)"),
						() => skip_handoff(frm),
						__("Hand-Off")
					);
				}
			});
	}

	frappe.ui.form.on("Opportunity", {
		refresh(frm) {
			render(frm);
			add_buttons(frm);
		},
	});
})();
