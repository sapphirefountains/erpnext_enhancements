// Design WIP — Design Dashboard Custom HTML Block.
//
// In-flight Water Feature Designs with age and completion, from
// erpnext_enhancements.api.design_dashboard.get_wip_board.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.design_dashboard.get_wip_board";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#dwb-body")) {
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
        return `<div class="dwb-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#dwb-body");
        const count = container.querySelector("#dwb-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Design WIP is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const designs = message.designs || [];
        const by = message.by_status || {};
        count.textContent = (message.statuses || [])
            .map((s) => `${s} ${by[s] || 0}`)
            .join(" · ");

        if (!designs.length) {
            body.innerHTML = muted(__("Nothing is in flight."));
            return;
        }

        body.innerHTML = designs
            .map((d) => {
                const pct = Math.max(0, Math.min(100, Number(d.percent) || 0));
                const fill = pct >= 80 ? "dwb-fill-good" : pct >= 40 ? "dwb-fill-warn" : "";
                const blockers = d.blockers
                    ? `<span class="dwb-pill dwb-pill-bad">${esc(__("{0} blocked", [d.blockers]))}</span>`
                    : "";
                const meta = [d.customer, d.status].filter(Boolean).map(esc).join(" · ");
                // Age is time in the pipeline; a long-lived design is the point of the board.
                const tone = d.age_days >= 60 ? "dwb-bad" : d.age_days >= 30 ? "dwb-warn" : "";
                return `
                    <div class="dwb-row">
                        <a class="dwb-name" href="/app/water-feature-design/${encodeURIComponent(d.name)}">${esc(d.title)}</a>
                        ${blockers}
                        <span class="dwb-meta">${meta}</span>
                        <span class="dwb-bar" title="${esc(pct + "%")}"><span class="dwb-fill ${fill}" style="width:${pct}%"></span></span>
                        <span class="dwb-age ${tone}">${esc(__("{0}d", [d.age_days]))}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#dwb-body");
        const refresh = container.querySelector("#dwb-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load the design board."));
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
