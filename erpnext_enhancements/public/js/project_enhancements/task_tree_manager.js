/**
 * TaskTreeManager — interactive, hierarchical task grid.
 *
 * Targets: rendered into a host element on the Project form's Scope tab
 * (`custom_tasks_html`).
 * Loaded via: hooks.py `app_include_js` (global), so the class is always defined;
 * the caller (project_form_script.js) also `frappe.require`s it defensively before
 * constructing.
 *
 * Renders an Asana-style tree of Tasks for one Project with: expand/collapse with
 * lazy child loading, inline editing of dates / expected time / status / priority,
 * quick-add rows, name/owner/status filtering, per-user column visibility, and
 * SortableJS drag-and-drop that both reorders siblings AND re-parents (nest) tasks
 * — drop intent is decided by where in the target row you release (see
 * handleDragMove). All mutations round-trip through the project_dashboard.py
 * whitelisted methods. Expanded-state and column choices persist in localStorage.
 *
 * Scope-tab flows (v1.777.0, api/scope_tasks.py), each an in-page dialog so the
 * browser's Back and Forward are never touched: "Add task" (the full dialog:
 * subject, parent, group, priority, type, assignee, dates, hours, description;
 * "Add and add another" keeps it open) and the per-row "+" that preselects the
 * row as parent; "Add from template" (every task of a Project Template, with a
 * preview of its tree and dependencies, under a chosen task or the top level,
 * tasks already there skipped and listed); and "Select", which shows a checkbox
 * per row for "Delete selected" (subtasks under a picked group go too; a task
 * ERPNext refuses to delete is listed with its reason).
 *
 * Export / print (exportTasks, printTasks) re-fetch the WHOLE tree server-side
 * rather than reading `this.tasks` — the grid loads children lazily per level,
 * so the client only holds the branches the user expanded. Shared branding and
 * the print window live in public/js/export_utils.js.
 *
 * Drag-to-parent (v1.784.0, Nikolas 2026-11-03: "Make any task a parent by simple
 * drag and drop"): while a row is dragged, the row under the pointer says what the
 * drop will do -- a "Make subtask of <task>" wash on its middle half, an insertion
 * line at its top or bottom edge for a reorder -- and a hint bar above the grid says
 * the same in words. "Move under..." on each row is the no-mouse path (a dialog).
 * The save is api/scope_tasks.move_tasks: a leaf task dropped on becomes a group,
 * only the rows that changed are saved, and ERPNext's refusal comes back as a
 * message the tree shows in a dialog and then re-fetches, so the tree never sits
 * showing a move that was not saved. A task with subtasks shows its dates read-only
 * here: they follow its subtasks (task_enhancements/parent_dates.py).
 *
 * Assets: styles ship globally in desk_addons.bundle.css (task_tree import);
 * SortableJS 1.15.6 is vendored at public/js/project_enhancements/lib/ (a UMD build
 * that sets window.Sortable, like frappe-gantt there) and loaded on demand in
 * loadAssets(). It used to come from a CDN on its unpinned "latest" tag: a
 * third-party script on every Project form whose version could change under us.
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

		// Drag-to-resize column widths (per user). The first (Task) column stays
		// elastic and the tiny Actions column is left alone; the rest get a right-
		// edge drag handle. Widths are applied as a fixed flex-basis on the cells
		// carrying the matching `data-column`, so no width is set until the user
		// drags — the CSS defaults (task_tree.css) remain the baseline, and
		// "Reset column widths" clears the overrides. Guarded so a missing bundle
		// asset can't break the whole tree.
		const ColumnResizer = (erpnext_enhancements.dashboard_components || {}).ColumnResizer;
		this.columnResizer = ColumnResizer
			? new ColumnResizer(
					`taskTreeWidths_${frappe.session.user}`,
					[
						{ key: "owner", minWidth: 90, maxWidth: 360 },
						{ key: "status", minWidth: 90, maxWidth: 320 },
						{ key: "priority", minWidth: 80, maxWidth: 280 },
						{ key: "start_date", minWidth: 90, maxWidth: 260 },
						{ key: "due_date", minWidth: 90, maxWidth: 260 },
						{ key: "progress", minWidth: 90, maxWidth: 320 },
						{ key: "duration", minWidth: 90, maxWidth: 280 },
					],
					{
						applyWidth: (root, key, px) => {
							const $cells = root.find(`.task-grid-cell[data-column="${key}"]`);
							$cells.css("flex", px == null ? "" : `0 1 ${px}px`);
						},
						measureWidth: (root, key) => {
							const $cell = root
								.find(`.task-grid-header .task-grid-cell[data-column="${key}"]`)
								.first();
							return $cell.length ? $cell.outerWidth() : 120;
						},
					}
			  )
			: null;

		this.init();
	}

	applyColumnWidths() {
		if (this.columnResizer) this.columnResizer.apply(this.wrapper);
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
		// Styles already ship in desk_addons.bundle.css (global include); the
		// old frappe.require of a raw task_tree.css path 404'd with a MIME
		// error since the bundle move. Only SortableJS loads on demand.
		// SortableJS is vendored and pinned (v1.784.0). The file name carries the
		// version: raw /assets paths are served immutable for a year, so a new
		// release must be a new file name to reach a device that cached this one.
		return new Promise((resolve) => {
			if (window.Sortable) {
				resolve();
				return;
			}
			frappe.require(erpnext_enhancements.TaskTreeManager.SORTABLE_ASSET, () => resolve());
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
                            <span class="task-drag-hint text-muted small mr-2" style="display: none;" role="status"><i class="fa fa-info-circle mr-1"></i>${__("Drop on the middle of a row to make this a subtask of it; near its top or bottom edge to put it before or after.")}</span>
                            ${
								this.readonly
									? ""
									: `<button class="btn btn-sm btn-primary mr-2 add-task-btn" title="${__("Add a task without leaving this page")}">${__("Add task")}</button>
                            <button class="btn btn-sm btn-default mr-2 add-template-btn" title="${__("Add every task of a Project Template, under a task or at the top")}"><i class="fa fa-clone mr-1"></i> ${__("Add from template")}</button>
                            <button class="btn btn-sm btn-default select-tasks-btn" title="${__("Pick tasks to delete")}">${__("Select")}</button>
                            <div class="task-select-controls ml-2" style="display: none;">
                                <div class="btn-group btn-group-sm">
                                    <button type="button" class="btn btn-danger delete-selected-btn" disabled>${__("Delete selected")}</button>
                                    <button type="button" class="btn btn-default select-all-btn">${__("Select all")}</button>
                                    <button type="button" class="btn btn-default cancel-select-btn">${__("Cancel")}</button>
                                </div>
                            </div>`
							}
                        </div>
                        <div class="d-flex align-items-center">
                            <div class="dropdown mr-2">
                                <button class="btn btn-sm btn-default dropdown-toggle task-export-toggle" type="button" data-toggle="dropdown" aria-haspopup="true" aria-expanded="false" title="Export or print this task list">
                                    <i class="fa fa-download"></i>
                                </button>
                                <div class="dropdown-menu dropdown-menu-right">
                                    <a href="#" class="dropdown-item task-print-btn"><i class="fa fa-print mr-1"></i> Print / Save as PDF</a>
                                    <div class="dropdown-divider"></div>
                                    <a href="#" class="dropdown-item task-export-btn" data-format="csv"><i class="fa fa-file-text-o mr-1"></i> CSV data</a>
                                    <a href="#" class="dropdown-item task-export-btn" data-format="xlsx"><i class="fa fa-file-excel-o mr-1"></i> Excel data</a>
                                </div>
                            </div>
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
                                    <div class="dropdown-divider"></div>
                                    <a href="#" class="dropdown-item px-0 reset-col-widths-btn"><i class="fa fa-arrows-h mr-1"></i> Reset column widths</a>
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

		// Attach resize handles to the (persistent) header cells and apply any
		// saved widths. The header is built once here; renderGrid/fetchChildren
		// only rebuild the body, so they re-apply widths but don't re-attach.
		if (this.columnResizer) {
			this.columnResizer.attach_handles(
				this.wrapper,
				this.wrapper.find(".task-grid-header .task-grid-cell"),
				($cell) => $cell.attr("data-column")
			);
			this.applyColumnWidths();
		}
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

	/**
	 * Export / print the project's task tree.
	 *
	 * Both pull the WHOLE tree from the server rather than reading `this.tasks`.
	 * The grid loads children one level at a time as the user expands them, so
	 * the client only ever holds the branches somebody clicked into — a file
	 * built from that would silently omit most of the project. The screen
	 * filters are not applied either: this produces a schedule document for the
	 * project, not a snapshot of the current search box.
	 */
	exportTasks(format) {
		frappe.call({
			method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.export_project_tasks",
			args: {
				project: this.projectName,
				file_format: format === "xlsx" ? "xlsx" : "csv",
				title: `${this.projectName}-tasks`,
			},
			callback: (r) => {
				erpnext_enhancements.export_utils.download_payload(r && r.message);
			},
			error: () => {
				frappe.show_alert({ message: __("Could not export the task list."), indicator: "red" });
			},
		});
	}

	printTasks() {
		frappe.call({
			method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.get_project_task_tree",
			args: { project: this.projectName },
			callback: async (r) => {
				const rows = (r && r.message) || [];
				if (!rows.length) {
					frappe.show_alert({ message: __("No tasks to print."), indicator: "orange" });
					return;
				}
				const U = erpnext_enhancements.export_utils;
				U.print_document(this.buildPrintTable(rows), {
					title: this.projectName,
					subtitle: __("Task List"),
					brand: await U.build_brand({}),
					orientation: "portrait",
					footer: __("{0} tasks", [rows.length]),
				});
			},
		});
	}

	/** The task tree as a printable table; hierarchy shown by indentation. */
	buildPrintTable(rows) {
		const esc = erpnext_enhancements.export_utils.escape_html;
		const date = (v) => (v ? frappe.datetime.str_to_user(v) : "");
		const body = rows
			.map((t) => {
				// Indent in em so it scales with the print font size; a nested
				// task must still read as nested once the browser shrinks the
				// page to fit.
				const indent = `padding-left:${t.level * 1.2}em;`;
				const weight = t.level === 0 ? "font-weight:600;" : "";
				const name = t.is_milestone ? `◆ ${t.subject}` : t.subject;
				return (
					`<tr>` +
					`<td style="${indent}${weight}">${esc(name)}</td>` +
					`<td>${esc(t.status)}</td>` +
					`<td>${esc(t.priority)}</td>` +
					`<td>${esc(t.assigned_to)}</td>` +
					`<td style="white-space:nowrap;">${esc(date(t.exp_start_date))}</td>` +
					`<td style="white-space:nowrap;">${esc(date(t.exp_end_date))}</td>` +
					`<td style="text-align:right;">${t.progress ? Math.round(t.progress) + "%" : ""}</td>` +
					`</tr>`
				);
			})
			.join("");
		return (
			`<table><thead><tr>` +
			`<th>${__("Task")}</th><th>${__("Status")}</th><th>${__("Priority")}</th>` +
			`<th>${__("Assigned To")}</th><th>${__("Start")}</th><th>${__("Due")}</th>` +
			`<th style="text-align:right;">${__("%")}</th>` +
			`</tr></thead><tbody>${body}</tbody></table>`
		);
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
			this.applyColumnWidths();
			return;
		}
		tasks.forEach((task) => this.renderTaskNode(task, gridBody, 0));
		gridBody.prepend(this.createQuickAddRow(null, 0));
		this.initializeTaskSorting();
		this.applyColumnWidths();
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
							${this.readonly ? "" : `<input type="checkbox" class="task-select-cb mr-2" data-task-id="${task.name}" title="${__("Select")}">`}
							<i class="fa fa-bars task-drag-handle mr-2 text-muted" style="cursor: grab; flex-shrink: 0;"></i>
							<i class="fa fa-fw ${iconClass}" style="cursor: pointer; flex-shrink: 0; font-size: 10px;"></i>
							${milestoneIcon}${recurringIcon}<a href="/desk/task/${encodeURIComponent(task.name)}" class="task-name-cell-text" title="${frappe.utils.escape_html(task.subject || "")}">${frappe.utils.escape_html(task.subject || "")}</a>
						</div>
					</div>
					<div class="task-grid-cell assignee-cell ${this.columnVisibility.owner ? "" : "hidden-column"}" data-column="owner"><a href="#" class="assignee-link">${task.assigned_to || "Unassigned"}</a></div>
					<div class="task-grid-cell status-cell ${this.columnVisibility.status ? "" : "hidden-column"}" data-column="status">${statusBadge}</div>
					<div class="task-grid-cell priority-cell ${this.columnVisibility.priority ? "" : "hidden-column"}" data-column="priority">
						<span class="priority-badge badge badge-pill badge-${this.getPriorityColor(task.priority)}" style="cursor: pointer; text-transform: uppercase; font-size: 10px; padding: 4px 8px;">${task.priority || "Medium"}</span>
					</div>
					${this.renderDateCell(task, "exp_start_date", "start_date", start_date, hasChildren)}
					${this.renderDateCell(task, "exp_end_date", "due_date", end_date, hasChildren)}
					<div class="task-grid-cell ${this.columnVisibility.progress ? "" : "hidden-column"}" data-column="progress"><div class="progress" style="height: 15px; width: 100%;"><div class="progress-bar" role="progressbar" style="width: ${progress}%;" aria-valuenow="${progress}" aria-valuemin="0" aria-valuemax="100">${progress}%</div></div></div>
					<div class="task-grid-cell editable-time ${this.columnVisibility.duration ? "" : "hidden-column"}" data-field="expected_time" data-task-id="${task.name}" data-original-value="${task.expected_time || 0}" data-column="duration"><a href="#">${task.expected_time || 0}</a></div>
					${!this.readonly ? `<div class="task-grid-cell actions-cell" data-column="actions"><button class="btn btn-xs btn-default add-subtask-btn mr-1" title="${__("Add a subtask")}" data-task-name="${task.name}" data-task-subject="${frappe.utils.escape_html(task.subject || "")}"><i class="fa fa-plus"></i></button><button class="btn btn-xs btn-default move-task-btn mr-1" title="${__("Move under another task, or to the top level")}" data-task-name="${task.name}" data-task-subject="${frappe.utils.escape_html(task.subject || "")}"><i class="fa fa-indent"></i></button><button class="btn btn-xs btn-danger delete-task-btn" title="Delete Task" data-task-name="${task.name}" data-task-subject="${frappe.utils.escape_html(task.subject || "")}"><i class="fa fa-trash"></i></button></div>` : ''}
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
				this.applyColumnWidths();
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

	/**
	 * One date cell. A task with subtasks takes its dates from them (v1.784.0:
	 * earliest start, latest end, kept by the Task hooks), so its cell is plain
	 * text with a tooltip and no link: the inline editor binds to `.editable-date a`
	 * and never fires, and the server refuses a direct write anyway.
	 */
	renderDateCell(task, field, column, text, derivedFromChildren) {
		const shown = this.columnVisibility[column] ? "" : "hidden-column";
		if (derivedFromChildren) {
			const title = __("Follows its subtasks: the earliest start and the latest end among them.");
			return `<div class="task-grid-cell derived-date ${shown}" data-field="${field}" data-task-id="${task.name}" data-column="${column}" title="${title}"><span class="text-muted">${text}</span><i class="fa fa-level-down ml-1 text-extra-muted" aria-hidden="true"></i></div>`;
		}
		return `<div class="task-grid-cell editable-date ${shown}" data-field="${field}" data-task-id="${task.name}" data-original-date="${task[field] || ""}" data-column="${column}"><a href="#">${text}</a></div>`;
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

		this.wrapper.on("click", ".task-export-btn", function (e) {
			e.preventDefault();
			me.exportTasks($(this).data("format"));
		});
		this.wrapper.on("click", ".task-print-btn", (e) => {
			e.preventDefault();
			me.printTasks();
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

		this.wrapper.on("click", ".reset-col-widths-btn", function (e) {
			e.preventDefault();
			if (me.columnResizer) me.columnResizer.reset(me.wrapper);
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
		// The three Scope-tab flows (v1.777.0). Every one is a frappe.ui.Dialog: no route
		// change, no history entry, so the browser's Back and Forward keep working.
		this.wrapper.on("click", ".add-task-btn", () => me.openTaskDialog({}));
		this.wrapper.on("click", ".add-subtask-btn", function () {
			me.openTaskDialog({ parent: $(this).data("task-name"), parentSubject: $(this).data("task-subject") });
		});
		this.wrapper.on("click", ".move-task-btn", function () {
			me.openMoveDialog($(this).data("task-name"), $(this).data("task-subject"));
		});
		this.wrapper.on("click", ".add-template-btn", () => me.openTemplateDialog());
		this.wrapper.on("click", ".select-tasks-btn", () => me.setSelectMode(true));
		this.wrapper.on("click", ".cancel-select-btn", () => me.setSelectMode(false));
		this.wrapper.on("click", ".select-all-btn", () => {
			me.wrapper.find(".task-select-cb:visible").prop("checked", true);
			me.updateSelection();
		});
		this.wrapper.on("change", ".task-select-cb", () => me.updateSelection());
		this.wrapper.on("click", ".delete-selected-btn", () => me.deleteSelected());

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

	// ------------------------------------------------------------------
	// Scope-tab flows (v1.777.0): add a task, add a template, delete many.
	// Server: api/scope_tasks.py. None of these leaves the Project page.
	// ------------------------------------------------------------------

	/** Expand `parent` so a task just added under it is on screen after the refresh. */
	revealParent(parent) {
		if (!parent) return;
		this.expandedTasks.add(parent);
		localStorage.setItem(`expandedTasks_${this.projectName}`, JSON.stringify(Array.from(this.expandedTasks)));
	}

	/** The full in-page "Add task" dialog. `parent` preselects the node the user clicked. */
	openTaskDialog({ parent = null, parentSubject = "" } = {}) {
		const me = this;
		const taskQuery = () => ({ filters: { project: me.projectName, is_template: 0 } });
		const d = new frappe.ui.Dialog({
			title: parent ? __("Add a subtask of {0}", [parentSubject || parent]) : __("Add a task"),
			size: "large",
			fields: [
				{ label: __("Subject"), fieldname: "subject", fieldtype: "Data", reqd: 1 },
				{
					label: __("Parent task"),
					fieldname: "parent_task",
					fieldtype: "Link",
					options: "Task",
					default: parent || "",
					get_query: taskQuery,
					description: __("Leave empty for a top-level task. A parent that is not yet a group becomes one."),
				},
				{ label: __("This task holds subtasks (group)"), fieldname: "is_group", fieldtype: "Check" },
				{ fieldtype: "Column Break" },
				{
					label: __("Priority"),
					fieldname: "priority",
					fieldtype: "Select",
					options: ["Low", "Medium", "High", "Urgent"],
					default: "Medium",
				},
				{ label: __("Type"), fieldname: "type", fieldtype: "Link", options: "Task Type" },
				{ label: __("Assign to"), fieldname: "assign_to", fieldtype: "Link", options: "User", get_query: () => ({ filters: { user_type: "System User", enabled: 1 } }) },
				{ fieldtype: "Section Break", label: __("Dates and hours") },
				{ label: __("Expected start"), fieldname: "exp_start_date", fieldtype: "Date" },
				{ fieldtype: "Column Break" },
				{ label: __("Expected end"), fieldname: "exp_end_date", fieldtype: "Date" },
				{ fieldtype: "Column Break" },
				{ label: __("Expected hours"), fieldname: "expected_time", fieldtype: "Float" },
				{ fieldtype: "Section Break" },
				{ label: __("Description"), fieldname: "description", fieldtype: "Text Editor" },
			],
			primary_action_label: __("Add task"),
			primary_action: (values) => save(values, true),
			secondary_action_label: __("Add and add another"),
			secondary_action: () => {
				const values = d.get_values();
				if (values) save(values, false);
			},
		});

		const save = (values, close) => {
			const start = values.exp_start_date;
			const end = values.exp_end_date;
			if (start && end && start > end) {
				frappe.msgprint({ message: __("The expected end is before the expected start."), indicator: "orange" });
				return;
			}
			// Both footer buttons: a double click on "Add and add another" would post twice.
			const buttons = d.$wrapper.find(".modal-footer button");
			buttons.prop("disabled", true);
			frappe.call({
				method: "erpnext_enhancements.api.scope_tasks.add_task",
				args: { project: me.projectName, values: JSON.stringify(values) },
				always: () => buttons.prop("disabled", false),
				callback: (r) => {
					const made = r.message;
					if (!made || !made.name) return;
					const link = `<a href="/desk/task/${encodeURIComponent(made.name)}">${frappe.utils.escape_html(made.subject)}</a>`;
					frappe.show_alert({ message: __("Added {0}", [link]), indicator: "green" }, 7);
					(made.converted || []).forEach((name) =>
						frappe.show_alert({ message: __("{0} is now a group task.", [name]), indicator: "blue" }, 7)
					);
					(made.warnings || []).forEach((text) => frappe.show_alert({ message: text, indicator: "orange" }, 10));
					me.revealParent(made.parent_task);
					me.fetchData();
					if (close) {
						d.hide();
					} else {
						d.set_value("subject", "");
						d.set_value("description", "");
						d.fields_dict.subject.$input.focus();
					}
				},
			});
		};
		d.show();
		setTimeout(() => d.fields_dict.subject.$input.focus(), 200);
	}

	/**
	 * "Move under...": the no-mouse path to re-parenting (v1.784.0). A Link to a
	 * task of this project, or empty for the top level; the task lands after the
	 * new parent's last subtask, and a parent that is not yet a group becomes one.
	 * The server refuses the task itself and anything under it, with a message.
	 */
	openMoveDialog(taskId, subject) {
		const me = this;
		const d = new frappe.ui.Dialog({
			title: __("Move {0}", [subject || taskId]),
			fields: [
				{
					label: __("Under"),
					fieldname: "parent_task",
					fieldtype: "Link",
					options: "Task",
					get_query: () => ({
						filters: { project: me.projectName, is_template: 0, name: ["!=", taskId] },
					}),
					description: __(
						"Leave empty to move it to the top level. A task that is not yet a group becomes one; the task goes after that task's last subtask. Its own subtasks are listed but refused."
					),
				},
			],
			primary_action_label: __("Move"),
			primary_action: (values) => {
				d.get_primary_btn().prop("disabled", true);
				me.revealParent(values.parent_task || null);
				me.saveStructure([{ name: taskId, parent_task: values.parent_task || null }], {
					always: () => d.get_primary_btn().prop("disabled", false),
					done: () => d.hide(),
				});
			},
		});
		d.show();
	}

	/** "Add from template": pick a Project Template, see its tasks, pick where they go. */
	openTemplateDialog() {
		const me = this;
		frappe.call({
			method: "erpnext_enhancements.api.scope_tasks.get_template_catalog",
			args: { project: me.projectName },
			callback: (r) => {
				const catalog = r.message;
				if (!catalog || !catalog.templates || !catalog.templates.length) {
					frappe.msgprint(__("There are no enabled Project Templates to add from."));
					return;
				}
				me.showTemplateDialog(catalog);
			},
		});
	}

	showTemplateDialog(catalog) {
		const me = this;
		const byName = {};
		catalog.templates.forEach((t) => (byName[t.name] = t));
		const options = catalog.templates.map((t) => ({
			label: t.project_type ? `${t.name} (${t.project_type}, ${t.count})` : `${t.name} (${t.count})`,
			value: t.name,
		}));
		const today = catalog.today || frappe.datetime.get_today();
		// Default start: the project's expected start when it is still ahead, else today.
		const defaultStart =
			catalog.expected_start_date && catalog.expected_start_date >= today ? catalog.expected_start_date : today;
		const d = new frappe.ui.Dialog({
			title: __("Add tasks from a template"),
			size: "large",
			fields: [
				{
					label: __("Project Template"),
					fieldname: "template",
					fieldtype: "Select",
					options: options,
					reqd: 1,
					default: options[0].value,
					onchange: () => render(),
				},
				{ fieldtype: "Column Break" },
				{
					label: __("Put them under"),
					fieldname: "parent_task",
					fieldtype: "Link",
					options: "Task",
					get_query: () => ({ filters: { project: me.projectName, is_template: 0 } }),
					description: __("Leave empty to add them at the top level."),
				},
				{ fieldtype: "Column Break" },
				{
					label: __("Start on"),
					fieldname: "start_date",
					fieldtype: "Date",
					reqd: 1,
					default: defaultStart,
					description: __("Each task is dated from here by the template's days."),
				},
				{ fieldtype: "Section Break" },
				{ fieldname: "preview", fieldtype: "HTML" },
			],
			primary_action_label: __("Add tasks"),
			primary_action: (values) => {
				d.get_primary_btn().prop("disabled", true);
				frappe.call({
					method: "erpnext_enhancements.api.scope_tasks.import_template",
					args: {
						project: me.projectName,
						template: values.template,
						parent_task: values.parent_task || null,
						start_date: values.start_date,
					},
					always: () => d.get_primary_btn().prop("disabled", false),
					callback: (r) => {
						const out = r.message;
						if (!out) return;
						d.hide();
						if (out.count) {
							frappe.show_alert({ message: __("Added {0} tasks from {1}.", [out.count, values.template]), indicator: "green" }, 7);
						}
						(out.converted || []).forEach((name) =>
							frappe.show_alert({ message: __("{0} is now a group task.", [name]), indicator: "blue" }, 7)
						);
						if (out.skipped && out.skipped.length) {
							const rows = out.skipped
								.map((s) => `<li>${frappe.utils.escape_html(s.subject)} <span class="text-muted">(${frappe.utils.escape_html(s.existing)})</span></li>`)
								.join("");
							frappe.msgprint({
								title: out.count ? __("Already there, left alone") : __("Nothing to add"),
								indicator: out.count ? "blue" : "orange",
								message: `<p>${__("These template tasks already exist under the same parent:")}</p><ul>${rows}</ul>`,
							});
						}
						me.revealParent(values.parent_task);
						me.fetchData();
					},
				});
			},
		});

		const render = () => {
			const picked = byName[d.get_value("template")];
			const $preview = d.fields_dict.preview.$wrapper;
			if (!picked) {
				$preview.empty();
				return;
			}
			const esc = frappe.utils.escape_html;
			const rows = picked.rows
				.map((row) => {
					const icon = row.is_group ? "fa-folder-o" : "fa-circle-o";
					const bits = [];
					if (row.start || row.duration) bits.push(__("day {0}, {1} d", [row.start, row.duration]));
					if (row.hours) bits.push(__("{0} h", [row.hours]));
					if (row.waits.length) bits.push(__("after {0}", [row.waits.map(esc).join(", ")]));
					return (
						`<div class="scope-template-row" style="padding-left: ${row.level * 18}px;">` +
						`<i class="fa fa-fw ${icon} text-muted mr-1"></i>${esc(row.subject)}` +
						(bits.length ? `<span class="text-muted small ml-2">${bits.join(" · ")}</span>` : "") +
						`</div>`
					);
				})
				.join("");
			$preview.html(
				`<div class="scope-template-preview"><div class="text-muted small mb-2">${__(
					"{0} tasks. A task that already exists under the same parent with the same subject is skipped.",
					[picked.count]
				)}</div>${rows || `<div class="text-muted">${__("This template has no tasks.")}</div>`}</div>`
			);
		};
		d.show();
		render();
	}

	setSelectMode(on) {
		this.wrapper.find(".task-tree-manager").toggleClass("select-mode", !!on);
		this.wrapper.find(".task-select-controls").toggle(!!on);
		this.wrapper.find(".select-tasks-btn, .add-task-btn, .add-template-btn").toggle(!on);
		if (!on) this.wrapper.find(".task-select-cb").prop("checked", false);
		this.updateSelection();
	}

	selectedTaskIds() {
		return this.wrapper
			.find(".task-select-cb:checked")
			.map(function () {
				return $(this).data("task-id");
			})
			.get();
	}

	updateSelection() {
		const n = this.selectedTaskIds().length;
		const $btn = this.wrapper.find(".delete-selected-btn");
		$btn.prop("disabled", n === 0).text(n ? __("Delete selected ({0})", [n]) : __("Delete selected"));
	}

	/** Count first (subtasks under a picked group count too), confirm, then delete in one request. */
	deleteSelected() {
		const me = this;
		const ids = this.selectedTaskIds();
		if (!ids.length) return;
		frappe.call({
			method: "erpnext_enhancements.api.scope_tasks.delete_preview",
			args: { project: me.projectName, tasks: JSON.stringify(ids) },
			callback: (r) => {
				const p = r.message;
				if (!p) return;
				const esc = frappe.utils.escape_html;
				let message = `<p>${__("Delete {0} tasks from {1}?", [p.total, esc(me.projectName)])}</p>`;
				if (p.under) {
					message += `<p class="text-muted">${__("{0} of them are subtasks under the ones you picked.", [p.under])}</p>`;
				}
				message += `<ul>${p.subjects.map((s) => `<li>${esc(s)}</li>`).join("")}${p.picked > p.subjects.length ? `<li>…</li>` : ""}</ul>`;
				frappe.confirm(message, () => {
					me.wrapper.find(".task-saving-indicator").show();
					frappe.call({
						method: "erpnext_enhancements.api.scope_tasks.delete_tasks",
						args: { project: me.projectName, tasks: JSON.stringify(ids) },
						always: () => me.wrapper.find(".task-saving-indicator").hide(),
						callback: (r2) => {
							const out = r2.message || { deleted: [], kept: [] };
							if (out.deleted.length) {
								frappe.show_alert({ message: __("Deleted {0} tasks.", [out.deleted.length]), indicator: "green" }, 7);
							}
							if (out.kept.length) {
								// `reason` is left unescaped on purpose: it is the server's own message
								// (Frappe's link refusals carry `<a>` markup to the blocking record),
								// never user input.
								const rows = out.kept
									.map((k) => `<li><b>${esc(k.subject)}</b> <span class="text-muted">(${esc(k.name)})</span><br><span class="small">${k.reason}</span></li>`)
									.join("");
								frappe.msgprint({
									title: __("{0} tasks were kept", [out.kept.length]),
									indicator: "orange",
									message: `<ul>${rows}</ul>`,
								});
							}
							me.setSelectMode(false);
							me.fetchData();
						},
					});
				});
			},
		});
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
				onStart: function () {
					me._dropMode = null;
					me._dropTargetId = null;
					me.wrapper.find(".task-drag-hint").show();
				},
				onMove: function (evt, originalEvent) { return me.handleDragMove(evt, originalEvent); },
				onEnd: function (evt) {
					me.wrapper.find(".task-drag-hint").hide();
					me.handleDragEnd(evt);
				},
			});
			me.sortableInstances.push(sortable);
		});
	}

	clearDropIndicators() {
		this.wrapper
			.find(".drop-as-child, .drop-before, .drop-after")
			.removeClass("drop-as-child drop-before drop-after")
			.removeAttr("data-drop-label");
	}

	// Decide drag intent based on cursor position over the hovered task row:
	//   middle half  -> nest the dragged task as a child of the hovered task
	//                   (the row says so: a wash plus "Make subtask of <task>")
	//   top/bottom   -> reorder as a sibling (let SortableJS place it; the edge
	//                   the task will land on is drawn as a line)
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
		const $row = $(related).children(".task-grid-row").first();
		if (relY > h * 0.25 && relY < h * 0.75) {
			// Nest: say so on the target row and suppress the sibling reorder. The
			// band is the middle HALF of the row (it was 40%, 13px on a 32px row:
			// too thin to hit on purpose, so nesting looked broken).
			const subject = $row.find(".task-name-cell-text").first().text() || $(related).data("task-id");
			$row.addClass("drop-as-child").attr("data-drop-label", __("Make subtask of {0}", [subject]));
			this._dropMode = "child";
			this._dropTargetId = $(related).data("task-id");
			return false;
		}

		// Reorder as sibling: draw the edge SortableJS will put the task on.
		$row.addClass(evt.willInsertAfter ? "drop-after" : "drop-before");
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
			if (mode === "child" && targetId) {
				// Nest: save the ONE row with no position, so the server puts it after the
				// target's last subtask (rules.next_position), and open the target so the
				// re-fetch shows it there. Nothing in the DOM is moved: the tree loads
				// children lazily, so a collapsed target's container is empty, and reading
				// the order off it sent custom_subtask_order 1 for the dropped task,
				// colliding with the real first child (review of #1418). The dragged
				// node was not moved by SortableJS either (handleDragMove returned false).
				const draggedId = $(draggedNode).data("task-id");
				if (draggedId && draggedId !== targetId && !$.contains(draggedNode, me.wrapper.find(`.task-node[data-task-id="${targetId}"]`)[0])) {
					me.revealParent(targetId);
					me.saveStructure([{ name: draggedId, parent_task: targetId }]);
				}
				return;
			}

			// Reorder: persist the sibling order as the DOM now holds it (re-fetches).
			if (reordered) me.saveTaskOrder();
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
		this.saveStructure(tasksToUpdate);
	}

	/**
	 * Save `rows` ({name, parent_task, custom_subtask_order?}) through
	 * api/scope_tasks.move_tasks (v1.784.0). Whatever comes back, the tree is
	 * re-fetched: on success so it re-indents from the saved truth, on a refusal
	 * so the dropped row goes back where it was. The refusal itself (ERPNext's own
	 * message: an open task under a completed parent, a project mismatch) is shown
	 * in a dialog -- it used to be swallowed, so a drop that was not saved looked
	 * saved until the next reload. `frappe.call` does not call `error` on every
	 * failure (a query timeout, a deadlock), so the re-fetch rides on the promise.
	 */
	saveStructure(rows, { always = null, done = null } = {}) {
		const me = this;
		this.wrapper.find(".task-saving-indicator").show();
		const finish = () => {
			me.wrapper.find(".task-saving-indicator").hide();
			if (always) always();
		};
		const call = frappe.call({
			method: "erpnext_enhancements.api.scope_tasks.move_tasks",
			args: { project: this.projectName, tasks: JSON.stringify(rows) },
			callback: (r) => {
				const out = r.message || {};
				if (out.refused) {
					// `reason` is the server's own message (ERPNext's refusals carry
					// `<a>` links to the records involved), never user input.
					frappe.msgprint({
						title: __("Could not move {0}", [frappe.utils.escape_html(out.refused.subject || out.refused.name || "")]),
						indicator: "red",
						message: `<p>${out.refused.reason || __("The move was refused.")}</p><p class="text-muted small">${__("Nothing was changed; the tree shows the tasks as they are saved.")}</p>`,
					});
					return;
				}
				if ((out.moved || []).length) {
					frappe.show_alert({ message: __("Task order saved successfully"), indicator: "green" });
				}
				(out.converted || []).forEach((name) =>
					frappe.show_alert({ message: __("{0} is now a group task.", [name]), indicator: "blue" }, 7)
				);
				me.wrapper.find(".save-order-btn").hide();
				if (done) done();
			},
		});
		const settle = () => {
			finish();
			me.fetchData();
		};
		if (call && typeof call.then === "function") {
			call.then(settle, settle);
		} else {
			settle();
		}
	}
};

/** The vendored SortableJS build the tree loads on demand (pinned; see loadAssets). */
erpnext_enhancements.TaskTreeManager.SORTABLE_ASSET =
	"/assets/erpnext_enhancements/js/project_enhancements/lib/sortable-1.15.6.min.js";
