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

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#fww-body")) {
            startApp(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    // Is this render's block actually on screen? `root_element` is a shadow root, so ask
    // its host: isConnected is false once detached, and getClientRects() is empty under
    // any display:none ancestor (a hidden workspace page). Fails open without a host.
    function onScreen(container) {
        const host = container && container.host;
        // No host, or one without layout APIs (a test double), fails open: polling as before.
        if (!host || typeof host.getClientRects !== "function") return true;
        return Boolean(host.isConnected && host.getClientRects().length > 0);
    }

    function render(body, message) {
        const esc = frappe.utils.escape_html;
        if (!message || message.enabled === false) {
            body.innerHTML = `<div class="fww-muted">${__("Who's Working is turned off in ERPNext Enhancements Settings.")}</div>`;
            return;
        }
        const workers = message.workers || [];
        if (!workers.length) {
            body.innerHTML = `<div class="fww-muted">${__("Nobody is clocked in right now.")}</div>`;
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
                            <span class="fww-name">${esc(w.employee_name || "")}</span>
                            <span class="fww-elapsed">${esc(w.elapsed_label || "")}${paused ? " " : ""}${paused}</span>
                        </div>
                        ${where ? `<div class="fww-where">${esc(where)}</div>` : ""}
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const state = (window.__ee_fww = window.__ee_fww || {});
        if (state.timer) {
            clearInterval(state.timer);
            state.timer = null;
        }

        const body = container.querySelector("#fww-body");
        const refresh = container.querySelector("#fww-refresh");

        // The refresh button, the timer and a return to the workspace can all ask while
        // an answer is on its way, so each ask takes a ticket and only the newest draws.
        function load() {
            state.stale = false;
            state.last = Date.now();
            const ticket = (container.__ee_ticket = (container.__ee_ticket || 0) + 1);
            const current = () => container.__ee_ticket === ticket;
            refresh.disabled = true;
            frappe
                .call({ method: "erpnext_enhancements.api.finance_dashboard.get_whos_working" })
                .then((r) => {
                    if (current()) render(body, r.message);
                })
                .catch(() => {
                    if (!current()) return;
                    body.innerHTML = `<div class="fww-muted">${__("Could not load time-clock status.")}</div>`;
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
        load();
        state.timer = setInterval(tick, TICK_MS);
        // The return helper runs the same load at once on an ordinary return and owns the
        // only router handler; the heartbeat above is untouched by it.
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.onWorkspaceReturn) blocks.onWorkspaceReturn(container, load);
    }

    waitForDOM();
})();
