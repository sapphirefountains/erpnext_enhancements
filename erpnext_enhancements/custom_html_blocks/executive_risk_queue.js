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

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#erq-body")) {
            startApp(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function esc(value) {
        return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
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

    function muted(text) {
        return `<div class="erq-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#erq-body");
        const count = container.querySelector("#erq-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Risk Queue is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const risks = message.risks || [];
        const bad = message.bad || [];
        count.textContent = message.bad_total ? __("{0} red KPIs", [message.bad_total]) : "";

        if (!risks.length && !bad.length) {
            body.innerHTML = muted(__("Nothing is red and every department reported overnight."));
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
                        <span class="erq-name">${esc(r.department)}</span>
                        <span class="erq-pill erq-pill-bad">${esc(KIND_LABEL[r.kind] || r.kind)}</span>
                        <span class="erq-meta">${esc(r.detail)}</span>
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
                        <span class="erq-name">${esc(b.label)}</span>
                        <span class="erq-pill">${esc(b.department)}</span>
                        <span class="erq-meta">${esc(b.detail)}</span>
                        ${b.stale ? `<span class="erq-age erq-warn">${__("stale")}</span>` : ""}
                    </div>`
                  )
                  .join("") +
              (message.bad_total > bad.length
                  ? muted(__("and {0} more", [message.bad_total - bad.length]))
                  : "")
            : "";

        body.innerHTML = runs + kpis;
    }

    function startApp(container) {
        const body = container.querySelector("#erq-body");
        const refresh = container.querySelector("#erq-refresh");

        // The refresh button and a return to the workspace can both ask while an answer
        // is on its way, so each ask takes a ticket and only the newest one draws. The
        // ticket lives on the root: an answer only ever draws into the root it was for.
        function load() {
            const ticket = (container.__ee_ticket = (container.__ee_ticket || 0) + 1);
            const current = () => container.__ee_ticket === ticket;
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => {
                    if (current()) render(container, r.message);
                })
                .catch(() => {
                    if (current()) body.innerHTML = muted(__("Could not load the risk queue."));
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
