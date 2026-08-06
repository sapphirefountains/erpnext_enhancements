// Build WIP & Aging — Production Dashboard Custom HTML Block.
//
// Active builds, longest-running first, from
// erpnext_enhancements.api.production_dashboard.get_wip_aging.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.production_dashboard.get_wip_aging";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#pwa-body")) {
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
        return `<div class="pwa-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#pwa-body");
        const count = container.querySelector("#pwa-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Build WIP & Aging is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const projects = message.projects || [];
        count.textContent = projects.length ? __("{0} in flight", [projects.length]) : "";
        if (!projects.length) {
            body.innerHTML = muted(__("No build is in progress."));
            return;
        }

        body.innerHTML = projects
            .map((p) => {
                const pct = Math.max(0, Math.min(100, Number(p.percent) || 0));
                const fill = pct >= 80 ? "pwa-fill-good" : pct >= 40 ? "pwa-fill-warn" : "";
                const late = p.overdue_days
                    ? `<span class="pwa-pill pwa-pill-bad">${esc(__("{0}d late", [p.overdue_days]))}</span>`
                    : "";
                const meta = [p.customer, money(p.amount)].filter(Boolean).map(esc).join(" · ");
                return `
                    <div class="pwa-row">
                        <a class="pwa-name" href="/app/project/${encodeURIComponent(p.name)}">${esc(p.title)}</a>
                        ${late}
                        <span class="pwa-meta">${meta}</span>
                        <span class="pwa-bar" title="${esc(pct + "%")}"><span class="pwa-fill ${fill}" style="width:${pct}%"></span></span>
                        <span class="pwa-age">${esc(__("{0}d open", [p.age_days]))}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#pwa-body");
        const refresh = container.querySelector("#pwa-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load builds in progress."));
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
