// Company Scorecard — Executive Dashboard Custom HTML Block.
//
// One tile per department — Good / Watch / Bad counts from its latest KPI
// Snapshot, from erpnext_enhancements.api.executive_dashboard.get_scorecard.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.executive_dashboard.get_scorecard";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#exs-body")) {
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
        return `<div class="exs-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#exs-body");
        const count = container.querySelector("#exs-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Company Scorecard is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const cards = message.cards || [];
        count.textContent = "";
        if (!cards.length) {
            body.innerHTML = muted(__("No departments are configured."));
            return;
        }

        body.innerHTML =
            `<div class="exs-tiles">` +
            cards
                .map((c) => {
                    // Only link where the viewer can actually open that dashboard — an
                    // Accounts Manager should not get a link into HR.
                    const href = c.linkable
                        ? `/app/${frappe.router.slug(c.department + " Dashboard")}`
                        : null;
                    const tag = href ? "a" : "div";
                    const attrs = href ? ` href="${esc(href)}"` : "";

                    if (!c.available) {
                        return `
                    <${tag} class="exs-tile"${attrs}>
                        <span class="exs-tile-label">${esc(c.department)}</span>
                        <span class="exs-tile-value">—</span>
                        <span class="exs-tile-note">${esc(c.reason || "")}</span>
                    </${tag}>`;
                    }

                    const k = c.counts || {};
                    // Bad wins the headline: an executive tile should read as its worst
                    // number, not its average.
                    const tone = k.bad ? "exs-bad" : k.watch ? "exs-warn" : "exs-good";
                    const headline = k.bad ? k.bad : k.watch ? k.watch : k.good;
                    const headlineLabel = k.bad ? __("bad") : k.watch ? __("watch") : __("good");
                    const note = [
                        __("{0} of {1} KPIs", [headline, c.total]),
                        c.stale ? __("{0}d old", [c.age_days]) : "",
                    ]
                        .filter(Boolean)
                        .join(" · ");
                    return `
                    <${tag} class="exs-tile"${attrs}>
                        <span class="exs-tile-label">${esc(c.department)}</span>
                        <span class="exs-tile-value ${tone}">${num(headline)} ${esc(headlineLabel)}</span>
                        <span class="exs-tile-note ${c.stale ? "exs-warn" : ""}">${esc(note)}</span>
                    </${tag}>`;
                })
                .join("") +
            `</div>`;
    }

    function startApp(container) {
        const body = container.querySelector("#exs-body");
        const refresh = container.querySelector("#exs-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load the scorecard."));
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
