/**
 * @file Hand-off process preview on the Opportunity form (PRO-0204).
 * @description
 * Shows the first three hand-off steps — Mark Opportunity as Won, Hold Hand-Off
 * Meeting, Create Project — in the Opportunity's "Hand-Off Process" tab
 * (`custom_process_progress` HTML field). These cover the opportunity -> project
 * handover; once the project exists the full 7-step tracker continues on the
 * Project (steps 4-7 run there).
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
		return [
			{ no: 1, title: __("Mark Opportunity as Won"), status: won ? "Completed" : "Pending", current: !won, meta: won_meta },
			{
				no: 2,
				title: __("Hold Hand-Off Meeting"),
				status: "Pending",
				current: won && project_created,
				meta: project_created ? __("Detailed tracker not started yet") : __("After the project is created"),
			},
			{
				no: 3,
				title: __("Create Project in PM System"),
				status: project_created ? "Completed" : "Pending",
				current: won && !project_created,
				meta: project_created ? __("Done") : won ? __("Use the Create project prompt") : "",
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

	frappe.ui.form.on("Opportunity", {
		refresh(frm) {
			render(frm);
		},
	});
})();
