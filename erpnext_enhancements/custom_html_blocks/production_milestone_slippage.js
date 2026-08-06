// Milestone Slippage — Production Dashboard Custom HTML Block.
//
// Pending project process steps past their due date, from
// erpnext_enhancements.api.production_dashboard.get_milestone_slippage.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.production_dashboard.get_milestone_slippage";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#pms-body")) {
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
        return `<div class="pms-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#pms-body");
        const count = container.querySelector("#pms-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Milestone Slippage is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const steps = message.steps || [];
        count.textContent = steps.length ? __("{0} overdue", [steps.length]) : "";
        if (!steps.length) {
            body.innerHTML = muted(__("No milestone is overdue."));
            return;
        }

        body.innerHTML = steps
            .map((s) => {
                const tone = s.overdue_days >= 14 ? "pms-pill-bad" : "pms-pill-warn";
                const meta = [s.project_title, s.customer, s.role].filter(Boolean).map(esc).join(" · ");
                return `
                    <div class="pms-row">
                        <a class="pms-name" href="/app/project/${encodeURIComponent(s.project)}">${esc(s.title)}</a>
                        <span class="pms-pill ${tone}">${esc(__("{0}d late", [s.overdue_days]))}</span>
                        <span class="pms-meta">${meta}</span>
                        <span class="pms-age">${esc(s.due_by || "")}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#pms-body");
        const refresh = container.querySelector("#pms-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load overdue milestones."));
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
