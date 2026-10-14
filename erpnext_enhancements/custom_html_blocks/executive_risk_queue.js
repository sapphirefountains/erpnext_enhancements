// Risk Queue — Executive Dashboard Custom HTML Block.
//
// What is red company-wide — failed or stale snapshot runs first, then Bad
// KPIs, from erpnext_enhancements.api.executive_dashboard.get_risk_queue.
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
    const METHOD = "erpnext_enhancements.api.executive_dashboard.get_risk_queue";
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
        if (container.querySelector("#erq-body")) {
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
        const body = container.querySelector("#erq-body");
        const count = container.querySelector("#erq-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            sf.state.render(body, "empty", {
                title: __("Risk Queue is turned off in ERPNext Enhancements Settings."),
            });
            return;
        }
        const risks = message.risks || [];
        const bad = message.bad || [];
        count.textContent = message.bad_total ? __("{0} red KPIs", [message.bad_total]) : "";

        if (!risks.length && !bad.length) {
            sf.state.render(body, "empty", {
                title: __("Nothing is red and every department reported overnight."),
            });
            return;
        }

        const KIND_LABEL = {
            missing: __("No snapshot"),
            stale: __("Stale"),
            error: __("Run failed"),
        };

        // Snapshot problems first: a department whose run failed has no Bad KPIs at
        // all, and reading that silence as health is the mistake worth preventing.
        const runs = risks.length
            ? `<div class="erq-group">${__("Reporting")}</div>` +
              risks
                  .map(
                      (r) => `
                    <div class="erq-row">
                        <span class="erq-name">${sf.esc(r.department)}</span>
                        ${sf.chip(KIND_LABEL[r.kind] || r.kind, "danger")}
                        <span class="erq-meta">${sf.esc(r.detail)}</span>
                    </div>`
                  )
                  .join("")
            : "";

        const kpis = bad.length
            ? `<div class="erq-group">${__("Off target")}</div>` +
              bad
                  .map(
                      (b) => `
                    <div class="erq-row">
                        <span class="erq-name">${sf.esc(b.label)}</span>
                        ${sf.chip(b.department)}
                        <span class="erq-meta">${sf.esc(b.detail)}</span>
                        ${b.stale ? `<span class="erq-age erq-warn">${__("stale")}</span>` : ""}
                    </div>`
                  )
                  .join("") +
              (message.bad_total > bad.length
                  ? `<div class="erq-muted">${__("and {0} more", [message.bad_total - bad.length])}</div>`
                  : "")
            : "";

        body.innerHTML = runs + kpis;
    }

    function startApp(container, sf, ticket) {
        const body = container.querySelector("#erq-body");
        const refresh = container.querySelector("#erq-refresh");

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
                    const title = __("Could not load the risk queue.");
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
