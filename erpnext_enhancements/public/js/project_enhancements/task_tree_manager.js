/* global erpnext_enhancements */

/**
 * TaskTreeManager — interactive, hierarchical task grid.
 *
 * Targets: rendered into a host element on the Project form's Scope tab
 * (`custom_tasks_html`) and into the Project Dashboard "Tasks" tab tree view.
 * Loaded via: hooks.py `app_include_js` (global), so the class is always defined;
 * callers (project_form_script.js, dashboard tasks_view.js) also `frappe.require`
 * it defensively before constructing.
 *
 * Renders an Asana-style tree of Tasks for one Project with: expand/collapse with
 * lazy child loading, inline editing of dates / expected time / status / priority,
 * quick-add rows, name/owner/status filtering, per-user column visibility, and
 * SortableJS drag-and-drop that both reorders siblings AND re-parents (nest) tasks
 * — drop intent is decided by where in the target row you release (see
 * handleDragMove). All mutations round-trip through the project_dashboard.py
 * whitelisted methods. Expanded-state and column choices persist in localStorage.
 *
 * Assets (task_tree.css, SortableJS from CDN) are loaded on demand in loadAssets().
 */
frappe.provide("erpnext_enhancements");

erpnext_enhancements.TaskTreeManager = class TaskTreeManager {
	constructor({
		wrapper,
		projectName,
		readonly = false,
		preFetchedData = null,
		taskStatusOptions = null,
	}) {
		this.wrapper = $(wrapper);
		this.projectName = projectName;
		this.readonly = readonly;
		this.tasks = [];
		this.expandedTasks = new Set();
		this.pendingChanges = {};
		this.taskStatusOptions = taskStatusOptions || [
			"Open",
			"Working",
			"Completed",
			"Canceled",
		];
		this.sortableInstances = [];
		this.preFetchedData = preFetchedData;

		const savedState = localStorage.getItem(`expandedTasks_${this.projectName}`);
		if (savedState) {
			this.expandedTasks = new Set(JSON.parse(savedState));
		}

		this.columnVisibility = {
			owner: false,
			status: true,
			priority: true,
			start_date: false,
			due_date: true,
			progress: true,
			duration: false,
		};
		const savedColumns = localStorage.getItem(`taskTreeColumns_${frappe.session.user}`);
		if (savedColumns) {
			this.columnVisibility = JSON.parse(savedColumns);
		}

		this.init();
	}

	init() {
		this.loadAssets().then(() => {
			this.renderStructure();
			if (this.preFetchedData) {
				this.hydrate(this.preFetchedData);
			} else {
				this.fetchData();
			}
		});
	}

	hydrate(data) {
		this.tasks = data;
		this.updateStatusFilterOptions();
		this.renderGrid(this.tasks);
	}

	loadAssets() {
		return new Promise((resolve) => {
			frappe.require("/assets/erpnext_enhancements/css/task_tree.css", () => {
				const script_url = "https://cdn.jsdelivr.net/npm/sortablejs@latest/Sortable.min.js";
				frappe.require(script_url, () => resolve());
			});
		});
	}

	renderStructure() {
		this.wrapper.html(`
            <div class="task-tree-manager p-3 border rounded" style="background: var(--card-bg);">
                <div class="task-tree-header mb-3">
                    <div class="d-flex justify-content-between align-items-center mb-2">
                        <div class="d-flex align-items-center">
                            <h5 class="mb-0 mr-3">Tasks</h5>
                            <span class="task-saving-indicator text-muted mr-3" style="display: none;"><i class="fa fa-spinner fa-spin"></i> Saving...</span>
                            <div class="task-pending-changes-controls mr-2" style="display: none;">
                                <div class="btn-group btn-group-sm">
                                    <button type="button" class="btn btn-success save-changes-btn">Save Changes</button>
                                    <button type="button" class="btn btn-danger discard-changes-btn">Discard Changes</button>
                                </div>
                            </div>
                            <button class="btn btn-sm btn-success mr-2 save-order-btn" style="display: none;">Save Order</button>
                            <button class="btn btn-sm btn-primary add-task-btn">Add Task</button>
                        </div>
                        <div class="d-flex align-items-center">
                            <div class="dropdown mr-2">
                                <button class="btn btn-sm btn-default dropdown-toggle" type="button" id="columnToggleDropdown" data-toggle="dropdown" aria-haspopup="true" aria-expanded="false">
                                    <i class="fa fa-columns"></i>
                                </button>
                                <div class="dropdown-menu dropdown-menu-right p-3" aria-labelledby="columnToggleDropdown" style="min-width: 200px;">
                                    <h6 class="dropdown-header px-0">Visible Columns</h6>
                                    <div class="form-check mb-1">
                                        <input class="form-check-input column-toggle-cb" type="checkbox" value="owner" id="cb-col-owner" ${this.columnVisibility.owner ? "checked" : ""}>
                                        <label class="form-check-label" for="cb-col-owner">Assigned To</label>
                                    </div>
                                    <div class="form-check mb-1">
                                        <input class="form-check-input column-toggle-cb" type="checkbox" value="status" id="cb-col-status" ${this.columnVisibility.status ? "checked" : ""}>
                                        <label class="form-check-label" for="cb-col-status">Status</label>
                                    </div>
                                    <div class="form-check mb-1">
                                        <input class="form-check-input column-toggle-cb" type="checkbox" value="priority" id="cb-col-priority" ${this.columnVisibility.priority ? "checked" : ""}>
                                        <label class="form-check-label" for="cb-col-priority">Priority</label>
                                    </div>
                                    <div class="form-check mb-1">
                                        <input class="form-check-input column-toggle-cb" type="checkbox" value="start_date" id="cb-col-start-date" ${this.columnVisibility.start_date ? "checked" : ""}>
                                        <label class="form-check-label" for="cb-col-start-date">Start Date</label>
                                    </div>
                                    <div class="form-check mb-1">
                                        <input class="form-check-input column-toggle-cb" type="checkbox" value="due_date" id="cb-col-due-date" ${this.columnVisibility.due_date ? "checked" : ""}>
                                        <label class="form-check-label" for="cb-col-due-date">Due Date</label>
                                    </div>
                                    <div class="form-check mb-1">
                                        <input class="form-check-input column-toggle-cb" type="checkbox" value="progress" id="cb-col-progress" ${this.columnVisibility.progress ? "checked" : ""}>
                                        <label class="form-check-label" for="cb-col-progress">% Complete</label>
                                    </div>
                                    <div class="form-check mb-1">
                                        <input class="form-check-input column-toggle-cb" type="checkbox" value="duration" id="cb-col-duration" ${this.columnVisibility.duration ? "checked" : ""}>
                                        <label class="form-check-label" for="cb-col-duration">Expected Time</label>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="d-flex align-items-center mb-2 task-filters-row">
                        <input type="text" class="form-control form-control-sm task-name-filter mr-2" placeholder="Filter by task name...">
                        <input type="text" class="form-control form-control-sm task-owner-filter mr-2" placeholder="Filter by owner...">
                        <div class="dropdown mr-2">
                            <button class="btn btn-sm btn-default dropdown-toggle" type="button" id="statusFilterMenu" data-toggle="dropdown" aria-haspopup="true" aria-expanded="false">
                                Statuses
                            </button>
                            <div class="dropdown-menu p-3 status-filter-dropdown" aria-labelledby="statusFilterMenu" style="min-width: 150px;">
                                <!-- Dynamic status checkboxes -->
                            </div>
                        </div>
                        <button class="btn btn-sm btn-default clear-filters-btn">Clear</button>
                    </div>
                </div>

                <div class="task-grid">
                    <div class="task-grid-header d-flex font-weight-bold border-bottom py-2" style="background: var(--subtle-fg);">
                        <div class="task-grid-cell" style="flex: 5;">Task</div>
                        <div class="task-grid-cell assignee-cell ${this.columnVisibility.owner ? "" : "hidden-column"}" data-column="owner" style="flex: 1.5;">Assigned To</div>
                        <div class="task-grid-cell status-cell ${this.columnVisibility.status ? "" : "hidden-column"}" data-column="status" style="flex: 1.5;">Status</div>
                        <div class="task-grid-cell priority-cell ${this.columnVisibility.priority ? "" : "hidden-column"}" data-column="priority" style="flex: 1;">Priority</div>
                        <div class="task-grid-cell date-cell ${this.columnVisibility.start_date ? "" : "hidden-column"}" data-column="start_date" style="flex: 1;">Start Date</div>
                        <div class="task-grid-cell date-cell ${this.columnVisibility.due_date ? "" : "hidden-column"}" data-column="due_date" style="flex: 1;">Due Date</div>
                        <div class="task-grid-cell progress-cell ${this.columnVisibility.progress ? "" : "hidden-column"}" data-column="progress" style="flex: 1;">% Complete</div>
                        <div class="task-grid-cell duration-cell ${this.columnVisibility.duration ? "" : "hidden-column"}" data-column="duration" style="flex: 1;">Expected Time</div>
                        ${!this.readonly ? `<div class="task-grid-cell actions-cell" data-column="actions" style="flex: 0.5;">Actions</div>` : ''}
                    </div>
                    <div class="task-grid-body">
                        <div class="p-4 text-center text-muted">Loading tasks...</div>
                    </div>
                </div>
            </div>
        `);
		this.bindEvents();
	}

	updateStatusFilterOptions() {
		const $dropdown = this.wrapper.find(".status-filter-dropdown");
		$dropdown.empty();
		this.taskStatusOptions.forEach((status) => {
			$dropdown.append(`
                <div class="form-check mb-1">
                    <input class="form-check-input task-status-cb" type="checkbox" value="${status}" id="filter-status-${status}">
                    <label class="form-check-label" for="filter-status-${status}">${status}</label>
                </div>
            `);
		});
	}

	fetchData() {
		frappe.call({
			method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.get_project_tasks",
			args: { project: this.projectName },
			callback: (r) => {
				if (r.message && !r.message.error) {
					this.tasks = r.message;
					this.updateStatusFilterOptions();
					this.renderGrid(this.tasks);
				}
			},
		});
	}

	refresh() {
		this.fetchData();
	}

	applyFilters() {
		const nameFilter = (this.wrapper.find(".task-name-filter").val() || "").toLowerCase();
		const ownerFilter = (this.wrapper.find(".task-owner-filter").val() || "").toLowerCase();
		const statusFilters = [];
		this.wrapper.find(".task-status-cb:checked").each(function () { statusFilters.push($(this).val()); });

		let filteredTasks = JSON.parse(JSON.stringify(this.tasks));
		const filterNode = (task) => {
			const nameMatch = !nameFilter || task.subject.toLowerCase().includes(nameFilter);
			const ownerMatch = !ownerFilter || (task.assigned_to || "").toLowerCase().includes(ownerFilter);
			const statusMatch = statusFilters.length === 0 || statusFilters.includes(task.status);
			task.is_direct_match = nameMatch && ownerMatch && statusMatch;
			if (task.children && task.children.length > 0) task.children = task.children.map(filterNode).filter(Boolean);
			const hasVisibleChildren = task.children && task.children.length > 0;
			return (task.is_direct_match || hasVisibleChildren) ? task : null;
		};
		filteredTasks = filteredTasks.map(filterNode).filter(Boolean);
		this.renderGrid(filteredTasks);
	}

	renderGrid(tasks) {
		const gridBody = this.wrapper.find(".task-grid-body");
		gridBody.empty();
		if (!tasks || tasks.length === 0) {
			gridBody.html('<div class="p-4 text-center text-muted">No tasks match filters.</div>');
			gridBody.append(this.createQuickAddRow(null, 0));
			return;
		}
		tasks.forEach((task) => this.renderTaskNode(task, gridBody, 0));
		gridBody.prepend(this.createQuickAddRow(null, 0));
		this.initializeTaskSorting();
	}

	renderTaskNode(task, container, level) {
		const start_date = task.exp_start_date ? frappe.datetime.str_to_user(task.exp_start_date) : "Set Date";
		const end_date = task.exp_end_date ? frappe.datetime.str_to_user(task.exp_end_date) : "Set Date";
		const progress = task.progress || 0;
		const isExpanded = this.expandedTasks.has(task.name);
		const isCollapsed = !isExpanded;
		const hasChildren = task.has_children || (task.children && task.children.length > 0);
		const iconClass = hasChildren ? (isCollapsed ? "fa-caret-right" : "fa-caret-down") + " toggle-child-tasks" : "fa-circle text-extra-muted" ;
		const statusBadge = this.getStatusBadge(task);
		const milestoneIcon = task.is_milestone ? `<i class="fa fa-diamond task-milestone-icon mr-1" title="Milestone"></i>` : "";
		const recurringIcon = task.custom_is_recurring ? `<i class="fa fa-circle task-recurring-icon mr-1" title="Recurring Task"></i>` : "";
		const isGhost = (this.wrapper.find(".task-name-filter").val() || this.wrapper.find(".task-owner-filter").val()) && !task.is_direct_match;

		const node = $(`
			<div class="task-node ${isGhost ? 'task-ghost-node' : ''}" data-task-id="${task.name}" data-loaded="${(task.children && task.children.length > 0) || !hasChildren}">
				<div class="task-grid-row ${task.is_direct_match ? 'task-search-match' : ''}">
					<div class="task-grid-cell">
						<div style="padding-left: ${level * 20}px; display: flex; align-items: center; width: 100%;">
							<i class="fa fa-bars task-drag-handle mr-2 text-muted" style="cursor: grab; flex-shrink: 0;"></i>
							<i class="fa fa-fw ${iconClass}" style="cursor: pointer; flex-shrink: 0; font-size: 10px;"></i>
							${milestoneIcon}${recurringIcon}<a href="/app/task/${task.name}" class="task-name-cell-text" title="${task.subject}">${task.subject}</a>
						</div>
					</div>
					<div class="task-grid-cell assignee-cell ${this.columnVisibility.owner ? "" : "hidden-column"}" data-column="owner"><a href="#" class="assignee-link">${task.assigned_to || "Unassigned"}</a></div>
					<div class="task-grid-cell status-cell ${this.columnVisibility.status ? "" : "hidden-column"}" data-column="status">${statusBadge}</div>
					<div class="task-grid-cell priority-cell ${this.columnVisibility.priority ? "" : "hidden-column"}" data-column="priority">
						<span class="priority-badge badge badge-pill badge-${this.getPriorityColor(task.priority)}" style="cursor: pointer; text-transform: uppercase; font-size: 10px; padding: 4px 8px;">${task.priority || "Medium"}</span>
					</div>
					<div class="task-grid-cell editable-date ${this.columnVisibility.start_date ? "" : "hidden-column"}" data-field="exp_start_date" data-task-id="${task.name}" data-original-date="${task.exp_start_date || ""}" data-column="start_date"><a href="#">${start_date}</a></div>
					<div class="task-grid-cell editable-date ${this.columnVisibility.due_date ? "" : "hidden-column"}" data-field="exp_end_date" data-task-id="${task.name}" data-original-date="${task.exp_end_date || ""}" data-column="due_date"><a href="#">${end_date}</a></div>
					<div class="task-grid-cell ${this.columnVisibility.progress ? "" : "hidden-column"}" data-column="progress"><div class="progress" style="height: 15px; width: 100%;"><div class="progress-bar" role="progressbar" style="width: ${progress}%;" aria-valuenow="${progress}" aria-valuemin="0" aria-valuemax="100">${progress}%</div></div></div>
					<div class="task-grid-cell editable-time ${this.columnVisibility.duration ? "" : "hidden-column"}" data-field="expected_time" data-task-id="${task.name}" data-original-value="${task.expected_time || 0}" data-column="duration"><a href="#">${task.expected_time || 0}</a></div>
					${!this.readonly ? `<div class="task-grid-cell actions-cell" data-column="actions"><button class="btn btn-xs btn-danger delete-task-btn" title="Delete Task" data-task-name="${task.name}" data-task-subject="${task.subject}"><i class="fa fa-trash"></i></button></div>` : ''}
				</div>
				<div class="child-tasks-container" style="${isCollapsed ? "display: none;" : ""}"></div>
			</div>
		`).appendTo(container);

		if (!$("#task-tree-styles").length) {
			$("<style id='task-tree-styles'>").html(`
				.task-search-match { background-color: var(--fg-hover-color) !important; }
				.task-ghost-node { opacity: 0.5; }
				.task-ghost-node:hover { opacity: 0.8; }
			`).appendTo("head");
		}

		node.find('.status-badge').on('click', (e) => { e.stopPropagation(); this.showStatusPicker(e, task.name); });
		node.find('.priority-badge').on('click', (e) => { e.stopPropagation(); this.showPriorityPicker(e, task.name); });
		node.find('.task-grid-row').on('mouseenter', () => { $(`.gantt .bar-wrapper[data-id="${task.name}"]`).addClass('highlight'); }).on('mouseleave', () => { $(`.gantt .bar-wrapper[data-id="${task.name}"]`).removeClass('highlight'); });

		if (task.children && task.children.length > 0) {
			const childContainer = node.find(".child-tasks-container");
			task.children.forEach((child) => this.renderTaskNode(child, childContainer, level + 1));
			if (isExpanded) childContainer.append(this.createQuickAddRow(task.name, level + 1));
		} else if (hasChildren && isExpanded) {
			node.find(".child-tasks-container").html('<div class="p-2 text-muted small"><i class="fa fa-spinner fa-spin mr-1"></i> Loading children...</div>');
			this.fetchChildren(task.name, node.find(".child-tasks-container"), level + 1);
			node.data("loaded", "true");
			node.attr("data-loaded", "true");
		}
	}

	fetchChildren(parentTaskId, container, level) {
		frappe.call({
			method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.get_task_children",
			args: { parent_task: parentTaskId },
			callback: (r) => {
				container.empty();
				if (r.message && r.message.length > 0) {
					r.message.forEach(child => { this.renderTaskNode(child, container, level); });
					container.append(this.createQuickAddRow(parentTaskId, level));
				} else {
					container.html('<div class="p-2 text-muted small">No subtasks.</div>');
				}
				this.initializeTaskSorting();
			}
		});
	}

	createQuickAddRow(parentTask, level) {
		const indent = level * 20;
		const row = $(`
			<div class="task-grid-row quick-add-row" style="background-color: var(--control-bg);">
				<div class="task-grid-cell" style="flex: 5;">
					<div style="padding-left: ${indent}px; display: flex; align-items: center; width: 100%;">
						<i class="fa fa-plus-circle text-muted mr-2" style="flex-shrink: 0;"></i>
						<input type="text" class="form-control input-xs quick-add-input" 
							placeholder="${parentTask ? 'Add subtask...' : 'Add root task...'}" 
							style="border: none; background: transparent; box-shadow: none; font-size: 12px; height: 24px; padding: 0;">
					</div>
				</div>
				<div class="task-grid-cell assignee-cell ${this.columnVisibility.owner ? "" : "hidden-column"}" data-column="owner"></div>
				<div class="task-grid-cell status-cell ${this.columnVisibility.status ? "" : "hidden-column"}" data-column="status"></div>
				<div class="task-grid-cell ${this.columnVisibility.priority ? "" : "hidden-column"}" data-column="priority"></div>
				<div class="task-grid-cell ${this.columnVisibility.start_date ? "" : "hidden-column"}" data-column="start_date"></div>
				<div class="task-grid-cell ${this.columnVisibility.due_date ? "" : "hidden-column"}" data-column="due_date"></div>
				<div class="task-grid-cell ${this.columnVisibility.progress ? "" : "hidden-column"}" data-column="progress"></div>
				<div class="task-grid-cell ${this.columnVisibility.duration ? "" : "hidden-column"}" data-column="duration"></div>
				${!this.readonly ? `<div class="task-grid-cell actions-cell" data-column="actions"></div>` : ''}
			</div>
		`);
		const input = row.find('.quick-add-input');
		input.on('keypress', (e) => { if (e.which === 13 && input.val().trim()) { this.saveInlineTask(input.val().trim(), parentTask); input.val(''); } });
		return row;
	}

	saveInlineTask(subject, parentTask) {
		frappe.call({
			method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.create_inline_task",
			args: { project: this.projectName, subject: subject, parent_task: parentTask },
			callback: (r) => { if (r.message && r.message.status === "success") { frappe.show_alert({ message: __("Task added: {0}", [subject]), indicator: 'green' }); this.fetchData(); } }
		});
	}

	getStatusBadge(task) {
		const colorMap = { 'Open': 'primary', 'Working': 'warning', 'Completed': 'success', 'Canceled': 'danger', 'On Hold': 'secondary', 'Active': 'primary', 'Paid': 'success', 'Invoiced': 'info' };
		let status = task.status;
		let color = colorMap[status] || 'secondary';
		if (task.is_overdue) { status = "OVERDUE"; color = "danger"; }
		return `<span class="badge badge-pill badge-${color} status-badge" style="cursor: pointer; text-transform: uppercase; font-size: 10px; padding: 4px 8px;">${status}</span>`;
	}

	getPriorityColor(priority) {
		const map = { 'Low': 'secondary', 'Medium': 'primary', 'High': 'warning', 'Urgent': 'danger' };
		return map[priority] || 'secondary';
	}

	showStatusPicker(event, taskName) {
		const statuses = this.taskStatusOptions.length > 0 ? this.taskStatusOptions : ['Open', 'Working', 'Completed', 'Canceled', 'On Hold'];
		const $menu = $('<div class="status-picker-menu dropdown-menu show" style="position: fixed; z-index: 2000; display: block;"></div>');
		statuses.forEach(s => { $('<a class="dropdown-item" href="#">' + s + '</a>').appendTo($menu).on('click', (e) => { e.preventDefault(); this.updateTaskStatus(taskName, s); $menu.remove(); }); });
		$('body').append($menu);
		this.positionPicker($menu, event);
		setTimeout(() => { $(document).one('click', () => $menu.remove()); }, 10);
	}

	showPriorityPicker(event, taskName) {
		const priorities = ['Low', 'Medium', 'High', 'Urgent'];
		const $menu = $('<div class="priority-picker-menu dropdown-menu show" style="position: fixed; z-index: 2000; display: block;"></div>');
		priorities.forEach(p => { $('<a class="dropdown-item" href="#">' + p + '</a>').appendTo($menu).on('click', (e) => { e.preventDefault(); this.updateTaskPriority(taskName, p); $menu.remove(); }); });
		$('body').append($menu);
		this.positionPicker($menu, event);
		setTimeout(() => { $(document).one('click', () => $menu.remove()); }, 10);
	}

	positionPicker($menu, event) {
		let top = event.pageY;
		let left = event.pageX;
		if (top + $menu.height() > $(window).height()) top -= $menu.height();
		if (left + $menu.width() > $(window).width()) left -= $menu.width();
		$menu.css({ top: top, left: left });
	}

	updateTaskStatus(taskName, newStatus) {
		frappe.call({ 
			method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.update_task_status", 
			args: { task_name: taskName, status: newStatus }, 
			callback: (r) => { 
				if (r.message && r.message.status === "success") {
					frappe.show_alert({ message: __("Task status updated successfully"), indicator: "green" });
					this.fetchData(); 
				} else if (r.message && r.message.status === "error") {
					frappe.show_alert({ message: __(r.message.message || "Failed to update task status"), indicator: "red" });
				} else {
					frappe.show_alert({ message: __("Failed to update task status"), indicator: "red" });
				}
			} 
		});
	}

	updateTaskPriority(taskName, newPriority) {
		frappe.call({ 
			method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.update_task_priority", 
			args: { task_name: taskName, priority: newPriority }, 
			callback: (r) => { 
				if (r.message && r.message.status === "success") {
					frappe.show_alert({ message: __("Task priority updated successfully"), indicator: "green" });
					this.fetchData(); 
				} else if (r.message && r.message.status === "error") {
					frappe.show_alert({ message: __(r.message.message || "Failed to update task priority"), indicator: "red" });
				} else {
					frappe.show_alert({ message: __("Failed to update task priority"), indicator: "red" });
				}
			} 
		});
	}

	bindEvents() {
		const me = this;
		this.wrapper.on("keyup", ".task-name-filter, .task-owner-filter", frappe.utils.debounce(() => me.applyFilters(), 300));
		this.wrapper.on("change", ".task-status-cb", () => {
			const checkedCount = me.wrapper.find(".task-status-cb:checked").length;
			const btn = me.wrapper.find("#statusFilterMenu");
			if (checkedCount === 0) btn.text("Statuses");
			else if (checkedCount === 1) btn.text(me.wrapper.find(".task-status-cb:checked").first().val());
			else btn.text(`${checkedCount} selected`);
			me.applyFilters();
		});
		this.wrapper.on("click", ".clear-filters-btn", () => {
			me.wrapper.find(".task-name-filter").val("");
			me.wrapper.find(".task-owner-filter").val("");
			me.wrapper.find(".task-status-cb").prop("checked", false);
			me.wrapper.find("#statusFilterMenu").text("Statuses");
			me.applyFilters();
		});

		this.wrapper.on("click", ".toggle-child-tasks", function () {
			const $icon = $(this);
			const $taskNode = $icon.closest(".task-node");
			const taskId = $taskNode.data("task-id");
			const $childContainer = $taskNode.find(".child-tasks-container");
			const isLoaded = $taskNode.data("loaded");

			if ($icon.hasClass("fa-caret-down")) {
				$icon.removeClass("fa-caret-down").addClass("fa-caret-right");
				$childContainer.hide();
				me.expandedTasks.delete(taskId);
			} else {
				$icon.removeClass("fa-caret-right").addClass("fa-caret-down");
				$childContainer.show();
				me.expandedTasks.add(taskId);
				if (!isLoaded || isLoaded === "false" || isLoaded === false) {
					const padding = $taskNode.find('.task-grid-row:first .task-grid-cell:first div').css('padding-left') || '0px';
					const level = parseInt(padding.replace('px', '')) / 20;
					me.fetchChildren(taskId, $childContainer, level + 1);
					$taskNode.data("loaded", "true");
				}
			}
			localStorage.setItem(`expandedTasks_${me.projectName}`, JSON.stringify(Array.from(me.expandedTasks)));
		});

		this.wrapper.on("change", ".column-toggle-cb", function () {
			const column = $(this).val();
			const isVisible = $(this).is(":checked");
			me.columnVisibility[column] = isVisible;
			localStorage.setItem(`taskTreeColumns_${frappe.session.user}`, JSON.stringify(me.columnVisibility));
			const $cells = me.wrapper.find(`.task-grid-cell[data-column="${column}"]`);
			isVisible ? $cells.removeClass("hidden-column") : $cells.addClass("hidden-column");
		});

		this.wrapper.on("click", ".editable-date a", function (e) {
			e.preventDefault(); if (me.readonly) return;
			const $cell = $(this).parent(); const taskId = $cell.data("task-id"); const field = $cell.data("field"); const originalDate = $cell.data("original-date");
			const d = new frappe.ui.Dialog({
				title: __("Set Date"),
				fields: [{ label: __("Date"), fieldname: "date", fieldtype: "Date", default: originalDate }],
				primary_action(values) { me.updatePendingChange(taskId, field, values.date); $cell.find("a").text(frappe.datetime.str_to_user(values.date)); $cell.addClass("unsaved-change"); me.showPendingControls(); d.hide(); },
			});
			d.show();
		});

		this.wrapper.on("click", ".editable-time a", function (e) {
			e.preventDefault(); if (me.readonly) return;
			const $cell = $(this).parent(); const taskId = $cell.data("task-id"); const field = $cell.data("field"); const originalValue = $cell.data("original-value");
			const d = new frappe.ui.Dialog({
				title: __("Set Expected Time"),
				fields: [{ label: __("Hours"), fieldname: "hours", fieldtype: "Float", default: originalValue }],
				primary_action(values) { me.updatePendingChange(taskId, field, values.hours); $cell.find("a").text(values.hours); $cell.addClass("unsaved-change"); me.showPendingControls(); d.hide(); },
			});
			d.show();
		});

		this.wrapper.on("click", ".save-changes-btn", () => me.savePendingChanges());
		this.wrapper.on("click", ".discard-changes-btn", () => { me.pendingChanges = {}; me.renderGrid(me.tasks); me.hidePendingControls(); });
		this.wrapper.on("click", ".add-task-btn", () => {
			const d = new frappe.ui.Dialog({
				title: __("New Task"),
				fields: [{ label: __("Subject"), fieldname: "subject", fieldtype: "Data", reqd: 1 }, { label: __("Parent Task"), fieldname: "parent_task", fieldtype: "Link", options: "Task", get_query: () => { return { filters: { project: me.projectName } }; } }],
				primary_action(values) { me.saveInlineTask(values.subject, values.parent_task); d.hide(); },
			});
			d.show();
		});

		this.wrapper.on("click", ".delete-task-btn", function () {
			const taskId = $(this).data("task-name"); const subject = $(this).data("task-subject");
			frappe.confirm(__("Delete task {0}: {1}?", [taskId, subject]), () => {
				frappe.call({ method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.delete_task", args: { task_name: taskId }, callback: (r) => { if (r.message && r.message.status === "success") { frappe.show_alert({ message: __("Task deleted successfully"), indicator: "green" }); me.fetchData(); } } });
			});
		});
		this.wrapper.on("click", ".save-order-btn", () => me.saveTaskOrder());

		this.wrapper.on("click", ".assignee-link", function (e) {
			e.preventDefault();
			if (me.readonly) return;
			const taskId = $(this).closest(".task-node").data("task-id");
			me.showAssigneePicker(taskId);
		});
	}

	showAssigneePicker(taskName) {
		const me = this;
		const d = new frappe.ui.Dialog({
			title: __("Assign Task"),
			fields: [
				{
					fieldname: "current_assignees_html",
					fieldtype: "HTML",
				},
				{
					label: __("Assign To"),
					fieldname: "user",
					fieldtype: "Link",
					options: "User",
				},
			],
			primary_action_label: __("Add"),
			primary_action(values) {
				if (!values.user) return;
				frappe.call({
					method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.add_task_assignee",
					args: { task_name: taskName, user_id: values.user },
					callback: (r) => {
						if (r.message && r.message.status === "success") {
							frappe.show_alert({ message: __("User assigned"), indicator: "green" });
							d.set_value("user", "");
							renderCurrentAssignees(r.message.assignees);
							me.fetchData();
						} else {
							frappe.show_alert({
								message: __((r.message && r.message.message) || "Failed to assign user"),
								indicator: "red",
							});
						}
					},
				});
			},
		});

		const renderCurrentAssignees = (assignees) => {
			const $field = d.fields_dict.current_assignees_html.$wrapper;
			if (!assignees || assignees.length === 0) {
				$field.html('<p class="text-muted small mb-2">No users assigned yet.</p>');
				return;
			}
			const rows = assignees
				.map(
					(a) => `
				<div class="d-flex justify-content-between align-items-center py-1 assignee-row">
					<span>${frappe.utils.escape_html(a.full_name || a.email)}</span>
					<button class="btn btn-xs btn-default remove-assignee-btn" data-user="${a.email}" title="${__("Remove")}">
						<i class="fa fa-times"></i>
					</button>
				</div>`
				)
				.join("");
			$field.html(`<div class="mb-2">${rows}</div>`);
			$field.find(".remove-assignee-btn").on("click", function () {
				const userId = $(this).data("user");
				frappe.call({
					method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.remove_task_assignee",
					args: { task_name: taskName, user_id: userId },
					callback: (r) => {
						if (r.message && r.message.status === "success") {
							frappe.show_alert({ message: __("Assignment removed"), indicator: "green" });
							renderCurrentAssignees(r.message.assignees);
							me.fetchData();
						} else {
							frappe.show_alert({
								message: __((r.message && r.message.message) || "Failed to remove assignment"),
								indicator: "red",
							});
						}
					},
				});
			});
		};

		renderCurrentAssignees([]);
		// Load current assignees from open ToDos for this task.
		frappe.db
			.get_list("ToDo", {
				filters: { reference_type: "Task", reference_name: taskName, status: "Open" },
				fields: ["allocated_to"],
				limit: 0,
			})
			.then((todos) => {
				const emails = [...new Set((todos || []).map((t) => t.allocated_to).filter(Boolean))];
				if (emails.length === 0) {
					renderCurrentAssignees([]);
					return;
				}
				frappe.db
					.get_list("User", {
						filters: { email: ["in", emails] },
						fields: ["email", "full_name"],
						limit: 0,
					})
					.then((users) => renderCurrentAssignees(users));
			});

		d.show();
	}

	updatePendingChange(taskId, field, value) { if (!this.pendingChanges[taskId]) this.pendingChanges[taskId] = {}; this.pendingChanges[taskId][field] = value; }
	showPendingControls() { this.wrapper.find(".task-pending-changes-controls").show(); }
	hidePendingControls() { this.wrapper.find(".task-pending-changes-controls").hide(); }

	savePendingChanges() {
		const updates = []; for (const taskId in this.pendingChanges) updates.push({ name: taskId, ...this.pendingChanges[taskId] });
		if (updates.length === 0) return;
		this.wrapper.find(".task-saving-indicator").show();
		frappe.call({
			method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.update_multiple_docs",
			args: { project_updates: "{}", task_updates: JSON.stringify(updates.reduce((acc, curr) => { const { name, ...rest } = curr; acc[name] = rest; return acc; }, {})) },
			callback: (r) => { 
				this.wrapper.find(".task-saving-indicator").hide(); 
				if (r.message && r.message.status === "success") { 
					frappe.show_alert({ message: __("Changes saved successfully"), indicator: "green" }); 
					this.pendingChanges = {}; 
					this.hidePendingControls(); 
					this.fetchData(); 
				} else if (r.message && r.message.status === "error") {
					frappe.show_alert({ message: __(r.message.message || "Failed to save changes"), indicator: "red" });
				} else {
					frappe.show_alert({ message: __("Failed to save changes"), indicator: "red" });
				}
			}
		});
	}

	initializeTaskSorting() {
		const me = this; if (this.readonly) return;
		this.sortableInstances.forEach((s) => s.destroy()); this.sortableInstances = [];
		const containers = this.wrapper.find(".task-grid-body, .child-tasks-container");
		containers.each(function () {
			const sortable = new Sortable(this, {
				group: "task-tree",
				handle: ".task-drag-handle",
				draggable: ".task-node",
				animation: 150,
				fallbackOnBody: true,
				swapThreshold: 0.65,
				onStart: function () { me._dropMode = null; me._dropTargetId = null; },
				onMove: function (evt, originalEvent) { return me.handleDragMove(evt, originalEvent); },
				onEnd: function (evt) { me.handleDragEnd(evt); },
			});
			me.sortableInstances.push(sortable);
		});
	}

	clearDropIndicators() {
		this.wrapper.find(".drop-as-child").removeClass("drop-as-child");
	}

	// Decide drag intent based on cursor position over the hovered task row:
	//   middle band  -> nest the dragged task as a child of the hovered task
	//   top/bottom   -> reorder as a sibling (let SortableJS place it)
	handleDragMove(evt, originalEvent) {
		const related = evt.related;
		// Hovering a non-task element (e.g. the quick-add row) or an empty
		// container: fall back to SortableJS's default placement.
		if (!related || !$(related).hasClass("task-node")) {
			this.clearDropIndicators();
			this._dropMode = null;
			this._dropTargetId = null;
			return true;
		}

		const dragged = evt.dragged;
		// Never allow a task to be dropped into itself or one of its own descendants.
		if (related === dragged || $.contains(dragged, related)) {
			this.clearDropIndicators();
			this._dropMode = null;
			this._dropTargetId = null;
			return false;
		}

		// Measure intent against the hovered node's OWN row, not the whole
		// .task-node. An expanded parent's .task-node spans its entire subtree, so
		// using its bounding box would stretch the "nest" band far below the parent
		// row and nesting under an expanded parent would never trigger (only leaves
		// would nest). The first .task-grid-row is the node's own row.
		const rowEl = related.querySelector(".task-grid-row");
		const rect = (rowEl || related).getBoundingClientRect();
		const clientY = originalEvent.clientY != null
			? originalEvent.clientY
			: (originalEvent.touches && originalEvent.touches[0] ? originalEvent.touches[0].clientY : rect.top);
		const relY = clientY - rect.top;
		const h = rect.height || 1;

		this.clearDropIndicators();
		if (relY > h * 0.3 && relY < h * 0.7) {
			// Nest: highlight the target row and suppress the sibling reorder.
			$(related).children(".task-grid-row").addClass("drop-as-child");
			this._dropMode = "child";
			this._dropTargetId = $(related).data("task-id");
			return false;
		}

		// Reorder as sibling.
		this._dropMode = "reorder";
		this._dropTargetId = null;
		return true;
	}

	handleDragEnd(evt) {
		this.clearDropIndicators();
		const me = this;
		const draggedNode = evt.item;
		const mode = this._dropMode;
		const targetId = this._dropTargetId;
		const reordered = evt.from !== evt.to || evt.oldIndex !== evt.newIndex;

		this._dropMode = null;
		this._dropTargetId = null;

		// CRITICAL: never mutate the DOM synchronously inside onEnd. SortableJS is
		// still in the middle of its own drop cleanup when this fires; re-parenting
		// the dragged node here leaves its internals pointing at a detached element
		// and throws inside Sortable.min.js _onDrop:
		//   "Cannot read properties of null (reading 'removeEventListener')".
		// Deferring to the next tick lets Sortable finish first, then we re-parent
		// on a clean DOM and persist.
		setTimeout(() => {
			let changed = reordered;

			if (mode === "child" && targetId) {
				const $target = me.wrapper.find(`.task-node[data-task-id="${targetId}"]`).first();
				if ($target.length && !$.contains(draggedNode, $target[0]) && $target[0] !== draggedNode) {
					let $childContainer = $target.children(".child-tasks-container");
					if (!$childContainer.length) {
						$childContainer = $('<div class="child-tasks-container"></div>').appendTo($target);
					}
					// Drop a placeholder/"No subtasks." message if present, then nest.
					$childContainer.children(".text-muted").remove();
					$childContainer.show().append(draggedNode);
					// Reflect the now-expanded parent so the re-render keeps it open.
					const $icon = $target.children(".task-grid-row").find(".fa-circle, .toggle-child-tasks").first();
					$icon.removeClass("fa-circle text-extra-muted fa-caret-right").addClass("fa-caret-down toggle-child-tasks");
					$target.attr("data-loaded", "true").data("loaded", "true");
					me.expandedTasks.add(targetId);
					localStorage.setItem(`expandedTasks_${me.projectName}`, JSON.stringify(Array.from(me.expandedTasks)));
					changed = true;
				}
			}

			// Persist so the tree re-indents itself (saveTaskOrder re-fetches).
			if (changed) me.saveTaskOrder();
		}, 0);
	}

	saveTaskOrder() {
		const me = this; const tasksToUpdate = [];
		const processLevel = (container, parentTaskId = null) => {
			$(container).children(".task-node").each(function (index) {
				const taskId = $(this).data("task-id");
				tasksToUpdate.push({ name: taskId, parent_task: parentTaskId, custom_subtask_order: index + 1 });
				const childContainer = $(this).children(".child-tasks-container");
				if (childContainer.length > 0) processLevel(childContainer[0], taskId);
			});
		};
		processLevel(this.wrapper.find(".task-grid-body")[0]);
		this.wrapper.find(".task-saving-indicator").show();
		frappe.call({ method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.update_task_structure", args: { project_name: this.projectName, tasks: JSON.stringify(tasksToUpdate) }, callback: (r) => { this.wrapper.find(".task-saving-indicator").hide(); if (r.message && r.message.status === "success") { frappe.show_alert({ message: __("Task order saved successfully"), indicator: "green" }); this.wrapper.find(".save-order-btn").hide(); this.fetchData(); } } });
	}
};
