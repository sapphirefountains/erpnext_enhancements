// Cash & Receivables — Executive Dashboard Custom HTML Block.
//
// Cash, receivables and DSO from the latest Finance snapshot, from
// erpnext_enhancements.api.executive_dashboard.get_cash_runway.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.executive_dashboard.get_cash_runway";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#ecr-body")) {
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
        return `<div class="ecr-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#ecr-body");
        const count = container.querySelector("#ecr-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Cash & Receivables is turned off in ERPNext Enhancements Settings."));
            return;
        }
        if (message.unavailable) {
            count.textContent = "";
            body.innerHTML = muted(esc(message.unavailable));
            return;
        }

        const figures = message.figures || [];
        count.textContent = message.snapshot_date ? __("as of {0}", [message.snapshot_date]) : "";
        if (!figures.length) {
            body.innerHTML = muted(__("The Finance snapshot carries none of these figures yet."));
            return;
        }

        body.innerHTML =
            `<div class="ecr-tiles">` +
            figures
                .map((f) => {
                    const tone =
                        f.status === "Bad" ? "ecr-bad" : f.status === "Watch" ? "ecr-warn" : f.status === "Good" ? "ecr-good" : "";
                    const trend =
                        f.trend_pct === null || f.trend_pct === undefined
                            ? ""
                            : `${f.trend_pct > 0 ? "▲" : f.trend_pct < 0 ? "▼" : ""} ${num(Math.abs(f.trend_pct), 1)}%`;
                    const note = [trend, f.stale ? __("stale source") : ""].filter(Boolean).join(" · ");
                    return `
                    <div class="ecr-tile">
                        <span class="ecr-tile-label">${esc(f.label)}</span>
                        <span class="ecr-tile-value ${tone}">${esc(f.text || num(f.value))}</span>
                        <span class="ecr-tile-note ${f.stale ? "ecr-warn" : ""}">${esc(note)}</span>
                    </div>`;
                })
                .join("") +
            `</div>`;
    }

    function startApp(container) {
        const body = container.querySelector("#ecr-body");
        const refresh = container.querySelector("#ecr-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load cash figures."));
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
