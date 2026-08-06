// Hand-Off Readiness — Design Dashboard Custom HTML Block.
//
// Designs carrying blockers or warnings, blockers first, from
// erpnext_enhancements.api.design_dashboard.get_handoff_readiness.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.design_dashboard.get_handoff_readiness";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#dhr-body")) {
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
        return `<div class="dhr-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#dhr-body");
        const count = container.querySelector("#dhr-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Hand-Off Readiness is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const designs = message.designs || [];
        count.textContent = designs.length ? __("{0} with issues", [designs.length]) : "";
        if (!designs.length) {
            body.innerHTML = muted(__("No design is carrying a blocker or a warning."));
            return;
        }

        body.innerHTML = designs
            .map((d) => {
                const pills = [];
                if (d.blockers) {
                    pills.push(`<span class="dhr-pill dhr-pill-bad">${esc(__("{0} blockers", [d.blockers]))}</span>`);
                }
                if (d.warnings) {
                    pills.push(`<span class="dhr-pill dhr-pill-warn">${esc(__("{0} warnings", [d.warnings]))}</span>`);
                }
                const meta = [d.customer, d.summary].filter(Boolean).map(esc).join(" · ");
                return `
                    <div class="dhr-row">
                        <a class="dhr-name" href="/app/water-feature-design/${encodeURIComponent(d.name)}">${esc(d.title)}</a>
                        ${pills.join("")}
                        <span class="dhr-meta">${meta}</span>
                        <span class="dhr-age">${esc(d.status)}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#dhr-body");
        const refresh = container.querySelector("#dhr-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load hand-off readiness."));
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
