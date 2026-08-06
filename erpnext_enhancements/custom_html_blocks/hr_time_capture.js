// Time Capture — HR Dashboard Custom HTML Block.
//
// Active employees who have logged no time in the window, from
// erpnext_enhancements.api.hr_dashboard.get_timesheet_completeness.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.hr_dashboard.get_timesheet_completeness";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#htk-body")) {
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
        return `<div class="htk-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#htk-body");
        const count = container.querySelector("#htk-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Time Capture is turned off in ERPNext Enhancements Settings."));
            return;
        }
        if (message.unavailable) {
            count.textContent = "";
            body.innerHTML = muted(esc(message.unavailable));
            return;
        }

        const missing = message.missing || [];
        count.textContent = __("{0} of {1} logged time in {2}d", [
            message.logged_count,
            message.active_count,
            message.window_days,
        ]);

        if (!missing.length) {
            body.innerHTML = muted(__("Every active employee has logged time this week."));
            return;
        }

        body.innerHTML =
            `<div class="htk-group">${__("No time logged")}</div>` +
            missing
                .map((e) => {
                    const meta = [e.designation, e.department].filter(Boolean).map(esc).join(" · ");
                    return `
                    <div class="htk-row">
                        <a class="htk-name" href="/app/employee/${encodeURIComponent(e.name)}">${esc(e.title)}</a>
                        <span class="htk-meta">${meta}</span>
                    </div>`;
                })
                .join("") +
            (message.missing_count > missing.length
                ? muted(__("and {0} more", [message.missing_count - missing.length]))
                : "");
    }

    function startApp(container) {
        const body = container.querySelector("#htk-body");
        const refresh = container.querySelector("#htk-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load time capture."));
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
