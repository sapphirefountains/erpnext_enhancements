/* global erpnext_enhancements, Gantt, moment */
frappe.provide("erpnext_enhancements.dashboard_components");

/**
 * Project Dashboard tab — Portfolio Gantt.
 *
 * Targets: the "Portfolio Gantt" tab of the Project Dashboard page.
 * Loaded via: lazy `frappe.require` from project_dashboard.js (constructed by
 * name; render()/unmount() on tab show/hide). The Gantt library itself
 * (lib/frappe-gantt.umd.js) and the shared zoom ladder (gantt_zoom.js) are loaded
 * globally via app_include_js.
 *
 * Draws a single Frappe Gantt across the whole portfolio: rows are grouped by
 * Master Project, and an optional "Detailed View" expands each project into its
 * task hierarchy. Master/project/task nodes are collapsible (the ▶/▼ prefixes in
 * the bar labels are clickable, tracked in `collapsedNodes`), filterable by
 * status, and zoomable via the shared ladder. Dragging a project/task bar writes
 * new dates back through the dashboard API (master bars are aggregates and snap
 * back). A fair amount of the code preserves the horizontal scroll position across
 * re-renders (toggles, filters, date saves) instead of jumping to "today" — see
 * capture_scroll_for_next_render and the do_preserve logic in render_gantt.
 */
