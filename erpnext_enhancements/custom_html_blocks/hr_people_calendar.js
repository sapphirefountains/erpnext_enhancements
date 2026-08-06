// People Calendar — HR Dashboard Custom HTML Block.
//
// Upcoming work anniversaries for active employees, from
// erpnext_enhancements.api.hr_dashboard.get_people_calendar.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.hr_dashboard.get_people_calendar";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#hpc-body")) {
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
        return `<div class="hpc-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#hpc-body");
        const count = container.querySelector("#hpc-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("People Calendar is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const upcoming = message.upcoming || [];
        count.textContent = __("next {0} days", [message.horizon_days]);
        if (!upcoming.length) {
            body.innerHTML = muted(__("No work anniversaries coming up."));
            return;
        }

        body.innerHTML = upcoming
            .map((e) => {
                const when = e.days_until === 0 ? __("today") : __("in {0}d", [e.days_until]);
                const meta = [e.role, __("{0} years", [e.years])].filter(Boolean).map(esc).join(" · ");
                return `
                    <div class="hpc-row">
                        <a class="hpc-name" href="/app/employee/${encodeURIComponent(e.name)}">${esc(e.title)}</a>
                        <span class="hpc-meta">${meta}</span>
                        <span class="hpc-age">${esc(when)}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#hpc-body");
        const refresh = container.querySelector("#hpc-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load upcoming anniversaries."));
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
