/*
 * Projects Dashboard — behaviour for the "Projects Dashboard" Custom HTML Block.
 *
 * Exported source of a Frappe Custom HTML Block; this repo copy is the source of truth
 * (setup/custom_html_blocks.py upserts it on migrate). Pairs with projects_dashboard.html
 * (shell) + projects_dashboard.css.
 *
 * Runs inside the Custom HTML Block sandbox, where `root_element` is the block's
 * root node. It loads the shared ColumnSelector asset, then fetches data through
 * the project_dashboard page's whitelisted methods
 * (erpnext_enhancements.project_enhancements.page.project_dashboard.*) and renders:
 *   - the head band's four live tiles — Active, Past expected end, Due in 30 days, No
 *                           company rank — tallied here from get_project_data. Each is
 *                           a button that filters the Priority Overview (Active clears
 *                           it), and "Updated N min ago" beside the Refresh button;
 *   - Priority Overview   — every active project (client streams and internal), with
 *                           stream chips (All / Design / Build / Service / Events /
 *                           Internal, with counts), a Ranked-only box (company priority
 *                           is a number; off by default) and a Group-by-stream toggle.
 *                           Flat by company rank by default; grouped, each stream is a
 *                           header row (Build, Design, Service, Events, then Internal)
 *                           ordered by Value Stream Priority. Inline-editable company /
 *                           stream rank cells; Budget (custom_project_dollar_amount)
 *                           in place of Spend %, which was always a dash because
 *                           estimated_costing is 0 on every active project;
 *   - Internal            — active projects whose project_type is internal
 *                           (INTERNAL_PROJECT_TYPES), one table with a header row per
 *                           master project;
 *   - Completed           — read-only list of inactive projects;
 *   - Portfolio Gantt     — the embeddable Gantt widget
 *                           (erpnext_enhancements.gantt.mount) in composite mode:
 *                           Project Type (or Master Project) groups -> Project
 *                           rows -> Task subtrees, loaded lazily when a
 *                           project's caret is opened, via the
 *                           permission-checked get_gantt_data endpoint.
 *                           Toolbar: find-a-project search, project / status /
 *                           type / customer / date-window / at-risk filters,
 *                           view-mode zoom (Week default), Today, and
 *                           individually toggleable grid columns. The whole
 *                           view persists per user in localStorage.
 *                           Read-only for now (drag-editing returns with the
 *                           widget's per-embed edit opt-in milestone);
 *   - Summary             — native module overview computed client-side from the
 *                           fetched project_data: headline number cards + CSS-bar
 *                           breakdowns (status / type / completion). It is what the
 *                           "Dashboard" tab used to show; that tab is gone.
 * The five tabs are always visible (Mock B, v1.781.0; "View More" is retired). What a
 * person chooses is kept per browser in localStorage, never in frappe user_settings
 * (Redis only, flushed on every deploy): the tab (chb_projects_dashboard_tab_v1), each
 * tab's sort (chb_projects_dashboard_sort_v1), the stream chip, Ranked only and Group by
 * stream (chb_projects_dashboard_overview_v1), plus the columns and widths the shared
 * ColumnSelector / ColumnResizer already keep. The pressed tile is not kept.
 * The band carries one "+" button whose menu holds "New Project" / "New Master Project".
 * The list tabs' Columns control is an icon button (tooltip "Columns"); "Reset widths"
 * is an item in its menu. Table edits persist back via the same whitelisted methods.
 *
 * The workspace runs this script again, with a fresh root, only when it renders the
 * page: the first visit, and coming back from a different workspace. Coming back
 * from a project, a task or a list does not (v16's Workspace.show() returns early on
 * the workspace already shown), so the script hands fetch_data() to the shared
 * return helper, public/js/global_enhancements/workspace_block_return.js, which runs
 * it again then.
 *
 * Its loading, empty, error and no-access panels, its status chips and its escaping
 * come from sf_kit, and its "only the newest answer draws" counter from
 * blocks.ticket, both handed over by the same helper (custom_html_blocks/README.md).
 * Only those: the tables, the column selector and resizer, the Gantt and the saved
 * per-user view are as they were.
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
        () => withKit(root_element)
    );

    // The kit (panels, chips, escaping) and the ticket come from the shared helper,
    // workspace_block_return.js. A bundle cached from before them has neither: the block
    // then draws plain text, and every answer draws.
    function withKit(container) {
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.kit && blocks.ticket) {
            blocks.kit(container).then((sf) => startApp(container, sf, blocks.ticket));
            return;
        }
        const words = {
            loading: __("Loading…"),
            empty: __("Nothing to show"),
            error: __("Could not load"),
            "no-access": __("No access"),
        };
        const plain = (el, kind, opts) => (el.textContent = (opts && opts.title) || words[kind] || "");
        const escape = frappe.utils.escape_html;
        startApp(container, { esc: escape, chip: escape, state: { render: plain } }, () => () => true);
    }

    function startApp(container, sf, ticket) {
    const $root = $(container);

    // ----- SAVED VIEW (per browser) -----
    //
    // What a person chooses is kept in localStorage under chb_projects_dashboard_*_v1,
    // never in frappe user_settings (Redis only, flushed on every deploy). Every read and
    // write is wrapped: a browser with storage blocked still draws, it just forgets.
    const TAB_KEY = "chb_projects_dashboard_tab_v1";
    const SORT_KEY = "chb_projects_dashboard_sort_v1";
    const OVERVIEW_KEY = "chb_projects_dashboard_overview_v1";
    const TAB_ROUTES = ["priority-overview", "portfolio-gantt", "active-internal-projects", "completed-projects", "summary"];

    function read_saved(key) {
        try {
            return JSON.parse(localStorage.getItem(key) || "null");
        } catch (e) {
            return null;
        }
    }

    function write_saved(key, value) {
        try {
            localStorage.setItem(key, JSON.stringify(value));
        } catch (e) {
            // storage disabled/full — the choice just won't persist
        }
    }

    function load_tab() {
        const saved = read_saved(TAB_KEY);
        return TAB_ROUTES.includes(saved) ? saved : "priority-overview";
    }

    let current_tab = load_tab();
    let project_data = [];
    let priority_options = { project_priority: [], company_priority: [] };
    let status_options = [];

    // The Priority Overview's filters: the stream chip ("" is All), Ranked only (off by
    // default: ranked = the company priority is a number) and Group by stream (off by
    // default: the flat list by company rank). The pressed tile is session state only.
    const DEFAULT_OVERVIEW = { stream: "", ranked_only: false, group: false };
    let overview = Object.assign({}, DEFAULT_OVERVIEW);
    let tile_filter = "active";
    let last_fetch_at = null;

    // Set by the first fetch that answers with rows. Until then, and after a denial or a
    // failed read, the panel on #dashboard-content (loading; no access; the error with Try
    // again) is the page: a tile, a chip, a box or a tab draws that panel again, never
    // "No active projects" over it.
    let loaded = false;
    let panel = { kind: "loading" };

    // A remembered chip must be one the bar has (a typo, or a chip a later release removed,
    // would leave the rows narrowed with nothing pressed, not even All), as load_tab does.
    function load_overview() {
        const saved = read_saved(OVERVIEW_KEY);
        if (!saved || typeof saved !== "object") return;
        if ([""].concat(STREAM_CHIPS).includes(saved.stream)) overview.stream = saved.stream;
        overview.ranked_only = !!saved.ranked_only;
        overview.group = !!saved.group;
    }

    function save_overview() {
        write_saved(OVERVIEW_KEY, overview);
    }

    // Client-facing value streams scoping the Portfolio Gantt (gantt_root_filters) and
    // the Gantt's type filter. Kept in one place so this block stays in sync with the
    // server side (project_dashboard.py).
    const PRIORITY_PROJECT_TYPES = ["Build", "Design", "Events", "Service", "Delivery"];

    // Internal (non client-facing) project types: the Internal tab, the Internal stream
    // chip and the Internal group on Priority Overview. Mirrors INTERNAL_PROJECT_TYPES in
    // the page dashboard (project_dashboard.py); keep the lists in sync.
    const INTERNAL_PROJECT_TYPES = ["Internal", "Organizational Projects", "Group Projects", "Other"];

    // The stream a project is filed under: an internal type is "Internal", a client
    // type is itself, no type is "Uncategorized". The stream chips name the first five.
    const INTERNAL_STREAM = "Internal";
    const STREAM_CHIPS = ["Design", "Build", "Service", "Events", "Internal"];
    function stream_of(p) {
        if (INTERNAL_PROJECT_TYPES.includes(p.project_type)) return INTERNAL_STREAM;
        return p.project_type || "Uncategorized";
    }

    // Value streams to pillars (docs/app-design-system.md "Pillars and value streams";
    // print_style.STREAM_PILLAR is the record): Design violet, Build / Products / Delivery
    // navy, Service fresh-blue, Events the Rent teal; everything else, Internal included,
    // neutral. The chip tone AND the group header's edge colour come from this.
    const STREAM_PILLAR = { Design: "design", Build: "build", Products: "build", Delivery: "build", Service: "service", Events: "rent" };
    const pillar_of = (stream) => STREAM_PILLAR[stream] || "neutral";

    // Group order on Priority Overview (Nikolas, 2026-11-03): Build, Design, Service,
    // Events, then any other client stream alphabetically, and Internal last.
    const VALUE_STREAM_ORDER = ["Build", "Design", "Service", "Events"];
    const compare_value_streams = (a, b) => {
        if (a === b) return 0;
        if (a === INTERNAL_STREAM) return 1;
        if (b === INTERNAL_STREAM) return -1;
        let ia = VALUE_STREAM_ORDER.indexOf(a);
        let ib = VALUE_STREAM_ORDER.indexOf(b);
        if (ia === -1) ia = VALUE_STREAM_ORDER.length;
        if (ib === -1) ib = VALUE_STREAM_ORDER.length;
        if (ia !== ib) return ia - ib;
        return String(a).localeCompare(String(b));
    };

    // ----- DATES AND MONEY -----

    // Dates compare as ISO strings (get_project_data returns YYYY-MM-DD), so "within the
    // next 30 days" is a string range and never a time-zone question.
    const today_iso = () => frappe.datetime.get_today();
    function iso_plus_days(iso, days) {
        const d = new Date(iso + "T00:00:00Z");
        if (isNaN(d)) return iso;
        d.setUTCDate(d.getUTCDate() + days);
        return d.toISOString().slice(0, 10);
    }
    const date_part = (value) => (value ? String(value).slice(0, 10) : "");
    const pct_of = (p) => parseFloat(p.percent_complete) || 0;
    const is_past_due = (p) => Boolean(date_part(p.expected_end_date) && date_part(p.expected_end_date) < today_iso() && pct_of(p) < 100);
    const is_due_soon = (p) => {
        const end = date_part(p.expected_end_date);
        return Boolean(end && end >= today_iso() && end <= iso_plus_days(today_iso(), 30));
    };
    // "Ranked": the company priority is a number. "Unranked": it is empty or Not Assigned.
    // The named values (Maintenance, Repair Visit) are neither.
    const rank_number = (value) => parseInt(String(value === null || value === undefined ? "" : value).trim(), 10);
    const is_ranked = (p) => !isNaN(rank_number(p.custom_company_priority));
    const is_unranked = (p) => {
        const v = String(p.custom_company_priority || "").trim();
        return !v || v.toLowerCase() === "not assigned";
    };

    // Budget: whole dollars in the company currency, a dash when it is 0 or missing.
    const budget_of = (p) => parseFloat(p.custom_project_dollar_amount) || 0;
    function fmt_money(amount) {
        const currency = (frappe.boot && frappe.boot.sysdefaults && frappe.boot.sysdefaults.currency) || "USD";
        try {
            return Number(amount).toLocaleString("en-US", { style: "currency", currency, maximumFractionDigits: 0 });
        } catch (e) {
            return String(Math.round(amount));
        }
    }

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
    let gantt_search = "";                   // free-text project-name filter
    // Optional grid columns — the name column is always shown. Toggled from the
    // Columns dropdown and persisted per user with the rest of the saved view.
    const GANTT_OPTIONAL_COLUMNS = [
        { key: "project_type", label: "Type" },
        { key: "start_date", label: "Start" },
        { key: "end_date", label: "End" },
        { key: "progress", label: "%" },
    ];
    let gantt_visible_columns = new Set(GANTT_OPTIONAL_COLUMNS.map((c) => c.key));
    // projects the user has expanded, and those whose tasks are already loaded
    // (a reload clears the latter but keeps the former, so the tree is restored)
    let gantt_expanded_projects = new Set();
    const gantt_loaded_projects = new Set();

    // Each list tab's sort, remembered per browser. A saved column must be one the tab
    // has, or the default stands.
    const DEFAULT_SORT = {
        'priority-overview': { col: 'company_priority', order: 'asc' },
        'active-internal-projects': { col: 'project_name', order: 'asc' },
        'completed-projects': { col: 'completed_on', order: 'desc' }
    };
    const SORTABLE_COLUMNS = {
        'priority-overview': ['project_name', 'project_type', 'company_priority', 'project_priority', 'percent_complete', 'expected_end', 'budget'],
        'active-internal-projects': ['project_name', 'status', 'custom_project_priority', 'percent_complete', 'project_user'],
        'completed-projects': ['project_name', 'status', 'project_type', 'project_user', 'completed_on']
    };
    let sort_state = {};
    Object.keys(DEFAULT_SORT).forEach((tab) => { sort_state[tab] = Object.assign({}, DEFAULT_SORT[tab]); });
    // The Priority Overview's sort is `auto` while it is the page's own: the default, or what
    // Group by stream set. A clicked header clears it, and the toggle swaps only an auto sort.
    // Saved with the sort, so a reload keeps the distinction; a saved sort without the flag
    // counts as the person's.
    sort_state['priority-overview'].auto = true;

    function load_sort_state() {
        const saved = read_saved(SORT_KEY);
        if (!saved || typeof saved !== "object") return;
        Object.keys(DEFAULT_SORT).forEach((tab) => {
            const s = saved[tab];
            if (!s || typeof s !== "object") return;
            if (SORTABLE_COLUMNS[tab].includes(s.col)) sort_state[tab].col = s.col;
            if (s.order === 'asc' || s.order === 'desc') sort_state[tab].order = s.order;
            if (tab === 'priority-overview') sort_state[tab].auto = s.auto === true;
        });
    }

    function save_sort_state() {
        write_saved(SORT_KEY, sort_state);
    }

    load_sort_state();
    load_overview();

    const ColumnSelector = erpnext_enhancements.dashboard_components.ColumnSelector;
    const column_selectors = {
        'priority-overview': new ColumnSelector('chb_priority_overview_columns', [
            { key: 'project_name', label: 'Project Name', locked: true },
            { key: 'project_id', label: 'Project ID' },
            { key: 'project_type', label: 'Type' },
            { key: 'company_priority', label: 'Company Priority' },
            { key: 'project_priority', label: 'Value Stream Priority' },
            { key: 'percent_complete', label: 'Completion' },
            { key: 'expected_end', label: 'Expected End' },
            { key: 'budget', label: 'Budget' }
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
            { key: 'project_name', defaultWidth: 240, minWidth: 140, maxWidth: 520 },
            { key: 'project_id', defaultWidth: 110, minWidth: 90, maxWidth: 320 },
            { key: 'project_type', defaultWidth: 110, minWidth: 90, maxWidth: 240 },
            { key: 'company_priority', defaultWidth: 140, minWidth: 110, maxWidth: 320 },
            { key: 'project_priority', defaultWidth: 150, minWidth: 120, maxWidth: 340 },
            { key: 'percent_complete', defaultWidth: 170, minWidth: 120, maxWidth: 320 },
            { key: 'expected_end', defaultWidth: 170, minWidth: 120, maxWidth: 300 },
            { key: 'budget', defaultWidth: 120, minWidth: 90, maxWidth: 300 }
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
        // The Columns button is an icon (three dots, tooltip "Columns"). "Reset widths" lives in
        // its menu rather than as a button of its own: dragging a column edge is still undoable.
        const actions = [];
        if (column_resizers[current_tab]) {
            actions.push({
                label: __("Reset column widths"),
                onClick: () => {
                    column_resizers[current_tab].reset(container);
                    render_current_tab();
                },
            });
        }
        selector.render_button(toolbar, () => {
            selector.apply(container);
            fit_group_rows(container, current_tab);
        }, { icon: true, actions });
    }

    // Project ID header is non-sortable; built inline to sit beside Project Name.
    const project_id_th = '<th class="dashcol dashcol-project_id">Project ID</th>';

    // The two cells every list tab starts with: the name (bold, a link to the project)
    // and the ID (muted, the same link).
    const project_link = (p) => `/desk/project/${encodeURIComponent(p.name)}`;
    const name_cells = (p) =>
        `<td class="dashcol dashcol-project_name project-name-cell"><a href="${project_link(p)}" class="pd-pname">${sf.esc(p.project_name || "")}</a></td>` +
        `<td class="dashcol dashcol-project_id project-id-cell pd-pid"><a href="${project_link(p)}">${sf.esc(p.name)}</a></td>`;

    // Completion as a bar on the rule colour, filled in the primary, then the number.
    const completion_cell = (p, col_key) => `
        <td class="dashcol dashcol-${col_key}">
            <div class="pd-prog">
                <div class="pd-bar" aria-hidden="true"><i style="width: ${Math.max(0, Math.min(100, pct_of(p)))}%"></i></div>
                <span class="pd-pct">${Math.round(pct_of(p))}%</span>
            </div>
        </td>`;

    // The project type as a pillar chip (sf_kit tones; the kit's stand-in draws the word).
    const type_chip = (p) => (p.project_type ? sf.chip(p.project_type, pillar_of(stream_of(p))) : '<span class="pd-muted">Uncategorized</span>');

    // frappe.call's own promise, not its callback / error options: v16 calls `error` with
    // no arguments on a 403 (request.js statusCode[403]) and not at all on some failures,
    // while the promise always settles and rejects with the jqXHR, whose `status` tells a
    // 403 (no access) from any other failure.
    const api_call = (method, args = {}) => {
        return new Promise((resolve, reject) => {
            frappe.call({
                method: `erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.${method}`,
                args: args,
            }).then(resolve, reject);
        });
    };

    function sanitizeId(str) {
        return String(str || "").replace(/[^a-zA-Z0-9\-_]/g, '_');
    }

    // The element the tabs draw into: the kit's panels are written into an element, never
    // a jQuery set, so the older bundle's plain-text stand-in can write it too.
    function content_el() {
        return $root.find('#dashboard-content')[0];
    }

    // The loading, no-access or error panel, remembered so render_current_tab can draw it
    // again while nothing has loaded.
    function show_panel(kind, opts) {
        panel = { kind, opts };
        sf.state.render(content_el(), kind, opts);
    }

    // A tab with nothing to list: the kit's empty panel, under whatever the tab has
    // already drawn (its column toolbar, the Dashboard's zero cards).
    function empty_panel($container, title) {
        const el = $('<div class="dashboard-empty-state"></div>').appendTo($container)[0];
        sf.state.render(el, "empty", { title });
    }

    // project_dashboard.get_project_data's answer to a user without the page role
    // (check_permission): {"error": "You do not have permission to view the Project
    // Dashboard."}, on HTTP 200. Its other {"error"} is a failed read, worth a retry.
    function is_denial(message) {
        return Boolean(message && typeof message.error === "string" && /permission/i.test(message.error));
    }

    async function fetch_initial_data() {
        show_panel("loading");
        init_gantt_filters();
        await fetch_data();
    }

    // The fetch half on its own, so a return to the workspace can run it again
    // without re-binding the Gantt toolbar (init_gantt_filters binds its handlers,
    // and a second call would stack them). A return and the first load can overlap,
    // so each fetch takes a ticket on the root (blocks.ticket) and only the newest
    // one draws.
    async function fetch_data() {
        const current = ticket(container);
        const $refresh = $root.find('#btn-refresh').prop('disabled', true);
        try {
            const [projectsRes, priorityRes, statusRes] = await Promise.all([
                api_call('get_project_data'),
                api_call('get_priority_options'),
                api_call('get_status_options')
            ]);
            if (!current()) return;

            if (priorityRes.message && !priorityRes.message.error) {
                priority_options = priorityRes.message;
            }
            if (statusRes.message && !statusRes.message.error) {
                status_options = statusRes.message;
            }
            if (projectsRes.message && !projectsRes.message.error) {
                project_data = projectsRes.message;
                loaded = true;
                last_fetch_at = Date.now();
                render_updated();
                render_tiles();
                render_current_tab();
            } else if (is_denial(projectsRes.message)) {
                // No page role: get_project_data answers 200 with {"error": "You do not have
                // permission ..."} rather than a 403. Asking again cannot help, so no Try again.
                show_panel("no-access");
            } else {
                const title = __("Could not load the projects.");
                show_panel("error", { title, on_action: fetch_data });
            }
        } catch (err) {
            if (!current()) return;
            const title = __("Could not load the projects: the request failed.");
            if (err && err.status === 403) show_panel("no-access");
            else show_panel("error", { title, on_action: fetch_data });
        } finally {
            if (current()) $refresh.prop('disabled', false);
        }
    }

    // ----- HEAD BAND: tiles, Refresh, "Updated N min ago" -----

    // Every active project: is_active = Yes and status not Completed / Canceled (is_active
    // is unreliable on its own). The page's one definition of active: the Active tile, the
    // All chip, the Priority Overview's rows, the Internal tab and the Summary's cards all
    // read it, so no two numbers on the page disagree.
    function active_pool() {
        return project_data.filter(p =>
            p.is_active === "Yes" &&
            p.status !== "Completed" &&
            p.status !== "Canceled"
        );
    }

    const TILE_TESTS = {
        "active": () => true,
        "past-due": is_past_due,
        "due-30": is_due_soon,
        "unranked": is_unranked,
    };

    function render_tiles() {
        const pool = active_pool();
        Object.keys(TILE_TESTS).forEach((key) => {
            $root.find(`[data-tile-count="${key}"]`).text(String(pool.filter(TILE_TESTS[key]).length));
        });
        paint_tiles();
    }

    function paint_tiles() {
        Object.keys(TILE_TESTS).forEach((key) => {
            $root.find(`.pd-tile[data-tile="${key}"]`).attr('aria-pressed', key === tile_filter ? 'true' : 'false');
        });
    }

    // A tile narrows the Priority Overview; pressing it from another tab goes there.
    $root.find('.pd-tile').on('click', function() {
        const key = $(this).attr('data-tile');
        tile_filter = TILE_TESTS[key] ? key : "active";
        paint_tiles();
        if (current_tab !== "priority-overview") show_tab("priority-overview");
        else render_current_tab();
    });

    // Relative time since the last successful fetch, refreshed each minute while the
    // block is still in the document (the timer retires itself once it has left).
    function render_updated() {
        const $out = $root.find('#pd-updated');
        if (!last_fetch_at) { $out.text(""); return; }
        const minutes = Math.floor((Date.now() - last_fetch_at) / 60000);
        let text;
        if (minutes < 1) text = __("Updated just now");
        else if (minutes < 60) text = __("Updated {0} min ago", [minutes]);
        else text = __("Updated {0} h ago", [Math.floor(minutes / 60)]);
        $out.text(text);
    }

    if (typeof setInterval === "function") {
        const updated_timer = setInterval(() => {
            const host = container && container.host;
            if (host && host.isConnected === false) {
                clearInterval(updated_timer);
                return;
            }
            render_updated();
        }, 60000);
    }

    $root.find('#btn-refresh').on('click', function() {
        fetch_data();
    });

    // ----- GANTT TOOLBAR CONTROLS -----

    // A plain flex <label> rather than Bootstrap's .custom-control, whose
    // absolutely-positioned input strands the tick as soon as a long value
    // (customer names) wraps to a second line. Styles: .pg-check in the CSS.
    const checkbox_row = (cls, value, checked) => {
        const label = sf.esc(value);
        return `
            <label class="pg-check" data-name="${label.toLowerCase()}">
                <input type="checkbox" class="${cls}" value="${label}" ${checked ? 'checked' : ''}>
                <span>${label}</span>
            </label>`;
    };

    // Paints every checkbox list + button label from the current filter state.
    // Called on first render and after "Reset view".
    function init_gantt_filter_controls() {
        const $status = $root.find('#gantt-status-checkboxes').empty();
        all_gantt_statuses.forEach((s) => {
            $status.append(checkbox_row('gantt-status-cb', s, gantt_status_filters.includes(s)));
        });
        $root.find('#ganttStatusDropdown').text(
            gantt_status_filters.length === all_gantt_statuses.length || !gantt_status_filters.length
                ? 'All Statuses' : `Selected (${gantt_status_filters.length})`
        );

        const $types = $root.find('#gantt-type-checkboxes').empty();
        PRIORITY_PROJECT_TYPES.forEach((t) => {
            $types.append(checkbox_row('gantt-type-cb', t, !gantt_type_filters.size || gantt_type_filters.has(t)));
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
        $root.find('#gantt-search').val(gantt_search || "");

        const $cols = $root.find('#gantt-column-checkboxes').empty();
        GANTT_OPTIONAL_COLUMNS.forEach((c) => {
            $cols.append(`
                <label class="pg-check" data-col="${c.key}">
                    <input type="checkbox" class="gantt-col-cb" value="${c.key}" ${gantt_visible_columns.has(c.key) ? 'checked' : ''}>
                    <span>${c.label}</span>
                </label>`);
        });
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

        $root.find('#gantt-reset-view').on('click', reset_gantt_view);

        // Export / print. This block owns its own toolbar markup, so it drives
        // the widget's export methods directly rather than switching on the
        // widget's built-in toolbar (which would render a second control row).
        //
        // Images and print come from gantt_export.js, which re-draws the chart
        // as vector SVG from the rows — NOT a screenshot of the DHTMLX DOM.
        // That matters most here: this chart is the one with 50+ projects, and
        // DHTMLX only keeps the rows near the viewport in the DOM, so a capture
        // would silently export the handful that were scrolled into view.
        $root.find('.pg-export-item').on('click', async function() {
            const $btn = $(this);
            const kind = $btn.data('export');
            if (!portfolio_gantt_widget) return;
            const meta = {
                title: __('Project Portfolio'),
                subtitle: gantt_export_subtitle(),
                filename: 'Portfolio-Gantt',
            };
            // "Date range…" opens the widget's picker instead of exporting; the
            // format is chosen inside it. Nothing to disable — the dialog owns
            // the wait, and freezing the toolbar behind a modal would only
            // leave it frozen if somebody dismissed the dialog.
            if (kind === 'range') {
                portfolio_gantt_widget.export_range_dialog({
                    ...meta,
                    range: gantt_export_seed_range(),
                });
                return;
            }
            $root.find('#ganttExportDropdown').prop('disabled', true);
            try {
                const NS = erpnext_enhancements.gantt_export;
                if (kind === 'print') {
                    await NS.print(portfolio_gantt_widget, meta);
                } else if (kind === 'png' || kind === 'svg') {
                    await (kind === 'png' ? NS.export_png : NS.export_svg)(portfolio_gantt_widget, meta);
                } else {
                    await portfolio_gantt_widget.export_data(kind, meta);
                }
            } catch (e) {
                console.error('Portfolio Gantt export failed', e);
                frappe.show_alert({ message: __('Export failed.'), indicator: 'red' });
            } finally {
                $root.find('#ganttExportDropdown').prop('disabled', false);
            }
        });

        // Find-a-project: filters the chart in place (server-side, debounced)
        // so the user never leaves the dashboard.
        let search_timer = null;
        $root.find('#gantt-search').on('input', function() {
            const value = $(this).val().trim();
            clearTimeout(search_timer);
            search_timer = setTimeout(() => {
                if (value === gantt_search) return;
                gantt_search = value;
                save_gantt_view();
                fetch_and_render_portfolio_gantt();
            }, 400);
        });
        $root.find('#gantt-search-clear').on('click', function() {
            if (!gantt_search && !$root.find('#gantt-search').val()) return;
            $root.find('#gantt-search').val("");
            gantt_search = "";
            save_gantt_view();
            fetch_and_render_portfolio_gantt();
        });

        // Column toggles apply straight to the live chart — no refetch needed.
        $root.find('#gantt-column-checkboxes').on('change', '.gantt-col-cb', function() {
            gantt_visible_columns = new Set(
                $root.find('.gantt-col-cb:checked').map(function() { return $(this).val(); }).get()
            );
            save_gantt_view();
            apply_gantt_columns();
        });

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
            $root.find('#gantt-customer-checkboxes .pg-check').each(function() {
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
            const label = sf.esc(p.project_name || p.name);
            container.append(`
                <label class="pg-check gantt-proj-filter-item" data-name="${label.toLowerCase()}">
                    <input type="checkbox" class="gantt-proj-cb" value="${sf.esc(p.name)}" ${isChecked ? 'checked' : ''}>
                    <span>${label}</span>
                </label>
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
            container.append(checkbox_row('gantt-customer-cb', c, checked));
        });
    }


    // ----- PORTFOLIO GANTT -----
    //
    // Rendered by the embeddable Gantt widget (erpnext_enhancements.gantt.mount)
    // in composite mode: Project Type groups -> Project rows -> Task subtrees
    // fetched lazily when a project's caret is opened. All data comes from the
    // permission-checked get_gantt_data endpoint. Read-only for now.

    const DEFAULT_GANTT_VIEW = "Week";

    const set_view_mode_button = (mode) => {
        $root.find('.view-mode-group button').removeClass('active btn-secondary').addClass('btn-outline-secondary');
        $root.find(`.view-mode-group button[data-view="${mode}"]`).addClass('active btn-secondary').removeClass('btn-outline-secondary');
    };

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
                search: gantt_search,
                columns: [...gantt_visible_columns],
                zoom: $root.find('.view-mode-group button.active').data('view') || DEFAULT_GANTT_VIEW,
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
        gantt_search = saved.search || "";
        if (Array.isArray(saved.columns)) gantt_visible_columns = new Set(saved.columns);
        if (saved.zoom) set_view_mode_button(saved.zoom);
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
        gantt_search = "";
        gantt_visible_columns = new Set(GANTT_OPTIONAL_COLUMNS.map((c) => c.key));
        set_view_mode_button(DEFAULT_GANTT_VIEW);
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
        if (gantt_search) {
            filters.push(["project_name", "like", "%" + gantt_search + "%"]);
        }
        if (gantt_at_risk) {
            filters.push(["expected_end_date", "<", frappe.datetime.get_today()]);
            filters.push(["percent_complete", "<", 100]);
        }
        return filters;
    }

    // What the exported document says it is showing. A portfolio chart is
    // almost always filtered, and a PNG pasted into an email with no note of
    // the filters is actively misleading — it reads as "all our projects".
    function gantt_export_subtitle() {
        const parts = [];
        if (gantt_search) parts.push(__('Search: "{0}"', [gantt_search]));
        if (gantt_selected_projects.size > 0) {
            parts.push(__("{0} projects selected", [gantt_selected_projects.size]));
        }
        if (gantt_status_filters.length) parts.push(gantt_status_filters.join(", "));
        if (gantt_customer_filters.size > 0) {
            parts.push(__("{0} customers", [gantt_customer_filters.size]));
        }
        if (gantt_date_window) parts.push(__("next {0} days", [gantt_date_window]));
        if (gantt_at_risk) parts.push(__("at risk only"));
        return parts.length ? parts.join(" · ") : __("All active projects");
    }

    // What the export range dialog opens on, when the board's own date window
    // is set.
    //
    // The two are different things and stay different: the toolbar's "Next 90
    // days" decides which PROJECTS load (an overlap filter in the query, see
    // gantt_filters), while the export range decides what the exported page
    // covers — its calendar, and the child Tasks inside it, which that filter
    // never touches. But somebody who has narrowed the board to the next 90
    // days and then asks to export a range wants that same window, and having
    // to say so twice is the friction that gets a feature called fiddly.
    //
    // `to` is today + N - 1, so a 30-day window is thirty days and matches the
    // dialog's own "Next 30 days" preset exactly — the picker then opens on
    // that preset rather than on "Custom". The board filter's own comparison is
    // `<= today + N`, one day wider; what people read is the label on the
    // screen, and changing what the board shows is not this change's business.
    // 180 and 365 have no matching preset and open as Custom, which is honest.
    function gantt_export_seed_range() {
        const days = parseInt(gantt_date_window, 10);
        if (!days) return null;
        const today = frappe.datetime.get_today();
        return { from: today, to: frappe.datetime.add_days(today, days - 1) };
    }

    function gantt_widget_config() {
        const mode = $root.find('.view-mode-group button.active').data('view') || DEFAULT_GANTT_VIEW;
        return {
            doctype: "Project",
            fields: { text: "project_name", start: "expected_start_date", end: "expected_end_date", progress: "percent_complete" },
            filters: gantt_root_filters(),
            // Master Project where one is set, otherwise the project type
            group_by: ["custom_master_project", "project_type"],
            extra_fields: ["project_type", "status", "customer"],
            children: GANTT_TASK_CHILDREN,
            lazy_children: true,
            // Task rows only: Project bars are derived (expected_start/end are
            // read-only and recomputed from tasks by sync_project_dates_from_tasks),
            // and the widget never marks group rows editable.
            editable: { dates: true, progress: true },
            order_by: "expected_start_date asc",
            limit: 1000,
            today: true, // today column + open-at-today default (block owns the button)
            tooltip: true,
            zoom: GANTT_VIEW_MODE_MAP[mode] || "month",
            columns: gantt_columns(),
            gantt: { grid_width: gantt_grid_width() },
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
                // Group rows (G::) are synthetic buckets — clicking one should
                // only expand/collapse, never open the Project Type document.
                if (String(id).indexOf("G::") === 0) return;
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

    const is_narrow = () => window.innerWidth < 992;

    function gantt_columns() {
        const optional = [
            { name: "project_type", label: "Type", width: 70, align: "center", template: (t) => t.project_type || "" },
            { name: "start_date", label: "Start", width: 78, align: "center", template: (t) => (t.start_date ? fmt_date(moment(t.start_date).format("YYYY-MM-DD")) : "") },
            { name: "end_date", label: "End", width: 78, align: "center", template: inclusive_end },
            { name: "progress", label: "%", width: 44, align: "center", template: (t) => (typeof t.progress === "number" ? Math.round(t.progress * 100) + "%" : "") },
        ].filter((c) => gantt_visible_columns.has(c.name));
        return [
            { name: "text", label: "Project / Task", tree: true, width: is_narrow() ? 150 : 220, resize: true },
            ...optional,
        ];
    }

    // Sized from the columns actually shown, so a phone is not left with a
    // grid wider than its screen and no room for the chart.
    function gantt_grid_width() {
        return gantt_columns().reduce((sum, c) => sum + (c.width || 80), 0) + 16;
    }

    // Columns can change without refetching — rebuild them on the live chart.
    function apply_gantt_columns() {
        if (!portfolio_gantt_widget || !portfolio_gantt_widget.gantt) return;
        const g = portfolio_gantt_widget.gantt;
        g.config.columns = gantt_columns();
        g.config.grid_width = gantt_grid_width();
        g.render();
    }

    // ---- row styling -------------------------------------------------------

    // Composite ids are prefixed G:: (group) / P:: (project) / C:: (task) by
    // get_gantt_data. Projects take their project_type colour; their tasks take
    // a lighter shade of the same colour (stamped in load_project_tasks).
    // Styles live in projects_dashboard.css.
    function portfolio_row_class(start, end, task) {
        const id = String(task.id);
        const classes = [];
        if (task.ee_placeholder) return "pg-placeholder";
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
            html += `<span class="pg-legend-item"><i class="pg-swatch" style="background:${t.color}"></i>${sf.esc(t.type)}</span>`;
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
                // pass the row id so its "Loading…" placeholder is retired
                const added = portfolio_gantt_widget.add_rows(child_rows, data.links, row_id);
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
            sf.state.render(container[0], "error", {
                title: __("The Gantt widget is not loaded. Please refresh the page."),
            });
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

    // A rank as a pill: the company rank filled (navy in light, sky in dark), the stream
    // rank an outline. Named values (Maintenance, Repair Visit, Not Assigned) are muted
    // text: a rank is a number, and a word is not a rank. No colour scale, so a 1 and a
    // 30 read the same way: by the number.
    function rank_html(priority, kind) {
        const p = String(priority === null || priority === undefined ? "" : priority).trim();
        if (!p || p.toLowerCase() === "not assigned") return '<span class="pd-named">Not Assigned</span>';
        if (isNaN(parseInt(p, 10))) return `<span class="pd-named">${sf.esc(p)}</span>`;
        return `<span class="pd-rank${kind === "stream" ? " pd-rank-outline" : ""}">${sf.esc(p)}</span>`;
    }

    function build_editable_priority_cell(project_name, field, current_val, options_array, col_key, kind) {
        let opts_html = '<option value="">Not Assigned</option>' +
            options_array.map(opt => `<option value="${sf.esc(opt)}" ${opt === current_val ? 'selected' : ''}>${sf.esc(opt)}</option>`).join('');

        return `
            <td class="editable-priority dashcol dashcol-${col_key}" data-project="${sf.esc(project_name)}" data-field="${field}" data-kind="${kind}">
                <div class="static-view" title="Click to edit">
                    ${rank_html(current_val, kind)}
                </div>
                <select class="edit-view" style="display: none;" aria-label="${kind === "stream" ? "Value Stream Priority" : "Company Priority"}">
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

    function th(col_name, label, title="", extra_cls="") {
        let state = sort_state[current_tab];
        let cls = "sortable-header dashcol dashcol-" + col_name;
        if (state && state.col === col_name) {
            cls += " active-sort sort-" + state.order;
        }
        if (extra_cls) cls += " " + extra_cls;
        return `<th class="${cls}" data-sort="${col_name}" title="${sf.esc(title)}">${label}</th>`;
    }

    // One table for every list tab: the shared style, fixed layout for the resizer.
    const table_shell = (head_html) => $(`
        <table class="pd-table dashboard-resizable-table">
            <thead><tr>${head_html}</tr></thead>
            <tbody></tbody>
        </table>
    `);
    const table_wrap = () => $('<div class="pd-table-wrap table-responsive"></div>');

    // A group header row inside the table (Group by stream; the Internal tab's master
    // projects): the stream's pillar edge down its left side, a muted note after it.
    // The edge is the pillar's themed chip edge (sky-200 for Build and Neutral in dark,
    // where their opening stops sink into the navy card). Spelled out per tone so each
    // token the block reads is one sf_tokens.bundle.css defines.
    const GROUP_EDGE = {
        design: "var(--sf-chip-edge-design)",
        build: "var(--sf-chip-edge-build)",
        service: "var(--sf-chip-edge-service)",
        rent: "var(--sf-chip-edge-rent)",
        neutral: "var(--sf-chip-edge-neutral)",
    };
    function group_row(label, note, pillar, colspan) {
        const edge = `--pd-group-color: ${GROUP_EDGE[pillar] || GROUP_EDGE.neutral}`;
        return `<tr class="pd-group" style="${edge}"><td colspan="${colspan}">${sf.esc(label)}${note ? `<span>${sf.esc(note)}</span>` : ""}</td></tr>`;
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
            // The person's own choice now: the Group toggle leaves it alone.
            state.auto = false;
            save_sort_state();
            render_current_tab();
        });
    }

    // The group header rows (Group by stream; the Internal tab's master projects) span the
    // columns on show. The Columns menu hides cells by class, and a colspan wider than the
    // visible columns adds phantom columns, which take a share of the width under the fixed
    // table layout. Counted from the tab's selector, and set again when a column is toggled.
    function visible_column_count(tab) {
        const selector = column_selectors[tab];
        if (!selector || !Array.isArray(selector.columns) || typeof selector.is_hidden !== "function") return 1;
        return Math.max(1, selector.columns.filter((c) => !selector.is_hidden(c.key)).length);
    }
    function fit_group_rows(container, tab) {
        container.find('tr.pd-group > td').attr('colspan', String(visible_column_count(tab)));
    }

    // The Priority Overview's columns. Budget replaced Spend % (v1.781.0): Spend % was
    // estimated_costing over custom_project_dollar_amount, and estimated_costing is 0 on
    // every active project on prod, so the column had only ever shown a dash.
    function priority_head() {
        return [
            th('project_name', 'Project Name'),
            project_id_th,
            th('project_type', 'Type'),
            th('company_priority', 'Company Priority', 'Click a rank to change it'),
            th('project_priority', 'Value Stream Priority', 'Rank within the stream; click to change it'),
            th('percent_complete', 'Completion'),
            th('expected_end', 'Expected End'),
            th('budget', 'Budget', 'The project dollar amount', 'pd-num'),
        ].join('');
    }

    function priority_row(p) {
        const end = date_part(p.expected_end_date);
        const late = is_past_due(p) ? '<span class="pd-late">Past due</span>' : '';
        const end_html = end ? `${sf.esc(fmt_date(end))}${late}` : '<span class="pd-dash">—</span>';
        const budget = budget_of(p);
        return `
            <tr class="pd-row">
                ${name_cells(p)}
                <td class="dashcol dashcol-project_type">${type_chip(p)}</td>
                ${build_editable_priority_cell(p.name, 'custom_company_priority', p.custom_company_priority, priority_options.company_priority || [], 'company_priority', 'company')}
                ${build_editable_priority_cell(p.name, 'custom_project_priority', p.custom_project_priority, priority_options.project_priority || [], 'project_priority', 'stream')}
                ${completion_cell(p, 'percent_complete')}
                <td class="dashcol dashcol-expected_end pd-due">${end_html}</td>
                <td class="dashcol dashcol-budget pd-num">${budget ? sf.esc(fmt_money(budget)) : '<span class="pd-dash">—</span>'}</td>
            </tr>
        `;
    }

    // Click a rank to change it: the select takes the cell's place, saves on change.
    function bind_editable_priorities(table) {
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
                staticView.html(rank_html(val, cell.data('kind')));
                select.hide();
                staticView.show();
                auto_save_field(cell.data('project'), cell.data('field'), val, cell);
            });
        });
    }

    // One table: flat, or with a header row per stream when `groups` is given
    // ([{ stream, projects }], already ordered).
    function build_priority_table(projects, groups) {
        const wrapper = table_wrap();
        const table = table_shell(priority_head());
        const tbody = table.find('tbody');
        if (groups) {
            groups.forEach((g) => {
                const ranked = g.projects.filter(is_ranked).length;
                const note = __("{0} projects · {1} ranked", [g.projects.length, ranked]);
                tbody.append(group_row(g.stream, note, pillar_of(g.stream), visible_column_count('priority-overview')));
                g.projects.forEach((p) => tbody.append(priority_row(p)));
            });
        } else {
            projects.forEach((p) => tbody.append(priority_row(p)));
        }
        bind_editable_priorities(table);
        bind_sortable_headers(table);
        wrapper.append(table);
        return wrapper;
    }

    // The sort for the Priority Overview's columns. Blank dates sink either way.
    function compare_priority_rows(a, b, state) {
        let diff = 0;
        if (state.col === 'project_name') {
            diff = String(a.project_name || "").localeCompare(String(b.project_name || ""));
        } else if (state.col === 'project_type') {
            diff = compare_value_streams(stream_of(a), stream_of(b));
        } else if (state.col === 'company_priority') {
            diff = get_priority_weight(a.custom_company_priority) - get_priority_weight(b.custom_company_priority);
        } else if (state.col === 'project_priority') {
            diff = get_priority_weight(a.custom_project_priority) - get_priority_weight(b.custom_project_priority);
        } else if (state.col === 'percent_complete') {
            diff = pct_of(a) - pct_of(b);
        } else if (state.col === 'expected_end') {
            const ea = date_part(a.expected_end_date), eb = date_part(b.expected_end_date);
            if (!ea && eb) return 1;
            if (ea && !eb) return -1;
            diff = ea.localeCompare(eb);
        } else if (state.col === 'budget') {
            diff = budget_of(a) - budget_of(b);
        }
        if (diff === 0 && state.col !== 'project_name') {
            diff = String(a.project_name || "").localeCompare(String(b.project_name || ""));
        }
        return state.order === 'asc' ? diff : -diff;
    }

    // The rows the tile and the Ranked-only box leave; the stream chips count these.
    function overview_candidates() {
        const tile = TILE_TESTS[tile_filter] || TILE_TESTS.active;
        return active_pool().filter((p) => tile(p) && (!overview.ranked_only || is_ranked(p)));
    }

    function render_stream_chips(candidates) {
        $root.find('[data-stream-count=""]').text(String(candidates.length));
        STREAM_CHIPS.forEach((stream) => {
            $root.find(`[data-stream-count="${stream}"]`).text(String(candidates.filter((p) => stream_of(p) === stream).length));
        });
        [""].concat(STREAM_CHIPS).forEach((stream) => {
            $root.find(`.pd-fchip[data-stream="${stream}"]`).attr('aria-pressed', stream === overview.stream ? 'true' : 'false');
        });
        $root.find('#pd-ranked-only').prop('checked', overview.ranked_only);
        $root.find('#pd-group-by-stream').prop('checked', overview.group);
    }

    function render_priority_overview() {
        let container = $root.find('#dashboard-content');
        container.empty();
        render_column_toolbar(container);

        const state = sort_state['priority-overview'];
        const candidates = overview_candidates();
        render_stream_chips(candidates);

        if (!active_pool().length) {
            empty_panel(container, __("No active projects."));
            return;
        }
        const projects_to_show = overview.stream
            ? candidates.filter((p) => stream_of(p) === overview.stream)
            : candidates.slice();
        if (!projects_to_show.length) {
            empty_panel(container, __("No projects match these filters."));
            return;
        }

        projects_to_show.sort((a, b) => compare_priority_rows(a, b, state));

        if (overview.group) {
            const by_stream = {};
            projects_to_show.forEach((p) => {
                const stream = stream_of(p);
                if (!by_stream[stream]) by_stream[stream] = [];
                by_stream[stream].push(p);
            });
            const groups = Object.keys(by_stream).sort(compare_value_streams).map((stream) => ({ stream, projects: by_stream[stream] }));
            container.append(build_priority_table(projects_to_show, groups));
        } else {
            container.append(build_priority_table(projects_to_show));
        }

        column_selectors['priority-overview'].apply(container);
        apply_column_resizing(container);
    }

    // ----- PRIORITY OVERVIEW FILTERS: stream chips, Ranked only, Group by stream -----

    $root.find('.pd-fchip').on('click', function() {
        overview.stream = $(this).attr('data-stream') || "";
        save_overview();
        if (current_tab === "priority-overview") render_current_tab();
    });

    $root.find('#pd-ranked-only').on('change', function() {
        overview.ranked_only = $(this).is(':checked');
        save_overview();
        if (current_tab === "priority-overview") render_current_tab();
    });

    // Grouping orders each stream by its Value Stream Priority (the pick), so switching it
    // on moves the sort from the flat default (company rank) to the stream rank, and
    // switching it off moves it back — while the sort is the page's own (`auto`). A column
    // a person clicked, Company Priority or Value Stream Priority included, is left alone,
    // in the direction they chose; the swap itself never touches the direction either.
    $root.find('#pd-group-by-stream').on('change', function() {
        overview.group = $(this).is(':checked');
        const state = sort_state['priority-overview'];
        const own = overview.group ? 'project_priority' : 'company_priority';
        if (state.auto && state.col !== own) {
            state.col = own;
            save_sort_state();
        }
        save_overview();
        if (current_tab === "priority-overview") render_current_tab();
    });

    // The Internal tab: one table, a header row per master project (the same group row
    // the Priority Overview draws), status and priority as selects that save on change.
    function internal_row(p) {
        const status_html = `<select class="pd-select project-inline-edit" data-field="status" data-project="${sf.esc(p.name)}" aria-label="Status">
            ${status_options.map(s => `<option value="${sf.esc(s)}" ${p.status === s ? 'selected' : ''}>${sf.esc(s)}</option>`).join('')}
        </select>`;

        const priority_opts = priority_options.project_priority || [];
        const priority_html = `<select class="pd-select project-inline-edit" data-field="custom_project_priority" data-project="${sf.esc(p.name)}" aria-label="Priority">
            <option value="">Not Assigned</option>
            ${priority_opts.map(opt => `<option value="${sf.esc(opt)}" ${p.custom_project_priority === opt ? 'selected' : ''}>${sf.esc(opt)}</option>`).join('')}
        </select>`;

        return `
            <tr class="pd-row">
                ${name_cells(p)}
                <td class="dashcol dashcol-status">${status_html}</td>
                <td class="dashcol dashcol-custom_project_priority">${priority_html}</td>
                ${completion_cell(p, 'percent_complete')}
                <td class="dashcol dashcol-project_user">${p.project_user ? sf.esc(p.project_user) : '<span class="pd-muted">Unassigned</span>'}</td>
            </tr>
        `;
    }

    function build_internal_table(groups) {
        const wrapper = table_wrap();
        const table = table_shell([
            th('project_name', 'Project Name'),
            project_id_th,
            th('status', 'Status'),
            th('custom_project_priority', 'Priority'),
            th('percent_complete', '% Complete'),
            th('project_user', 'Assigned To'),
        ].join(''));
        const tbody = table.find('tbody');

        groups.forEach((g) => {
            tbody.append(group_row(g.master, __("{0} projects", [g.projects.length]), "neutral", visible_column_count('active-internal-projects')));
            g.projects.forEach((p) => tbody.append(internal_row(p)));
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
        // active_pool(), so this tab and the Internal chip count the same projects.
        let internal_projects = active_pool().filter(p => INTERNAL_PROJECT_TYPES.includes(p.project_type));
        if (!internal_projects.length) {
            empty_panel(container, __("No active internal projects."));
            return;
        }

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

        const ordered = sorted_masters.map((master) => ({
            master,
            projects: groups[master].sort((a, b) => {
                let diff = 0;
                if (state.col === 'project_name') diff = String(a.project_name||"").localeCompare(String(b.project_name||""));
                else if (state.col === 'status') diff = String(a.status||"").localeCompare(String(b.status||""));
                else if (state.col === 'custom_project_priority') diff = get_priority_weight(a.custom_project_priority) - get_priority_weight(b.custom_project_priority);
                else if (state.col === 'percent_complete') diff = pct_of(a) - pct_of(b);
                else if (state.col === 'project_user') diff = String(a.project_user||"").localeCompare(String(b.project_user||""));
                if (diff === 0 && state.col !== 'project_name') diff = String(a.project_name||"").localeCompare(String(b.project_name||""));
                return state.order === 'asc' ? diff : -diff;
            }),
        }));
        container.append(build_internal_table(ordered));

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

        const wrapper = table_wrap();
        const table = table_shell([
            th('project_name', 'Project Name'),
            project_id_th,
            th('status', 'Status'),
            th('project_type', 'Type'),
            th('project_user', 'Assigned To'),
            th('completed_on', 'Completed On', 'When the project was marked inactive'),
        ].join(''));

        completed_projects.forEach(p => {
            // The status word in a kit chip: finished and paid is good, the rest neutral.
            const status_tone = p.status === 'Completed' || p.status === 'Paid' ? 'good' : 'neutral';

            table.find('tbody').append(`
                <tr class="pd-row">
                    ${name_cells(p)}
                    <td class="dashcol dashcol-status">${sf.chip(p.status, status_tone)}</td>
                    <td class="dashcol dashcol-project_type">${type_chip(p)}</td>
                    <td class="dashcol dashcol-project_user pd-muted">${sf.esc(p.project_user || "Unassigned")}</td>
                    <td class="dashcol dashcol-completed_on">${p.completed_on ? sf.esc(frappe.datetime.str_to_user(p.completed_on)) : '<span class="pd-dash">—</span>'}</td>
                </tr>
            `);
        });

        bind_sortable_headers(table);
        wrapper.append(table);

        if (completed_projects.length === 0) empty_panel(container, __("No completed projects found."));
        else {
            container.append(wrapper);
            column_selectors['completed-projects'].apply(container);
            apply_column_resizing(container);
        }
    }

    // Summary tab — a native Projects-module overview computed client-side from
    // the already-fetched project_data (no extra server call). Number cards + CSS
    // bar breakdowns; deliberately no frappe.Chart, whose JS-injected styles don't
    // cross into this block's shadow root. Styled from the tokens like the tables.
    function build_breakdown(title, obj, full) {
        const labels = Object.keys(obj);
        const max = Math.max(1, ...labels.map(l => obj[l]));
        let rows = "";
        labels.forEach(l => {
            const v = obj[l];
            const pct = Math.round((v / max) * 100);
            rows += `
                <div class="pd-brow">
                    <div class="pd-brow-l">
                        <span>${sf.esc(l)}</span>
                        <span>${v}</span>
                    </div>
                    <div class="pd-brow-bar" aria-hidden="true"><i style="width: ${pct}%"></i></div>
                </div>`;
        });
        if (!labels.length) rows = '<div class="pd-muted">No data</div>';
        return `
            <section class="pd-breakdown${full ? ' pd-breakdown-full' : ''}">
                <h3>${sf.esc(title)}</h3>
                ${rows}
            </section>`;
    }

    function render_dashboard() {
        let container = $root.find('#dashboard-content');
        container.empty();

        const today = frappe.datetime.get_today();
        // active_pool(), so Active Projects and Overdue agree with the tiles.
        const active = active_pool();
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

        // The numbers in the display face, the strong ink; only Overdue is coloured, in
        // the danger red, and it carries its word.
        const cards = [
            { label: "Active Projects", value: total_active },
            { label: "Overdue", value: overdue, bad: true },
            { label: "Avg % Complete", value: avg_complete + "%" },
            { label: "Open Tasks", value: open_tasks },
            { label: "Master Projects", value: masters.size },
            { label: "Completed", value: completed }
        ];

        let cardRow = $('<div class="pd-cards"></div>');
        cards.forEach(c => {
            cardRow.append(`
                <section class="pd-card">
                    <div class="pd-card-l">${sf.esc(c.label)}</div>
                    <div class="pd-card-n${c.bad ? ' pd-card-n-bad' : ''}">${sf.esc(c.value)}</div>
                </section>
            `);
        });
        container.append(cardRow);

        let chartRow = $('<div class="pd-breakdowns"></div>');
        chartRow.append(build_breakdown("Active Projects by Status", by_status, false));
        chartRow.append(build_breakdown("Active Projects by Type", by_type, false));
        chartRow.append(build_breakdown("Active Projects by Completion", buckets, true));
        container.append(chartRow);

        if (total_active === 0) {
            empty_panel(container, __("No active projects."));
        }
    }

    // Which controls belong to the tab on screen: the band's search box and the stream
    // filter row are for the list tabs (the filter row for Priority Overview only); the
    // Gantt has its own toolbar.
    function show_tab_controls() {
        const on_gantt = current_tab === "portfolio-gantt";
        $root.find('#gantt-controls').toggle(on_gantt);
        $root.find('#standard-controls').toggle(!on_gantt);
        $root.find('#overview-controls').toggle(current_tab === "priority-overview");
    }

    function render_current_tab() {
        show_tab_controls();
        if (current_tab === "portfolio-gantt") {
            fetch_and_render_portfolio_gantt();
        } else {
            // Leaving the Gantt tab: the renderers below empty
            // #dashboard-content, so tear the widget down properly first
            // (destructor removes DHTMLX's document listeners + interval).
            if (portfolio_gantt_widget) {
                portfolio_gantt_widget.destroy();
                portfolio_gantt_widget = null;
            }

            // Nothing has loaded (still loading, no access, or a failed read): the panel
            // stands, with its Try again, until a fetch answers with rows.
            if (!loaded) {
                show_panel(panel.kind, panel.opts);
                return;
            }

            if (current_tab === "priority-overview") render_priority_overview();
            else if (current_tab === "active-internal-projects") render_active_internal();
            else if (current_tab === "completed-projects") render_completed_projects();
            else if (current_tab === "summary") render_dashboard();

            setTimeout(apply_search_filter, 50);
        }
    }

    // The text a person sees in a row. A rank or status select carries every option, so
    // row.text() matched "Maintenance", "Not Assigned" or any digit on every row; here a
    // select counts as its chosen option only.
    function row_search_text(row) {
        const copy = row.clone();
        copy.find('select').each(function() {
            $(this).replaceWith($('<span></span>').text($(this).find('option:selected').text()));
        });
        return copy.text().toLowerCase();
    }

    // The band's search narrows the rows of the list tab on screen by their text. A group
    // header row stays while any row under it is showing, and goes when none is.
    function apply_search_filter() {
        let search_term = $root.find('#global-project-search').val();
        search_term = (search_term || "").toLowerCase();
        let rows = $root.find('#dashboard-content table tbody tr');

        let group = null;
        let group_has_rows = false;
        const close_group = () => { if (group) group.toggle(group_has_rows); };
        rows.each(function() {
            let row = $(this);
            if (row.hasClass('pd-group')) {
                close_group();
                group = row;
                group_has_rows = false;
                return;
            }
            const shown = row_search_text(row).indexOf(search_term) !== -1;
            row.toggle(shown);
            if (shown) group_has_rows = true;
        });
        close_group();
    }

    $root.find('#global-project-search').on('input', function() {
        apply_search_filter();
    });

    $root.find('.pd-tab').on('click', function(e) {
        e.preventDefault();
        show_tab($(this).attr('data-route'));
    });

    // ----- TABS -----
    //
    // Five tabs, always in the bar. The one chosen is kept per browser (TAB_KEY), so the
    // dashboard opens where it was left.
    function show_tab(route) {
        if (!TAB_ROUTES.includes(route)) route = "priority-overview";
        $root.find('.pd-tab').removeClass('active').attr('aria-pressed', 'false');
        $root.find(`.pd-tab[data-route="${route}"]`).addClass('active').attr('aria-pressed', 'true');
        current_tab = route;
        write_saved(TAB_KEY, route);
        render_current_tab();
    }

    // Mark the remembered tab in the bar before the first fetch draws it.
    $root.find('.pd-tab').removeClass('active').attr('aria-pressed', 'false');
    $root.find(`.pd-tab[data-route="${current_tab}"]`).addClass('active').attr('aria-pressed', 'true');
    show_tab_controls();

    // ----- HEADER ACTIONS: one "+" button -----
    //
    // The "+" opens a small menu: New Project, New Master Project. Each item does what its
    // button did before (frappe.new_doc, which opens the new form on its own history entry,
    // so Back returns here). Escape or a click elsewhere closes the menu.
    (function init_new_menu() {
        const $wrap = $root.find('#pg-new-menu');
        const $toggle = $root.find('#btn-new');
        const $list = $root.find('#pg-new-list');
        if (!$wrap.length) return;

        const close = () => {
            $list.prop('hidden', true);
            $toggle.attr('aria-expanded', 'false');
        };
        const open = () => {
            $list.prop('hidden', false);
            $toggle.attr('aria-expanded', 'true');
            $list.find('.pg-new-item').first().trigger('focus');
        };

        // No stopPropagation: the click must reach the document so another open menu (Columns,
        // a Gantt filter) closes; the outside() check below knows this button is inside.
        $toggle.on('click', function() {
            if ($list.prop('hidden')) open(); else close();
        });
        $wrap.on('keydown', function(e) {
            if (e.key === 'Escape' && !$list.prop('hidden')) {
                close();
                $toggle.trigger('focus');
            } else if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && !$list.prop('hidden')) {
                e.preventDefault();
                const items = $list.find('.pg-new-item').get();
                const at = items.indexOf($(e.target).closest('.pg-new-item')[0]);
                const next = e.key === 'ArrowDown' ? (at + 1) % items.length : (at - 1 + items.length) % items.length;
                items[next].focus();
            }
        });

        // The workspace can draw the page again (a different root), so this document-level
        // listener retires itself once its button has left the page.
        const outside = (ev) => {
            if (!$wrap[0].isConnected) {
                $(document).off('click', outside);
                return;
            }
            // composedPath sees through the shadow boundary, where ev.target is only the host
            const oe = ev.originalEvent || ev;
            const path = typeof oe.composedPath === 'function' ? oe.composedPath() : [];
            if (path.indexOf($wrap[0]) === -1) close();
        };
        $(document).on('click', outside);

        $root.find('#btn-new-project').on('click', () => { close(); frappe.new_doc('Project'); });
        $root.find('#btn-new-master-project').on('click', () => { close(); frappe.new_doc('Master Project'); });
    })();

    // Init
    fetch_initial_data();

    // Coming back to the workspace from a project or a task re-runs nothing (the
    // header), so the shared return helper runs fetch_data() then: the rows are
    // fetched again and the tab on screen is drawn again, the Portfolio Gantt
    // included, with the tab, the filters and the expanded projects kept. A bundle
    // cached from before the helper has none, and the block still draws.
    const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
    if (blocks && blocks.onWorkspaceReturn) blocks.onWorkspaceReturn(container, fetch_data);
    }
})();
