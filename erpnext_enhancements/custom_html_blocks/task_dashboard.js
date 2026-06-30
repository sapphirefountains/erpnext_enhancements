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
// debounced 5s, plus a 5-minute interval as the kiosk fallback. Workspace
// re-renders re-run this whole script with a NEW root_element, so all timers
// and the single realtime subscription are stored on `window` and re-pointed
// instead of stacked.

(function () {
    const REFRESH_MS = 5 * 60 * 1000;
    const DEBOUNCE_MS = 5000;
    const MAX_ATTEMPTS = 50;
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function esc(value) {
        return frappe.utils.escape_html(String(value == null ? "" : value));
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#tdb-top-projects")) {
            startApp(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        } else {
            console.warn("Task Dashboard: block DOM never appeared.");
        }
    }

    function startApp(container) {
        const state = (window.__ee_task_dashboard = window.__ee_task_dashboard || {});

        // Re-point timers at the freshest render; never stack them.
        if (state.refresh_timer) clearInterval(state.refresh_timer);
        if (state.clock_timer) clearInterval(state.clock_timer);

        function tick_clock() {
            const el = container.querySelector("#tdb-clock");
            if (!el) return;
            const now = new Date();
            el.textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
        }

        function refresh() {
            frappe
                .call("erpnext_enhancements.api.task_dashboard.get_task_dashboard_data")
                .then((r) => render(container, r.message))
                .catch((err) => console.error("Task Dashboard refresh failed:", err));
        }

        state.refresh = refresh;
        state.refresh_timer = setInterval(() => {
            if (!document.hidden) refresh();
        }, REFRESH_MS);
        state.clock_timer = setInterval(tick_clock, 30 * 1000);

        // One realtime subscription for the lifetime of the tab; it always
        // calls whatever the latest render registered as state.refresh.
        if (!state.realtime_bound && window.frappe && frappe.realtime) {
            state.realtime_bound = true;
            const debounced = frappe.utils.debounce(() => {
                if (!document.hidden && window.__ee_task_dashboard.refresh) {
                    window.__ee_task_dashboard.refresh();
                }
            }, DEBOUNCE_MS);
            frappe.realtime.on("project_dashboard_updated", debounced);
        }

        tick_clock();
        refresh();
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
            ? `<a class="tdb-task-project" href="/app/project/${encodeURIComponent(task.project)}">
                   &#128194; ${esc(task.project_label || task.project)}${rank_star(task.project_rank)}
               </a>`
            : '<span class="tdb-task-project tdb-no-project">&#9888; No project linked</span>';
        return `
            <a class="tdb-task prio-${priority_slug(task.priority)}" href="/app/task/${encodeURIComponent(task.name)}">
                <div class="tdb-task-top">
                    <span class="tdb-task-subject">${esc(task.subject)}</span>
                    ${extra_badge || ""}
                </div>
                ${project}
                <div class="tdb-task-people">${assignee_chips(task.assignees)}</div>
            </a>
        `;
    }

    function render(container, data) {
        if (!data) return;

        const updated = container.querySelector("#tdb-updated");
        if (updated) {
            updated.textContent = "updated " + new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
        }

        // --- Top 10 priority projects: the all-at-once list ---
        const projects_el = container.querySelector("#tdb-top-projects");
        if (data.top_projects.length) {
            projects_el.innerHTML = data.top_projects
                .map(
                    (p) => `
                    <a class="tdb-project" href="/app/project/${encodeURIComponent(p.name)}">
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
            projects_el.innerHTML = '<div class="tdb-empty">No ranked projects (set Company Priority 1&ndash;30).</div>';
        }

        // --- Overdue / at-risk ---
        const overdue_el = container.querySelector("#tdb-overdue");
        const overdue_count = container.querySelector("#tdb-overdue-count");
        if (overdue_count) {
            overdue_count.textContent = `${data.overdue_tasks.length}${data.overdue_overflow ? "+" : ""}`;
        }
        overdue_el.innerHTML = data.overdue_tasks.length
            ? data.overdue_tasks
                    .map((t) =>
                        task_row(t, `<span class="tdb-overdue-badge">${esc(t.days_overdue)}d overdue</span>`)
                    )
                    .join("")
            : '<div class="tdb-empty tdb-empty-good">Nothing overdue &#127881;</div>';

        // --- Today's tasks, with the technicians on them ---
        const today_el = container.querySelector("#tdb-today");
        const today_count = container.querySelector("#tdb-today-count");
        if (today_count) today_count.textContent = String(data.today_tasks.length);
        today_el.innerHTML = data.today_tasks.length
            ? data.today_tasks.map((t) => task_row(t)).join("")
            : '<div class="tdb-empty">No tasks scheduled for today.</div>';

        // --- Today's calendar ---
        const events_el = container.querySelector("#tdb-events");
        events_el.innerHTML = data.events.length
            ? data.events
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
                    .join("")
            : '<div class="tdb-empty">No events today.</div>';
    }

    waitForDOM();
})();
