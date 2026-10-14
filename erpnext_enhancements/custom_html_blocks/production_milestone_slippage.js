// Milestone Slippage — Production Dashboard Custom HTML Block.
//
// Pending project process steps past their due date, from
// erpnext_enhancements.api.production_dashboard.get_milestone_slippage.
//
// Shadow-DOM sandbox: `root_element` is the shadow root. The workspace runs this
// whole script again, with a fresh root, only when it renders the page: the first
// visit, and coming back from a different workspace. Coming back from a form or a
// list does not (v16's Workspace.show() returns early on the workspace already
// shown), so startApp hands load() to the shared return helper,
// public/js/global_enhancements/workspace_block_return.js, which runs it again
// then. Nothing is cached across renders, and the refresh listener is bound to the
// fresh DOM each time.

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.production_dashboard.get_milestone_slippage";
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
        if (container.querySelector("#pms-body")) {
            withKit(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function money(value) {
        if (value === null || value === undefined || value === "") return "";
        try {
            return frappe.format(value, { fieldtype: "Currency" });
        } catch (e) {
            return String(value);
        }
    }

    function num(value, decimals) {
        if (value === null || value === undefined || value === "") return "—";
        return Number(value).toLocaleString(undefined, {
            minimumFractionDigits: decimals || 0,
            maximumFractionDigits: decimals || 0,
        });
    }

    function render(container, sf, message) {
        const body = container.querySelector("#pms-body");
        const count = container.querySelector("#pms-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            sf.state.render(body, "empty", {
                title: __("Milestone Slippage is turned off in ERPNext Enhancements Settings."),
            });
            return;
        }
        const steps = message.steps || [];
        count.textContent = steps.length ? __("{0} overdue", [steps.length]) : "";
        if (!steps.length) {
            sf.state.render(body, "empty", { title: __("No milestone is overdue.") });
            return;
        }

        body.innerHTML = steps
            .map((s) => {
                const tone = s.overdue_days >= 14 ? "danger" : "warn";
                const meta = [s.project_title, s.customer, s.role].filter(Boolean).map(sf.esc).join(" · ");
                return `
                    <div class="pms-row">
                        <a class="pms-name" href="/desk/project/${encodeURIComponent(s.project)}">${sf.esc(s.title)}</a>
                        ${sf.chip(__("{0}d late", [s.overdue_days]), tone)}
                        <span class="pms-meta">${meta}</span>
                        <span class="pms-age">${sf.esc(s.due_by || "")}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container, sf, ticket) {
        const body = container.querySelector("#pms-body");
        const refresh = container.querySelector("#pms-refresh");

        // The refresh button and a return to the workspace can both ask while an answer
        // is on its way, so each ask takes a ticket on the root (blocks.ticket) and only
        // the newest answer draws, into the root it was for.
        function load() {
            const current = ticket(container);
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => {
                    if (current()) render(container, sf, r.message);
                })
                .catch((err) => {
                    if (!current()) return;
                    const title = __("Could not load overdue milestones.");
                    if (err && err.status === 403) sf.state.render(body, "no-access");
                    else sf.state.render(body, "error", { title, on_action: load });
                })
                .then(() => {
                    if (current()) refresh.disabled = false;
                });
        }

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
