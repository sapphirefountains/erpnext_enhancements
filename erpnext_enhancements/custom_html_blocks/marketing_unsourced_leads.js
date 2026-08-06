// Unsourced Leads — Marketing Dashboard Custom HTML Block.
//
// Recent leads with no attributed source, newest first, from
// erpnext_enhancements.api.marketing_dashboard.get_unsourced_leads.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.marketing_dashboard.get_unsourced_leads";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#mul-body")) {
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
        return `<div class="mul-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#mul-body");
        const count = container.querySelector("#mul-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Unsourced Leads is turned off in ERPNext Enhancements Settings."));
            return;
        }
        if (message.unavailable) {
            count.textContent = "";
            body.innerHTML = muted(esc(message.unavailable));
            return;
        }

        const leads = message.leads || [];
        count.textContent =
            message.unsourced_pct === null || message.unsourced_pct === undefined
                ? ""
                : __("{0} of {1} ({2}%)", [
                      message.unsourced_count,
                      message.total_count,
                      num(message.unsourced_pct, 0),
                  ]);

        if (!leads.length) {
            body.innerHTML = muted(__("Every lead from the last 30 days has a source. Keep it that way."));
            return;
        }

        body.innerHTML = leads
            .map((l) => {
                const meta = [l.company, l.status, l.owner].filter(Boolean).map(esc).join(" · ");
                return `
                    <div class="mul-row">
                        <a class="mul-name" href="/app/lead/${encodeURIComponent(l.name)}">${esc(l.title)}</a>
                        <span class="mul-meta">${meta}</span>
                        <span class="mul-age">${esc(l.created || "")}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#mul-body");
        const refresh = container.querySelector("#mul-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load unsourced leads."));
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
