/* global erpnext_enhancements */
frappe.provide("erpnext_enhancements.dashboard_components");

/**
 * Project Dashboard tab — Active Internal Projects.
 *
 * Targets: the "Active Internal Projects" tab of the Project Dashboard page.
 * Loaded via: lazy `frappe.require` from project_dashboard.js, which constructs
 * this class (by name) and calls render()/unmount() as the tab is shown/hidden.
 *
 * Fetches all projects, keeps the active internal ones, groups them by Master
 * Project (Independent Projects sorted last), and renders an editable table where
 * inline status/priority edits emit a `dashboard_project_change` document event
 * for the parent dashboard to persist. Uses an AbortController so an in-flight
 * request is cancelled when the user switches tabs (unmount).
 */
erpnext_enhancements.dashboard_components.ActiveInternalProjects = class ActiveInternalProjects {
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.abortController = null;
		this.columnSelector = new erpnext_enhancements.dashboard_components.ColumnSelector(
			"project_dashboard_active_internal_columns",
			[
				{ key: "project_name", label: "Project Name", locked: true },
				{ key: "project_id", label: "Project ID" },
				{ key: "status", label: "Status" },
				{ key: "priority", label: "Priority" },
				{ key: "percent_complete", label: "% Complete" },
				{ key: "assigned_to", label: "Assigned To" },
			]
		);
	}

	async render() {
		this.wrapper.empty();
		this.show_skeleton();

		try {
			await this.fetch_and_render_data();
		} catch (error) {
			this.handle_error(error);
		}
	}

	async fetch_and_render_data() {
		this.abortController = new AbortController();
		const signal = this.abortController.signal;

		try {
			const projects = await erpnext_enhancements.dashboard_api.call(
				{
					method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.get_project_data",
				},
				signal
			);

			if (signal.aborted) return;

			if (projects.message && !projects.message.error) {
				const filteredProjects = projects.message.filter(
					(p) => p.is_active === "Yes"
				);

				this.render_list_view(filteredProjects);
			} else {
				throw new Error(
					projects.message ? projects.message.error : "Unknown error fetching projects"
				);
			}
		} finally {
			this.abortController = null;
		}
	}

	render_list_view(projects) {
		this.wrapper.empty();

		if (!projects || projects.length === 0) {
			this.wrapper.html(
				'<p class="text-muted text-center p-4">No active internal projects found.</p>'
			);
			return;
		}

		const toolbar = $('<div class="dashboard-list-toolbar"></div>').appendTo(this.wrapper);
		this.columnSelector.render_button(toolbar, () =>
			this.columnSelector.apply(this.wrapper)
		);

		const listContainer = $('<div class="frappe-list"></div>').appendTo(this.wrapper);

		// Group by custom_master_project
		let groups = {};
		projects.forEach((p) => {
			let master = p.custom_master_project || "Independent Projects";
			if (!groups[master]) {
				groups[master] = [];
			}
			groups[master].push(p);
		});

		// Sort master groups alphabetically, but keep Independent Projects at the bottom
		let sorted_masters = Object.keys(groups).sort((a, b) => {
			if (a === "Independent Projects") return 1;
			if (b === "Independent Projects") return -1;
			return a.localeCompare(b);
		});

		sorted_masters.forEach((master) => {
			let master_projects = groups[master];

			// Sort projects within the group alphabetically by name
			master_projects.sort((a, b) => {
				let nameA = a.project_name || "";
				let nameB = b.project_name || "";
				return nameA.localeCompare(nameB);
			});

			$(`<h5 class="mt-4 mb-3 text-muted border-bottom pb-2">${master}</h5>`).appendTo(
				listContainer
			);
			this.render_table(listContainer, master_projects);
		});

		this.columnSelector.apply(this.wrapper);
	}

	render_table(container, projects) {
		const table = $(`
            <table class="table table-bordered table-hover mb-4">
                <thead class="thead-light">
                    <tr>
                        <th class="dashcol dashcol-project_name">Project Name</th>
                        <th class="dashcol dashcol-project_id">Project ID</th>
                        <th class="dashcol dashcol-status">Status</th>
                        <th class="dashcol dashcol-priority">Priority</th>
                        <th class="dashcol dashcol-percent_complete">% Complete</th>
                        <th class="dashcol dashcol-assigned_to">Assigned To</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        `).appendTo(container);

		const tbody = table.find("tbody");
		const statusOptions = [
			"Active",
			"Client Hold",
			"Parked",
			"Completed",
			"Invoiced",
			"Paid",
			"Canceled",
		];
		const priorityOptions = ["High", "Medium", "Low"];

		projects.forEach((p) => {
			const statusSelect = statusOptions
				.map(
					(s) => `<option value="${s}" ${p.status === s ? "selected" : ""}>${s}</option>`
				)
				.join("");
			const prioritySelect = priorityOptions
				.map(
					(pr) =>
						`<option value="${pr}" ${
							p.custom_project_priority === pr ? "selected" : ""
						}>${pr}</option>`
				)
				.join("");

			const row = $(`
                <tr data-project="${p.name}">
                    <td class="dashcol dashcol-project_name project-name-cell"><a href="/app/project/${
						p.name
					}" class="font-weight-bold">${p.project_name}</a></td>
                    <td class="dashcol dashcol-project_id project-id-cell"><a href="/app/project/${
						p.name
					}" class="text-muted">${p.name}</a></td>
                    <td class="dashcol dashcol-status"><select class="form-control form-control-sm project-edit" data-field="status">${statusSelect}</select></td>
                    <td class="dashcol dashcol-priority"><select class="form-control form-control-sm project-edit" data-field="custom_project_priority">${prioritySelect}</select></td>
                    <td class="dashcol dashcol-percent_complete">
                        <div class="progress" style="height: 10px; border-radius: 4px;">
                            <div class="progress-bar bg-primary" role="progressbar" style="width: ${
								p.percent_complete || 0
							}%" aria-valuenow="${
				p.percent_complete || 0
			}" aria-valuemin="0" aria-valuemax="100"></div>
                        </div>
                    </td>
                    <td class="dashcol dashcol-assigned_to text-muted">${
						p.project_user || "Unassigned"
					}</td>
                </tr>
            `);

			// Attach all project data to the row for dynamic filtering
			Object.keys(p).forEach((key) => {
				row.attr(`data-${key}`, p[key]);
			});

			tbody.append(row);
		});

		// Trigger global event on edit
		table.find(".project-edit").on("change", (e) => {
			const el = $(e.target);
			const project = el.closest("tr").data("project");
			const field = el.data("field");
			const val = el.val();

			// Dispatch a custom event to the document so the main controller can pick it up
			$(document).trigger("dashboard_project_change", {
				project: project,
				field: field,
				value: val,
			});
		});
	}

	get_status_badge(status) {
		switch (status) {
			case "Active":
				return "badge-primary";
			case "Completed":
				return "badge-success";
			case "Paid":
				return "badge-success";
			case "Overdue":
				return "badge-danger";
			case "Canceled":
			case "Canceled":
				return "badge-danger";
			case "Working":
				return "badge-warning";
			case "Client Hold":
			case "Parked":
				return "badge-warning";
			case "Invoiced":
				return "badge-info";
			default:
				return "badge-secondary";
		}
	}

	show_skeleton() {
		this.wrapper.html(`
            <div class="skeleton-list p-4">
                <div class="skeleton-line" style="width: 100%; height: 20px; margin-bottom: 10px;"></div>
                <div class="skeleton-line" style="width: 100%; height: 20px; margin-bottom: 10px;"></div>
                <div class="skeleton-line" style="width: 100%; height: 20px; margin-bottom: 10px;"></div>
                <div class="skeleton-line" style="width: 100%; height: 20px;"></div>
            </div>
        `);
	}

	handle_error(error) {
		if (error.name === "CancellationError") {
			console.log("Active Internal Projects request aborted due to context switch.");
			return;
		}

		console.error("Active Internal Projects Error:", error);

		this.wrapper.html(`
            <div class="alert alert-danger p-4 text-center">
                <h4><i class="fa fa-exclamation-triangle mr-2"></i> Failed to Load Data</h4>
                <p>${error.message || "An unexpected error occurred."}</p>
                <button class="btn btn-primary btn-sm mt-3 retry-btn">Retry</button>
            </div>
        `);

		this.wrapper.find(".retry-btn").on("click", () => {
			this.render();
		});
	}

	unmount() {
		if (this.abortController) {
			this.abortController.abort();
		}
		this.wrapper.empty();
	}
};
