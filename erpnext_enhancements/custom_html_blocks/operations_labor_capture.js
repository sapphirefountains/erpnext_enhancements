// Labor Capture Gaps — Operations Dashboard Custom HTML Block.
//
// Completed visits that closed with no labour captured, from
// erpnext_enhancements.api.operations_dashboard.get_labor_capture.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.operations_dashboard.get_labor_capture";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#olc-body")) {
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
        return `<div class="olc-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#olc-body");
        const count = container.querySelector("#olc-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Labor Capture Gaps is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const visits = message.visits || [];
        count.textContent = visits.length ? __("{0} in {1}d", [visits.length, message.lookback_days]) : "";
        if (!visits.length) {
            body.innerHTML = muted(__("Every completed visit carries labour. Nothing is leaking."));
            return;
        }

        body.innerHTML = visits
            .map((v) => {
                const meta = [v.customer, v.technician].filter(Boolean).map(esc).join(" · ");
                return `
                    <div class="olc-row">
                        <a class="olc-name" href="/app/sapphire-maintenance-record/${encodeURIComponent(v.name)}">${esc(v.title)}</a>
                        <span class="olc-meta">${meta}</span>
                        <span class="olc-age">${esc(v.visit_date || "")}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#olc-body");
        const refresh = container.querySelector("#olc-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load labour gaps."));
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
