// Bookings & Backlog — Executive Dashboard Custom HTML Block.
//
// Won value, pipeline and booked-but-unbuilt backlog, from
// erpnext_enhancements.api.executive_dashboard.get_bookings_backlog.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.executive_dashboard.get_bookings_backlog";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#ebb-body")) {
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
        return `<div class="ebb-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#ebb-body");
        const count = container.querySelector("#ebb-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Bookings & Backlog is turned off in ERPNext Enhancements Settings."));
            return;
        }
        if (message.unavailable) {
            count.textContent = "";
            body.innerHTML = muted(esc(message.unavailable));
            return;
        }

        const figures = message.figures || [];
        count.textContent = "";
        if (!figures.length) {
            body.innerHTML = muted(__("The Sales and Production snapshots carry none of these figures yet."));
            return;
        }

        // Each figure carries its own snapshot date: if one department's nightly run
        // failed, its number must not borrow the other's freshness.
        body.innerHTML =
            `<div class="ebb-tiles">` +
            figures
                .map((f) => {
                    const tone =
                        f.status === "Bad" ? "ebb-bad" : f.status === "Watch" ? "ebb-warn" : f.status === "Good" ? "ebb-good" : "";
                    const note = [f.source, f.as_of].filter(Boolean).join(" · ");
                    return `
                    <div class="ebb-tile">
                        <span class="ebb-tile-label">${esc(f.label)}</span>
                        <span class="ebb-tile-value ${tone}">${esc(f.text || num(f.value))}</span>
                        <span class="ebb-tile-note">${esc(note)}</span>
                    </div>`;
                })
                .join("") +
            `</div>`;
    }

    function startApp(container) {
        const body = container.querySelector("#ebb-body");
        const refresh = container.querySelector("#ebb-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load bookings and backlog."));
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