erpnext_enhancements.dashboard_components.PortfolioGantt = class PortfolioGantt {
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.abortController = null;
		
		this.detailedView = false;
		this.statusFilters = ["Active", "Working", "Client Hold"]; 
		this.allStatuses = ["Active", "Working", "Client Hold", "Parked", "Completed", "Invoiced", "Paid", "Canceled"];
        
        this.selectedProjects = new Set();
        this.collapsedNodes = new Set();
        this.ganttDataCache = null;
        this.isTogglingNode = false;

        // Live Gantt instance + current zoom ladder index. Zoom persists while the
        // dashboard tab stays open but resets to the default on a fresh page load
        // (a new PortfolioGantt is constructed each load).
        this.ganttInstance = null;
        this.zoomIndex = erpnext_enhancements.gantt_zoom.DEFAULT_INDEX.Month;

        // Scroll preservation across re-renders. When an action that empties the
        // chart container (e.g. toggling Detailed View) needs to keep the user's
        // scroll position, it stashes the current position here before the
        // re-render and render_gantt() restores it instead of jumping to today.
        this._preserveNextScroll = false;
        this._pendingScrollLeft = null;
        this._pendingScrollTop = null;
	}

	// Capture the current scroll position so the next render_gantt() restores it.
	// Call this BEFORE emptying the chart container.
	capture_scroll_for_next_render() {
		const gc = this.chartContainer && this.chartContainer.find(".gantt-container")[0];
		if (gc) {
			this._pendingScrollLeft = gc.scrollLeft;
			this._pendingScrollTop = gc.scrollTop;
			this._preserveNextScroll = true;
		}
	}

	async render() {
		this.wrapper.empty();
		this.render_controls();
		this.chartContainer = $('<div class="portfolio-gantt-chart-area mt-3"></div>').appendTo(this.wrapper);
		this.show_skeleton();

		try {
			await this.fetch_and_render_data();
		} catch (error) {
			this.handle_error(error);
		}
	}

	render_controls() {
		const controlsHTML = `
			<div class="gantt-controls-wrapper d-flex align-items-center mb-3 p-3 bg-light rounded border flex-wrap">
				<div class="custom-control custom-switch mr-4" style="font-size: 1.1em;">
					<input type="checkbox" class="custom-control-input" id="gantt-detailed-toggle" ${this.detailedView ? 'checked' : ''}>
					<label class="custom-control-label font-weight-bold" for="gantt-detailed-toggle" style="cursor: pointer;">Detailed View (Show Tasks)</label>
				</div>
				<div class="d-flex align-items-center ml-4">
					<label class="mr-2 mb-0 font-weight-bold">Status Filter:</label>
					<div class="dropdown check-dropdown">
						<button class="btn btn-sm btn-white border custom-dropdown-toggle" type="button" id="ganttStatusDropdown" style="min-width: 200px; text-align: left;">
							Selected (${this.statusFilters.length})
						</button>
						<div class="dropdown-menu p-2 shadow-sm" style="min-width: 220px;">
							<div id="gantt-status-checkboxes">
								${this.allStatuses.map(s => `
									<div class="form-check mb-1 custom-control custom-checkbox">
										<input class="form-check-input custom-control-input gantt-status-cb" type="checkbox" value="${s}" id="filter-gantt-${s.replace(/\s+/g, '')}" ${this.statusFilters.includes(s) ? 'checked' : ''}>
										<label class="custom-control-label form-check-label" for="filter-gantt-${s.replace(/\s+/g, '')}">${s}</label>
									</div>
								`).join('')}
							</div>
							<div class="dropdown-divider"></div>
							<button class="btn btn-sm btn-primary w-100" id="apply-gantt-filters">Apply Filters</button>
						</div>
					</div>
				</div>
				<div class="d-flex align-items-center ml-auto">
					<label class="mr-2 mb-0 font-weight-bold">Zoom:</label>
					<div class="btn-group btn-group-sm">
						<button class="btn btn-white border btn-gantt-zoom-out" type="button" title="Zoom out (show more)"><i class="fa fa-search-minus"></i></button>
						<button class="btn btn-white border btn-gantt-zoom-in" type="button" title="Zoom in (show detail)"><i class="fa fa-search-plus"></i></button>
					</div>
				</div>
			</div>
		`;
		
		this.wrapper.append(controlsHTML);

		if (!document.getElementById('portfolio-gantt-styles')) {
			$('<style id="portfolio-gantt-styles">').html(`
				.gantt .gantt-master-project .bar { fill: #34495e !important; }
				.gantt .gantt-master-project .bar-progress { fill: #2c3e50 !important; }
				.gantt .gantt-master-project .bar-label { font-weight: bold; fill: #fff; }
				
				.gantt .gantt-project .bar { fill: #3498db !important; }
				.gantt .gantt-project .bar-progress { fill: #2980b9 !important; }
				.gantt .gantt-project .bar-label { font-weight: bold; fill: #fff; }
				
				.gantt .gantt-task .bar { fill: #95a5a6 !important; height: 14px; transform: translateY(3px); }
				.gantt .gantt-task .bar-progress { fill: #7f8c8d !important; height: 14px; transform: translateY(3px); }
				.gantt .gantt-task .bar-label { font-size: 11px; fill: #333; }
                
                .gantt .bar-label { cursor: pointer; pointer-events: auto; transition: fill 0.2s; user-select: none; }
                .gantt .bar-label:hover { fill: #007bff !important; text-decoration: underline; }
                
                .check-dropdown .dropdown-menu.show { display: block !important; }
			`).appendTo('head');
		}

        this.wrapper.find('.custom-dropdown-toggle').on('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            let $menu = $(this).next('.dropdown-menu');
            let isShown = $menu.hasClass('show');
            $('.dropdown-menu').removeClass('show'); 
            if (!isShown) $menu.addClass('show');
        });

        $(document).on('click', (e) => {
            if (!$(e.target).closest('.check-dropdown').length) {
                this.wrapper.find('.dropdown-menu').removeClass('show');
            }
        });

		this.wrapper.find('#gantt-detailed-toggle').on('change', (e) => {
			this.detailedView = $(e.currentTarget).is(':checked');
            if (this.detailedView && this.ganttDataCache) {
                this.collapsedNodes.clear();
                this.ganttDataCache.projects.forEach(p => {
                    this.collapsedNodes.add('project_' + p.name);
                });
            }
			// Keep the current scroll position rather than jumping to today when
			// expanding/collapsing the task detail.
			this.capture_scroll_for_next_render();
			this.chartContainer.empty();
			this.show_skeleton();
			this.fetch_and_render_data();
		});

		this.wrapper.find('#apply-gantt-filters').on('click', () => {
			const selected = [];
			this.wrapper.find('.gantt-status-cb:checked').each(function() {
				selected.push($(this).val());
			});
			this.statusFilters = selected;
			this.wrapper.find('#ganttStatusDropdown').text(`Selected (${selected.length})`);
			this.wrapper.find('.dropdown-menu').removeClass('show');

			this.capture_scroll_for_next_render();
			this.chartContainer.empty();
			this.show_skeleton();
			this.fetch_and_render_data();
		});
		
		this.wrapper.find('.check-dropdown .dropdown-menu').on('click', function(e) {
			e.stopPropagation();
		});

		// Zoom controls. Applying a zoom re-renders the existing Gantt in place
		// (no data refetch) via the shared zoom ladder, preserving scroll position.
		const Z = erpnext_enhancements.gantt_zoom;
		const sync_zoom = () => {
			this.wrapper.find('.btn-gantt-zoom-out').prop('disabled', Z.is_min(this.zoomIndex));
			this.wrapper.find('.btn-gantt-zoom-in').prop('disabled', Z.is_max(this.zoomIndex));
		};
		const apply_zoom = (index) => {
			this.zoomIndex = Z.clamp(index);
			if (this.ganttInstance) Z.apply(this.ganttInstance, this.zoomIndex);
			sync_zoom();
		};
		this.wrapper.find('.btn-gantt-zoom-in').on('click', () => apply_zoom(this.zoomIndex + 1));
		this.wrapper.find('.btn-gantt-zoom-out').on('click', () => apply_zoom(this.zoomIndex - 1));
		sync_zoom();
	}

	async fetch_and_render_data() {
		this.abortController = new AbortController();
		const signal = this.abortController.signal;

		try {
			const data = await erpnext_enhancements.dashboard_api.call(
				{
					method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.get_all_projects_for_gantt",
					args: {
						include_tasks: this.detailedView ? 1 : 0,
						statuses: JSON.stringify(this.statusFilters)
					}
				},
				signal
			);

			if (signal.aborted) return;

			if (data.message && !data.message.error) {
                this.ganttDataCache = data.message;
				this.render_gantt();
			} else {
				throw new Error(data.message ? data.message.error : "Unknown error fetching projects for gantt");
			}
		} finally {
			this.abortController = null;
		}
	}

	render_gantt(preserve_scroll = false) {
		if (!this.ganttDataCache || !this.ganttDataCache.projects) return;

        let data = this.ganttDataCache;

		if (data.projects.length === 0) {
			this.chartContainer.html('<p class="text-muted text-center p-4">No active projects match the current filters.</p>');
			return;
		}

        let taskMap = {};
        let projectTaskRoots = {};
        if (this.detailedView && data.tasks) {
            data.tasks.forEach(t => { t.children = []; taskMap[t.name] = t; });
            data.tasks.forEach(t => {
                if (t.parent_task && taskMap[t.parent_task]) {
                    taskMap[t.parent_task].children.push(t);
                } else {
                    if (!projectTaskRoots[t.project]) projectTaskRoots[t.project] = [];
                    projectTaskRoots[t.project].push(t);
                }
            });
        }

		let mappedItems = [];
		let masterGroups = {};
		
		data.projects.forEach(p => {
			let master = p.custom_master_project || "Independent Projects";
			if (!masterGroups[master]) masterGroups[master] = [];
			masterGroups[master].push(p);
		});

        const getSafeDates = (startStr, endStr, fallbackStartStr = null) => {
            let start = startStr ? new Date(startStr) : (fallbackStartStr ? new Date(fallbackStartStr) : new Date());
            let end = endStr ? new Date(endStr) : new Date(start.getTime() + (3*24*60*60*1000));
            if (end < start) end = new Date(start.getTime() + (24*60*60*1000));
            return { start, end };
        };

		Object.keys(masterGroups).sort().forEach(master => {
			let projects = masterGroups[master];
            let is_independent = (master === "Independent Projects");
			let masterStart = null, masterEnd = null, totalProgress = 0;

			projects.forEach(p => {
                let d = getSafeDates(p.expected_start_date, p.expected_end_date);
				if (!masterStart || d.start < masterStart) masterStart = d.start;
				if (!masterEnd || d.end > masterEnd) masterEnd = d.end;
				totalProgress += (p.percent_complete || 0);
			});

			if (!masterStart) masterStart = new Date();
			if (!masterEnd || masterEnd < masterStart) {
				masterEnd = new Date(masterStart.getTime() + (24*60*60*1000));
			}

			let avgProgress = projects.length > 0 ? (totalProgress / projects.length) : 0;
            let master_id = 'master_' + master;
            let is_m_collapsed = this.collapsedNodes.has(master_id);

			if (!is_independent) {
                let m_prefix = projects.length > 0 ? (is_m_collapsed ? '▶ ' : '▼ ') : '';
                mappedItems.push({
                    id: master_id,
                    name: m_prefix + master.toUpperCase(),
                    start: moment(masterStart).format("YYYY-MM-DD"),
                    end: moment(masterEnd).format("YYYY-MM-DD"),
                    progress: avgProgress,
                    custom_class: 'gantt-master-project',
                    isMaster: true,
                    hasChildren: projects.length > 0
                });
            }

            if (!is_independent && is_m_collapsed) return;

			projects.forEach(p => {
				let pDates = getSafeDates(p.expected_start_date, p.expected_end_date);
                let p_id = 'project_' + p.name;
                let t_roots = projectTaskRoots[p.name] || [];
                let has_tasks = this.detailedView && t_roots.length > 0;
                let is_p_collapsed = this.collapsedNodes.has(p_id);
				
                let base_indent = is_independent ? '' : '  ';
                let p_prefix = base_indent + (has_tasks ? (is_p_collapsed ? '▶ ' : '▼ ') : (is_independent ? '' : '↳ '));

				mappedItems.push({
					id: p_id,
					name: p_prefix + (p.project_name || p.name),
					start: moment(pDates.start).format("YYYY-MM-DD"),
					end: moment(pDates.end).format("YYYY-MM-DD"),
					progress: p.percent_complete || 0,
					custom_class: 'gantt-project',
					custom_start_date: p.expected_start_date,
					isProject: true,
					project_docname: p.name,
                    hasChildren: has_tasks
				});

				if (!has_tasks || is_p_collapsed) return;

                const pushTasks = (tasks, indentLevel) => {
                    tasks.forEach(t => {
                        let tDates = getSafeDates(t.exp_start_date, t.exp_end_date, pDates.start);
                        let t_id = 'task_' + t.name;
                        let has_sub = t.children && t.children.length > 0;
                        let is_t_collapsed = this.collapsedNodes.has(t_id);

                        let baseIndent = is_independent ? '  ' : '    ';
                        for(let i=0; i<indentLevel; i++) baseIndent += '  ';
                        let t_prefix = has_sub ? (is_t_collapsed ? baseIndent + '▶ ' : baseIndent + '▼ ') : baseIndent + '• ';

                        mappedItems.push({
                            id: t_id,
                            name: t_prefix + (t.subject || t.name),
                            start: moment(tDates.start).format("YYYY-MM-DD"),
                            end: moment(tDates.end).format("YYYY-MM-DD"),
                            progress: t.progress || 0,
                            dependencies: indentLevel === 0 ? p_id : 'task_' + t.parent_task,
                            custom_class: 'gantt-task', 
                            custom_start_date: t.exp_start_date || p.expected_start_date,
                            isTask: true,
                            task_docname: t.name,
                            hasChildren: has_sub
                        });

                        if (has_sub && !is_t_collapsed) {
                            pushTasks(t.children, indentLevel + 1);
                        }
                    });
                };

                pushTasks(t_roots, 0);
			});
		});

        // Preserve scroll when either the caller asked for it (e.g. node toggle)
        // or an earlier handler stashed a position before emptying the container.
        const do_preserve = preserve_scroll || this._preserveNextScroll;
        let scroll_left = 0, scroll_top = 0;
        if (do_preserve) {
            if (this._preserveNextScroll && this._pendingScrollLeft != null) {
                scroll_left = this._pendingScrollLeft;
                scroll_top = this._pendingScrollTop;
            } else if (this.chartContainer.find(".gantt-container").length) {
                scroll_left = this.chartContainer.find(".gantt-container")[0].scrollLeft;
                scroll_top = this.chartContainer.find(".gantt-container")[0].scrollTop;
            }
        }
        this._preserveNextScroll = false;
        this._pendingScrollLeft = null;
        this._pendingScrollTop = null;

        let $chartWrapper = $('<div id="gantt-chart-target" style="width: 100%; height: 600px;"></div>');
        this.chartContainer.empty().append($chartWrapper);

		frappe.require([
			"/assets/erpnext_enhancements/js/lib/frappe-gantt.umd.js"
		], () => {
			const zoom_level = erpnext_enhancements.gantt_zoom.level(this.zoomIndex);
			this.ganttInstance = new Gantt($chartWrapper[0], mappedItems, {
				view_mode: zoom_level.view_mode,
				column_width: zoom_level.column_width,
                auto_move_label: true,
                // Let the library center on today for a fresh render (it locates
                // today via the date cells, which is reliable). On a preserve
                // render, suppress it so only our manual restore moves the viewport.
                scroll_to: do_preserve ? null : "today",
				on_click: (item) => {
                    if (this.isTogglingNode) return;
					if (item.isProject) frappe.set_route("Form", "Project", item.project_docname);
					else if (item.isTask) frappe.set_route("Form", "Task", item.task_docname);
				},
				on_date_change: (item, start, end) => {
					this.handle_date_change(item, start, end);
				},
				custom_popup_html: function (item) {
					if (item.isMaster) {
						return `<div class="gantt-popup" style="padding: 10px; background: white; border: 1px solid #ccc; border-radius: 4px;">
									<h5 class="mb-1">${item.name.replace(/[▼▶]/g, '').trim()}</h5>
									<p class="mb-0 text-muted"><strong>Overall Progress:</strong> ${Math.round(item.progress)}%</p>
								</div>`;
					}
					
					const startDate = frappe.datetime.str_to_user(item.custom_start_date);
					const endDate = frappe.datetime.str_to_user(item.end);
					const titlePrefix = item.isTask ? "Task" : "Project";
					const cleanName = item.name.replace(/[↳•▼▶]/g, '').trim();

					return `
						<div class="gantt-popup" style="padding: 12px; background: white; border: 1px solid #e2e8f0; border-radius: 6px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); z-index: 1000; position: absolute; min-width: 200px;">
							<h6 style="margin: 0 0 8px 0; color: #333;">${titlePrefix}: ${cleanName}</h6>
							<p style="margin: 0 0 4px 0; font-size: 12px;"><strong>Start:</strong> ${startDate}</p>
							<p style="margin: 0 0 4px 0; font-size: 12px;"><strong>End:</strong> ${endDate}</p>
							<p style="margin: 0; font-size: 12px;"><strong>Progress:</strong> ${Math.round(item.progress)}%</p>
						</div>
					`;
				}
			});

            $chartWrapper.find('.gantt-container').off('click', '.bar-label').on('click', '.bar-label', (e) => {
                e.stopPropagation(); 
                this.isTogglingNode = true;
                setTimeout(() => this.isTogglingNode = false, 300); 
                
                let wrapper = $(e.currentTarget).closest('.bar-wrapper');
                let id = wrapper.attr('data-id');
                let item = mappedItems.find(i => i.id === id);
                
                if (item && item.hasChildren) {
                    if (this.collapsedNodes.has(id)) this.collapsedNodes.delete(id);
                    else this.collapsedNodes.add(id);
                    this.render_gantt(true); 
                }
            });

			const apply_scroll = () => {
                const real_container = $chartWrapper.find(".gantt-container")[0];
                if (!real_container) return;

                if (do_preserve) {
                    real_container.scrollTo({ left: scroll_left, top: scroll_top, behavior: "auto" });
                } else {
                    // This frappe-gantt build marks today with `.current-highlight`
                    // (NOT `.today-highlight`). scroll_to:"today" already handles the
                    // common case; this is a layout-timing backup.
                    const today_el = real_container.querySelector(".current-highlight, .current-date-highlight, .today");
                    if (today_el) {
                        const container_width = real_container.clientWidth;
                        const element_left_relative = today_el.getBoundingClientRect().left - real_container.getBoundingClientRect().left;
                        const scroll_to_position = real_container.scrollLeft + element_left_relative - container_width / 2;
                        real_container.scrollTo({ left: scroll_to_position, behavior: "smooth" });
                    }
                }
            };
            // Run twice so a preserve-restore wins over any late library scroll.
            setTimeout(apply_scroll, 50);
            if (do_preserve) setTimeout(apply_scroll, 200);
		});
	}

	handle_date_change(item, start, end) {
		// Master rows are aggregates of their children — not directly editable.
		// Re-render (preserving scroll) so the bar snaps back to its computed span.
		if (!item || item.isMaster) {
			this.capture_scroll_for_next_render();
			this.render_gantt(true);
			return;
		}

		const startStr = moment(start).format("YYYY-MM-DD");
		const endStr = moment(end).format("YYYY-MM-DD");

		let method, args;
		if (item.isTask) {
			method = "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.update_task_dates_from_gantt";
			args = { task_name: item.task_docname, start_date: startStr, end_date: endStr };
		} else if (item.isProject) {
			method = "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.update_project_dates_from_gantt";
			args = { project_name: item.project_docname, start_date: startStr, end_date: endStr };
		} else {
			return;
		}

		frappe.call({
			method,
			args,
			callback: (r) => {
				if (r.message && r.message.status === "success") {
					frappe.show_alert({ message: __("Dates updated"), indicator: "green" });
				} else {
					frappe.show_alert({ message: __((r.message && r.message.message) || "Failed to update dates"), indicator: "red" });
				}
				// Re-fetch so dependency-driven date shifts are reflected, keeping
				// the current scroll position.
				this.capture_scroll_for_next_render();
				this.fetch_and_render_data();
			},
		});
	}

	show_skeleton() {
		if (this.chartContainer) {
			this.chartContainer.html(`
				<div class="skeleton-list p-4">
					<div class="skeleton-line" style="width: 100%; height: 20px; margin-bottom: 10px;"></div>
					<div class="skeleton-line" style="width: 100%; height: 20px; margin-bottom: 10px;"></div>
					<div class="skeleton-line" style="width: 100%; height: 20px; margin-bottom: 10px;"></div>
					<div class="skeleton-line" style="width: 100%; height: 20px;"></div>
				</div>
			`);
		}
	}

	handle_error(error) {
		if (error.name === "CancellationError") return;
		console.error("Portfolio Gantt Error:", error);
		this.wrapper.html(`
			<div class="alert alert-danger p-4 text-center">
				<h4><i class="fa fa-exclamation-triangle mr-2"></i> Failed to Load Data</h4>
				<p>${error.message || "An unexpected error occurred."}</p>
				<button class="btn btn-primary btn-sm mt-3 retry-btn">Retry</button>
			</div>
		`);
		this.wrapper.find(".retry-btn").on("click", () => this.render());
	}

	unmount() {
		if (this.abortController) this.abortController.abort();
		this.wrapper.empty();
	}
};