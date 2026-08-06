// Material Readiness — Production Dashboard Custom HTML Block.
//
// Submitted material requests on live projects that have not fully landed, from
// erpnext_enhancements.api.production_dashboard.get_material_readiness.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.production_dashboard.get_material_readiness";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#pmr-body")) {
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
        return `<div class="pmr-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#pmr-body");
        const count = container.querySelector("#pmr-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Material Readiness is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const requests = message.requests || [];
        count.textContent = requests.length ? __("{0} outstanding", [requests.length]) : "";
        if (!requests.length) {
            body.innerHTML = muted(__("Every material request on a live project has landed."));
            return;
        }

        body.innerHTML = requests
            .map((r) => {
                const late = r.late_days
                    ? `<span class="pmr-pill pmr-pill-bad">${esc(__("{0}d late", [r.late_days]))}</span>`
                    : `<span class="pmr-pill">${esc(r.status)}</span>`;
                const meta = [
                    r.project_title,
                    __("{0} of {1} lines open", [r.open_lines, r.line_count]),
                ]
                    .filter(Boolean)
                    .map(esc)
                    .join(" · ");
                return `
                    <div class="pmr-row">
                        <a class="pmr-name" href="/app/material-request/${encodeURIComponent(r.name)}">${esc(r.name)}</a>
                        ${late}
                        <span class="pmr-meta">${meta}</span>
                        <span class="pmr-age">${esc(r.needed_by || "")}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#pmr-body");
        const refresh = container.querySelector("#pmr-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load material requests."));
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
