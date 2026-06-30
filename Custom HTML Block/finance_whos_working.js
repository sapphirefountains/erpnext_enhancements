// Who's Working — Finance Dashboard Custom HTML Block.
//
// Renders employees currently clocked in (open/paused Job Intervals) from
// erpnext_enhancements.api.finance_dashboard.get_whos_working. Auto-refreshes so
// the elapsed times stay live; the interval is stored on `window` and cleared on
// re-run so workspace navigations don't stack timers (shadow-DOM block model).

(function () {
    const MAX_ATTEMPTS = 50;
    const REFRESH_MS = 60000;
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#fww-body")) {
            startApp(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function render(body, message) {
        const esc = frappe.utils.escape_html;
        if (!message || message.enabled === false) {
            body.innerHTML = `<div class="fww-muted">${__("Who's Working is turned off in ERPNext Enhancements Settings.")}</div>`;
            return;
        }
        const workers = message.workers || [];
        if (!workers.length) {
            body.innerHTML = `<div class="fww-muted">${__("Nobody is clocked in right now.")}</div>`;
            return;
        }
        body.innerHTML = workers
            .map((w) => {
                const where = w.task_subject || w.project_title || "";
                const paused =
                    w.status === "Paused" ? `<span class="fww-paused">${__("paused")}</span>` : "";
                return `
                    <div class="fww-item">
                        <div class="fww-line">
                            <span class="fww-name">${esc(w.employee_name || "")}</span>
                            <span class="fww-elapsed">${esc(w.elapsed_label || "")}${paused ? " " : ""}${paused}</span>
                        </div>
                        ${where ? `<div class="fww-where">${esc(where)}</div>` : ""}
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const state = (window.__ee_fww = window.__ee_fww || {});
        if (state.timer) {
            clearInterval(state.timer);
            state.timer = null;
        }

        const body = container.querySelector("#fww-body");
        const refresh = container.querySelector("#fww-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: "erpnext_enhancements.api.finance_dashboard.get_whos_working" })
                .then((r) => render(body, r.message))
                .catch(() => {
                    body.innerHTML = `<div class="fww-muted">${__("Could not load time-clock status.")}</div>`;
                })
                .then(() => {
                    refresh.disabled = false;
                });
        }

        refresh.addEventListener("click", load);
        load();
        state.timer = setInterval(load, REFRESH_MS);
    }

    waitForDOM();
})();
