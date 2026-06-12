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
 * with a column selector. Sorted by "Completed On" (derived server-side from the
 * Version history of the is_active flag), newest first, with clickable column
 * headers to re-sort. Notable for its exponential-backoff retry on timeout /
 * fetch errors (up to 3 attempts) before surfacing an error card. AbortController
 * cancels in-flight work on tab switch.
 */
erpnext_enhancements.dashboard_components.CompletedProjects = class CompletedProjects {
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.abortController = null;
		this.projects = null;
		this.sort_state = { col: "completed_on", order: "desc" };
		this.columnSelector = new erpnext_enhancements.dashboard_components.ColumnSelector(
			"project_dashboard_completed_columns",
			[
				{ key: "project_name", label: "Project Name", locked: true },
				{ key: "project_id", label: "Project ID" },
				{ key: "status", label: "Status" },
				{ key: "project_type", label: "Type" },
				{ key: "assigned_to", label: "Assigned To" },
				{ key: "completed_on", label: "Completed On" },
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

		this.projects = projects;
		this._inject_sort_styles();

		const toolbar = $('<div class="dashboard-list-toolbar"></div>').appendTo(this.wrapper);
		this.columnSelector.render_button(toolbar, () =>
			this.columnSelector.apply(this.wrapper)
		);

		const listContainer = $('<div class="frappe-list"></div>').appendTo(this.wrapper);

		const table = $(`
            <table class="table table-bordered table-hover">
                <thead class="thead-light">
                    <tr>
                        ${this.sortable_th("project_name", "Project Name")}
                        <th class="dashcol dashcol-project_id">Project ID</th>
                        ${this.sortable_th("status", "Status")}
                        ${this.sortable_th("project_type", "Type")}
                        ${this.sortable_th("assigned_to", "Assigned To")}
                        ${this.sortable_th("completed_on", "Completed On")}
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        `).appendTo(listContainer);

		this.bind_sortable_headers(table);

		const tbody = table.find("tbody");

		this.sort_projects(projects).forEach((p) => {
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
                    <td class="dashcol dashcol-completed_on">${
						p.completed_on
							? frappe.datetime.str_to_user(p.completed_on)
							: '<span class="text-muted">—</span>'
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

	/** Returns a copy of `projects` ordered by the current sort state. */
	sort_projects(projects) {
		const { col, order } = this.sort_state;
		const dir = order === "asc" ? 1 : -1;
		const by_name = (a, b) =>
			String(a.project_name || "").localeCompare(String(b.project_name || ""));

		return [...projects].sort((a, b) => {
			if (col === "completed_on") {
				// Projects without a completion date sink to the bottom either way
				if (!a.completed_on && !b.completed_on) return by_name(a, b);
				if (!a.completed_on) return 1;
				if (!b.completed_on) return -1;
				const diff = String(a.completed_on).localeCompare(String(b.completed_on));
				return diff !== 0 ? diff * dir : by_name(a, b);
			}

			const text_of = (p) => {
				if (col === "status") return p.status || "";
				if (col === "project_type") return p.project_type || "Uncategorized";
				if (col === "assigned_to") return p.project_user || "";
				return p.project_name || "";
			};
			const diff = text_of(a).localeCompare(text_of(b));
			if (diff !== 0) return diff * dir;
			return col !== "project_name" ? by_name(a, b) : 0;
		});
	}

	sortable_th(key, label) {
		const { col, order } = this.sort_state;
		const active = col === key ? ` active-sort sort-${order}` : "";
		return `<th class="sortable-header dashcol dashcol-${key}${active}" data-sort="${key}">${label}</th>`;
	}

	bind_sortable_headers(table) {
		table.find(".sortable-header").on("click", (e) => {
			const key = $(e.currentTarget).attr("data-sort");
			if (this.sort_state.col === key) {
				this.sort_state.order = this.sort_state.order === "asc" ? "desc" : "asc";
			} else {
				// Dates read best newest-first; text columns A->Z
				this.sort_state = {
					col: key,
					order: key === "completed_on" ? "desc" : "asc",
				};
			}
			this.render_list_view(this.projects);
		});
	}

	/**
	 * Ships the sortable-header styles with the component (same approach as
	 * ColumnSelector) since the dashboard tabs have no dedicated stylesheet.
	 * Matches the Custom HTML Block dashboard's sort affordance.
	 */
	_inject_sort_styles() {
		if (document.getElementById("dashboard-sortable-header-styles")) {
			return;
		}
		$("<style id='dashboard-sortable-header-styles'>").html(`
			.sortable-header { cursor: pointer; user-select: none; transition: background-color 0.2s; white-space: nowrap; }
			.sortable-header:hover { background-color: var(--fg-hover-color) !important; }
			.sortable-header.active-sort { background-color: var(--control-bg) !important; color: var(--primary); }
			.sortable-header.active-sort.sort-asc::after { content: " \\f0de"; font-family: "FontAwesome"; margin-left: 6px; }
			.sortable-header.active-sort.sort-desc::after { content: " \\f0dd"; font-family: "FontAwesome"; margin-left: 6px; }
		`).appendTo("head");
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
