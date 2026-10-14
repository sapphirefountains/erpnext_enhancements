// Task Dashboard — Custom HTML Block script (runs in the block's shadow-DOM
// sandbox; `root_element` is the shadow root provided by Frappe).
//
// Fetches everything from one whitelisted endpoint
// (erpnext_enhancements.api.task_dashboard.get_task_dashboard_data) and
// renders four panels: Top-10 priority projects (all at once, with PM/tech
// lead), Overdue/At-Risk tasks, Today's tasks (with assigned technicians),
// and today's public calendar events.
//
// Refresh model: realtime "project_dashboard_updated" (already published on
// every Project/Task save by project_dashboard.publish_realtime_update),
// debounced 5s, plus a 5-minute interval as the kiosk fallback. Both only fetch
// while the block is on screen (onScreen); a refresh skipped off screen is paid
// by the return helper, or by the 30 s clock tick after it is back. Workspace
// re-renders re-run this whole script with a NEW root_element, so all timers
// and the single realtime subscription are stored on `window` and re-pointed
// instead of stacked. A re-render is the first visit and coming back from a
// different workspace, not every return: from a form or a list, v16's
// Workspace.show() returns early on the workspace already shown. startApp hands
// refresh() to the shared return helper,
// public/js/global_enhancements/workspace_block_return.js, which runs it then, so
// a desk user does not wait up to five minutes to see a task they just closed.
//
// Its panels (loading, empty, error, no access), chips and escaping come from sf_kit,
// and its "only the newest answer draws" counter from blocks.ticket, both handed over
// by the same helper (custom_html_blocks/README.md).

