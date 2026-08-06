// Channel Spend & CPL — Marketing Dashboard Custom HTML Block.
//
// Month-to-date spend per channel against attributed leads, from
// erpnext_enhancements.api.marketing_dashboard.get_channel_spend.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.marketing_dashboard.get_channel_spend";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#mcs-body")) {
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
        return `<div class="mcs-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#mcs-body");
        const count = container.querySelector("#mcs-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Channel Spend & CPL is turned off in ERPNext Enhancements Settings."));
            return;
        }
        if (message.unavailable) {
            count.textContent = "";
            body.innerHTML = muted(esc(message.unavailable));
            return;
        }

        const channels = message.channels || [];
        count.textContent = message.blended_cpl
            ? __("{0} blended CPL", [money(message.blended_cpl)])
            : __("month to date");

        if (!channels.length) {
            body.innerHTML = muted(__("No spend recorded for this month yet."));
            return;
        }

        const warning = message.attribution_available
            ? ""
            : muted(__("Lead attribution is not installed, so no cost per lead can be computed."));

        body.innerHTML =
            warning +
            channels
                .map((c) => {
                    // A channel with spend and no attributed leads is the case worth
                    // seeing, so it is listed with an em dash rather than hidden.
                    const cpl = c.cpl ? money(c.cpl) : "—";
                    const leads = c.matched ? __("{0} leads", [c.leads]) : __("no attributed leads");
                    const tone = c.matched ? "" : "mcs-warn";
                    return `
                    <div class="mcs-row">
                        <span class="mcs-name">${esc(c.channel)}</span>
                        <span class="mcs-meta ${tone}">${esc(leads)}</span>
                        <span class="mcs-age">${esc(money(c.spend))} · ${esc(cpl)}</span>
                    </div>`;
                })
                .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#mcs-body");
        const refresh = container.querySelector("#mcs-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load channel spend."));
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
