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
 *   - Priority Overview   — client-facing value streams (Build/Design/Events/Service),
 *                           with inline-editable company/project priority cells;
 *   - Active Internal Projects — active projects whose project_type is internal
 *                           (INTERNAL_PROJECT_TYPES), grouped by master project;
 *   - Completed Projects  — read-only list of inactive projects;
 *   - Portfolio Gantt     — the embeddable Gantt widget
 *                           (erpnext_enhancements.gantt.mount) in composite mode:
 *                           Master Project groups -> Project rows -> Task trees
 *                           via the permission-checked get_gantt_data endpoint,
 *                           with project/status filters, a Show Tasks toggle,
 *                           view-mode zoom, tooltip popups and a Today button.
 *                           Read-only for now (drag-editing returns with the
 *                           widget's per-embed edit opt-in milestone);
 *   - Dashboard           — native module overview computed client-side from the
 *                           fetched project_data: headline number cards + CSS-bar
 *                           breakdowns (status / type / completion).
 * The toolbar also carries "New Project" / "New Master Project" quick-create buttons.
 * Table edits persist back via the same whitelisted methods.
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

    // Client-facing value streams shown on Priority Overview and scoping the
    // Portfolio Gantt (gantt_root_filters). Kept in one place so this Custom
    // HTML Block stays in sync with the page dashboard (priority_overview.js),
    // which filters to exactly these project types.
    const PRIORITY_PROJECT_TYPES = ["Build", "Design", "Events", "Service", "Delivery"];

    // Internal (non client-facing) project types shown on the Active Internal
    // Projects tab. Everything else — the client-facing streams above and untyped
    // projects — is excluded. Mirrors INTERNAL_PROJECT_TYPES in the page dashboard
    // (project_dashboard.py / active_internal_projects.js); keep the lists in sync.
    const INTERNAL_PROJECT_TYPES = ["Internal", "Organizational Projects", "Group Projects", "Other"];

    // Business-preferred ordering for the value-stream groups on Priority
    // Overview (when sorted by Project Priority). Streams not listed (e.g.
    // Delivery, Uncategorized) fall after these, alphabetically. Kept in sync
    // with priority_overview.js's VALUE_STREAM_ORDER.
    const VALUE_STREAM_ORDER = ["Design", "Build", "Service", "Events"];
    const compare_value_streams = (a, b) => {
        let ia = VALUE_STREAM_ORDER.indexOf(a);
        let ib = VALUE_STREAM_ORDER.indexOf(b);
        if (ia === -1) ia = VALUE_STREAM_ORDER.length;
        if (ib === -1) ib = VALUE_STREAM_ORDER.length;
        if (ia !== ib) return ia - ib;
        return String(a).localeCompare(String(b));
    };

    // Gantt State tracking. The Portfolio Gantt renders through the embeddable
    // Gantt widget (erpnext_enhancements.gantt.mount — public/js/gantt_widget/)
    // in composite mode: Master Project groups -> Project rows -> Task trees,
    // all fetched by the permission-checked get_gantt_data endpoint. This block
    // only owns the toolbar state below and translates it into widget config;
    // scroll preservation, today handling and expand/collapse are the widget's
    // (and DHTMLX's) own. Read-only: drag-editing returns with the widget's
    // per-embed edit opt-in milestone.
    const DEFAULT_GANTT_STATUSES = ["Active", "Working", "Client Hold"];
    const all_gantt_statuses = ["Active", "Working", "Client Hold", "Parked", "Completed", "Invoiced", "Paid", "Canceled"];
    let gantt_status_filters = DEFAULT_GANTT_STATUSES.slice();

    let portfolio_gantt_widget = null;
    let gantt_selected_projects = new Set();
    let gantt_type_filters = new Set();      // empty = every PRIORITY_PROJECT_TYPE
    let gantt_customer_filters = new Set();  // empty = all customers
    let gantt_date_window = "";              // "" | "30" | "90" | "180" | "365"
    let gantt_at_risk = false;
    // projects the user has expanded, and those whose tasks are already loaded
    // (a reload clears the latter but keeps the former, so the tree is restored)
    let gantt_expanded_projects = new Set();
    const gantt_loaded_projects = new Set();

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

    // ----- GANTT TOOLBAR CONTROLS -----

    const checkbox_row = (cls, value, checked, id_prefix) => {
        const safe_id = id_prefix + sanitizeId(value);
        const label = frappe.utils.escape_html(value);
        return `
            <div class="custom-control custom-checkbox mb-1" data-name="${label.toLowerCase()}">
                <input type="checkbox" class="custom-control-input ${cls}" value="${label}" id="${safe_id}" ${checked ? 'checked' : ''}>
                <label class="custom-control-label" for="${safe_id}" style="cursor: pointer; padding-top: 2px;">${label}</label>
            </div>`;
    };

    // Paints every checkbox list + button label from the current filter state.
    // Called on first render and after "Reset view".
    function init_gantt_filter_controls() {
        const $status = $root.find('#gantt-status-checkboxes').empty();
        all_gantt_statuses.forEach((s) => {
            $status.append(checkbox_row('gantt-status-cb', s, gantt_status_filters.includes(s), 'filter-gantt-'));
        });
        $root.find('#ganttStatusDropdown').text(
            gantt_status_filters.length === all_gantt_statuses.length || !gantt_status_filters.length
                ? 'All Statuses' : `Selected (${gantt_status_filters.length})`
        );

        const $types = $root.find('#gantt-type-checkboxes').empty();
        PRIORITY_PROJECT_TYPES.forEach((t) => {
            $types.append(checkbox_row('gantt-type-cb', t, !gantt_type_filters.size || gantt_type_filters.has(t), 'filter-gtype-'));
        });
        $root.find('#ganttTypeDropdown').text(
            gantt_type_filters.size ? `Types (${gantt_type_filters.size})` : 'All Types'
        );

        $root.find('#ganttCustomerDropdown').text(
            gantt_customer_filters.size ? `Customers (${gantt_customer_filters.size})` : 'All Customers'
        );
        $root.find('#ganttProjectDropdown').text(
            gantt_selected_projects.size ? `Projects (${gantt_selected_projects.size})` : 'All Projects'
        );
        $root.find('#gantt-date-window').val(gantt_date_window || "");
        $root.find('#gantt-at-risk').prop('checked', gantt_at_risk);
    }

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

        // restore the saved view BEFORE painting the controls
        load_gantt_view();
        init_gantt_filter_controls();

        $root.find('#apply-gantt-status-filters').on('click', function() {
            gantt_status_filters = $root.find('.gantt-status-cb:checked').map(function() { return $(this).val(); }).get();
            $root.find('#ganttStatusDropdown').text(`Selected (${gantt_status_filters.length})`);
            $root.find('#gantt-status-menu').removeClass('show');
            save_gantt_view();
            fetch_and_render_portfolio_gantt();
        });

        $root.find('#apply-gantt-type-filters').on('click', function() {
            const checked = $root.find('.gantt-type-cb:checked').map(function() { return $(this).val(); }).get();
            // all ticked == no narrowing, so the default scope still applies
            gantt_type_filters = checked.length === PRIORITY_PROJECT_TYPES.length ? new Set() : new Set(checked);
            $root.find('#ganttTypeDropdown').text(gantt_type_filters.size ? `Types (${gantt_type_filters.size})` : 'All Types');
            $root.find('#gantt-type-menu').removeClass('show');
            save_gantt_view();
            fetch_and_render_portfolio_gantt();
        });

        $root.find('#apply-gantt-customer-filters').on('click', function() {
            const total = $root.find('.gantt-customer-cb').length;
            const checked = $root.find('.gantt-customer-cb:checked');
            gantt_customer_filters = checked.length < total
                ? new Set(checked.map(function() { return $(this).val(); }).get())
                : new Set();
            $root.find('#ganttCustomerDropdown').text(
                gantt_customer_filters.size ? `Customers (${gantt_customer_filters.size})` : 'All Customers'
            );
            $root.find('#gantt-customer-menu').removeClass('show');
            save_gantt_view();
            fetch_and_render_portfolio_gantt();
        });

        $root.find('#gantt-date-window').on('change', function() {
            gantt_date_window = $(this).val();
            save_gantt_view();
            fetch_and_render_portfolio_gantt();
        });

        $root.find('#gantt-at-risk').on('change', function() {
            gantt_at_risk = $(this).is(':checked');
            save_gantt_view();
            fetch_and_render_portfolio_gantt();
        });

        $root.find('.view-mode-group button').on('click', function() {
            $root.find('.view-mode-group button').removeClass('active btn-secondary').addClass('btn-outline-secondary');
            $(this).addClass('active btn-secondary').removeClass('btn-outline-secondary');
            if (portfolio_gantt_widget) {
                portfolio_gantt_widget.set_zoom(GANTT_VIEW_MODE_MAP[$(this).data('view')] || "month");
            }
            save_gantt_view();
        });

        $root.find('#gantt-today-btn').on('click', function() {
            if (portfolio_gantt_widget) portfolio_gantt_widget.scroll_to_today();
        });

        $root.find('#gantt-export-btn').on('click', export_gantt_png);
        $root.find('#gantt-reset-view').on('click', reset_gantt_view);

        $root.find('#gantt-select-all-projects').on('change', function() {
            $root.find('.gantt-proj-cb').prop('checked', $(this).is(':checked'));
        });

        $root.find('#gantt-project-search, #gantt-customer-search, #global-project-search').on('keydown keyup keypress input', function(e) {
            e.stopPropagation();
            if (e.key === 'Enter') e.preventDefault();
        });

        $root.find('#gantt-project-search').on('input', function() {
            const val = $(this).val().toLowerCase();
            $root.find('.gantt-proj-filter-item').each(function() {
                $(this).toggle($(this).data('name').indexOf(val) !== -1);
            });
        });

        $root.find('#gantt-customer-search').on('input', function() {
            const val = $(this).val().toLowerCase();
            $root.find('#gantt-customer-checkboxes .custom-checkbox').each(function() {
                $(this).toggle(String($(this).data('name')).indexOf(val) !== -1);
            });
        });

        $root.find('#apply-gantt-project-filters').on('click', function() {
            gantt_selected_projects.clear();
            const total = $root.find('.gantt-proj-cb').length;
            const checked = $root.find('.gantt-proj-cb:checked');
            if (checked.length < total) {
                checked.each(function() { gantt_selected_projects.add($(this).val()); });
                $root.find('#ganttProjectDropdown').text(`Projects (${checked.length})`);
            } else {
                $root.find('#ganttProjectDropdown').text('All Projects');
            }
            $root.find('#gantt-project-menu').removeClass('show');
            save_gantt_view();
            fetch_and_render_portfolio_gantt();
        });
    }

    function populate_projects_dropdown(projects) {
        const container = $root.find('#gantt-project-checkboxes');
        container.empty();

        const sorted = [...projects].sort((a, b) => (a.project_name || a.name).localeCompare(b.project_name || b.name));
        sorted.forEach((p) => {
            const isChecked = gantt_selected_projects.size === 0 || gantt_selected_projects.has(p.name);
            const label = frappe.utils.escape_html(p.project_name || p.name);
            const safe_id = sanitizeId(p.name);
            container.append(`
                <div class="custom-control custom-checkbox mb-1 gantt-proj-filter-item" data-name="${label.toLowerCase()}">
                    <input type="checkbox" class="custom-control-input gantt-proj-cb" value="${frappe.utils.escape_html(p.name)}" id="filter-proj-${safe_id}" ${isChecked ? 'checked' : ''}>
                    <label class="custom-control-label" for="filter-proj-${safe_id}" style="cursor: pointer; padding-top: 2px;">${label}</label>
                </div>
            `);
        });

        const checked = $root.find('.gantt-proj-cb:checked').length;
        $root.find('#gantt-select-all-projects').prop('checked', sorted.length > 0 && checked === sorted.length);
    }

    function populate_customers_dropdown(customers) {
        const container = $root.find('#gantt-customer-checkboxes');
        container.empty();
        const unique = [...new Set((customers || []).filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b)));
        unique.forEach((c) => {
            const checked = gantt_customer_filters.size === 0 || gantt_customer_filters.has(c);
            container.append(checkbox_row('gantt-customer-cb', c, checked, 'filter-cust-'));
        });
    }


    // ----- PORTFOLIO GANTT -----
    //
    // Rendered by the embeddable Gantt widget (erpnext_enhancements.gantt.mount)
    // in composite mode: Project Type groups -> Project rows -> Task subtrees
    // fetched lazily when a project's caret is opened. All data comes from the
    // permission-checked get_gantt_data endpoint. Read-only for now.

    const GANTT_VIEW_MODE_MAP = {
        "Quarter Day": "quarter_day",
        "Half Day": "half_day",
        "Day": "day",
        "Week": "week",
        "Month": "month",
    };

    // Colour key. Keep in sync with the pg-type-* rules in projects_dashboard.css:
    // project bars use the solid colour, their tasks a lighter shade of it.
    const GANTT_TYPE_COLORS = [
        { type: "Build", cls: "pg-type-build", color: "#1f6fb2" },
        { type: "Design", cls: "pg-type-design", color: "#7c4dbd" },
        { type: "Service", cls: "pg-type-service", color: "#2e9e5b" },
        { type: "Events", cls: "pg-type-events", color: "#d98324" },
        { type: "Delivery", cls: "pg-type-delivery", color: "#0f9aa8" },
    ];
    const GANTT_TYPE_CLASS = {};
    GANTT_TYPE_COLORS.forEach((t) => { GANTT_TYPE_CLASS[t.type] = t.cls; });

    // Task subtree loaded per project when its caret opens. Mirrors the retired
    // get_all_projects_for_gantt task query: open work only.
    const GANTT_TASK_CHILDREN = {
        doctype: "Task",
        link_field: "project",
        fields: { text: "subject", start: "exp_start_date", end: "exp_end_date", progress: "progress", parent: "parent_task" },
        filters: { status: ["not in", ["Completed", "Canceled"]] },
        extra_fields: ["status"],
        dependencies: "depends_on",
        order_by: "exp_start_date asc",
        lazy: true,
    };

    // ---- saved view (filters / zoom / expanded projects), per user ----------

    const GANTT_VIEW_KEY = "chb_portfolio_gantt_view_v1";

    function save_gantt_view() {
        try {
            localStorage.setItem(GANTT_VIEW_KEY, JSON.stringify({
                statuses: gantt_status_filters,
                types: [...gantt_type_filters],
                customers: [...gantt_customer_filters],
                projects: [...gantt_selected_projects],
                window_days: gantt_date_window,
                at_risk: gantt_at_risk,
                zoom: $root.find('.view-mode-group button.active').data('view') || "Month",
                expanded: [...gantt_expanded_projects],
            }));
        } catch (e) {
            // storage disabled/full — the view just won't persist
        }
    }

    function load_gantt_view() {
        let saved = null;
        try {
            saved = JSON.parse(localStorage.getItem(GANTT_VIEW_KEY) || "null");
        } catch (e) {
            saved = null;
        }
        if (!saved) return;
        if (Array.isArray(saved.statuses)) gantt_status_filters = saved.statuses;
        if (Array.isArray(saved.types)) gantt_type_filters = new Set(saved.types);
        if (Array.isArray(saved.customers)) gantt_customer_filters = new Set(saved.customers);
        if (Array.isArray(saved.projects)) gantt_selected_projects = new Set(saved.projects);
        if (Array.isArray(saved.expanded)) gantt_expanded_projects = new Set(saved.expanded);
        gantt_date_window = saved.window_days || "";
        gantt_at_risk = !!saved.at_risk;
        if (saved.zoom) {
            $root.find('.view-mode-group button').removeClass('active btn-secondary').addClass('btn-outline-secondary');
            $root.find(`.view-mode-group button[data-view="${saved.zoom}"]`).addClass('active btn-secondary').removeClass('btn-outline-secondary');
        }
    }

    function reset_gantt_view() {
        try { localStorage.removeItem(GANTT_VIEW_KEY); } catch (e) { /* ignore */ }
        gantt_status_filters = DEFAULT_GANTT_STATUSES.slice();
        gantt_type_filters = new Set();
        gantt_customer_filters = new Set();
        gantt_selected_projects = new Set();
        gantt_expanded_projects = new Set();
        gantt_date_window = "";
        gantt_at_risk = false;
        init_gantt_filter_controls();
        fetch_and_render_portfolio_gantt();
    }

    // ---- config ------------------------------------------------------------

    function gantt_root_filters() {
        // Mirrors the retired get_all_projects_for_gantt scoping: active,
        // client-facing project types, never Canceled. NOTE: unlike that
        // endpoint (which read with get_all), the widget's get_gantt_data
        // enforces the caller's Project/Task read permissions.
        const filters = [
            ["is_active", "=", "Yes"],
            ["project_type", "in", gantt_type_filters.size ? [...gantt_type_filters] : PRIORITY_PROJECT_TYPES],
        ];
        if (gantt_status_filters.length) {
            filters.push(["status", "in", gantt_status_filters]);
        } else {
            filters.push(["status", "!=", "Canceled"]);
        }
        if (gantt_selected_projects.size > 0) {
            filters.push(["name", "in", [...gantt_selected_projects]]);
        }
        if (gantt_customer_filters.size > 0) {
            filters.push(["customer", "in", [...gantt_customer_filters]]);
        }
        if (gantt_date_window) {
            // overlap test: starts before the window ends AND ends after it
            // begins. Undated projects drop out, which is the point of the filter.
            const today = frappe.datetime.get_today();
            const until = frappe.datetime.add_days(today, parseInt(gantt_date_window, 10));
            filters.push(["expected_start_date", "<=", until]);
            filters.push(["expected_end_date", ">=", today]);
        }
        if (gantt_at_risk) {
            filters.push(["expected_end_date", "<", frappe.datetime.get_today()]);
            filters.push(["percent_complete", "<", 100]);
        }
        return filters;
    }

    function gantt_widget_config() {
        const mode = $root.find('.view-mode-group button.active').data('view') || "Month";
        return {
            doctype: "Project",
            fields: { text: "project_name", start: "expected_start_date", end: "expected_end_date", progress: "percent_complete" },
            filters: gantt_root_filters(),
            // Master Project where one is set, otherwise the project type
            group_by: ["custom_master_project", "project_type"],
            extra_fields: ["project_type", "status", "customer"],
            children: GANTT_TASK_CHILDREN,
            lazy_children: true,
            order_by: "expected_start_date asc",
            limit: 1000,
            today: true, // today column + open-at-today default (block owns the button)
            tooltip: true,
            zoom: GANTT_VIEW_MODE_MAP[mode] || "month",
            columns: gantt_columns(),
            gantt: { grid_width: 420 },
            templates: {
                task_class: portfolio_row_class,
                grid_row_class: portfolio_row_class,
            },
            on_task_expand: (id, task) => load_project_tasks(id, task),
            on_task_collapse: (id) => {
                gantt_expanded_projects.delete(id);
                save_gantt_view();
            },
            on_task_click: (id, task) => {
                if (task && task.ref_doctype && task.ref_name) {
                    frappe.set_route("Form", task.ref_doctype, task.ref_name);
                }
            },
        };
    }

    // ---- grid columns ------------------------------------------------------

    const fmt_date = (value) => (value ? frappe.datetime.str_to_user(String(value).split(" ")[0]) : "");

    // The API's end_date is EXCLUSIVE (a date-only end is pushed to midnight of
    // the next day), so show the inclusive day the user actually entered.
    function inclusive_end(task) {
        if (!task.end_date) return "";
        const d = new Date(task.end_date.getTime ? task.end_date.getTime() : task.end_date);
        if (isNaN(d)) return "";
        if (d.getHours() === 0 && d.getMinutes() === 0) d.setDate(d.getDate() - 1);
        return frappe.datetime.str_to_user(moment(d).format("YYYY-MM-DD"));
    }

    function gantt_columns() {
        return [
            { name: "text", label: "Project / Task", tree: true, width: 200, resize: true },
            { name: "project_type", label: "Type", width: 70, align: "center", template: (t) => t.project_type || "" },
            { name: "start_date", label: "Start", width: 78, align: "center", template: (t) => (t.start_date ? fmt_date(moment(t.start_date).format("YYYY-MM-DD")) : "") },
            { name: "end_date", label: "End", width: 78, align: "center", template: inclusive_end },
            { name: "progress", label: "%", width: 44, align: "center", template: (t) => (typeof t.progress === "number" ? Math.round(t.progress * 100) + "%" : "") },
        ];
    }

    // ---- row styling -------------------------------------------------------

    // Composite ids are prefixed G:: (group) / P:: (project) / C:: (task) by
    // get_gantt_data. Projects take their project_type colour; their tasks take
    // a lighter shade of the same colour (stamped in load_project_tasks).
    // Styles live in projects_dashboard.css.
    function portfolio_row_class(start, end, task) {
        const id = String(task.id);
        const classes = [];
        if (id.indexOf("G::") === 0) {
            classes.push("pg-master");
        } else if (id.indexOf("P::") === 0) {
            classes.push("pg-project", GANTT_TYPE_CLASS[task.project_type] || "pg-type-other");
        } else {
            classes.push("pg-task", GANTT_TYPE_CLASS[task.ee_project_type] || "pg-type-other");
        }
        if (is_overdue(task)) classes.push("pg-overdue");
        return classes.join(" ");
    }

    function is_overdue(task) {
        if (!task.end_date || String(task.id).indexOf("G::") === 0) return false;
        if (task.status === "Completed" || task.progress >= 1) return false;
        return task.end_date < new Date();
    }

    function render_gantt_legend() {
        const $legend = $root.find('#gantt-legend');
        if (!$legend.length) return;
        let html = '<span class="pg-legend-label">Project type:</span>';
        GANTT_TYPE_COLORS.forEach((t) => {
            html += `<span class="pg-legend-item"><i class="pg-swatch" style="background:${t.color}"></i>${frappe.utils.escape_html(t.type)}</span>`;
        });
        html += '<span class="pg-legend-item"><i class="pg-swatch pg-swatch-task"></i>Tasks (lighter shade of their project)</span>';
        html += '<span class="pg-legend-item"><i class="pg-swatch pg-swatch-overdue"></i>Past its end date</span>';
        $legend.html(html);
    }

    // ---- lazy task loading -------------------------------------------------

    // Fetch ONE project's tasks when its caret is opened. Reuses the same
    // composite config narrowed to that project, then keeps only the C:: rows —
    // the group/project rows are already on the chart.
    async function load_project_tasks(row_id, task) {
        gantt_expanded_projects.add(row_id);
        save_gantt_view();
        if (!task || !task.ref_name || gantt_loaded_projects.has(row_id)) return;
        gantt_loaded_projects.add(row_id);

        const cfg = gantt_widget_config();
        cfg.filters = [["name", "=", task.ref_name]];
        cfg.children = Object.assign({}, GANTT_TASK_CHILDREN, { lazy: false });
        try {
            const r = await frappe.call({
                method: "erpnext_enhancements.api.gantt.get_gantt_data",
                args: { config: {
                    doctype: cfg.doctype,
                    fields: cfg.fields,
                    filters: cfg.filters,
                    group_by: cfg.group_by,
                    extra_fields: cfg.extra_fields,
                    children: cfg.children,
                    order_by: cfg.order_by,
                    limit: 1,
                } },
            });
            const data = (r && r.message) || { tasks: [], links: [] };
            const child_rows = data.tasks.filter((t) => String(t.id).indexOf("C::") === 0);
            // stamp the parent project's type so tasks can be shaded to match
            child_rows.forEach((t) => { t.ee_project_type = task.project_type; });
            if (portfolio_gantt_widget && !portfolio_gantt_widget.destroyed) {
                const added = portfolio_gantt_widget.add_rows(child_rows, data.links);
                if (!added && !child_rows.length) {
                    frappe.show_alert({ message: __("No scheduled tasks for {0}", [task.text]), indicator: "blue" });
                }
            }
        } catch (err) {
            gantt_loaded_projects.delete(row_id); // let the user try again
            console.error("Portfolio Gantt: loading tasks failed", err);
        }
    }

    // ---- render ------------------------------------------------------------

    async function fetch_and_render_portfolio_gantt() {
        const container = $root.find('#dashboard-content');
        if (!window.erpnext_enhancements || !erpnext_enhancements.gantt) {
            container.html('<div class="alert alert-danger">The Gantt widget is not loaded. Please refresh the page.</div>');
            return;
        }
        let host = container.find('.pg-widget-host')[0];
        if (!host) {
            // Another tab replaced #dashboard-content. Destroy the orphaned
            // widget BEFORE dropping the reference: the mount registry keys on
            // the (now detached) host element, so a new mount on a new host
            // would never clean it up — each leaked DHTMLX instance keeps live
            // document listeners and a polling interval forever.
            if (portfolio_gantt_widget) {
                portfolio_gantt_widget.destroy();
            }
            portfolio_gantt_widget = null;
            container.empty();
            host = $('<div class="pg-widget-host"></div>').appendTo(container)[0];
        }
        // a reload discards loaded subtrees; they are re-fetched on re-expand
        gantt_loaded_projects.clear();
        try {
            if (portfolio_gantt_widget && !portfolio_gantt_widget.destroyed) {
                // toolbar state changed: rebuild the config in place and refetch
                Object.assign(portfolio_gantt_widget.config, gantt_widget_config());
                await portfolio_gantt_widget.refresh();
            } else {
                portfolio_gantt_widget = erpnext_enhancements.gantt.mount(host, gantt_widget_config());
                await portfolio_gantt_widget.ready;
            }
            render_gantt_legend();
            refresh_gantt_filter_options();
            restore_expanded_projects();
        } catch (err) {
            // the widget shows its own error overlay
            console.error("Portfolio Gantt:", err);
        }
    }

    // Re-open the projects the user had expanded before the reload.
    function restore_expanded_projects() {
        if (!portfolio_gantt_widget || !portfolio_gantt_widget.gantt) return;
        const g = portfolio_gantt_widget.gantt;
        [...gantt_expanded_projects].forEach((id) => {
            if (g.isTaskExists(id)) {
                g.open(id); // fires onTaskOpened -> load_project_tasks
            }
        });
    }

    // The pickers list every project/customer the UNFILTERED portfolio query
    // returns; a narrowed fetch must not shrink the option lists, or an
    // unchecked entry could never be re-checked.
    function refresh_gantt_filter_options() {
        const narrowed = gantt_selected_projects.size > 0 || gantt_customer_filters.size > 0;
        if (!narrowed) {
            const data = portfolio_gantt_widget && portfolio_gantt_widget.data;
            if (!data) return;
            const roots = data.tasks.filter((t) => String(t.id).indexOf("P::") === 0);
            populate_projects_dropdown(roots.map((t) => ({ name: t.ref_name, project_name: t.text })));
            populate_customers_dropdown(roots.map((t) => t.customer));
            return;
        }
        // a light roots-only query, without the project/customer narrowing, so
        // the option lists keep tracking the other filters
        const cfg = gantt_widget_config();
        const filters = cfg.filters.filter((f) => f[0] !== "name" && f[0] !== "customer");
        frappe.call({
            method: "erpnext_enhancements.api.gantt.get_gantt_data",
            args: { config: {
                doctype: cfg.doctype,
                fields: cfg.fields,
                filters: filters,
                extra_fields: cfg.extra_fields,
                order_by: cfg.order_by,
                limit: cfg.limit,
            } },
        }).then((r) => {
            const tasks = (r.message && r.message.tasks) || [];
            // single-source response: ids ARE the project names
            populate_projects_dropdown(tasks.map((t) => ({ name: t.id, project_name: t.text })));
            populate_customers_dropdown(tasks.map((t) => t.customer));
        }).catch(() => {});
    }

    // ---- PNG export --------------------------------------------------------

    // Rendered client-side with dom-to-image. Deliberately NOT DHTMLX's
    // exportToPNG(), which POSTs the chart data to export.dhtmlx.com — project
    // schedules must not leave the browser.
    function export_gantt_png() {
        const node = portfolio_gantt_widget && portfolio_gantt_widget.chart_el;
        if (!node) return;
        const $btn = $root.find('#gantt-export-btn');
        const original = $btn.html();
        $btn.prop('disabled', true).html('<i class="fa fa-spinner fa-spin mr-1"></i>Exporting...');
        const done = () => $btn.prop('disabled', false).html(original);
        frappe.require('https://cdnjs.cloudflare.com/ajax/libs/dom-to-image/2.6.0/dom-to-image.min.js', () => {
            if (typeof domtoimage === "undefined") {
                frappe.show_alert({ message: __("Could not load the image exporter"), indicator: "red" });
                done();
                return;
            }
            domtoimage.toPng(node, { bgcolor: "#ffffff", width: node.scrollWidth, height: node.scrollHeight })
                .then((url) => {
                    const link = document.createElement('a');
                    link.download = 'Portfolio-Gantt-' + moment().format('YYYYMMDD') + '.png';
                    link.href = url;
                    link.click();
                    done();
                })
                .catch((err) => {
                    console.error("Gantt export failed", err);
                    frappe.show_alert({ message: __("Gantt export failed"), indicator: "red" });
                    done();
                });
        });
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
        // Design, Events, Service) that are still active and in progress. The
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
        let internal_projects = project_data.filter(
            p => p.is_active === "Yes" && INTERNAL_PROJECT_TYPES.includes(p.project_type)
        );

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

    // Dashboard tab — a native Projects-module overview computed client-side from
    // the already-fetched project_data (no extra server call). Number cards + CSS
    // bar breakdowns; deliberately no frappe.Chart, whose JS-injected styles don't
    // cross into this block's shadow root.
    function build_breakdown(title, obj, full) {
        const labels = Object.keys(obj);
        const max = Math.max(1, ...labels.map(l => obj[l]));
        let rows = "";
        labels.forEach(l => {
            const v = obj[l];
            const pct = Math.round((v / max) * 100);
            rows += `
                <div class="mb-2">
                    <div class="d-flex justify-content-between" style="font-size: 0.85rem;">
                        <span>${frappe.utils.escape_html(l)}</span>
                        <span class="text-muted">${v}</span>
                    </div>
                    <div style="height: 8px; background: var(--control-bg, #f0f4f7); border-radius: 4px; overflow: hidden;">
                        <div style="height: 100%; width: ${pct}%; background: var(--blue-500, #2490ef);"></div>
                    </div>
                </div>`;
        });
        if (!labels.length) rows = '<div class="text-muted text-center py-3">No data</div>';
        return `
            <div class="${full ? 'col-12' : 'col-12 col-lg-6'} mb-4 px-2">
                <div style="background: var(--card-bg, #fff); border: 1px solid var(--border-color, #e2e6e9); border-radius: 8px; padding: 16px; height: 100%;">
                    <h6 class="text-muted mb-3">${frappe.utils.escape_html(title)}</h6>
                    ${rows}
                </div>
            </div>`;
    }

    function render_dashboard() {
        let container = $root.find('#dashboard-content');
        container.empty();

        const today = frappe.datetime.get_today();
        const active = project_data.filter(p => p.is_active === "Yes");
        const total_active = active.length;

        let overdue = 0, pct_sum = 0, open_tasks = 0;
        const by_status = {}, by_type = {};
        const buckets = { "0–25%": 0, "25–50%": 0, "50–75%": 0, "75–99%": 0, "100%": 0 };

        active.forEach(p => {
            const pc = parseFloat(p.percent_complete) || 0;
            pct_sum += pc;
            if (p.expected_end_date && p.expected_end_date < today && pc < 100) overdue++;
            if (pc >= 100) buckets["100%"]++;
            else if (pc >= 75) buckets["75–99%"]++;
            else if (pc >= 50) buckets["50–75%"]++;
            else if (pc >= 25) buckets["25–50%"]++;
            else buckets["0–25%"]++;
            const s = p.status || "Unknown"; by_status[s] = (by_status[s] || 0) + 1;
            const t = p.project_type || "Unassigned"; by_type[t] = (by_type[t] || 0) + 1;
            open_tasks += Math.max(0, (parseInt(p.total_tasks) || 0) - (parseInt(p.completed_tasks) || 0));
        });

        const avg_complete = total_active ? Math.round((pct_sum / total_active) * 10) / 10 : 0;
        const completed = project_data.filter(p => p.is_active === "No").length;
        const masters = new Set(project_data.map(p => p.custom_master_project).filter(Boolean));

        const cards = [
            { label: "Active Projects", value: total_active, color: "var(--blue-500, #2490ef)" },
            { label: "Overdue", value: overdue, color: "var(--red-500, #e24c4c)" },
            { label: "Avg % Complete", value: avg_complete + "%", color: "var(--green-500, #28a745)" },
            { label: "Open Tasks", value: open_tasks, color: "var(--orange-500, #f5a623)" },
            { label: "Master Projects", value: masters.size, color: "var(--purple-500, #7574d6)" },
            { label: "Completed", value: completed, color: "var(--gray-600, #6c757d)" }
        ];

        let cardRow = $('<div class="row m-0 mb-2"></div>');
        cards.forEach(c => {
            cardRow.append(`
                <div class="col-6 col-md-4 col-lg-2 mb-3 px-2">
                    <div style="background: var(--card-bg, #fff); border: 1px solid var(--border-color, #e2e6e9); border-radius: 8px; padding: 16px; height: 100%;">
                        <div style="font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-muted, #6c757d);">${frappe.utils.escape_html(c.label)}</div>
                        <div style="font-size: 1.7rem; font-weight: 700; line-height: 1.3; color: ${c.color};">${c.value}</div>
                    </div>
                </div>
            `);
        });
        container.append(cardRow);

        let chartRow = $('<div class="row m-0"></div>');
        chartRow.append(build_breakdown("Active Projects by Status", by_status, false));
        chartRow.append(build_breakdown("Active Projects by Type", by_type, false));
        chartRow.append(build_breakdown("Active Projects by Completion", buckets, true));
        container.append(chartRow);

        if (total_active === 0) {
            container.append('<div class="p-4 text-center text-muted">No active projects.</div>');
        }
    }

    function render_current_tab() {
        if (current_tab === "portfolio-gantt") {
            $root.find('#gantt-controls').show();
            $root.find('#standard-controls').hide();
            fetch_and_render_portfolio_gantt();
        } else {
            // Leaving the Gantt tab: the renderers below empty
            // #dashboard-content, so tear the widget down properly first
            // (destructor removes DHTMLX's document listeners + interval).
            if (portfolio_gantt_widget) {
                portfolio_gantt_widget.destroy();
                portfolio_gantt_widget = null;
            }
            $root.find('#gantt-controls').hide();
            $root.find('#standard-controls').show();
            
            if (current_tab === "priority-overview") render_priority_overview();
            else if (current_tab === "active-internal-projects") render_active_internal();
            else if (current_tab === "completed-projects") render_completed_projects();
            else if (current_tab === "dashboard") render_dashboard();

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

    // Header actions: quick-create a Project or Master Project.
    $root.find('#btn-new-project').on('click', () => frappe.new_doc('Project'));
    $root.find('#btn-new-master-project').on('click', () => frappe.new_doc('Master Project'));

    // Init
    fetch_initial_data();
    }
})();