(function () {
    const REFRESH_MS = 5 * 60 * 1000;
    const DEBOUNCE_MS = 5000;
    const MAX_ATTEMPTS = 50;
    let attempts = 0;

    // The kit's escape (sf.esc), set by startApp before anything draws. The row helpers
    // below read it by this name.
    let esc;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

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

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#tdb-top-projects")) {
            withKit(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        } else {
            console.warn("Task Dashboard: block DOM never appeared.");
        }
    }

    // Is this render's block actually on screen? The desk is a single-page app: leaving
    // the Home workspace for a form or list hides the block (display:none on an
    // ancestor) or, once the workspace re-renders, detaches it, and its timers keep
    // firing all day. The shared helper asks the shadow root's host (detached, or no
    // client rects under a display:none ancestor), which beats comparing
    // frappe.get_route() to a workspace name: that breaks on a private or renamed Home.
    // A device whose bundle predates the helper has no answer, so it fails open (true):
    // polling as before.
    function onScreen(container) {
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (!blocks || typeof blocks.onScreen !== "function") return true;
        return blocks.onScreen(container);
    }

    function startApp(container, sf, ticket) {
        esc = sf.esc;
        const state = (window.__ee_task_dashboard = window.__ee_task_dashboard || {});

        // Re-point timers at the freshest render; never stack them.
        if (state.refresh_timer) clearInterval(state.refresh_timer);
        if (state.clock_timer) clearInterval(state.clock_timer);
        state.refresh_timer = state.clock_timer = null;

        // Anything the interval or realtime wanted to do while the block was off screen is
        // owed one refresh. An ordinary return to the workspace is refreshed at once by the
        // shared return helper below (the router handler lives there, not in a block); this
        // flag covers a block that comes back some other way, via the 30 s tick_clock.
        state.stale = false;

        function tick_clock() {
            // The clock is local, so it keeps ticking; it is also the cheap heartbeat that
            // notices the block came back and pays the refresh it was owed.
            if (!onScreen(container)) return;
            if (state.stale && state.refresh === refresh && !document.hidden) refresh();
            const el = container.querySelector("#tdb-clock");
            if (!el) return;
            const now = new Date();
            el.textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
        }

        // The interval, a realtime update and a return to the workspace can all ask at
        // once, so each ask takes a ticket on the root (blocks.ticket) and only the newest
        // answer draws.
        function refresh() {
            state.stale = false;
            const current = ticket(container);
            frappe
                .call("erpnext_enhancements.api.task_dashboard.get_task_dashboard_data")
                .then((r) => {
                    if (current()) render(container, r.message, sf);
                })
                .catch((err) => {
                    console.error("Task Dashboard refresh failed:", err);
                    // A transient failure never wipes data already showing; only a block
                    // that has never drawn swaps its loading panel for an error one.
                    if (!current() || container.__ee_rendered) return;
                    const el = container.querySelector("#tdb-top-projects");
                    if (!el) return;
                    const title = __("Could not load the dashboard. It will retry shortly.");
                    if (err && err.status === 403) sf.state.render(el, "no-access");
                    else sf.state.render(el, "error", { title, on_action: refresh });
                });
        }

        // Interval and realtime both come through here: refresh only while the block is
        // on screen, skip silently otherwise and remember that a refresh is owed.
        function refresh_if_visible() {
            if (document.hidden || !onScreen(container)) {
                state.stale = true;
                return;
            }
            refresh();
        }

        state.refresh = refresh;
        state.refresh_if_visible = refresh_if_visible;
        state.refresh_timer = setInterval(refresh_if_visible, REFRESH_MS);
        state.clock_timer = setInterval(tick_clock, 30 * 1000);

        // One realtime subscription for the lifetime of the tab; it always
        // calls whatever the latest render registered as state.refresh_if_visible.
        if (!state.realtime_bound && window.frappe && frappe.realtime) {
            state.realtime_bound = true;
            const debounced = frappe.utils.debounce(() => {
                if (window.__ee_task_dashboard.refresh_if_visible) {
                    window.__ee_task_dashboard.refresh_if_visible();
                }
            }, DEBOUNCE_MS);
            frappe.realtime.on("project_dashboard_updated", debounced);
        }

        tick_clock();
        const first = container.querySelector("#tdb-top-projects");
        if (first) sf.state.render(first, "loading");
        refresh();

        // No timer and no subscription here: the helper binds its one route handler
        // itself, and runs this render's refresh() only while this block is on the page.
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.onWorkspaceReturn) blocks.onWorkspaceReturn(container, refresh);
    }

    // ------------------------------------------------------------------ render

    function priority_slug(priority) {
        const p = String(priority || "low").toLowerCase();
        return ["urgent", "high", "medium", "low"].includes(p) ? p : "low";
    }

    function assignee_chips(assignees) {
        if (!assignees || !assignees.length) {
            return '<span class="tdb-chip tdb-chip-none">Unassigned</span>';
        }
        return assignees.map((name) => `<span class="tdb-chip">${esc(name)}</span>`).join("");
    }

    function rank_star(rank) {
        return rank ? `<span class="tdb-rank">&#9733; ${esc(rank)}</span>` : "";
    }

    function task_row(task, extra_badge) {
        const project = task.project
            ? `<a class="tdb-task-project" href="${frappe.utils.get_form_link("Project", task.project)}">
                   &#128194; ${esc(task.project_label || task.project)}${rank_star(task.project_rank)}
               </a>`
            : '<span class="tdb-task-project tdb-no-project">&#9888; No project linked</span>';
        return `
            <a class="tdb-task prio-${priority_slug(task.priority)}" href="${frappe.utils.get_form_link("Task", task.name)}">
                <div class="tdb-task-top">
                    <span class="tdb-task-subject">${esc(task.subject)}</span>
                    ${extra_badge || ""}
                </div>
                ${project}
                <div class="tdb-task-people">${assignee_chips(task.assignees)}</div>
            </a>
        `;
    }

    // Each panel's own sentence in the kit's empty panel.
    function empty(el, sf, title) {
        if (el) sf.state.render(el, "empty", { title });
    }

    function render(container, data, sf) {
        if (!data) {
            // An empty answer never wipes data already showing; a block that has never
            // drawn says there is nothing to show rather than loading forever.
            if (!container.__ee_rendered) {
                empty(container.querySelector("#tdb-top-projects"), sf, __("Nothing to show yet."));
            }
            return;
        }
        container.__ee_rendered = true;
        const top_projects = data.top_projects || [];
        const overdue_tasks = data.overdue_tasks || [];
        const today_tasks = data.today_tasks || [];
        const events = data.events || [];

        const updated = container.querySelector("#tdb-updated");
        if (updated) {
            updated.textContent = "updated " + new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
        }

        // --- Top 10 priority projects: the all-at-once list ---
        const projects_el = container.querySelector("#tdb-top-projects");
        if (top_projects.length) {
            projects_el.innerHTML = top_projects
                .map(
                    (p) => `
                    <a class="tdb-project" href="${frappe.utils.get_form_link("Project", p.name)}">
                        <span class="tdb-project-rank">${esc(p.rank)}</span>
                        <span class="tdb-project-main">
                            <span class="tdb-project-name">${esc(p.project_name || p.name)}</span>
                            <span class="tdb-project-people">
                                ${p.pm ? `<span class="tdb-role">PM</span> ${esc(p.pm)}` : ""}
                                ${p.pm && p.tech_lead ? "&nbsp;&middot;&nbsp;" : ""}
                                ${p.tech_lead ? `<span class="tdb-role">Tech</span> ${esc(p.tech_lead)}` : ""}
                            </span>
                        </span>
                        <span class="tdb-project-pct">
                            <span class="tdb-pct-bar"><span style="width:${Math.min(Math.max(p.percent_complete, 0), 100)}%"></span></span>
                            ${esc(p.percent_complete)}%
                        </span>
                    </a>`
                )
                .join("");
        } else {
            empty(projects_el, sf, __("No ranked projects (set Company Priority 1–30)."));
        }

        // --- Overdue / at-risk ---
        const overdue_el = container.querySelector("#tdb-overdue");
        const overdue_count = container.querySelector("#tdb-overdue-count");
        if (overdue_count) {
            overdue_count.textContent = `${overdue_tasks.length}${data.overdue_overflow ? "+" : ""}`;
        }
        if (overdue_tasks.length) {
            overdue_el.innerHTML = overdue_tasks
                .map((t) => task_row(t, sf.chip(__("{0}d overdue", [t.days_overdue]), "danger")))
                .join("");
        } else {
            empty(overdue_el, sf, __("Nothing overdue."));
        }

        // --- Today's tasks, with the technicians on them ---
        const today_el = container.querySelector("#tdb-today");
        const today_count = container.querySelector("#tdb-today-count");
        if (today_count) today_count.textContent = String(today_tasks.length);
        if (today_tasks.length) today_el.innerHTML = today_tasks.map((t) => task_row(t)).join("");
        else empty(today_el, sf, __("No tasks scheduled for today."));

        // --- Today's calendar ---
        const events_el = container.querySelector("#tdb-events");
        if (!events.length) {
            empty(events_el, sf, __("No events today."));
            return;
        }
        events_el.innerHTML = events
            .map((ev) => {
                const when = ev.all_day
                    ? "All day"
                    : new Date(ev.starts_on.replace(" ", "T")).toLocaleTimeString([], {
                            hour: "numeric",
                            minute: "2-digit",
                        });
                return `
                    <div class="tdb-event">
                        <span class="tdb-event-time">${esc(when)}</span>
                        <span class="tdb-event-subject">${esc(ev.subject)}</span>
                    </div>`;
            })
            .join("");
    }

    waitForDOM();
})();
