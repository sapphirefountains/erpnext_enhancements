/**
 * Project form customization — health banner and reminders.
 *
 * Customizes: the Project doctype form. Loaded via `doctype_js["Project"]` in
 * hooks.py (one of several Project form scripts).
 *
 * This file registers two `frappe.ui.form.on("Project", { refresh })` handlers:
 *
 * 1) Health banner (first handler): renders a health-metrics banner (schedule
 *    health, overdue counts, overall progress) prepended to the form body,
 *    fed by `get_project_health_metrics` on
 *    page/project_dashboard/project_dashboard.py, and re-renders it on the
 *    "project_dashboard_updated" realtime event (published server-side by
 *    publish_realtime_update on Project/Task on_update). An idempotency flag
 *    (`__health_bound`) guards against rebinding across repeated refreshes.
 *
 *    NOTE: the Gantt chart that used to be rendered here (a frappe-gantt with
 *    drag-editing, heatmap and dependency linking) was replaced by the
 *    embeddable read-only Gantt widget — `custom_gantt_chart_html` is now
 *    mounted by public/js/project_enhancements/project_gantt_widget.js via
 *    erpnext_enhancements.gantt.mount (see public/js/gantt_widget/).
 *
 * 2) Reminder button (second handler): replaces the `custom_reminder_action`
 *    field with a "Set Reminder" button that opens a dialog and inserts a ToDo
 *    linked to the Project at the chosen time.
 */
frappe.ui.form.on("Project", {
	refresh: function (frm) {
		const wrapperField = frm.get_field("custom_gantt_chart_html");

		// Global Health Indicator (prepended to the form body — independent of
		// the Gantt widget that owns this field's wrapper)
		if (wrapperField && !frm.is_new()) {
			if (!wrapperField.__health_bound) {
				wrapperField.render_health_indicator = function(frm) {
					frappe.call({
						method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.get_project_health_metrics",
						args: { project_name: frm.doc.name },
						callback: (r) => {
							if (r.message && r.message.total_tasks > 0) {
								const data = r.message;
								const schedule_color = data.schedule_health > 80 ? 'text-success' : (data.schedule_health > 50 ? 'text-warning' : 'text-danger');
								const html = `<div class="project-health-dashboard d-flex align-items-center p-3 mb-3 border rounded shadow-sm" style="background: var(--card-bg);"><div class="health-metric mr-4 text-center" style="min-width: 100px;"><div class="h3 mb-0 ${schedule_color}">${data.schedule_health}%</div><div class="small text-muted text-uppercase font-weight-bold">Schedule Health</div></div><div class="health-metric mr-4 border-left pl-4"><div class="d-flex align-items-baseline"><span class="h4 mb-0 mr-2">${data.overdue_count}</span><span class="small text-muted">Overdue Tasks</span></div>${data.high_priority_overdue > 0 ? `<div class="small text-danger"><i class="fa fa-exclamation-triangle"></i> ${data.high_priority_overdue} High Priority Overdue</div>` : ''}</div><div class="health-metric mr-4 border-left pl-4 flex-grow-1"><div class="small d-flex justify-content-between mb-1"><span class="text-muted text-uppercase font-weight-bold">Overall Progress</span><span class="font-weight-bold">${data.overall_progress}%</span></div><div class="progress" style="height: 10px;"><div class="progress-bar bg-success" role="progressbar" style="width: ${data.overall_progress}%"></div></div></div><div class="health-metric border-left pl-4 text-center"><div class="h4 mb-0 text-primary">${data.completed_count}/${data.total_tasks}</div><div class="small text-muted">Tasks Done</div></div></div>`;
								const $container = frm.$wrapper.find('.form-body');
								$container.find('.project-health-dashboard').remove();
								$container.prepend(html);
							}
						}
					});
				};
				wrapperField.__health_bound = true;
				frappe.realtime.on("project_dashboard_updated", (data) => {
					if (data.project === frm.doc.name) {
						wrapperField.render_health_indicator(frm);
						// The Gantt widget refreshes itself on this event — see
						// project_gantt_widget.js.
					}
				});
			}
			wrapperField.render_health_indicator(frm);
		}
	},
});

frappe.ui.form.on("Project", {
	refresh: function (frm) {
		if (!frm.is_new()) {
			var field = frm.get_field("custom_reminder_action");
			if (field) {
				var $btn = $('<button class="btn btn-default btn-sm icon-btn"><span class="icon icon-sm"><svg class="es-icon es-line  icon-sm" aria-hidden="true"><use href="#es-line-bell"></use></svg></span> Set Reminder</button>');
				$btn.on("click", function () {
					var d = new frappe.ui.Dialog({
						title: __("Create a Reminder"),
						fields: [
							{ label: "Remind Me In", fieldname: "remind_in", fieldtype: "Select", options: [{ label: "30 Minutes", value: 30 }, { label: "1 Hour", value: 60 }, { label: "2 Hours", value: 120 }, { label: "4 Hours", value: 240 }, { label: "Tomorrow Morning", value: "tomorrow" }], onchange: function () { var choice = this.get_value(); if (!choice) return; var new_time; if (choice === "tomorrow") { new_time = moment().add(1, "days").set({ hour: 9, minute: 0, second: 0 }).format("YYYY-MM-DD HH:mm:ss"); } else { new_time = moment().add(choice, "minutes").format("YYYY-MM-DD HH:mm:ss"); } d.set_value("remind_at", new_time); } },
							{ fieldtype: "Column Break" },
							{ label: "Remind At", fieldname: "remind_at", fieldtype: "Datetime", reqd: 1, default: frappe.datetime.now_datetime() },
							{ fieldtype: "Section Break" },
							{ label: "Description", fieldname: "description", fieldtype: "Small Text", reqd: 1, default: "Reminder for Project: " + frm.doc.project_name },
						],
						primary_action_label: __("Create"),
						primary_action: function (values) {
							frappe.db.insert({ doctype: "ToDo", reference_type: frm.doc.doctype, reference_name: frm.doc.name, description: values.description, date: values.remind_at, allocated_to: frappe.session.user, status: "Open" }).then(() => { d.hide(); frappe.show_alert({ message: __("Reminder created successfully"), indicator: "green" }); });
						},
					});
					d.show();
				});
				field.$wrapper.empty().append($btn);
			}
		}
	},
});
