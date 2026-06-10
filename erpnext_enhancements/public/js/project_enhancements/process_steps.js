/**
 * @file Hand-off process bar on the Project form (PRO-0204 tracker UI).
 * @description
 * Renders the `custom_process_steps` child table as a step progress bar in
 * the `custom_process_progress` HTML field (Hand-Off Process tab):
 *
 *  - ✓ completed steps (with who/when), the current step highlighted with
 *    its due date and a one-click "Mark Complete" action, upcoming steps
 *    muted, skipped steps struck through.
 *  - Overdue current step glows red (matches the Sales Pipeline lights).
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
			.ee-process-actions { margin-top: 6px; }
		</style>
	`;

	function render(frm) {
		const field = frm.get_field("custom_process_progress");
		if (!field || !field.$wrapper) return;
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

		const current = steps.find((s) => s.status === "Pending");
		const done = steps.filter((s) => s.status === "Completed").length;
		const now = frappe.datetime.now_datetime();

		let html = `${STYLE}<div class="ee-process-bar">`;
		steps.forEach((step) => {
			const is_current = current && step.name === current.name;
			const overdue = is_current && step.due_by && step.due_by < now;
			const cls =
				step.status === "Completed"
					? "done"
					: step.status === "Skipped"
						? "skipped"
						: is_current
							? `current${overdue ? " overdue" : ""}`
							: "";
			let meta = esc(step.responsible_role || "");
			if (step.status === "Completed" && step.completed_on) {
				meta = `${__("Done")} ${frappe.datetime.str_to_user(step.completed_on)}`;
			} else if (is_current && step.due_by) {
				meta = `${esc(step.responsible_role || "")} · ${overdue ? __("OVERDUE") : __("due")} ${frappe.datetime.str_to_user(step.due_by)}`;
			}
			html += `
				<div class="ee-process-step ${cls}" data-step="${esc(step.name)}">
					<span class="ee-step-no">${step.status === "Completed" ? "✓" : esc(String(step.step_number || ""))}</span>
					<div class="ee-step-title">${esc(step.step_title || "")}</div>
					<div class="ee-step-meta">${meta}</div>
					<div class="ee-step-extra" data-extra="${esc(step.name)}"></div>
				</div>
			`;
		});
		html += "</div>";

		if (current) {
			html += `
				<div class="ee-process-actions">
					<button class="btn btn-xs btn-primary ee-complete-step">
						${__("Mark Step {0} Complete", [current.step_number])}
					</button>
					<span class="text-muted" style="margin-left:8px; font-size:11px;">
						${__("{0} of {1} done", [done, steps.length])}
					</span>
				</div>
			`;
		}

		field.$wrapper.html(html);

		if (current) {
			field.$wrapper.find(".ee-complete-step").on("click", () => {
				frappe.model.set_value(current.doctype, current.name, "status", "Completed");
				frm.save();
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
			render(frm);
		},
	});
})();
