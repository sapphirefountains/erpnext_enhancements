/* global erpnext_enhancements */
frappe.provide("erpnext_enhancements.dashboard_components");

erpnext_enhancements.dashboard_components.BufferManager = class BufferManager {
	constructor(parentComponent) {
		this.parent = parentComponent;
		this.pendingChanges = {}; // format: { projectId: { field: value } }
		this.originalData = {}; // format: { projectId: { field: value } }
	}

	set(projectId, field, value, originalValue) {
		if (!this.originalData[projectId]) {
			this.originalData[projectId] = {};
		}
		if (!(field in this.originalData[projectId])) {
			this.originalData[projectId][field] = originalValue;
		}

		if (!this.pendingChanges[projectId]) {
			this.pendingChanges[projectId] = {};
		}

		// If reverting to original value, remove from pending
		if (this.originalData[projectId][field] === value) {
			delete this.pendingChanges[projectId][field];
			if (Object.keys(this.pendingChanges[projectId]).length === 0) {
				delete this.pendingChanges[projectId];
			}
		} else {
			this.pendingChanges[projectId][field] = value;
		}

		this.updateGlobalUI();
		this.commit();
	}

	hasChanges() {
		return Object.keys(this.pendingChanges).length > 0;
	}

	getPendingValue(projectId, field) {
		if (this.pendingChanges[projectId] && field in this.pendingChanges[projectId]) {
			return this.pendingChanges[projectId][field];
		}
		return null;
	}

	rollback() {
		this.pendingChanges = {};
		this.updateGlobalUI();
		this.parent.render_list_view(this.parent.projects);
	}

	async commit() {
		if (!this.hasChanges()) return;

		// Lock rows and show loading indicators
		Object.keys(this.pendingChanges).forEach((projectId) => {
			const row = this.parent.wrapper.find(
				`tr:has(.project-link[data-name="${projectId}"])`
			);
			if (row.length) {
				row.addClass("saving-row");
				row.find(".editable-cell select").prop("disabled", true);
				row.find("td").css("opacity", "0.6");
				// Could add a spinner, but opacity + disabled prevents interaction
			}
		});

		const maxRetries = 3;
		const baseDelay = 1000;

		const executeWithRetry = async (projectId, changes, retries = 0) => {
			try {
				const response = await erpnext_enhancements.dashboard_api.call({
					method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.update_multiple_docs",
					args: {
						project_updates: JSON.stringify({ [projectId]: changes }),
						task_updates: "{}",
					},
				});

				if (response.message && response.message.status === "success") {
					const proj = this.parent.projects.find((p) => p.name === projectId);
					if (proj) {
						Object.assign(proj, changes);
					}
					return response;
				} else {
					throw new Error(
						response.message ? response.message.message : "Error updating project"
					);
				}
			} catch (error) {
				if (error.name === "CancellationError") {
					throw error; // Don't retry aborts
				}
				if (retries < maxRetries) {
					const delay = baseDelay * Math.pow(2, retries);
					console.warn(
						`Retry ${retries + 1}/${maxRetries} for ${projectId} after ${delay}ms`
					);
					await new Promise((resolve) => setTimeout(resolve, delay));
					return executeWithRetry(projectId, changes, retries + 1);
				}
				throw error;
			}
		};

		const pendingEntries = Object.entries(this.pendingChanges);
		const updatePromises = pendingEntries.map(([projectId, changes]) => {
			return executeWithRetry(projectId, changes);
		});

		const results = await Promise.allSettled(updatePromises);

		let allSuccess = true;
		let successfulCount = 0;

		results.forEach((result, index) => {
			const projectId = pendingEntries[index][0];
			const row = this.parent.wrapper.find(
				`tr:has(.project-link[data-name="${projectId}"])`
			);

			// Unlock row
			if (row.length) {
				row.removeClass("saving-row");
				row.find(".editable-cell select").prop("disabled", false);
				row.find("td").css("opacity", "");
			}

			if (result.status === "rejected") {
				allSuccess = false;
				frappe.show_alert({
					message: `Error saving changes for ${projectId}: ` + result.reason.message,
					indicator: "red",
				});
			} else {
				successfulCount++;
				// Remove successful changes from pending buffer
				delete this.pendingChanges[projectId];
				delete this.originalData[projectId];
			}
		});

		if (allSuccess) {
			frappe.show_alert({ message: "Changes saved successfully!", indicator: "green" });
			this.pendingChanges = {};
			this.originalData = {};
			this.updateGlobalUI();
			this.parent.render_list_view(this.parent.projects);
		} else {
			this.updateGlobalUI();
			// Re-render to show correct current state (successful ones updated, failed ones remain dirty)
			if (successfulCount > 0) {
				this.parent.render_list_view(this.parent.projects);
			}
		}
	}

	updateGlobalUI() {
		if (this.hasChanges()) {
			$("#global-pending-changes").show();
			// Bind our own click handlers if not already bound, or just override the default dashboard ones
			// Since dashboard logic also listens, we'll intercept by changing button classes or handling in component.
			// A simpler approach for this isolated component is to trigger a custom event that dashboard listens to,
			// but the prompt asked for commit/rollback within BufferManager.
			// We will attach one-off listeners on the dashboard's buttons.
			$("#save-global-changes")
				.off("click")
				.on("click", () => this.commit());
			$("#discard-global-changes")
				.off("click")
				.on("click", () => this.rollback());
		} else {
			$("#global-pending-changes").hide();
		}
	}
};

