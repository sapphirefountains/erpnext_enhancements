// Renewal Radar — Sales Dashboard Custom HTML Block.
//
// Active maintenance contracts ending inside the renewal horizon, soonest first,
// from erpnext_enhancements.api.sales_dashboard.get_renewal_radar.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.sales_dashboard.get_renewal_radar";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#srr-body")) {
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
        return `<div class="srr-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#srr-body");
        const count = container.querySelector("#srr-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Renewal Radar is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const contracts = message.contracts || [];
        count.textContent = contracts.length
            ? __("{0} in {1}d", [contracts.length, message.horizon_days])
            : "";
        if (!contracts.length) {
            body.innerHTML = muted(__("No contract ends in the next {0} days.", [message.horizon_days]));
            return;
        }

        body.innerHTML = contracts
            .map((c) => {
                // A non-renewal notice outranks auto-renew: it is the one flag that
                // means this contract ends unless somebody acts.
                const flag = c.non_renewing
                    ? `<span class="srr-pill srr-pill-bad">${__("Non-renewing")}</span>`
                    : c.auto_renew
                      ? `<span class="srr-pill srr-pill-good">${__("Auto-renew")}</span>`
                      : "";
                const meta = [c.customer, money(c.amount)].filter(Boolean).map(esc).join(" · ");
                const tone = c.days_left <= 30 ? "srr-bad" : c.days_left <= 60 ? "srr-warn" : "";
                return `
                    <div class="srr-row">
                        <a class="srr-name" href="/app/sapphire-maintenance-contract/${encodeURIComponent(c.name)}">${esc(c.name)}</a>
                        ${flag}
                        <span class="srr-meta">${meta}</span>
                        <span class="srr-age ${tone}">${esc(__("{0}d left", [c.days_left]))}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#srr-body");
        const refresh = container.querySelector("#srr-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load upcoming renewals."));
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
