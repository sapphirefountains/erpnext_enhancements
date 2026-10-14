// Who's Working — Finance Dashboard Custom HTML Block.
//
// Renders employees currently clocked in (open/paused Job Intervals) from
// erpnext_enhancements.api.finance_dashboard.get_whos_working. Auto-refreshes so
// the elapsed times stay live; the interval is stored on `window` and cleared on
// re-run, so the workspace rendering the page again never stacks timers. It only
// fetches while the block is on screen (onScreen): leaving the workspace hides or
// detaches the block but not its timer, which used to poll all day.
//
// Shadow-DOM sandbox: `root_element` is the shadow root. The workspace runs this
// whole script again, with a fresh root, only when it renders the page: the first
// visit, and coming back from a different workspace. Coming back from a form or a
// list does not (v16's Workspace.show() returns early on the workspace already
// shown), so startApp also hands load() to the shared return helper,
// public/js/global_enhancements/workspace_block_return.js, which runs it again
// then, rather than leaving the list up to a minute old.

(function () {
    const MAX_ATTEMPTS = 50;
    const REFRESH_MS = 60000;
    const TICK_MS = 15000;
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
        const plain = (el, kind, opts) => (el.textContent = (opts && opts.title) || kind);
        const escape = frappe.utils.escape_html;
        startApp(container, { esc: escape, chip: escape, state: { render: plain } }, () => () => true);
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#fww-body")) {
            withKit(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    // Is this render's block actually on screen? The shared helper asks the shadow root's
    // host: detached, or no client rects under a display:none ancestor (a hidden workspace
    // page). A device whose bundle predates the helper fails open (true): polling as before.
    function onScreen(container) {
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (!blocks || typeof blocks.onScreen !== "function") return true;
        return blocks.onScreen(container);
    }

    function render(body, sf, message) {
        if (!message || message.enabled === false) {
            sf.state.render(body, "empty", {
                title: __("Who's Working is turned off in ERPNext Enhancements Settings."),
            });
            return;
        }
        const workers = message.workers || [];
        if (!workers.length) {
            sf.state.render(body, "empty", { title: __("Nobody is clocked in right now.") });
            return;
        }
        body.innerHTML = workers
            .map((w) => {
                const where = w.task_subject || w.project_title || "";
                const paused =
                    w.status === "Paused" ? `<span class="fww-paused">${__("paused")}</span>` : "";
                return `
                    <div class="fww-item">
                        <div class="fww-line">
                            <span class="fww-name">${sf.esc(w.employee_name || "")}</span>
                            <span class="fww-elapsed">${sf.esc(w.elapsed_label || "")}${paused ? " " : ""}${paused}</span>
                        </div>
                        ${where ? `<div class="fww-where">${sf.esc(where)}</div>` : ""}
                    </div>`;
            })
            .join("");
    }

    function startApp(container, sf, ticket) {
        const state = (window.__ee_fww = window.__ee_fww || {});
        if (state.timer) {
            clearInterval(state.timer);
            state.timer = null;
        }

        const body = container.querySelector("#fww-body");
        const refresh = container.querySelector("#fww-refresh");

        // The refresh button, the timer and a return to the workspace can all ask while
        // an answer is on its way, so each ask takes a ticket on the root (blocks.ticket)
        // and only the newest draws.
        function load() {
            state.stale = false;
            state.last = Date.now();
            const current = ticket(container);
            refresh.disabled = true;
            frappe
                .call({ method: "erpnext_enhancements.api.finance_dashboard.get_whos_working" })
                .then((r) => {
                    if (current()) render(body, sf, r.message);
                })
                .catch((err) => {
                    if (!current()) return;
                    const title = __("Could not load time-clock status.");
                    if (err && err.status === 403) sf.state.render(body, "no-access");
                    else sf.state.render(body, "error", { title, on_action: load });
                })
                .then(() => {
                    if (current()) refresh.disabled = false;
                });
        }

        // A short heartbeat rather than one 60 s interval: off screen it only notes that a
        // refresh is owed; on screen it loads when the minute is up or one is owed, so a
        // block that comes back some other way catches up within TICK_MS.
        function tick() {
            if (document.hidden || !onScreen(container)) {
                state.stale = true;
                return;
            }
            if (state.stale || Date.now() - (state.last || 0) >= REFRESH_MS) load();
        }

        refresh.addEventListener("click", load);
        sf.state.render(body, "loading");
        load();
        state.timer = setInterval(tick, TICK_MS);
        // The return helper runs the same load at once on an ordinary return and owns the
        // only router handler; the heartbeat above is untouched by it.
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.onWorkspaceReturn) blocks.onWorkspaceReturn(container, load);
    }

    waitForDOM();
})();
