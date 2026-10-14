// Speed to Lead — Sales Dashboard Custom HTML Block.
//
// Leads with no outbound Communication yet, oldest first, from
// erpnext_enhancements.api.sales_dashboard.get_speed_to_lead.
//
// Shadow-DOM sandbox: `root_element` is the shadow root. The workspace runs this
// whole script again, with a fresh root, only when it renders the page: the first
// visit, and coming back from a different workspace. Coming back from a form or a
// list does not (v16's Workspace.show() returns early on the workspace already
// shown), so startApp hands load() to the shared return helper,
// public/js/global_enhancements/workspace_block_return.js, which runs it again
// then. Nothing is cached across renders, and listeners are bound to the fresh DOM
// each time.

(function () {
    const MAX_ATTEMPTS = 50;
    let attempts = 0;

    // Hours unanswered before a lead reads as warm / cold. A working day is the
    // cold line: past that, the industry's speed-to-lead advantage is gone.
    const WARM_HOURS = 4;
    const COLD_HOURS = 24;

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
        if (container.querySelector("#stl-body")) {
            withKit(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function waitLabel(hours) {
        if (hours === null || hours === undefined) return "";
        if (hours < 1) return __("{0}m", [Math.max(1, Math.round(hours * 60))]);
        if (hours < 48) return __("{0}h", [Math.round(hours)]);
        return __("{0}d", [Math.round(hours / 24)]);
    }

    function waitClass(hours) {
        if (hours >= COLD_HOURS) return "stl-cold";
        if (hours >= WARM_HOURS) return "stl-warm";
        return "";
    }

    function render(container, sf, message) {
        const body = container.querySelector("#stl-body");
        const count = container.querySelector("#stl-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            sf.state.render(body, "empty", {
                title: __("Speed to Lead is turned off in ERPNext Enhancements Settings."),
            });
            return;
        }

        const leads = message.leads || [];
        count.textContent = leads.length ? __("{0} waiting", [leads.length]) : "";
        if (!leads.length) {
            sf.state.render(body, "empty", { title: __("Every lead has been contacted. Nice.") });
            return;
        }

        body.innerHTML = leads
            .map((l) => {
                const meta = [l.company, l.owner, l.source].filter(Boolean).map(sf.esc).join(" · ");
                return `
                    <div class="stl-row">
                        <a class="stl-name" href="/desk/lead/${encodeURIComponent(l.name)}">${sf.esc(l.title)}</a>
                        <span class="stl-meta">${meta}</span>
                        <span class="stl-age ${waitClass(l.hours_waiting)}">${sf.esc(waitLabel(l.hours_waiting))}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container, sf, ticket) {
        const body = container.querySelector("#stl-body");
        const refresh = container.querySelector("#stl-refresh");

        // The refresh button and a return to the workspace can both ask while an answer
        // is on its way, so each ask takes a ticket on the root (blocks.ticket) and only
        // the newest answer draws, into the root it was for.
        function load() {
            const current = ticket(container);
            refresh.disabled = true;
            frappe
                .call({ method: "erpnext_enhancements.api.sales_dashboard.get_speed_to_lead" })
                .then((r) => {
                    if (current()) render(container, sf, r.message);
                })
                .catch((err) => {
                    if (!current()) return;
                    const title = __("Could not load the lead queue.");
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
