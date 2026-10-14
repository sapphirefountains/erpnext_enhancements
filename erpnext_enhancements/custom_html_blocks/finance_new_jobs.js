// New Jobs Queue — Finance Dashboard Custom HTML Block.
//
// Renders the most recently created Active Projects from
// erpnext_enhancements.api.finance_dashboard.get_new_jobs.
//
// Shadow-DOM sandbox: `root_element` is the shadow root. The workspace runs this
// whole script again, with a fresh root, only when it renders the page: the first
// visit, and coming back from a different workspace. Coming back from a form or a
// list does not (v16's Workspace.show() returns early on the workspace already
// shown), so startApp hands load() to the shared return helper,
// public/js/global_enhancements/workspace_block_return.js, which runs it again
// then. Listeners are bound to the fresh DOM each time.

(function () {
    const MAX_ATTEMPTS = 50;
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
        if (container.querySelector("#fnj-body")) {
            withKit(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function ageLabel(days) {
        if (days === null || days === undefined) return "";
        if (days <= 0) return __("today");
        if (days === 1) return __("1 day ago");
        return __("{0} days ago", [days]);
    }

    function render(body, sf, message) {
        if (!message || message.enabled === false) {
            sf.state.render(body, "empty", {
                title: __("New Jobs Queue is turned off in ERPNext Enhancements Settings."),
            });
            return;
        }
        const jobs = message.jobs || [];
        if (!jobs.length) {
            sf.state.render(body, "empty", { title: __("No active jobs.") });
            return;
        }
        body.innerHTML = jobs
            .map((j) => {
                const opp = j.opportunity
                    ? `<a class="fnj-opp" href="/desk/opportunity/${encodeURIComponent(j.opportunity)}">${sf.esc(j.opportunity)}</a>`
                    : "";
                const meta = [sf.esc(j.customer || ""), sf.esc(j.owner || ""), sf.esc(ageLabel(j.age_days))]
                    .filter(Boolean)
                    .join(" · ");
                return `
                    <div class="fnj-item">
                        <a class="fnj-name" href="/desk/project/${encodeURIComponent(j.name)}">${sf.esc(j.project_name)}</a>
                        <div class="fnj-meta">${meta}</div>
                        ${opp ? `<div class="fnj-meta">${__("from")} ${opp}</div>` : ""}
                    </div>`;
            })
            .join("");
    }

    function startApp(container, sf, ticket) {
        const body = container.querySelector("#fnj-body");
        const refresh = container.querySelector("#fnj-refresh");

        // The refresh button and a return to the workspace can both ask while an answer
        // is on its way, so each ask takes a ticket on the root (blocks.ticket) and only
        // the newest answer draws, into the root it was for.
        function load() {
            const current = ticket(container);
            sf.state.render(body, "loading");
            refresh.disabled = true;
            frappe
                .call({ method: "erpnext_enhancements.api.finance_dashboard.get_new_jobs" })
                .then((r) => {
                    if (current()) render(body, sf, r.message);
                })
                .catch((err) => {
                    if (!current()) return;
                    const title = __("Could not load new jobs.");
                    if (err && err.status === 403) sf.state.render(body, "no-access");
                    else sf.state.render(body, "error", { title, on_action: load });
                })
                .then(() => {
                    if (current()) refresh.disabled = false;
                });
        }

        refresh.addEventListener("click", load);
        load();
        // Coming back to this workspace runs nothing again (the header), so the shared
        // helper reloads the block then. A bundle cached from before the helper has no
        // helper, and the block still draws; it just keeps its first answer.
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.onWorkspaceReturn) blocks.onWorkspaceReturn(container, load);
    }

    waitForDOM();
})();
