/**
 * Project form — Schedule tab Gantt: first real embed of the reusable widget.
 *
 * Targets: the Project DocType form. Loaded via hooks.py `doctype_js["Project"]`.
 *
 * Mounts `erpnext_enhancements.gantt.mount(...)` (public/js/gantt_widget/) into
 * the existing `custom_gantt_chart_html` field on the Schedule tab — the field
 * that previously hosted the legacy interactive frappe-gantt (removed from
 * project_enhancements/doctype/project/project.js in the same change; that
 * file still owns the health banner bound off this field). Read-only:
 * drag-editing returns with the widget's per-embed edit opt-in milestone.
 *
 * Config: the project's Tasks (host binding = filter on the current record),
 * task tree via parent_task, dependency arrows from the depends_on child
 * table, a task-status filter (all statuses shown by default), and a Today
 * button — the chart opens scrolled to today with a today marker.
 *
 * Lifecycle handled here:
 *  - unsaved docs: a placeholder instead of a mount (no record to filter on);
 *  - destroy-on-refresh: every form refresh (save, SPA navigation to another
 *    Project) tears the old instance, visibility observer and realtime
 *    listener down before re-arming, so DHTMLX internals and DOM never leak
 *    across documents;
 *  - lazy mount via IntersectionObserver: on this Frappe build every tab's
 *    fields are built eagerly into hidden panes, and tab activation is handled
 *    by Frappe's own tab code (no reliable Bootstrap shown.bs.tab, and the
 *    nav markup — <button class="nav-link" data-fieldname=…> — is easy to
 *    mis-select). Observing the host element's actual visibility sidesteps
 *    the markup entirely: the widget mounts (and fetches data) only when the
 *    Schedule pane first becomes visible, which also guarantees DHTMLX
 *    initializes with a real, non-zero container size;
 *  - realtime freshness: once mounted, the chart re-fetches on the
 *    "project_dashboard_updated" event (published by Project/Task on_update),
 *    preserving scroll position, matching the legacy gantt's behavior.
 */

// Task.status Select options (verified against live meta); order is display order.
const EE_TASK_STATUSES = [
	"Open",
	"Working",
	"Pending Review",
	"Overdue",
	"Completed",
	"Invoiced",
	"Canceled",
	"Template",
];

frappe.ui.form.on("Project", {
	refresh(frm) {
		const field = frm.get_field("custom_gantt_chart_html");
		if (!field || !erpnext_enhancements.gantt) {
			// field missing on this site, or the widget bundle failed to load
			return;
		}

		if (frm.__project_gantt) {
			frm.__project_gantt.destroy();
			frm.__project_gantt = null;
		}
		if (frm.__project_gantt_observer) {
			frm.__project_gantt_observer.disconnect();
			frm.__project_gantt_observer = null;
		}
		if (frm.__project_gantt_realtime) {
			frappe.realtime.off("project_dashboard_updated", frm.__project_gantt_realtime);
			frm.__project_gantt_realtime = null;
		}
		if (!field.$wrapper) {
			// defensive: this build renders tab fields eagerly, so the wrapper
			// exists from the first refresh — but never crash if that changes
			return;
		}

		if (frm.is_new()) {
			field.$wrapper.html(
				`<div class="text-muted">${__("Save the project to see its schedule.")}</div>`
			);
			return;
		}

		const docname = frm.doc.name;
		field.$wrapper.empty();
		const host = $('<div class="ee-project-gantt"></div>').appendTo(field.$wrapper)[0];

		const observer = new IntersectionObserver((entries) => {
			if (!entries.some((entry) => entry.isIntersecting)) {
				return;
			}
			observer.disconnect();
			if (frm.__project_gantt_observer === observer) {
				frm.__project_gantt_observer = null;
			}
			if (frm.doc.name !== docname || frm.__project_gantt) {
				return; // SPA-navigated away before the tab was ever opened
			}
			frm.__project_gantt = erpnext_enhancements.gantt.mount(host, {
				doctype: "Task",
				fields: {
					text: "subject",
					start: "exp_start_date",
					end: "exp_end_date",
					progress: "progress",
					parent: "parent_task",
				},
				filters: { project: docname },
				dependencies: "depends_on",
				order_by: "exp_start_date asc",
				limit: 1000,
				toolbar: {
					today: true,
					filters: [
						{
							fieldname: "status",
							label: __("Status"),
							options: EE_TASK_STATUSES,
						},
					],
				},
				on_task_click: (id) => frappe.set_route("Form", "Task", id),
			});

			// In-place refresh when tasks change elsewhere (another user, the
			// dashboard, a background job). Bound only once mounted; torn down
			// on the next form refresh above.
			const on_realtime = (data) => {
				if (
					data &&
					data.project === docname &&
					frm.doc.name === docname &&
					frm.__project_gantt &&
					!frm.__project_gantt.destroyed
				) {
					frm.__project_gantt.refresh();
				}
			};
			frappe.realtime.on("project_dashboard_updated", on_realtime);
			frm.__project_gantt_realtime = on_realtime;
		});
		observer.observe(host);
		frm.__project_gantt_observer = observer;
	},
});
