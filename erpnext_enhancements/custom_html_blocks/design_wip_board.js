// Design WIP — Design Dashboard Custom HTML Block.
//
// In-flight Water Feature Designs with age and completion, from
// erpnext_enhancements.api.design_dashboard.get_wip_board.
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
    const METHOD = "erpnext_enhancements.api.design_dashboard.get_wip_board";
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
        if (container.querySelector("#dwb-body")) {
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
        const body = container.querySelector("#dwb-body");
        const count = container.querySelector("#dwb-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            sf.state.render(body, "empty", {
                title: __("Design WIP is turned off in ERPNext Enhancements Settings."),
            });
            return;
        }
        const designs = message.designs || [];
        const by = message.by_status || {};
        count.textContent = (message.statuses || [])
            .map((s) => `${s} ${by[s] || 0}`)
            .join(" · ");

        if (!designs.length) {
            sf.state.render(body, "empty", { title: __("Nothing is in flight.") });
            return;
        }

        body.innerHTML = designs
            .map((d) => {
                const pct = Math.max(0, Math.min(100, Number(d.percent) || 0));
                const fill = pct >= 80 ? "dwb-fill-good" : pct >= 40 ? "dwb-fill-warn" : "";
                const blockers = d.blockers
                    ? sf.chip(__("{0} blocked", [d.blockers]), "danger")
                    : "";
                const meta = [d.customer, d.status].filter(Boolean).map(sf.esc).join(" · ");
                // Age is time in the pipeline; a long-lived design is the point of the board.
                const tone = d.age_days >= 60 ? "dwb-bad" : d.age_days >= 30 ? "dwb-warn" : "";
                return `
                    <div class="dwb-row">
                        <a class="dwb-name" href="/desk/water-feature-design/${encodeURIComponent(d.name)}">${sf.esc(d.title)}</a>
                        ${blockers}
                        <span class="dwb-meta">${meta}</span>
                        <span class="dwb-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" aria-label="${sf.esc(__("Progress"))}" title="${pct}%"><span class="dwb-fill ${fill}" style="width:${pct}%"></span></span>
                        <span class="dwb-age ${tone}">${sf.esc(__("{0}d", [d.age_days]))}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container, sf, ticket) {
        const body = container.querySelector("#dwb-body");
        const refresh = container.querySelector("#dwb-refresh");

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
                    const title = __("Could not load the design board.");
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
