/**
 * Project form — "Timeline" tab: first real embed of the reusable Gantt widget.
 *
 * Targets: the Project DocType form. Loaded via hooks.py `doctype_js["Project"]`.
 *
 * Mounts `erpnext_enhancements.gantt.mount(...)` (public/js/gantt_widget/) into
 * the `custom_timeline_gantt_html` field on the custom "Timeline" tab (both
 * created by project_enhancements/setup.py on migrate). Read-only by design:
 * the interactive/editable Gantt remains the legacy frappe-gantt on the
 * Schedule tab (project_enhancements/doctype/project/project.js) — this embed
 * exercises the config-driven widget + permission-checked read API.
 *
 * Config: the project's Tasks (host binding = filter on the current record),
 * task tree via parent_task, dependency arrows from the depends_on child table.
 *
 * Lifecycle handled here:
 *  - unsaved docs: a placeholder instead of a mount (no record to filter on);
 *  - destroy-on-refresh: every form refresh (save, SPA navigation to another
 *    Project) tears the old instance + visibility observer down before
 *    re-arming, so DHTMLX internals and DOM never leak across documents;
 *  - lazy mount via IntersectionObserver: on this Frappe build every tab's
 *    fields are built eagerly into hidden panes, and tab activation is handled
 *    by Frappe's own tab code (no reliable Bootstrap shown.bs.tab, and the
 *    nav markup — <button class="nav-link" data-fieldname=…> — is easy to
 *    mis-select). Observing the host element's actual visibility sidesteps
 *    the markup entirely: the widget mounts (and fetches data) only when the
 *    Timeline pane first becomes visible, which also guarantees DHTMLX
 *    initializes with a real, non-zero container size.
 */
frappe.ui.form.on("Project", {
	refresh(frm) {
		const field = frm.get_field("custom_timeline_gantt_html");
		if (!field || !erpnext_enhancements.gantt) {
			// field not migrated in yet, or the widget bundle failed to load
			return;
		}

		if (frm.__timeline_gantt) {
			frm.__timeline_gantt.destroy();
			frm.__timeline_gantt = null;
		}
		if (frm.__timeline_gantt_observer) {
			frm.__timeline_gantt_observer.disconnect();
			frm.__timeline_gantt_observer = null;
		}
		if (!field.$wrapper) {
			// defensive: this build renders tab fields eagerly, so the wrapper
			// exists from the first refresh — but never crash if that changes
			return;
		}

		if (frm.is_new()) {
			field.$wrapper.html(
				`<div class="text-muted">${__("Save the project to see its timeline.")}</div>`
			);
			return;
		}

		const docname = frm.doc.name;
		field.$wrapper.empty();
		const host = $('<div class="ee-timeline-gantt"></div>').appendTo(field.$wrapper)[0];

		const observer = new IntersectionObserver((entries) => {
			if (!entries.some((entry) => entry.isIntersecting)) {
				return;
			}
			observer.disconnect();
			if (frm.__timeline_gantt_observer === observer) {
				frm.__timeline_gantt_observer = null;
			}
			if (frm.doc.name !== docname || frm.__timeline_gantt) {
				return; // SPA-navigated away before the tab was ever opened
			}
			frm.__timeline_gantt = erpnext_enhancements.gantt.mount(host, {
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
				on_task_click: (id) => frappe.set_route("Form", "Task", id),
			});
		});
		observer.observe(host);
		frm.__timeline_gantt_observer = observer;
	},
});
