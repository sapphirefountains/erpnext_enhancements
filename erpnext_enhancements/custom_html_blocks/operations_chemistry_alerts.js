// Chemistry Alerts — Operations Dashboard Custom HTML Block.
//
// Recent visits flagged with out-of-range chemistry readings, from
// erpnext_enhancements.api.operations_dashboard.get_chemistry_alerts.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.operations_dashboard.get_chemistry_alerts";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#oca-body")) {
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
        return `<div class="oca-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#oca-body");
        const count = container.querySelector("#oca-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Chemistry Alerts is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const visits = message.visits || [];
        count.textContent = visits.length ? __("{0} in {1}d", [visits.length, message.lookback_days]) : "";
        if (!visits.length) {
            body.innerHTML = muted(__("No out-of-range readings in the last {0} days.", [message.lookback_days]));
            return;
        }

        body.innerHTML = visits
            .map((v) => {
                // An alert on a visit still open is actionable now; one on a closed
                // visit is a follow-up. Both are shown, flagged differently.
                const flag = v.open
                    ? `<span class="oca-pill oca-pill-warn">${__("Open")}</span>`
                    : `<span class="oca-pill">${esc(v.state)}</span>`;
                const meta = [v.customer, v.technician].filter(Boolean).map(esc).join(" · ");
                return `
                    <div class="oca-row">
                        <a class="oca-name" href="/app/sapphire-maintenance-record/${encodeURIComponent(v.name)}">${esc(v.title)}</a>
                        ${flag}
                        <span class="oca-meta">${meta}</span>
                        <span class="oca-age">${esc(v.visit_date || "")}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#oca-body");
        const refresh = container.querySelector("#oca-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load chemistry alerts."));
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
