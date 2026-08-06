// Training Compliance — HR Dashboard Custom HTML Block.
//
// Open training assignments that are overdue or due soon, from
// erpnext_enhancements.api.hr_dashboard.get_training_compliance.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.hr_dashboard.get_training_compliance";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#htc-body")) {
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
        return `<div class="htc-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#htc-body");
        const count = container.querySelector("#htc-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Training Compliance is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const assignments = message.assignments || [];
        count.textContent = message.overdue_count ? __("{0} overdue", [message.overdue_count]) : "";
        if (!assignments.length) {
            body.innerHTML = muted(__("Nothing is overdue or due in the next fortnight."));
            return;
        }

        body.innerHTML = assignments
            .map((a) => {
                const days = a.days_until;
                const label = a.overdue ? __("{0}d late", [Math.abs(days)]) : __("in {0}d", [days]);
                const tone = a.overdue ? "htc-pill-bad" : days <= 3 ? "htc-pill-warn" : "htc-pill";
                const meta = [a.learner, a.status].filter(Boolean).map(esc).join(" · ");
                return `
                    <div class="htc-row">
                        <a class="htc-name" href="/app/training-assignment/${encodeURIComponent(a.name)}">${esc(a.course)}</a>
                        <span class="htc-pill ${tone}">${esc(label)}</span>
                        <span class="htc-meta">${meta}</span>
                        <span class="htc-age">${esc(a.due_date || "")}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#htc-body");
        const refresh = container.querySelector("#htc-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load training compliance."));
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
