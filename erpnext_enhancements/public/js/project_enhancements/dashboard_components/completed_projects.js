/* global erpnext_enhancements */
frappe.provide("erpnext_enhancements.dashboard_components");

/**
 * Project Dashboard tab — Completed Projects.
 *
 * Targets: the "Completed Projects" tab of the Project Dashboard page.
 * Loaded via: lazy `frappe.require` from project_dashboard.js (constructed by
 * name; render()/unmount() called on tab show/hide).
 *
 * Fetches inactive projects (`is_active = "No"`) and renders a read-only table
 * with a column selector. Notable for its exponential-backoff retry on timeout /
 * fetch errors (up to 3 attempts) before surfacing an error card. AbortController
 * cancels in-flight work on tab switch.
 */
erpnext_enhancements.dashboard_components.CompletedProjects = class CompletedProjects {
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.abortController = null;
		this.columnSelector = new erpnext_enhancements.dashboard_components.ColumnSelector(
			"project_dashboard_completed_columns",
			[
				{ key: "project_name", label: "Project Name", locked: true },
				{ key: "project_id", label: "Project ID" },
				{ key: "status", label: "Status" },
				{ key: "project_type", label: "Type" },
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

	async fetch_and_render_data(attempt = 1) {
		this.abortController = new AbortController();
		const signal = this.abortController.signal;

		try {
			const projects = await erpnext_enhancements.dashboard_api.call(
				{
					method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.get_project_data",
					args: {
						is_active: "No",
					},
				},
				signal
			);

			if (signal.aborted) return;

			if (projects.message && !projects.message.error) {
				this.render_list_view(projects.message);
			} else {
				throw new Error(
					projects.message
						? projects.message.error
						: "Unknown error fetching completed projects"
				);
			}
		} catch (error) {
			if (error.name === "CancellationError") {
				return;
			}

			// Exponential backoff logic for retries
			const maxRetries = 3;
			if (
				attempt <= maxRetries &&
				(error.name === "TimeoutError" || error.message.includes("fetch"))
			) {
				console.warn(
					`Attempt ${attempt} failed. Retrying in ${Math.pow(2, attempt)} seconds...`
				);
				this.wrapper.html(`
                    <div class="alert alert-warning p-4 text-center">
                        <p><i class="fa fa-spinner fa-spin mr-2"></i> Retrying data fetch (Attempt ${attempt}/${maxRetries})...</p>
                    </div>
                `);

				await new Promise((resolve) => setTimeout(resolve, Math.pow(2, attempt) * 1000));

				if (signal.aborted) return;

				return this.fetch_and_render_data(attempt + 1);
			} else {
				this.handle_error(error);
			}
		} finally {
			if (this.abortController && this.abortController.signal === signal) {
				this.abortController = null;
			}
		}
	}

	render_list_view(projects) {
		this.wrapper.empty();

		if (!projects || projects.length === 0) {
			this.wrapper.html(
				'<p class="text-muted text-center p-4">No completed projects found.</p>'
			);
			return;
		}

		const toolbar = $('<div class="dashboard-list-toolbar"></div>').appendTo(this.wrapper);
		this.columnSelector.render_button(toolbar, () =>
			this.columnSelector.apply(this.wrapper)
		);

		const listContainer = $('<div class="frappe-list"></div>').appendTo(this.wrapper);

		const table = $(`
            <table class="table table-bordered table-hover">
                <thead class="thead-light">
                    <tr>
                        <th class="dashcol dashcol-project_name">Project Name</th>
                        <th class="dashcol dashcol-project_id">Project ID</th>
                        <th class="dashcol dashcol-status">Status</th>
                        <th class="dashcol dashcol-project_type">Type</th>
                        <th class="dashcol dashcol-assigned_to">Assigned To</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        `).appendTo(listContainer);

		const tbody = table.find("tbody");

		projects.forEach((p) => {
			const row = $(`
                <tr data-project="${p.name}">
                    <td class="dashcol dashcol-project_name project-name-cell"><a href="/app/project/${
						p.name
					}" class="font-weight-bold">${p.project_name}</a></td>
                    <td class="dashcol dashcol-project_id project-id-cell"><a href="/app/project/${
						p.name
					}" class="text-muted">${p.name}</a></td>
                    <td class="dashcol dashcol-status"><span class="badge ${this.get_status_badge(
						p.status
					)}">${p.status}</span></td>
                    <td class="dashcol dashcol-project_type">${
						p.project_type || "Uncategorized"
					}</td>
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

		this.columnSelector.apply(this.wrapper);
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
			console.log("Completed Projects request aborted due to context switch.");
			return;
		}

		console.error("Completed Projects Error:", error);

		this.wrapper.html(`
            <div class="alert alert-danger p-4 text-center">
                <h4><i class="fa fa-exclamation-triangle mr-2"></i> Service Unavailable</h4>
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
