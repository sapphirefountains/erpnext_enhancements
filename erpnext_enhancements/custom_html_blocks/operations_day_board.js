// Today's Visits — Operations Dashboard Custom HTML Block.
//
// Every maintenance visit scheduled for today, unfinished work first, from
// erpnext_enhancements.api.operations_dashboard.get_day_board.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.operations_dashboard.get_day_board";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#odb-body")) {
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
        return `<div class="odb-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#odb-body");
        const count = container.querySelector("#odb-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Today's Visits is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const visits = message.visits || [];
        count.textContent = visits.length ? __("{0} of {1} done", [message.completed, message.total]) : "";
        if (!visits.length) {
            body.innerHTML = muted(__("Nothing is scheduled for today."));
            return;
        }

        body.innerHTML = visits
            .map((v) => {
                const pct = Math.max(0, Math.min(100, Number(v.percent) || 0));
                const fill = v.complete ? "odb-fill-good" : pct > 0 ? "odb-fill-warn" : "";
                const flag = v.on_site ? `<span class="odb-pill odb-pill-good">${__("On site")}</span>` : "";
                const meta = [v.technician, v.customer].filter(Boolean).map(esc).join(" · ");
                return `
                    <div class="odb-row">
                        <a class="odb-name" href="/app/sapphire-maintenance-record/${encodeURIComponent(v.name)}">${esc(v.title)}</a>
                        ${flag}
                        <span class="odb-meta">${meta}</span>
                        <span class="odb-bar" title="${esc(pct + "%")}"><span class="odb-fill ${fill}" style="width:${pct}%"></span></span>
                        <span class="odb-age">${esc(v.state)}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#odb-body");
        const refresh = container.querySelector("#odb-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load today's visits."));
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
