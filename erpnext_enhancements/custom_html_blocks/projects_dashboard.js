/*
 * Projects Dashboard — behaviour for the "Projects Dashboard" Custom HTML Block.
 *
 * Exported source of a Frappe Custom HTML Block (authored/stored in the Frappe UI);
 * this repo copy is the source of truth / backup — edit here, paste back into the
 * UI to deploy. Pairs with projects_dashboard.html (shell) + projects_dashboard.css.
 *
 * Runs inside the Custom HTML Block sandbox, where `root_element` is the block's
 * root node. It loads the shared ColumnSelector asset, then fetches data through
 * the project_dashboard page's whitelisted methods
 * (erpnext_enhancements.project_enhancements.page.project_dashboard.*) and renders:
 *   - Priority Overview   — client-facing value streams (Build/Design/Rent/Service),
 *                           with inline-editable company/project priority cells;
 *   - Active Internal Projects — grouped by master project, inline status/priority;
 *   - Completed Projects  — read-only list of inactive projects;
 *   - Portfolio Gantt     — frappe-gantt of projects (and optionally tasks), with
 *                           project/status filters, collapsible master/project/task
 *                           nodes, drag-to-reschedule, and scroll preservation.
 * Table edits and Gantt date drags persist back via the same whitelisted methods.
 */
