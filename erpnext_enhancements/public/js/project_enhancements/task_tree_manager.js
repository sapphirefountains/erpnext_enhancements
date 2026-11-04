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
 * Drag-to-parent (v1.787.0, Nikolas 2026-11-03: "Make any task a parent by simple
 * drag and drop"; redone in v1.791.0 after it still only reordered on prod): while
 * a row is dragged NOTHING on screen moves. The row under the pointer says what the
 * drop will do -- a "Nest under <task>" wash over its body, a line on the thin band
 * at its top or bottom edge for a reorder -- and a hint line above the grid says
 * the same in words. SortableJS only starts and ends the drag (handle, ghost, the
 * auto-scroll); its own swap is refused on every move (onMove returns false), and
 * the drop target is read from the pointer (document.elementFromPoint) on each
 * dragover. v1.787.0 let SortableJS swap rows the moment the pointer crossed a row's
 * edge band: the target slid out from under the pointer, the pointer then sat over
 * the dragged row itself (where SortableJS fires nothing), and the drop saved the
 * last intent recorded, a reorder. The nest band was reachable only by backing into
 * a row from the far side. "Move under..." on each row is the no-mouse path (a
 * dialog). The save is api/scope_tasks.move_tasks: a leaf task dropped on becomes a
 * group, only the rows that changed are saved, and ERPNext's refusal comes back as
 * a message the tree shows in a dialog and then re-fetches, so the tree never sits
 * showing a move that was not saved. A task with subtasks shows its dates read-only
 * here: they follow its subtasks (task_enhancements/parent_dates.py).
 *
 * v1.791.0 also: one Actions menu per row (add subtask, move under, delete; a
 * button with a menu, arrow keys and Escape) instead of three icon buttons; the
 * column headers sort (ascending, descending, then back to the manual order kept in
 * custom_subtask_order), within each parent, remembered per browser in
 * localStorage and never written to the server, with drag switched off while a
 * sort is on; and the grid scrolls sideways inside its own box on a narrow screen,
 * the Task column staying put. The rules of all three (what a drop does, the sort,
 * the menu) are pure functions on erpnext_enhancements.task_tree_rules, run under
 * node by scripts/test_task_tree_manager.js.
 *
 * Assets: styles ship globally in desk_addons.bundle.css (task_tree import);
 * SortableJS 1.15.6 is vendored at public/js/project_enhancements/lib/ (a UMD build
 * that sets window.Sortable, like frappe-gantt there) and loaded on demand in
 * loadAssets(). It used to come from a CDN on its unpinned "latest" tag: a
 * third-party script on every Project form whose version could change under us.
 */
frappe.provide("erpnext_enhancements");

/**
 * The grid's rules, pure (no DOM, no jQuery, no frappe), so scripts/test_task_tree_manager.js
 * can run them under node: what a drop does, how a column sorts, what the row menu holds.
 */
erpnext_enhancements.task_tree_rules = {
	// ---- drop zones -------------------------------------------------------------------
	// Nikolas, 2026-11-03: the whole row body nests; only a thin band between rows reorders.
	// The band is a quarter of the row, held between 6 and 10 px: on the 45 px rows the Desk
	// draws it is 10 px, so 25 px of every row nest, and the 20 px straddling a border reorder.
	EDGE_BAND_MAX: 10,
	EDGE_BAND_MIN: 6,

	edgeBand(height) {
		const quarter = Math.round((height || 0) * 0.25);
		return Math.min(this.EDGE_BAND_MAX, Math.max(this.EDGE_BAND_MIN, quarter));
	},

	/** "before" | "nest" | "after" for a pointer `relY` px below the top of a row `height` px tall. */
	dropZone(relY, height) {
		const band = this.edgeBand(height);
		if (relY < band) return "before";
		if (relY > height - band) return "after";
		return "nest";
	},

	/** The row's ancestors, nearest first, from a visible-row list. */
	ancestors(rows, id) {
		const byId = {};
		rows.forEach((row) => (byId[row.id] = row));
		const out = [];
		let cur = byId[id];
		while (cur && cur.parent && byId[cur.parent] && out.length < rows.length) {
			out.push(cur.parent);
			cur = byId[cur.parent];
		}
		return out;
	},

	/**
	 * What dropping `draggedId` on the row `hoverId` in `zone` does. `rows` are the rows on
	 * screen, top to bottom, each {id, parent} (parent null at the top level; a collapsed
	 * task's subtasks are not on screen and so not listed).
	 *
	 * Returns null when the drop would do nothing or must not happen (the row itself, one of
	 * its own subtasks, its own parent, the place it already is), {mode: "nest", target} to
	 * make it the last subtask of `target`, or {mode: "before" | "after", target, parent} to
	 * put it beside `target` under `parent`. A line between two rows always means "before the
	 * lower row": so the band under an open parent is "first subtask of it", the band under
	 * the last subtask of a group is "before the next row, at that row's level", and only the
	 * band under the last row on screen means "after it".
	 */
	dropPlan(rows, draggedId, hoverId, zone) {
		const at = {};
		rows.forEach((row, i) => (at[row.id] = i));
		if (!(hoverId in at) || !(draggedId in at) || hoverId === draggedId) return null;
		const hover = rows[at[hoverId]];
		if (this.ancestors(rows, hoverId).includes(draggedId)) return null;
		const dragged = rows[at[draggedId]];
		if (zone === "nest") {
			return hoverId === (dragged.parent || null) ? null : { mode: "nest", target: hoverId };
		}
		let target = hover;
		if (zone === "after") {
			const next = rows[at[hoverId] + 1];
			if (!next) return { mode: "after", target: hoverId, parent: hover.parent || null };
			target = next;
		}
		if (target.id === draggedId) return null;
		// Already directly above it, as its sibling: nothing to do.
		const above = rows[at[target.id] - 1];
		if (above && above.id === draggedId && (dragged.parent || null) === (target.parent || null)) return null;
		return { mode: "before", target: target.id, parent: target.parent || null };
	},

	/**
	 * The rows to save for a "before" / "after" drop: `siblings` are the ids under the new
	 * parent in their order on screen (the dragged one among them when it was already there).
	 * Every sibling is sent with its position; the server saves only what changed.
	 */
	reorderRows(siblings, draggedId, targetId, mode, parent) {
		const rest = siblings.filter((id) => id !== draggedId);
		let i = rest.indexOf(targetId);
		if (i < 0) i = mode === "after" ? rest.length - 1 : rest.length;
		rest.splice(mode === "after" ? i + 1 : i, 0, draggedId);
		return rest.map((id, n) => ({ name: id, parent_task: parent || null, custom_subtask_order: n + 1 }));
	},

	// ---- column sort -------------------------------------------------------------------
	SORT_LABELS: {
		subject: "Task",
		assigned_to: "Assigned To",
		status: "Status",
		priority: "Priority",
		exp_start_date: "Start Date",
		exp_end_date: "Due Date",
		progress: "% Complete",
		expected_time: "Expected Time",
	},
	PRIORITY_RANK: { Low: 0, Medium: 1, High: 2, Urgent: 3 },
	STATUS_RANK: { Open: 0, Working: 1, "Pending Review": 2, Overdue: 3, Template: 4, Completed: 5, Canceled: 6, Cancelled: 6 },

	/** Ascending, then descending, then back to the manual order; another column starts ascending. */
	nextSort(current, key) {
		if (!key || !(key in this.SORT_LABELS)) return current || null;
		if (!current || current.key !== key) return { key, dir: "asc" };
		if (current.dir === "asc") return { key, dir: "desc" };
		return null;
	},

	sortValue(task, key) {
		const v = task ? task[key] : null;
		if (key === "priority") return v in this.PRIORITY_RANK ? this.PRIORITY_RANK[v] : null;
		if (key === "status") return v in this.STATUS_RANK ? this.STATUS_RANK[v] : null;
		if (key === "progress" || key === "expected_time") return v == null || v === "" ? null : Number(v);
		if (v == null || v === "") return null;
		return String(v).toLowerCase();
	},

	/** Compare two tasks on `key` in `dir`; a task without a value goes last either way. */
	compare(a, b, key, dir) {
		const va = this.sortValue(a, key);
		const vb = this.sortValue(b, key);
		if (va == null && vb == null) return 0;
		if (va == null) return 1;
		if (vb == null) return -1;
		let c = 0;
		if (typeof va === "number" && typeof vb === "number") c = va - vb;
		else c = va < vb ? -1 : va > vb ? 1 : 0;
		return dir === "desc" ? -c : c;
	},

	/**
	 * A copy of the tree with every list of siblings sorted by `sort` ({key, dir}), each child
	 * staying under its parent; `sort` null (the manual order) returns the rows as given.
	 */
	sortTree(tasks, sort) {
		const list = Array.isArray(tasks) ? tasks : [];
		if (!sort || !sort.key || !(sort.key in this.SORT_LABELS)) return list;
		const sorted = list
			.map((task, i) => ({ task, i }))
			.sort((x, y) => this.compare(x.task, y.task, sort.key, sort.dir) || x.i - y.i)
			.map((x) => x.task);
		return sorted.map((task) =>
			task && Array.isArray(task.children) && task.children.length
				? Object.assign({}, task, { children: this.sortTree(task.children, sort) })
				: task
		);
	},

	// ---- the row's Actions menu ----------------------------------------------------------
	rowActions(task) {
		if (!task) return [];
		return [
			{ action: "add-subtask", label: "Add subtask", icon: "fa-plus" },
			{ action: "move", label: "Move under…", icon: "fa-indent" },
			{ action: "delete", label: "Delete", icon: "fa-trash", danger: true },
		];
	},

	/** Where a key press takes the focus in a menu of `count` items; "close" for Escape or Tab. */
	menuMove(key, index, count) {
		if (key === "Escape" || key === "Tab") return "close";
		if (!count) return null;
		if (key === "ArrowDown") return (index + 1) % count;
		if (key === "ArrowUp") return (index - 1 + count) % count;
		if (key === "Home") return 0;
		if (key === "End") return count - 1;
		return null;
	},
};

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

		// The column sort (v1.791.0): {key, dir} or null for the manual order. Per browser, in
		// localStorage (never frappe's user_settings: those live in Redis and follow the user
		// to every device); read and written in try/catch, a private window has neither.
		this.sort = null;
		try {
			const savedSort = JSON.parse(localStorage.getItem(TaskTreeManager.SORT_STORAGE_KEY()) || "null");
			if (savedSort && savedSort.key in erpnext_enhancements.task_tree_rules.SORT_LABELS) {
				this.sort = { key: savedSort.key, dir: savedSort.dir === "desc" ? "desc" : "asc" };
			}
		} catch (e) {
			this.sort = null;
		}
		this._dropPlan = null;
		this._dragRows = null;
		this._draggedId = null;
		this._onDocDragOver = (e) => this.trackPointer(e);

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
		// SortableJS is vendored and pinned (v1.787.0). The file name carries the
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
            <div class="task-tree-manager p-3 border rounded ${this.sort ? "sort-active" : ""}" style="background: var(--card-bg);">
                <div class="task-tree-header mb-3">
                    <div class="d-flex justify-content-between align-items-center mb-2 task-toolbar-row">
                        <div class="d-flex align-items-center task-toolbar-actions">
                            <h5 class="mb-0 mr-3">Tasks</h5>
                            <span class="task-saving-indicator text-muted mr-3" style="display: none;"><i class="fa fa-spinner fa-spin"></i> Saving...</span>
                            <div class="task-pending-changes-controls mr-2" style="display: none;">
                                <div class="btn-group btn-group-sm">
                                    <button type="button" class="btn btn-success save-changes-btn">Save Changes</button>
                                    <button type="button" class="btn btn-danger discard-changes-btn">Discard Changes</button>
                                </div>
                            </div>
                            <button class="btn btn-sm btn-success mr-2 save-order-btn" style="display: none;">Save Order</button>
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
                    <div class="task-drag-hint text-muted small" role="status" aria-live="polite"><i class="fa fa-info-circle mr-1"></i><span class="task-drag-hint-text"></span></div>
                </div>

                <div class="task-grid">
                    <div class="task-grid-header d-flex font-weight-bold border-bottom py-2" style="background: var(--subtle-fg);">
                        ${this.headerCell("subject", "Task", "", "flex: 5;")}
                        ${this.headerCell("assigned_to", "Assigned To", "assignee-cell", "flex: 1.5;", "owner")}
                        ${this.headerCell("status", "Status", "status-cell", "flex: 1.5;", "status")}
                        ${this.headerCell("priority", "Priority", "priority-cell", "flex: 1;", "priority")}
                        ${this.headerCell("exp_start_date", "Start Date", "date-cell", "flex: 1;", "start_date")}
                        ${this.headerCell("exp_end_date", "Due Date", "date-cell", "flex: 1;", "due_date")}
                        ${this.headerCell("progress", "% Complete", "progress-cell", "flex: 1;", "progress")}
                        ${this.headerCell("expected_time", "Expected Time", "duration-cell", "flex: 1;", "duration")}
                        ${!this.readonly ? `<div class="task-grid-cell actions-cell" data-column="actions" style="flex: 0.5;"><span class="sr-only">${__("Actions")}</span></div>` : ''}
                    </div>
                    <div class="task-grid-body">
                        <div class="p-4 text-center text-muted">Loading tasks...</div>
                    </div>
                </div>
            </div>
        `);
		this.bindEvents();
		this.renderSortState();

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

	/**
	 * One sortable header cell (v1.791.0). A click sorts ascending, a second descending, a
	 * third returns to the manual order; Enter and Space do the same from the keyboard. The
	 * column resizer's handle sits inside the cell and swallows its own clicks.
	 */
	headerCell(sortKey, label, cls, style, column) {
		const shown = column && !this.columnVisibility[column] ? "hidden-column" : "";
		const data = column ? `data-column="${column}"` : "";
		return `<div class="task-grid-cell sortable-header ${cls} ${shown}" ${data} data-sort-key="${sortKey}" style="${style}" role="button" tabindex="0" aria-sort="none" title="${__("Sort by {0}", [__(label)])}"><span class="sortable-header-label">${__(label)}</span><span class="sort-arrow" aria-hidden="true"></span></div>`;
	}

	/** Draw the arrow on the sorted header, the drag-off note, and the dimmed handles. */
	renderSortState() {
		const rules = erpnext_enhancements.task_tree_rules;
		const sort = this.sort;
		this.wrapper.find(".sortable-header").each(function () {
			const $cell = $(this);
			const on = sort && $cell.data("sort-key") === sort.key;
			$cell.toggleClass("sorted-asc", !!on && sort.dir === "asc").toggleClass("sorted-desc", !!on && sort.dir === "desc");
			$cell.attr("aria-sort", on ? (sort.dir === "asc" ? "ascending" : "descending") : "none");
			$cell.find(".sort-arrow").text(on ? (sort.dir === "asc" ? "▲" : "▼") : "");
		});
		this.wrapper.find(".task-tree-manager").toggleClass("sort-active", !!sort);
		if (sort) {
			const label = __(rules.SORT_LABELS[sort.key] || sort.key);
			this.showHint(
				__("Sorted by {0} ({1}). Drag is off until the manual order is back: click the {0} header again.", [
					label,
					sort.dir === "asc" ? __("ascending") : __("descending"),
				])
			);
		} else {
			this.hideHint();
		}
	}

	/**
	 * The hint line always holds a line of text (the idle tip, the drag's words, or the
	 * sorted note), so the grid never jumps when a drag starts; showing it only during the
	 * drag pushed every row down by a line under the pointer the moment the drag began.
	 */
	showHint(text) {
		this.wrapper.find(".task-drag-hint-text").text(text);
	}

	hideHint() {
		this.showHint(
			this.readonly
				? ""
				: __("Drag a task by its handle onto another task to nest it there, or onto the line between two tasks to reorder.")
		);
	}

	/** Set (or clear) the column sort, remember it for this browser, and redraw. */
	setSort(sort) {
		this.sort = sort || null;
		try {
			if (this.sort) localStorage.setItem(TaskTreeManager.SORT_STORAGE_KEY(), JSON.stringify(this.sort));
			else localStorage.removeItem(TaskTreeManager.SORT_STORAGE_KEY());
		} catch (e) {
			// A private window or a full store: the sort still applies for this page.
		}
		this.renderSortState();
		this.applyFilters();
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
		// The column sort is applied here, within each parent, on a copy: `this.tasks` keeps
		// the manual order so returning to it needs no fetch.
		tasks = erpnext_enhancements.task_tree_rules.sortTree(tasks, this.sort);
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
					${!this.readonly ? `<div class="task-grid-cell actions-cell" data-column="actions"><button type="button" class="btn btn-xs btn-default task-actions-btn" title="${__("Actions")}" aria-haspopup="menu" aria-expanded="false" data-task-name="${task.name}" data-task-subject="${frappe.utils.escape_html(task.subject || "")}"><i class="fa fa-ellipsis-h" aria-hidden="true"></i><span class="sr-only">${__("Actions for {0}", [frappe.utils.escape_html(task.subject || task.name)])}</span></button></div>` : ''}
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
					const children = erpnext_enhancements.task_tree_rules.sortTree(r.message, this.sort);
					children.forEach(child => { this.renderTaskNode(child, container, level); });
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
	 * One date cell. A task with subtasks takes its dates from them (v1.787.0:
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
		// One Actions menu per row (v1.791.0): add subtask, move under, delete.
		this.wrapper.on("click", ".task-actions-btn", function (e) {
			e.preventDefault();
			e.stopPropagation();
			me.openRowMenu($(this));
		});
		this.wrapper.on("keydown", ".task-actions-btn", function (e) {
			if (e.key === "ArrowDown" || e.key === "ArrowUp") {
				e.preventDefault();
				me.openRowMenu($(this), e.key === "ArrowUp" ? "last" : "first");
			}
		});
		// Column sort (v1.791.0): click or Enter/Space on a header. The resizer's handle stops
		// its own clicks, and a click that lands on it is ignored here too.
		this.wrapper.on("click", ".sortable-header", function (e) {
			if ($(e.target).closest(".ee-col-resize-handle").length) return;
			me.setSort(erpnext_enhancements.task_tree_rules.nextSort(me.sort, $(this).data("sort-key")));
		});
		this.wrapper.on("keydown", ".sortable-header", function (e) {
			if (e.key === "Enter" || e.key === " ") {
				e.preventDefault();
				me.setSort(erpnext_enhancements.task_tree_rules.nextSort(me.sort, $(this).data("sort-key")));
			}
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

		this.wrapper.on("click", ".save-order-btn", () => me.saveTaskOrder());

		this.wrapper.on("click", ".assignee-link", function (e) {
			e.preventDefault();
			if (me.readonly) return;
			const taskId = $(this).closest(".task-node").data("task-id");
			me.showAssigneePicker(taskId);
		});
	}

	/**
	 * The row's Actions menu (v1.791.0). A floating menu under the button: Add subtask, Move
	 * under…, Delete. Arrow keys move, Home/End jump, Escape or Tab close and put the focus
	 * back on the button, a click outside closes. `focus` "first" | "last" picks the item
	 * focused on open (the arrow key that opened it).
	 */
	openRowMenu($btn, focus = "first") {
		const me = this;
		const rules = erpnext_enhancements.task_tree_rules;
		this.closeRowMenu();
		const taskId = $btn.data("task-name");
		const subject = $btn.data("task-subject");
		const items = rules.rowActions({ name: taskId, subject });
		const $menu = $(`<div class="dropdown-menu show task-row-menu" role="menu" aria-label="${__("Actions for {0}", [frappe.utils.escape_html(subject || taskId)])}"></div>`);
		items.forEach((item) => {
			$(
				`<button type="button" class="dropdown-item ${item.danger ? "text-danger" : ""}" role="menuitem" data-action="${item.action}"><i class="fa fa-fw ${item.icon} mr-1" aria-hidden="true"></i>${__(item.label)}</button>`
			).appendTo($menu);
		});
		$("body").append($menu);
		const rect = $btn[0].getBoundingClientRect();
		const w = $menu.outerWidth();
		const h = $menu.outerHeight();
		let top = rect.bottom + 2;
		let left = rect.right - w;
		if (top + h > window.innerHeight) top = Math.max(0, rect.top - h - 2);
		if (left < 4) left = 4;
		$menu.css({ position: "fixed", top, left, zIndex: 2000 });
		$btn.attr("aria-expanded", "true");
		this._rowMenu = { $menu, $btn };

		const $items = $menu.find("[role=menuitem]");
		const focusAt = (i) => $items.eq(i).trigger("focus");
		$items.on("click", function (e) {
			e.preventDefault();
			const action = $(this).data("action");
			me.closeRowMenu();
			me.runRowAction(action, taskId, subject);
		});
		$menu.on("keydown", function (e) {
			const index = $items.index(document.activeElement);
			const next = rules.menuMove(e.key, index < 0 ? 0 : index, $items.length);
			if (next === "close") {
				if (e.key === "Escape") e.preventDefault();
				me.closeRowMenu(e.key === "Escape");
				return;
			}
			if (next === null) return;
			e.preventDefault();
			focusAt(next);
		});
		setTimeout(() => {
			$(document).on("mousedown.taskRowMenu", (e) => {
				if (!$(e.target).closest(".task-row-menu").length) me.closeRowMenu();
			});
		}, 0);
		focusAt(focus === "last" ? $items.length - 1 : 0);
	}

	closeRowMenu(refocus = false) {
		$(document).off("mousedown.taskRowMenu");
		// Every row menu on the page, not only this instance's: there is only ever one
		// meant to be open, and one left by a tree that was rebuilt under it must go too.
		$(".task-row-menu").remove();
		$(".task-actions-btn[aria-expanded=true]").attr("aria-expanded", "false");
		const open = this._rowMenu;
		this._rowMenu = null;
		if (!open) return;
		if (refocus) open.$btn.trigger("focus");
	}

	runRowAction(action, taskId, subject) {
		if (action === "add-subtask") this.openTaskDialog({ parent: taskId, parentSubject: subject });
		else if (action === "move") this.openMoveDialog(taskId, subject);
		else if (action === "delete") this.deleteOne(taskId, subject);
	}

	deleteOne(taskId, subject) {
		const me = this;
		frappe.confirm(__("Delete task {0}: {1}?", [taskId, frappe.utils.escape_html(subject || "")]), () => {
			frappe.call({
				method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.delete_task",
				args: { task_name: taskId },
				callback: (r) => {
					if (r.message && r.message.status === "success") {
						frappe.show_alert({ message: __("Task deleted successfully"), indicator: "green" });
						me.fetchData();
					} else if (r.message && r.message.message) {
						frappe.msgprint({ title: __("Could not delete {0}", [frappe.utils.escape_html(subject || taskId)]), indicator: "red", message: r.message.message });
					}
				},
			});
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
	 * "Move under...": the no-mouse path to re-parenting (v1.787.0). A Link to a
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

	/**
	 * Drag-and-drop (v1.791.0). SortableJS supplies the drag itself: the handle, the ghost
	 * row, touch support and the auto-scroll. It is never allowed to move a row (onMove
	 * returns false on every call), so the tree stays still under the pointer and the drop
	 * target is whatever row the pointer is over, read with elementFromPoint on each
	 * dragover (native drag) and from onMove's own event (the touch fallback). While a
	 * column sort is on there is no drag: the order on screen is not the manual order a
	 * drop would be saved against, and the hint line says so.
	 */
	initializeTaskSorting() {
		const me = this; if (this.readonly) return;
		// A destroyed Sortable has no `el`; destroying it again throws (the form script
		// destroys them on a refresh too) and would stop the grid from rendering.
		this.sortableInstances.forEach((s) => { if (s.el) s.destroy(); });
		this.sortableInstances = [];
		if (this.sort) {
			this.wrapper.find(".task-drag-handle").attr("title", __("Drag is off while the tree is sorted. Click the sorted header again for the manual order."));
			return;
		}
		this.wrapper.find(".task-drag-handle").attr("title", __("Drag onto a task to nest under it; onto the line between two tasks to put it there."));
		const containers = this.wrapper.find(".task-grid-body, .child-tasks-container");
		containers.each(function () {
			const sortable = new Sortable(this, {
				group: "task-tree",
				handle: ".task-drag-handle",
				draggable: ".task-node",
				animation: 0,
				fallbackOnBody: true,
				swapThreshold: 1,
				onStart: function (evt) { me.handleDragStart(evt); },
				onMove: function (evt, originalEvent) {
					// The touch fallback has no dragover: read the pointer from this event.
					// A native drag is tracked by the document listener; either way, refuse
					// SortableJS's own move so nothing slides away from under the pointer.
					if (originalEvent) me.trackPointer(originalEvent);
					return false;
				},
				onEnd: function (evt) { me.handleDragEnd(evt); },
			});
			me.sortableInstances.push(sortable);
		});
	}

	/** The rows on screen, top to bottom, as the drop rules read them: {id, parent}. */
	visibleRows() {
		const rows = [];
		const walk = (container, parent) => {
			$(container).children(".task-node").each(function () {
				const id = $(this).data("task-id");
				rows.push({ id, parent });
				const $children = $(this).children(".child-tasks-container");
				if ($children.length && $children[0].style.display !== "none") walk($children[0], id);
			});
		};
		walk(this.wrapper.find(".task-grid-body")[0], null);
		return rows;
	}

	handleDragStart(evt) {
		this._draggedId = $(evt.item).data("task-id");
		this._dragRows = this.visibleRows();
		this._dropPlan = null;
		this.showHint(__("Drop onto a task to nest it under that task (it becomes a group). Drop on the line between two tasks to put it there."));
		document.addEventListener("dragover", this._onDocDragOver);
		document.addEventListener("pointermove", this._onDocDragOver);
	}

	/** Where the pointer is during a drag, from a dragover, pointer or touch event. */
	trackPointer(e) {
		if (!this._draggedId) return;
		const point = e.touches && e.touches[0] ? e.touches[0] : e;
		if (point.clientX == null || point.clientY == null) return;
		this.updateDropIntent(point.clientX, point.clientY);
	}

	/** Read the row under (x, y), decide the drop, and draw it. */
	updateDropIntent(x, y) {
		const rules = erpnext_enhancements.task_tree_rules;
		const under = document.elementFromPoint(x, y);
		const row = under && under.closest ? under.closest(".task-grid-row") : null;
		const node = row && !row.classList.contains("quick-add-row") ? row.closest(".task-node") : null;
		let plan = null;
		if (node && $.contains(this.wrapper[0], node)) {
			const rect = row.getBoundingClientRect();
			const zone = rules.dropZone(y - rect.top, rect.height);
			plan = rules.dropPlan(this._dragRows || [], this._draggedId, node.getAttribute("data-task-id"), zone);
		}
		this.drawDropPlan(plan);
	}

	drawDropPlan(plan) {
		const same =
			(!plan && !this._dropPlan) ||
			(plan && this._dropPlan && plan.mode === this._dropPlan.mode && plan.target === this._dropPlan.target);
		this._dropPlan = plan;
		if (same) return;
		this.clearDropIndicators();
		if (!plan) return;
		const $node = this.wrapper.find(`.task-node[data-task-id="${plan.target}"]`);
		const $row = $node.children(".task-grid-row").first();
		if (plan.mode === "nest") {
			const subject = $row.find(".task-name-cell-text").first().text() || plan.target;
			// The label sits on the sticky Task cell: CSS attr() reads the element the
			// pseudo-element belongs to, so the attribute must be on that cell, not the row.
			$row.addClass("drop-as-child");
			$row.children(".task-grid-cell").first().attr("data-drop-label", __("Nest under {0}", [subject]));
		} else {
			$row.addClass(plan.mode === "after" ? "drop-after" : "drop-before");
		}
	}

	clearDropIndicators() {
		this.wrapper
			.find(".drop-as-child, .drop-before, .drop-after")
			.removeClass("drop-as-child drop-before drop-after");
		this.wrapper.find("[data-drop-label]").removeAttr("data-drop-label");
	}

	handleDragEnd(evt) {
		const me = this;
		const original = evt.originalEvent;
		// A native drag that ends in `dragend` without a `drop` was canceled: Escape, or
		// let go outside the page. (A drop fires `drop` first, and SortableJS nulls itself
		// then, so the later `dragend` never reaches here.)
		const canceled = !!(original && original.type === "dragend");
		// Decide from where the pointer let go, not only from the last dragover seen: a
		// quick drop can land with no dragover reported at its own point (seen with a
		// driven mouse on prod; a hand moves through many more). The touch fallback ends
		// in touchend, whose point is in changedTouches.
		const point = original && original.changedTouches && original.changedTouches[0] ? original.changedTouches[0] : original;
		if (!canceled && this._draggedId && point && point.clientX != null && point.clientY != null) {
			this.updateDropIntent(point.clientX, point.clientY);
		}
		const plan = this._dropPlan;
		const draggedId = this._draggedId;
		const rows = this._dragRows || [];
		this._dropPlan = null;
		this._draggedId = null;
		this._dragRows = null;
		document.removeEventListener("dragover", this._onDocDragOver);
		document.removeEventListener("pointermove", this._onDocDragOver);
		this.clearDropIndicators();
		this.renderSortState();
		if (!plan || !draggedId || canceled) return;

		// Never touch the DOM inside onEnd: SortableJS is still in its own drop cleanup
		// and a re-parented node leaves its internals pointing at a detached element
		// ("Cannot read properties of null (reading 'removeEventListener')" in _onDrop).
		setTimeout(() => me.applyDropPlan(plan, draggedId, rows), 0);
	}

	/**
	 * Save what the drop decided. A nest saves the one row with no position, so the server
	 * puts it after the target's last subtask (rules.next_position; the target's subtasks may
	 * not be loaded here) and the target is opened for the re-fetch. A reorder sends the
	 * target's siblings with their new positions (and the new parent on the moved row).
	 */
	applyDropPlan(plan, draggedId, rows) {
		const rules = erpnext_enhancements.task_tree_rules;
		if (plan.mode === "nest") {
			this.revealParent(plan.target);
			this.saveStructure([{ name: draggedId, parent_task: plan.target }]);
			return;
		}
		const siblings = rows.filter((row) => (row.parent || null) === (plan.parent || null)).map((row) => row.id);
		this.saveStructure(rules.reorderRows(siblings, draggedId, plan.target, plan.mode, plan.parent));
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
	 * api/scope_tasks.move_tasks (v1.787.0). Whatever comes back, the tree is
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

/** Where this browser keeps the column sort (localStorage, per user; v1.791.0). */
erpnext_enhancements.TaskTreeManager.SORT_STORAGE_KEY = () => `taskTreeSort_${frappe.session.user}`;
