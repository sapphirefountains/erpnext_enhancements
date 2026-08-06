// Funnel Cascade — Marketing Dashboard Custom HTML Block.
//
// Sessions to leads to opportunities to won, with the rate at each step, from
// erpnext_enhancements.api.marketing_dashboard.get_funnel_cascade.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.marketing_dashboard.get_funnel_cascade";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#mfc-body")) {
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
        return `<div class="mfc-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#mfc-body");
        const count = container.querySelector("#mfc-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Funnel Cascade is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const w = message.last_30 || {};
        const w90 = message.last_90 || {};
        count.textContent = __("last 30 days");

        function tile(label, value, note) {
            return `
                <div class="mfc-tile">
                    <span class="mfc-tile-label">${esc(label)}</span>
                    <span class="mfc-tile-value">${value}</span>
                    <span class="mfc-tile-note">${esc(note || "")}</span>
                </div>`;
        }

        function pct(value) {
            return value === null || value === undefined ? "—" : num(value, 1) + "%";
        }

        // A blank sessions step means the GA4 pull failed, which is not the same as
        // zero traffic — so it renders as an em dash with a note, never as 0.
        const sessions = w.sessions === null || w.sessions === undefined ? "—" : num(w.sessions);
        const sessionNote = w.sessions === null || w.sessions === undefined ? __("GA4 pull unavailable") : "";

        body.innerHTML = `
            <div class="mfc-tiles">
                ${tile(__("Sessions"), sessions, sessionNote)}
                ${tile(__("Leads"), num(w.leads), pct(w.session_to_lead_pct) + " " + __("of sessions"))}
                ${tile(__("Converted"), num(w.converted), pct(w.lead_to_opp_pct) + " " + __("of leads"))}
                ${tile(__("Won"), num(w.won), pct(w.opp_to_won_pct) + " " + __("of opportunities"))}
            </div>
            <div class="mfc-group">${__("Last 90 days")}</div>
            <div class="mfc-row">
                <span class="mfc-meta">${esc(__("{0} leads · {1} opportunities · {2} won", [num(w90.leads), num(w90.opportunities), num(w90.won)]))}</span>
                <span class="mfc-age">${esc(__("win rate {0}", [pct(w90.opp_to_won_pct)]))}</span>
            </div>`;
    }

    function startApp(container) {
        const body = container.querySelector("#mfc-body");
        const refresh = container.querySelector("#mfc-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load the funnel."));
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