(function() {
    // The ColumnSelector / ColumnResizer classes live in separate assets registered
    // via app_include_js. Don't assume that bundle has already executed when this
    // block renders -- load them explicitly so the dashboard works regardless of
    // asset load order / build state.
    frappe.provide("erpnext_enhancements.dashboard_components");
    frappe.require(
        [
            "/assets/erpnext_enhancements/js/project_enhancements/dashboard_components/column_selector.js",
            "/assets/erpnext_enhancements/js/project_enhancements/dashboard_components/column_resizer.js",
        ],
        init_dashboard
    );

    function init_dashboard() {
    const $root = $(root_element);

    let current_tab = "priority-overview";
    let project_data = [];
    let priority_options = { project_priority: [], company_priority: [] };
    let status_options = [];

    // Client-facing value streams shown on Priority Overview and used as the
    // Portfolio Gantt fallback. Kept in one place so this Custom HTML Block stays
    // in sync with the page dashboard (priority_overview.js / the backend's
    // get_all_projects_for_gantt), which filter to exactly these project types.
    const PRIORITY_PROJECT_TYPES = ["Build", "Design", "Rent", "Service", "Delivery"];

    // Business-preferred ordering for the value-stream groups on Priority
    // Overview (when sorted by Project Priority). Streams not listed (e.g.
    // Delivery, Uncategorized) fall after these, alphabetically. Kept in sync
    // with priority_overview.js's VALUE_STREAM_ORDER.
    const VALUE_STREAM_ORDER = ["Design", "Build", "Service", "Rent"];
    const compare_value_streams = (a, b) => {
        let ia = VALUE_STREAM_ORDER.indexOf(a);
        let ib = VALUE_STREAM_ORDER.indexOf(b);
        if (ia === -1) ia = VALUE_STREAM_ORDER.length;
        if (ib === -1) ib = VALUE_STREAM_ORDER.length;
        if (ia !== ib) return ia - ib;
        return String(a).localeCompare(String(b));
    };

    // Gantt State tracking
    let gantt_detailed_view = false;
    let gantt_status_filters = ["Active", "Working", "Client Hold"]; 
    const all_gantt_statuses = ["Active", "Working", "Client Hold", "Parked", "Completed", "Invoiced", "Paid", "Canceled"];
    
    let portfolio_gantt_instance = null; 
    let gantt_current_data = null; 
    let gantt_selected_projects = new Set(); 
    let gantt_collapsed_nodes = new Set(); 

    let is_toggling_gantt_node = false;

    // Scroll preservation: set by actions that re-render the Gantt but should keep
    // the viewport (expand/collapse, date drag) rather than snapping back to today.
    let gantt_preserve_next = false;
    let gantt_pending_scroll = null;

    let sort_state = {
        'priority-overview': { col: 'company_priority', order: 'asc' },
        'active-internal-projects': { col: 'project_name', order: 'asc' },
        'completed-projects': { col: 'completed_on', order: 'desc' }
    };

    const ColumnSelector = erpnext_enhancements.dashboard_components.ColumnSelector;
    const column_selectors = {
        'priority-overview': new ColumnSelector('chb_priority_overview_columns', [
            { key: 'project_name', label: 'Project Name', locked: true },
            { key: 'project_id', label: 'Project ID' },
            { key: 'company_priority', label: 'Company Priority' },
            { key: 'project_priority', label: 'Value Stream Priority' },
            { key: 'percent_complete', label: 'Completion' },
            { key: 'spend_percent', label: 'Spend %' }
        ]),
        'active-internal-projects': new ColumnSelector('chb_active_internal_columns', [
            { key: 'project_name', label: 'Project Name', locked: true },
            { key: 'project_id', label: 'Project ID' },
            { key: 'status', label: 'Status' },
            { key: 'custom_project_priority', label: 'Priority' },
            { key: 'percent_complete', label: '% Complete' },
            { key: 'project_user', label: 'Assigned To' }
        ]),
        'completed-projects': new ColumnSelector('chb_completed_columns', [
            { key: 'project_name', label: 'Project Name', locked: true },
            { key: 'project_id', label: 'Project ID' },
            { key: 'status', label: 'Status' },
            { key: 'project_type', label: 'Type' },
            { key: 'project_user', label: 'Assigned To' },
            { key: 'completed_on', label: 'Completed On' }
        ])
    };

    // Per-tab drag-to-resize column widths (persisted per user in localStorage).
    // Defaults roughly match the columns' historical min-widths so the baseline
    // look is preserved under the fixed table layout the resizer relies on.
    const ColumnResizer = erpnext_enhancements.dashboard_components.ColumnResizer;
    const make_table_resizer = (storageKey, columns) =>
        new ColumnResizer(storageKey, columns, {
            applyWidth: (root, key, px) => {
                const $cells = root.find(`.dashcol-${key}`);
                $cells.css("width", px == null ? "" : px + "px");
            },
            measureWidth: (root, key) => {
                const $th = root.find(`th.dashcol-${key}`).filter(":visible").first();
                return $th.length ? $th.outerWidth() : 130;
            },
        });
    const column_resizers = {
        'priority-overview': make_table_resizer('chb_priority_overview_widths', [
            { key: 'project_name', defaultWidth: 220, minWidth: 140, maxWidth: 520 },
            { key: 'project_id', defaultWidth: 130, minWidth: 90, maxWidth: 320 },
            { key: 'company_priority', defaultWidth: 160, minWidth: 110, maxWidth: 320 },
            { key: 'project_priority', defaultWidth: 170, minWidth: 120, maxWidth: 340 },
            { key: 'percent_complete', defaultWidth: 160, minWidth: 120, maxWidth: 320 },
            { key: 'spend_percent', defaultWidth: 140, minWidth: 90, maxWidth: 300 }
        ]),
        'active-internal-projects': make_table_resizer('chb_active_internal_widths', [
            { key: 'project_name', defaultWidth: 220, minWidth: 140, maxWidth: 520 },
            { key: 'project_id', defaultWidth: 130, minWidth: 90, maxWidth: 320 },
            { key: 'status', defaultWidth: 160, minWidth: 110, maxWidth: 320 },
            { key: 'custom_project_priority', defaultWidth: 160, minWidth: 110, maxWidth: 320 },
            { key: 'percent_complete', defaultWidth: 160, minWidth: 120, maxWidth: 320 },
            { key: 'project_user', defaultWidth: 160, minWidth: 110, maxWidth: 320 }
        ]),
        'completed-projects': make_table_resizer('chb_completed_widths', [
            { key: 'project_name', defaultWidth: 220, minWidth: 140, maxWidth: 520 },
            { key: 'project_id', defaultWidth: 130, minWidth: 90, maxWidth: 320 },
            { key: 'status', defaultWidth: 140, minWidth: 100, maxWidth: 300 },
            { key: 'project_type', defaultWidth: 160, minWidth: 110, maxWidth: 320 },
            { key: 'project_user', defaultWidth: 160, minWidth: 110, maxWidth: 320 },
            { key: 'completed_on', defaultWidth: 150, minWidth: 110, maxWidth: 300 }
        ])
    };

    // Column key parsed from a header cell's `dashcol-<key>` class.
    function resizer_key_of($th) {
        const m = (($th.attr('class') || '').match(/dashcol-([A-Za-z0-9_]+)/));
        return m ? m[1] : null;
    }

    // Apply saved widths and (re)attach the drag handles after a tab re-renders.
    function apply_column_resizing(container) {
        const resizer = column_resizers[current_tab];
        if (!resizer) return;
        resizer.apply(container);
        resizer.attach_handles(container, container.find('table thead th'), resizer_key_of);
    }

    function render_column_toolbar(container) {
        let selector = column_selectors[current_tab];
        if (!selector) return;
        let toolbar = $('<div class="dashboard-list-toolbar"></div>');
        container.append(toolbar);
        if (column_resizers[current_tab]) {
            let reset_btn = $('<button type="button" class="btn btn-sm btn-default dashboard-reset-widths" title="Reset column widths to default"><i class="fa fa-arrows-h mr-1"></i> Reset widths</button>');
            reset_btn.on('click', () => {
                column_resizers[current_tab].reset(container);
                render_current_tab();
            });
            toolbar.append(reset_btn);
        }
        selector.render_button(toolbar, () => selector.apply(container));
    }

    // Project ID header is non-sortable; built inline to sit beside Project Name.
    const project_id_th = '<th class="dashcol dashcol-project_id" style="min-width: 120px; white-space: nowrap;">Project ID</th>';

    const api_call = (method, args = {}) => {
        return new Promise((resolve, reject) => {
            frappe.call({
                method: `erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.${method}`,
                args: args,
                callback: (r) => resolve(r),
                error: (r) => reject(r)
            });
        });
    };

    function sanitizeId(str) {
        return String(str || "").replace(/[^a-zA-Z0-9\-_]/g, '_');
    }

    function show_skeleton() {
        $root.find('#dashboard-content').html(`
            <div class="skeleton-list p-4">
                <div class="skeleton-line" style="width: 100%; height: 20px; margin-bottom: 10px;"></div>
                <div class="skeleton-line" style="width: 100%; height: 20px; margin-bottom: 10px;"></div>
                <div class="skeleton-line" style="width: 100%; height: 20px; margin-bottom: 10px;"></div>
                <div class="skeleton-line" style="width: 100%; height: 20px;"></div>
            </div>
        `);
    }

    async function fetch_initial_data() {
        show_skeleton();
        init_gantt_filters();

        try {
            const [projectsRes, priorityRes, statusRes] = await Promise.all([
                api_call('get_project_data'),
                api_call('get_priority_options'),
                api_call('get_status_options')
            ]);
            
            if (priorityRes.message && !priorityRes.message.error) {
                priority_options = priorityRes.message;
            }
            if (statusRes.message && !statusRes.message.error) {
                status_options = statusRes.message;
            }
            if (projectsRes.message && !projectsRes.message.error) {
                project_data = projectsRes.message;
                render_current_tab();
            } else {
                $root.find('#dashboard-content').html('<div class="alert alert-danger">Error loading projects.</div>');
            }
        } catch (err) {
            $root.find('#dashboard-content').html('<div class="alert alert-danger">Network Error.</div>');
        }
    }

    // ----- GANTT CHART LOGIC -----
    
    function init_gantt_filters() {
        $root.find('.custom-dropdown-toggle').on('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            let $menu = $(this).next('.dropdown-menu');
            let isShown = $menu.hasClass('show');
            $root.find('.dropdown-menu').removeClass('show'); 
            if (!isShown) $menu.addClass('show');
        });

        $(document).on('click', function(e) {
            if (!$(e.target).closest('.check-dropdown').length) {
                $root.find('.dropdown-menu').removeClass('show');
            }
        });

        $root.find('.check-dropdown .dropdown-menu').on('click', function(e) { 
            e.stopPropagation(); 
        });

        const statusContainer = $root.find('#gantt-status-checkboxes');
        statusContainer.empty();
        all_gantt_statuses.forEach(s => {
            let safe_id = sanitizeId(s);
            statusContainer.append(`
                <div class="custom-control custom-checkbox mb-1">
                    <input type="checkbox" class="custom-control-input gantt-status-cb" value="${s}" id="filter-gantt-${safe_id}" ${gantt_status_filters.includes(s) ? 'checked' : ''}>
                    <label class="custom-control-label" for="filter-gantt-${safe_id}" style="cursor: pointer; padding-top: 2px;">${s}</label>
                </div>
            `);
        });

        $root.find('#apply-gantt-status-filters').on('click', function() {
            let selected = [];
            $root.find('.gantt-status-cb:checked').each(function() { selected.push($(this).val()); });
            gantt_status_filters = selected;
            $root.find('#ganttStatusDropdown').text(`Selected (${selected.length})`);
            $root.find('#gantt-status-menu').removeClass('show');
            fetch_and_render_portfolio_gantt(); 
        });

        $root.find('#gantt-detailed-view').on('change', function() {
            gantt_detailed_view = $(this).is(':checked');
            if (gantt_detailed_view && gantt_current_data) {
                gantt_collapsed_nodes.clear();
                gantt_current_data.projects.forEach(p => {
                    gantt_collapsed_nodes.add('project_' + sanitizeId(p.name));
                });
            }
            // Keep the current scroll position when expanding/collapsing task detail.
            capture_gantt_scroll();
            fetch_and_render_portfolio_gantt();
        });

        $root.find('.view-mode-group button').on('click', function() {
            $root.find('.view-mode-group button').removeClass('active btn-secondary').addClass('btn-outline-secondary');
            $(this).addClass('active btn-secondary').removeClass('btn-outline-secondary');
            let mode = $(this).data('view');
            if (portfolio_gantt_instance) portfolio_gantt_instance.change_view_mode(mode);
        });

        $root.find('#gantt-today-btn').on('click', function() {
            if (!portfolio_gantt_instance) return;
            // Use the library's own routine (locates today via the date cells).
            // The old code queried `.today-highlight`, which this build never emits.
            portfolio_gantt_instance.set_scroll_position('today');
        });

        $root.find('#gantt-select-all-projects').on('change', function() {
            let isChecked = $(this).is(':checked');
            $root.find('.gantt-proj-cb').prop('checked', isChecked);
        });

        $root.find('#gantt-project-search, #global-project-search').on('keydown keyup keypress input', function(e) {
            e.stopPropagation();
            if (e.key === 'Enter') e.preventDefault();
        });

        $root.find('#gantt-project-search').on('input', function() {
            let val = $(this).val().toLowerCase();
            $root.find('.gantt-proj-filter-item').each(function() {
                if ($(this).data('name').includes(val)) $(this).show();
                else $(this).hide();
            });
        });

        $root.find('#apply-gantt-project-filters').on('click', function() {
            gantt_selected_projects.clear();
            let total = $root.find('.gantt-proj-cb').length;
            let checked = $root.find('.gantt-proj-cb:checked');
            
            if (checked.length < total) {
                checked.each(function() { gantt_selected_projects.add($(this).val()); });
                $root.find('#ganttProjectDropdown').text(`Projects (${checked.length})`);
            } else {
                $root.find('#ganttProjectDropdown').text('All Projects');
            }
            $root.find('#gantt-project-menu').removeClass('show');
            build_gantt_chart(false); 
        });
    }

    function populate_projects_dropdown(projects) {
        const container = $root.find('#gantt-project-checkboxes');
        container.empty();
        
        let sorted = [...projects].sort((a,b) => (a.project_name||a.name).localeCompare(b.project_name||b.name));
        
        sorted.forEach(p => {
            let isChecked = gantt_selected_projects.size === 0 || gantt_selected_projects.has(p.name);
            let safe_name = (p.project_name || p.name).toLowerCase().replace(/"/g, ''); 
            let safe_id = sanitizeId(p.name);

            container.append(`
                <div class="custom-control custom-checkbox mb-1 gantt-proj-filter-item" data-name="${safe_name}">
                    <input type="checkbox" class="custom-control-input gantt-proj-cb" value="${p.name}" id="filter-proj-${safe_id}" ${isChecked ? 'checked' : ''}>
                    <label class="custom-control-label" for="filter-proj-${safe_id}" style="cursor: pointer; padding-top: 2px;">${p.project_name || p.name}</label>
                </div>
            `);
        });

        let total = sorted.length;
        let checked = $root.find('.gantt-proj-cb:checked').length;
        $root.find('#gantt-select-all-projects').prop('checked', total > 0 && checked === total);
    }

    function get_fallback_gantt_data_from_dashboard_projects() {
        const selected_statuses = new Set(gantt_status_filters || []);
        const projects = (project_data || []).filter(p => {
            if (p.is_active !== "Yes") return false;
            if (p.status === "Canceled") return false;
            // Keep the fallback in sync with the backend: client-facing work only.
            if (!PRIORITY_PROJECT_TYPES.includes(p.project_type)) return false;
            return selected_statuses.size === 0 || selected_statuses.has(p.status);
        });

        return {
            projects,
            tasks: []
        };
    }

    async function fetch_and_render_portfolio_gantt() {
        let container = $root.find('#dashboard-content');
        container.empty().html('<p class="text-muted text-center p-4"><i class="fa fa-spinner fa-spin mr-2"></i>Fetching Gantt Data...</p>');

        try {
            const res = await api_call('get_all_projects_for_gantt', { 
                include_tasks: gantt_detailed_view ? 1 : 0, 
                statuses: JSON.stringify(gantt_status_filters) 
            });

            if (!res.message || res.message.error || res.message.projects.length === 0) {
                // Fall back to the project data the dashboard already loaded.
                // This covers both an empty result and a backend error (e.g. a
                // permission gate), so the Gantt still renders the active
                // projects instead of showing nothing.
                const fallback_data = get_fallback_gantt_data_from_dashboard_projects();

                if (fallback_data.projects.length === 0) {
                    container.html('<div class="alert alert-info">No projects match the current filters.</div>');
                    portfolio_gantt_instance = null;
                    return;
                }

                gantt_current_data = fallback_data;
            } else {
                gantt_current_data = res.message;
            }

            populate_projects_dropdown(gantt_current_data.projects);
            build_gantt_chart(false);

        } catch (err) {
            console.error(err);
            container.html('<div class="alert alert-danger">An error occurred while fetching the Gantt chart data.</div>');
        }
    }

    function build_gantt_chart(preserve_scroll = false) {
        if (!gantt_current_data || !gantt_current_data.projects) return;

        let container = $root.find('#dashboard-content');
        let data = gantt_current_data;

        // Capture the current scroll position BEFORE the chart is torn down, so a
        // preserve re-render restores it. Declaring these here also fixes a
        // ReferenceError: they were used in the restore block but never declared.
        const do_preserve = preserve_scroll || gantt_preserve_next;
        let scroll_left = 0, scroll_top = 0;
        if (do_preserve) {
            if (gantt_pending_scroll) {
                scroll_left = gantt_pending_scroll.left;
                scroll_top = gantt_pending_scroll.top;
            } else {
                const existing_gc = $root.find(".gantt-container")[0];
                if (existing_gc) { scroll_left = existing_gc.scrollLeft; scroll_top = existing_gc.scrollTop; }
            }
        }
        gantt_preserve_next = false;
        gantt_pending_scroll = null;

        let filtered_projects = data.projects;
        if (gantt_selected_projects.size > 0) {
            filtered_projects = data.projects.filter(p => gantt_selected_projects.has(p.name));
        }

        if (filtered_projects.length === 0) {
            container.html('<div class="alert alert-info">No projects match your selection.</div>');
            portfolio_gantt_instance = null;
            return;
        }

        let taskMap = {};
        let projectTaskRoots = {};
        if (gantt_detailed_view && data.tasks) {
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

        filtered_projects.forEach(p => {
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

        const baseHues = [210, 145, 280, 35, 0, 175, 15]; 
        let project_hue_counter = 0;
        let dynamicStyles = "";

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
            if (!masterEnd || masterEnd < masterStart) masterEnd = new Date(masterStart.getTime() + (24*60*60*1000));
            let avgProgress = projects.length > 0 ? (totalProgress / projects.length) : 0;

            let master_id = 'master_' + sanitizeId(master);
            let is_m_collapsed = gantt_collapsed_nodes.has(master_id);

            if (!is_independent) {
                let m_prefix = projects.length > 0 ? (is_m_collapsed ? '<tspan class="gantt-toggle-btn">▶</tspan> ' : '<tspan class="gantt-toggle-btn">▼</tspan> ') : '';
                let m_hue = baseHues[project_hue_counter % baseHues.length]; 
                let m_color = `hsl(${m_hue}, 75%, 35%)`;

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

                dynamicStyles += `
                    svg.gantt [data-id="${master_id}"] .bar { fill: ${m_color} !important; }
                    svg.gantt [data-id="${master_id}"] .bar-progress { fill: hsl(${m_hue}, 75%, 25%) !important; }
                `;
            }

            if (!is_independent && is_m_collapsed) return; 

            projects.forEach(p => {
                let p_hue = baseHues[project_hue_counter % baseHues.length];
                project_hue_counter++;

                let pColor = `hsl(${p_hue}, 70%, 45%)`; 
                let pDates = getSafeDates(p.expected_start_date, p.expected_end_date);
                let p_id = 'project_' + sanitizeId(p.name);
                let t_roots = projectTaskRoots[p.name] || [];
                let has_tasks = gantt_detailed_view && t_roots.length > 0;
                let is_p_collapsed = gantt_collapsed_nodes.has(p_id);
                
                let base_indent = is_independent ? '' : '  ';
                let p_prefix = base_indent + (has_tasks ? (is_p_collapsed ? '<tspan class="gantt-toggle-btn">▶</tspan> ' : '<tspan class="gantt-toggle-btn">▼</tspan> ') : (is_independent ? '' : '↳ '));

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
                    hasChildren: has_tasks,
                    task_color: pColor
                });

                dynamicStyles += `
                    svg.gantt [data-id="${p_id}"] .bar { fill: ${pColor} !important; }
                    svg.gantt [data-id="${p_id}"] .bar-progress { fill: hsl(${p_hue}, 70%, 35%) !important; }
                    svg.gantt path[data-from="${p_id}"] { stroke: ${pColor} !important; stroke-width: 2px !important; opacity: 1 !important; }
                `;

                if (!has_tasks || is_p_collapsed) return; 

                const pushTasks = (tasks, indentLevel, inheritedColorObj) => {
                    tasks.forEach((t, t_idx) => {
                        let tColor;
                        if (indentLevel === 0) {
                            const lightnesses = [55, 35, 65, 40, 50, 30];
                            const saturations = [80, 60, 95, 70, 85, 65];
                            let l = lightnesses[t_idx % lightnesses.length];
                            let s = saturations[t_idx % saturations.length];
                            tColor = {
                                bar: `hsl(${p_hue}, ${s}%, ${l}%)`,
                                prog: `hsl(${p_hue}, ${s}%, ${Math.max(10, l - 10)}%)`
                            };
                        } else {
                            tColor = inheritedColorObj;
                        }

                        let tDates = getSafeDates(t.exp_start_date, t.exp_end_date, pDates.start);
                        let t_id = 'task_' + sanitizeId(t.name);
                        let has_sub = t.children && t.children.length > 0;
                        let is_t_collapsed = gantt_collapsed_nodes.has(t_id);

                        let baseIndent = is_independent ? '  ' : '    ';
                        for(let i=0; i<indentLevel; i++) baseIndent += '  ';
                        let t_prefix = has_sub ? (is_t_collapsed ? baseIndent + '<tspan class="gantt-toggle-btn">▶</tspan> ' : baseIndent + '<tspan class="gantt-toggle-btn">▼</tspan> ') : baseIndent + '• ';
                        
                        let dep_id = indentLevel === 0 ? p_id : 'task_' + sanitizeId(t.parent_task);

                        mappedItems.push({
                            id: t_id,
                            name: t_prefix + (t.subject || t.name),
                            start: moment(tDates.start).format("YYYY-MM-DD"),
                            end: moment(tDates.end).format("YYYY-MM-DD"),
                            progress: t.progress || 0,
                            dependencies: dep_id,
                            custom_class: 'gantt-task', 
                            custom_start_date: t.exp_start_date || p.expected_start_date,
                            isTask: true,
                            task_docname: t.name,
                            hasChildren: has_sub,
                            task_color: tColor.bar
                        });

                        // FIX: Explicitly applying the same 3px translation to the .bar-label as the bar itself
                        dynamicStyles += `
                            svg.gantt [data-id="${t_id}"] .bar { fill: ${tColor.bar} !important; height: 14px !important; transform: translateY(3px) !important; opacity: 1 !important; }
                            svg.gantt [data-id="${t_id}"] .bar-progress { fill: ${tColor.prog} !important; height: 14px !important; transform: translateY(3px) !important; }
                            svg.gantt [data-id="${t_id}"] .bar-label { transform: translateY(3px) !important; }
                            svg.gantt path[data-from="${t_id}"], svg.gantt path[data-to="${t_id}"] { stroke: ${tColor.bar} !important; stroke-width: 1.5px !important; opacity: 1 !important;}
                        `;

                        if (has_sub && !is_t_collapsed) {
                            pushTasks(t.children, indentLevel + 1, tColor);
                        }
                    });
                };

                pushTasks(t_roots, 0, null);
            });
        });

        $('#dynamic-gantt-colors').remove();
        let $chartWrapper = $('<div id="gantt-chart-target" style="width: 100%; height: 600px;"></div>');
        $chartWrapper.append(`<style id="dynamic-gantt-colors">${dynamicStyles}</style>`);
        
        container.empty().append($chartWrapper);

        frappe.require(["/assets/erpnext_enhancements/js/project_enhancements/lib/frappe-gantt.umd.js"], () => {
            let activeViewMode = $root.find('.view-mode-group button.active').data('view') || "Month";

            portfolio_gantt_instance = new Gantt($chartWrapper[0], mappedItems, {
                view_mode: activeViewMode,
                auto_move_label: true,
                // On a preserve render, suppress the library's scroll-to-today so
                // only our manual restore (below) moves the viewport.
                scroll_to: do_preserve ? null : "today",
                on_click: (item) => {
                    if (is_toggling_gantt_node) return;
                    if (item.isProject) frappe.set_route("Form", "Project", item.project_docname);
                    else if (item.isTask) frappe.set_route("Form", "Task", item.task_docname);
                },
                on_date_change: (item, start, end) => {
                    if (!item || item.isMaster) { capture_gantt_scroll(); build_gantt_chart(true); return; }
                    const s = moment(start).format("YYYY-MM-DD");
                    const e2 = moment(end).format("YYYY-MM-DD");
                    let method, args;
                    if (item.isTask) { method = "update_task_dates_from_gantt"; args = { task_name: item.task_docname, start_date: s, end_date: e2 }; }
                    else if (item.isProject) { method = "update_project_dates_from_gantt"; args = { project_name: item.project_docname, start_date: s, end_date: e2 }; }
                    else return;
                    api_call(method, args).then((r) => {
                        if (r.message && r.message.status === "success") {
                            frappe.show_alert({ message: __("Dates updated"), indicator: "green" });
                        } else {
                            frappe.show_alert({ message: __((r.message && r.message.message) || "Failed to update dates"), indicator: "red" });
                        }
                        // Re-fetch (to reflect cascaded successor shifts) but keep scroll.
                        capture_gantt_scroll();
                        fetch_and_render_portfolio_gantt();
                    });
                },
                custom_popup_html: function (item) {
                    const cleanName = item.name.replace(/<[^>]*>?/gm, '').replace(/[↳•▼▶]/g, '').trim();
                    if (item.isMaster) {
                        return `<div class="gantt-popup" style="padding: 10px; background: var(--card-bg); color: var(--text-color); border: 1px solid var(--border-color); border-radius: 4px;">
                                    <h5 class="mb-1">${cleanName}</h5>
                                    <p class="mb-0 text-muted"><strong>Overall Progress:</strong> ${Math.round(item.progress)}%</p>
                                </div>`;
                    }
                    const startDate = frappe.datetime.str_to_user(item.custom_start_date);
                    const endDate = frappe.datetime.str_to_user(item.end);
                    const titlePrefix = item.isTask ? "Task" : "Project";

                    return `
                        <div class="gantt-popup" style="padding: 12px; background: var(--card-bg); color: var(--text-color); border: 1px solid var(--border-color); border-radius: 6px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); z-index: 1000; position: absolute; min-width: 200px;">
                            <h6 style="margin: 0 0 8px 0; color: var(--text-color);">${titlePrefix}: ${cleanName}</h6>
                            <p style="margin: 0 0 4px 0; font-size: 12px;"><strong>Start:</strong> ${startDate}</p>
                            <p style="margin: 0 0 4px 0; font-size: 12px;"><strong>End:</strong> ${endDate}</p>
                            <p style="margin: 0; font-size: 12px;"><strong>Progress:</strong> ${Math.round(item.progress)}%</p>
                        </div>
                    `;
                }
            });

            $chartWrapper.on('mousedown', '.gantt-toggle-btn', function(e) {
                is_toggling_gantt_node = true;
            });

            $chartWrapper.on('click', '.gantt-toggle-btn', function(e) {
                e.stopPropagation(); 
                is_toggling_gantt_node = true;
                setTimeout(() => is_toggling_gantt_node = false, 300); 
                
                let wrapper = $(this).closest('.bar-wrapper');
                let id = wrapper.attr('data-id');
                let item = mappedItems.find(i => i.id === id);
                
                if (item && item.hasChildren) {
                    if (gantt_collapsed_nodes.has(id)) gantt_collapsed_nodes.delete(id);
                    else gantt_collapsed_nodes.add(id);
                    build_gantt_chart(true); 
                }
            });

            function applyColors() {
                mappedItems.forEach(item => {
                    if (item.task_color) {
                        try {
                            $chartWrapper.find(`[data-id="${item.id}"] .bar`).css('fill', item.task_color);
                            $chartWrapper.find(`path[data-from="${item.id}"], path[data-to="${item.id}"]`).css({
                                'stroke': item.task_color,
                                'stroke-width': item.isTask ? '1.5px' : '2px',
                                'opacity': '1'
                            });
                        } catch(e) {}
                    }
                });
            }

            setTimeout(applyColors, 100);
            $chartWrapper.on('scroll mousewheel touchmove click', '.gantt-container', function() {
                clearTimeout(window.colorRefreshTimer);
                window.colorRefreshTimer = setTimeout(applyColors, 50);
            });

            const apply_scroll = () => {
                const real_container = $chartWrapper.find(".gantt-container")[0];
                if (!real_container) return;

                if (do_preserve) {
                    real_container.scrollTo({ left: scroll_left, top: scroll_top, behavior: "auto" });
                } else {
                    portfolio_gantt_instance.set_scroll_position('today');
                }
            };
            // Run twice so a preserve-restore wins over any late library scroll.
            setTimeout(apply_scroll, 50);
            if (do_preserve) setTimeout(apply_scroll, 200);
        });
    }

    // Stash the current Gantt scroll position so the next build_gantt_chart()
    // restores it (used before a refetch that empties the chart container).
    function capture_gantt_scroll() {
        const gc = $root.find(".gantt-container")[0];
        if (gc) {
            gantt_pending_scroll = { left: gc.scrollLeft, top: gc.scrollTop };
            gantt_preserve_next = true;
        }
    }

    // ----- HELPERS & OTHER RENDERERS -----

    function get_priority_weight(priority) {
        if (!priority) return 100; 
        let p = String(priority).trim();
        if (p.toLowerCase() === "not assigned") return 100;
        if (p.toLowerCase() === "repair visit") return 101;
        if (p.toLowerCase() === "maintenance") return 102;
        let num = parseInt(p, 10);
        if (!isNaN(num)) return num; 
        return 200; 
    }

    function get_priority_badge(priority) {
        if (!priority) return '<span class="badge badge-secondary">Not Assigned</span>';
        let p = String(priority).trim();
        if (p.toLowerCase() === "not assigned") return '<span class="badge badge-secondary">Not Assigned</span>';
        if (p.toLowerCase() === "repair visit") return '<span class="badge" style="background-color: #6f42c1; color: white;">Repair Visit</span>';
        if (p.toLowerCase() === "maintenance") return '<span class="badge" style="background-color: #007bff; color: white;">Maintenance</span>';
        
        let num = parseInt(p, 10);
        if (!isNaN(num)) {
            let hue = ((Math.max(1, Math.min(30, num)) - 1) / 29) * 120;
            return `<span class="badge" style="background-color: hsl(${hue}, 100%, 45%); color: white;">${frappe.utils.escape_html(p)}</span>`;
        }
        return `<span class="badge badge-secondary">${frappe.utils.escape_html(p)}</span>`;
    }

    function build_editable_priority_cell(project_name, field, current_val, options_array, col_key) {
        let opts_html = '<option value="">Not Assigned</option>' +
            options_array.map(opt => `<option value="${opt}" ${opt === current_val ? 'selected' : ''}>${opt}</option>`).join('');

        return `
            <td class="editable-priority dashcol dashcol-${col_key}" data-project="${project_name}" data-field="${field}" style="min-width: 140px;">
                <div class="static-view" style="cursor: pointer;" title="Click to edit">
                    ${get_priority_badge(current_val)}
                </div>
                <select class="form-control form-control-sm edit-view" style="display: none;">
                    ${opts_html}
                </select>
            </td>
        `;
    }

    async function auto_save_field(project_name, field, value, cell_element) {
        cell_element.css({'opacity': '0.5', 'pointer-events': 'none'});
        try {
            let res = await api_call('update_project_details', { project_name: project_name, field: field, value: value });
            if (res.message && res.message.status === 'success') {
                frappe.show_alert({message: 'Changes Saved', indicator: 'green'});
                let p = project_data.find(proj => proj.name === project_name);
                if(p) p[field] = value;
            } else {
                throw new Error(res.message.message || "Failed to save");
            }
        } catch (e) {
            frappe.show_alert({message: e.message || 'Network error while saving', indicator: 'red'});
            render_current_tab(); 
        } finally {
            cell_element.css({'opacity': '', 'pointer-events': ''});
        }
    }

    function th(col_name, label, title="") {
        let state = sort_state[current_tab];
        let cls = "sortable-header dashcol dashcol-" + col_name;
        if (state && state.col === col_name) {
            cls += " active-sort sort-" + state.order;
        }
        return `<th class="${cls}" data-sort="${col_name}" title="${title}" style="min-width: 130px; white-space: nowrap;">${label}</th>`;
    }

    function bind_sortable_headers(table) {
        table.find('.sortable-header').on('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            
            let sort_col = $(this).attr('data-sort');
            let state = sort_state[current_tab];
            
            if (state.col === sort_col) {
                state.order = state.order === 'asc' ? 'desc' : 'asc';
            } else {
                state.col = sort_col;
                // Dates read best newest-first; text columns A->Z
                state.order = sort_col === 'completed_on' ? 'desc' : 'asc';
            }
            
            render_current_tab();
        });
    }

    function build_priority_table(projects) {
        let wrapper = $('<div class="table-responsive mb-4"></div>');
        let table = $(`
            <table class="table table-bordered mb-0 dashboard-resizable-table">
                <thead class="thead-light">
                    <tr>
                        ${th('project_name', 'Project Name')}
                        ${project_id_th}
                        ${th('company_priority', 'Company Priority')}
                        ${th('project_priority', 'Value Stream Priority', 'Groups by Value Stream')}
                        ${th('percent_complete', 'Completion')}
                        ${th('spend_percent', 'Spend %', 'Spend as % of total project budget')}
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        `);

        projects.forEach(p => {
            let total_budget = parseFloat(p.custom_project_dollar_amount) || 0;
            let spend = parseFloat(p.estimated_costing) || 0;
            let spend_percent = total_budget ? (spend / total_budget) * 100 : 0;
            let spend_color = spend_percent > 100 ? "text-danger" : "text-success";

            let row = $(`
                <tr>
                    <td class="dashcol dashcol-project_name project-name-cell" style="min-width: 200px;"><a href="/app/project/${p.name}" class="font-weight-bold">${p.project_name}</a></td>
                    <td class="dashcol dashcol-project_id project-id-cell"><a href="/app/project/${p.name}" class="text-muted">${p.name}</a></td>
                    ${build_editable_priority_cell(p.name, 'custom_company_priority', p.custom_company_priority, priority_options.company_priority || [], 'company_priority')}
                    ${build_editable_priority_cell(p.name, 'custom_project_priority', p.custom_project_priority, priority_options.project_priority || [], 'project_priority')}
                    <td class="dashcol dashcol-percent_complete" style="min-width: 150px;">
                        <div class="d-flex align-items-center">
                            <div class="progress flex-grow-1 mr-2" style="height: 10px;">
                                <div class="progress-bar bg-primary" style="width: ${p.percent_complete || 0}%"></div>
                            </div>
                            <span class="small font-weight-bold">${Math.round(p.percent_complete || 0)}%</span>
                        </div>
                    </td>
                    <td class="dashcol dashcol-spend_percent font-weight-bold ${spend_color}" style="min-width: 140px;">${total_budget ? Math.round(spend_percent) + '%' : '—'}</td>
                </tr>
            `);
            table.find('tbody').append(row);
        });

        table.find(".editable-priority").each((_, cellEl) => {
            const cell = $(cellEl);
            const select = cell.find("select.edit-view");
            const staticView = cell.find(".static-view");

            staticView.on("click", (e) => {
                e.stopPropagation();
                table.find(".edit-view").hide();
                table.find(".static-view").show();
                staticView.hide();
                select.show().focus();
            });

            select.on("blur", () => {
                setTimeout(() => {
                    select.hide();
                    staticView.show();
                }, 150);
            });

            select.on("change", () => {
                const val = select.val();
                staticView.html(get_priority_badge(val));
                select.hide();
                staticView.show();
                auto_save_field(cell.data('project'), cell.data('field'), val, cell);
            });
        });

        bind_sortable_headers(table);
        wrapper.append(table);
        return wrapper;
    }

    function render_priority_overview() {
        let container = $root.find('#dashboard-content');
        container.empty();
        render_column_toolbar(container);

        let state = sort_state['priority-overview'];

        // Match the page dashboard: only client-facing value streams (Build,
        // Design, Rent, Service) that are still active and in progress. The
        // is_active flag is unreliable on its own, so we gate on project_type +
        // is_active + a live status rather than denylisting internal types.
        let projects_to_show = project_data.filter(p =>
            PRIORITY_PROJECT_TYPES.includes(p.project_type) &&
            p.is_active === "Yes" &&
            p.status !== "Completed" &&
            p.status !== "Canceled"
        );

        if (state.col === "project_priority") {
            let groups = {};
            projects_to_show.forEach(p => {
                let stream = p.project_type || "Uncategorized";
                if (!groups[stream]) groups[stream] = [];
                groups[stream].push(p);
            });

            let sorted_streams = Object.keys(groups).sort(compare_value_streams);
            sorted_streams.forEach(stream => {
                let stream_projects = groups[stream].sort((a, b) => {
                    let weightA = get_priority_weight(a.custom_project_priority);
                    let weightB = get_priority_weight(b.custom_project_priority);
                    let diff = weightA - weightB;
                    if (diff === 0) diff = String(a.project_name || "").localeCompare(String(b.project_name || ""));
                    return state.order === 'asc' ? diff : -diff;
                });
                container.append(`<h5 class="mt-4 mb-3 text-muted border-bottom pb-2">${stream}</h5>`);
                container.append(build_priority_table(stream_projects));
            });
        } else {
            projects_to_show.sort((a, b) => {
                let diff = 0;
                if (state.col === 'project_name') {
                    diff = String(a.project_name || "").localeCompare(String(b.project_name || ""));
                } else if (state.col === 'company_priority') {
                    diff = get_priority_weight(a.custom_company_priority) - get_priority_weight(b.custom_company_priority);
                } else if (state.col === 'percent_complete') {
                    diff = (parseFloat(a.percent_complete) || 0) - (parseFloat(b.percent_complete) || 0);
                } else if (state.col === 'spend_percent') {
                    let budgetA = parseFloat(a.custom_project_dollar_amount) || 0;
                    let budgetB = parseFloat(b.custom_project_dollar_amount) || 0;
                    let pctA = budgetA ? ((parseFloat(a.estimated_costing) || 0) / budgetA) * 100 : 0;
                    let pctB = budgetB ? ((parseFloat(b.estimated_costing) || 0) / budgetB) * 100 : 0;
                    diff = pctA - pctB;
                }
                
                if (diff === 0 && state.col !== 'project_name') {
                    diff = String(a.project_name || "").localeCompare(String(b.project_name || ""));
                }
                return state.order === 'asc' ? diff : -diff;
            });
            container.append(build_priority_table(projects_to_show));
        }

        column_selectors['priority-overview'].apply(container);
        apply_column_resizing(container);
    }

    function build_internal_table(projects) {
        let wrapper = $('<div class="table-responsive mb-4"></div>');
        let table = $(`
            <table class="table table-bordered mb-0 dashboard-resizable-table">
                <thead class="thead-light">
                    <tr>
                        ${th('project_name', 'Project Name')}
                        ${project_id_th}
                        ${th('status', 'Status')}
                        ${th('custom_project_priority', 'Priority')}
                        ${th('percent_complete', '% Complete')}
                        ${th('project_user', 'Assigned To')}
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        `);

        projects.forEach(p => {
            let status_html = `<select class="form-control form-control-sm project-inline-edit" data-field="status" data-project="${p.name}">
                ${status_options.map(s => `<option value="${s}" ${p.status === s ? 'selected' : ''}>${s}</option>`).join('')}
            </select>`;

            let priority_opts = priority_options.project_priority || [];
            let priority_html = `<select class="form-control form-control-sm project-inline-edit" data-field="custom_project_priority" data-project="${p.name}">
                <option value="">Not Assigned</option>
                ${priority_opts.map(opt => `<option value="${opt}" ${p.custom_project_priority === opt ? 'selected' : ''}>${opt}</option>`).join('')}
            </select>`;

            table.find('tbody').append(`
                <tr>
                    <td class="dashcol dashcol-project_name project-name-cell" style="min-width: 200px;"><a href="/app/project/${p.name}" class="font-weight-bold">${p.project_name}</a></td>
                    <td class="dashcol dashcol-project_id project-id-cell"><a href="/app/project/${p.name}" class="text-muted">${p.name}</a></td>
                    <td class="dashcol dashcol-status" style="min-width: 140px;">${status_html}</td>
                    <td class="dashcol dashcol-custom_project_priority" style="min-width: 140px;">${priority_html}</td>
                    <td class="dashcol dashcol-percent_complete" style="min-width: 150px;">
                        <div class="d-flex align-items-center">
                            <div class="progress flex-grow-1 mr-2" style="height: 10px;">
                                <div class="progress-bar bg-primary" style="width: ${p.percent_complete || 0}%"></div>
                            </div>
                            <span class="small font-weight-bold">${Math.round(p.percent_complete || 0)}%</span>
                        </div>
                    </td>
                    <td class="dashcol dashcol-project_user" style="min-width: 150px;">${p.project_user || "Unassigned"}</td>
                </tr>
            `);
        });

        table.find('.project-inline-edit').on('change', function() {
            let select = $(this);
            auto_save_field(select.data('project'), select.data('field'), select.val(), select.closest('td'));
        });

        bind_sortable_headers(table);
        wrapper.append(table);
        return wrapper;
    }

    function render_active_internal() {
        let container = $root.find('#dashboard-content');
        container.empty();
        render_column_toolbar(container);

        let state = sort_state['active-internal-projects'];
        let internal_projects = project_data.filter(p => p.is_active === "Yes");

        let groups = {};
        internal_projects.forEach(p => {
            let master = p.custom_master_project || "Independent Projects";
            if (!groups[master]) groups[master] = [];
            groups[master].push(p);
        });

        let sorted_masters = Object.keys(groups).sort((a, b) => {
            if (a === "Independent Projects") return 1;
            if (b === "Independent Projects") return -1;
            return a.localeCompare(b);
        });

        sorted_masters.forEach(master => {
            let master_projects = groups[master].sort((a, b) => {
                let diff = 0;
                if (state.col === 'project_name') diff = String(a.project_name||"").localeCompare(String(b.project_name||""));
                else if (state.col === 'status') diff = String(a.status||"").localeCompare(String(b.status||""));
                else if (state.col === 'custom_project_priority') diff = get_priority_weight(a.custom_project_priority) - get_priority_weight(b.custom_project_priority);
                else if (state.col === 'percent_complete') diff = (parseFloat(a.percent_complete)||0) - (parseFloat(b.percent_complete)||0);
                else if (state.col === 'project_user') diff = String(a.project_user||"").localeCompare(String(b.project_user||""));
                
                if (diff === 0 && state.col !== 'project_name') diff = String(a.project_name||"").localeCompare(String(b.project_name||""));
                return state.order === 'asc' ? diff : -diff;
            });
            
            container.append(`<h5 class="mt-4 mb-3 text-muted border-bottom pb-2">${master}</h5>`);
            container.append(build_internal_table(master_projects));
        });

        column_selectors['active-internal-projects'].apply(container);
        apply_column_resizing(container);
    }

    function render_completed_projects() {
        let container = $root.find('#dashboard-content');
        container.empty();
        render_column_toolbar(container);

        let state = sort_state['completed-projects'];
        let completed_projects = project_data.filter(p => p.is_active === "No");

        completed_projects.sort((a, b) => {
            let diff = 0;
            if (state.col === 'project_name') diff = String(a.project_name||"").localeCompare(String(b.project_name||""));
            else if (state.col === 'status') diff = String(a.status||"").localeCompare(String(b.status||""));
            else if (state.col === 'project_type') diff = String(a.project_type||"").localeCompare(String(b.project_type||""));
            else if (state.col === 'project_user') diff = String(a.project_user||"").localeCompare(String(b.project_user||""));
            else if (state.col === 'completed_on') {
                // Projects without a completion date sink to the bottom either way
                if (!a.completed_on && b.completed_on) return 1;
                if (a.completed_on && !b.completed_on) return -1;
                diff = String(a.completed_on||"").localeCompare(String(b.completed_on||""));
            }

            if (diff === 0 && state.col !== 'project_name') diff = String(a.project_name||"").localeCompare(String(b.project_name||""));
            return state.order === 'asc' ? diff : -diff;
        });

        let wrapper = $('<div class="table-responsive mb-4"></div>');
        let table = $(`
            <table class="table table-bordered table-hover mb-0 dashboard-resizable-table">
                <thead class="thead-light">
                    <tr>
                        ${th('project_name', 'Project Name')}
                        ${project_id_th}
                        ${th('status', 'Status')}
                        ${th('project_type', 'Type')}
                        ${th('project_user', 'Assigned To')}
                        ${th('completed_on', 'Completed On', 'When the project was marked inactive')}
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        `);

        completed_projects.forEach(p => {
            let status_badge = 'secondary';
            if (p.status === 'Completed' || p.status === 'Paid') status_badge = 'success';
            else if (p.status === 'Active') status_badge = 'primary';
            else if (p.status === 'Invoiced') status_badge = 'info';

            table.find('tbody').append(`
                <tr>
                    <td class="dashcol dashcol-project_name project-name-cell" style="min-width: 200px;"><a href="/app/project/${p.name}" class="font-weight-bold">${p.project_name}</a></td>
                    <td class="dashcol dashcol-project_id project-id-cell"><a href="/app/project/${p.name}" class="text-muted">${p.name}</a></td>
                    <td class="dashcol dashcol-status" style="min-width: 120px;"><span class="badge badge-${status_badge}">${p.status}</span></td>
                    <td class="dashcol dashcol-project_type" style="min-width: 150px;">${p.project_type || "Uncategorized"}</td>
                    <td class="dashcol dashcol-project_user text-muted" style="min-width: 150px;">${p.project_user || "Unassigned"}</td>
                    <td class="dashcol dashcol-completed_on" style="min-width: 130px;">${p.completed_on ? frappe.datetime.str_to_user(p.completed_on) : '<span class="text-muted">—</span>'}</td>
                </tr>
            `);
        });

        bind_sortable_headers(table);
        wrapper.append(table);

        if (completed_projects.length === 0) container.html('<div class="p-4 text-center text-muted">No completed projects found.</div>');
        else {
            container.append(wrapper);
            column_selectors['completed-projects'].apply(container);
            apply_column_resizing(container);
        }
    }

    function render_current_tab() {
        if (current_tab === "portfolio-gantt") {
            $root.find('#gantt-controls').show();
            $root.find('#standard-controls').hide();
            fetch_and_render_portfolio_gantt();
        } else {
            $root.find('#gantt-controls').hide();
            $root.find('#standard-controls').show();
            
            if (current_tab === "priority-overview") render_priority_overview();
            else if (current_tab === "active-internal-projects") render_active_internal();
            else if (current_tab === "completed-projects") render_completed_projects();
            
            setTimeout(apply_search_filter, 50);
        }
    }

    function apply_search_filter() {
        let search_term = $root.find('#global-project-search').val().toLowerCase();
        let rows = $root.find('#dashboard-content table tbody tr');

        rows.each(function() {
            let row = $(this);
            let row_text = row.text().toLowerCase();
            
            if (row_text.indexOf(search_term) !== -1) row.show();
            else row.hide();
        });
    }

    $root.find('#global-project-search').on('input', function() {
        apply_search_filter();
    });

    $root.find('.nav-link').click(function(e) {
        e.preventDefault();
        $root.find('.nav-link').removeClass('active');
        $(this).addClass('active');
        current_tab = $(this).data('route');
        
        if (current_tab === "portfolio-gantt") {
            $root.find('#gantt-controls').show();
            $root.find('#standard-controls').hide();
        } else {
            $root.find('#gantt-controls').hide();
            $root.find('#standard-controls').show();
        }
        
        render_current_tab();
    });

    // Init
    fetch_initial_data();
    }
})();
