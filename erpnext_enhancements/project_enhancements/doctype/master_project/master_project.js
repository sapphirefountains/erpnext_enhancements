// Copyright (c) 2024, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.ui.form.on("Master Project", {
	refresh: function (frm) {
		if (frm.doc.__islocal) {
			frm.get_field("projects_html").$wrapper.html(
				"<p class='text-muted'>Save to view projects.</p>"
			);
			frm.get_field("tasks_html").$wrapper.html(
				"<p class='text-muted'>Save to view tasks.</p>"
			);
			return;
		}

		frm.call({
			doc: frm.doc,
			method: "get_projects_and_tasks",
			callback: function (r) {
				if (r.message) {
					render_projects_table(frm, r.message.projects);
					render_tasks_table(frm, r.message.projects, r.message.tasks);
				}
			},
		});
	},
});

function render_projects_table(frm, projects) {
	let wrapper = frm.get_field("projects_html").$wrapper;
	if (!projects || projects.length === 0) {
		wrapper.html("<p class='text-muted'>No projects linked to this Master Project.</p>");
		return;
	}

	let html = `
		<table class="table table-bordered">
			<thead>
				<tr>
					<th>Project</th>
					<th>Status</th>
					<th>Priority</th>
					<th>Progress</th>
					<th>Due Date</th>
				</tr>
			</thead>
			<tbody>
	`;

	projects.forEach((p) => {
		let progress = p.percent_complete ? p.percent_complete + "%" : "0%";
		let due_date = p.expected_end_date ? frappe.datetime.str_to_user(p.expected_end_date) : "";
		let link = frappe.utils.get_form_link("Project", p.name, true, p.project_name || p.name);

		html += `
			<tr>
				<td>${link}</td>
				<td>${p.status || ""}</td>
				<td>${p.priority || ""}</td>
				<td>${progress}</td>
				<td>${due_date}</td>
			</tr>
		`;
	});

	html += `
			</tbody>
		</table>
	`;

	wrapper.html(html);
}

function render_tasks_table(frm, projects, tasks) {
	let wrapper = frm.get_field("tasks_html").$wrapper;
	if (!projects || projects.length === 0) {
		wrapper.html("<p class='text-muted'>No tasks to display.</p>");
		return;
	}

	// Group tasks by project
	let tasks_by_project = {};
	projects.forEach((p) => {
		tasks_by_project[p.name] = [];
	});

	if (tasks && tasks.length > 0) {
		tasks.forEach((t) => {
			if (tasks_by_project[t.project]) {
				tasks_by_project[t.project].push(t);
			}
		});
	}

	let html = `
		<table class="table table-bordered">
			<thead>
				<tr>
					<th>Task</th>
					<th>Status</th>
					<th>Progress</th>
					<th>Due Date</th>
				</tr>
			</thead>
			<tbody>
	`;

	projects.forEach((p, index) => {
		let p_link = frappe.utils.get_form_link("Project", p.name, true, p.project_name || p.name);
		let project_class = "project-" + index;

		html += `
			<tr class="table-active project-row" data-target=".${project_class}" style="cursor: pointer;">
				<td colspan="4">
					<span class="toggle-icon">&#9660;</span>
					<strong>Project: ${p_link}</strong>
				</td>
			</tr>
		`;

		let p_tasks = tasks_by_project[p.name] || [];
		if (p_tasks.length === 0) {
			html += `
				<tr class="${project_class}">
					<td colspan="4" class="text-muted" style="padding-left: 30px;">No tasks found for this project.</td>
				</tr>
			`;
		} else {
			p_tasks.forEach((t) => {
				let t_link = frappe.utils.get_form_link("Task", t.name, true, t.subject || t.name);
				let progress = t.progress != null ? t.progress + "%" : "";
				let due_date = t.exp_end_date ? frappe.datetime.str_to_user(t.exp_end_date) : "";
				html += `
					<tr class="${project_class}">
						<td style="padding-left: 30px;">${t_link}</td>
						<td>${t.status || ""}</td>
						<td>${progress}</td>
						<td>${due_date}</td>
					</tr>
				`;
			});
		}
	});

	html += `
			</tbody>
		</table>
	`;

	wrapper.html(html);

	// Add event listener for collapsing/expanding
	wrapper.find(".project-row").on("click", function () {
		let targetClass = $(this).data("target");
		let targetRows = wrapper.find(targetClass);
		let icon = $(this).find(".toggle-icon");

		if (targetRows.is(":visible")) {
			targetRows.hide();
			icon.html("&#9654;"); // Right arrow
		} else {
			targetRows.show();
			icon.html("&#9660;"); // Down arrow
		}
	});
}