erpnext_enhancements.dashboard_components.PriorityOverview = class PriorityOverview {
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.abortController = null;
		this.current_view = "company_priority";
		this.projects = [];
		this.bufferManager = new erpnext_enhancements.dashboard_components.BufferManager(this);
		this.projectPriorityOptions = [];
		this.companyPriorityOptions = [];
		this.columnSelector = new erpnext_enhancements.dashboard_components.ColumnSelector(
			"project_dashboard_priority_columns",
			[
				{ key: "project_name", label: "Project Name", locked: true },
				{ key: "project_id", label: "Project ID" },
				{ key: "custom_company_priority", label: "Company Priority" },
				{ key: "custom_project_priority", label: "Project Priority" },
				{ key: "completion", label: "Completion Percentage" },
				{ key: "spend_percent", label: "Spend %" },
			]
		);
	}

	set_view(view) {
		this.current_view = view;
		if (this.projects && this.projects.length > 0) {
			this.render_list_view(this.projects);
		}
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
			// Fetch projects and priority options concurrently
			const [projectsRes, optionsRes] = await Promise.all([
				erpnext_enhancements.dashboard_api.call(
					{
						method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.get_project_data",
					},
					signal
				),
				erpnext_enhancements.dashboard_api.call(
					{
						method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.get_priority_options",
					},
					signal
				),
			]);

			if (signal.aborted) return;

			if (optionsRes.message && !optionsRes.message.error) {
				this.projectPriorityOptions = optionsRes.message.project_priority || [];
				this.companyPriorityOptions = optionsRes.message.company_priority || [];
			} else if (Array.isArray(optionsRes.message)) {
				// Backward compatibility just in case
				this.projectPriorityOptions = optionsRes.message;
				this.companyPriorityOptions = optionsRes.message;
			}

			if (projectsRes.message && !projectsRes.message.error) {
				this.projects = projectsRes.message.filter((p) => p.is_active === "Yes");
				this.render_list_view(this.projects);
			} else {
				throw new Error(
					projectsRes.message
						? projectsRes.message.error
						: "Unknown error fetching projects"
				);
			}
		} finally {
			this.abortController = null;
		}
	}

	get_priority_weight(priority) {
		if (!priority) return 100; // Empty/null treated as Not Assigned

		let p = String(priority).trim();

		if (p.toLowerCase() === "not assigned") return 100;
		if (p.toLowerCase() === "repair visit") return 101;
		if (p.toLowerCase() === "maintenance") return 102;

		let num = parseInt(p, 10);
		if (!isNaN(num)) {
			return num; // 1 to 30
		}

		return 200; // Unknown string values get pushed to the very bottom
	}

	render_list_view(projects) {
		this.wrapper.empty();

		if (!projects || projects.length === 0) {
			this.wrapper.html(
				'<p class="text-muted text-center p-4">No active projects found for priority overview.</p>'
			);
			return;
		}

		const toolbar = $('<div class="dashboard-list-toolbar"></div>').appendTo(this.wrapper);
		this.columnSelector.render_button(toolbar, () =>
			this.columnSelector.apply(this.wrapper)
		);

		const listContainer = $('<div class="frappe-list"></div>').appendTo(this.wrapper);

		if (this.current_view === "company_priority") {
			// Sort by company priority
			let sorted_projects = [...projects].sort((a, b) => {
				let weightA = this.get_priority_weight(a.custom_company_priority);
				let weightB = this.get_priority_weight(b.custom_company_priority);

				if (weightA !== weightB) {
					return weightA - weightB;
				}

				// Fallback to alphabetical project name if priorities are same
				let nameA = a.project_name || "";
				let nameB = b.project_name || "";
				return nameA.localeCompare(nameB);
			});

			this.render_table(listContainer, sorted_projects);
		} else if (this.current_view === "value_stream") {
			// Group by project_type (Value Stream)
			let groups = {};
			projects.forEach((p) => {
				let stream = p.project_type || "Uncategorized";
				if (stream === "Group Projects" || stream === "Internal") {
					return; // Exclude Group Projects and Internal
				}
				if (!groups[stream]) {
					groups[stream] = [];
				}
				groups[stream].push(p);
			});

			// Sort streams alphabetically
			let sorted_streams = Object.keys(groups).sort((a, b) => a.localeCompare(b));

			sorted_streams.forEach((stream) => {
				// Sort projects within stream by project priority
				let stream_projects = groups[stream].sort((a, b) => {
					let weightA = this.get_priority_weight(a.custom_project_priority);
					let weightB = this.get_priority_weight(b.custom_project_priority);

					if (weightA !== weightB) {
						return weightA - weightB;
					}

					let nameA = a.project_name || "";
					let nameB = b.project_name || "";
					return nameA.localeCompare(nameB);
				});

				$(`<h5 class="mt-4 mb-3 text-muted border-bottom pb-2">${stream}</h5>`).appendTo(
					listContainer
				);
				this.render_table(listContainer, stream_projects);
			});
		}

		this.columnSelector.apply(this.wrapper);
	}

	render_table(container, projects) {
		const table = $(`
            <table class="table table-bordered table-hover mb-4">
                <thead class="thead-light">
                    <tr>
                        <th class="dashcol dashcol-project_name">Project Name</th>
                        <th class="dashcol dashcol-project_id">Project ID</th>
                        <th class="dashcol dashcol-custom_company_priority">Company Priority</th>
                        <th class="dashcol dashcol-custom_project_priority">Project Priority</th>
                        <th class="dashcol dashcol-completion">Completion Percentage</th>
                        <th class="dashcol dashcol-spend_percent">Spend %</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        `).appendTo(container);

		const tbody = table.find("tbody");
		projects.forEach((p) => {
			// Apply pending changes if they exist in the buffer
			const display_company_priority =
				this.bufferManager.getPendingValue(p.name, "custom_company_priority") !== null
					? this.bufferManager.getPendingValue(p.name, "custom_company_priority")
					: p.custom_company_priority;

			const display_project_priority =
				this.bufferManager.getPendingValue(p.name, "custom_project_priority") !== null
					? this.bufferManager.getPendingValue(p.name, "custom_project_priority")
					: p.custom_project_priority;

			let total_budget = p.custom_project_dollar_amount || 0;
			let spend = p.estimated_costing || 0;
			let spend_percent = total_budget ? (spend / total_budget) * 100 : 0;
			let spend_percent_formatted = total_budget ? `${Math.round(spend_percent)}%` : "—";
			let spend_color = spend_percent > 100 ? "text-danger" : "text-success";

			let completion = p.percent_complete || 0;

			const row = $(`
                <tr>
                    <td class="dashcol dashcol-project_name project-name-cell"><a href="/app/project/${
						p.name
					}" class="font-weight-bold project-link" data-name="${p.name}">${
				p.project_name
			}</a></td>
                    <td class="dashcol dashcol-project_id project-id-cell"><a href="/app/project/${
						p.name
					}" class="text-muted project-link" data-name="${p.name}">${p.name}</a></td>
                    <td class="dashcol dashcol-custom_company_priority editable-cell priority-cell" data-field="custom_company_priority" data-project="${
						p.name
					}">
                        <div class="static-view" style="cursor: pointer;" title="Click to edit">
                            ${this.get_priority_badge(display_company_priority)}
                        </div>
                        <div class="edit-view" style="display: none;">
                            <select class="form-control form-control-sm"></select>
                        </div>
                    </td>
                    <td class="dashcol dashcol-custom_project_priority editable-cell priority-cell" data-field="custom_project_priority" data-project="${
						p.name
					}">
                        <div class="static-view" style="cursor: pointer;" title="Click to edit">
                            ${this.get_priority_badge(display_project_priority)}
                        </div>
                        <div class="edit-view" style="display: none;">
                            <select class="form-control form-control-sm"></select>
                        </div>
                    </td>
                    <td class="dashcol dashcol-completion" style="min-width: 150px;">
                        <div class="d-flex align-items-center">
                            <div class="progress flex-grow-1 mr-2" style="height: 10px;">
                                <div class="progress-bar ${
									completion === 100 ? "bg-success" : "bg-primary"
								}" role="progressbar" style="width: ${completion}%" aria-valuenow="${completion}" aria-valuemin="0" aria-valuemax="100"></div>
                            </div>
                            <span class="small font-weight-bold">${Math.round(completion)}%</span>
                        </div>
                    </td>
                    <td class="dashcol dashcol-spend_percent font-weight-bold ${spend_color}">${spend_percent_formatted}</td>
                </tr>
            `);

			row.find(".project-link").on("click", (e) => {
				e.preventDefault();
				frappe.set_route("project", p.name);
			});

			// Initialize inline editing functionality
			row.find(".editable-cell").each((_, cellEl) => {
				const cell = $(cellEl);
				const field = cell.data("field");
				const select = cell.find("select");
				const staticView = cell.find(".static-view");
				const editView = cell.find(".edit-view");

				// Populate options
				let optionsHtml = '<option value="">Not Assigned</option>';
				const optionsToUse =
					field === "custom_company_priority"
						? this.companyPriorityOptions
						: this.projectPriorityOptions;

				optionsToUse.forEach((opt) => {
					optionsHtml += `<option value="${frappe.utils.escape_html(
						opt
					)}">${frappe.utils.escape_html(opt)}</option>`;
				});
				select.html(optionsHtml);

				// Determine current value
				const currentValue =
					field === "custom_company_priority"
						? display_company_priority
						: display_project_priority;
				select.val(currentValue || "");

				// Event Listeners for Virtual DOM editing
				staticView.on("click", () => {
					// Close any other open selects in the table
					container.find(".edit-view").hide();
					container.find(".static-view").show();

					staticView.hide();
					editView.show();
					select.focus();
				});

				select.on("blur", () => {
					// Slight delay to allow change event to fire
					setTimeout(() => {
						editView.hide();
						staticView.show();
					}, 100);
				});

				select.on("change", () => {
					const newValue = select.val();
					const originalValue =
						field === "custom_company_priority"
							? p.custom_company_priority
							: p.custom_project_priority;

					this.bufferManager.set(p.name, field, newValue, originalValue);

					// Update the badge visually
					staticView.html(this.get_priority_badge(newValue));
					editView.hide();
					staticView.show();
				});
			});

			// Add data attributes to allow global search filtering
			row.data("project_name", p.project_name);
			row.data("custom_project_priority", display_project_priority);
			row.data("custom_company_priority", display_company_priority);
			row.data("percent_complete", completion);
			row.data("spend_percent", spend_percent);

			tbody.append(row);
		});

		// Ensure if click outside table cells, we close open selects
		container.off("click.closeSelects").on("click.closeSelects", (e) => {
			if (!$(e.target).closest(".editable-cell").length) {
				container.find(".edit-view").hide();
				container.find(".static-view").show();
			}
		});
	}

	get_priority_badge(priority) {
		if (!priority)
			return '<span class="badge" style="background-color: #6c757d; color: white;">Not Assigned</span>';

		let p = String(priority).trim();
		let safe_p = frappe.utils.escape_html(p);

		if (p.toLowerCase() === "not assigned") {
			return (
				'<span class="badge" style="background-color: #6c757d; color: white;">' +
				safe_p +
				"</span>"
			);
		}
		if (p.toLowerCase() === "repair visit") {
			return (
				'<span class="badge" style="background-color: #6f42c1; color: white;">' +
				safe_p +
				"</span>"
			);
		}
		if (p.toLowerCase() === "maintenance") {
			return (
				'<span class="badge" style="background-color: #007bff; color: white;">' +
				safe_p +
				"</span>"
			);
		}

		let num = parseInt(p, 10);
		if (!isNaN(num)) {
			// 1 to 30 -> Red (0) to Green (120)
			let clamped_num = Math.max(1, Math.min(30, num));
			let hue = ((clamped_num - 1) / 29) * 120;
			// Use 45% lightness so white text is readable across all hues
			return `<span class="badge" style="background-color: hsl(${hue}, 100%, 45%); color: white;">${safe_p}</span>`;
		}

		return (
			'<span class="badge" style="background-color: #6c757d; color: white;">' +
			safe_p +
			"</span>"
		);
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
                <div class="skeleton-line" style="width: 100%; height: 20px; margin-bottom: 10px;"></div>
                <div class="skeleton-line" style="width: 100%; height: 20px;"></div>
            </div>
        `);
	}

	handle_error(error) {
		if (error.name === "CancellationError") {
			console.log("Priority Overview request aborted due to context switch.");
			return;
		}

		console.error("Priority Overview Error:", error);

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
