// Hand-Off Backlog — Sales Dashboard Custom HTML Block.
//
// Closed-Won Opportunities that still have no Project, longest wait first, from
// erpnext_enhancements.api.sales_dashboard.get_handoff_backlog.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.sales_dashboard.get_handoff_backlog";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#shb-body")) {
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
        return `<div class="shb-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#shb-body");
        const count = container.querySelector("#shb-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Hand-Off Backlog is turned off in ERPNext Enhancements Settings."));
            return;
        }
        if (message.unavailable) {
            count.textContent = "";
            body.innerHTML = muted(esc(message.unavailable));
            return;
        }

        const deals = message.deals || [];
        count.textContent = deals.length ? __("{0} waiting", [deals.length]) : "";
        if (!deals.length) {
            body.innerHTML = muted(__("Every won deal has a project. The hand-off is clean."));
            return;
        }

        body.innerHTML = deals
            .map((d) => {
                const meta = [money(d.amount), d.won_on ? __("won {0}", [d.won_on]) : ""]
                    .filter(Boolean)
                    .map(esc)
                    .join(" · ");
                const tone = d.waiting_days >= 7 ? "shb-bad" : d.waiting_days >= 3 ? "shb-warn" : "";
                return `
                    <div class="shb-row">
                        <a class="shb-name" href="/app/opportunity/${encodeURIComponent(d.name)}">${esc(d.title)}</a>
                        <span class="shb-meta">${meta}</span>
                        <span class="shb-age ${tone}">${esc(__("{0}d", [d.waiting_days]))}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#shb-body");
        const refresh = container.querySelector("#shb-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load the hand-off backlog."));
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
