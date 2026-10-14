// My Priority Tasks — the "Home Dashboard Tasks" Custom HTML Block.
//
// The viewer's own open tasks, the company's top-ranked projects first, from
// erpnext_enhancements.api.task_dashboard.get_my_priority_tasks. The block was made on
// the site in 2025-12 and lived only in the database until v1.680.0; this is its source
// now, and the seeder overwrites the site copy with it. What changed in the move: the two
// client-side get_list calls became one endpoint that does the sorting, every value is
// escaped, a task opens in place instead of reloading the Desk, it binds nothing on
// document.body (the old script added a body listener on every render, and they stacked),
// and its colours are the app's tokens.
//
// Shadow-DOM sandbox: `root_element` is the shadow root. The workspace runs this whole
// script again, with a fresh root, only when it renders the page: the first visit, and
// coming back from a different workspace. Coming back from a form or a list does not
// (v16's Workspace.show() returns early on the workspace already shown), so startApp hands
// load() to the shared return helper, public/js/global_enhancements/workspace_block_return.js,
// which runs it again then. Every listener is bound to the fresh DOM.

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.task_dashboard.get_my_priority_tasks";
    // How many cards one "Show more" adds, as the old block had it.
    const BATCH = 50;
    let attempts = 0;

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
        if (container.querySelector("#mpt-body")) {
            withKit(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    // Urgent and High carry a status tone; Medium and Low are plain words. The word is
    // always there, so the colour is never the only signal.
    const PRIORITY_TONE = { Urgent: "danger", High: "warn" };

    function userDate(value) {
        if (!value) return "";
        try {
            return frappe.datetime.str_to_user(value);
        } catch (e) {
            return String(value);
        }
    }

    function card(sf, t) {
        const project = t.project
            ? sf.esc(t.project_name ? `${t.project_name} · ${t.project}` : t.project)
            : `<span class="mpt-none">${sf.esc(__("No project"))}</span>`;
        const rank = t.rank ? `<span class="mpt-rank">${sf.esc(__("Rank {0}", [t.rank]))}</span>` : "";
        const due = t.exp_end_date ? sf.esc(__("Due {0}", [userDate(t.exp_end_date)])) : "";
        const overdue = t.status === "Overdue" ? sf.chip(__("Overdue"), "danger") : "";
        const priority = t.priority ? sf.chip(__(t.priority), PRIORITY_TONE[t.priority] || "") : "";
        const tone = PRIORITY_TONE[t.priority] ? ` mpt-task--${PRIORITY_TONE[t.priority]}` : "";
        return `
            <a class="mpt-task${tone}" href="/desk/task/${encodeURIComponent(t.name)}" data-task="${sf.esc(t.name)}">
                <span class="mpt-subject">${sf.esc(t.subject || t.name)}</span>
                <span class="mpt-project">${project}${rank}</span>
                <span class="mpt-meta">${priority}${overdue}<span class="mpt-due">${due}</span><span class="mpt-id">${sf.esc(t.name)}</span></span>
            </a>`;
    }

    function startApp(container, sf, ticket) {
        const body = container.querySelector("#mpt-body");
        const count = container.querySelector("#mpt-count");
        const refresh = container.querySelector("#mpt-refresh");
        const more = container.querySelector("#mpt-more");
        let rows = [];
        let shown = 0;

        function draw() {
            body.innerHTML = rows
                .slice(0, shown)
                .map((t) => card(sf, t))
                .join("");
            more.hidden = shown >= rows.length;
            more.textContent = __("Show more ({0} left)", [rows.length - shown]);
        }

        function render(message) {
            rows = Array.isArray(message) ? message : [];
            count.textContent = rows.length ? __("{0} open", [rows.length]) : "";
            if (!rows.length) {
                more.hidden = true;
                sf.state.render(body, "empty", { title: __("No open tasks are assigned to you.") });
                return;
            }
            shown = Math.min(BATCH, rows.length);
            draw();
        }

        // The refresh button and a return to the workspace can both ask while an answer is
        // on its way, so each ask takes a ticket on the root (blocks.ticket) and only the
        // newest answer draws, into the root it was for.
        function load() {
            const current = ticket(container);
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => {
                    if (current()) render(r.message);
                })
                .catch((err) => {
                    if (!current()) return;
                    const title = __("Could not load your tasks.");
                    more.hidden = true;
                    if (err && err.status === 403) sf.state.render(body, "no-access");
                    else sf.state.render(body, "error", { title, on_action: load });
                })
                .then(() => {
                    if (current()) refresh.disabled = false;
                });
        }

        // A task opens in place. A plain link would load the whole Desk again (a click inside
        // a shadow root never reaches the router's own link handler); a modified click still
        // opens a new tab the browser's way.
        body.addEventListener("click", (ev) => {
            const link = ev.target && ev.target.closest ? ev.target.closest("a.mpt-task") : null;
            if (!link || ev.button || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
            ev.preventDefault();
            frappe.set_route("Form", "Task", link.getAttribute("data-task"));
        });
        more.addEventListener("click", () => {
            shown = Math.min(shown + BATCH, rows.length);
            draw();
        });
        refresh.addEventListener("click", load);
        sf.state.render(body, "loading");
        load();
        // Coming back to this workspace runs nothing again (the header), so the shared
        // helper reloads the block then. A bundle cached from before the helper has no
        // helper, and the block still draws; it just keeps its first answer.
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.onWorkspaceReturn) blocks.onWorkspaceReturn(container, load);
    }

    waitForDOM();
})();
