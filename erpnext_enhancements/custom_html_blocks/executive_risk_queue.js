// Risk Queue — Executive Dashboard Custom HTML Block.
//
// What is red company-wide — failed or stale snapshot runs first, then Bad
// KPIs, from erpnext_enhancements.api.executive_dashboard.get_risk_queue.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

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

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load the risk queue."));
                })
                .then(() => {
                    refresh.disabled = false;
                });
        }

        refresh.addEventListener("click", load);
        load();
    }

    waitForDOM();
})();
