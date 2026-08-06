// Stalled Deals — Sales Dashboard Custom HTML Block.
//
// Open Opportunities with no edit for two weeks, quietest first, from
// erpnext_enhancements.api.sales_dashboard.get_stalled_deals.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.sales_dashboard.get_stalled_deals";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#ssd-body")) {
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
        return `<div class="ssd-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#ssd-body");
        const count = container.querySelector("#ssd-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Stalled Deals is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const deals = message.deals || [];
        count.textContent = deals.length ? __("{0} idle {1}d+", [deals.length, message.threshold_days]) : "";
        if (!deals.length) {
            body.innerHTML = muted(__("Nothing has gone quiet. Every open deal was touched this fortnight."));
            return;
        }

        body.innerHTML = deals
            .map((d) => {
                const meta = [d.owner, money(d.amount)].filter(Boolean).map(esc).join(" · ");
                const stage = d.stage ? `<span class="ssd-pill">${esc(d.stage)}</span>` : "";
                const tone = d.idle_days >= 30 ? "ssd-bad" : "ssd-warn";
                return `
                    <div class="ssd-row">
                        <a class="ssd-name" href="/app/opportunity/${encodeURIComponent(d.name)}">${esc(d.title)}</a>
                        ${stage}
                        <span class="ssd-meta">${meta}</span>
                        <span class="ssd-age ${tone}">${esc(__("{0}d idle", [d.idle_days]))}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#ssd-body");
        const refresh = container.querySelector("#ssd-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load stalled deals."));
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
