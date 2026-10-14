// Hours: Budget vs Actual — Production Dashboard Custom HTML Block.
//
// Active builds ranked by how far actual hours have run past budget, from
// erpnext_enhancements.api.production_dashboard.get_hours_variance.
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
    const METHOD = "erpnext_enhancements.api.production_dashboard.get_hours_variance";
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
        if (container.querySelector("#phv-body")) {
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
        const body = container.querySelector("#phv-body");
        const count = container.querySelector("#phv-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            sf.state.render(body, "empty", {
                title: __("Hours: Budget vs Actual is turned off in ERPNext Enhancements Settings."),
            });
            return;
        }
        if (message.unavailable) {
            count.textContent = "";
            sf.state.render(body, "empty", { title: message.unavailable });
            return;
        }

        const projects = message.projects || [];
        const over = projects.filter((p) => p.over).length;
        count.textContent = projects.length ? __("{0} over budget", [over]) : "";
        if (!projects.length) {
            sf.state.render(body, "empty", { title: __("No active build has an hours budget recorded.") });
            return;
        }

        body.innerHTML = projects
            .map((p) => {
                // The bar fills to the budget; past 100% it stays full and the number
                // carries the overrun, so a 400% job doesn't render a bar four times wide.
                const width = Math.max(0, Math.min(100, Number(p.used_pct) || 0));
                const fill = p.over ? "phv-fill-bad" : p.used_pct >= 80 ? "phv-fill-warn" : "phv-fill-good";
                const tone = p.over ? "phv-bad" : p.used_pct >= 80 ? "phv-warn" : "";
                const meta = [
                    p.customer,
                    __("{0} of {1} h", [num(p.actual_hours, 0), num(p.budget_hours, 0)]),
                ]
                    .filter(Boolean)
                    .map(sf.esc)
                    .join(" · ");
                return `
                    <div class="phv-row">
                        <a class="phv-name" href="/desk/project/${encodeURIComponent(p.name)}">${sf.esc(p.title)}</a>
                        <span class="phv-meta">${meta}</span>
                        <span class="phv-bar"><span class="phv-fill ${fill}" style="width:${width}%"></span></span>
                        <span class="phv-age ${tone}">${sf.esc(num(p.used_pct, 0) + "%")}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container, sf, ticket) {
        const body = container.querySelector("#phv-body");
        const refresh = container.querySelector("#phv-refresh");

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
                    const title = __("Could not load hours against budget.");
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
