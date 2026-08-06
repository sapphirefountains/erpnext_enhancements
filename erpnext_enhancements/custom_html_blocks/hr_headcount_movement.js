// Headcount Movement — HR Dashboard Custom HTML Block.
//
// Active headcount plus recent joiners and leavers, from
// erpnext_enhancements.api.hr_dashboard.get_headcount_movement.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.hr_dashboard.get_headcount_movement";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#hhm-body")) {
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
        return `<div class="hhm-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#hhm-body");
        const count = container.querySelector("#hhm-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Headcount Movement is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const joined = message.joined || [];
        const left = message.left || [];
        count.textContent = __("last {0} days", [message.window_days]);

        const net = message.net;
        const netTone = net > 0 ? "hhm-good" : net < 0 ? "hhm-bad" : "";
        const tiles = `
            <div class="hhm-tiles">
                <div class="hhm-tile">
                    <span class="hhm-tile-label">${__("Active")}</span>
                    <span class="hhm-tile-value">${num(message.active)}</span>
                </div>
                <div class="hhm-tile">
                    <span class="hhm-tile-label">${__("Joined")}</span>
                    <span class="hhm-tile-value">${num(joined.length)}</span>
                </div>
                <div class="hhm-tile">
                    <span class="hhm-tile-label">${__("Left")}</span>
                    <span class="hhm-tile-value">${num(left.length)}</span>
                </div>
                <div class="hhm-tile">
                    <span class="hhm-tile-label">${__("Net")}</span>
                    <span class="hhm-tile-value ${netTone}">${net > 0 ? "+" : ""}${num(net)}</span>
                </div>
            </div>`;

        function people(rows, label, showTenure) {
            if (!rows.length) return "";
            return (
                `<div class="hhm-group">${esc(label)}</div>` +
                rows
                    .map((e) => {
                        const meta = [e.role, showTenure && e.tenure_years ? __("{0}y tenure", [e.tenure_years]) : ""]
                            .filter(Boolean)
                            .map(esc)
                            .join(" · ");
                        return `
                    <div class="hhm-row">
                        <a class="hhm-name" href="/app/employee/${encodeURIComponent(e.name)}">${esc(e.title)}</a>
                        <span class="hhm-meta">${meta}</span>
                        <span class="hhm-age">${esc(e.date || "")}</span>
                    </div>`;
                    })
                    .join("")
            );
        }

        body.innerHTML = tiles + people(joined, __("Joined"), false) + people(left, __("Left"), true);
    }

    function startApp(container) {
        const body = container.querySelector("#hhm-body");
        const refresh = container.querySelector("#hhm-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load headcount movement."));
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
