// Funnel Cascade — Marketing Dashboard Custom HTML Block.
//
// Sessions to leads to opportunities to won, with the rate at each step, from
// erpnext_enhancements.api.marketing_dashboard.get_funnel_cascade.
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
    const METHOD = "erpnext_enhancements.api.marketing_dashboard.get_funnel_cascade";
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
        if (container.querySelector("#mfc-body")) {
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
        const body = container.querySelector("#mfc-body");
        const count = container.querySelector("#mfc-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            sf.state.render(body, "empty", {
                title: __("Funnel Cascade is turned off in ERPNext Enhancements Settings."),
            });
            return;
        }
        const w = message.last_30 || {};
        const w90 = message.last_90 || {};
        count.textContent = __("last 30 days");

        function tile(label, value, note) {
            return `
                <div class="mfc-tile">
                    <span class="mfc-tile-label">${sf.esc(label)}</span>
                    <span class="mfc-tile-value">${value}</span>
                    <span class="mfc-tile-note">${sf.esc(note || "")}</span>
                </div>`;
        }

        function pct(value) {
            return value === null || value === undefined ? "—" : num(value, 1) + "%";
        }

        // A blank sessions step means the GA4 pull failed, which is not the same as
        // zero traffic — so it renders as an em dash with a note, never as 0.
        const sessions = w.sessions === null || w.sessions === undefined ? "—" : num(w.sessions);
        const sessionNote = w.sessions === null || w.sessions === undefined ? __("GA4 pull unavailable") : "";

        body.innerHTML = `
            <div class="mfc-tiles">
                ${tile(__("Sessions"), sessions, sessionNote)}
                ${tile(__("Leads"), num(w.leads), pct(w.session_to_lead_pct) + " " + __("of sessions"))}
                ${tile(__("Converted"), num(w.converted), pct(w.lead_to_opp_pct) + " " + __("of leads"))}
                ${tile(__("Won"), num(w.won), pct(w.opp_to_won_pct) + " " + __("of opportunities"))}
            </div>
            <div class="mfc-group">${__("Last 90 days")}</div>
            <div class="mfc-row">
                <span class="mfc-meta">${sf.esc(__("{0} leads · {1} opportunities · {2} won", [num(w90.leads), num(w90.opportunities), num(w90.won)]))}</span>
                <span class="mfc-age">${sf.esc(__("win rate {0}", [pct(w90.opp_to_won_pct)]))}</span>
            </div>`;
    }

    function startApp(container, sf, ticket) {
        const body = container.querySelector("#mfc-body");
        const refresh = container.querySelector("#mfc-refresh");

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
                    const title = __("Could not load the funnel.");
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
