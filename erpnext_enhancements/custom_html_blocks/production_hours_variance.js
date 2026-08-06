// Hours: Budget vs Actual — Production Dashboard Custom HTML Block.
//
// Active builds ranked by how far actual hours have run past budget, from
// erpnext_enhancements.api.production_dashboard.get_hours_variance.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.production_dashboard.get_hours_variance";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#phv-body")) {
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
        return `<div class="phv-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#phv-body");
        const count = container.querySelector("#phv-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Hours: Budget vs Actual is turned off in ERPNext Enhancements Settings."));
            return;
        }
        if (message.unavailable) {
            count.textContent = "";
            body.innerHTML = muted(esc(message.unavailable));
            return;
        }

        const projects = message.projects || [];
        const over = projects.filter((p) => p.over).length;
        count.textContent = projects.length ? __("{0} over budget", [over]) : "";
        if (!projects.length) {
            body.innerHTML = muted(__("No active build has an hours budget recorded."));
            return;
        }

        body.innerHTML = projects
            .map((p) => {
                // The bar fills to the budget; past 100% it stays full and the number
                // carries the overrun, so a 400% job doesn't render a bar four times wide.
                const width = Math.max(0, Math.min(100, Number(p.used_pct) || 0));
                const fill = p.over ? "phv-fill-bad" : p.used_pct >= 80 ? "phv-fill-warn" : "phv-fill-good";
                const tone = p.over ? "phv-bad" : p.used_pct >= 80 ? "phv-warn" : "";
                const meta = [
                    p.customer,
                    __("{0} of {1} h", [num(p.actual_hours, 0), num(p.budget_hours, 0)]),
                ]
                    .filter(Boolean)
                    .map(esc)
                    .join(" · ");
                return `
                    <div class="phv-row">
                        <a class="phv-name" href="/app/project/${encodeURIComponent(p.name)}">${esc(p.title)}</a>
                        <span class="phv-meta">${meta}</span>
                        <span class="phv-bar"><span class="phv-fill ${fill}" style="width:${width}%"></span></span>
                        <span class="phv-age ${tone}">${esc(num(p.used_pct, 0) + "%")}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#phv-body");
        const refresh = container.querySelector("#phv-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load hours against budget."));
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
