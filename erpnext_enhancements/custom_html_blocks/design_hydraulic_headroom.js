// Hydraulic Headroom — Design Dashboard Custom HTML Block.
//
// Issued designs ranked by flow margin, thinnest first, from
// erpnext_enhancements.api.design_dashboard.get_hydraulic_headroom.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.design_dashboard.get_hydraulic_headroom";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#dhh-body")) {
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
        return `<div class="dhh-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#dhh-body");
        const count = container.querySelector("#dhh-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Hydraulic Headroom is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const designs = message.designs || [];
        count.textContent = designs.length ? __("thinnest margins first") : "";
        if (!designs.length) {
            body.innerHTML = muted(__("No issued design has both a circulation requirement and a design flow recorded."));
            return;
        }

        body.innerHTML = designs
            .map((d) => {
                // The bar shows margin against a 50% "comfortable" ceiling, so a thin
                // selection reads as a short bar rather than needing the number read.
                const margin = d.margin_pct;
                const width = margin === null ? 0 : Math.max(0, Math.min(100, (margin / 50) * 100));
                const fill = d.under ? "dhh-fill-bad" : d.thin ? "dhh-fill-warn" : "dhh-fill-good";
                const tone = d.under ? "dhh-bad" : d.thin ? "dhh-warn" : "dhh-good";
                const label = margin === null ? "—" : `${margin > 0 ? "+" : ""}${num(margin, 1)}%`;
                const meta = [
                    d.pump,
                    __("{0} of {1} GPM", [num(d.design_gpm, 0), num(d.required_gpm, 0)]),
                ]
                    .filter(Boolean)
                    .map(esc)
                    .join(" · ");
                return `
                    <div class="dhh-row">
                        <a class="dhh-name" href="/app/water-feature-design/${encodeURIComponent(d.name)}">${esc(d.title)}</a>
                        <span class="dhh-meta">${meta}</span>
                        <span class="dhh-bar"><span class="dhh-fill ${fill}" style="width:${width}%"></span></span>
                        <span class="dhh-age ${tone}">${esc(label)}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#dhh-body");
        const refresh = container.querySelector("#dhh-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load hydraulic margins."));
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
