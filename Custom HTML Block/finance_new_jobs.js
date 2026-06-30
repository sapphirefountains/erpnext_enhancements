// New Jobs Queue — Finance Dashboard Custom HTML Block.
//
// Renders the most recently created Active Projects from
// erpnext_enhancements.api.finance_dashboard.get_new_jobs. Shadow-DOM sandbox:
// `root_element` is the shadow root; the workspace re-runs this whole script with
// a fresh root on every navigation, so state lives on `window` and listeners are
// re-bound to the fresh DOM (same model as the KPI Cockpit block).

(function () {
    const MAX_ATTEMPTS = 50;
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#fnj-body")) {
            startApp(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function ageLabel(days) {
        if (days === null || days === undefined) return "";
        if (days <= 0) return __("today");
        if (days === 1) return __("1 day ago");
        return __("{0} days ago", [days]);
    }

    function render(body, message) {
        const esc = frappe.utils.escape_html;
        if (!message || message.enabled === false) {
            body.innerHTML = `<div class="fnj-muted">${__("New Jobs Queue is turned off in ERPNext Enhancements Settings.")}</div>`;
            return;
        }
        const jobs = message.jobs || [];
        if (!jobs.length) {
            body.innerHTML = `<div class="fnj-muted">${__("No active jobs.")}</div>`;
            return;
        }
        body.innerHTML = jobs
            .map((j) => {
                const opp = j.opportunity
                    ? `<a class="fnj-opp" href="/app/opportunity/${encodeURIComponent(j.opportunity)}">${esc(j.opportunity)}</a>`
                    : "";
                const meta = [esc(j.customer || ""), esc(j.owner || ""), esc(ageLabel(j.age_days))]
                    .filter(Boolean)
                    .join(" · ");
                return `
                    <div class="fnj-item">
                        <a class="fnj-name" href="/app/project/${encodeURIComponent(j.name)}">${esc(j.project_name)}</a>
                        <div class="fnj-meta">${meta}</div>
                        ${opp ? `<div class="fnj-meta">${__("from")} ${opp}</div>` : ""}
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#fnj-body");
        const refresh = container.querySelector("#fnj-refresh");

        function load() {
            body.innerHTML = `<div class="fnj-muted">${__("Loading…")}</div>`;
            refresh.disabled = true;
            frappe
                .call({ method: "erpnext_enhancements.api.finance_dashboard.get_new_jobs" })
                .then((r) => render(body, r.message))
                .catch(() => {
                    body.innerHTML = `<div class="fnj-muted">${__("Could not load new jobs.")}</div>`;
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
