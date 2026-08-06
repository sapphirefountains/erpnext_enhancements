// Speed to Lead — Sales Dashboard Custom HTML Block.
//
// Leads with no outbound Communication yet, oldest first, from
// erpnext_enhancements.api.sales_dashboard.get_speed_to_lead. Shadow-DOM
// sandbox: `root_element` is the shadow root and the workspace re-runs this
// whole script with a fresh root on every navigation, so nothing is cached
// across renders and listeners are bound to the fresh DOM each time (same model
// as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    let attempts = 0;

    // Hours unanswered before a lead reads as warm / cold. A working day is the
    // cold line: past that, the industry's speed-to-lead advantage is gone.
    const WARM_HOURS = 4;
    const COLD_HOURS = 24;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#stl-body")) {
            startApp(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function waitLabel(hours) {
        if (hours === null || hours === undefined) return "";
        if (hours < 1) return __("{0}m", [Math.max(1, Math.round(hours * 60))]);
        if (hours < 48) return __("{0}h", [Math.round(hours)]);
        return __("{0}d", [Math.round(hours / 24)]);
    }

    function waitClass(hours) {
        if (hours >= COLD_HOURS) return "stl-cold";
        if (hours >= WARM_HOURS) return "stl-warm";
        return "";
    }

    function render(container, message) {
        const body = container.querySelector("#stl-body");
        const count = container.querySelector("#stl-count");
        const esc = frappe.utils.escape_html;

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = `<div class="stl-muted">${__("Speed to Lead is turned off in ERPNext Enhancements Settings.")}</div>`;
            return;
        }

        const leads = message.leads || [];
        count.textContent = leads.length ? __("{0} waiting", [leads.length]) : "";
        if (!leads.length) {
            body.innerHTML = `<div class="stl-muted">${__("Every lead has been contacted. Nice.")}</div>`;
            return;
        }

        body.innerHTML = leads
            .map((l) => {
                const meta = [l.company, l.owner, l.source].filter(Boolean).map(esc).join(" · ");
                return `
                    <div class="stl-row">
                        <a class="stl-name" href="/app/lead/${encodeURIComponent(l.name)}">${esc(l.title)}</a>
                        <span class="stl-meta">${meta}</span>
                        <span class="stl-age ${waitClass(l.hours_waiting)}">${esc(waitLabel(l.hours_waiting))}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#stl-body");
        const refresh = container.querySelector("#stl-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: "erpnext_enhancements.api.sales_dashboard.get_speed_to_lead" })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = `<div class="stl-muted">${__("Could not load the lead queue.")}</div>`;
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
