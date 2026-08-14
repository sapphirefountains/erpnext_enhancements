/**
 * @file Hand-off gate and process preview on the Opportunity form (PRO-0204).
 * @description
 * Shows the first three hand-off steps — Mark Opportunity as Won, Hold Hand-Off
 * Meeting, Create Project — in the Opportunity's "Hand-Off Process" tab
 * (`custom_process_progress` HTML field), together with the buttons that walk
 * through them.
 *
 * As of the 2026-08-06 process meeting, step 2 happens HERE, before the project
 * exists. As of v1.263.0 the order inside the tab is:
 *
 *   Closed Won  ->  [Schedule Hand-Off Meeting]
 *   booked      ->  [Create Project in PM System]  [Hand-Off Meeting Finished]
 *
 * Booking the meeting is what opens the gate; "Hand-Off Meeting Finished" closes
 * step 2 and never blocks the project — a meeting two days out should not hold up
 * a project that starts today, and making it do so was teaching people to tick
 * "held" for meetings still in the diary. See `crm_enhancements/handoff.py`.
 *
 * **The buttons live in the tab, not the toolbar** — they are the tab's content,
 * rather than five entries in a form menu shared with every other thing an
 * Opportunity can do. **And inside the tab they live in the step's own box**, not
 * in a row underneath it: the meeting buttons sit in step 2, "Create Project" in
 * step 3, so the button and the state it acts on are read together. Buttons are
 * mapped to steps by number (`STEP_MEETING` / `STEP_PROJECT`), which is what lets
 * the mapping survive the bar switching to the Project's real rows.
 *
 * They are convenience only — `process_steps.enforce_handoff_gate` on Project
 * `before_insert` is the enforcement, because the audit found 8 of 28 projects
 * created through paths no button controls.
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
			.ee-process-status { font-size: 12px; margin-top: 10px; }
			.ee-process-status.green { color: #16a34a; }
			.ee-process-status.orange { color: #d97706; }
			.ee-process-status.red { color: #dc2626; font-weight: 600; }
			/* A step's buttons live in the step's own box. Wider basis so the
			   labels fit, stacked and full-width so they stay readable when the
			   bar is squeezed. */
			.ee-process-step.has-actions { flex-basis: 170px; min-width: 150px; }
			.ee-process-step .ee-step-actions { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; }
			.ee-process-step .ee-step-actions .btn { width: 100%; white-space: normal; }
			.ee-process-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
		</style>
	`;

	function step_html(s, buttons) {
		const esc = frappe.utils.escape_html;
		const cls =
			s.status === "Completed"
				? "done"
				: s.status === "Skipped"
					? "skipped"
					: s.current
						? "current"
						: "";
		const actions =
			buttons && buttons.length ? `<div class="ee-step-actions">${buttons.join("")}</div>` : "";
		return `
			<div class="ee-process-step ${cls}${actions ? " has-actions" : ""}">
				<span class="ee-step-no">${s.status === "Completed" ? "✓" : esc(String(s.no))}</span>
				<div class="ee-step-title">${esc(s.title)}</div>
				<div class="ee-step-meta">${esc(s.meta || "")}</div>
				${actions}
			</div>`;
	}

	function paint(field, steps, by_step, footer, status) {
		const placed = new Set();
		let html = `${STYLE}<div class="ee-process-bar">`;
		steps.forEach((s) => {
			const buttons = by_step[Number(s.no)] || [];
			if (buttons.length) placed.add(Number(s.no));
			html += step_html(s, buttons);
		});
		html += "</div>";
		if (footer) html += footer;
		if (status) html += status;
		// A button whose step isn't among the rendered rows still has to appear —
		// the rows can come from the Project's own tracker, which this file does
		// not control. Losing the button would leave the step with no way to
		// advance it at all, so it falls back to a row beneath the bar.
		const orphans = Object.keys(by_step)
			.filter((no) => !placed.has(Number(no)))
			.reduce((all, no) => all.concat(by_step[no]), []);
		if (orphans.length) html += `<div class="ee-process-actions">${orphans.join("")}</div>`;
		field.$wrapper.html(html);
	}

	function when(value) {
		return value ? frappe.datetime.str_to_user(value) : "";
	}

	// -----------------------------------------------------------------------
	// One view of the truth
	//
	// `handoff_state` is authoritative (it is the server's own gate predicate),
	// but it is a round trip that can fail, and a tab that renders nothing on a
	// dropped request is the bug this file already had once. So every fact falls
	// back to the document the form is holding.
	// -----------------------------------------------------------------------

	function facts(frm, state) {
		const s = state || {};
		const known = !!s.available;
		const held = !!(known ? s.held : frm.doc.custom_handoff_meeting_held);
		const due_by = (known ? s.due_by : frm.doc.custom_handoff_due_by) || "";
		const won = frm.doc.status === "Closed Won";
		return {
			won: won,
			held: held,
			skip_reason: (known ? s.skip_reason : frm.doc.custom_handoff_skip_reason) || "",
			held_on: (known ? s.held_on : frm.doc.custom_handoff_meeting_on) || "",
			held_by: s.held_by || "",
			scheduled: !!(known ? s.event : frm.doc.custom_handoff_event),
			event_on: s.event_on || "",
			due_by: due_by,
			overdue: known
				? !!s.overdue
				: won && !held && !!due_by && due_by < frappe.datetime.now_datetime(),
			// Unknown state is treated as "the gate applies and is closed": the
			// pessimistic reading only ever hides a button, and the server refuses
			// anything the hidden button would have started anyway.
			gate_applies: known ? !!(s.gate_applies && s.enabled) : true,
			gate_open: known ? !!s.gate_open : false,
			gate_message: s.gate_message || __("Schedule the Hand-Off Meeting on the Opportunity first."),
			can_skip: !!s.can_skip,
			project: (known ? s.project : frm.doc.custom_created_project) || "",
		};
	}

	// The three opportunity->project steps derived from the Opportunity itself.
	// `project_created` is true when a Project already exists but its hand-off
	// tracker was never started (in-flight projects aren't auto-seeded — see
	// process_steps.py), so "Create Project" reads as done and the meeting is the
	// live step. When false (no project yet) this is the original pre-project view.
	function derived_steps(frm, f, project_created) {
		const won_meta = f.won
			? frm.doc.custom_date_closed_won
				? `${__("Done")} ${when(frm.doc.custom_date_closed_won)}`
				: __("Done")
			: __("Mark the opportunity Closed Won");

		// Step 2 is recorded HERE now, ahead of the project (2026-08-06 meeting),
		// so it reads from the Opportunity's own hand-off fields rather than
		// waiting for a project row to exist.
		let handoff_meta;
		if (f.held) {
			handoff_meta = f.skip_reason
				? __("Skipped")
				: f.held_on
					? `${__("Done")} ${when(f.held_on)}`
					: __("Done");
		} else if (!f.won) {
			handoff_meta = __("After the opportunity is won");
		} else if (f.scheduled) {
			handoff_meta = f.event_on ? `${__("Booked")} ${when(f.event_on)}` : __("Booked");
		} else if (f.due_by) {
			handoff_meta = `${f.overdue ? __("OVERDUE") : __("due")} ${when(f.due_by)}`;
		} else {
			handoff_meta = __("Hold the hand-off meeting");
		}

		// The gate, shown rather than merely enforced, so the order is visible
		// before somebody hits a server error.
		const unblocked = f.scheduled || f.held || !f.gate_applies;
		let create_meta;
		if (project_created) {
			create_meta = __("Done");
		} else if (f.won && !unblocked) {
			create_meta = __("Blocked until the hand-off meeting is booked");
		} else if (f.won) {
			create_meta = __("Ready");
		} else {
			create_meta = "";
		}

		return [
			{
				no: 1,
				title: __("Mark Opportunity as Won"),
				status: f.won ? "Completed" : "Pending",
				current: !f.won,
				meta: won_meta,
			},
			{
				no: 2,
				title: __("Hold Hand-Off Meeting"),
				status: f.held ? (f.skip_reason ? "Skipped" : "Completed") : "Pending",
				current: f.won && !f.held,
				meta: handoff_meta,
			},
			{
				no: 3,
				title: __("Create Project in PM System"),
				status: project_created ? "Completed" : "Pending",
				current: f.won && unblocked && !project_created,
				meta: create_meta,
			},
		];
	}

	// -----------------------------------------------------------------------
	// The buttons that DO the steps — rendered into the tab.
	// -----------------------------------------------------------------------

	function button(action, label, variant) {
		return `<button type="button" class="btn btn-${variant} btn-sm" data-ee-handoff="${action}">
			${frappe.utils.escape_html(label)}</button>`;
	}

	function status_html(f) {
		if (!f.won) return "";
		let cls = "orange";
		let text;
		if (f.held && f.skip_reason) {
			text = __("Hand-off SKIPPED {0} by {1}: {2}", [
				when(f.held_on) || __("earlier"),
				f.held_by || "",
				f.skip_reason,
			]);
		} else if (f.held) {
			cls = "green";
			text = __("Hand-off recorded {0} by {1}.", [when(f.held_on) || __("earlier"), f.held_by || ""]);
		} else if (f.scheduled) {
			// Booked and still past SLA is a real state — the meeting can be booked
			// for later than the two business days allowed — and it must not read
			// as "all good" just because half of it was done.
			const record_it = __(
				"Use Hand-Off Meeting Finished once it has happened — the project does not wait for it."
			);
			const booked = f.event_on
				? __("Meeting booked for {0}.", [when(f.event_on)])
				: __("Meeting booked.");
			cls = f.overdue ? "red" : "green";
			text = f.overdue
				? `${__("Hand-off is past SLA (due {0}).", [when(f.due_by)])} ${booked} ${record_it}`
				: `${booked} ${record_it}`;
		} else if (f.overdue) {
			cls = "red";
			text = __("Hand-off meeting is OVERDUE (due {0}). {1}", [when(f.due_by), f.gate_message]);
		} else if (f.gate_applies) {
			text = f.gate_message;
		} else {
			return "";
		}
		return `<div class="ee-process-status ${cls}">${frappe.utils.escape_html(text)}</div>`;
	}

	// Every button belongs to one of the steps in the bar, and is rendered in
	// that step's box. The step numbers are the process's own and match the
	// Project tracker's, so the mapping holds whether the bar is showing the
	// derived three steps or the project's real rows.
	const STEP_MEETING = 2;
	const STEP_PROJECT = 3;

	function step_buttons(f) {
		const by_step = {};
		if (!f.won) return by_step;
		const at = (no, html) => (by_step[no] = by_step[no] || []).push(html);

		// Booking is step 2 and comes first. On a gate-exempt legacy deal the two
		// can be offered together, and then this one still leads — one primary
		// button, pointing at the step the process actually wants next.
		const booking_first = !f.held && !f.scheduled;
		if (booking_first) {
			at(STEP_MEETING, button("schedule", __("Schedule Hand-Off Meeting"), "primary"));
		}
		// Offered strictly on the server's own predicate, so the tab can never
		// show a button whose insert is guaranteed to be refused.
		if (!f.project && (f.gate_open || !f.gate_applies)) {
			at(
				STEP_PROJECT,
				button("create", __("Create Project in PM System"), booking_first ? "default" : "primary")
			);
		}
		// Secondary on purpose: closing step 2 is bookkeeping the process needs,
		// not the thing standing between the user and their project.
		if (!f.held && (f.scheduled || !f.gate_applies)) {
			at(STEP_MEETING, button("finish", __("Hand-Off Meeting Finished"), "default"));
		}
		// A meeting that moved is a normal thing to happen, just no longer the
		// primary action once one is on the calendar.
		if (!f.held && f.scheduled) {
			at(STEP_MEETING, button("reschedule", __("Re-schedule Meeting"), "default"));
		}
		if (!f.held && f.gate_applies && f.can_skip) {
			at(STEP_MEETING, button("skip", __("Skip Hand-Off"), "default"));
		}

		return by_step;
	}

	function schedule_meeting(frm, reschedule) {
		erpnext_enhancements.handoff_meeting_dialog.schedule_for_opportunity(frm.doc.name, {
			title: reschedule ? __("Re-schedule Hand-Off Meeting") : __("Schedule Hand-Off Meeting"),
			on_success: () => frm.reload_doc(),
		});
	}

	function create_project(frm) {
		erpnext_enhancements.crm.open_create_project_dialog(frm.doc.name, {
			frm: frm,
			on_success: () => frm.reload_doc(),
		});
	}

	function mark_complete(frm) {
		frappe
			.xcall("erpnext_enhancements.crm_enhancements.handoff.mark_handoff_complete", {
				opportunity: frm.doc.name,
			})
			.then(() => {
				frappe.show_alert({ message: __("Hand-off recorded."), indicator: "green" });
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

	const ACTIONS = {
		schedule: (frm) => schedule_meeting(frm, false),
		reschedule: (frm) => schedule_meeting(frm, true),
		create: create_project,
		finish: mark_complete,
		skip: skip_handoff,
	};

	function bind(frm, field) {
		field.$wrapper.find("[data-ee-handoff]").on("click", function () {
			const run = ACTIONS[$(this).attr("data-ee-handoff")];
			if (run) run(frm);
		});
	}

	// -----------------------------------------------------------------------
	// Render
	// -----------------------------------------------------------------------

	/** `{steps, footer}` for the bar — the project's real rows where they exist. */
	function step_view(frm, f) {
		if (!f.project) return Promise.resolve({ steps: derived_steps(frm, f, false) });

		return frappe
			.xcall("erpnext_enhancements.crm_enhancements.project_prompt.opportunity_handoff_steps", {
				opportunity_name: frm.doc.name,
			})
			.then((data) => {
				const rows = (data && data.steps) || [];
				const proj = (data && data.project) || f.project;
				const link = `<a href="/app/project/${encodeURIComponent(proj)}">${frappe.utils.escape_html(
					proj
				)}</a>`;
				if (!rows.length) {
					// Project exists but its hand-off tracker was never started
					// (in-flight projects opt in on the Project via "Start Hand-Off
					// Process"). Show the project-aware derived view + a pointer, so
					// the tab is never blank.
					return {
						steps: derived_steps(frm, f, true),
						footer: `<div class="ee-step-meta" style="margin-top:6px;">
							${__("Detailed hand-off tracker not started on")} ${link}
						</div>`,
					};
				}
				const current = rows.find((s) => s.status === "Pending");
				return {
					steps: rows.map((s) => ({
						no: s.step_number,
						title: s.step_title,
						status: s.status,
						current: !!current && s.step_number === current.step_number,
						meta:
							s.status === "Completed" && s.completed_on
								? `${__("Done")} ${when(s.completed_on)}`
								: s.responsible_role || "",
					})),
					footer: `<div class="ee-step-meta" style="margin-top:6px;">
						${__("Full hand-off continues on")} ${link}
					</div>`,
				};
			})
			// Never leave the tab blank on a transient call failure.
			.catch(() => ({ steps: derived_steps(frm, f, true) }));
	}

	function render(frm) {
		const field = frm.get_field("custom_process_progress");
		if (!field || !field.$wrapper) return;
		// Master switch (server guards are authority): hide while dormant.
		if (!frappe.boot.ee_process_automation || frm.is_new()) {
			field.$wrapper.html("");
			return;
		}

		frappe
			.xcall("erpnext_enhancements.crm_enhancements.handoff.handoff_state", {
				opportunity: frm.doc.name,
			})
			.catch(() => null)
			.then((state) => {
				const f = facts(frm, state);
				// An overdue hand-off is the one thing that stays on the form
				// dashboard rather than moving into the tab with the buttons: the
				// audit found 17 of 17 meetings silently past SLA, and a warning
				// only visible to somebody who already went looking is the failure
				// mode, not the fix.
				if (f.overdue && !f.held) {
					frm.dashboard.add_comment(
						__("Hand-off meeting is OVERDUE (due {0}). {1}", [
							when(f.due_by),
							f.scheduled
								? __("A meeting is booked — record it on the Hand-Off Process tab.")
								: f.gate_message,
						]),
						"red",
						true
					);
				}
				return step_view(frm, f).then((view) => {
					paint(field, view.steps, step_buttons(f), view.footer, status_html(f));
					bind(frm, field);
				});
			});
	}

	frappe.ui.form.on("Opportunity", {
		refresh(frm) {
			render(frm);
		},
	});
})();
