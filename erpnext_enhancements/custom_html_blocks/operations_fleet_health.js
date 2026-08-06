// Fleet & Device Health — Operations Dashboard Custom HTML Block.
//
// Managed-device compliance counts plus the devices out of contact, from
// erpnext_enhancements.api.operations_dashboard.get_fleet_health.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.operations_dashboard.get_fleet_health";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#ofh-body")) {
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
        return `<div class="ofh-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#ofh-body");
        const count = container.querySelector("#ofh-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Fleet & Device Health is turned off in ERPNext Enhancements Settings."));
            return;
        }
        if (message.unavailable) {
            count.textContent = "";
            body.innerHTML = muted(esc(message.unavailable));
            return;
        }

        const c = message.counts || {};
        const pct = message.compliant_pct;
        count.textContent = message.total ? __("{0} devices", [message.total]) : "";

        const tiles = `
            <div class="ofh-tiles">
                <div class="ofh-tile">
                    <span class="ofh-tile-label">${__("Compliant")}</span>
                    <span class="ofh-tile-value">${pct === null || pct === undefined ? "—" : num(pct, 0) + "%"}</span>
                    <span class="ofh-tile-note">${esc(__("{0} of {1}", [c.compliant || 0, message.total || 0]))}</span>
                </div>
                <div class="ofh-tile">
                    <span class="ofh-tile-label">${__("Non-compliant")}</span>
                    <span class="ofh-tile-value ${c.non_compliant ? "ofh-bad" : ""}">${num(c.non_compliant || 0)}</span>
                </div>
                <div class="ofh-tile">
                    <span class="ofh-tile-label">${__("Never checked in")}</span>
                    <span class="ofh-tile-value ${message.never_seen ? "ofh-warn" : ""}">${num(message.never_seen || 0)}</span>
                </div>
            </div>`;

        const stale = message.stale || [];
        const list = stale.length
            ? `<div class="ofh-group">${__("Silent for {0}+ days", [message.stale_days])}</div>` +
              stale
                  .map(
                      (d) => `
                    <div class="ofh-row">
                        <a class="ofh-name" href="/app/managed-device/${encodeURIComponent(d.name)}">${esc(d.title)}</a>
                        <span class="ofh-meta">${esc(d.compliance)}</span>
                        <span class="ofh-age ofh-warn">${esc(__("{0}d", [d.days]))}</span>
                    </div>`
                  )
                  .join("")
            : "";

        body.innerHTML = tiles + list;
    }

    function startApp(container) {
        const body = container.querySelector("#ofh-body");
        const refresh = container.querySelector("#ofh-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load device health."));
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
