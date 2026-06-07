/* global erpnext_enhancements */
frappe.provide("erpnext_enhancements.dashboard_components");

erpnext_enhancements.dashboard_components.TasksView = class TasksView {
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.abortController = null;
		this.currentSubView = "gantt";

		// View registry outlining properties of each sub-view
		this.subViews = {
			gantt: { renderType: "dom" },
			tree: { renderType: "dom" },
			kanban: { renderType: "route" },
			calendar: { renderType: "route" },
		};

		this.treeManagerInstance = null;
		this.ganttInstance = null;
		this.taskData = null; // Store centralized task data
		this.taskStatusOptions = [];
	}

	cleanupSubView() {
		if (this.ganttInstance) {
			// Destroy Gantt instance if there's a destroy method
			if (typeof this.ganttInstance.clear === "function") {
				this.ganttInstance.clear();
			}
			this.ganttInstance = null;
		}

		if (this.treeManagerInstance) {
			// Detach event handlers bounded to the wrapper or child nodes
			if (this.treeManagerInstance.wrapper) {
				this.treeManagerInstance.wrapper.off();
			}
			this.treeManagerInstance = null;
		}

		// Ensure DOM elements specifically for the subviews are cleaned up
		const viewContainer = this.wrapper.find(".task-view-container");
		if (viewContainer.length) {
			viewContainer.empty();
		}
	}

	async render(projectName) {
		this.wrapper.empty();

		if (projectName) {
			// Fetch centralized data, setup UI container and render default view
			this.show_skeleton();
			try {
				await this.fetch_task_data(projectName);
				await this.render_project_tasks(projectName);
			} catch (error) {
				this.handle_error(error);
			}
		} else {
			// Render project selection view
			this.show_skeleton();
			try {
				await this.fetch_and_render_project_selection();
			} catch (error) {
				this.handle_error(error);
			}
		}
	}

	async fetch_task_data(projectName) {
		this.abortController = new AbortController();
		const signal = this.abortController.signal;

		try {
			const fetchStatusOptions = erpnext_enhancements.dashboard_api.call(
				{
					method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.get_task_status_options",
				},
				signal
			);

			const fetchTasks = erpnext_enhancements.dashboard_api.call(
				{
					method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.get_project_tasks",
					args: { project: projectName },
				},
				signal
			);

			const results = await Promise.all([fetchStatusOptions, fetchTasks]);

			if (signal.aborted) return;

			const statusResult = results[0];
			const tasksResult = results[1];

			if (statusResult.message) {
				this.taskStatusOptions = statusResult.message;
			}

			if (tasksResult.message && !tasksResult.message.error) {
				this.taskData = tasksResult.message;
			} else {
				throw new Error(
					tasksResult.message
						? tasksResult.message.error
						: "Unknown error fetching tasks"
				);
			}
		} finally {
			this.abortController = null;
		}
	}

	async fetch_and_render_project_selection() {
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
				const activeProjects = projects.message.filter((p) => p.is_active === "Yes");
				this.render_project_list(activeProjects);
			} else {
				throw new Error(
					projects.message ? projects.message.error : "Unknown error fetching projects"
				);
			}
		} finally {
			this.abortController = null;
		}
	}

	render_project_list(projects) {
		this.wrapper.empty();

		if (!projects || projects.length === 0) {
			this.wrapper.html(
				'<p class="text-muted text-center p-4">No active projects found.</p>'
			);
			return;
		}

		const listContainer = $('<div class="frappe-list"></div>').appendTo(this.wrapper);

		const table = $(`
            <table class="table table-bordered table-hover">
                <thead class="thead-light">
                    <tr>
                        <th>Project Name</th>
                        <th width="150px">Actions</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        `).appendTo(listContainer);

		const tbody = table.find("tbody");

		// Sort projects alphabetically by project name
		projects.sort((a, b) => a.project_name.localeCompare(b.project_name));

		projects.forEach((p) => {
			const row = $(`
                <tr>
                    <td><a href="/app/project/${p.name}" class="font-weight-bold">${p.project_name}</a></td>
                    <td><button class="btn btn-primary btn-sm view-tasks-btn" data-project="${p.name}">View Tasks</button></td>
                </tr>
            `);
			tbody.append(row);
		});

		// Event listener for view tasks buttons
		this.wrapper.find(".view-tasks-btn").on("click", async (e) => {
			const projectName = $(e.currentTarget).data("project");
			try {
				// Await the routing transition to the Project form
				await frappe.set_route("project", projectName);
				// Once the promise resolves, assert the state
				window.location.hash = "#custom_scope";
			} catch (error) {
				// Intercept any rejected promises (aborted navigation, permission blocks)
				console.error("Failed to navigate to project tasks:", error);
			}
		});
	}

	render_project_tasks(projectName) {
		// Create header with back button
		const header = $(`
            <div class="d-flex align-items-center mb-3 tasks-view-header">
                <button class="btn btn-default btn-sm mr-3 back-to-projects-btn">
                    <i class="fa fa-arrow-left mr-1"></i> Back to Projects
                </button>
                <h4 class="mb-0 mr-4">Tasks for ${projectName}</h4>
                <div class="btn-group view-switcher" role="group">
                    <button type="button" class="btn btn-sm btn-outline-secondary" data-view="gantt">Gantt</button>
                    <button type="button" class="btn btn-sm btn-outline-secondary" data-view="tree">Tree</button>
                    <button type="button" class="btn btn-sm btn-outline-secondary" data-view="kanban">Kanban</button>
                    <button type="button" class="btn btn-sm btn-outline-secondary" data-view="calendar">Calendar</button>
                </div>
            </div>
            <div class="task-view-container"></div>
        `).appendTo(this.wrapper);

		this.update_view_switcher_ui();

		// Handle Back Navigation via Frappe router
		this.wrapper.on("click", ".back-to-projects-btn", () => {
			frappe.set_route("project-dashboard", "tasks-view");
		});

		// Handle view switching
		this.wrapper.on("click", ".view-switcher button", (e) => {
			const viewType = $(e.currentTarget).data("view");
			this.renderSubView(viewType, projectName);
		});

		// Render the default view
		this.renderSubView(this.currentSubView, projectName);
	}

	update_view_switcher_ui() {
		this.wrapper
			.find(".view-switcher button")
			.removeClass("active btn-secondary")
			.addClass("btn-outline-secondary");
		this.wrapper
			.find(`.view-switcher button[data-view="${this.currentSubView}"]`)
			.removeClass("btn-outline-secondary")
			.addClass("active btn-secondary");
	}

	async renderSubView(viewType, projectName) {
		if (!this.subViews[viewType]) {
			console.error(`Unknown sub-view type: ${viewType}`);
			return;
		}

		const viewConfig = this.subViews[viewType];

		if (viewConfig.renderType === "route") {
			if (viewType === "kanban") {
				frappe.route_options = { project: projectName };
				await frappe.set_route("List", "Task", "Kanban");
			} else if (viewType === "calendar") {
				frappe.route_options = { project: projectName };
				await frappe.set_route("List", "Task", "Calendar");
			}
			return;
		}

		this.currentSubView = viewType;
		this.update_view_switcher_ui();
		this.cleanupSubView();

		const viewContainer = this.wrapper.find(".task-view-container");
		viewContainer.empty();

		if (viewType === "gantt") {
			this.render_gantt_view(projectName, viewContainer);
		} else if (viewType === "tree") {
			this.render_tree_view(projectName, viewContainer);
		}
	}

	mapTasksForGantt(tasks) {
		const mappedTasks = [];

		const traverseTasks = (nodeList) => {
			if (!nodeList) return;
			nodeList.forEach((task) => {
				mappedTasks.push({
					id: task.name,
					name: task.subject,
					start: task.exp_start_date || frappe.datetime.get_today(),
					end:
						task.exp_end_date ||
						frappe.datetime.add_days(frappe.datetime.get_today(), 1),
					progress: task.progress || 0,
					dependencies: task.depends_on ? task.depends_on : "",
				});
				if (task.children && task.children.length > 0) {
					traverseTasks(task.children);
				}
			});
		};

		traverseTasks(tasks);
		return mappedTasks;
	}

	render_gantt_view(projectName, container) {
		// Create an inner wrapper for the gantt chart to handle auto-scrolling
		const ganttWrapper = $(
			'<div class="gantt-scroll-wrapper" style="overflow-x: auto; overflow-y: auto; height: 100%;"></div>'
		).appendTo(container);

		// Prevent horizontal scroll when only vertical scrolling is intended
		ganttWrapper.on("wheel", function (e) {
			if (e.originalEvent.deltaY !== 0 && !e.originalEvent.shiftKey) {
				e.stopPropagation();
			}
		});

		const ganttTarget = $('<svg id="gantt-target"></svg>').appendTo(ganttWrapper);

		// Required Frappe Gantt JS/CSS assets
		const assets = ["frappe-gantt.css", "frappe-gantt.js"];

		frappe.require(assets, () => {
			const ganttTasks = this.mapTasksForGantt(this.taskData);

			if (!ganttTasks || ganttTasks.length === 0) {
				container.html(
					'<div class="p-4 text-center text-muted">No task data available to construct Gantt chart.</div>'
				);
				return;
			}

			this.ganttInstance = new Gantt("#gantt-target", ganttTasks, {
				header_height: 50,
				column_width: 30,
				step: 24,
				view_modes: ["Quarter Day", "Half Day", "Day", "Week", "Month"],
				bar_height: 20,
				bar_corner_radius: 3,
				arrow_curve: 5,
				padding: 18,
				view_mode: "Day",
				date_format: "YYYY-MM-DD",
				custom_popup_html: null,
				on_click: (task) => {
					frappe.set_route("task", task.id);
				},
				on_date_change: (task, start, end) => {
					const formattedStart = frappe.datetime.moment(start).format("YYYY-MM-DD");
					const formattedEnd = frappe.datetime.moment(end).format("YYYY-MM-DD");

					// Non-blocking async update
					frappe.call({
						method: "frappe.client.set_value",
						args: {
							doctype: "Task",
							name: task.id,
							fieldname: {
								exp_start_date: formattedStart,
								exp_end_date: formattedEnd,
							},
						},
					});
				},
				on_progress_change: (task, progress) => {
					// Non-blocking async update
					frappe.call({
						method: "frappe.client.set_value",
						args: {
							doctype: "Task",
							name: task.id,
							fieldname: "progress",
							value: progress,
						},
					});
				},
			});

			// Re-use standard Frappe Gantt autoscroll snippet (we already have a global watcher, but trigger an explicit update)
			setTimeout(() => {
				const today_el = ganttWrapper[0].querySelector(".today-highlight");
				if (today_el) {
					const scroll_container = ganttWrapper[0];
					const container_width = scroll_container.clientWidth;
					const element_rect = today_el.getBoundingClientRect();
					const container_rect = scroll_container.getBoundingClientRect();

					const element_left_relative = element_rect.left - container_rect.left;
					const element_width = element_rect.width;

					const scroll_to_position =
						scroll_container.scrollLeft +
						element_left_relative -
						container_width / 2 +
						element_width / 2;

					scroll_container.scrollTo({
						left: scroll_to_position,
						behavior: "smooth",
					});
				}
			}, 500);
		});
	}

	render_tree_view(projectName, container) {
		frappe.require("/assets/erpnext_enhancements/js/task_tree_manager.js", () => {
			this.treeManagerInstance = new erpnext_enhancements.TaskTreeManager({
				wrapper: container,
				projectName: projectName,
				preFetchedData: this.taskData,
				taskStatusOptions: this.taskStatusOptions,
			});
		});
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
			console.log("Tasks Tree request aborted due to context switch.");
			return;
		}

		console.error("Tasks Tree Error:", error);

		this.wrapper.html(`
            <div class="alert alert-danger p-4 text-center">
                <h4><i class="fa fa-exclamation-triangle mr-2"></i> Failed to Load Data</h4>
                <p>${error.message || "An unexpected error occurred."}</p>
                <button class="btn btn-primary btn-sm mt-3 retry-btn">Retry</button>
            </div>
        `);

		this.wrapper.find(".retry-btn").on("click", () => {
			this.render(); // This will re-fetch and render project selection, not specific tree (unless managed in state).
			// We'll rely on route change logic to trigger correct render
		});
	}

	unmount() {
		if (this.abortController) {
			this.abortController.abort();
		}
		this.wrapper.empty();
	}
};
